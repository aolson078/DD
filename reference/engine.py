"""
Core engine: step(), Transition, SessionState.

See specs/01-execution-model.md for the normative definitions.

The engine is a pure state machine. step(state, input) -> Transition.
No I/O, no time reads, no threading, no global mutation.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from reference.domain import (
    EntityId, Event, EventKind, EventBatch,
    Entity, Stats, Resources, ResourcePool, Position, Faction,
    Controller, PlayerController,
)
from reference.rng import Rng, seed_rng
from reference.effects import EffectStack
from reference.combat import CombatState, advance_turn, TurnPhase
from reference.canonical import canonical_hash, update_rolling_hash


# ---------------------------------------------------------------------------
# StepInput  (spec 01 Section 2)
# ---------------------------------------------------------------------------

class StepInputKind(Enum):
    """See spec 01 Section 2."""
    START = "Start"
    DRIVER_RESPONSE = "DriverResponse"
    TICK = "Tick"
    HALT = "Halt"


@dataclass
class StepInput:
    """See spec 01 Section 2.

    StepInput =
      | Start
      | DriverResponse(request_id, value)
      | Tick(clock_tag)
      | Halt
    """
    kind: StepInputKind
    request_id: int | None = None
    value: Any = None
    clock_tag: str | None = None

    @staticmethod
    def start() -> StepInput:
        return StepInput(kind=StepInputKind.START)

    @staticmethod
    def driver_response(request_id: int, value: Any) -> StepInput:
        return StepInput(kind=StepInputKind.DRIVER_RESPONSE,
                         request_id=request_id, value=value)

    @staticmethod
    def tick(clock_tag: str) -> StepInput:
        return StepInput(kind=StepInputKind.TICK, clock_tag=clock_tag)

    @staticmethod
    def halt() -> StepInput:
        return StepInput(kind=StepInputKind.HALT)


# ---------------------------------------------------------------------------
# Transition  (spec 01 Section 3)
# ---------------------------------------------------------------------------

class TransitionKind(Enum):
    """See spec 01 Section 3."""
    YIELDED = "Yielded"
    NEEDS_DECISION = "NeedsDecision"
    TERMINAL = "Terminal"
    ERRORED = "Errored"


@dataclass
class Transition:
    """See spec 01 Section 3.

    Transition =
      | Yielded(events, next_state)
      | NeedsDecision(request, continuation, next_state)
      | Terminal(final_events)
      | Errored(error, recoverable, next_state)

    Invariant E3: Exactly one variant is returned per step() call.
    """
    kind: TransitionKind
    events: EventBatch = field(default_factory=list)
    next_state: SessionState | None = None
    request: dict | None = None
    continuation: dict | None = None
    error: dict | None = None
    recoverable: bool = False

    @staticmethod
    def yielded(events: EventBatch, next_state: SessionState) -> Transition:
        """See spec 01 Section 3 - Yielded."""
        return Transition(
            kind=TransitionKind.YIELDED,
            events=events,
            next_state=next_state,
        )

    @staticmethod
    def needs_decision(request: dict, continuation: dict,
                       next_state: SessionState) -> Transition:
        """See spec 01 Section 3 - NeedsDecision."""
        return Transition(
            kind=TransitionKind.NEEDS_DECISION,
            request=request,
            continuation=continuation,
            next_state=next_state,
        )

    @staticmethod
    def terminal(final_events: EventBatch) -> Transition:
        """See spec 01 Section 3 - Terminal."""
        return Transition(
            kind=TransitionKind.TERMINAL,
            events=final_events,
        )

    @staticmethod
    def errored(error: dict, recoverable: bool,
                next_state: SessionState) -> Transition:
        """See spec 01 Section 3 - Errored."""
        return Transition(
            kind=TransitionKind.ERRORED,
            error=error,
            recoverable=recoverable,
            next_state=next_state,
        )


# ---------------------------------------------------------------------------
# EngineError  (spec 01 Section 6)
# ---------------------------------------------------------------------------

@dataclass
class EngineError:
    """See spec 01 Section 6."""
    tier: str          # "Fatal" or "Recoverable"
    kind: str          # e.g. "ProtocolViolation", "ResourceExhausted"
    detail: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# SessionState  (spec 02 Section 5)
# ---------------------------------------------------------------------------

@dataclass
class SessionState:
    """See spec 02 Section 5.

    Every field is serializable to canonical JSON. No opaque pointers,
    closures, or OS handles. Invariant D3.
    """
    schema_version: str = "1.0.0"
    sfs_version: str = "1.0.0"
    pack_ids: list[str] = field(default_factory=list)
    entities: dict[EntityId, Entity] = field(default_factory=dict)
    scenes: dict[str, dict] = field(default_factory=dict)
    active_scene: str = ""
    stack: EffectStack = field(default_factory=EffectStack)
    clocks: dict[str, int] = field(default_factory=dict)
    _next_entity_id: int = 1
    _next_effect_id: int = 1
    _next_request_id: int = 1
    _next_event_id: int = 1
    rng: Rng = field(default_factory=lambda: seed_rng([42, 0, 0, 0]))
    rng_policy: str = "EngineOnly"
    event_log_hash_so_far: str = "0" * 64
    open_continuation: dict | None = None
    recoverable_retry_count: int = 0
    max_recoverable_retries: int = 3

    # Combat state (Option<CombatState> per spec 07)
    combat: CombatState | None = None

    # Pack data loaded via pack_loader
    pack_data: dict | None = None

    # Session state flags
    started: bool = False
    halted: bool = False

    # Mode tracking
    mode: str = "Exploration"

    def next_entity_id_val(self) -> int:
        """Allocate a new EntityId. See spec 02 Invariant D1."""
        eid = self._next_entity_id
        self._next_entity_id += 1
        return eid

    def next_effect_id_val(self) -> int:
        """Allocate a new EffectInstanceId."""
        eid = self._next_effect_id
        self._next_effect_id += 1
        return eid

    def next_request_id_val(self) -> int:
        """Allocate a new RequestId."""
        rid = self._next_request_id
        self._next_request_id += 1
        return rid

    def next_event_id(self) -> int:
        """Allocate a new EventId."""
        eid = self._next_event_id
        self._next_event_id += 1
        return eid

    def add_entity(self, name: str, **components: Any) -> Entity:
        """Create and register a new entity."""
        eid = self.next_entity_id_val()
        entity = Entity(id=eid, name=name, components=components)
        self.entities[eid] = entity
        return entity


# ---------------------------------------------------------------------------
# step()  (spec 01 Section 1, spec 05 Section 10)
# ---------------------------------------------------------------------------

def step(state: SessionState, input: StepInput) -> Transition:
    """The core engine operation.

    See spec 01 Section 1 and spec 05 Section 10.

    step(state, input) -> Transition

    - state is immutable; step returns a new state inside the Transition.
    - step performs no I/O, no time reads, no threading, no global mutation.
    - step is a total function: every (state, input) pair maps to exactly
      one Transition (Invariant E1).
    """
    # Deep copy state so we don't mutate the caller's copy
    state = copy.deepcopy(state)

    match input.kind:
        case StepInputKind.START:
            return _step_start(state)

        case StepInputKind.DRIVER_RESPONSE:
            return _step_driver_response(state, input)

        case StepInputKind.TICK:
            return _step_tick(state, input)

        case StepInputKind.HALT:
            return _step_halt(state)

    # Should never reach here; defensive
    return Transition.errored(
        {"tier": "Fatal", "kind": "InvariantViolation",
         "detail": {"invariant_id": "E3", "where": "step"}},
        recoverable=False,
        next_state=state,
    )


def _step_start(state: SessionState) -> Transition:
    """Handle Start input.

    See spec 05 Section 10:
    - If stack is non-empty: resolve_top_of_stack
    - If pending_turn_advance: advance_turn (combat mode)
    - Otherwise: session startup or idle yield
    """
    events: EventBatch = []

    # Session startup
    if not state.started:
        state.started = True
        event_id = state.next_event_id()
        seed_data = [state.rng.s0, state.rng.s1, state.rng.s2, state.rng.s3]
        events.append(Event(
            id=event_id,
            clock=dict(state.clocks),
            kind=EventKind.SESSION_STARTED,
            data={"seed": seed_data},
        ))

        # If combat entities are loaded, auto-enter combat
        if state.combat is not None:
            return Transition.yielded(events, state)

        return Transition.yielded(events, state)

    # 1. Effect stack resolution
    if not state.stack.is_empty():
        from reference.effects import resolve_top_of_stack
        state.stack, stack_events, needs_decision = resolve_top_of_stack(
            state.stack, state
        )
        events.extend(stack_events)
        if events:
            return Transition.yielded(events, state)
        # If no events but stack still has work, return a no-op yield
        # to let the host loop call again
        if not state.stack.is_empty():
            return Transition.yielded(
                [Event(id=state.next_event_id(), clock=dict(state.clocks),
                       kind=EventKind.EFFECT_RESOLVED, data={})],
                state,
            )

    # 2. Combat turn advance
    if state.combat is not None:
        state, turn_events, driver_request = advance_turn(state)
        events.extend(turn_events)

        if driver_request is not None:
            # NeedsDecision for action selection
            request_id = driver_request.get("request_id", 0)
            state.open_continuation = {"type": "action_selection", "request_id": request_id}
            return Transition.needs_decision(
                request=driver_request,
                continuation=state.open_continuation,
                next_state=state,
            )

        if events:
            return Transition.yielded(events, state)

        # Combat is active but no events were produced (e.g. StartOfTurn
        # phase with no triggers). Yield a synthetic event to keep the
        # host loop calling step() so combat progresses.
        # See spec 07 Invariant C2: all phases must be stepped through.
        active = state.combat.active_entity
        return Transition.yielded(
            [Event(
                id=state.next_event_id(),
                clock=dict(state.clocks),
                kind=EventKind.TURN_STARTED,
                source=active,
                data={"entity": active, "phase": state.combat.turn_phase.value,
                      "round": state.combat.round},
            )],
            state,
        )

    # Nothing to do
    if not events:
        events.append(Event(
            id=state.next_event_id(),
            clock=dict(state.clocks),
            kind=EventKind.SESSION_ENDED,
            data={"reason": "idle"},
        ))
        return Transition.terminal(events)

    return Transition.yielded(events, state)


def _step_driver_response(state: SessionState, input: StepInput) -> Transition:
    """Handle DriverResponse input.

    See spec 01 Section 3 - NeedsDecision and spec 05 Section 10.
    """
    events: EventBatch = []

    if state.open_continuation is None:
        return Transition.errored(
            {"detail": "No open continuation for DriverResponse",
             "kind": "ProtocolViolation", "tier": "Fatal"},
            recoverable=False,
            next_state=state,
        )

    # Validate request_id matches
    expected_id = state.open_continuation.get("request_id")
    if expected_id is not None and input.request_id != expected_id:
        return Transition.errored(
            {"detail": f"Expected request_id {expected_id}, got {input.request_id}",
             "kind": "ProtocolViolation", "tier": "Fatal"},
            recoverable=False,
            next_state=state,
        )

    continuation_type = state.open_continuation.get("type", "")
    state.open_continuation = None  # Clear continuation

    if continuation_type == "action_selection":
        return _handle_action_selection(state, input, events)

    # Generic continuation handling
    return Transition.yielded(events or [
        Event(id=state.next_event_id(), clock=dict(state.clocks),
              kind=EventKind.DRIVER_RESPONSE_RECORDED,
              data={"request_id": input.request_id, "response": input.value})
    ], state)


def _handle_action_selection(state: SessionState, input: StepInput,
                             events: EventBatch) -> Transition:
    """Handle an action selection response in combat.

    See spec 07 Section 4.
    """
    response = input.value
    choice_id = ""
    target_id = None

    # Extract choice_id and target from various response formats
    if isinstance(response, dict):
        # Format: {"type": "ChoiceResponse", "choice": {"id": "...", "target": N}}
        # or:     {"id": "...", "target": N}
        # or:     {"id": "..."}
        choice = response.get("choice", response)
        if isinstance(choice, dict):
            choice_id = choice.get("id", "")
            target_id = choice.get("target")
        elif isinstance(choice, str):
            choice_id = choice
        else:
            choice_id = str(choice) if choice is not None else ""
    elif isinstance(response, str):
        choice_id = response
    else:
        choice_id = str(response) if response is not None else ""

    # Record the response
    event_id = state.next_event_id()
    events.append(Event(
        id=event_id,
        clock=dict(state.clocks),
        kind=EventKind.DRIVER_RESPONSE_RECORDED,
        data={"choice": choice_id, "request_id": input.request_id},
    ))

    if choice_id == "end_turn":
        # End the current turn
        if state.combat:
            state.combat.turn_phase = TurnPhase.END_OF_TURN
        return Transition.yielded(events, state)

    # Process the chosen action
    if state.combat:
        active = state.combat.active_entity
        if active is not None:
            economy = state.combat.action_economy.get(active)
            if economy:
                # Look up action definition
                action_def = None
                if state.pack_data:
                    for a in state.pack_data.get("actions", []):
                        if a.get("id") == choice_id:
                            action_def = a
                            break

                if action_def:
                    kind = action_def.get("kind", "Action")
                    if kind == "Action":
                        economy.action_used = True
                    elif kind == "BonusAction":
                        economy.bonus_action_used = True

                    # Resolve the action's effects
                    state, action_events = _resolve_action_effects(
                        state, action_def, active, target_id
                    )
                    events.extend(action_events)

                    # After an action that costs an action, end the turn
                    if kind == "Action":
                        state.combat.turn_phase = TurnPhase.END_OF_TURN

        # After action, check if we should re-prompt or end turn
        # See spec 07 Section 4: prompt again unless end conditions met

    return Transition.yielded(events, state)


def _resolve_action_effects(
    state: SessionState,
    action_def: dict,
    source: EntityId,
    target_id: EntityId | None,
) -> tuple[SessionState, EventBatch]:
    """Resolve an action's effect templates.

    Effect references in actions are strings that point to effect_templates
    in the pack. Each effect template has a body (Damage, Heal, etc.)
    that we resolve via SFS functions.
    """
    from reference.sfs import dispatch_sfs
    from reference.rules import resolve_attack
    from reference.domain import DieGroup, DieKindDn, KeepRule, RollMode, RollPurpose

    events: EventBatch = []
    effect_refs = action_def.get("effects", [])

    # Look up effect templates from pack data
    effect_templates = {}
    if state.pack_data:
        for et in state.pack_data.get("effect_templates", []):
            if isinstance(et, dict) and "id" in et:
                effect_templates[et["id"]] = et

    # Check if this is a spell attack action
    attack_type = action_def.get("attack_type")

    for ref in effect_refs:
        # Effect refs can be strings (template ids) or dicts with SFS calls
        if isinstance(ref, str):
            template = effect_templates.get(ref)
            if template is None:
                continue

            body = template.get("body", {})

            # Handle Damage body
            if "Damage" in body:
                damage_body = body["Damage"]
                amounts = damage_body.get("amounts", [])

                if target_id is None:
                    continue

                # If this is a spell attack, roll an attack first
                if attack_type in ("ranged", "melee"):
                    # Get spell attack bonus from attacker
                    attacker = state.entities.get(source)
                    attack_bonus = 0
                    if attacker:
                        stats = attacker.get_stats()
                        if stats:
                            attack_bonus = stats.derived.get(
                                "spell_attack_bonus",
                                stats.derived.get("attack_bonus", 0)
                            )

                    # Get defender AC
                    defender = state.entities.get(target_id)
                    ac = 10
                    if defender:
                        stats = defender.get_stats()
                        if stats:
                            ac = stats.derived.get("armor_class", 10)

                    # Parse damage dice from expression
                    damage_dice = []
                    damage_type = "slashing"
                    for amt in amounts:
                        expr = amt.get("expression", "1d8")
                        damage_type = amt.get("type", "slashing")
                        dice_groups = _parse_dice_expression(expr)
                        damage_dice.extend(dice_groups)

                    state, attack_result, attack_events = resolve_attack(
                        state, source, target_id,
                        attack_bonus=attack_bonus,
                        ac=ac,
                        damage_dice=damage_dice if damage_dice else None,
                        damage_type=damage_type,
                        damage_bonus=0,
                    )
                    events.extend(attack_events)

                    # If hit, apply damage (with resistance/vulnerability)
                    if attack_result.hit and attack_result.damage:
                        damage = attack_result.damage
                        state, apply_events = _apply_damage_with_resistance(
                            state, target_id, damage
                        )
                        events.extend(apply_events)
                else:
                    # Direct damage (no attack roll)
                    state, damage_result, damage_events = dispatch_sfs(
                        "sfs.damage.typed", state, {
                            "source": source,
                            "target": target_id,
                            "amounts": amounts,
                        }
                    )
                    events.extend(damage_events)

            # Handle Heal body
            elif "Heal" in body:
                heal_body = body["Heal"]
                amount_data = heal_body.get("amount", {})
                expr = amount_data.get("expression", "0")
                # For simplicity, parse and roll the expression
                total = _eval_dice_expression(expr, state)
                heal_target = target_id if target_id is not None else source
                state, _, heal_events = dispatch_sfs(
                    "sfs.heal.flat", state, {
                        "source": source,
                        "target": heal_target,
                        "amount": total,
                    }
                )
                events.extend(heal_events)

        elif isinstance(ref, dict):
            # Direct SFS call reference
            sfs_fn = ref.get("sfs_function")
            if sfs_fn:
                try:
                    sfs_args = dict(ref.get("args", {}))
                    # Substitute references
                    for key in ("actor", "attacker", "source"):
                        if key in sfs_args and sfs_args[key] == "$active":
                            sfs_args[key] = source
                    if "target" in sfs_args and sfs_args["target"] == "$target":
                        sfs_args["target"] = target_id
                    if "defender" in sfs_args and sfs_args["defender"] == "$target":
                        sfs_args["defender"] = target_id

                    state, _, sfs_events = dispatch_sfs(sfs_fn, state, sfs_args)
                    events.extend(sfs_events)
                except Exception:
                    pass

    return state, events


def _parse_dice_expression(expr: str) -> list:
    """Parse a dice expression like '1d10' into DieGroups."""
    from reference.domain import DieGroup, DieKindDn, KeepRule

    groups = []
    parts = expr.replace("-", "+-").split("+")
    for part in parts:
        part = part.strip()
        if not part:
            continue
        if "d" in part:
            count_str, die_str = part.split("d", 1)
            count = int(count_str) if count_str else 1
            die_size = int(die_str)
            groups.append(DieGroup(count=count, kind=DieKindDn(die_size), keep=KeepRule.ALL))
    return groups


def _eval_dice_expression(expr: str, state: SessionState) -> int:
    """Parse and roll a dice expression, returning the total."""
    from reference.domain import DieGroup, DieKindDn, KeepRule, RollSpec, RollMode, RollPurpose
    from reference.rules import resolve_roll

    total_bonus = 0
    dice_groups = []
    parts = expr.replace("-", "+-").split("+")
    for part in parts:
        part = part.strip()
        if not part:
            continue
        if "d" in part:
            count_str, die_str = part.split("d", 1)
            count = int(count_str) if count_str else 1
            die_size = int(die_str)
            dice_groups.append(DieGroup(count=count, kind=DieKindDn(die_size), keep=KeepRule.ALL))
        else:
            total_bonus += int(part)

    if not dice_groups:
        return total_bonus

    spec = RollSpec(dice=dice_groups, mode=RollMode.STRAIGHT, purpose=RollPurpose.ATTACK)
    result, state.rng = resolve_roll(spec, state.rng)
    return result.total + total_bonus


def _apply_damage_with_resistance(
    state: SessionState,
    target_id: EntityId,
    damage: 'DamageInstance',
) -> tuple[SessionState, EventBatch]:
    """Apply damage to a target, accounting for resistance/vulnerability
    from persistent effects.

    Persistent effects with ScaleNumeric transformers that match the damage
    type will scale the damage (e.g., resistance halves fire damage).
    """
    from reference.domain import DamageInstance

    events: EventBatch = []
    target = state.entities.get(target_id)
    if target is None:
        return state, events

    original_amount = damage.amount
    final_amount = damage.amount
    damage_type = damage.type

    # Check for persistent effects on target with damage transformers
    persistent_effects = target.components.get("persistent_effects", [])
    for pe in persistent_effects:
        if not isinstance(pe, dict):
            continue
        for transformer in pe.get("transformers", []):
            if not isinstance(transformer, dict):
                continue
            t_type = transformer.get("type", "")
            applies_to = transformer.get("applies_to", "")
            t_filter = transformer.get("filter", {})

            if t_type == "ScaleNumeric" and applies_to == "damage":
                # Check if the filter matches the damage type
                filter_damage_type = t_filter.get("damage_type", "")
                if filter_damage_type and filter_damage_type != damage_type:
                    continue

                # Apply scaling
                numerator = transformer.get("numerator", 1)
                denominator = transformer.get("denominator", 1)
                if denominator != 0:
                    # Floor division for resistance
                    final_amount = (final_amount * numerator) // denominator

    # Emit EffectPushed event for the damage
    event_id = state.next_event_id()
    events.append(Event(
        id=event_id,
        clock=dict(state.clocks),
        kind=EventKind.EFFECT_PUSHED,
        source=damage.source,
        data={
            "type": "Damage",
            "damage_type": damage_type,
            "amount": original_amount,
            "target": target_id,
        },
    ))

    # Emit EffectResolved event showing the transformed amount
    event_id = state.next_event_id()
    events.append(Event(
        id=event_id,
        clock=dict(state.clocks),
        kind=EventKind.EFFECT_RESOLVED,
        source=damage.source,
        data={
            "original_amount": original_amount,
            "transformed_amount": final_amount,
            "target": target_id,
        },
    ))

    # Apply damage to HP
    resources = target.get_resources()
    if resources:
        hp_pool = resources.pools.get("hp")
        if hp_pool:
            hp_before = hp_pool.current
            hp_pool.current = max(0, hp_pool.current - final_amount)
            delta = hp_pool.current - hp_before

            event_id = state.next_event_id()
            events.append(Event(
                id=event_id,
                clock=dict(state.clocks),
                kind=EventKind.RESOURCE_CHANGED,
                source=target_id,
                data={
                    "delta": delta,
                    "entity": target_id,
                    "new_current": hp_pool.current,
                    "resource": "hp",
                },
            ))

    return state, events


def _step_tick(state: SessionState, input: StepInput) -> Transition:
    """Handle Tick input.

    See spec 05 Section 9.
    """
    events: EventBatch = []
    clock_tag = input.clock_tag or "round"

    state.clocks[clock_tag] = state.clocks.get(clock_tag, 0) + 1

    event_id = state.next_event_id()
    events.append(Event(
        id=event_id,
        clock=dict(state.clocks),
        kind=EventKind.CLOCK_TICKED,
        data={"by": 1, "clock": clock_tag},
    ))

    return Transition.yielded(events, state)


def _step_halt(state: SessionState) -> Transition:
    """Handle Halt input.

    See spec 05 Section 10.
    """
    state.halted = True
    events: EventBatch = [
        Event(
            id=state.next_event_id(),
            clock=dict(state.clocks),
            kind=EventKind.SESSION_ENDED,
            data={"reason": "halt"},
        )
    ]
    return Transition.terminal(events)


# ---------------------------------------------------------------------------
# Convenience: create a fresh session
# ---------------------------------------------------------------------------

def create_session(seed: list[int] | None = None,
                   rng_policy: str = "EngineOnly") -> SessionState:
    """Create a new SessionState with the given seed.

    See spec 02 Section 5.
    """
    if seed is None:
        seed = [42, 0, 0, 0]
    return SessionState(
        rng=seed_rng(seed),
        rng_policy=rng_policy,
    )
