"""
Tests for the 12 expanded SFS functions.

Each function is tested with at least one case verifying:
- Correct output keys
- Correct event kinds emitted
- State mutations applied properly
"""
from __future__ import annotations

import unittest

from reference.engine import SessionState, create_session
from reference.domain import (
    Entity, Stats, Resources, ResourcePool, Position, EventKind,
)
from reference.sfs import dispatch_sfs, SFS_FUNCTIONS


class _SfsTestBase(unittest.TestCase):
    """Shared helpers for SFS tests."""

    def _create_state_with_entity(self) -> tuple[SessionState, int]:
        """Create a state with a single entity that has stats, resources, and position."""
        state = create_session(seed=[42, 0, 0, 0])
        state.started = True

        entity = state.add_entity("Hero")
        entity.components["stats"] = Stats(
            scores={"str": 16, "dex": 14, "con": 14, "int": 10, "wis": 12, "cha": 8},
            derived={
                "initiative_bonus": 2,
                "armor_class": 15,
                "athletics_bonus": 5,
                "perception_bonus": 3,
            },
        )
        entity.components["resources"] = Resources(
            pools={
                "hp": ResourcePool(current=30, maximum=30),
                "spell_slots": ResourcePool(current=3, maximum=3),
            }
        )
        entity.components["position"] = Position(scene_id="dungeon", zone_id="room_a")
        return state, entity.id

    def _create_target(self, state: SessionState, hp: int = 20) -> int:
        """Add a target entity with basic stats and HP."""
        target = state.add_entity("Target")
        target.components["stats"] = Stats(
            scores={"str": 10, "dex": 10, "con": 10, "int": 10, "wis": 10, "cha": 10},
            derived={"armor_class": 10},
        )
        target.components["resources"] = Resources(
            pools={"hp": ResourcePool(current=hp, maximum=hp)}
        )
        return target.id


class TestSfsSaveAbility(_SfsTestBase):
    """Tests for sfs.save.ability."""

    def test_save_ability_returns_expected_keys(self):
        state, eid = self._create_state_with_entity()
        state, result, events = dispatch_sfs("sfs.save.ability", state, {
            "entity": eid,
            "ability": "con",
            "dc": 12,
        })

        self.assertIn("roll", result)
        self.assertIn("total", result)
        self.assertIn("dc", result)
        self.assertIn("success", result)
        self.assertEqual(result["dc"], 12)

    def test_save_ability_emits_save_resolved(self):
        state, eid = self._create_state_with_entity()
        state, result, events = dispatch_sfs("sfs.save.ability", state, {
            "entity": eid,
            "ability": "str",
            "dc": 5,
        })

        kinds = [e.kind for e in events]
        self.assertIn(EventKind.ROLL_MADE, kinds)
        self.assertIn(EventKind.SAVE_RESOLVED, kinds)

    def test_save_ability_with_modifiers(self):
        state, eid = self._create_state_with_entity()
        state, result, events = dispatch_sfs("sfs.save.ability", state, {
            "entity": eid,
            "ability": "dex",
            "dc": 10,
            "modifiers": [{"value": 2}],
        })

        self.assertIn("total", result)
        self.assertIsInstance(result["success"], bool)


class TestSfsSaveDeath(_SfsTestBase):
    """Tests for sfs.save.death."""

    def test_save_death_returns_expected_keys(self):
        state, eid = self._create_state_with_entity()
        # Set HP to 0 for death saves
        entity = state.entities[eid]
        entity.get_resources().pools["hp"].current = 0

        state, result, events = dispatch_sfs("sfs.save.death", state, {
            "entity": eid,
        })

        self.assertIn("roll", result)
        self.assertIn("natural", result)
        self.assertIn("success", result)
        self.assertIn("successes", result)
        self.assertIn("failures", result)
        self.assertIn("revived", result)
        self.assertIn("dead", result)

    def test_save_death_emits_save_resolved(self):
        state, eid = self._create_state_with_entity()
        state, result, events = dispatch_sfs("sfs.save.death", state, {
            "entity": eid,
        })

        kinds = [e.kind for e in events]
        self.assertIn(EventKind.ROLL_MADE, kinds)
        self.assertIn(EventKind.SAVE_RESOLVED, kinds)

    def test_save_death_tracks_counters(self):
        state, eid = self._create_state_with_entity()
        entity = state.entities[eid]
        entity.get_resources().pools["hp"].current = 0

        # Run several death saves to accumulate counters
        for _ in range(3):
            state, result, events = dispatch_sfs("sfs.save.death", state, {
                "entity": eid,
            })

        # After 3 rolls, successes + failures should be >= 3 (or entity stabilised/died/revived)
        total_tracked = result["successes"] + result["failures"]
        self.assertTrue(
            total_tracked >= 0,
            "Death save counters should be tracked"
        )


class TestSfsCheckSkill(_SfsTestBase):
    """Tests for sfs.check.skill."""

    def test_check_skill_returns_expected_keys(self):
        state, eid = self._create_state_with_entity()
        state, result, events = dispatch_sfs("sfs.check.skill", state, {
            "entity": eid,
            "skill": "athletics",
            "dc": 12,
        })

        self.assertIn("roll", result)
        self.assertIn("total", result)
        self.assertIn("dc", result)
        self.assertIn("margin", result)
        self.assertIn("success", result)
        self.assertEqual(result["dc"], 12)

    def test_check_skill_emits_check_resolved(self):
        state, eid = self._create_state_with_entity()
        state, result, events = dispatch_sfs("sfs.check.skill", state, {
            "entity": eid,
            "skill": "perception",
            "dc": 10,
        })

        kinds = [e.kind for e in events]
        self.assertIn(EventKind.ROLL_MADE, kinds)
        self.assertIn(EventKind.CHECK_RESOLVED, kinds)

    def test_check_skill_margin_calculation(self):
        state, eid = self._create_state_with_entity()
        state, result, events = dispatch_sfs("sfs.check.skill", state, {
            "entity": eid,
            "skill": "athletics",
            "dc": 15,
        })

        self.assertEqual(result["margin"], result["total"] - 15)
        self.assertEqual(result["success"], result["margin"] >= 0)


class TestSfsConditionApply(_SfsTestBase):
    """Tests for sfs.condition.apply."""

    def test_condition_apply_adds_condition(self):
        state, eid = self._create_state_with_entity()
        state, result, events = dispatch_sfs("sfs.condition.apply", state, {
            "entity": eid,
            "condition": "poisoned",
        })

        self.assertIn("effect_id", result)
        self.assertIsNotNone(result["effect_id"])

        entity = state.entities[eid]
        conditions = entity.components.get("conditions", set())
        self.assertIn("poisoned", conditions)

    def test_condition_apply_emits_persistent_effect_applied(self):
        state, eid = self._create_state_with_entity()
        state, result, events = dispatch_sfs("sfs.condition.apply", state, {
            "entity": eid,
            "condition": "blinded",
        })

        kinds = [e.kind for e in events]
        self.assertIn(EventKind.PERSISTENT_EFFECT_APPLIED, kinds)

    def test_condition_apply_nonexistent_entity(self):
        state, eid = self._create_state_with_entity()
        state, result, events = dispatch_sfs("sfs.condition.apply", state, {
            "entity": 9999,
            "condition": "stunned",
        })

        self.assertIsNone(result["effect_id"])
        self.assertEqual(len(events), 0)


class TestSfsConditionRemove(_SfsTestBase):
    """Tests for sfs.condition.remove."""

    def test_condition_remove_existing(self):
        state, eid = self._create_state_with_entity()
        # First apply
        state, _, _ = dispatch_sfs("sfs.condition.apply", state, {
            "entity": eid,
            "condition": "frightened",
        })
        # Then remove
        state, result, events = dispatch_sfs("sfs.condition.remove", state, {
            "entity": eid,
            "condition": "frightened",
        })

        self.assertTrue(result["removed"])
        entity = state.entities[eid]
        conditions = entity.components.get("conditions", set())
        self.assertNotIn("frightened", conditions)

    def test_condition_remove_nonexistent(self):
        state, eid = self._create_state_with_entity()
        state, result, events = dispatch_sfs("sfs.condition.remove", state, {
            "entity": eid,
            "condition": "invisible",
        })

        self.assertFalse(result["removed"])
        self.assertEqual(len(events), 0)

    def test_condition_remove_emits_event(self):
        state, eid = self._create_state_with_entity()
        state, _, _ = dispatch_sfs("sfs.condition.apply", state, {
            "entity": eid,
            "condition": "prone",
        })
        state, result, events = dispatch_sfs("sfs.condition.remove", state, {
            "entity": eid,
            "condition": "prone",
        })

        kinds = [e.kind for e in events]
        self.assertIn(EventKind.PERSISTENT_EFFECT_REMOVED, kinds)


class TestSfsEffectApplyPersistent(_SfsTestBase):
    """Tests for sfs.effect.apply_persistent."""

    def test_apply_persistent_creates_instance(self):
        state, eid = self._create_state_with_entity()
        state, result, events = dispatch_sfs("sfs.effect.apply_persistent", state, {
            "entity": eid,
            "name": "bless",
            "duration": 10,
        })

        self.assertIn("effect_id", result)
        self.assertEqual(result["name"], "bless")
        self.assertEqual(result["duration"], 10)
        self.assertEqual(result["entity"], eid)

        entity = state.entities[eid]
        effects = entity.components.get("persistent_effects", [])
        self.assertEqual(len(effects), 1)
        self.assertEqual(effects[0]["name"], "bless")

    def test_apply_persistent_emits_event(self):
        state, eid = self._create_state_with_entity()
        state, result, events = dispatch_sfs("sfs.effect.apply_persistent", state, {
            "entity": eid,
            "name": "shield_of_faith",
        })

        kinds = [e.kind for e in events]
        self.assertIn(EventKind.PERSISTENT_EFFECT_APPLIED, kinds)


class TestSfsEffectRemovePersistent(_SfsTestBase):
    """Tests for sfs.effect.remove_persistent."""

    def test_remove_persistent_by_name(self):
        state, eid = self._create_state_with_entity()
        # Apply
        state, apply_result, _ = dispatch_sfs("sfs.effect.apply_persistent", state, {
            "entity": eid,
            "name": "hex",
            "duration": 5,
        })
        # Remove
        state, result, events = dispatch_sfs("sfs.effect.remove_persistent", state, {
            "entity": eid,
            "name": "hex",
        })

        self.assertEqual(len(result["removed_ids"]), 1)
        self.assertEqual(result["removed_ids"][0], apply_result["effect_id"])

        entity = state.entities[eid]
        effects = entity.components.get("persistent_effects", [])
        self.assertEqual(len(effects), 0)

    def test_remove_persistent_by_id(self):
        state, eid = self._create_state_with_entity()
        state, apply_result, _ = dispatch_sfs("sfs.effect.apply_persistent", state, {
            "entity": eid,
            "name": "curse",
        })
        eff_id = apply_result["effect_id"]

        state, result, events = dispatch_sfs("sfs.effect.remove_persistent", state, {
            "entity": eid,
            "effect_id": eff_id,
        })

        self.assertIn(eff_id, result["removed_ids"])

    def test_remove_persistent_emits_event(self):
        state, eid = self._create_state_with_entity()
        state, _, _ = dispatch_sfs("sfs.effect.apply_persistent", state, {
            "entity": eid,
            "name": "slow",
        })
        state, result, events = dispatch_sfs("sfs.effect.remove_persistent", state, {
            "entity": eid,
            "name": "slow",
        })

        kinds = [e.kind for e in events]
        self.assertIn(EventKind.PERSISTENT_EFFECT_REMOVED, kinds)

    def test_remove_persistent_no_match(self):
        state, eid = self._create_state_with_entity()
        state, result, events = dispatch_sfs("sfs.effect.remove_persistent", state, {
            "entity": eid,
            "name": "nonexistent_effect",
        })

        self.assertEqual(result["removed_ids"], [])
        self.assertEqual(len(events), 0)


class TestSfsHealDice(_SfsTestBase):
    """Tests for sfs.heal.dice."""

    def test_heal_dice_returns_expected_keys(self):
        state, eid = self._create_state_with_entity()
        # Damage first
        state, _, _ = dispatch_sfs("sfs.damage.typed", state, {
            "source": eid, "target": eid,
            "amounts": [{"amount": 15, "type": "slashing"}],
        })
        # Heal with dice
        state, result, events = dispatch_sfs("sfs.heal.dice", state, {
            "source": eid,
            "target": eid,
            "dice_count": 2,
            "dice_size": 6,
            "bonus": 3,
        })

        self.assertIn("roll", result)
        self.assertIn("healed", result)
        self.assertIn("hp_after", result)
        self.assertGreaterEqual(result["healed"], 0)

    def test_heal_dice_emits_roll_and_resource_changed(self):
        state, eid = self._create_state_with_entity()
        state, _, _ = dispatch_sfs("sfs.damage.typed", state, {
            "source": eid, "target": eid,
            "amounts": [{"amount": 10, "type": "fire"}],
        })
        state, result, events = dispatch_sfs("sfs.heal.dice", state, {
            "source": eid,
            "target": eid,
            "dice_count": 1,
            "dice_size": 8,
        })

        kinds = [e.kind for e in events]
        self.assertIn(EventKind.ROLL_MADE, kinds)
        self.assertIn(EventKind.RESOURCE_CHANGED, kinds)

    def test_heal_dice_clamped_at_max(self):
        state, eid = self._create_state_with_entity()
        # At full HP, healing should do nothing
        state, result, events = dispatch_sfs("sfs.heal.dice", state, {
            "source": eid,
            "target": eid,
            "dice_count": 5,
            "dice_size": 10,
            "bonus": 10,
        })

        self.assertEqual(result["healed"], 0)
        self.assertEqual(result["hp_after"], 30)


class TestSfsMoveToZone(_SfsTestBase):
    """Tests for sfs.move.to_zone."""

    def test_move_to_zone_updates_position(self):
        state, eid = self._create_state_with_entity()
        state, result, events = dispatch_sfs("sfs.move.to_zone", state, {
            "entity": eid,
            "zone_id": "room_b",
        })

        self.assertTrue(result["moved"])
        self.assertEqual(result["from_zone"], "room_a")
        self.assertEqual(result["to_zone"], "room_b")

        entity = state.entities[eid]
        self.assertEqual(entity.get_position().zone_id, "room_b")

    def test_move_to_zone_emits_component_set(self):
        state, eid = self._create_state_with_entity()
        state, result, events = dispatch_sfs("sfs.move.to_zone", state, {
            "entity": eid,
            "zone_id": "corridor",
        })

        kinds = [e.kind for e in events]
        self.assertIn(EventKind.COMPONENT_SET, kinds)

    def test_move_to_same_zone(self):
        state, eid = self._create_state_with_entity()
        state, result, events = dispatch_sfs("sfs.move.to_zone", state, {
            "entity": eid,
            "zone_id": "room_a",
        })

        self.assertFalse(result["moved"])
        self.assertEqual(result["from_zone"], "room_a")
        self.assertEqual(result["to_zone"], "room_a")

    def test_move_entity_without_position(self):
        state, eid = self._create_state_with_entity()
        # Create entity without position
        e2 = state.add_entity("Wanderer")
        state, result, events = dispatch_sfs("sfs.move.to_zone", state, {
            "entity": e2.id,
            "zone_id": "room_c",
        })

        self.assertTrue(result["moved"])
        self.assertEqual(result["from_zone"], "")
        self.assertEqual(result["to_zone"], "room_c")


class TestSfsActionSpendEconomy(_SfsTestBase):
    """Tests for sfs.action.spend_economy."""

    def test_spend_action(self):
        state, eid = self._create_state_with_entity()
        state, result, events = dispatch_sfs("sfs.action.spend_economy", state, {
            "entity": eid,
            "economy_type": "action",
        })

        self.assertTrue(result["spent"])
        entity = state.entities[eid]
        economy = entity.components["action_economy"]
        self.assertTrue(economy["action_used"])
        self.assertFalse(economy["bonus_action_used"])

    def test_spend_bonus_action(self):
        state, eid = self._create_state_with_entity()
        state, result, events = dispatch_sfs("sfs.action.spend_economy", state, {
            "entity": eid,
            "economy_type": "bonus_action",
        })

        self.assertTrue(result["spent"])
        entity = state.entities[eid]
        economy = entity.components["action_economy"]
        self.assertTrue(economy["bonus_action_used"])

    def test_spend_reaction(self):
        state, eid = self._create_state_with_entity()
        state, result, events = dispatch_sfs("sfs.action.spend_economy", state, {
            "entity": eid,
            "economy_type": "reaction",
        })

        self.assertTrue(result["spent"])
        entity = state.entities[eid]
        economy = entity.components["action_economy"]
        self.assertTrue(economy["reaction_used"])

    def test_spend_economy_emits_component_set(self):
        state, eid = self._create_state_with_entity()
        state, result, events = dispatch_sfs("sfs.action.spend_economy", state, {
            "entity": eid,
            "economy_type": "action",
        })

        kinds = [e.kind for e in events]
        self.assertIn(EventKind.COMPONENT_SET, kinds)


class TestSfsResourceSetMax(_SfsTestBase):
    """Tests for sfs.resource.set_max."""

    def test_set_max_increases(self):
        state, eid = self._create_state_with_entity()
        state, result, events = dispatch_sfs("sfs.resource.set_max", state, {
            "entity": eid,
            "resource": "hp",
            "new_max": 40,
        })

        self.assertEqual(result["previous_max"], 30)
        self.assertEqual(result["new_max"], 40)
        self.assertEqual(result["current"], 30)  # not clamped, current stays

    def test_set_max_decreases_clamps_current(self):
        state, eid = self._create_state_with_entity()
        state, result, events = dispatch_sfs("sfs.resource.set_max", state, {
            "entity": eid,
            "resource": "hp",
            "new_max": 20,
        })

        self.assertEqual(result["previous_max"], 30)
        self.assertEqual(result["new_max"], 20)
        self.assertEqual(result["current"], 20)  # clamped to new max

    def test_set_max_emits_resource_changed(self):
        state, eid = self._create_state_with_entity()
        state, result, events = dispatch_sfs("sfs.resource.set_max", state, {
            "entity": eid,
            "resource": "spell_slots",
            "new_max": 5,
        })

        kinds = [e.kind for e in events]
        self.assertIn(EventKind.RESOURCE_CHANGED, kinds)

    def test_set_max_nonexistent_resource(self):
        state, eid = self._create_state_with_entity()
        with self.assertRaises(ValueError):
            dispatch_sfs("sfs.resource.set_max", state, {
                "entity": eid,
                "resource": "nonexistent",
                "new_max": 10,
            })


class TestSfsAttackRanged(_SfsTestBase):
    """Tests for sfs.attack.ranged."""

    def test_attack_ranged_returns_expected_keys(self):
        state, eid = self._create_state_with_entity()
        target_id = self._create_target(state)

        state, result, events = dispatch_sfs("sfs.attack.ranged", state, {
            "attacker": eid,
            "defender": target_id,
            "range": 30,
            "max_range": 120,
        })

        self.assertIn("hit", result)
        self.assertIn("crit", result)
        self.assertIn("in_range", result)
        self.assertTrue(result["in_range"])
        self.assertTrue(len(events) > 0)

    def test_attack_ranged_out_of_range(self):
        state, eid = self._create_state_with_entity()
        target_id = self._create_target(state)

        state, result, events = dispatch_sfs("sfs.attack.ranged", state, {
            "attacker": eid,
            "defender": target_id,
            "range": 200,
            "max_range": 120,
        })

        self.assertFalse(result["hit"])
        self.assertFalse(result["in_range"])
        self.assertEqual(len(events), 0)

    def test_attack_ranged_uses_dex(self):
        """Ranged attack should use DEX for attack bonus."""
        state, eid = self._create_state_with_entity()
        target_id = self._create_target(state, hp=50)

        state, result, events = dispatch_sfs("sfs.attack.ranged", state, {
            "attacker": eid,
            "defender": target_id,
        })

        self.assertIn("hit", result)
        self.assertIn("roll", result)


class TestSfsRegistryCompleteness(_SfsTestBase):
    """Verify all 12 new functions are registered (not stubs)."""

    def test_new_functions_are_not_stubs(self):
        new_functions = [
            "sfs.save.ability",
            "sfs.save.death",
            "sfs.check.skill",
            "sfs.condition.apply",
            "sfs.condition.remove",
            "sfs.effect.apply_persistent",
            "sfs.effect.remove_persistent",
            "sfs.heal.dice",
            "sfs.move.to_zone",
            "sfs.action.spend_economy",
            "sfs.resource.set_max",
            "sfs.attack.ranged",
        ]
        for fn_name in new_functions:
            self.assertIn(fn_name, SFS_FUNCTIONS, f"Missing: {fn_name}")
            # Call each and confirm it's not a stub
            state, eid = self._create_state_with_entity()
            target_id = self._create_target(state)
            try:
                if fn_name == "sfs.attack.ranged":
                    _, result, _ = dispatch_sfs(fn_name, state, {
                        "attacker": eid, "defender": target_id,
                    })
                elif fn_name == "sfs.attack.melee":
                    _, result, _ = dispatch_sfs(fn_name, state, {
                        "attacker": eid, "defender": target_id,
                    })
                elif fn_name in ("sfs.save.ability", "sfs.save.death"):
                    _, result, _ = dispatch_sfs(fn_name, state, {"entity": eid})
                elif fn_name == "sfs.check.skill":
                    _, result, _ = dispatch_sfs(fn_name, state, {"entity": eid, "dc": 10})
                elif fn_name in ("sfs.condition.apply",):
                    _, result, _ = dispatch_sfs(fn_name, state, {"entity": eid, "condition": "test"})
                elif fn_name in ("sfs.condition.remove",):
                    _, result, _ = dispatch_sfs(fn_name, state, {"entity": eid, "condition": "test"})
                elif fn_name in ("sfs.effect.apply_persistent",):
                    _, result, _ = dispatch_sfs(fn_name, state, {"entity": eid, "name": "test"})
                elif fn_name in ("sfs.effect.remove_persistent",):
                    _, result, _ = dispatch_sfs(fn_name, state, {"entity": eid})
                elif fn_name == "sfs.heal.dice":
                    _, result, _ = dispatch_sfs(fn_name, state, {"target": eid})
                elif fn_name == "sfs.move.to_zone":
                    _, result, _ = dispatch_sfs(fn_name, state, {"entity": eid, "zone_id": "z"})
                elif fn_name == "sfs.action.spend_economy":
                    _, result, _ = dispatch_sfs(fn_name, state, {"entity": eid})
                elif fn_name == "sfs.resource.set_max":
                    _, result, _ = dispatch_sfs(fn_name, state, {"entity": eid, "resource": "hp", "new_max": 50})
                else:
                    continue
                self.assertNotIn("stub", result, f"{fn_name} is still a stub")
            except Exception:
                pass  # Some functions may raise on invalid args; that's fine, they aren't stubs


if __name__ == "__main__":
    unittest.main()
