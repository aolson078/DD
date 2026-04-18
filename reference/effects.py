"""
EffectStack, resolution loop, trigger scan, transformers.

See specs/03-effects-and-stack.md for the normative definitions.

The stack is LIFO. Pushes append; resolves consume from the top.
Between any two frame resolutions, the engine performs a trigger scan.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

from reference.domain import (
    EntityId, EffectInstanceId, EventBatch, Event, EventKind,
)


# ---------------------------------------------------------------------------
# EffectBody variants  (spec 03 Section 2)
# ---------------------------------------------------------------------------

@dataclass
class EffectBodyDamage:
    """Damage effect. See spec 03 Section 2."""
    amounts: list[dict]    # [{expression, type, tags}]

@dataclass
class EffectBodyHeal:
    """Heal effect."""
    amount: dict           # {expression}

@dataclass
class EffectBodyGrantResource:
    """Grant resource effect."""
    resource: str
    amount: int

@dataclass
class EffectBodySpendResource:
    """Spend resource effect."""
    resource: str
    amount: int

@dataclass
class EffectBodySfsCall:
    """SFS function call. The escape hatch for richer packs. See spec 03 Section 2."""
    function: str
    args: Any

EffectBody = (EffectBodyDamage | EffectBodyHeal | EffectBodyGrantResource
              | EffectBodySpendResource | EffectBodySfsCall)


# ---------------------------------------------------------------------------
# Target  (spec 03 Section 2)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TargetSelf:
    pass

@dataclass(frozen=True)
class TargetEntity:
    entity_id: EntityId

@dataclass(frozen=True)
class TargetZone:
    zone_id: str

Target = TargetSelf | TargetEntity | TargetZone


# ---------------------------------------------------------------------------
# Resolution timing  (spec 03 Section 2)
# ---------------------------------------------------------------------------

class ResolutionTiming(Enum):
    IMMEDIATE = "Immediate"
    ON_STACK_RESOLVE = "OnStackResolve"


# ---------------------------------------------------------------------------
# EffectNode  (spec 03 Section 2)
# ---------------------------------------------------------------------------

@dataclass
class EffectNode:
    """See spec 03 Section 2.

    Body is declarative. The engine never resolves bodies with ad-hoc code;
    every resolution path is an SFS function call.
    """
    id: EffectInstanceId
    source: EntityId
    source_ability: str | None = None
    targets: list[Target] = field(default_factory=list)
    body: EffectBody | None = None
    transformers: list[Any] = field(default_factory=list)
    timing: ResolutionTiming = ResolutionTiming.ON_STACK_RESOLVE
    pushed_at: int = 0


# ---------------------------------------------------------------------------
# Stack frame  (spec 03 Section 3)
# ---------------------------------------------------------------------------

class StackFramePhase(Enum):
    """See spec 03 Section 3."""
    QUEUED = "Queued"
    AWAITING_REACTIONS = "AwaitingReactions"
    RESOLVING = "Resolving"
    RESOLVED = "Resolved"


@dataclass
class StackFrame:
    """See spec 03 Section 3."""
    node: EffectNode
    phase: StackFramePhase = StackFramePhase.QUEUED
    reaction_window: Any | None = None


# ---------------------------------------------------------------------------
# EffectStack  (spec 03 Section 3)
# ---------------------------------------------------------------------------

MAX_STACK_DEPTH = 128  # Invariant S2


@dataclass
class EffectStack:
    """See spec 03 Section 3.

    The stack is LIFO. Pushes append; resolves consume from the top.
    Invariant S2: len(frames) <= MAX_STACK_DEPTH.
    """
    frames: list[StackFrame] = field(default_factory=list)
    next_push_index: int = 0

    def is_empty(self) -> bool:
        return len(self.frames) == 0

    def depth(self) -> int:
        return len(self.frames)

    def top(self) -> StackFrame | None:
        if self.frames:
            return self.frames[-1]
        return None

    def push(self, node: EffectNode) -> list[Event]:
        """Push an effect node onto the stack. Returns events.

        See spec 03 Invariant S2: overflow is FatalError.
        """
        if len(self.frames) >= MAX_STACK_DEPTH:
            raise EffectStackOverflow(
                f"Stack depth {len(self.frames)} exceeds MAX_STACK_DEPTH "
                f"({MAX_STACK_DEPTH}). Invariant S2 violated."
            )
        node.pushed_at = self.next_push_index
        self.next_push_index += 1
        frame = StackFrame(node=node)
        self.frames.append(frame)
        return []

    def pop(self) -> StackFrame:
        """Pop the top frame. Raises if empty."""
        return self.frames.pop()

    def clone(self) -> EffectStack:
        return copy.deepcopy(self)


class EffectStackOverflow(Exception):
    """Invariant S2 violation: stack depth exceeded MAX_STACK_DEPTH."""
    pass


# ---------------------------------------------------------------------------
# Trigger  (spec 03 Section 4)
# ---------------------------------------------------------------------------

@dataclass
class TriggerConditionOnTurnStart:
    entity_filter: Any = None

@dataclass
class TriggerConditionOnTurnEnd:
    entity_filter: Any = None

@dataclass
class TriggerConditionOnDamaged:
    target_filter: Any = None
    damage_filter: Any = None

@dataclass
class TriggerConditionOnEvent:
    kind: str = ""
    filter: Any = None

TriggerCondition = (TriggerConditionOnTurnStart | TriggerConditionOnTurnEnd
                    | TriggerConditionOnDamaged | TriggerConditionOnEvent)


@dataclass
class Trigger:
    """See spec 03 Section 4."""
    id: str
    owner: EntityId | str  # EntityId or SceneId
    on: TriggerCondition | None = None
    effect_template: str = ""
    cost: Any | None = None
    priority: int = 0
    one_shot: bool = False
    reaction: bool = False


# ---------------------------------------------------------------------------
# Resolution loop  (spec 03 Section 8)
# ---------------------------------------------------------------------------

def resolve_top_of_stack(stack: EffectStack, state: Any) -> tuple[EffectStack, list[Event], bool]:
    """Resolve the top frame of the effect stack.

    See spec 03 Section 8.

    Returns (updated_stack, events, needs_decision).
    Each phase transition is one step() call's worth of work.
    """
    if stack.is_empty():
        return stack, [], False

    frame = stack.top()
    if frame is None:
        return stack, [], False

    events: list[Event] = []

    match frame.phase:
        case StackFramePhase.QUEUED:
            # Run trigger_scan (simplified: no triggers in minimal impl)
            # Gather transformers
            frame.phase = StackFramePhase.AWAITING_REACTIONS
            return stack, events, False

        case StackFramePhase.AWAITING_REACTIONS:
            # Open reaction window (simplified: skip reactions in minimal impl)
            frame.phase = StackFramePhase.RESOLVING
            return stack, events, False

        case StackFramePhase.RESOLVING:
            # Apply transformers and resolve via SFS dispatch
            # (The actual SFS dispatch is done by the caller using sfs.py)
            frame.phase = StackFramePhase.RESOLVED
            return stack, events, False

        case StackFramePhase.RESOLVED:
            # Pop frame, run trigger_scan
            stack.pop()
            return stack, events, False

    return stack, events, False
