"""
Verify canonical JSON output.

See specs/02-domain-model.md Section 7, Invariant D4.

Rules verified:
1. Objects emitted with keys sorted by UTF-8 byte order
2. Strings use minimal-escape JSON
3. Numbers are integers only
4. No whitespace inside records
5. Arrays preserve authorial order
"""
from __future__ import annotations

import json
import unittest

from reference.canonical import canonical_json, canonical_hash, update_rolling_hash
from reference.domain import Event, EventKind


class TestCanonicalJson(unittest.TestCase):
    """Invariant D4: canonical JSON serialization."""

    def test_keys_sorted(self):
        """Object keys are sorted by UTF-8 byte order."""
        obj = {"zebra": 1, "apple": 2, "mango": 3}
        result = canonical_json(obj)
        self.assertEqual(result, '{"apple":2,"mango":3,"zebra":1}')

    def test_nested_keys_sorted(self):
        """Nested object keys are also sorted."""
        obj = {"b": {"z": 1, "a": 2}, "a": 3}
        result = canonical_json(obj)
        self.assertEqual(result, '{"a":3,"b":{"a":2,"z":1}}')

    def test_no_whitespace(self):
        """No whitespace inside records."""
        obj = {"key": "value", "num": 42}
        result = canonical_json(obj)
        self.assertNotIn(" ", result)
        self.assertNotIn("\n", result)
        self.assertNotIn("\t", result)

    def test_arrays_preserve_order(self):
        """Arrays preserve authorial order (not sorted)."""
        obj = {"items": [3, 1, 2]}
        result = canonical_json(obj)
        self.assertEqual(result, '{"items":[3,1,2]}')

    def test_integers_only(self):
        """Numbers are integers only."""
        obj = {"value": 42}
        result = canonical_json(obj)
        self.assertEqual(result, '{"value":42}')

    def test_null_value(self):
        """Null values are serialized as 'null'."""
        obj = {"value": None}
        result = canonical_json(obj)
        self.assertEqual(result, '{"value":null}')

    def test_boolean_values(self):
        """Boolean values are serialized correctly."""
        obj = {"a": True, "b": False}
        result = canonical_json(obj)
        self.assertEqual(result, '{"a":true,"b":false}')

    def test_string_minimal_escape(self):
        """Strings use minimal escaping."""
        obj = {"text": "hello world"}
        result = canonical_json(obj)
        self.assertEqual(result, '{"text":"hello world"}')

    def test_empty_object(self):
        result = canonical_json({})
        self.assertEqual(result, '{}')

    def test_empty_array(self):
        result = canonical_json([])
        self.assertEqual(result, '[]')

    def test_complex_nested(self):
        """Complex nested structure with sorted keys at all levels."""
        obj = {
            "z_outer": {
                "b_inner": [1, 2],
                "a_inner": "val"
            },
            "a_outer": True
        }
        result = canonical_json(obj)
        parsed = json.loads(result)
        # Verify keys are sorted
        keys_outer = list(json.loads(result).keys()) if isinstance(parsed, dict) else []
        # The JSON string itself should have a_outer before z_outer
        self.assertLess(result.index('"a_outer"'), result.index('"z_outer"'))


class TestCanonicalHash(unittest.TestCase):
    """Test canonical hashing for event log integrity."""

    def test_hash_determinism(self):
        """Same input produces same hash."""
        obj = {"kind": "SessionStarted", "seed": [42, 0, 0, 0]}
        h1 = canonical_hash(obj)
        h2 = canonical_hash(obj)
        self.assertEqual(h1, h2)

    def test_hash_is_sha256(self):
        """Hash is a 64-character hex string (SHA-256)."""
        obj = {"test": True}
        h = canonical_hash(obj)
        self.assertEqual(len(h), 64)
        self.assertTrue(all(c in "0123456789abcdef" for c in h))

    def test_different_input_different_hash(self):
        """Different inputs produce different hashes."""
        h1 = canonical_hash({"a": 1})
        h2 = canonical_hash({"a": 2})
        self.assertNotEqual(h1, h2)

    def test_key_order_does_not_affect_hash(self):
        """Key order in input dict does not affect hash (sorted internally)."""
        h1 = canonical_hash({"b": 2, "a": 1})
        h2 = canonical_hash({"a": 1, "b": 2})
        self.assertEqual(h1, h2)


class TestRollingHash(unittest.TestCase):
    """Test rolling hash update for event log."""

    def test_rolling_hash_determinism(self):
        """Rolling hash is deterministic."""
        initial = "0" * 64
        record = {"kind": "SessionStarted"}
        h1 = update_rolling_hash(initial, record)
        h2 = update_rolling_hash(initial, record)
        self.assertEqual(h1, h2)

    def test_rolling_hash_chain(self):
        """Rolling hash chain produces unique values per record."""
        h = "0" * 64
        records = [
            {"kind": "SessionStarted"},
            {"kind": "TurnStarted", "entity": 1},
            {"kind": "RollMade", "total": 15},
        ]
        hashes = []
        for r in records:
            h = update_rolling_hash(h, r)
            hashes.append(h)

        # All hashes should be unique
        self.assertEqual(len(set(hashes)), len(hashes))


class TestEventSerialization(unittest.TestCase):
    """Test Event.to_dict() produces canonical-ready output."""

    def test_event_to_dict_keys_sorted(self):
        """Event.to_dict() produces dict with sorted keys."""
        event = Event(
            id=1,
            clock={"round": 1, "minute": 0},
            kind=EventKind.SESSION_STARTED,
            data={"seed": [42, 0, 0, 0]},
        )
        d = event.to_dict()
        keys = list(d.keys())
        self.assertEqual(keys, sorted(keys))

    def test_event_canonical_json(self):
        """Event serializes to valid canonical JSON."""
        event = Event(
            id=1,
            clock={},
            kind=EventKind.SESSION_STARTED,
            data={"seed": [42]},
        )
        result = canonical_json(event.to_dict())
        # Should be valid JSON
        parsed = json.loads(result)
        self.assertEqual(parsed["kind"], "SessionStarted")
        self.assertEqual(parsed["id"], 1)


if __name__ == "__main__":
    unittest.main()
