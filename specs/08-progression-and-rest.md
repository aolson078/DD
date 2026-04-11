# 08 - Progression and Rest

**Introduces**: `XpAward`, `LevelUpFlow`, `RestKind`, `RestResult`,
`DeathState`, `ExhaustionState`.
**Uses**: `00`-`07`.

## 1. Experience and Milestones

```
XpAward = { recipients: [EntityId], amount: u64, source: XpSource, reason: String }
XpSource = Encounter(SceneId) | Milestone(String) | Driver | PackDefined(tag)
```

- XP is awarded by SFS calls (`sfs.xp.award`). The Engine records awards as
  `ResourceChanged` events on a reserved resource id `xp`.
- If the pack uses milestone progression, `XpSource::Milestone` is awarded
  as a named flag rather than a quantity; the progression check treats
  flags uniformly with xp totals.

### Invariant PR1 (XP does not auto-level)
Awarding xp does not trigger a level-up. The Driver must later invoke the
level-up flow; the Engine exposes `sfs.progression.check(entity)` to
report eligibility.

## 2. Level-Up Flow

Level-up is a **pack-scripted sequence of PromptDecision requests**. It
uses no pack code.

```
LevelUpFlow = {
    entity: EntityId,
    class: ClassId,
    to_level: u32,
    step_index: usize,
    steps: [LevelUpStep],                     // cloned from ProgressionConfig
}
```

### 2.1 Driving the flow

```
start_level_up(state, entity, class):
    flow = LevelUpFlow { entity, class, to_level: current+1, step_index: 0, steps: clone(progression.level_up_steps[class]) }
    state.open_level_up = Some(flow)
    events.push(LevelUpStarted { entity, class, to_level })
    return Yielded(events, state)

step_level_up(state):
    flow = state.open_level_up.unwrap()
    if flow.step_index >= len(flow.steps):
        apply_final_adjustments(state, flow)
        events.push(LevelUpCompleted { entity, class, new_level })
        state.open_level_up = None
        return Yielded(events, state)
    step = flow.steps[flow.step_index]
    legal = sfs_call(step.legal_choices_fn, state, flow)
    if legal is empty:
        flow.step_index += 1
        return Yielded(events, state)
    return NeedsDecision(PromptDecision {
        kind: step.prompt_kind,
        context: DecisionContext { entity, step: step.id },
        legal_choices: legal,
    }, continuation, state)

apply_level_up_choice(state, choice):
    flow = state.open_level_up.unwrap()
    step = flow.steps[flow.step_index]
    sfs_call(step.apply_fn, state, choice)
    events.push(LevelUpStep { entity: flow.entity, step: step.id, result: choice.id })
    flow.step_index += 1
    return Yielded(events, state)
```

### Invariant PR2 (Single open level-up)
At most one `open_level_up` exists per session at a time. A second
concurrent level-up is a `FatalError::InvariantViolation(PR2)`.

### 2.2 Multiclassing

Multiclassing is a pack concern. The pack provides alternate
`level_up_steps` tables keyed by target class. The Engine has no knowledge
of "multiclass prerequisites"; the pack's first `legal_choices_fn` on the
new class can enforce them by returning an empty list with a
`Choice::unavailable { reason }` hint the Host can surface.

## 3. Rests

```
RestKind = { id: RestId, duration: Duration, tags: OrderedSet<RestTag> }
RestResult = {
    rest_kind: RestId,
    recovered_resources: OrderedMap<(EntityId, ResourceId), i32>,
    expired_effects: [PersistentEffectInstanceId],
    time_advanced: OrderedMap<ClockTag, u64>,
    interrupted: bool,
    interrupt_reason: Option<String>,
}
```

### 3.1 Executing a rest

```
execute_rest(state, rest_id, participants) -> Transition:
    rest = state.pack.rests[rest_id]
    // Pre-rest scan: any pack-declared interrupter?
    for trigger in triggers_on_before_rest(rest_id):
        push trigger effect
    // If the stack produced an interrupting effect, the rest is aborted
    if state.pending_interrupt:
        events.push(RestInterrupted { reason })
        return Yielded(events, state)
    // Apply recovery rules for every participant
    for entity in participants:
        for (resource_id, pool) in entity.resources:
            if pool.recovery matches OnRest { rest_kind: rest_id }:
                apply recovery
                events.push(ResourceChanged)
    // Advance clocks by the rest's duration
    for (clock, amount) in clock_advances_for(rest.duration):
        state.clocks[clock] += amount
        events.push(ClockTicked)
    // Expire persistent effects due on rest
    for instance in persistent_effects_with_rest_expiry(rest_id):
        remove and push PersistentEffectRemoved
    events.push(RestCompleted { rest_kind: rest_id, result })
    return Yielded(events, state)
```

### 3.2 Hit dice short rest (sample pack)

Hit-die spending during a short rest is modeled as a `PromptDecision`
series: the Engine asks the Driver which dice to spend, applies healing
via `sfs.roll.hit_die` + `sfs.heal.dice`, loops until the Driver declines
or no hit dice remain. All of this is expressed in the pack's `RestDef`;
the Engine reuses the generic prompt-loop infrastructure.

### Invariant PR3 (Rest atomicity)
All events produced by a successful rest are emitted in one `Yielded`
batch. Either the whole rest succeeds (all recoveries applied) or it is
interrupted and recorded as such; there is no partial state.

## 4. Death and Dying

```
DeathState =
  | Alive
  | Dying { successes: u32, failures: u32, stable: bool }
  | Dead
  | Unconscious { reason: String }
```

- `DeathState` is a component on entities that support it. The pack
  registers a `DeathRules` block declaring:
  - Trigger at `current_hp == 0` (pack may choose `<= 0`)
  - Massive damage threshold for instant death
  - Save mechanic (`sfs.save.death`, DC, successes needed)
  - Stabilization rules

- The Engine transitions state by running the pack's death rules through
  SFS, not by hardcoding thresholds.

## 5. Exhaustion

```
ExhaustionState = { level: u32, effects: [TransformerId] }
```

- Exhaustion is a resource with pack-defined bounds and a set of
  transformers activated per level. The pack's `ExhaustionRules` block
  specifies the mapping from `level` to active transformers.

## 6. Sample Pack Sketch

To ground the spec, here is how the 5e-shaped sample pack uses the above.

### 6.1 Classes
- `fighter` (single class; the sample pack covers one for verification).
- Subclass choice at level 3 (as a `PromptDecision` with
  `legal_choices_fn = sfs.sample.fighter.subclass_options`).

### 6.2 Level-up steps for fighter
1. `hp_increase` - choose roll vs average HP.
2. `asi_or_feat` - at levels 4, 8, 12, 16, 19.
3. `subclass` - at level 3.
4. `proficiency_bonus_update` - automatic; `legal_choices` returns a
   single auto-applied Choice.
5. `feature_grants` - automatic; adds features like Extra Attack.

Each step's `legal_choices_fn` and `apply_fn` reference SFS functions the
sample pack either maps to standard SFS entries (`sfs.xp.award`,
`sfs.resource.set_max`) or introduces as sample-pack-specific entries
(those entries are vendor-prefixed, e.g. `sample.fighter.subclass_apply`,
and live in the sample pack's required functions list; hosts that want to
play the sample pack must implement them).

### 6.3 Rests
- `short_rest` - `Hours(1)`, `tags: {short}`.
- `long_rest` - `Hours(8)`, `tags: {long, heals, recovers_slots}`.

### 6.4 Death
- Trigger at `hp == 0`.
- Three `sfs.save.death` successes stabilize; three failures = Dead.
- Healing from 0 HP -> Alive with HP equal to healed amount.
- Massive damage instant death at `damage - remaining_hp >= max_hp`.

## 7. Open Questions

*(None at lock time.)*
