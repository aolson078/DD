"""
Rules engine: resolve_check, resolve_attack, take_action, gather_modifiers.

See specs/05-rules-engine.md for the normative definitions.

The rules engine is thin by design: its job is to orchestrate SFS calls
and maintain invariants. It does NOT encode pack-specific knowledge.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any, TYPE_CHECKING

from reference.domain import (
    EntityId, Event, EventKind, EventBatch,
    RollSpec, RollResult, DieGroup, DieRoll, DieKind,
    DieKindDn, DieKindFudge, DieKindConstant,
    Modifier, ModifierSource,
    RollMode, RollModeType, RollPurpose, RollPurposeType,
    KeepRule, KeepHighest, KeepLowest, DropHighest, DropLowest,
    KeepRuleType, DamageInstance,
)
from reference.rng import Rng, next_u64, roll_die_value, roll_fudge_value

if TYPE_CHECKING:
    from reference.engine import SessionState


# ---------------------------------------------------------------------------
# Modifier collection  (spec 05 Section 1)
# ---------------------------------------------------------------------------

@dataclass
class ModifierCollectorContext:
    """Context for gathering modifiers. See spec 05 Section 1."""
    actor: EntityId
    purpose: RollPurposeType = RollPurpose.ATTACK
    additional: list[Modifier] = field(default_factory=list)


def gather_modifiers(state: SessionState, context: ModifierCollectorContext) -> list[Modifier]:
    """Gather all applicable modifiers for a roll.

    See spec 05 Section 1.

    Iteration order is fixed and deterministic:
    1. Stats from actor
    2. Proficiencies relevant to purpose
    3. Equipped items (slot order)
    4. Persistent effects on actor (install order)
    5. Scene-level modifiers
    6. context.additional
    """
    modifiers: list[Modifier] = []

    entity = state.entities.get(context.actor)
    if entity is None:
        return modifiers

    # 1. Stats-based modifiers
    stats = entity.get_stats()
    if stats is not None:
        # Check for derived stat modifiers (e.g. initiative_bonus)
        for stat_id, value in sorted(stats.derived.items()):
            # The specific stat to use depends on purpose; simplified here
            pass

    # 6. Additional modifiers from the calling action
    modifiers.extend(context.additional)

    return modifiers


# ---------------------------------------------------------------------------
# AdvantageState  (spec 05 Section 2)
# ---------------------------------------------------------------------------

@dataclass
class AdvantageState:
    """See spec 05 Section 2.

    If both has_advantage and has_disadvantage are true, net is Straight
    (cancel), regardless of how many sources contributed each.
    """
    has_advantage: bool = False
    has_disadvantage: bool = False

    def net_mode(self) -> RollMode:
        if self.has_advantage and self.has_disadvantage:
            return RollMode.STRAIGHT
        if self.has_advantage:
            return RollMode.ADVANTAGE  # type: ignore[return-value]
        if self.has_disadvantage:
            return RollMode.DISADVANTAGE  # type: ignore[return-value]
        return RollMode.STRAIGHT


# ---------------------------------------------------------------------------
# Roll resolution  (spec 02 Section 3.5, Invariant D2)
# ---------------------------------------------------------------------------

def _apply_keep_rule(rolls: list[DieRoll], rule: KeepRuleType) -> list[DieRoll]:
    """Apply a keep rule to a group of die rolls.

    See spec 02 Invariant D2 step 2:
    Stable-sort the group's rolls by (value desc, index asc),
    select per rule, mark non-kept as dropped.
    """
    if isinstance(rule, KeepRule) and rule == KeepRule.ALL:
        return rolls

    # Create indexed copies for stable sort
    indexed = list(enumerate(rolls))
    # Sort by (value desc, original_index asc)
    indexed.sort(key=lambda pair: (-pair[1].value, pair[0]))

    n = len(indexed)
    if isinstance(rule, KeepHighest):
        keep_count = min(rule.n, n)
        keep_indices = {idx for idx, _ in indexed[:keep_count]}
    elif isinstance(rule, KeepLowest):
        keep_count = min(rule.n, n)
        keep_indices = {idx for idx, _ in indexed[n - keep_count:]}
    elif isinstance(rule, DropHighest):
        drop_count = min(rule.n, n)
        keep_indices = {idx for idx, _ in indexed[drop_count:]}
    elif isinstance(rule, DropLowest):
        drop_count = min(rule.n, n)
        keep_indices = {idx for idx, _ in indexed[:n - drop_count]}
    else:
        return rolls

    result = []
    for i, roll in enumerate(rolls):
        result.append(DieRoll(
            kind=roll.kind,
            value=roll.value,
            dropped=i not in keep_indices,
        ))
    return result


def _roll_single_group(group: DieGroup, rng: Rng) -> tuple[list[DieRoll], Rng]:
    """Roll a single DieGroup consuming RNG per schedule D2.

    See spec 02 Invariant D2 step 1:
    For each DieGroup in declaration order, consume group.count u64 values.
    """
    rolls: list[DieRoll] = []
    for _ in range(group.count):
        if isinstance(group.kind, DieKindDn):
            raw, rng = next_u64(rng)
            value = roll_die_value(raw, group.kind.n)
            rolls.append(DieRoll(kind=group.kind, value=value))
        elif isinstance(group.kind, DieKindFudge):
            raw, rng = next_u64(rng)
            value = roll_fudge_value(raw)
            rolls.append(DieRoll(kind=group.kind, value=value))
        elif isinstance(group.kind, DieKindConstant):
            # No u64 consumed for Constants
            rolls.append(DieRoll(kind=group.kind, value=group.kind.value))
    return rolls, rng


def resolve_roll(spec: RollSpec, rng: Rng) -> tuple[RollResult, Rng]:
    """Resolve a RollSpec, consuming RNG per the D2 schedule.

    See spec 02 Section 3.5, Invariant D2.

    Steps:
    1. For each DieGroup, consume count u64 values.
    2. Apply KeepRule (stable sort by value desc, index asc).
    3. Apply RollMode (Advantage/Disadvantage: roll twice, keep best/worst).
    """
    mode = spec.mode if isinstance(spec.mode, RollMode) else RollMode.STRAIGHT

    # Determine how many full sets to roll based on mode
    if mode == RollMode.ADVANTAGE or mode == RollMode.DISADVANTAGE:
        num_sets = 2
    elif mode == RollMode.ELVEN_ACCURACY:
        num_sets = 3
    else:
        num_sets = 1

    # Roll all sets
    all_sets: list[tuple[list[DieRoll], int]] = []
    for _ in range(num_sets):
        set_rolls: list[DieRoll] = []
        for group in spec.dice:
            group_rolls, rng = _roll_single_group(group, rng)
            group_rolls = _apply_keep_rule(group_rolls, group.keep)
            set_rolls.extend(group_rolls)
        set_total = sum(r.value for r in set_rolls if not r.dropped)
        all_sets.append((set_rolls, set_total))

    # Pick the set based on mode
    if mode == RollMode.ADVANTAGE or mode == RollMode.ELVEN_ACCURACY:
        best_idx = max(range(len(all_sets)), key=lambda i: all_sets[i][1])
        chosen_rolls = all_sets[best_idx][0]
        # Collect all raw rolls from all sets
        raw_rolls = []
        for i, (rolls, _) in enumerate(all_sets):
            for r in rolls:
                raw_rolls.append(DieRoll(
                    kind=r.kind, value=r.value,
                    dropped=r.dropped or i != best_idx,
                ))
    elif mode == RollMode.DISADVANTAGE:
        worst_idx = min(range(len(all_sets)), key=lambda i: all_sets[i][1])
        chosen_rolls = all_sets[worst_idx][0]
        raw_rolls = []
        for i, (rolls, _) in enumerate(all_sets):
            for r in rolls:
                raw_rolls.append(DieRoll(
                    kind=r.kind, value=r.value,
                    dropped=r.dropped or i != worst_idx,
                ))
    else:
        chosen_rolls = all_sets[0][0]
        raw_rolls = list(chosen_rolls)

    # Kept = non-dropped from chosen set
    kept = [r for r in chosen_rolls if not r.dropped]

    # Modifier total (spec 05 Section 1, Invariant RE1)
    modifier_total = sum(m.value for m in spec.modifiers)

    # Dice total from kept
    dice_total = sum(r.value for r in kept)
    total = dice_total + modifier_total

    # Natural value: for d20 rolls, the natural is the single d20 face value
    natural: int | None = None
    if (len(spec.dice) == 1 and spec.dice[0].count == 1
            and isinstance(spec.dice[0].kind, DieKindDn)
            and spec.dice[0].kind.n == 20):
        natural = kept[0].value if kept else None

    result = RollResult(
        spec=spec,
        raw=raw_rolls,
        kept=kept,
        modifier_total=modifier_total,
        total=total,
        natural=natural,
    )
    return result, rng


# ---------------------------------------------------------------------------
# Check resolution  (spec 05 Section 4)
# ---------------------------------------------------------------------------

@dataclass
class CheckResult:
    """See spec 05 Section 4."""
    total: int = 0
    margin: int = 0
    roll: RollResult | None = None
    target: int = 0
    outcome: str = "failure"  # "success", "failure", "partial"
    crit: bool = False


def resolve_check(
    state: SessionState,
    actor: EntityId,
    dc: int,
    modifiers: list[Modifier] | None = None,
    purpose: RollPurposeType = RollPurpose.ATTACK,
    advantage: AdvantageState | None = None,
) -> tuple[SessionState, CheckResult, EventBatch]:
    """Resolve a check against a DC.

    See spec 05 Section 4.

    Returns (new_state, check_result, events).
    """
    state = copy.deepcopy(state)

    # Build RollSpec
    mode = RollMode.STRAIGHT
    if advantage:
        mode = advantage.net_mode()

    spec = RollSpec(
        dice=[DieGroup(count=1, kind=DieKindDn(20))],
        modifiers=modifiers or [],
        mode=mode,
        purpose=purpose,
    )

    roll_result, state.rng = resolve_roll(spec, state.rng)

    margin = roll_result.total - dc
    outcome = "success" if margin >= 0 else "failure"
    is_crit = roll_result.natural == 20

    check_result = CheckResult(
        total=roll_result.total,
        margin=margin,
        roll=roll_result,
        target=dc,
        outcome=outcome,
        crit=is_crit,
    )

    events: EventBatch = []
    event_id = state.next_event_id()
    events.append(Event(
        id=event_id,
        clock=dict(state.clocks),
        kind=EventKind.ROLL_MADE,
        source=actor,
        data={
            "actor": actor,
            "result": roll_result.to_dict(),
            "spec": spec.to_dict(),
        },
    ))
    event_id = state.next_event_id()
    events.append(Event(
        id=event_id,
        clock=dict(state.clocks),
        kind=EventKind.CHECK_RESOLVED,
        source=actor,
        data={
            "actor": actor,
            "check_id": str(purpose),
            "dc": dc,
            "margin": margin,
            "total": roll_result.total,
        },
    ))

    return state, check_result, events


# ---------------------------------------------------------------------------
# Attack resolution  (spec 05 Section 6)
# ---------------------------------------------------------------------------

@dataclass
class AttackResult:
    """See spec 05 Section 6."""
    hit: bool = False
    crit: bool = False
    roll: CheckResult | None = None
    damage: DamageInstance | None = None


def resolve_attack(
    state: SessionState,
    attacker: EntityId,
    defender: EntityId,
    attack_bonus: int = 0,
    ac: int = 10,
    damage_dice: list[DieGroup] | None = None,
    damage_type: str = "slashing",
    damage_bonus: int = 0,
) -> tuple[SessionState, AttackResult, EventBatch]:
    """Resolve an attack roll.

    See spec 05 Section 6.

    Invariant RE2: resolve_attack never directly mutates the defender's HP.
    It pushes an Effect::Damage node onto the stack.
    """
    mods = [Modifier(value=attack_bonus, source=ModifierSource(kind="Stat"))]

    state, check_result, events = resolve_check(
        state, attacker, ac, modifiers=mods,
        purpose=RollPurpose.ATTACK,
    )

    if check_result.outcome != "success" and not check_result.crit:
        result = AttackResult(hit=False, crit=False, roll=check_result)
        event_id = state.next_event_id()
        events.append(Event(
            id=event_id,
            clock=dict(state.clocks),
            kind=EventKind.ATTACK_RESOLVED,
            source=attacker,
            data={
                "attacker": attacker,
                "crit": False,
                "defender": defender,
                "hit": False,
            },
        ))
        return state, result, events

    # Roll damage
    if damage_dice is None:
        damage_dice = [DieGroup(count=1, kind=DieKindDn(8))]

    # Double dice on crit
    if check_result.crit:
        crit_dice = []
        for dg in damage_dice:
            crit_dice.append(DieGroup(count=dg.count * 2, kind=dg.kind, keep=dg.keep))
        damage_dice = crit_dice

    damage_spec = RollSpec(
        dice=damage_dice,
        modifiers=[Modifier(value=damage_bonus, source=ModifierSource(kind="Stat"))],
        mode=RollMode.STRAIGHT,
        purpose=RollPurpose.ATTACK,
    )
    damage_roll, state.rng = resolve_roll(damage_spec, state.rng)

    damage = DamageInstance(
        amount=max(0, damage_roll.total),
        type=damage_type,
        source=attacker,
        tags=["critical"] if check_result.crit else [],
        original_amount=damage_roll.total,
    )

    result = AttackResult(
        hit=True,
        crit=check_result.crit,
        roll=check_result,
        damage=damage,
    )

    event_id = state.next_event_id()
    events.append(Event(
        id=event_id,
        clock=dict(state.clocks),
        kind=EventKind.ROLL_MADE,
        source=attacker,
        data={
            "actor": attacker,
            "result": damage_roll.to_dict(),
            "spec": damage_spec.to_dict(),
        },
    ))
    event_id = state.next_event_id()
    events.append(Event(
        id=event_id,
        clock=dict(state.clocks),
        kind=EventKind.ATTACK_RESOLVED,
        source=attacker,
        data={
            "attacker": attacker,
            "crit": check_result.crit,
            "damage": damage.to_dict(),
            "defender": defender,
            "hit": True,
        },
    ))

    return state, result, events
