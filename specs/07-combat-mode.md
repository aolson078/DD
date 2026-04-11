# 07 - Combat Mode

**Introduces**: `CombatState`, `InitiativeOrder`, `Turn`, `TurnPhase`,
`ActionEconomy`, `ReactionBudget`, `MovementBudget`, `OpportunityRules`.
**Uses**: `00`-`06`.

Combat is a Scene Mode. It reuses every type from prior files - the stack,
effects, reactions, rules engine - and adds a turn-order discipline.

## 1. CombatState

```
CombatState = {
    round: u32,
    initiative: InitiativeOrder,
    active_index: usize,                      // index into initiative.order
    turn_phase: TurnPhase,
    action_economy: OrderedMap<EntityId, ActionEconomy>,
    pending_turn_end: bool,
    round_start_triggers_pending: bool,
}

InitiativeOrder = {
    order: [EntityId],                         // initiative-sorted
    scores: OrderedMap<EntityId, InitiativeScore>,
    tiebreakers: OrderedMap<EntityId, Tiebreaker>,
    skipped_this_round: OrderedSet<EntityId>,  // e.g. incapacitated
}
InitiativeScore = i32
Tiebreaker = { dex: i32, entity_id: u64 }

TurnPhase = StartOfTurn | Main | EndOfTurn | BetweenTurns

ActionEconomy = {
    action_used: bool,
    bonus_action_used: bool,
    reaction_used: bool,
    movement_remaining: i32,                   // in zone-adjacent step units
    free_actions_used: u32,
    extras: OrderedMap<ActionKindId, u32>,     // for "extra attack" etc.
}
```

## 2. Initiative

### 2.1 Rolling

On entering combat mode, the Engine:

```
initialize_combat(state, scene_id) -> events:
    events = [ModeChanged(..)]
    for entity in scene.entities_present:
        roll = sfs.roll.initiative(state, entity)   // d20 + dex + features
        state.combat.scores[entity] = roll.total
        state.combat.tiebreakers[entity] = Tiebreaker { dex: entity.stats.DEX, entity_id: entity.id }
        events.push(RollMade, ...)
    state.combat.order = sort_by(scores desc, tiebreakers)
    state.combat.round = 1
    state.combat.active_index = 0
    state.combat.turn_phase = StartOfTurn
    events.push(TurnStarted { entity: order[0], round: 1 })
    return events
```

Sort is stable with the tiebreaker cascade `(score desc, dex desc, entity_id
asc)`. No randomness in tiebreaking.

### 2.2 Adding and removing combatants

- Summoning a new creature mid-combat inserts it at the bottom of initiative
  (pack-definable via `InsertionRule::AtBottom | AfterSummoner | RollNow`).
- An entity reduced to 0 HP or flagged `Incapacitated` is moved to
  `skipped_this_round` and retains its slot for future rounds.

## 3. Turn Flow

```
advance_turn(state) -> Transition:
    // Called when step() sees CombatState with no stack activity
    match state.combat.turn_phase:
        StartOfTurn:
            run trigger_scan for OnTurnStart(active_entity)
            state.combat.action_economy[active] = fresh economy
            state.combat.turn_phase = Main
            return Yielded(events, state)
        Main:
            // Prompt the active entity's Driver for the next action
            active_controller = state.entities[active].Controller
            choices = legal_actions(state, active)
            return NeedsDecision(PromptDecision {
                kind: PromptKind::ActionSelection,
                context: DecisionContext { active, turn_phase: Main, scene: active_scene },
                legal_choices: choices,
            }, continuation, state)
        EndOfTurn:
            run trigger_scan for OnTurnEnd(active_entity)
            run persistent effect expiry for UntilEndOfTurn(active_entity)
            state.combat.turn_phase = BetweenTurns
            return Yielded(events, state)
        BetweenTurns:
            state.combat.active_index += 1
            if state.combat.active_index >= len(order):
                state.combat.active_index = 0
                state.combat.round += 1
                run round-start effects; recover per-round resources
                events.push(ClockTicked { clock: "round", by: 1 })
            state.combat.turn_phase = StartOfTurn
            events.push(TurnStarted { entity: new_active, round })
            return Yielded(events, state)
```

### Invariant C1 (Main always yields)
When `turn_phase = Main`, `advance_turn` always returns `NeedsDecision`
unless legal_actions is empty, in which case it auto-advances with an
`EndTurn` action emitted as a no-op (logged as an event).

### Invariant C2 (No skipped phases)
Turn phases follow `StartOfTurn -> Main -> EndOfTurn -> BetweenTurns` in
strict order. Even a dead entity's turn steps through all four phases to
preserve trigger symmetry; it just has no legal actions.

## 4. Action Selection Loop in Combat

During `Main` phase, the Driver is prompted repeatedly until it chooses
`EndTurn`. After each chosen action:

```
after_action(state):
    run stack resolution to completion (possibly including reactions)
    if end_turn implicit conditions met (no resources, incapacitated, pack-defined):
        force end of turn
    else:
        return to Main, prompt again
```

### 4.1 legal_actions

```
legal_actions(state, entity) -> [Choice]:
    choices = []
    for action in state.pack.actions:
        if action.kind is Reaction: skip (reactions fire via triggers, not selection)
        if prerequisites fail in any way: skip (reason logged only for debug)
        choices.push(Choice { id, display, targets_preview })
    choices.push(EndTurn choice)
    return choices
```

- The set is deterministic: iteration order is pack-declared order for
  actions, stable across implementations.
- A zero-action turn still offers `EndTurn`.

## 5. Reactions in Combat

Reactions in combat use the general reaction machinery from
`03-effects-and-stack.md`. Combat adds:

### 5.1 ReactionBudget

Each entity has `action_economy.reaction_used: bool`. Taking a reaction sets
it to `true`; round start (or feature-defined refresh) resets it.

### Invariant C3 (Reaction budget honored)
A reaction trigger with `reaction: true` is only legal if
`state.combat.action_economy[reactor].reaction_used == false`. If the
Engine presents such an option when the budget is spent, it is
`FatalError::InvariantViolation(C3)`.

### 5.2 Opportunity attacks

```
OpportunityRules = {
    provokes_on: [MovementKind],             // Walk, Stand, ItemSwap, ...
    requires_hostile: bool,
    requires_reach: bool,
    trigger_template: TriggerId,
}
```

Opportunity attacks are triggers with condition `OnActionDeclared(move)`
filtered on leaving a zone adjacent to a hostile with reach. The Engine
emits `ReactionOffered` to eligible reactors BEFORE the move completes; if
a reaction hits and drops the mover to 0 HP, the move is canceled via the
standard stack resolution rules.

## 6. Area Effects

Area effects target `Target::AllIn { zone, filter }` or `Target::Zone`. The
resolver expands the target into a concrete entity list at push time, not
at resolve time, so reactions cannot dodge by moving out while the stack
is resolving.

### Invariant C4 (Target freeze at push)
`EffectNode.targets` is frozen at push; subsequent state changes do not
alter which entities are targeted. Dodging is modeled via save
transformers, not target deletion.

## 7. Exiting Combat

Combat ends when one of the following is true:

- All entities on one faction side are incapacitated or have fled.
- A pack-defined `CombatEndCondition` SFS function returns true.
- The Driver issues `Adjudicate` with a `FleeScene` proposal that is
  approved.

On exit:

```
exit_combat(state) -> events:
    events.push(ModeChanged { from: Combat, to: pack-chosen_post_combat_mode })
    state.scenes[active].state = new mode's initial state
    state.combat = None   (stored as Option<CombatState> on the scene)
    // action_economy cleared
    return events
```

The post-combat mode defaults to `Exploration`; packs may override.

## 8. Open Questions

*(None at lock time.)*
