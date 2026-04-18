"""
Tests for scenarios S02 through S05.

Each test loads the scenario, runs it to completion, and verifies:
1. No exceptions are raised during execution
2. The event log is non-empty
3. The event log ends with SessionEnded
4. All events have required fields (id, kind, clock, data)
5. Scenario-specific assertions on key events
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

from reference.engine import create_session
from reference.driver import ScriptedDriver
from reference.pack_loader import load_pack
from reference.run_scenario import (
    load_initial_state, run_scenario, _add_scenario_actions_to_pack,
)


PACK_PATH = _REPO_ROOT / "packs" / "srd-5e-lite" / "pack.json"
SCENARIOS_DIR = _REPO_ROOT / "conformance" / "scenarios"


def _load_and_run_scenario(scenario_name: str) -> list[dict]:
    """Helper: load pack, load scenario, run to completion, return event log."""
    state = create_session()
    pack_data, _ = load_pack(PACK_PATH, state)
    pack_data = _add_scenario_actions_to_pack(pack_data)

    scenario_dir = SCENARIOS_DIR / scenario_name
    initial_state_path = scenario_dir / "initial_state.json"
    driver_script_path = scenario_dir / "driver_script.json"

    state = load_initial_state(initial_state_path, pack_data)
    driver = ScriptedDriver.from_file(driver_script_path)

    event_log = run_scenario(state, driver, max_steps=200, verbose=False)
    return event_log


class TestS02AdvantageCanceled(unittest.TestCase):
    """S02: Advantage canceled by disadvantage -> straight roll for stealth."""

    def setUp(self):
        self.event_log = _load_and_run_scenario("S02-advantage-canceled")

    def test_runs_without_crash(self):
        """Scenario runs to completion without raising exceptions."""
        self.assertTrue(len(self.event_log) > 0)

    def test_ends_with_session_ended(self):
        """Event log ends with SessionEnded."""
        self.assertEqual(self.event_log[-1]["kind"], "SessionEnded")

    def test_has_check_resolved(self):
        """Event log contains a CheckResolved for the stealth check."""
        check_events = [
            e for e in self.event_log if e["kind"] == "CheckResolved"
        ]
        self.assertTrue(len(check_events) >= 1,
                        "Expected at least one CheckResolved event")
        check = check_events[0]
        self.assertEqual(check["data"]["skill"], "stealth")

    def test_roll_mode_is_straight(self):
        """The stealth roll uses Straight mode (advantage + disadvantage cancel)."""
        roll_events = [
            e for e in self.event_log if e["kind"] == "RollMade"
        ]
        self.assertTrue(len(roll_events) >= 1)
        spec = roll_events[0]["data"].get("spec", {})
        self.assertEqual(spec.get("mode"), "Straight")

    def test_stealth_bonus_applied(self):
        """The stealth check uses the Rogue's stealth_bonus (+8)."""
        roll_events = [
            e for e in self.event_log if e["kind"] == "RollMade"
        ]
        self.assertTrue(len(roll_events) >= 1)
        result = roll_events[0]["data"].get("result", {})
        self.assertEqual(result.get("modifier_total"), 8)

    def test_all_events_have_required_fields(self):
        """Every event has id, kind, clock, and data fields."""
        for event in self.event_log:
            self.assertIn("id", event)
            self.assertIn("kind", event)
            if event["kind"] != "Error":
                self.assertIn("clock", event)
                self.assertIn("data", event)


class TestS03OpportunityAttack(unittest.TestCase):
    """S03: Fighter moves, Goblin attacks on its turn."""

    def setUp(self):
        self.event_log = _load_and_run_scenario("S03-opportunity-attack")

    def test_runs_without_crash(self):
        """Scenario runs to completion without raising exceptions."""
        self.assertTrue(len(self.event_log) > 0)

    def test_ends_with_session_ended(self):
        """Event log ends with SessionEnded."""
        self.assertEqual(self.event_log[-1]["kind"], "SessionEnded")

    def test_has_movement(self):
        """Event log contains a ComponentSet event for movement."""
        move_events = [
            e for e in self.event_log
            if e["kind"] == "ComponentSet"
            and e["data"].get("component") == "position"
        ]
        self.assertTrue(len(move_events) >= 1,
                        "Expected at least one movement ComponentSet event")
        self.assertEqual(move_events[0]["data"]["to_zone"], "zone_b")
        self.assertTrue(move_events[0]["data"]["moved"])

    def test_has_attack_resolved(self):
        """Event log contains an AttackResolved for the goblin attacking."""
        attack_events = [
            e for e in self.event_log if e["kind"] == "AttackResolved"
        ]
        self.assertTrue(len(attack_events) >= 1,
                        "Expected at least one AttackResolved event")
        attack = attack_events[0]
        self.assertEqual(attack["data"]["attacker"], 2)
        self.assertEqual(attack["data"]["defender"], 1)

    def test_has_both_turns(self):
        """Both entity 1 and entity 2 get TurnStarted events."""
        turn_started = [
            e for e in self.event_log if e["kind"] == "TurnStarted"
        ]
        entities = [e["data"]["entity"] for e in turn_started]
        self.assertIn(1, entities)
        self.assertIn(2, entities)

    def test_all_events_have_required_fields(self):
        """Every event has id, kind, clock, and data fields."""
        for event in self.event_log:
            self.assertIn("id", event)
            self.assertIn("kind", event)
            if event["kind"] != "Error":
                self.assertIn("clock", event)
                self.assertIn("data", event)


class TestS04Counterspell(unittest.TestCase):
    """S04: Wizard casts firebolt at enemy wizard."""

    def setUp(self):
        self.event_log = _load_and_run_scenario("S04-counterspell")

    def test_runs_without_crash(self):
        """Scenario runs to completion without raising exceptions."""
        self.assertTrue(len(self.event_log) > 0)

    def test_ends_with_session_ended(self):
        """Event log ends with SessionEnded."""
        self.assertEqual(self.event_log[-1]["kind"], "SessionEnded")

    def test_has_attack_resolved(self):
        """Event log contains an AttackResolved event for the firebolt."""
        attack_events = [
            e for e in self.event_log if e["kind"] == "AttackResolved"
        ]
        self.assertTrue(len(attack_events) >= 1,
                        "Expected at least one AttackResolved event")
        self.assertEqual(attack_events[0]["data"]["attacker"], 1)
        self.assertEqual(attack_events[0]["data"]["defender"], 2)

    def test_has_roll_made(self):
        """Event log contains at least one RollMade event."""
        roll_events = [
            e for e in self.event_log if e["kind"] == "RollMade"
        ]
        self.assertTrue(len(roll_events) >= 1)

    def test_all_events_have_required_fields(self):
        """Every event has id, kind, clock, and data fields."""
        for event in self.event_log:
            self.assertIn("id", event)
            self.assertIn("kind", event)
            if event["kind"] != "Error":
                self.assertIn("clock", event)
                self.assertIn("data", event)


class TestS05ConcentrationBroken(unittest.TestCase):
    """S05: Cleric takes damage, fails concentration save, Bless removed."""

    def setUp(self):
        self.event_log = _load_and_run_scenario("S05-concentration-broken")

    def test_runs_without_crash(self):
        """Scenario runs to completion without raising exceptions."""
        self.assertTrue(len(self.event_log) > 0)

    def test_ends_with_session_ended(self):
        """Event log ends with SessionEnded."""
        self.assertEqual(self.event_log[-1]["kind"], "SessionEnded")

    def test_attack_hits(self):
        """The skeleton's attack hits the cleric."""
        attack_events = [
            e for e in self.event_log if e["kind"] == "AttackResolved"
        ]
        self.assertTrue(len(attack_events) >= 1)
        self.assertTrue(attack_events[0]["data"]["hit"])
        self.assertEqual(attack_events[0]["data"]["attacker"], 2)
        self.assertEqual(attack_events[0]["data"]["defender"], 1)

    def test_hp_reduced(self):
        """The cleric's HP is reduced by the attack."""
        hp_events = [
            e for e in self.event_log
            if e["kind"] == "ResourceChanged"
            and e["data"].get("resource") == "hp"
            and e["data"].get("entity") == 1
        ]
        self.assertTrue(len(hp_events) >= 1,
                        "Expected ResourceChanged for cleric HP")
        self.assertLess(hp_events[0]["data"]["delta"], 0)

    def test_concentration_save_attempted(self):
        """A concentration saving throw is attempted."""
        save_events = [
            e for e in self.event_log if e["kind"] == "SaveResolved"
        ]
        self.assertTrue(len(save_events) >= 1,
                        "Expected SaveResolved for concentration")
        self.assertEqual(save_events[0]["data"]["ability"], "concentration")

    def test_concentration_save_fails(self):
        """The concentration save fails."""
        save_events = [
            e for e in self.event_log if e["kind"] == "SaveResolved"
        ]
        self.assertTrue(len(save_events) >= 1)
        self.assertFalse(save_events[0]["data"]["success"])

    def test_bless_removed(self):
        """Bless persistent effect is removed with ConcentrationBroken reason."""
        removal_events = [
            e for e in self.event_log
            if e["kind"] == "PersistentEffectRemoved"
        ]
        self.assertTrue(len(removal_events) >= 1,
                        "Expected PersistentEffectRemoved for Bless")
        self.assertEqual(removal_events[0]["data"]["reason"],
                         "ConcentrationBroken")
        self.assertEqual(removal_events[0]["data"]["entity"], 1)
        self.assertEqual(removal_events[0]["data"]["effect_id"], 100)

    def test_all_events_have_required_fields(self):
        """Every event has id, kind, clock, and data fields."""
        for event in self.event_log:
            self.assertIn("id", event)
            self.assertIn("kind", event)
            if event["kind"] != "Error":
                self.assertIn("clock", event)
                self.assertIn("data", event)

    def test_event_ids_monotonically_increase(self):
        """Event IDs increase monotonically."""
        ids = [e["id"] for e in self.event_log if "id" in e]
        for i in range(1, len(ids)):
            self.assertGreater(ids[i], ids[i - 1])


if __name__ == "__main__":
    unittest.main()
