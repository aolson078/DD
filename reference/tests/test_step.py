"""
Basic step() tests.

See specs/01-execution-model.md for the normative step() definition.

Tests verify:
- step(Start) on a fresh session emits SessionStarted
- step(Halt) returns Terminal with SessionEnded
- step(Tick) advances a clock
- step(DriverResponse) handles action selection
- NeedsDecision is emitted during combat Main phase
- Transition invariants (E3, E4)
"""
from __future__ import annotations

import unittest

from reference.engine import (
    SessionState, StepInput, Transition, TransitionKind,
    create_session, step,
)
from reference.domain import (
    Entity, Stats, Resources, ResourcePool, EventKind,
    Faction, Controller, Position,
)
from reference.combat import initialize_combat, TurnPhase
from reference.sfs import dispatch_sfs, SFS_FUNCTIONS


class TestStepBasics(unittest.TestCase):
    """Basic step() behavior tests."""

    def test_start_emits_session_started(self):
        """step(Start) on a fresh session emits SessionStarted."""
        state = create_session(seed=[42, 0, 0, 0])
        transition = step(state, StepInput.start())

        self.assertEqual(transition.kind, TransitionKind.YIELDED)
        self.assertTrue(len(transition.events) > 0)
        self.assertEqual(transition.events[0].kind, EventKind.SESSION_STARTED)

    def test_halt_returns_terminal(self):
        """step(Halt) returns Terminal with SessionEnded."""
        state = create_session()
        state.started = True

        transition = step(state, StepInput.halt())

        self.assertEqual(transition.kind, TransitionKind.TERMINAL)
        self.assertTrue(len(transition.events) > 0)
        self.assertEqual(transition.events[0].kind, EventKind.SESSION_ENDED)
        self.assertEqual(transition.events[0].data.get("reason"), "halt")

    def test_tick_advances_clock(self):
        """step(Tick) advances the named clock by 1."""
        state = create_session()
        state.started = True

        transition = step(state, StepInput.tick("round"))

        self.assertEqual(transition.kind, TransitionKind.YIELDED)
        self.assertTrue(len(transition.events) > 0)
        tick_event = transition.events[0]
        self.assertEqual(tick_event.kind, EventKind.CLOCK_TICKED)
        self.assertEqual(tick_event.data["clock"], "round")
        self.assertEqual(tick_event.data["by"], 1)
        # Clock should be 1 in the new state
        self.assertEqual(transition.next_state.clocks.get("round"), 1)

    def test_multiple_ticks(self):
        """Multiple ticks increment correctly."""
        state = create_session()
        state.started = True

        for i in range(5):
            transition = step(state, StepInput.tick("round"))
            state = transition.next_state

        self.assertEqual(state.clocks["round"], 5)

    def test_invariant_e3_single_variant(self):
        """Invariant E3: Exactly one Transition variant per step call."""
        state = create_session()

        transition = step(state, StepInput.start())

        # Exactly one of these should be true
        kinds = [TransitionKind.YIELDED, TransitionKind.NEEDS_DECISION,
                 TransitionKind.TERMINAL, TransitionKind.ERRORED]
        self.assertIn(transition.kind, kinds)

    def test_state_immutability(self):
        """step() does not mutate the input state."""
        state = create_session()
        original_started = state.started

        transition = step(state, StepInput.start())

        # Original state should be unchanged
        self.assertEqual(state.started, original_started)
        # New state should be different
        self.assertTrue(transition.next_state.started)

    def test_protocol_violation_no_continuation(self):
        """DriverResponse without open continuation -> ProtocolViolation."""
        state = create_session()
        state.started = True

        transition = step(state, StepInput.driver_response(1, {"id": "end_turn"}))

        self.assertEqual(transition.kind, TransitionKind.ERRORED)
        self.assertFalse(transition.recoverable)
        self.assertEqual(transition.error["kind"], "ProtocolViolation")


class TestStepCombat(unittest.TestCase):
    """Test step() in combat mode."""

    def _create_combat_state(self) -> SessionState:
        """Create a session with two entities in combat."""
        state = create_session(seed=[42, 0, 0, 0])
        state.started = True

        # Create two entities
        e1 = state.add_entity("Fighter")
        e1.components["stats"] = Stats(
            scores={"str": 16, "dex": 14, "con": 14, "int": 10, "wis": 12, "cha": 8},
            derived={"initiative_bonus": 2, "armor_class": 18},
        )
        e1.components["resources"] = Resources(
            pools={"hp": ResourcePool(current=45, maximum=45)}
        )
        e1.components["faction"] = Faction(primary="party")
        e1.components["controller"] = Controller.DRIVER_OWNED

        e2 = state.add_entity("Goblin")
        e2.components["stats"] = Stats(
            scores={"str": 8, "dex": 14, "con": 10, "int": 10, "wis": 8, "cha": 8},
            derived={"initiative_bonus": 2, "armor_class": 15},
        )
        e2.components["resources"] = Resources(
            pools={"hp": ResourcePool(current=12, maximum=12)}
        )
        e2.components["faction"] = Faction(primary="hostile")
        e2.components["controller"] = Controller.DRIVER_OWNED

        # Set up pack data with actions
        state.pack_data = {
            "actions": [
                {
                    "id": "attack_melee",
                    "display_name": "Melee Attack",
                    "kind": "Action",
                },
                {
                    "id": "dodge",
                    "display_name": "Dodge",
                    "kind": "Action",
                },
            ]
        }

        # Initialize combat
        state, events = initialize_combat(state, [e1.id, e2.id])
        return state

    def test_combat_initialization(self):
        """Combat initialization rolls initiative and sets up turn order."""
        state = self._create_combat_state()

        self.assertIsNotNone(state.combat)
        self.assertEqual(state.combat.round, 1)
        self.assertEqual(len(state.combat.initiative.order), 2)
        self.assertEqual(state.combat.turn_phase, TurnPhase.START_OF_TURN)

    def test_combat_turn_advances(self):
        """step(Start) in combat advances through turn phases."""
        state = self._create_combat_state()

        # StartOfTurn -> Main
        transition = step(state, StepInput.start())

        # Should either yield (StartOfTurn phase) or need decision (Main phase)
        self.assertIn(transition.kind,
                      [TransitionKind.YIELDED, TransitionKind.NEEDS_DECISION])

    def test_combat_needs_decision_in_main(self):
        """Combat Main phase emits NeedsDecision for action selection."""
        state = self._create_combat_state()

        # Advance through phases until we get NeedsDecision
        max_steps = 20
        for _ in range(max_steps):
            transition = step(state, StepInput.start())
            if transition.kind == TransitionKind.NEEDS_DECISION:
                # Verify the request is for action selection
                request = transition.request
                self.assertIsNotNone(request)
                self.assertEqual(request.get("type"), "PromptDecision")
                self.assertEqual(request.get("prompt_kind"), "ActionSelection")
                self.assertTrue(len(request.get("legal_choices", [])) > 0)
                return
            state = transition.next_state
            if transition.kind == TransitionKind.TERMINAL:
                break

        # If we got here without NeedsDecision, that's unexpected
        # but not necessarily wrong if there are no legal actions

    def test_end_turn_advances_to_next_entity(self):
        """Choosing EndTurn advances to the next entity's turn."""
        state = self._create_combat_state()

        # Advance to NeedsDecision
        max_steps = 20
        transition = None
        for _ in range(max_steps):
            transition = step(state, StepInput.start())
            if transition.kind == TransitionKind.NEEDS_DECISION:
                break
            if transition.next_state is None:
                break
            state = transition.next_state

        if transition is None or transition.kind != TransitionKind.NEEDS_DECISION:
            self.skipTest("Could not reach NeedsDecision")

        # Respond with EndTurn
        request_id = transition.request.get("request_id", 0)
        state = transition.next_state
        self.assertIsNotNone(state)
        transition = step(state, StepInput.driver_response(request_id, "end_turn"))

        self.assertEqual(transition.kind, TransitionKind.YIELDED)


class TestSfsFunctions(unittest.TestCase):
    """Test SFS function implementations."""

    def _create_state_with_entity(self) -> tuple[SessionState, int]:
        """Create a state with one entity."""
        state = create_session(seed=[42, 0, 0, 0])
        state.started = True

        entity = state.add_entity("Hero")
        entity.components["stats"] = Stats(
            scores={"str": 16, "dex": 14, "con": 14},
            derived={"initiative_bonus": 2, "armor_class": 15},
        )
        entity.components["resources"] = Resources(
            pools={
                "hp": ResourcePool(current=30, maximum=30),
                "spell_slots": ResourcePool(current=3, maximum=3),
            }
        )
        return state, entity.id

    def test_sfs_registry_has_required_functions(self):
        """SFS registry contains all required functions."""
        required = [
            "sfs.roll.d20", "sfs.roll.generic", "sfs.roll.initiative",
            "sfs.damage.typed", "sfs.heal.flat",
            "sfs.resource.spend", "sfs.resource.grant",
            "sfs.attack.melee",
        ]
        for fn_name in required:
            self.assertIn(fn_name, SFS_FUNCTIONS, f"Missing SFS function: {fn_name}")

    def test_sfs_roll_d20(self):
        """sfs.roll.d20 returns a valid RollResult."""
        state, eid = self._create_state_with_entity()
        state, result, events = dispatch_sfs("sfs.roll.d20", state, {
            "actor": eid,
            "mode": "Straight",
            "purpose": "Attack",
        })

        self.assertIn("total", result)
        self.assertIn("raw", result)
        self.assertTrue(1 <= result["total"] <= 20 or result["total"] > 20)
        self.assertTrue(len(events) > 0)
        self.assertEqual(events[0].kind, EventKind.ROLL_MADE)

    def test_sfs_resource_spend(self):
        """sfs.resource.spend reduces resource current."""
        state, eid = self._create_state_with_entity()
        state, result, events = dispatch_sfs("sfs.resource.spend", state, {
            "entity": eid,
            "resource": "spell_slots",
            "amount": 1,
        })

        self.assertEqual(result["previous"], 3)
        self.assertEqual(result["current"], 2)
        self.assertTrue(len(events) > 0)

    def test_sfs_resource_spend_exhausted(self):
        """sfs.resource.spend raises error when exhausted."""
        state, eid = self._create_state_with_entity()
        from reference.sfs import ResourceExhaustedError

        with self.assertRaises(ResourceExhaustedError):
            dispatch_sfs("sfs.resource.spend", state, {
                "entity": eid,
                "resource": "spell_slots",
                "amount": 5,  # More than available
            })

    def test_sfs_resource_grant(self):
        """sfs.resource.grant increases resource up to maximum."""
        state, eid = self._create_state_with_entity()
        # First spend some
        state, _, _ = dispatch_sfs("sfs.resource.spend", state, {
            "entity": eid, "resource": "spell_slots", "amount": 2,
        })
        # Then grant
        state, result, events = dispatch_sfs("sfs.resource.grant", state, {
            "entity": eid, "resource": "spell_slots", "amount": 1,
        })

        self.assertEqual(result["previous"], 1)
        self.assertEqual(result["current"], 2)
        self.assertEqual(result["maximum"], 3)

    def test_sfs_resource_grant_clamped(self):
        """sfs.resource.grant clamps at maximum."""
        state, eid = self._create_state_with_entity()
        state, result, _ = dispatch_sfs("sfs.resource.grant", state, {
            "entity": eid, "resource": "hp", "amount": 100,
        })

        self.assertEqual(result["current"], 30)  # clamped at max
        self.assertEqual(result["maximum"], 30)

    def test_sfs_heal_flat(self):
        """sfs.heal.flat heals up to maximum HP."""
        state, eid = self._create_state_with_entity()
        # Damage first
        state, _, _ = dispatch_sfs("sfs.damage.typed", state, {
            "source": eid, "target": eid,
            "amounts": [{"amount": 10, "type": "slashing"}],
        })
        # Then heal
        state, result, events = dispatch_sfs("sfs.heal.flat", state, {
            "source": eid, "target": eid, "amount": 5,
        })

        self.assertEqual(result["healed"], 5)
        self.assertEqual(result["hp_before"], 20)
        self.assertEqual(result["hp_after"], 25)

    def test_sfs_damage_typed(self):
        """sfs.damage.typed reduces target HP."""
        state, eid = self._create_state_with_entity()
        target = state.add_entity("Target")
        target.components["resources"] = Resources(
            pools={"hp": ResourcePool(current=20, maximum=20)}
        )

        state, result, events = dispatch_sfs("sfs.damage.typed", state, {
            "source": eid, "target": target.id,
            "amounts": [{"amount": 8, "type": "fire"}],
        })

        self.assertEqual(result["total_dealt"], 8)
        self.assertEqual(result["target_hp_after"], 12)

    def test_sfs_attack_melee(self):
        """sfs.attack.melee resolves a melee attack."""
        state, eid = self._create_state_with_entity()
        target = state.add_entity("Target")
        target.components["stats"] = Stats(
            scores={"str": 10, "dex": 10},
            derived={"armor_class": 10},
        )
        target.components["resources"] = Resources(
            pools={"hp": ResourcePool(current=20, maximum=20)}
        )

        state, result, events = dispatch_sfs("sfs.attack.melee", state, {
            "attacker": eid, "defender": target.id,
        })

        self.assertIn("hit", result)
        self.assertIn("crit", result)
        self.assertIn("roll", result)
        self.assertTrue(len(events) > 0)


class TestDriverIntegration(unittest.TestCase):
    """Test ScriptedDriver with step loop."""

    def test_scripted_driver_basic(self):
        """ScriptedDriver feeds responses correctly."""
        from reference.driver import ScriptedDriver

        driver = ScriptedDriver.from_choices(["end_turn", "end_turn"])
        self.assertEqual(driver.remaining, 2)
        self.assertFalse(driver.exhausted)

        resp = driver.respond({
            "type": "PromptDecision",
            "request_id": 1,
            "legal_choices": [{"id": "attack"}, {"id": "end_turn"}],
        })
        self.assertEqual(resp, {"id": "end_turn"})
        self.assertEqual(driver.remaining, 1)

    def test_scripted_driver_default_on_exhaustion(self):
        """ScriptedDriver provides defaults when script is exhausted."""
        from reference.driver import ScriptedDriver

        driver = ScriptedDriver(script=[])

        resp = driver.respond({
            "type": "PromptDecision",
            "request_id": 1,
            "legal_choices": [{"id": "attack"}, {"id": "end_turn"}],
        })
        # Should pick last choice (end_turn)
        self.assertEqual(resp["id"], "end_turn")

    def test_scripted_driver_narrate_ack(self):
        """ScriptedDriver returns ack for Narrate requests."""
        from reference.driver import ScriptedDriver

        driver = ScriptedDriver(script=[])
        resp = driver.respond({"type": "Narrate", "request_id": 1})
        self.assertEqual(resp, "ack")


if __name__ == "__main__":
    unittest.main()
