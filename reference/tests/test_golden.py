"""
Golden-hash conformance test for scenario S01.

Runs the S01-damage-resistance scenario through the reference engine and
verifies the SHA-256 log_hash matches the expected golden value recorded
in conformance/suite.json.

See specs/09-persistence-and-replay.md Section 3.2 for the log_hash
algorithm, and specs/09 Section 2 (Invariant PS1) for canonicalization.
"""
from __future__ import annotations

import hashlib
import json
import sys
import unittest
from pathlib import Path

# Ensure repo root is on sys.path
_TESTS_DIR = Path(__file__).resolve().parent
_REFERENCE_DIR = _TESTS_DIR.parent
_REPO_ROOT = _REFERENCE_DIR.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from reference.canonical import canonical_json
from reference.driver import ScriptedDriver
from reference.engine import create_session
from reference.pack_loader import load_pack
from reference.run_scenario import (
    _add_scenario_actions_to_pack,
    load_initial_state,
    run_scenario,
)

PACK_PATH = _REPO_ROOT / "packs" / "srd-5e-lite" / "pack.json"
SUITE_PATH = _REPO_ROOT / "conformance" / "suite.json"
SCENARIO_DIR = _REPO_ROOT / "conformance" / "scenarios" / "S01-damage-resistance"


def _compute_log_hash(event_log: list[dict]) -> str:
    """Compute log_hash per spec 09 Section 3.2.

    h = SHA256()
    for record in log.records:
        h.update(canonical_bytes(record))
        h.update(b"\\n")
    return h.hexdigest()
    """
    h = hashlib.sha256()
    for record in event_log:
        h.update(canonical_json(record).encode("utf-8"))
        h.update(b"\n")
    return h.hexdigest()


def _run_s01() -> list[dict]:
    """Load pack + S01 scenario and run to completion."""
    state = create_session()
    pack_data, _ = load_pack(PACK_PATH, state)
    pack_data = _add_scenario_actions_to_pack(pack_data)

    initial_state_path = SCENARIO_DIR / "initial_state.json"
    state = load_initial_state(initial_state_path, pack_data)

    driver_path = SCENARIO_DIR / "driver_script.json"
    driver = ScriptedDriver.from_file(driver_path)

    return run_scenario(state, driver, max_steps=100, verbose=False)


class TestGoldenHashS01(unittest.TestCase):
    """Verify S01's event log hash matches the golden value in suite.json."""

    def setUp(self):
        self.event_log = _run_s01()

        with open(SUITE_PATH, "r", encoding="utf-8") as f:
            suite = json.load(f)

        self.expected_hash = None
        for scenario in suite["scenarios"]:
            if scenario["id"] == "S01":
                self.expected_hash = scenario["expected_log_sha256"]
                break

    def test_golden_hash_is_set(self):
        """suite.json has a non-TBD hash for S01."""
        self.assertIsNotNone(self.expected_hash)
        self.assertNotEqual(self.expected_hash, "TBD",
                            "S01 expected_log_sha256 is still TBD")

    def test_log_hash_matches_golden(self):
        """The computed log_hash matches the golden value from suite.json."""
        computed = _compute_log_hash(self.event_log)
        self.assertEqual(
            computed,
            self.expected_hash,
            f"S01 log_hash mismatch:\n"
            f"  computed: {computed}\n"
            f"  expected: {self.expected_hash}",
        )

    def test_hash_is_deterministic(self):
        """Running S01 twice produces the same log_hash."""
        hash1 = _compute_log_hash(self.event_log)
        event_log2 = _run_s01()
        hash2 = _compute_log_hash(event_log2)
        self.assertEqual(hash1, hash2,
                         "S01 log_hash is not deterministic across runs")

    def test_expected_events_match(self):
        """The event log matches the expected_events.json file."""
        expected_path = SCENARIO_DIR / "expected_events.json"
        with open(expected_path, "r", encoding="utf-8") as f:
            expected_events = json.load(f)

        self.assertEqual(
            len(self.event_log), len(expected_events),
            f"Event count mismatch: got {len(self.event_log)}, "
            f"expected {len(expected_events)}",
        )

        for i, (actual, expected) in enumerate(
            zip(self.event_log, expected_events)
        ):
            # Compare canonical JSON forms to ignore key ordering
            actual_canonical = canonical_json(actual)
            expected_canonical = canonical_json(expected)
            self.assertEqual(
                actual_canonical,
                expected_canonical,
                f"Event {i} mismatch:\n"
                f"  actual:   {actual_canonical}\n"
                f"  expected: {expected_canonical}",
            )

    def test_event_log_canonical_form(self):
        """Each event record round-trips through canonical JSON identically."""
        for i, record in enumerate(self.event_log):
            canonical = canonical_json(record)
            reparsed = json.loads(canonical)
            canonical2 = canonical_json(reparsed)
            self.assertEqual(
                canonical, canonical2,
                f"Event {i} is not stable under re-canonicalization",
            )


if __name__ == "__main__":
    unittest.main()
