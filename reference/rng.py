"""
Seeded RNG with the exact consumption schedule from spec D2.

See specs/01-execution-model.md Section 5 (Invariant E7) and
specs/02-domain-model.md Section 3.5 (Invariant D2).

The RNG is a pure value: next_u64(rng) -> (u64, new_rng).
The engine never instantiates its own RNG; the Host seeds it.

We use xoshiro256** as a well-known, deterministic PRNG with a 256-bit
state that fits the u64-output contract.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Rng:
    """Immutable RNG state using xoshiro256** algorithm.

    See specs/01 Section 5, Invariant E7.
    The Engine exposes one operation: next_u64(rng) -> (u64, rng).
    """
    s0: int
    s1: int
    s2: int
    s3: int

    def to_dict(self) -> dict:
        return {"s0": self.s0, "s1": self.s1, "s2": self.s2, "s3": self.s3}

    @classmethod
    def from_dict(cls, d: dict) -> Rng:
        return cls(s0=d["s0"], s1=d["s1"], s2=d["s2"], s3=d["s3"])


_MASK64 = (1 << 64) - 1


def _rotl64(x: int, k: int) -> int:
    """Rotate left on a 64-bit value."""
    return ((x << k) | (x >> (64 - k))) & _MASK64


def seed_rng(seed: list[int]) -> Rng:
    """Create an Rng from a 4-element seed list of u64 values.

    If the seed is all zeros, we substitute a non-zero default to avoid
    the absorbing state.
    """
    s = [v & _MASK64 for v in seed]
    if all(v == 0 for v in s):
        s = [1, 0, 0, 0]
    return Rng(s0=s[0], s1=s[1], s2=s[2], s3=s[3])


def next_u64(rng: Rng) -> tuple[int, Rng]:
    """Produce the next u64 and a new Rng state.

    Uses xoshiro256** algorithm. This is the ONLY randomness primitive
    the engine uses. See specs/01 Invariant E7.
    """
    s0, s1, s2, s3 = rng.s0, rng.s1, rng.s2, rng.s3

    # xoshiro256** result calculation
    result = (_rotl64((s1 * 5) & _MASK64, 7) * 9) & _MASK64

    # State advance
    t = (s1 << 17) & _MASK64
    s2 ^= s0
    s3 ^= s1
    s1 ^= s2
    s0 ^= s3
    s2 ^= t
    s3 = _rotl64(s3, 45)

    return result, Rng(s0=s0 & _MASK64, s1=s1 & _MASK64,
                       s2=s2 & _MASK64, s3=s3 & _MASK64)


def roll_die_value(raw_u64: int, n: int) -> int:
    """Map a u64 to a die face [1..=n] for Dn(n).

    See specs/02 Invariant D2:
      value = (u64_raw % n) + 1

    Documented bias for non-power-of-two n is accepted as canonical.
    """
    return (raw_u64 % n) + 1


def roll_fudge_value(raw_u64: int) -> int:
    """Map a u64 to a Fudge die result [-1, 0, +1].

    See specs/02 Invariant D2:
      value = ((u64_raw % 3) as i32) - 1
    """
    return (raw_u64 % 3) - 1
