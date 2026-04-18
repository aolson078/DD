"""
Integration test: load pack + S01 scenario and run to completion.

Verifies the end-to-end pipeline:
1. Load the sample pack (packs/srd-5e-lite/pack.json)
2. Load the S01-damage-resistance scenario
3. Run the step() loop using ScriptedDriver
4. Verify the event log contains expected event kinds
"""
from __future__ import annotations

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

from reference.engine import (
    SessionState, StepInput, Transition, TransitionKind,
    create_session, step,
)
from reference.domain import EventKind
from reference.driver import ScriptedDriver
from reference.pack_loader import load_pack
from reference.run_scenario import load_initial_state, run_scenario, _add_scenario_actions_to_pack


PACK_PATH = _REPO_ROOT / "packs" / "srd-5e-lite" / "pack.json"
SCENARIO_DIR = _REPO_ROOT / "conformance" / "scenarios" / "S01-damage-resistance"


class TestLoadPack(unittest.TestCase):
    """Test that the sample pack loads without error."""

    def test_pack_loads(self):
        """Pack loads and validates successfully."""
        state = create_session()
        pack_data, state = load_pack(PACK_PATH, state)
        self.assertIn("srd.5e.lite", state.pack_ids)
        self.assertIsNotNone(state.pack_data)

    def test_pack_has_actions(self):
        """Pack contains expected top-level sections."""
        state = create_session()
        pack_data, state = load_pack(PACK_PATH, state)
        self.assertIn("actions", pack_data)
        self.assertIn("effect_templates", pack_data)
        self.assertIn("spells", pack_data)
        self.assertIn("stats", pack_data)

    def test_pack_manifest_fields(self):
        """Pack manifest has all required fields."""
        state = create_session()
        pack_data, state = load_pack(PACK_PATH, state)
        manifest = pack_data["manifest"]
        self.assertEqual(manifest["id"], "srd.5e.lite")
        self.assertEqual(manifest["pack_schema_version"], "1.0.0")
        self.assertEqual(manifest["sfs_version"], "1.0.0")


class TestLoadScenario(unittest.TestCase):
    """Test loading the S01 scenario initial state."""

    def setUp(self):
        state = create_session()
        self.pack_data, _ = load_pack(PACK_PATH, state)
        self.pack_data = _add_scenario_actions_to_pack(self.pack_data)

    def test_initial_state_loads(self):
        """initial_state.json loads into a valid SessionState."""
        initial_state_path = SCENARIO_DIR / "initial_state.json"
        state = load_initial_state(initial_state_path, self.pack_data)

        self.assertIsInstance(state, SessionState)
        self.assertTrue(state.started)
        self.assertEqual(len(state.entities), 2)

    def test_entities_parsed(self):
        """Entities are parsed with correct names and components."""
        initial_state_path = SCENARIO_DIR / "initial_state.json"
        state = load_initial_state(initial_state_path, self.pack_data)

        self.assertIn(1, state.entities)
        self.assertIn(2, state.entities)
        self.assertEqual(state.entities[1].name, "Wizard")
        self.assertEqual(state.entities[2].name, "Goblin")

        # Wizard should have stats with spell_attack_bonus
        wizard_stats = state.entities[1].get_stats()
        self.assertIsNotNone(wizard_stats)
        self.assertEqual(wizard_stats.scores.get("INT"), 18)
        self.assertEqual(wizard_stats.derived.get("spell_attack_bonus"), 7)

        # Goblin should have resources (HP)
        goblin_resources = state.entities[2].get_resources()
        self.assertIsNotNone(goblin_resources)
        hp = goblin_resources.pools.get("hp")
        self.assertIsNotNone(hp)
        self.assertEqual(hp.current, 21)
        self.assertEqual(hp.maximum, 21)

    def test_combat_mode_active(self):
        """Combat mode is active with correct initiative order."""
        initial_state_path = SCENARIO_DIR / "initial_state.json"
        state = load_initial_state(initial_state_path, self.pack_data)

        self.assertIsNotNone(state.combat)
        self.assertEqual(state.combat.initiative.order, [1, 2])
        self.assertEqual(state.combat.round, 1)

    def test_goblin_has_persistent_effects(self):
        """Goblin has fire resistance persistent effect."""
        initial_state_path = SCENARIO_DIR / "initial_state.json"
        state = load_initial_state(initial_state_path, self.pack_data)

        goblin = state.entities[2]
        pe = goblin.components.get("persistent_effects", [])
        self.assertTrue(len(pe) > 0)
        self.assertEqual(pe[0]["template_id"], "resistance_fire")

    def test_driver_script_loads(self):
        """driver_script.json loads into a ScriptedDriver."""
        driver_path = SCENARIO_DIR / "driver_script.json"
        driver = ScriptedDriver.from_file(driver_path)

        self.assertFalse(driver.exhausted)
        self.assertEqual(driver.remaining, 1)

    def test_pack_has_cast_firebolt_action(self):
        """Pack has cast_firebolt action after scenario augmentation."""
        action_ids = [a["id"] for a in self.pack_data.get("actions", [])]
        self.assertIn("cast_firebolt", action_ids)


class TestS01RunToCompletion(unittest.TestCase):
    """Test running the S01 scenario end-to-end."""

    def setUp(self):
        state = create_session()
        self.pack_data, _ = load_pack(PACK_PATH, state)
        self.pack_data = _add_scenario_actions_to_pack(self.pack_data)

        initial_state_path = SCENARIO_DIR / "initial_state.json"
        self.state = load_initial_state(initial_state_path, self.pack_data)

        driver_path = SCENARIO_DIR / "driver_script.json"
        self.driver = ScriptedDriver.from_file(driver_path)

    def test_scenario_runs_without_crash(self):
        """The scenario runs to completion without raising exceptions."""
        event_log = run_scenario(
            self.state, self.driver,
            max_steps=100,
            verbose=False,
        )
        self.assertTrue(len(event_log) > 0)

    def test_event_log_has_turn_started(self):
        """Event log contains at least one TurnStarted event."""
        event_log = run_scenario(
            self.state, self.driver,
            max_steps=100,
            verbose=False,
        )
        kinds = [e["kind"] for e in event_log]
        self.assertIn("TurnStarted", kinds)

    def test_event_log_has_driver_response_recorded(self):
        """Event log contains DriverResponseRecorded for the cast_firebolt."""
        event_log = run_scenario(
            self.state, self.driver,
            max_steps=100,
            verbose=False,
        )
        kinds = [e["kind"] for e in event_log]
        self.assertIn("DriverResponseRecorded", kinds)

    def test_event_log_has_roll_made(self):
        """Event log contains RollMade for the attack roll."""
        event_log = run_scenario(
            self.state, self.driver,
            max_steps=100,
            verbose=False,
        )
        kinds = [e["kind"] for e in event_log]
        self.assertIn("RollMade", kinds)

    def test_event_log_has_attack_resolved(self):
        """Event log contains AttackResolved."""
        event_log = run_scenario(
            self.state, self.driver,
            max_steps=100,
            verbose=False,
        )
        kinds = [e["kind"] for e in event_log]
        self.assertIn("AttackResolved", kinds)

    def test_event_log_has_session_ended(self):
        """Event log ends with SessionEnded."""
        event_log = run_scenario(
            self.state, self.driver,
            max_steps=100,
            verbose=False,
        )
        self.assertTrue(len(event_log) > 0)
        last = event_log[-1]
        self.assertEqual(last["kind"], "SessionEnded")

    def test_event_log_has_turn_ended(self):
        """Event log contains TurnEnded for entity 1."""
        event_log = run_scenario(
            self.state, self.driver,
            max_steps=100,
            verbose=False,
        )
        kinds = [e["kind"] for e in event_log]
        self.assertIn("TurnEnded", kinds)

    def test_attack_roll_uses_spell_attack_bonus(self):
        """The attack roll uses the Wizard's spell_attack_bonus (+7)."""
        event_log = run_scenario(
            self.state, self.driver,
            max_steps=100,
            verbose=False,
        )
        for event in event_log:
            if event["kind"] == "RollMade":
                result = event["data"].get("result", {})
                self.assertEqual(result.get("modifier_total"), 7)
                break
        else:
            self.fail("No RollMade event found")

    def test_driver_script_consumed(self):
        """The driver script is fully consumed."""
        run_scenario(
            self.state, self.driver,
            max_steps=100,
            verbose=False,
        )
        self.assertTrue(self.driver.exhausted)

    def test_all_events_have_required_fields(self):
        """Every event in the log has id, kind, clock, and data fields."""
        event_log = run_scenario(
            self.state, self.driver,
            max_steps=100,
            verbose=False,
        )
        for event in event_log:
            self.assertIn("id", event)
            self.assertIn("kind", event)
            # Error events from the host loop may not have clock/data
            if event["kind"] != "Error":
                self.assertIn("clock", event)
                self.assertIn("data", event)

    def test_event_ids_are_unique(self):
        """All event IDs are unique."""
        event_log = run_scenario(
            self.state, self.driver,
            max_steps=100,
            verbose=False,
        )
        ids = [e["id"] for e in event_log if "id" in e]
        self.assertEqual(len(ids), len(set(ids)),
                         f"Duplicate event IDs: {[x for x in ids if ids.count(x) > 1]}")

    def test_event_ids_monotonically_increase(self):
        """Event IDs increase monotonically."""
        event_log = run_scenario(
            self.state, self.driver,
            max_steps=100,
            verbose=False,
        )
        ids = [e["id"] for e in event_log if "id" in e]
        for i in range(1, len(ids)):
            self.assertGreater(ids[i], ids[i - 1],
                               f"Event IDs not monotonic at index {i}: "
                               f"{ids[i-1]} >= {ids[i]}")


if __name__ == "__main__":
    unittest.main()
