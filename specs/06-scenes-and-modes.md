# 06 - Scenes and Modes

**Introduces**: `Scene`, `SceneId`, `SceneState`, `Zone`, `ZoneId`, `ZoneGraph`,
`Mode`, `SceneTransition`, `ExplorationState`, `SocialState`, `DowntimeState`.
**Uses**: `00`, `01`, `02`, `03`, `04`, `05`. (Combat mode state lives in `07`.)

## 1. Purpose

A **Scene** is the bounded gameplay context in which a set of Entities
interacts. It is the smallest save-point granularity larger than a single
Entity. All gameplay (exploration, social, combat, downtime) occurs within
some Scene.

A Scene owns a **Mode**, which determines which rules subset is active
without changing the underlying Entity state.

## 2. Scene

```
Scene = {
    id: SceneId,
    display_name: String,
    mode: Mode,
    zones: ZoneGraph,
    entities_present: OrderedSet<EntityId>,
    ambient_triggers: [TriggerId],
    ambient_transformers: [TransformerId],
    state: SceneState,
    tags: OrderedSet<SceneTag>,
}

SceneState =
  | Exploration(ExplorationState)
  | Social(SocialState)
  | Combat(CombatState)        // defined in 07
  | Downtime(DowntimeState)
  | Custom(JsonValue)
```

### Invariant SC1 (Mode/State consistency)
`Scene.mode` and `Scene.state` variant names MUST match. Transitioning
between modes is a `SceneTransition` event handled through
`perform_scene_transition` in the Engine.

## 3. Zone Graph

```
ZoneGraph = {
    zones: OrderedMap<ZoneId, Zone>,
    adjacency: OrderedMap<ZoneId, [ZoneId]>,
    grid: Option<GridDescriptor>,
}
Zone = {
    id: ZoneId,
    display_name: String,
    footprint: ZoneFootprint,
    cover: CoverLevel,
    difficult_terrain: bool,
    tags: OrderedSet<ZoneTag>,
}
ZoneFootprint = Abstract | Grid { cells: [GridOffset] }
CoverLevel = None | Half | ThreeQuarters | Full
```

- Zones are abstract by default. A grid scene has zones whose footprint is
  a list of tile coordinates.
- Adjacency is explicit. There is no implicit spatial inference: a wall is
  represented by omitting the adjacency edge.
- The Engine does not do pathfinding. The pack's movement actions decide
  whether a move is legal by walking adjacency (via `sfs.move.to_zone`).

## 4. Modes

### 4.1 Exploration

```
ExplorationState = {
    current_zone: Option<ZoneId>,            // party leader's zone if tracked
    pace: ExplorationPace,
    time_of_day: Option<TimeOfDay>,
    discovery_flags: OrderedSet<DiscoveryFlag>,
}
ExplorationPace = Slow | Normal | Fast | Custom(tag)
```

- Exploration allows any non-combat Action whose pack flag includes
  `ActionTag::Exploration`.
- No initiative. Entities take actions in the order the Driver proposes.
- Clock ticks occur on `Tick(minute)` / `Tick(hour)` as the Host decides.

### 4.2 Social

```
SocialState = {
    focus_entity: Option<EntityId>,         // the NPC currently being engaged
    stance: OrderedMap<EntityId, Stance>,
    disposition_modifiers: OrderedMap<EntityId, i32>,
    conversation_log: [ConversationBeat],   // Driver narration beats
}
Stance = Hostile | Unfriendly | Indifferent | Friendly | Helpful
ConversationBeat = { speaker: EntityId, beat_kind: BeatKind, tags: OrderedSet<String> }
BeatKind = Speak | Threaten | Persuade | Deceive | Insight | Custom(tag)
```

- The Engine does not generate dialog. Narration is the Driver's job via
  `Narrate` events. Social state tracks mechanical context (disposition,
  stance shifts from skill checks).
- Skill checks in social mode run through `resolve_check` like any other.

### 4.3 Downtime

```
DowntimeState = {
    activities: OrderedMap<EntityId, DowntimeActivity>,
    elapsed_days: u32,
}
DowntimeActivity = { id: DowntimeActivityId, progress: i32, target: i32 }
```

- Downtime ticks `day` clocks. Activities advance; at target, they complete
  via a pack-defined SFS call.

### 4.4 Custom

Packs may declare custom modes by naming them and registering
`state: Custom(JsonValue)`. The Engine stores custom state opaquely and
dispatches all mode-specific behavior through SFS functions declared in the
pack's `scene_templates` entry.

## 5. Scene Transitions

```
SceneTransition = {
    from: SceneId,
    to: SceneId,
    carry_entities: [EntityId],      // who moves with the party
    reason: TransitionReason,
}
TransitionReason = Travel | Triggered | Rest | EncounterStart | PackDefined(tag)
```

### 5.1 perform_scene_transition

```
perform_scene_transition(state) -> Transition:
    t = state.pending_scene_transition
    events = [SceneExited { scene: t.from }]
    state.active_scene = t.to
    for eid in t.carry_entities:
        move entity component Position.scene_id to t.to
    // Fire exit triggers on the old scene, entry triggers on the new scene.
    run trigger_scan for OnSceneExited and OnSceneEntered
    state.pending_scene_transition = None
    events.push(SceneEntered { scene: t.to })
    return Yielded(events, state)
```

### 5.2 Mode changes within a Scene

A Scene may change mode without transitioning to a new Scene. For example:
an exploration scene turns into combat when a hostile ambushes the party.

```
change_mode(state, scene_id, new_mode) -> Transition:
    old_mode = state.scenes[scene_id].mode
    state.scenes[scene_id].mode = new_mode
    state.scenes[scene_id].state = initial_state_for(new_mode, state)
    events = [ModeChanged { from: old_mode, to: new_mode }]
    if new_mode == Combat:
        // Initiate combat: see 07.
        events += initialize_combat(state, scene_id)
    return Yielded(events, state)
```

### Invariant SC2 (Entry/exit triggers fire exactly once)
On transition, `OnSceneExited` fires for every eligible trigger on the old
scene, then the scene reference changes, then `OnSceneEntered` fires for
the new. Triggers cannot fire on both sides of the transition for the same
event.

## 6. Visibility and Fog of War

Visibility is a per-Scene, per-Entity projection of which other entities
are observed. The Engine provides a helper but delegates the decision to
`sfs.visibility.compute`.

```
visibility_of(state, observer, scene) -> OrderedMap<EntityId, KnowledgeLevel>
```

- The pack supplies the compute function to avoid hardcoding perception
  rules.
- Social and exploration actions referencing "seen" entities get their
  target lists filtered by this map.

## 7. Scene Templates (pack-provided starter scenes)

A pack may ship `SceneTemplate`s as authoring conveniences:

```
SceneTemplate = {
    id: SceneTemplateId,
    display_name: String,
    zones: ZoneGraph,
    ambient_triggers: [TriggerId],
    ambient_transformers: [TransformerId],
    initial_entities: [EntityBlueprint],
    initial_mode: Mode,
}
EntityBlueprint = { blueprint_id: BlueprintId, components_override: JsonValue }
```

- The Host asks the Engine to instantiate a template via
  `sfs.scene.instantiate`, which clones the template, allocates fresh ids,
  and emits `EntityCreated` and `SceneEntered` events.

## 8. Multi-Scene Sessions

A SessionState may contain multiple `Scene`s. Only one is
`state.active_scene`. Non-active scenes freeze: their entities do not
receive trigger scans, their clocks do not advance.

### Invariant SC3 (Single active scene)
At any instant, exactly one scene is active. Transitioning to a new scene
clears the prior one's `pending_*` fields; if a prior scene had an open
reaction window, that is a `FatalError::InvariantViolation(SC3)`.

## 9. Save-Point Semantics

Snapshots for save/load happen at Scene granularity: a "save point" is a
state where no stack frames are pending and no reaction window is open. The
Engine computes `can_save(state) -> bool`:

```
can_save(state) = state.stack.is_empty()
              and state.open_continuation.is_none()
              and no pending reaction window
              and state.recoverable_retry_count == 0
```

Hosts that want mid-combat saves may save any state, but the spec only
guarantees clean resumes at save points. Mid-stack saves are supported by
replay from the nearest prior save point plus log tail; see
`09-persistence-and-replay.md`.
