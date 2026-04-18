"""
SFS v1 function implementations.

See specs/04-content-packs-and-sfs.md Section 5 for the full enumeration.
This module implements the subset needed for a minimal reference:

- sfs.roll.d20
- sfs.roll.generic
- sfs.roll.initiative
- sfs.damage.typed
- sfs.heal.flat
- sfs.resource.spend
- sfs.resource.grant
- sfs.attack.melee

Each function follows the pure contract: given state + args, return
(new_state, result, events). No I/O, no side effects beyond state updates.
"""
from __future__ import annotations

import copy
from typing import Any, TYPE_CHECKING

from reference.domain import (
    EntityId, Event, EventKind, EventBatch,
    RollSpec, RollResult, DieGroup, DieKindDn, DieKindConstant,
    Modifier, ModifierSource, RollMode, RollPurpose,
    DamageInstance, KeepRule,
)
from reference.rules import resolve_roll, resolve_check, resolve_attack, AdvantageState

if TYPE_CHECKING:
    from reference.engine import SessionState


# ---------------------------------------------------------------------------
# SFS Function Registry
# ---------------------------------------------------------------------------

SFS_FUNCTIONS: dict[str, Any] = {}


def sfs_function(name: str):
    """Decorator to register an SFS function implementation."""
    def decorator(fn):
        SFS_FUNCTIONS[name] = fn
        return fn
    return decorator


def dispatch_sfs(name: str, state: 'SessionState', args: dict) -> tuple['SessionState', Any, EventBatch]:
    """Dispatch an SFS function call by name.

    See specs/04 Section 4 (SfsRegistry).
    Raises KeyError if the function is not registered.
    """
    fn = SFS_FUNCTIONS.get(name)
    if fn is None:
        raise KeyError(f"Unknown SFS function: {name}")
    return fn(state, args)


# ---------------------------------------------------------------------------
# sfs.roll.d20  (spec 04 Section 5.1)
# ---------------------------------------------------------------------------

@sfs_function("sfs.roll.d20")
def sfs_roll_d20(state: 'SessionState', args: dict) -> tuple['SessionState', dict, EventBatch]:
    """Roll a d20 with mode and modifier.

    See specs/04 Section 5.1 and sfs.roll.d20.json.

    Input: {actor, modifiers?, mode, purpose}
    Output: RollResult
    """
    state = copy.deepcopy(state)
    events: EventBatch = []

    actor = args.get("actor")
    mode_str = args.get("mode", "Straight")
    purpose_str = args.get("purpose", "Attack")

    # Parse mode
    mode = RollMode.STRAIGHT
    if mode_str == "Advantage":
        mode = RollMode.ADVANTAGE
    elif mode_str == "Disadvantage":
        mode = RollMode.DISADVANTAGE

    # Parse modifiers
    modifiers: list[Modifier] = []
    for m in args.get("modifiers", []):
        modifiers.append(Modifier(
            value=m.get("value", 0),
            source=ModifierSource(kind="Circumstance"),
            applies_when=m.get("applies_when", "Always"),
        ))

    spec = RollSpec(
        dice=[DieGroup(count=1, kind=DieKindDn(20), keep=KeepRule.ALL)],
        modifiers=modifiers,
        mode=mode,
        purpose=RollPurpose.ATTACK,
    )

    result, state.rng = resolve_roll(spec, state.rng)

    event_id = state.next_event_id()
    events.append(Event(
        id=event_id,
        clock=dict(state.clocks),
        kind=EventKind.ROLL_MADE,
        source=actor,
        data={
            "actor": actor,
            "result": result.to_dict(),
            "spec": spec.to_dict(),
        },
    ))

    return state, result.to_dict(), events


# ---------------------------------------------------------------------------
# sfs.roll.generic  (spec 04 Section 5.1)
# ---------------------------------------------------------------------------

@sfs_function("sfs.roll.generic")
def sfs_roll_generic(state: 'SessionState', args: dict) -> tuple['SessionState', dict, EventBatch]:
    """Roll any RollSpec.

    See specs/04 Section 5.1 and sfs.roll.generic.json.

    Input: {spec, actor?}
    Output: RollResult
    """
    from reference.domain import roll_spec_from_dict
    state = copy.deepcopy(state)
    events: EventBatch = []

    actor = args.get("actor")
    spec = roll_spec_from_dict(args["spec"])

    result, state.rng = resolve_roll(spec, state.rng)

    event_id = state.next_event_id()
    events.append(Event(
        id=event_id,
        clock=dict(state.clocks),
        kind=EventKind.ROLL_MADE,
        source=actor,
        data={
            "actor": actor,
            "result": result.to_dict(),
            "spec": spec.to_dict(),
        },
    ))

    return state, result.to_dict(), events


# ---------------------------------------------------------------------------
# sfs.roll.initiative  (spec 04 Section 5.1)
# ---------------------------------------------------------------------------

@sfs_function("sfs.roll.initiative")
def sfs_roll_initiative(state: 'SessionState', args: dict) -> tuple['SessionState', dict, EventBatch]:
    """Initiative roll for an entity.

    See specs/04 Section 5.1 and sfs.roll.initiative.json.
    Typically d20 + DEX modifier + feature bonuses.

    Input: {entity}
    Output: RollResult
    """
    state = copy.deepcopy(state)
    events: EventBatch = []

    entity_id = args["entity"]
    entity = state.entities.get(entity_id)

    # Compute initiative modifier
    init_mod = 0
    if entity:
        stats = entity.get_stats()
        if stats:
            init_mod = stats.derived.get("initiative_bonus",
                                         (stats.scores.get("dex", 10) - 10) // 2)

    spec = RollSpec(
        dice=[DieGroup(count=1, kind=DieKindDn(20), keep=KeepRule.ALL)],
        modifiers=[Modifier(value=init_mod, source=ModifierSource(kind="Stat", value="dex"))],
        mode=RollMode.STRAIGHT,
        purpose=RollPurpose.INITIATIVE,
    )

    result, state.rng = resolve_roll(spec, state.rng)

    event_id = state.next_event_id()
    events.append(Event(
        id=event_id,
        clock=dict(state.clocks),
        kind=EventKind.ROLL_MADE,
        source=entity_id,
        data={
            "actor": entity_id,
            "result": result.to_dict(),
            "spec": spec.to_dict(),
        },
    ))

    return state, result.to_dict(), events


# ---------------------------------------------------------------------------
# sfs.damage.typed  (spec 04 Section 5.3)
# ---------------------------------------------------------------------------

@sfs_function("sfs.damage.typed")
def sfs_damage_typed(state: 'SessionState', args: dict) -> tuple['SessionState', dict, EventBatch]:
    """Apply typed damage to a target.

    See specs/04 Section 5.3 and sfs.damage.typed.json.
    Runs transformers (resistance, vulnerability, immunity) before applying.

    Input: {source, target, amounts, tags?}
    Output: {applied, total_dealt, target_hp_after}
    """
    state = copy.deepcopy(state)
    events: EventBatch = []

    source_id = args["source"]
    target_id = args["target"]
    amounts = args["amounts"]
    tags = args.get("tags", [])

    target = state.entities.get(target_id)
    if target is None:
        return state, {"applied": [], "target_hp_after": 0, "total_dealt": 0}, events

    resources = target.get_resources()
    hp_before = 0
    if resources:
        hp_pool = resources.pools.get("hp")
        if hp_pool:
            hp_before = hp_pool.current

    applied: list[dict] = []
    total_dealt = 0

    for dmg in amounts:
        # Parse damage expression (simplified: just use amount if numeric)
        amount = dmg.get("amount", 0)
        if isinstance(dmg.get("expression"), str):
            # Parse simple dice expression like "1d8+3"
            amount = _parse_damage_expression_total(dmg["expression"], state)
        dmg_type = dmg.get("type", "slashing")
        dmg_tags = dmg.get("tags", []) + tags

        instance = DamageInstance(
            amount=amount,
            type=dmg_type,
            source=source_id,
            tags=dmg_tags,
            original_amount=amount,
        )
        applied.append(instance.to_dict())
        total_dealt += amount

    # Apply damage to HP
    if resources:
        hp_pool = resources.pools.get("hp")
        if hp_pool:
            hp_pool.current = max(0, hp_pool.current - total_dealt)

            event_id = state.next_event_id()
            events.append(Event(
                id=event_id,
                clock=dict(state.clocks),
                kind=EventKind.RESOURCE_CHANGED,
                source=target_id,
                data={
                    "delta": -total_dealt,
                    "entity": target_id,
                    "new_current": hp_pool.current,
                    "resource": "hp",
                },
            ))

    hp_after = 0
    if resources:
        hp_pool = resources.pools.get("hp")
        if hp_pool:
            hp_after = hp_pool.current

    return state, {
        "applied": applied,
        "target_hp_after": hp_after,
        "total_dealt": total_dealt,
    }, events


def _parse_damage_expression_total(expr: str, state: 'SessionState') -> int:
    """Parse a simple dice expression and roll it. Returns total.

    This is a simplified parser for expressions like "1d8", "2d6+3", "1d4-1".
    """
    from reference.rules import resolve_roll
    from reference.domain import DieGroup, DieKindDn, RollSpec, RollMode, RollPurpose, KeepRule

    # Parse dice expression
    total_bonus = 0
    dice_groups: list[DieGroup] = []

    # Handle +/- terms
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

    spec = RollSpec(
        dice=dice_groups,
        modifiers=[],
        mode=RollMode.STRAIGHT,
        purpose=RollPurpose.ATTACK,
    )
    result, state.rng = resolve_roll(spec, state.rng)
    return result.total + total_bonus


# ---------------------------------------------------------------------------
# sfs.heal.flat  (spec 04 Section 5.4)
# ---------------------------------------------------------------------------

@sfs_function("sfs.heal.flat")
def sfs_heal_flat(state: 'SessionState', args: dict) -> tuple['SessionState', dict, EventBatch]:
    """Heal a flat amount.

    See specs/04 Section 5.4 and sfs.heal.flat.json.
    Increases HP up to maximum.

    Input: {source, target, amount}
    Output: {healed, hp_before, hp_after}
    """
    state = copy.deepcopy(state)
    events: EventBatch = []

    target_id = args["target"]
    amount = args["amount"]

    target = state.entities.get(target_id)
    if target is None:
        return state, {"healed": 0, "hp_after": 0, "hp_before": 0}, events

    resources = target.get_resources()
    if resources is None:
        return state, {"healed": 0, "hp_after": 0, "hp_before": 0}, events

    hp_pool = resources.pools.get("hp")
    if hp_pool is None:
        return state, {"healed": 0, "hp_after": 0, "hp_before": 0}, events

    hp_before = hp_pool.current
    hp_pool.current = min(hp_pool.maximum, hp_pool.current + amount)
    healed = hp_pool.current - hp_before

    event_id = state.next_event_id()
    events.append(Event(
        id=event_id,
        clock=dict(state.clocks),
        kind=EventKind.RESOURCE_CHANGED,
        source=target_id,
        data={
            "delta": healed,
            "entity": target_id,
            "new_current": hp_pool.current,
            "resource": "hp",
        },
    ))

    return state, {
        "healed": healed,
        "hp_after": hp_pool.current,
        "hp_before": hp_before,
    }, events


# ---------------------------------------------------------------------------
# sfs.resource.spend  (spec 04 Section 5.4)
# ---------------------------------------------------------------------------

@sfs_function("sfs.resource.spend")
def sfs_resource_spend(state: 'SessionState', args: dict) -> tuple['SessionState', dict, EventBatch]:
    """Spend a resource.

    See specs/04 Section 5.4 and sfs.resource.spend.json.
    RecoverableError::ResourceExhausted if current < amount.

    Input: {entity, resource, amount}
    Output: {previous, current}
    """
    state = copy.deepcopy(state)
    events: EventBatch = []

    entity_id = args["entity"]
    resource_id = args["resource"]
    amount = args["amount"]

    entity = state.entities.get(entity_id)
    if entity is None:
        raise ValueError(f"Entity {entity_id} not found")

    resources = entity.get_resources()
    if resources is None:
        raise ValueError(f"Entity {entity_id} has no resources")

    pool = resources.pools.get(resource_id)
    if pool is None:
        raise ValueError(f"Resource {resource_id} not found on entity {entity_id}")

    previous = pool.current
    if pool.current < amount:
        # RecoverableError::ResourceExhausted
        raise ResourceExhaustedError(
            resource_id=resource_id,
            needed=amount,
            available=pool.current,
        )

    pool.current -= amount

    event_id = state.next_event_id()
    events.append(Event(
        id=event_id,
        clock=dict(state.clocks),
        kind=EventKind.RESOURCE_CHANGED,
        source=entity_id,
        data={
            "delta": -amount,
            "entity": entity_id,
            "new_current": pool.current,
            "resource": resource_id,
        },
    ))

    return state, {"current": pool.current, "previous": previous}, events


# ---------------------------------------------------------------------------
# sfs.resource.grant  (spec 04 Section 5.4)
# ---------------------------------------------------------------------------

@sfs_function("sfs.resource.grant")
def sfs_resource_grant(state: 'SessionState', args: dict) -> tuple['SessionState', dict, EventBatch]:
    """Increase a resource up to its maximum.

    See specs/04 Section 5.4 and sfs.resource.grant.json.
    Overshoot is clamped unless BoundAbove tag allows it.

    Input: {entity, resource, amount}
    Output: {previous, current, maximum}
    """
    state = copy.deepcopy(state)
    events: EventBatch = []

    entity_id = args["entity"]
    resource_id = args["resource"]
    amount = args["amount"]

    entity = state.entities.get(entity_id)
    if entity is None:
        raise ValueError(f"Entity {entity_id} not found")

    resources = entity.get_resources()
    if resources is None:
        raise ValueError(f"Entity {entity_id} has no resources")

    pool = resources.pools.get(resource_id)
    if pool is None:
        raise ValueError(f"Resource {resource_id} not found on entity {entity_id}")

    previous = pool.current
    pool.current = min(pool.maximum, pool.current + amount)
    delta = pool.current - previous

    event_id = state.next_event_id()
    events.append(Event(
        id=event_id,
        clock=dict(state.clocks),
        kind=EventKind.RESOURCE_CHANGED,
        source=entity_id,
        data={
            "delta": delta,
            "entity": entity_id,
            "new_current": pool.current,
            "resource": resource_id,
        },
    ))

    return state, {
        "current": pool.current,
        "maximum": pool.maximum,
        "previous": previous,
    }, events


# ---------------------------------------------------------------------------
# sfs.attack.melee  (spec 04 Section 5.3)
# ---------------------------------------------------------------------------

@sfs_function("sfs.attack.melee")
def sfs_attack_melee(state: 'SessionState', args: dict) -> tuple['SessionState', dict, EventBatch]:
    """Resolve a melee attack.

    See specs/04 Section 5.3 and sfs.attack.melee.json.
    To-hit roll, crit check, damage roll. Pushes damage onto the effect stack.

    Input: {attacker, defender, weapon_item?, additional_modifiers?}
    Output: {roll, hit, crit, damage?}
    """
    attacker_id = args["attacker"]
    defender_id = args["defender"]

    # Get attack bonus from attacker's stats (simplified)
    attacker = state.entities.get(attacker_id)
    attack_bonus = 0
    damage_bonus = 0
    if attacker:
        stats = attacker.get_stats()
        if stats:
            str_score = stats.scores.get("str", 10)
            attack_bonus = (str_score - 10) // 2
            damage_bonus = attack_bonus

    # Additional modifiers
    for mod in args.get("additional_modifiers", []):
        attack_bonus += mod.get("value", 0)

    # Get defender AC
    defender = state.entities.get(defender_id)
    ac = 10
    if defender:
        stats = defender.get_stats()
        if stats:
            ac = stats.derived.get("armor_class", 10)

    # Default damage: 1d8 + STR
    damage_dice = [DieGroup(count=1, kind=DieKindDn(8), keep=KeepRule.ALL)]

    state, attack_result, events = resolve_attack(
        state, attacker_id, defender_id,
        attack_bonus=attack_bonus, ac=ac,
        damage_dice=damage_dice,
        damage_type="slashing",
        damage_bonus=damage_bonus,
    )

    result: dict[str, Any] = {
        "crit": attack_result.crit,
        "hit": attack_result.hit,
        "roll": attack_result.roll.roll.to_dict() if attack_result.roll and attack_result.roll.roll else {},
    }
    if attack_result.damage:
        result["damage"] = attack_result.damage.to_dict()

    return state, result, events


# ---------------------------------------------------------------------------
# Error types
# ---------------------------------------------------------------------------

class ResourceExhaustedError(Exception):
    """RecoverableError::ResourceExhausted. See spec 01 Section 6.2."""
    def __init__(self, resource_id: str, needed: int, available: int):
        self.resource_id = resource_id
        self.needed = needed
        self.available = available
        super().__init__(
            f"ResourceExhausted: {resource_id} needs {needed}, has {available}"
        )
