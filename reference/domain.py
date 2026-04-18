"""
Data types: Entity, Component, RollSpec, RollResult, Event, etc.

See specs/02-domain-model.md for the normative definitions.
All types are pure data with no behavior. Methods live in rules.py or engine.py.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


# ---------------------------------------------------------------------------
# Identifiers (all typed as str or int per spec)
# ---------------------------------------------------------------------------

EntityId = int           # u64, globally unique in a session (spec 02 Section 1)
EffectInstanceId = int
PersistentEffectInstanceId = int
EventId = int
RequestId = int

# String identifiers following the Id pattern from _common.json
StatId = str
ResourceId = str
ProficiencyId = str
DamageTypeStr = str
ActionId = str
CheckId = str
SaveId = str
TriggerId = str
TransformerId = str
SceneId = str
ZoneId = str
ComponentTypeId = str
ClockTag = str
PackId = str
SfsFunctionId = str
ItemInstanceId = int
ItemTemplateId = str
SlotId = str
FactionId = str
FeatureId = str
ClassId = str


# ---------------------------------------------------------------------------
# Dice and Rolls  (spec 02 Section 3)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DieKindDn:
    """A standard Dn die (d4, d6, d8, d10, d12, d20, d100, ...)."""
    n: int

@dataclass(frozen=True)
class DieKindFudge:
    """Fudge die: results in -1, 0, or +1."""
    pass

@dataclass(frozen=True)
class DieKindConstant:
    """Constant substitution: no RNG consumed. See spec 02 Section 3.1."""
    value: int

# Union type for DieKind
DieKind = DieKindDn | DieKindFudge | DieKindConstant


class KeepRule(Enum):
    """See spec 02 Section 3.2."""
    ALL = "All"

@dataclass(frozen=True)
class KeepHighest:
    n: int

@dataclass(frozen=True)
class KeepLowest:
    n: int

@dataclass(frozen=True)
class DropHighest:
    n: int

@dataclass(frozen=True)
class DropLowest:
    n: int

KeepRuleType = KeepRule | KeepHighest | KeepLowest | DropHighest | DropLowest


class RollMode(Enum):
    """See spec 02 Section 3.2."""
    STRAIGHT = "Straight"
    ADVANTAGE = "Advantage"
    DISADVANTAGE = "Disadvantage"
    ELVEN_ACCURACY = "ElvenAccuracy"


@dataclass(frozen=True)
class RollModeCustom:
    tag: str

RollModeType = RollMode | RollModeCustom


class RollPurpose(Enum):
    """See spec 02 Section 3.2."""
    ATTACK = "Attack"
    INITIATIVE = "InitiativeRoll"
    HIT_DICE = "HitDice"

@dataclass(frozen=True)
class RollPurposeSave:
    save_id: str

@dataclass(frozen=True)
class RollPurposeCheck:
    check_id: str

@dataclass(frozen=True)
class RollPurposeDamage:
    damage_type: str

@dataclass(frozen=True)
class RollPurposeCustom:
    tag: str

RollPurposeType = RollPurpose | RollPurposeSave | RollPurposeCheck | RollPurposeDamage | RollPurposeCustom


@dataclass(frozen=True)
class ModifierSource:
    """See spec 02 Section 3.3."""
    kind: str       # "Stat", "Proficiency", "Feature", "Item", "Effect", "Circumstance"
    value: Any = None

@dataclass(frozen=True)
class Modifier:
    """See spec 02 Section 3.3."""
    value: int
    source: ModifierSource
    applies_when: str = "Always"  # simplified; full version uses ModifierCondition


@dataclass
class DieGroup:
    """See spec 02 Section 3.2."""
    count: int
    kind: DieKind
    keep: KeepRuleType = KeepRule.ALL


@dataclass
class RollSpec:
    """See spec 02 Section 3.2.

    The complete specification of a roll before it is resolved.
    """
    dice: list[DieGroup] = field(default_factory=list)
    modifiers: list[Modifier] = field(default_factory=list)
    mode: RollModeType = RollMode.STRAIGHT
    purpose: RollPurposeType = RollPurpose.ATTACK

    def to_dict(self) -> dict:
        """Serialize to canonical JSON-compatible dict."""
        return {
            "dice": [_die_group_to_dict(g) for g in self.dice],
            "mode": _roll_mode_to_value(self.mode),
            "modifiers": [_modifier_to_dict(m) for m in self.modifiers],
            "purpose": _roll_purpose_to_value(self.purpose),
        }


@dataclass
class DieRoll:
    """See spec 02 Section 3.4."""
    kind: DieKind
    value: int
    dropped: bool = False


@dataclass
class RollResult:
    """See spec 02 Section 3.4."""
    spec: RollSpec
    raw: list[DieRoll] = field(default_factory=list)
    kept: list[DieRoll] = field(default_factory=list)
    modifier_total: int = 0
    total: int = 0
    natural: int | None = None

    def to_dict(self) -> dict:
        return {
            "kept": [_die_roll_to_dict(r) for r in self.kept],
            "modifier_total": self.modifier_total,
            "natural": self.natural,
            "raw": [_die_roll_to_dict(r) for r in self.raw],
            "spec": self.spec.to_dict(),
            "total": self.total,
        }


# ---------------------------------------------------------------------------
# Damage  (spec 02 Section 4)
# ---------------------------------------------------------------------------

@dataclass
class DamageInstance:
    """See spec 02 Section 4."""
    amount: int
    type: DamageTypeStr
    source: EntityId
    tags: list[str] = field(default_factory=list)
    original_amount: int = 0

    def to_dict(self) -> dict:
        return {
            "amount": self.amount,
            "original_amount": self.original_amount,
            "source": self.source,
            "tags": sorted(self.tags),
            "type": self.type,
        }


# ---------------------------------------------------------------------------
# Components  (spec 02 Section 2)
# ---------------------------------------------------------------------------

@dataclass
class ResourcePool:
    """See spec 02 Section 2.2."""
    current: int
    maximum: int
    recovery: str = "Manual"      # simplified; full version uses RecoveryRule
    tags: list[str] = field(default_factory=list)


@dataclass
class Stats:
    """See spec 02 Section 2.1."""
    scores: dict[StatId, int] = field(default_factory=dict)
    proficiencies: list[ProficiencyId] = field(default_factory=list)
    derived: dict[str, int] = field(default_factory=dict)


@dataclass
class Resources:
    """See spec 02 Section 2.2."""
    pools: dict[ResourceId, ResourcePool] = field(default_factory=dict)


@dataclass
class Position:
    """See spec 02 Section 2.4."""
    scene_id: SceneId = ""
    zone_id: ZoneId = ""
    offset: tuple[int, int] | None = None
    facing: str | None = None


@dataclass
class Faction:
    """See spec 02 Section 2.6."""
    primary: FactionId = ""
    disposition: dict[FactionId, int] = field(default_factory=dict)


class Controller(Enum):
    """See spec 02 Section 2.7."""
    DRIVER_OWNED = "DriverOwned"

@dataclass
class PlayerController:
    player_id: str

@dataclass
class ScriptController:
    script_id: str

ControllerType = Controller | PlayerController | ScriptController


@dataclass
class Inventory:
    """See spec 02 Section 2.3."""
    items: list[dict] = field(default_factory=list)
    equipped: dict[SlotId, ItemInstanceId] = field(default_factory=dict)
    attunements: list[ItemInstanceId] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Entity  (spec 02 Section 1)
# ---------------------------------------------------------------------------

@dataclass
class Entity:
    """See spec 02 Section 1.

    Entities have no fixed schema beyond id and name. All other state
    is a component keyed by ComponentTypeId.
    """
    id: EntityId
    name: str
    components: dict[ComponentTypeId, Any] = field(default_factory=dict)

    def get_stats(self) -> Stats | None:
        return self.components.get("stats")

    def get_resources(self) -> Resources | None:
        return self.components.get("resources")

    def get_position(self) -> Position | None:
        return self.components.get("position")

    def get_faction(self) -> Faction | None:
        return self.components.get("faction")

    def get_controller(self) -> ControllerType | None:
        return self.components.get("controller")

    def clone(self) -> Entity:
        return copy.deepcopy(self)


# ---------------------------------------------------------------------------
# Events  (spec 02 Section 6)
# ---------------------------------------------------------------------------

class EventKind(Enum):
    """Closed enumeration of event kinds. See spec 02 Section 6."""
    ENTITY_CREATED = "EntityCreated"
    ENTITY_REMOVED = "EntityRemoved"
    COMPONENT_SET = "ComponentSet"
    RESOURCE_CHANGED = "ResourceChanged"
    ROLL_MADE = "RollMade"
    ATTACK_RESOLVED = "AttackResolved"
    SAVE_RESOLVED = "SaveResolved"
    CHECK_RESOLVED = "CheckResolved"
    EFFECT_PUSHED = "EffectPushed"
    EFFECT_RESOLVED = "EffectResolved"
    EFFECT_EXPIRED = "EffectExpired"
    PERSISTENT_EFFECT_APPLIED = "PersistentEffectApplied"
    PERSISTENT_EFFECT_REMOVED = "PersistentEffectRemoved"
    CLOCK_TICKED = "ClockTicked"
    SCENE_ENTERED = "SceneEntered"
    SCENE_EXITED = "SceneExited"
    MODE_CHANGED = "ModeChanged"
    TURN_STARTED = "TurnStarted"
    TURN_ENDED = "TurnEnded"
    REACTION_OFFERED = "ReactionOffered"
    REACTION_TAKEN = "ReactionTaken"
    NARRATION = "Narration"
    DRIVER_REQUEST_EMITTED = "DriverRequestEmitted"
    DRIVER_RESPONSE_RECORDED = "DriverResponseRecorded"
    ERROR = "Error"
    LEVEL_UP_STARTED = "LevelUpStarted"
    LEVEL_UP_STEP = "LevelUpStep"
    LEVEL_UP_COMPLETED = "LevelUpCompleted"
    SESSION_STARTED = "SessionStarted"
    SESSION_ENDED = "SessionEnded"


@dataclass
class Event:
    """See spec 02 Section 6.

    Every state mutation produces events. Events are appended to an
    event log. The log is the single source of truth for replay.
    """
    id: EventId
    clock: dict[ClockTag, int] = field(default_factory=dict)
    kind: EventKind = EventKind.SESSION_STARTED
    source: EntityId | None = None
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        """Canonical dict representation. See spec 02 Section 7, Invariant D4."""
        d: dict[str, Any] = {
            "clock": dict(sorted(self.clock.items())),
            "data": _sort_dict_keys(self.data),
            "id": self.id,
            "kind": self.kind.value,
        }
        if self.source is not None:
            d["source"] = self.source
        return d


EventBatch = list[Event]


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------

def _sort_dict_keys(d: Any) -> Any:
    """Recursively sort dict keys for canonical form."""
    if isinstance(d, dict):
        return {k: _sort_dict_keys(v) for k, v in sorted(d.items())}
    if isinstance(d, list):
        return [_sort_dict_keys(item) for item in d]
    return d


def _die_kind_to_dict(dk: DieKind) -> Any:
    if isinstance(dk, DieKindDn):
        return {"Dn": dk.n}
    elif isinstance(dk, DieKindFudge):
        return "Fudge"
    elif isinstance(dk, DieKindConstant):
        return {"Constant": dk.value}
    return str(dk)


def _die_group_to_dict(g: DieGroup) -> dict:
    keep: Any
    if isinstance(g.keep, KeepRule):
        keep = g.keep.value
    elif isinstance(g.keep, KeepHighest):
        keep = {"KeepHighest": g.keep.n}
    elif isinstance(g.keep, KeepLowest):
        keep = {"KeepLowest": g.keep.n}
    elif isinstance(g.keep, DropHighest):
        keep = {"DropHighest": g.keep.n}
    elif isinstance(g.keep, DropLowest):
        keep = {"DropLowest": g.keep.n}
    else:
        keep = "All"
    return {
        "count": g.count,
        "keep": keep,
        "kind": _die_kind_to_dict(g.kind),
    }


def _die_roll_to_dict(r: DieRoll) -> dict:
    return {
        "dropped": r.dropped,
        "kind": _die_kind_to_dict(r.kind),
        "value": r.value,
    }


def _modifier_to_dict(m: Modifier) -> dict:
    src: Any
    if m.source.kind == "Proficiency":
        src = "Proficiency"
    elif m.source.value is not None:
        src = {m.source.kind: m.source.value}
    else:
        src = m.source.kind
    return {
        "applies_when": m.applies_when,
        "source": src,
        "value": m.value,
    }


def _roll_mode_to_value(mode: RollModeType) -> Any:
    if isinstance(mode, RollMode):
        return mode.value
    elif isinstance(mode, RollModeCustom):
        return {"Custom": mode.tag}
    return str(mode)


def _roll_purpose_to_value(purpose: RollPurposeType) -> Any:
    if isinstance(purpose, RollPurpose):
        return purpose.value
    elif isinstance(purpose, RollPurposeSave):
        return {"Save": {"save_id": purpose.save_id}}
    elif isinstance(purpose, RollPurposeCheck):
        return {"Check": {"check_id": purpose.check_id}}
    elif isinstance(purpose, RollPurposeDamage):
        return {"Damage": {"damage_type": purpose.damage_type}}
    elif isinstance(purpose, RollPurposeCustom):
        return {"Custom": purpose.tag}
    return str(purpose)


def die_kind_from_dict(d: Any) -> DieKind:
    """Deserialize a DieKind from its canonical JSON form."""
    if isinstance(d, str) and d == "Fudge":
        return DieKindFudge()
    if isinstance(d, dict):
        if "Dn" in d:
            return DieKindDn(d["Dn"])
        if "Constant" in d:
            return DieKindConstant(d["Constant"])
    raise ValueError(f"Unknown DieKind: {d}")


def keep_rule_from_dict(d: Any) -> KeepRuleType:
    """Deserialize a KeepRule from its canonical JSON form."""
    if isinstance(d, str) and d == "All":
        return KeepRule.ALL
    if isinstance(d, dict):
        if "KeepHighest" in d:
            return KeepHighest(d["KeepHighest"])
        if "KeepLowest" in d:
            return KeepLowest(d["KeepLowest"])
        if "DropHighest" in d:
            return DropHighest(d["DropHighest"])
        if "DropLowest" in d:
            return DropLowest(d["DropLowest"])
    raise ValueError(f"Unknown KeepRule: {d}")


def roll_mode_from_dict(d: Any) -> RollModeType:
    """Deserialize a RollMode from its canonical JSON form."""
    if isinstance(d, str):
        return RollMode(d)
    if isinstance(d, dict) and "Custom" in d:
        return RollModeCustom(d["Custom"])
    raise ValueError(f"Unknown RollMode: {d}")


def roll_purpose_from_dict(d: Any) -> RollPurposeType:
    """Deserialize a RollPurpose from its canonical JSON form."""
    if isinstance(d, str):
        return RollPurpose(d)
    if isinstance(d, dict):
        if "Save" in d:
            return RollPurposeSave(d["Save"]["save_id"])
        if "Check" in d:
            return RollPurposeCheck(d["Check"]["check_id"])
        if "Damage" in d:
            return RollPurposeDamage(d["Damage"]["damage_type"])
        if "Custom" in d:
            return RollPurposeCustom(d["Custom"])
    raise ValueError(f"Unknown RollPurpose: {d}")


def roll_spec_from_dict(d: dict) -> RollSpec:
    """Deserialize a RollSpec from its canonical JSON form."""
    dice = []
    for gd in d.get("dice", []):
        dice.append(DieGroup(
            count=gd["count"],
            kind=die_kind_from_dict(gd["kind"]),
            keep=keep_rule_from_dict(gd["keep"]),
        ))
    modifiers = []
    for md in d.get("modifiers", []):
        modifiers.append(Modifier(
            value=md["value"],
            source=ModifierSource(kind="Circumstance"),
            applies_when=md.get("applies_when", "Always"),
        ))
    return RollSpec(
        dice=dice,
        modifiers=modifiers,
        mode=roll_mode_from_dict(d.get("mode", "Straight")),
        purpose=roll_purpose_from_dict(d.get("purpose", "Attack")),
    )
