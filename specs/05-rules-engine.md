# 05 - Rules Engine

**Introduces**: `Challenge`, `CheckResult`, `ActionProposal`, `RulesContext`,
`ModifierCollector`, `AdvantageState`, `CritRule`, `take_action`,
`resolve_check`, `resolve_save`, `resolve_attack`, `gather_modifiers`,
`derive_stats`.
**Uses**: `00`, `01`, `02`, `03`, `04`.

This file specifies how the Engine interprets pack data to resolve checks,
saves, attacks, and actions. The rules engine is thin by design: its job is
to orchestrate SFS calls and maintain invariants. It is NOT a place to put
d20-specific knowledge - that lives in the pack.

## 1. Modifier Collection

Modifiers come from many sources (stats, proficiency, items, features,
effects, circumstances). The engine collects them deterministically:

```
gather_modifiers(state, context: ModifierCollectorContext) -> [Modifier]:
    m = []
    for source in modifier_sources_in_order(state, context):
        for modifier in source.modifiers:
            if modifier.applies_when evaluates to true in context:
                m.push(modifier)
    return m

modifier_sources_in_order(state, context):
    yield stats from context.actor
    yield proficiencies relevant to context.purpose
    yield equipped items (slot order, see 02.2.3)
    yield persistent effects on the actor (by install order)
    yield scene-level modifiers
    yield context.additional (from the calling Action)
```

Iteration order is fixed and deterministic. `applies_when` is a
`JsonFilter` evaluated against the context.

### Invariant RE1 (Modifier totality)
The total numeric bonus is the sum of `modifier.value` across all modifiers
whose `applies_when` is true. There is no capping, rounding, or reordering
by the Engine. Packs are responsible for any caps (e.g. "no more than one
bardic inspiration") via trigger conditions, not Engine logic.

## 2. AdvantageState

```
AdvantageState = { has_advantage: bool, has_disadvantage: bool }
```

- If both are true, net is `Straight` (cancel), regardless of how many
  sources contributed each. This is a pack convention baked into the
  `sfs.roll.d20` function; the Engine exposes the state via
  `ModifierCollectorContext.advantage_state` but does not enforce cancel
  policy.
- Sources contribute to advantage or disadvantage via a dedicated
  `ModifierSource::Circumstance` with a flag field `advantage: bool`.

## 3. Challenge

```
Challenge = {
    purpose: RollPurpose,                  // Attack / Save / Check / ...
    roll_spec: RollSpec,                   // pre-modifier dice
    target: ChallengeTarget,
    on_success: [EffectTemplateRef],
    on_failure: [EffectTemplateRef],
    on_partial: Option<[EffectTemplateRef]>,   // for ranged margins
    partial_thresholds: Option<[i32]>,     // e.g. [5, 10] for 3-tier
    crit_rule: Option<CritRule>,
    context: ModifierCollectorContext,
}
ChallengeTarget = FixedDC(i32) | Contested(EntityId, CheckId) | StatComparison(StatId, EntityId)
CritRule = { on_natural: u32, multiplier: u32, extra_effects: [EffectTemplateRef] }
```

## 4. resolve_check

```
resolve_check(state, challenge) -> (state', CheckResult, events):
    mods = gather_modifiers(state, challenge.context)
    modifier_total = sum(m.value for m in mods)
    advantage = collect advantage/disadvantage from mods; cancel if both
    spec = challenge.roll_spec with mode updated
    roll = sfs.roll.generic(spec, state)     // consumes RNG per schedule D2
    total = roll.total + modifier_total
    target_value = resolve_target(state, challenge.target)
    margin = total - target_value
    result = CheckResult {
        total, margin, roll, target: target_value,
        outcome: outcome_from_margin(margin, challenge.partial_thresholds),
        crit: is_crit(roll, challenge.crit_rule),
    }
    events = [RollMade(...), CheckResolved(...)]
    state' = apply_outcome_effects(state, challenge, result)
    return (state', result, events)
```

- `resolve_target` handles contested checks by issuing an additional
  `sfs.check.contested` call.
- `outcome_from_margin`:
  - `>= partial_thresholds[1]`: full success
  - `>= partial_thresholds[0]`: partial success
  - otherwise: failure
  - If no `partial_thresholds`, it is binary on `margin >= 0`.
- `is_crit` applies only when `challenge.crit_rule` is set and the roll's
  `natural` matches.

## 5. resolve_save

A save is a check whose target is a DC and whose purpose is a save. The
Engine uses the same `resolve_check` machinery with:

- `challenge.purpose = Save { save_id }`
- `challenge.target = FixedDC(dc)`
- `challenge.partial_thresholds = None`
- `challenge.crit_rule = None`

The `on_success` and `on_failure` effect lists are derived from the
triggering effect's template (e.g. fireball has "on success: half damage,
on failure: full damage"). The "half damage" portion is implemented as a
transformer on the damage sub-effects, not by duplicating the damage
effect.

## 6. resolve_attack

```
resolve_attack(state, attacker, defender, weapon_or_spell) -> (state', AttackResult, events):
    challenge = build_attack_challenge(attacker, defender, weapon_or_spell)
    (state', check_result, check_events) = resolve_check(state, challenge)
    if not check_result.outcome.is_success():
        return (state', AttackResult::Miss { roll: check_result }, check_events)
    if check_result.crit:
        damage_rolls = roll_damage(attacker, weapon_or_spell, crit=true)
    else:
        damage_rolls = roll_damage(attacker, weapon_or_spell, crit=false)
    damage_nodes = build_damage_effect_nodes(damage_rolls, defender)
    push onto stack
    return (state', AttackResult::Hit { roll: check_result, damage_nodes }, events)
```

- The damage nodes are pushed onto the effect stack, not applied directly.
  This is critical: it is the point at which reaction triggers
  (`Shield`-style, `Uncanny Dodge`-style) can fire.

### Invariant RE2 (Attack produces stack frames)
`resolve_attack` never directly mutates the defender's HP. It pushes an
`Effect::Damage` node onto the stack and relies on the resolution loop.

## 7. Actions

```
ActionProposal = {
    actor: EntityId,
    action_id: ActionId,
    targets: [Target],
    extra: JsonValue,         // action-specific arguments (e.g. spell slot level)
}
```

### 7.1 take_action

```
take_action(state, proposal) -> Transition:
    action_def = lookup_action(state, proposal.action_id)
    // 1. Prerequisites
    for prereq in action_def.prerequisites:
        if not check_prerequisite(state, proposal, prereq):
            return Errored(PrerequisiteUnmet, recoverable=true, state)
    // 2. Adjudication hint
    if action_def.adjudication matches AlwaysAdjudicate
        or (AdjudicateOnAmbiguity and is_ambiguous(state, proposal)):
        return NeedsDecision(Adjudicate { proposal, rules_context }, ...)
    // 3. Pay costs
    state = sfs.action.spend_economy(state, proposal)
    for cost in action_def.cost:
        state = sfs.resource.spend(state, proposal.actor, cost)
        // may return Recoverable if the pack allows "not enough slots"
    // 4. Instantiate and push effects
    for tmpl_ref in action_def.effects:
        node = instantiate_effect(state, tmpl_ref, proposal)
        state.stack.push(node)
    // 5. Return Yielded; the stack resolution loop takes over.
    return Yielded(events, state)
```

- Prerequisite checks are pure functions of state + proposal. They include
  line of sight, range, required proficiency, resource availability,
  reaction availability, and any pack-defined `PrerequisiteKind`.
- Adjudication is invoked only when the pack flags the action as needing
  it. Most actions resolve without Driver intervention beyond target
  selection.

### 7.2 PrerequisiteKind (closed enumeration)

```
PrerequisiteKind =
  | HasResource { id: ResourceId, amount: i32 }
  | HasActionEconomy { kind: ActionKind }
  | Proficient { with: ProficiencyId }
  | InRange { max: i32 }
  | InReach
  | LineOfSight
  | NotCondition { id: ConditionId }
  | HasEquipped { slot: SlotId, item_tag: ItemTag }
  | CustomSfs { function: SfsFunctionId }
```

- `CustomSfs` is the escape hatch; the pack declares an SFS function that
  returns a bool. Every other kind is handled by the Engine directly.

## 8. Derivation of Computed Stats

Certain stats are computed from others. Derivation is centralized so the
event log never disagrees with reality.

```
derive_stats(state, entity) -> Entity:
    e = entity.clone()
    for rule in state.pack.derived_stat_rules:
        e.components.Stats.derived[rule.id] = evaluate(rule.formula, e)
    return e
```

- `derived_stat_rules` is a closed list in the pack: each rule is a
  `DerivedStatRule { id, formula }` where `formula` is a tiny arithmetic
  tree (Add, Sub, Mul, Div, Const, StatRef, ProficiencyBonus, Clamp).
  This tree is evaluated by the Engine without SFS calls.
- The arithmetic tree is NOT a DSL. It has no variables, no loops, no
  function definitions. It is a constant-time evaluator over a fixed
  operator set. Maximum depth 8, enforced at load.
- Derivation is recomputed whenever a relevant source changes; the result
  is cached in `derived` for event-log consistency.

### Invariant RE3 (Cached derivations)
After every `ComponentSet` event touching `Stats`, `Resources`, or
`Inventory`, the Engine immediately recomputes derivations and emits
`ComponentSet` events for any changed `derived` values before the next
transition. Two conformant engines therefore produce byte-identical
sequences of derivation events.

## 9. Timing and Clocks

Time advances only via `Tick(clock_tag)` inputs from the Host. On tick:

```
on_tick(state, clock_tag):
    state.clocks[clock_tag] += 1
    events = [ClockTicked { clock, by: 1 }]
    // 1. Expiry scan for persistent effects
    for (entity, effect) in persistent_effects_referencing(clock_tag):
        if effect.expiry matches now:
            state.remove_persistent(entity, effect)
            events.push(PersistentEffectRemoved)
    // 2. OnClockTick triggers
    for trigger in triggers_on_tick(clock_tag):
        if trigger.condition.modulo == 0 or state.clocks[clock_tag] % modulo == 0:
            push trigger effect onto stack
    // 3. Resource recovery at clock-tick recovery rules
    for entity in state.entities:
        for resource in entity.resources:
            if resource.recovery matches OnClockTick(clock_tag):
                apply recovery
                events.push(ResourceChanged)
    return Yielded(events, state)
```

No wall clock is read. The Host owns pacing.

## 10. The Rules Engine Outer Loop

Putting it together, `step` is a dispatch:

```
step(state, input) -> Transition:
    match input:
        Start:
            if state.stack is non-empty:
                return resolve_top_of_stack(state)
            if state.pending_scene_transition is Some:
                return perform_scene_transition(state)
            if state.pending_turn_advance:
                return advance_turn(state)     // combat mode only; see 07
            // nothing to do; yield ambient tick
            return Yielded([], state)
        DriverResponse(id, resp):
            return apply_driver_response(state, id, resp)
        Tick(clock):
            return on_tick(state, clock)
        Halt:
            return Terminal([SessionEnded { reason: "halt" }])
```

`apply_driver_response` is a dispatch on the continuation's pending request
kind. Each branch is specified in the file that introduced the request
(combat reactions in `07`, level-up in `08`, action/target selection
here).

## 11. Thrust and Non-Thrust

The Rules Engine:
- Does NOT know about "classes", "levels", "spell slots", or "armor class"
  by name. Those are pack ids.
- Does NOT encode any math beyond summation, min/max, and `ScaleNumeric`
  fractions.
- Does NOT decide stacking rules (the pack does, via transformers and
  triggers).
- Does know about stack resolution, RNG consumption, event emission,
  Invariants RE1-RE3, and the driver contract dispatch.

If a reviewer finds the engine doing something pack-specific, that is a
bug in this spec.
