# 03 - Effects and the Stack

**Introduces**: `Effect`, `EffectInstance`, `EffectInstanceId`, `EffectNode`,
`EffectStack`, `Trigger`, `TriggerId`, `Transformer`, `TransformerPhase`,
`PersistentEffectInstance`, `PersistentEffectInstanceId`, `ExpiryPredicate`,
`ExpiryReason`, `ReactionOption`, `ReactionWindow`.
**Uses**: `00`, `01`, `02`.

This file is the hardest problem in the spec. Every other system leans on
this one. Read it twice.

## 1. Motivation

TTRPGs produce effects in an order that is not the order they resolve. A
fireball triggers concentration checks on everyone holding a spell, which
may fail, ending effects, which may themselves trigger further saves. A
counterspell interrupts the fireball before it ever resolves. An "on
damaged" ability on the target cares whether damage is reduced by resistance
first. None of this can be modeled by "apply effects immediately".

We borrow the priority-stack pattern from Magic: the Gathering, simplified
for TTRPG-shaped needs.

## 2. EffectNode

```
EffectNode = {
    id: EffectInstanceId,             // freshly allocated at push time
    source: EntityId,
    source_ability: Option<AbilityId>,
    targets: [Target],
    body: EffectBody,
    transformers: [Transformer],      // collected from the env at push time
    timing: ResolutionTiming,
    pushed_at: StackIndex,             // for tiebreakers
}

Target =
  | Self
  | Entity(EntityId)
  | Zone(ZoneId)
  | AllIn { zone: ZoneId, filter: TargetFilter }
  | Point(GridOffset)

EffectBody =
  | Damage { amounts: [DamageRoll] }       // multiple on-hit dice
  | Heal { amount: HealRoll }
  | ApplyPersistent { template: PersistentEffectTemplateId, duration: Duration }
  | RemovePersistent { match: PersistentEffectMatcher }
  | GrantResource { resource: ResourceId, amount: i32 }
  | SpendResource { resource: ResourceId, amount: i32 }
  | MoveEntity { to: Position }
  | SfsCall { function: SfsFunctionId, args: JsonValue }

ResolutionTiming = Immediate | OnStackResolve | DeferredTo { clock: ClockTag, at: u64 }
```

- `body` is declarative. The Engine never resolves bodies with ad-hoc code;
  every resolution path is an SFS function call. `Damage`, `Heal`, etc. are
  sugar over standard SFS functions (`sfs.damage.typed`, `sfs.heal.flat`).
- `SfsCall` is the escape hatch for richer packs. It identifies a named host
  function and passes a JSON value as arguments. The Engine validates the
  argument shape against the SFS signature before resolution.

## 3. The Stack

```
EffectStack = [StackFrame]
StackFrame = {
    node: EffectNode,
    phase: StackFramePhase,
    reaction_window: Option<ReactionWindow>,
}
StackFramePhase = Queued | AwaitingReactions | Resolving | Resolved
```

- The stack is LIFO. Pushes append; resolves consume from the top.
- Between any two frame resolutions, the Engine performs a **trigger scan**
  (see Section 4). Newly produced frames may land on top of the current
  frame, delaying its resolution. This is how reactions interrupt.

### Invariant S1 (Single ownership)
An `EffectInstanceId` appears in at most one stack frame at a time. After
resolution it MAY reappear as a `PersistentEffectInstanceId` on an entity
(e.g. a `Bless` effect resolves and installs a persistent buff).

### Invariant S2 (Bounded stack depth)
`EffectStack.len() <= MAX_STACK_DEPTH`. Default `MAX_STACK_DEPTH = 128`.
Overflow is `FatalError::InvariantViolation(S2)` - indicates an infinite
trigger loop in the pack.

### Invariant S3 (Deterministic ordering of ties)
When multiple triggers fire at the same moment, they are ordered by:
1. The `trigger_priority` field on the trigger template (lower first).
2. The `pushed_at` of the causing event (earlier first).
3. The `EntityId` of the reactor (lower first).
4. The lexicographic order of `TriggerId`.

No implicit ordering by map iteration or hash. Ties are always fully
resolved.

## 4. Triggers

```
Trigger = {
    id: TriggerId,
    owner: EntityOrScene,
    on: TriggerCondition,
    effect_template: EffectTemplateId,    // template, instantiated on fire
    cost: Option<ResourceCost>,
    priority: i32,
    one_shot: bool,
}

TriggerCondition =
  | OnEvent { kind: EventKindTag, filter: JsonFilter }
  | OnEnterZone { entity_filter: EntityFilter, zone: ZoneRef }
  | OnTurnStart { for: EntityFilter }
  | OnTurnEnd   { for: EntityFilter }
  | OnClockTick { clock: ClockTag, modulo: u64 }
  | OnDamaged { target: EntityFilter, damage_filter: DamageFilter }
  | OnSaveFailed { save: SaveFilter }
  | OnActionDeclared { action_filter: ActionFilter }
  | Custom(TriggerCustomTag)
```

- `JsonFilter` is a minimal pattern language covering equality, `in`, `lt`,
  `gt`, and `and`/`or`. It is NOT Turing-complete. Full grammar in Appendix
  A of this file.
- `TriggerCondition::Custom` dispatches to an SFS function that returns a
  bool; the pack declares which SFS function.

### 4.1 Trigger Scan

Between frame resolutions, the Engine runs:

```
scan(state):
    candidates = []
    for event in state.events_since_last_scan:
        for trigger in state.all_triggers():
            if matches(trigger, event):
                candidates.push((trigger, event))
    sort(candidates, by invariant S3)
    for (trigger, event) in candidates:
        if trigger.cost is Some and not state.can_pay(trigger.owner, trigger.cost):
            continue
        if trigger is reaction:
            // yields NeedsDecision to the reactor's Driver
            push ReactionOption(trigger, event) onto state.pending_reaction_offers
        else:
            push EffectNode(instantiate(trigger, event)) onto stack
    if state.pending_reaction_offers is non-empty:
        yield NeedsDecision(PromptDecision{kind: ReactionOptIn, ...})
    state.events_since_last_scan = []
```

- `events_since_last_scan` is cleared even if no triggers matched, to avoid
  re-scanning the same events.
- A trigger may fire multiple times per scan if it matches multiple events;
  `one_shot: true` triggers deactivate after the first match in a session.

### Invariant S4 (No retroactive triggers)
A trigger cannot match events that predate its own activation. Activation
timestamp is recorded at trigger install time; events before that timestamp
are never candidates.

## 5. Reactions

Reactions are a specialization of triggers.

```
ReactionWindow = {
    cause_event: EventId,
    eligible: [EntityId],
    options_per_entity: OrderedMap<EntityId, [ReactionOption]>,
    decisions_collected: OrderedMap<EntityId, ReactionDecision>,
}
ReactionOption = {
    trigger_id: TriggerId,
    label: String,                // for Driver display
    resource_cost: ResourceCost,
    legal: bool,                  // precomputed by the Engine
    illegal_reason: Option<String>,
}
ReactionDecision = Take(TriggerId) | Pass
```

### 5.1 Flow

1. A `ReactionWindow` opens when the Trigger Scan finds one or more
   reaction-priced triggers matching the last event.
2. The Engine emits `ReactionOffered{to, cause, options}` events for each
   eligible entity (order: lower `EntityId` first).
3. The Engine then emits `NeedsDecision{PromptDecision{kind: ReactionOptIn,
   legal_choices: [Take|Pass for each option]}}` for the first eligible
   entity whose decision has not been collected. This is sequential, not
   parallel, because a taken reaction may shift legality for later reactors.
4. On each response, the Engine appends `ReactionTaken` (if taken) and
   pushes the reaction effect onto the stack.
5. When all eligible reactors have responded, the window closes and
   resolution continues.

### Invariant R1 (Single-reaction-per-entity-per-cause)
Within one `ReactionWindow`, a given entity contributes at most one
reaction. Multiple triggers on the same entity are presented as alternatives
in `options_per_entity`. Taking one consumes the window for that entity.

### Invariant R2 (Reaction resource first)
A reaction Effect's resource cost is committed to `SpendResource` BEFORE
the reaction body pushes anything else onto the stack. If the cost cannot
be paid, the reaction is illegal and not offered.

## 6. Transformers

```
Transformer = {
    id: TransformerId,
    phase: TransformerPhase,
    applies_to: EffectPredicate,
    transform: TransformSpec,
    source: TransformerSource,
    priority: i32,
}
TransformerPhase = PreTarget | PostTarget | PreResolve | PostResolve
EffectPredicate = JsonFilter over EffectNode
TransformSpec =
  | ScaleNumeric { field: FieldPath, numerator: i32, denominator: i32 }
  | AddFlat      { field: FieldPath, amount: i32 }
  | SubstituteDamageType { from: DamageType, to: DamageType }
  | NullifyIfTagged { tag: DamageTag }
  | SetField { field: FieldPath, value: JsonValue }
```

- `ScaleNumeric(1,2)` halves; `ScaleNumeric(2,1)` doubles; `ScaleNumeric(0,1)`
  zeroes. Fractional results round down (explicit, deterministic).
- Transformer order within a phase is `priority` ascending, ties broken by
  `source` (effect source id), then `TransformerId` lexicographic.

### 6.1 Canonical transformers

The SFS ships these as standard transformers. Packs instantiate them.

- **Resistance**: `ScaleNumeric(1,2)` on `body.amounts[].amount` where the
  damage `type` matches, `PreResolve`, priority 100.
- **Vulnerability**: `ScaleNumeric(2,1)`, priority 110.
- **Immunity**: `SetField(body.amounts[].amount = 0)`, priority 90.
- **Half on save**: `ScaleNumeric(1,2)` on `body.amounts[].amount`, only
  applied on successful save by a SFS-evaluated condition, priority 120.
- **Temporary HP absorbtion**: `SubstituteTarget` (not a numeric transformer;
  modeled as a `PreResolve` transformer that redirects damage into a temp-HP
  pool, zeroing it before the remainder reaches HP).

### Invariant T1 (Transformer purity)
A transformer is a pure function of its inputs. It MUST NOT read or write
game state outside the `EffectNode` it transforms. Side effects go through
new stack pushes.

### Invariant T2 (Stacking order is documented)
Resistance + vulnerability interact per 5e-shaped sample rules: if both
apply, they cancel (i.e., net damage is normal). This is encoded by
sample-pack transformers that check for the counterpart in the effect's
`transformers` list, not by special Engine logic.

## 7. Persistent Effects

```
PersistentEffectInstance = {
    id: PersistentEffectInstanceId,
    template_id: PersistentEffectTemplateId,
    source: EntityId,
    target: EntityOrScene,
    applied_at: OrderedMap<ClockTag, u64>,
    duration: Duration,
    expiry: ExpiryPredicate,
    grants_triggers: [TriggerId],
    grants_transformers: [TransformerId],
    concentration: Option<ConcentrationInfo>,
    tags: OrderedSet<EffectTag>,
}
Duration = Rounds(u32) | Minutes(u32) | Hours(u32) | Days(u32) | UntilDispelled | UntilEndOfTurn(EntityId) | UntilExpiryPredicate
ExpiryPredicate =
  | TimeElapsed { clock: ClockTag, amount: u64 }
  | EventMatches { kind: EventKindTag, filter: JsonFilter }
  | Composite { any: [ExpiryPredicate] }
```

- When applied, an instance's `grants_triggers` and `grants_transformers`
  become active in the trigger/transformer scans.
- When expired (predicate fires, concentration broken, dispelled), those
  grants are removed. Any in-progress reactions granted by the expiring
  effect are canceled (if the window is still open).

### 7.1 Concentration

```
ConcentrationInfo = {
    concentrating_entity: EntityId,
    break_on: ConcentrationBreakRule,
}
ConcentrationBreakRule = {
    break_on_damage: bool,                 // trigger a CON save
    break_on_incapacitate: bool,
    break_on_new_concentration: bool,
    save: Option<SaveSpec>,                // DC = max(10, damage/2)
}
```

- At most one concentration effect per entity (`break_on_new_concentration`
  enforces this by ending the prior one when a new concentration begins).
- Concentration saves are triggered by damage events against the
  concentrator. The save spec comes from the pack.

### Invariant P1 (Concentration uniqueness)
`state.entities[id].persistent_effects` contains at most one
`PersistentEffectInstance` with `concentration.is_some()`. Pushing a second
one immediately expires the first via `grants_transformers` + a synthetic
`PersistentEffectRemoved` event.

## 8. The Resolution Loop

```
resolve_top_of_stack(state):
    if stack is empty: return state
    frame = stack.top()
    match frame.phase:
        Queued:
            run trigger_scan(state)         // may push more frames on top
            frame.transformers = gather_transformers(frame.node, state)
            frame.phase = AwaitingReactions
            return state                     // step() returns; next call continues
        AwaitingReactions:
            open_reaction_window(frame)      // may yield NeedsDecision
            frame.phase = Resolving
            return state
        Resolving:
            node = apply_transformers(frame.node, frame.transformers)
            events = sfs_dispatch(node.body, state)
            append events to state
            frame.phase = Resolved
            return state
        Resolved:
            pop frame
            run trigger_scan(state)
            return state
```

The loop is driven by `step()` from `01-execution-model.md`. Each phase
transition is one `step` call's worth of work. Reaction windows interleave
cleanly with this because they only appear at `AwaitingReactions`, and the
Engine yields `NeedsDecision` there without dropping the frame.

### Invariant L1 (Stack progress)
Between any two `NeedsDecision` yields, the stack's total frame count
either decreases or stays equal while advancing at least one frame's
`StackFramePhase`. This rules out livelock (no stack progress without
Driver input).

## 9. Appendix A: JsonFilter Grammar

```
JsonFilter =
  | Eq { path: FieldPath, value: JsonValue }
  | In { path: FieldPath, set: [JsonValue] }
  | Lt { path: FieldPath, value: i64 }
  | Gt { path: FieldPath, value: i64 }
  | Has { path: FieldPath }                   // key exists
  | Tagged { path: FieldPath, tag: String }   // element of a set field
  | And { all: [JsonFilter] }
  | Or  { any: [JsonFilter] }
  | Not { inner: JsonFilter }
```

- `FieldPath` is a dot-path with array indexing by literal index only:
  `body.amounts.0.type`. No wildcards, no functions. Deliberate.
- Evaluation is bounded: `And`/`Or` short-circuit, `Not` is constant-time
  modulo its inner. Maximum nesting depth is 16 (rejected at pack load).
- This grammar is intentionally dumb. If a pack needs more, it uses an
  SFS function.
