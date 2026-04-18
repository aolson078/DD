"""
Verify RNG consumption schedule matches spec D2.

See specs/02-domain-model.md Section 3.5, Invariant D2.

Tests that:
1. next_u64 is deterministic (same seed -> same sequence)
2. Die mapping: value = (u64_raw % n) + 1 for Dn(n)
3. Fudge mapping: value = ((u64_raw % 3)) - 1
4. Constants consume no RNG
5. RollMode (Advantage/Disadvantage) consumes the correct number of u64s
6. KeepRules apply stable sort correctly
"""
from __future__ import annotations

import unittest

from reference.rng import Rng, seed_rng, next_u64, roll_die_value, roll_fudge_value
from reference.domain import (
    RollSpec, DieGroup, DieKindDn, DieKindFudge, DieKindConstant,
    RollMode, RollPurpose, KeepRule, KeepHighest, DropLowest,
    Modifier, ModifierSource,
)
from reference.rules import resolve_roll


class TestRngDeterminism(unittest.TestCase):
    """Invariant E1: calling next_u64 with the same state must yield the
    same result. Invariant E7: RNG discipline."""

    def test_same_seed_same_sequence(self):
        """Two RNGs with the same seed produce identical sequences."""
        rng1 = seed_rng([1, 2, 3, 4])
        rng2 = seed_rng([1, 2, 3, 4])

        for _ in range(100):
            v1, rng1 = next_u64(rng1)
            v2, rng2 = next_u64(rng2)
            self.assertEqual(v1, v2)

    def test_different_seeds_different_sequences(self):
        """Different seeds produce different sequences (after warmup)."""
        rng1 = seed_rng([1, 2, 3, 4])
        rng2 = seed_rng([5, 6, 7, 8])

        # Consume a few values to get past any initial overlap
        values1 = []
        values2 = []
        for _ in range(10):
            v1, rng1 = next_u64(rng1)
            v2, rng2 = next_u64(rng2)
            values1.append(v1)
            values2.append(v2)
        self.assertNotEqual(values1, values2)

    def test_immutability(self):
        """next_u64 returns a new Rng; the old one is unchanged."""
        rng = seed_rng([42, 0, 0, 0])
        original_s0 = rng.s0

        _, new_rng = next_u64(rng)
        # Original is unchanged (frozen dataclass)
        self.assertEqual(rng.s0, original_s0)
        # New state differs
        self.assertNotEqual(rng, new_rng)

    def test_zero_seed_substitution(self):
        """All-zero seed is substituted to avoid absorbing state."""
        rng = seed_rng([0, 0, 0, 0])
        self.assertEqual(rng.s0, 1)

    def test_u64_range(self):
        """Output values are in [0, 2^64)."""
        rng = seed_rng([7, 13, 37, 42])
        for _ in range(1000):
            v, rng = next_u64(rng)
            self.assertGreaterEqual(v, 0)
            self.assertLess(v, 2**64)


class TestDieMapping(unittest.TestCase):
    """Invariant D2: die value mapping from u64."""

    def test_d20_range(self):
        """roll_die_value with n=20 produces values in [1, 20]."""
        rng = seed_rng([42, 0, 0, 0])
        seen = set()
        for _ in range(10000):
            raw, rng = next_u64(rng)
            val = roll_die_value(raw, 20)
            self.assertGreaterEqual(val, 1)
            self.assertLessEqual(val, 20)
            seen.add(val)
        # Should see all faces with high probability
        self.assertEqual(seen, set(range(1, 21)))

    def test_d6_range(self):
        """roll_die_value with n=6 produces values in [1, 6]."""
        rng = seed_rng([42, 0, 0, 0])
        for _ in range(100):
            raw, rng = next_u64(rng)
            val = roll_die_value(raw, 6)
            self.assertGreaterEqual(val, 1)
            self.assertLessEqual(val, 6)

    def test_d2_range(self):
        """roll_die_value with n=2 produces values in [1, 2] (coin flip)."""
        rng = seed_rng([42, 0, 0, 0])
        seen = set()
        for _ in range(100):
            raw, rng = next_u64(rng)
            val = roll_die_value(raw, 2)
            seen.add(val)
        self.assertEqual(seen, {1, 2})

    def test_exact_mapping_formula(self):
        """Verify the exact formula: value = (u64_raw % n) + 1."""
        self.assertEqual(roll_die_value(0, 20), 1)       # 0 % 20 + 1 = 1
        self.assertEqual(roll_die_value(19, 20), 20)      # 19 % 20 + 1 = 20
        self.assertEqual(roll_die_value(20, 20), 1)       # 20 % 20 + 1 = 1
        self.assertEqual(roll_die_value(5, 6), 6)         # 5 % 6 + 1 = 6

    def test_fudge_range(self):
        """Fudge die produces values in [-1, 0, 1]."""
        rng = seed_rng([42, 0, 0, 0])
        seen = set()
        for _ in range(100):
            raw, rng = next_u64(rng)
            val = roll_fudge_value(raw)
            self.assertIn(val, {-1, 0, 1})
            seen.add(val)
        self.assertEqual(seen, {-1, 0, 1})

    def test_fudge_exact_mapping(self):
        """Verify fudge formula: value = ((u64_raw % 3)) - 1."""
        self.assertEqual(roll_fudge_value(0), -1)   # 0 % 3 - 1 = -1
        self.assertEqual(roll_fudge_value(1), 0)    # 1 % 3 - 1 = 0
        self.assertEqual(roll_fudge_value(2), 1)    # 2 % 3 - 1 = 1
        self.assertEqual(roll_fudge_value(3), -1)   # 3 % 3 - 1 = -1


class TestRollSpecResolution(unittest.TestCase):
    """Test resolve_roll following Invariant D2 consumption schedule."""

    def test_simple_d20(self):
        """Roll 1d20, straight mode."""
        rng = seed_rng([42, 0, 0, 0])
        spec = RollSpec(
            dice=[DieGroup(count=1, kind=DieKindDn(20))],
            modifiers=[],
            mode=RollMode.STRAIGHT,
            purpose=RollPurpose.ATTACK,
        )
        result, new_rng = resolve_roll(spec, rng)

        self.assertEqual(len(result.raw), 1)
        self.assertEqual(len(result.kept), 1)
        self.assertGreaterEqual(result.total, 1)
        self.assertLessEqual(result.total, 20)
        self.assertIsNotNone(result.natural)
        # RNG state must have advanced
        self.assertNotEqual(rng, new_rng)

    def test_d20_with_modifier(self):
        """Roll 1d20 + 5."""
        rng = seed_rng([42, 0, 0, 0])
        spec = RollSpec(
            dice=[DieGroup(count=1, kind=DieKindDn(20))],
            modifiers=[Modifier(value=5, source=ModifierSource(kind="Stat"))],
            mode=RollMode.STRAIGHT,
            purpose=RollPurpose.ATTACK,
        )
        result, _ = resolve_roll(spec, rng)

        self.assertEqual(result.modifier_total, 5)
        self.assertEqual(result.total, result.kept[0].value + 5)

    def test_2d6(self):
        """Roll 2d6."""
        rng = seed_rng([42, 0, 0, 0])
        spec = RollSpec(
            dice=[DieGroup(count=2, kind=DieKindDn(6))],
            modifiers=[],
            mode=RollMode.STRAIGHT,
            purpose=RollPurpose.ATTACK,
        )
        result, _ = resolve_roll(spec, rng)

        self.assertEqual(len(result.raw), 2)
        self.assertEqual(len(result.kept), 2)
        for r in result.kept:
            self.assertGreaterEqual(r.value, 1)
            self.assertLessEqual(r.value, 6)

    def test_constant_no_rng_consumed(self):
        """Constant dice consume no RNG."""
        rng = seed_rng([42, 0, 0, 0])
        spec = RollSpec(
            dice=[DieGroup(count=1, kind=DieKindConstant(5))],
            modifiers=[],
            mode=RollMode.STRAIGHT,
            purpose=RollPurpose.ATTACK,
        )
        result, new_rng = resolve_roll(spec, rng)

        self.assertEqual(result.total, 5)
        self.assertEqual(len(result.kept), 1)
        self.assertEqual(result.kept[0].value, 5)
        # RNG should NOT have advanced
        self.assertEqual(rng, new_rng)

    def test_advantage_rolls_twice(self):
        """Advantage mode rolls twice, keeps highest."""
        rng = seed_rng([42, 0, 0, 0])
        spec = RollSpec(
            dice=[DieGroup(count=1, kind=DieKindDn(20))],
            modifiers=[],
            mode=RollMode.ADVANTAGE,
            purpose=RollPurpose.ATTACK,
        )
        result, _ = resolve_roll(spec, rng)

        # Raw should have 2 rolls
        self.assertEqual(len(result.raw), 2)
        # Kept should have 1
        self.assertEqual(len(result.kept), 1)
        # Total should be the higher of the two
        values = [r.value for r in result.raw]
        self.assertEqual(result.total, max(values))

    def test_disadvantage_rolls_twice_keeps_lowest(self):
        """Disadvantage mode rolls twice, keeps lowest."""
        rng = seed_rng([42, 0, 0, 0])
        spec = RollSpec(
            dice=[DieGroup(count=1, kind=DieKindDn(20))],
            modifiers=[],
            mode=RollMode.DISADVANTAGE,
            purpose=RollPurpose.ATTACK,
        )
        result, _ = resolve_roll(spec, rng)

        self.assertEqual(len(result.raw), 2)
        self.assertEqual(len(result.kept), 1)
        values = [r.value for r in result.raw]
        self.assertEqual(result.total, min(values))

    def test_keep_highest(self):
        """4d6 keep highest 3 (classic ability score roll)."""
        rng = seed_rng([42, 0, 0, 0])
        spec = RollSpec(
            dice=[DieGroup(count=4, kind=DieKindDn(6), keep=KeepHighest(3))],
            modifiers=[],
            mode=RollMode.STRAIGHT,
            purpose=RollPurpose.ATTACK,
        )
        result, _ = resolve_roll(spec, rng)

        self.assertEqual(len(result.raw), 4)
        # 3 should be kept, 1 dropped
        dropped = [r for r in result.raw if r.dropped]
        kept = [r for r in result.raw if not r.dropped]
        self.assertEqual(len(dropped), 1)
        self.assertEqual(len(kept), 3)
        # The dropped one should be the lowest
        self.assertLessEqual(dropped[0].value, min(r.value for r in kept))

    def test_deterministic_across_calls(self):
        """Same spec + same RNG = same result (Invariant E1)."""
        rng = seed_rng([42, 0, 0, 0])
        spec = RollSpec(
            dice=[DieGroup(count=3, kind=DieKindDn(8))],
            modifiers=[Modifier(value=2, source=ModifierSource(kind="Stat"))],
            mode=RollMode.STRAIGHT,
            purpose=RollPurpose.ATTACK,
        )

        r1, rng1 = resolve_roll(spec, rng)
        r2, rng2 = resolve_roll(spec, rng)

        self.assertEqual(r1.total, r2.total)
        self.assertEqual(len(r1.raw), len(r2.raw))
        for a, b in zip(r1.raw, r2.raw):
            self.assertEqual(a.value, b.value)
        self.assertEqual(rng1, rng2)

    def test_rng_serialization(self):
        """RNG can be serialized and deserialized without loss."""
        rng = seed_rng([1, 2, 3, 4])
        d = rng.to_dict()
        restored = Rng.from_dict(d)
        self.assertEqual(rng, restored)

        # Same sequence after round-trip
        v1, _ = next_u64(rng)
        v2, _ = next_u64(restored)
        self.assertEqual(v1, v2)


if __name__ == "__main__":
    unittest.main()
