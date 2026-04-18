"""
CombatState, initiative, turn flow.

See specs/07-combat-mode.md for the normative definitions.

Combat is a Scene Mode. It reuses every type from prior files -- the stack,
effects, reactions, rules engine -- and adds a turn-order discipline.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, TYPE_CHECKING

from reference.domain import (
    EntityId, Event, EventKind, EventBatch,
    RollSpec, DieGroup, DieKindDn, RollPurpose, Modifier, ModifierSource,
    KeepRule,
)
from reference.rules import resolve_roll

if TYPE_CHECKING:
    from reference.engine import SessionState


# ---------------------------------------------------------------------------
# TurnPhase  (spec 07 Section 1)
# ---------------------------------------------------------------------------

class TurnPhase(Enum):
    """See spec 07 Section 1.

    Invariant C2: Turn phases follow StartOfTurn -> Main -> EndOfTurn ->
    BetweenTurns in strict order.
    """
    START_OF_TURN = "StartOfTurn"
    MAIN = "Main"
    END_OF_TURN = "EndOfTurn"
    BETWEEN_TURNS = "BetweenTurns"


# ---------------------------------------------------------------------------
# ActionEconomy  (spec 07 Section 1)
# ---------------------------------------------------------------------------

@dataclass
class ActionEconomy:
    """See spec 07 Section 1."""
    action_used: bool = False
    bonus_action_used: bool = False
    reaction_used: bool = False
    movement_remaining: int = 30     # default; pack-configurable
    free_actions_used: int = 0
    extras: dict[str, int] = field(default_factory=dict)

    @staticmethod
    def fresh(movement: int = 30) -> ActionEconomy:
        """Create a fresh action economy for a new turn."""
        return ActionEconomy(movement_remaining=movement)


# ---------------------------------------------------------------------------
# InitiativeOrder  (spec 07 Section 2)
# ---------------------------------------------------------------------------

@dataclass
class Tiebreaker:
    """See spec 07 Section 2.1.

    Sort cascade: (score desc, dex desc, entity_id asc).
    """
    dex: int
    entity_id: int


@dataclass
class InitiativeOrder:
    """See spec 07 Section 2."""
    order: list[EntityId] = field(default_factory=list)
    scores: dict[EntityId, int] = field(default_factory=dict)
    tiebreakers: dict[EntityId, Tiebreaker] = field(default_factory=dict)
    skipped_this_round: list[EntityId] = field(default_factory=list)


# ---------------------------------------------------------------------------
# CombatState  (spec 07 Section 1)
# ---------------------------------------------------------------------------

@dataclass
class CombatState:
    """See spec 07 Section 1."""
    round: int = 1
    initiative: InitiativeOrder = field(default_factory=InitiativeOrder)
    active_index: int = 0
    turn_phase: TurnPhase = TurnPhase.START_OF_TURN
    action_economy: dict[EntityId, ActionEconomy] = field(default_factory=dict)
    pending_turn_end: bool = False
    round_start_triggers_pending: bool = False

    @property
    def active_entity(self) -> EntityId | None:
        """Get the currently active entity's id."""
        if self.initiative.order and 0 <= self.active_index < len(self.initiative.order):
            return self.initiative.order[self.active_index]
        return None


# ---------------------------------------------------------------------------
# Initiative rolling  (spec 07 Section 2.1)
# ---------------------------------------------------------------------------

def initialize_combat(state: SessionState, entity_ids: list[EntityId]) -> tuple[SessionState, EventBatch]:
    """Roll initiative and set up combat state.

    See spec 07 Section 2.1.

    On entering combat mode, the engine:
    1. Rolls initiative for each entity (d20 + DEX modifier + features)
    2. Sorts by (score desc, dex desc, entity_id asc)
    3. Sets round=1, active_index=0, turn_phase=StartOfTurn
    """
    state = copy.deepcopy(state)
    events: EventBatch = []

    combat = CombatState()

    # Roll initiative for each entity in entity_id order (deterministic)
    for eid in sorted(entity_ids):
        entity = state.entities.get(eid)
        if entity is None:
            continue

        # Get DEX modifier for initiative
        dex_mod = 0
        init_bonus = 0
        stats = entity.get_stats()
        if stats:
            dex_score = stats.scores.get("dex", 10)
            dex_mod = (dex_score - 10) // 2
            init_bonus = stats.derived.get("initiative_bonus", dex_mod)

        # Roll d20 + initiative bonus
        spec = RollSpec(
            dice=[DieGroup(count=1, kind=DieKindDn(20), keep=KeepRule.ALL)],
            modifiers=[Modifier(value=init_bonus, source=ModifierSource(kind="Stat", value="dex"))],
            mode=RollPurpose.INITIATIVE,  # type: ignore[arg-type]
            purpose=RollPurpose.INITIATIVE,
        )
        # Use Straight mode for initiative
        spec.mode = RollPurpose.INITIATIVE  # type: ignore[assignment]
        from reference.domain import RollMode
        spec.mode = RollMode.STRAIGHT

        roll_result, state.rng = resolve_roll(spec, state.rng)

        combat.initiative.scores[eid] = roll_result.total
        combat.initiative.tiebreakers[eid] = Tiebreaker(
            dex=dex_mod, entity_id=eid,
        )

        event_id = state.next_event_id()
        events.append(Event(
            id=event_id,
            clock=dict(state.clocks),
            kind=EventKind.ROLL_MADE,
            source=eid,
            data={
                "actor": eid,
                "result": roll_result.to_dict(),
                "spec": spec.to_dict(),
            },
        ))

    # Sort: score desc, dex desc, entity_id asc
    # See spec 07 Section 2.1
    combat.initiative.order = sorted(
        combat.initiative.scores.keys(),
        key=lambda eid: (
            -combat.initiative.scores[eid],
            -combat.initiative.tiebreakers[eid].dex,
            combat.initiative.tiebreakers[eid].entity_id,
        ),
    )

    combat.round = 1
    combat.active_index = 0
    combat.turn_phase = TurnPhase.START_OF_TURN

    # Fresh action economy for all combatants
    for eid in combat.initiative.order:
        combat.action_economy[eid] = ActionEconomy.fresh()

    state.combat = combat

    # Emit ModeChanged event
    event_id = state.next_event_id()
    events.append(Event(
        id=event_id,
        clock=dict(state.clocks),
        kind=EventKind.MODE_CHANGED,
        data={"from": "Exploration", "to": "Combat"},
    ))

    # Emit TurnStarted for first entity
    if combat.initiative.order:
        first = combat.initiative.order[0]
        event_id = state.next_event_id()
        events.append(Event(
            id=event_id,
            clock=dict(state.clocks),
            kind=EventKind.TURN_STARTED,
            source=first,
            data={"entity": first, "round": 1},
        ))

    return state, events


# ---------------------------------------------------------------------------
# Turn flow  (spec 07 Section 3)
# ---------------------------------------------------------------------------

@dataclass
class Choice:
    """A legal action choice for the Driver."""
    id: str
    display: str
    data: dict[str, Any] = field(default_factory=dict)


def legal_actions(state: SessionState, entity_id: EntityId) -> list[Choice]:
    """Compute legal actions for an entity during Main phase.

    See spec 07 Section 4.1.

    The set is deterministic. A zero-action turn still offers EndTurn.
    """
    choices: list[Choice] = []

    if state.combat is None:
        return [Choice(id="end_turn", display="End Turn")]

    economy = state.combat.action_economy.get(entity_id)
    if economy is None:
        return [Choice(id="end_turn", display="End Turn")]

    # Check for available pack-defined actions
    if state.pack_data:
        actions = state.pack_data.get("actions", [])
        for action_def in actions:
            action_id = action_def.get("id", "")
            action_kind = action_def.get("kind", "Action")
            display_name = action_def.get("display_name", action_id)

            # Skip reactions (they fire via triggers, not selection)
            if action_kind == "Reaction":
                continue

            # Check economy
            if action_kind == "Action" and economy.action_used:
                continue
            if action_kind == "BonusAction" and economy.bonus_action_used:
                continue

            choices.append(Choice(
                id=action_id,
                display=display_name,
                data=action_def,
            ))

    # Always offer EndTurn (spec 07 Section 4.1)
    choices.append(Choice(id="end_turn", display="End Turn"))
    return choices


def advance_turn(state: SessionState) -> tuple[SessionState, EventBatch, dict | None]:
    """Advance the combat turn flow.

    See spec 07 Section 3.

    Returns (new_state, events, driver_request_or_none).
    driver_request is non-None when NeedsDecision (Main phase).

    Invariant C2: Phases follow StartOfTurn -> Main -> EndOfTurn -> BetweenTurns.
    """
    state = copy.deepcopy(state)
    events: EventBatch = []
    driver_request: dict | None = None

    if state.combat is None:
        return state, events, None

    combat = state.combat
    active = combat.active_entity
    if active is None:
        return state, events, None

    match combat.turn_phase:
        case TurnPhase.START_OF_TURN:
            # Emit TurnStarted event
            event_id = state.next_event_id()
            events.append(Event(
                id=event_id,
                clock=dict(state.clocks),
                kind=EventKind.TURN_STARTED,
                source=active,
                data={"entity": active, "round": combat.round},
            ))
            # Run trigger_scan for OnTurnStart (simplified)
            # Fresh action economy
            combat.action_economy[active] = ActionEconomy.fresh()
            combat.turn_phase = TurnPhase.MAIN
            return state, events, None

        case TurnPhase.MAIN:
            # Prompt the active entity's Driver for the next action
            # See spec 07 Invariant C1
            choices = legal_actions(state, active)
            if len(choices) == 1 and choices[0].id == "end_turn":
                # Auto-advance if only EndTurn is available
                combat.turn_phase = TurnPhase.END_OF_TURN
                event_id = state.next_event_id()
                events.append(Event(
                    id=event_id,
                    clock=dict(state.clocks),
                    kind=EventKind.TURN_ENDED,
                    source=active,
                    data={"entity": active},
                ))
                return state, events, None

            request_id = state.next_request_id_val()
            driver_request = {
                "type": "PromptDecision",
                "request_id": request_id,
                "prompt_kind": "ActionSelection",
                "context": {
                    "active": active,
                    "turn_phase": "Main",
                    "round": combat.round,
                },
                "legal_choices": [
                    {"id": c.id, "display": c.display} for c in choices
                ],
            }
            return state, events, driver_request

        case TurnPhase.END_OF_TURN:
            # Run trigger_scan for OnTurnEnd (simplified)
            combat.turn_phase = TurnPhase.BETWEEN_TURNS
            event_id = state.next_event_id()
            events.append(Event(
                id=event_id,
                clock=dict(state.clocks),
                kind=EventKind.TURN_ENDED,
                source=active,
                data={"entity": active},
            ))
            return state, events, None

        case TurnPhase.BETWEEN_TURNS:
            # Advance to next entity
            combat.active_index += 1
            if combat.active_index >= len(combat.initiative.order):
                combat.active_index = 0
                combat.round += 1
                # Round-start: tick the round clock
                state.clocks["round"] = state.clocks.get("round", 0) + 1
                event_id = state.next_event_id()
                events.append(Event(
                    id=event_id,
                    clock=dict(state.clocks),
                    kind=EventKind.CLOCK_TICKED,
                    data={"by": 1, "clock": "round"},
                ))

            # Immediately transition through StartOfTurn: emit TurnStarted,
            # set up fresh action economy, and advance to Main phase.
            # This avoids the engine needing two separate step() calls
            # for BetweenTurns -> StartOfTurn -> Main.
            new_active = combat.active_entity
            if new_active is not None:
                event_id = state.next_event_id()
                events.append(Event(
                    id=event_id,
                    clock=dict(state.clocks),
                    kind=EventKind.TURN_STARTED,
                    source=new_active,
                    data={"entity": new_active, "round": combat.round},
                ))
                combat.action_economy[new_active] = ActionEconomy.fresh()
            combat.turn_phase = TurnPhase.MAIN
            return state, events, None

    return state, events, None
