# 02 - Domain Model

**Introduces**: `EntityId`, `Entity`, `Component`, `Stats`, `Resources`,
`Inventory`, `Position`, `Knowledge`, `Faction`, `Controller`,
`PersistentEffects`, `RollSpec`, `RollResult`, `DieKind`, `Modifier`,
`DamageType`, `DamageInstance`, `SessionState`, `EventBatch`, `Event`,
`canonical_form`.
**Uses**: everything in `00` and `01`.

This file defines the pure data types referenced by every other spec. No
behavior. If a type seems to call for methods, those methods are defined in
`05-rules-engine.md` or later.

## 1. Entity

```
EntityId = u64                            // globally unique in a session
Entity = {
    id: EntityId,
    name: String,
    components: OrderedMap<ComponentTypeId, Component>,
}
```

- Entities have no fixed schema beyond `id` and `name`. All other state is a
  component keyed by `ComponentTypeId`.
- `OrderedMap` iterates in insertion order AND serializes keys sorted
  lexicographically (canonical form). The two orders are separate to
  preserve author intent while keeping the log deterministic.

### Invariant D1 (Unique ids)
Within a SessionState, every `EntityId` is unique. New entities are assigned
monotonically increasing ids via `state.next_entity_id`.

## 2. Component

`Component` is a tagged union. The spec defines core components. Packs may
register additional component kinds via the pack manifest (see
`04-content-packs-and-sfs.md`), but pack-custom components MUST serialize to
the same canonical form and MUST NOT reach outside the SFS for behavior.

### 2.1 Stats

```
Stats = {
    scores: OrderedMap<StatId, i32>,
    proficiencies: OrderedSet<ProficiencyId>,
    derived: OrderedMap<DerivedStatId, i32>,
}
```

- `scores` holds whatever the pack defines. The sample 5e-shaped pack uses
  `STR, DEX, CON, INT, WIS, CHA`.
- `proficiencies` is a flat set of tokens (`"athletics"`, `"longsword"`).
- `derived` is a cache of computed values (`armor_class`, `initiative_bonus`,
  `passive_perception`). The Rules Engine recomputes these on demand; they
  are stored in state only to keep the Event Log decoupled from derivation
  logic.

### 2.2 Resources

```
Resources = OrderedMap<ResourceId, ResourcePool>
ResourcePool = {
    current: i32,
    maximum: i32,
    recovery: RecoveryRule,
    tags: OrderedSet<ResourceTag>,
}
RecoveryRule =
  | OnRest { rest_kind: RestId, amount: RecoveryAmount }
  | OnClockTick { clock: ClockTag, amount: RecoveryAmount }
  | Manual
RecoveryAmount =
  | Full
  | Flat(i32)
  | Fraction { numerator: i32, denominator: i32 }      // ceil
```

- `current` MAY exceed `maximum` only via a transformer that explicitly
  allows it (temporary HP is NOT modeled as `hp.current > hp.maximum`; see
  `03-effects-and-stack.md` for the `TempHP` transformer).
- Going below 0 is allowed for resources that model debt (e.g. exhaustion
  counters); packs declare bounds via `ResourceTag::BoundBelow(n)` and
  `BoundAbove(n)`. The Engine enforces bounds on spend operations.

### 2.3 Inventory

```
Inventory = {
    items: [ItemInstance],
    equipped: OrderedMap<SlotId, ItemInstanceId>,
    attunements: OrderedSet<ItemInstanceId>,
}
ItemInstance = {
    id: ItemInstanceId,
    template_id: ItemTemplateId,    // references pack.items
    charges: Option<ResourcePool>,  // for wands, potions, etc.
    tags: OrderedSet<ItemTag>,
}
```

- `SlotId` is pack-defined. Sample 5e-shaped pack slots: `main_hand`,
  `off_hand`, `armor`, `head`, `amulet`, `ring_left`, `ring_right`, `cloak`,
  `boots`, `gloves`.
- Equipping/unequipping is an Action (`05-rules-engine.md`), not a direct
  state mutation.

### 2.4 Position

```
Position = {
    scene_id: SceneId,
    zone_id: ZoneId,
    offset: Option<GridOffset>,   // present only if the scene uses a grid
    facing: Option<Direction>,
}
```

- Zones are abstract; a grid tile is a zone with a 1x1 footprint.
- `GridOffset = (i32, i32)`; `Direction = N|NE|E|SE|S|SW|W|NW|Up|Down`.

### 2.5 Knowledge

```
Knowledge = {
    known_entities: OrderedMap<EntityId, KnowledgeLevel>,
    seen_events: OrderedSet<EventId>,
}
KnowledgeLevel = Unknown | Glimpsed | Observed | Identified
```

- Tracks what each Entity knows about others for fog-of-war, stealth, and
  pack-defined perception rules. The Engine updates Knowledge only via SFS
  functions invoked by Effects.

### 2.6 Faction

```
Faction = {
    primary: FactionId,
    disposition: OrderedMap<FactionId, i32>,  // -100..100
}
```

- The sample pack uses dispositions for reaction rolls and NPC behavior.
  The Engine uses them only as inputs to SFS functions.

### 2.7 Controller

```
Controller =
  | Player(PlayerId)
  | DriverOwned       // NPCs / monsters driven via Driver requests tagged as DM
  | Script(ScriptId)  // pack-declared behavior via SFS function sequence
```

- `Script` references a pack-declared behavior list. The Engine executes
  scripted controllers by issuing SFS calls in the declared order; it never
  runs arbitrary code.

### 2.8 PersistentEffects

```
PersistentEffects = [PersistentEffectInstance]
```

- Detailed in `03-effects-and-stack.md`. Listed here because an Entity is
  its components.

## 3. Dice and Rolls

### 3.1 DieKind

```
DieKind =
  | Dn(u32)              // d4, d6, d8, d10, d12, d20, d100, ...
  | Fudge                // -1/0/+1, for systems that want it
  | Constant(i32)        // for substituting fixed values in tests
```

### 3.2 RollSpec

```
RollSpec = {
    dice: [DieGroup],
    modifiers: [Modifier],
    mode: RollMode,
    purpose: RollPurpose,
}
DieGroup = { count: u32, kind: DieKind, keep: KeepRule }
KeepRule = All | KeepHighest(u32) | KeepLowest(u32) | DropHighest(u32) | DropLowest(u32)
RollMode = Straight | Advantage | Disadvantage | ElvenAccuracy | Custom(tag)
RollPurpose = Attack | Save { save_id } | Check { check_id } | Damage { damage_type } | InitiativeRoll | HitDice | Custom(tag)
```

### 3.3 Modifier

```
Modifier = { value: i32, source: ModifierSource, applies_when: ModifierCondition }
ModifierSource = Stat(StatId) | Proficiency | Feature(FeatureId) | Item(ItemInstanceId) | Effect(EffectInstanceId) | Circumstance(tag)
ModifierCondition = Always | Tagged(tag_set) | AgainstFaction(FactionId) | Custom(tag)
```

- Modifiers are collected from all sources by the Rules Engine at roll time.
  Stacking rules are specified in `05-rules-engine.md`.

### 3.4 RollResult

```
RollResult = {
    spec: RollSpec,
    raw: [DieRoll],
    kept: [DieRoll],
    modifier_total: i32,
    total: i32,
    natural: Option<u32>,         // for crit detection on d20s
}
DieRoll = { kind: DieKind, value: i32, dropped: bool }
```

### 3.5 Rng consumption schedule

### Invariant D2 (RNG determinism)
To resolve a `RollSpec`, the Engine consumes `next_u64` calls in this exact
order:

1. For each `DieGroup` in declaration order: consume `group.count`
   `u64` values, map each to `[1..=n]` for `Dn(n)` by
   `value = (u64_raw % n as u64) as i32 + 1` (rejection sampling
   uninstalled - documented bias is accepted as the canonical consumption
   schedule). For `Fudge`, map `[0..=2]` to `[-1, 0, 1]` by
   `value = ((u64_raw % 3) as i32) - 1`. For `Constant(c)`, no `u64`
   consumed; the value is `c`.
2. Apply `KeepRule` deterministically: stable-sort the group's rolls by
   (value desc, index asc), select per rule, mark non-kept as `dropped`.
3. Apply `RollMode` by re-running step 1 for duplicate groups where
   required (`Advantage`: roll twice, keep highest total; `Disadvantage`:
   twice, keep lowest; `ElvenAccuracy`: roll three, keep highest).

The schedule is normative. Any deviation breaks Invariant E5.

> **Bias note**: modulo mapping of a `u64` onto a die face introduces a
> vanishingly small bias for `n` not a power of two. This is documented,
> accepted, and part of the conformance contract. A future SFS version MAY
> introduce an unbiased `RngPolicy::RejectionSampling` mode; it will be
> versioned separately.

## 4. Damage

```
DamageType = pack_defined_string              // e.g. "fire", "slashing"
DamageInstance = {
    amount: i32,                               // post-transformer
    type: DamageType,
    source: EntityId,
    tags: OrderedSet<DamageTag>,               // "magical", "silvered", "critical"
    original_amount: i32,                      // pre-transformer, for logs
}
```

- `DamageInstance` is what hits the target after transformers. The stack
  holds pre-transformer `Effect::Damage` nodes; see
  `03-effects-and-stack.md`.

## 5. SessionState

```
SessionState = {
    schema_version: SemVer,
    sfs_version: SemVer,
    pack_ids: [PackId],
    entities: OrderedMap<EntityId, Entity>,
    scenes: OrderedMap<SceneId, Scene>,        // defined in 06
    active_scene: SceneId,
    stack: EffectStack,                        // defined in 03
    clocks: OrderedMap<ClockTag, u64>,
    next_entity_id: u64,
    next_effect_id: u64,
    next_request_id: u64,
    rng: Rng,                                  // opaque seeded state
    rng_policy: RngPolicy,
    event_log_hash_so_far: Sha256,
    open_continuation: Option<Continuation>,
    recoverable_retry_count: u32,
    max_recoverable_retries: u32,
}
RngPolicy = EngineOnly | DriverRolls | Hybrid
```

- `event_log_hash_so_far` is the rolling SHA-256 of the canonical Event Log
  up to the last appended record. It is part of state so that a crashed Host
  resuming from a snapshot can verify integrity of the persisted log tail.
- `open_continuation` is populated on `NeedsDecision` and cleared on the
  matching `DriverResponse`. Two outstanding continuations is a
  `FatalError::ProtocolViolation`.

### Invariant D3 (State totality)
Every field of `SessionState` is serializable to canonical JSON. No opaque
pointers, closures, or OS handles. A saved state round-trips to an
identical state via canonical serialization.

## 6. Events

```
EventBatch = [Event]
Event = {
    id: EventId,
    clock: OrderedMap<ClockTag, u64>,           // clocks at event time
    kind: EventKind,
    source: Option<EntityId>,
}
EventKind =
  | EntityCreated { entity: Entity }
  | EntityRemoved { id: EntityId }
  | ComponentSet { entity: EntityId, type: ComponentTypeId, component: Component }
  | ResourceChanged { entity: EntityId, resource: ResourceId, delta: i32, new_current: i32 }
  | RollMade { spec: RollSpec, result: RollResult, actor: Option<EntityId> }
  | AttackResolved { attacker: EntityId, defender: EntityId, hit: bool, crit: bool, damage: Option<DamageInstance> }
  | SaveResolved { target: EntityId, save: SaveId, dc: i32, success: bool }
  | CheckResolved { actor: EntityId, check: CheckId, dc: Option<i32>, total: i32, margin: i32 }
  | EffectPushed { instance: EffectInstance }
  | EffectResolved { instance_id: EffectInstanceId }
  | EffectExpired { instance_id: EffectInstanceId, reason: ExpiryReason }
  | PersistentEffectApplied { target: EntityId, instance: PersistentEffectInstance }
  | PersistentEffectRemoved { target: EntityId, instance_id: PersistentEffectInstanceId, reason: ExpiryReason }
  | ClockTicked { clock: ClockTag, by: u64 }
  | SceneEntered { scene: SceneId }
  | SceneExited { scene: SceneId }
  | ModeChanged { from: Mode, to: Mode }
  | TurnStarted { entity: EntityId, round: u32 }
  | TurnEnded { entity: EntityId }
  | ReactionOffered { to: EntityId, cause: EventId, options: [ReactionOption] }
  | ReactionTaken { by: EntityId, effect: EffectInstanceId }
  | Narration { driver_text: String }
  | DriverRequestEmitted { request_id: u64, request: DriverRequest }
  | DriverResponseRecorded { request_id: u64, response: DriverResponse }
  | Error { tier: ErrorTier, kind: String, detail: JsonValue }
  | LevelUpStarted { entity: EntityId, class: ClassId, to_level: u32 }
  | LevelUpStep { entity: EntityId, step: String, result: JsonValue }
  | LevelUpCompleted { entity: EntityId, class: ClassId, new_level: u32 }
  | SessionStarted { seed: [u64; 4] }
  | SessionEnded { reason: String }
```

The list is closed: adding a new variant is a breaking change and requires
a schema-version major bump.

## 7. Canonical Form

### Invariant D4 (Canonical JSON)
Every record appended to the Event Log is serialized by the following
rules before hashing:

1. Objects are emitted with keys sorted by UTF-8 byte order.
2. Strings use minimal-escape JSON (no `\u00XX` for printable ASCII).
3. Numbers are integers only in this spec. Any non-integer is a bug and
   MUST be rerepresented (use `(numerator, denominator)` pairs).
4. Whitespace: exactly one `\n` between records; no whitespace inside
   records.
5. Arrays preserve authorial order.
6. Unknown fields are rejected by conformant parsers; there is no
   "forward-compatible" field skipping within a major version.

A reference canonicalization implementation is sketched in
`09-persistence-and-replay.md`.
