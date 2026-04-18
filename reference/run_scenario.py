#!/usr/bin/env python3
"""
CLI entry point: load pack + scenario, run to completion, print events.

Usage:
    python -m reference.run_scenario --pack path/to/pack.json \\
        --scenario path/to/scenario/ \\
        --seed 42

Or with a driver script:
    python -m reference.run_scenario --pack path/to/pack.json \\
        --driver-script path/to/driver_script.json

See specs/01-execution-model.md Section 7 for the reference host loop.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

# Ensure the parent of `reference/` is on sys.path so that
# `from reference.X import Y` works when invoked as a script.
_REFERENCE_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _REFERENCE_DIR.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from reference.engine import (
    SessionState, StepInput, Transition, TransitionKind,
    create_session, step,
)
from reference.driver import ScriptedDriver
from reference.pack_loader import load_pack
from reference.combat import initialize_combat, CombatState, InitiativeOrder, Tiebreaker, ActionEconomy, TurnPhase
from reference.canonical import canonical_json
from reference.domain import (
    Entity, Stats, Resources, ResourcePool, Position, Faction,
    Controller, PlayerController,
)
from reference.rng import seed_rng


def run_scenario(
    state: SessionState,
    driver: ScriptedDriver,
    max_steps: int = 1000,
    verbose: bool = False,
) -> list[dict]:
    """Run the reference host loop to completion.

    See spec 01 Section 7 (normative pseudocode).

    Returns the complete event log as a list of canonical dicts.
    """
    event_log: list[dict] = []
    input = StepInput.start()

    for step_num in range(max_steps):
        transition = step(state, input)

        match transition.kind:
            case TransitionKind.YIELDED:
                for event in transition.events:
                    record = event.to_dict()
                    event_log.append(record)
                    if verbose:
                        print(f"[step {step_num}] {event.kind.value}: "
                              f"{canonical_json(record)}")
                state = transition.next_state  # type: ignore[assignment]
                input = StepInput.start()

            case TransitionKind.NEEDS_DECISION:
                request = transition.request or {}
                if verbose:
                    print(f"[step {step_num}] NeedsDecision: "
                          f"{request.get('type', '?')} "
                          f"(id={request.get('request_id', '?')})")

                # If driver script is exhausted, halt instead of looping
                if driver.exhausted:
                    state = transition.next_state  # type: ignore[assignment]
                    input = StepInput.halt()
                    continue

                # Feed response from driver
                response = driver.respond(request)
                request_id = request.get("request_id", 0)

                if verbose:
                    print(f"  -> Driver response: {response}")

                state = transition.next_state  # type: ignore[assignment]
                input = StepInput.driver_response(request_id, response)

            case TransitionKind.TERMINAL:
                for event in transition.events:
                    record = event.to_dict()
                    event_log.append(record)
                    if verbose:
                        print(f"[step {step_num}] TERMINAL: "
                              f"{event.kind.value}: {canonical_json(record)}")
                if verbose:
                    print(f"\nSession ended after {step_num + 1} steps, "
                          f"{len(event_log)} events.")
                return event_log

            case TransitionKind.ERRORED:
                error = transition.error or {}
                if verbose:
                    print(f"[step {step_num}] ERROR: {error}")
                event_log.append({
                    "error": error,
                    "kind": "Error",
                    "recoverable": transition.recoverable,
                })
                if not transition.recoverable:
                    return event_log
                state = transition.next_state  # type: ignore[assignment]
                input = StepInput.start()

    if verbose:
        print(f"\nMax steps ({max_steps}) reached. {len(event_log)} events.")
    return event_log


# ---------------------------------------------------------------------------
# Scenario loading helpers
# ---------------------------------------------------------------------------

def _parse_entity_from_json(eid: int, edata: dict) -> Entity:
    """Parse an entity from scenario initial_state.json format."""
    name = edata.get("name", "Unknown")
    components: dict[str, Any] = {}

    comp_data = edata.get("components", {})

    # Stats
    if "stats" in comp_data:
        sd = comp_data["stats"]
        components["stats"] = Stats(
            scores=sd.get("scores", {}),
            proficiencies=sd.get("proficiencies", []),
            derived=sd.get("derived", {}),
        )

    # Resources -- the scenario format uses {"hp": {current, maximum, ...}}
    if "resources" in comp_data:
        rd = comp_data["resources"]
        resources = Resources()
        for res_id, res_val in rd.items():
            if isinstance(res_val, dict):
                resources.pools[res_id] = ResourcePool(
                    current=res_val.get("current", res_val.get("maximum", 0)),
                    maximum=res_val.get("maximum", 0),
                    recovery=str(res_val.get("recovery", "Manual")),
                    tags=res_val.get("tags", []),
                )
        components["resources"] = resources

    # Position
    if "position" in comp_data:
        pd = comp_data["position"]
        offset = None
        if pd.get("offset") and isinstance(pd["offset"], (list, tuple)):
            offset = tuple(pd["offset"][:2])
        components["position"] = Position(
            scene_id=pd.get("scene_id", ""),
            zone_id=pd.get("zone_id", ""),
            offset=offset,
            facing=pd.get("facing"),
        )

    # Controller
    if "controller" in comp_data:
        ctrl = comp_data["controller"]
        if ctrl == "DriverOwned" or (isinstance(ctrl, dict) and ctrl.get("type") == "DriverOwned"):
            components["controller"] = Controller.DRIVER_OWNED
        elif isinstance(ctrl, dict) and ctrl.get("type") == "Player":
            components["controller"] = PlayerController(ctrl.get("player_id", ""))

    # Faction
    if "faction" in comp_data:
        fd = comp_data["faction"]
        components["faction"] = Faction(
            primary=fd.get("primary", ""),
            disposition=fd.get("disposition", {}),
        )

    # persistent_effects -- store raw for now
    if "persistent_effects" in comp_data:
        components["persistent_effects"] = comp_data["persistent_effects"]

    return Entity(id=eid, name=name, components=components)


def load_initial_state(path: Path, pack_data: dict | None = None) -> SessionState:
    """Load a scenario initial_state.json into a SessionState.

    The initial_state.json defines entities, scenes, clocks, rng seed, etc.
    This function parses that into the engine's SessionState.
    """
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    # Create base session with seed from initial state
    rng_data = data.get("rng", {})
    seed = rng_data.get("seed", [42, 0, 0, 0])
    state = create_session(seed=seed)

    # Set versions
    state.schema_version = data.get("schema_version", "1.0.0")
    state.sfs_version = data.get("sfs_version", "1.0.0")
    state.pack_ids = data.get("pack_ids", [])
    state.rng_policy = data.get("rng_policy", "EngineOnly")

    # Counters
    state._next_entity_id = data.get("next_entity_id", 1)
    state._next_effect_id = data.get("next_effect_id", 1)
    state._next_request_id = data.get("next_request_id", 1)
    state._next_event_id = data.get("next_event_id", 1)

    # Clocks
    state.clocks = data.get("clocks", {})

    # Hashing
    state.event_log_hash_so_far = data.get("event_log_hash_so_far", "0" * 64)

    # Continuation
    state.open_continuation = data.get("open_continuation")
    state.recoverable_retry_count = data.get("recoverable_retry_count", 0)
    state.max_recoverable_retries = data.get("max_recoverable_retries", 3)

    # Scenes
    state.scenes = data.get("scenes", {})
    state.active_scene = data.get("active_scene", "")

    # Stack (simplified -- just note if non-empty)
    stack_data = data.get("stack", [])
    # For now, we leave stack empty (the scenario starts with empty stack)

    # Entities
    entities_data = data.get("entities", {})
    for eid_str, edata in entities_data.items():
        eid = int(eid_str)
        entity = _parse_entity_from_json(eid, edata)
        state.entities[eid] = entity

    # Pack data
    if pack_data is not None:
        state.pack_data = pack_data

    # Mark as started (the session is pre-initialized)
    state.started = True

    # Determine if we should be in combat mode
    # Check if the active scene is in combat mode
    active_scene_data = state.scenes.get(state.active_scene, {})
    if active_scene_data.get("mode") == "Combat" and state.entities:
        # Set up combat state directly without rolling initiative.
        # The scenario's initial_state defines a pre-configured combat
        # position. We use entity order by id (ascending) as initiative
        # order, preserving the RNG state for actual gameplay rolls.
        entity_ids = sorted(state.entities.keys())
        combat = CombatState()
        combat.round = state.clocks.get("round", 1)
        combat.initiative.order = entity_ids
        for eid in entity_ids:
            combat.initiative.scores[eid] = 0
            entity = state.entities.get(eid)
            dex_mod = 0
            if entity:
                stats = entity.get_stats()
                if stats:
                    dex_mod = (stats.scores.get("DEX", stats.scores.get("dex", 10)) - 10) // 2
            combat.initiative.tiebreakers[eid] = Tiebreaker(dex=dex_mod, entity_id=eid)
            combat.action_economy[eid] = ActionEconomy.fresh()
        combat.active_index = 0
        combat.turn_phase = TurnPhase.START_OF_TURN
        state.combat = combat
        state.mode = "Combat"

    return state


def _add_scenario_actions_to_pack(pack_data: dict) -> dict:
    """Ensure the pack has actions referenced by scenarios.

    The S01 scenario driver responds with 'cast_firebolt', which is a
    spell-based action. We synthesize it from the pack's spells and
    effect_templates so the engine can resolve it.
    """
    if pack_data is None:
        pack_data = {}

    actions = pack_data.get("actions", [])
    action_ids = {a["id"] for a in actions if isinstance(a, dict)}

    # Add cast_firebolt if fire_bolt spell exists but cast_firebolt action doesn't
    if "cast_firebolt" not in action_ids:
        spells = pack_data.get("spells", [])
        fire_bolt_spell = None
        for spell in spells:
            if spell.get("id") == "fire_bolt":
                fire_bolt_spell = spell
                break

        if fire_bolt_spell:
            actions.append({
                "id": "cast_firebolt",
                "display_name": "Fire Bolt",
                "kind": "Action",
                "prerequisites": [],
                "cost": [],
                "targeting": {
                    "kind": "SingleEntity",
                    "range": 120,
                    "must_be_hostile": True,
                },
                "effects": fire_bolt_spell.get("effects", []),
                "attack_type": fire_bolt_spell.get("attack_type", "ranged"),
                "spell_id": "fire_bolt",
                "tags": ["spell", "cantrip", "attack", "ranged"],
            })

    # Add attempt_stealth action for S02
    if "attempt_stealth" not in action_ids:
        actions.append({
            "id": "attempt_stealth",
            "display_name": "Attempt Stealth",
            "kind": "Action",
            "prerequisites": [],
            "cost": [],
            "targeting": {"kind": "Self"},
            "effects": [
                {
                    "sfs_function": "sfs.check.skill",
                    "args": {
                        "entity": "$active",
                        "skill": "stealth",
                        "dc": 14,
                    },
                }
            ],
            "tags": ["stealth", "check"],
        })

    # Add attack_shortsword action for S05
    if "attack_shortsword" not in action_ids:
        actions.append({
            "id": "attack_shortsword",
            "display_name": "Shortsword Attack",
            "kind": "Action",
            "prerequisites": [],
            "cost": [],
            "targeting": {
                "kind": "SingleEntity",
                "range": 5,
                "must_be_hostile": True,
            },
            "effects": ["melee_damage"],
            "attack_type": "melee",
            "tags": ["attack", "melee"],
        })

    # Add attack_scimitar action for S03
    if "attack_scimitar" not in action_ids:
        actions.append({
            "id": "attack_scimitar",
            "display_name": "Scimitar Attack",
            "kind": "Action",
            "prerequisites": [],
            "cost": [],
            "targeting": {
                "kind": "SingleEntity",
                "range": 5,
                "must_be_hostile": True,
            },
            "effects": ["melee_damage"],
            "attack_type": "melee",
            "tags": ["attack", "melee"],
        })

    pack_data["actions"] = actions
    return pack_data


def main() -> None:
    parser = argparse.ArgumentParser(
        description="D&D Simulator Reference Implementation - Scenario Runner"
    )
    parser.add_argument(
        "--pack", type=str, default=None,
        help="Path to pack JSON file or pack directory",
    )
    parser.add_argument(
        "--scenario", type=str, default=None,
        help="Path to scenario directory",
    )
    parser.add_argument(
        "--driver-script", type=str, default=None,
        help="Path to driver_script.json",
    )
    parser.add_argument(
        "--seed", type=int, nargs="+", default=[42, 0, 0, 0],
        help="RNG seed (1-4 u64 values, default: 42 0 0 0)",
    )
    parser.add_argument(
        "--max-steps", type=int, default=1000,
        help="Maximum number of step() calls (default: 1000)",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Print events as they are emitted",
    )
    parser.add_argument(
        "--output", "-o", type=str, default=None,
        help="Output file for event log JSON (default: stdout)",
    )
    parser.add_argument(
        "--combat", action="store_true",
        help="Auto-enter combat mode with all loaded entities",
    )
    args = parser.parse_args()

    # Pad seed to 4 values
    seed = (args.seed + [0, 0, 0, 0])[:4]

    # Load pack first (if provided)
    pack_data: dict | None = None
    if args.pack:
        pack_path = Path(args.pack)
        if not pack_path.exists():
            print(f"Error: Pack not found: {pack_path}", file=sys.stderr)
            sys.exit(1)
        # Load and validate pack, but we'll build state from scenario
        temp_state = create_session(seed=seed)
        pack_data, temp_state = load_pack(pack_path, temp_state)
        if args.verbose:
            print(f"Loaded pack: {temp_state.pack_ids}")

    # Load scenario
    if args.scenario:
        scenario_path = Path(args.scenario)
        initial_state_file = scenario_path / "initial_state.json"
        driver_script_file = scenario_path / "driver_script.json"

        if initial_state_file.exists():
            # Synthesize scenario-specific actions into pack data
            if pack_data is not None:
                pack_data = _add_scenario_actions_to_pack(pack_data)

            state = load_initial_state(initial_state_file, pack_data)

            if args.verbose:
                print(f"Loaded scenario from {scenario_path}")
                print(f"  Entities: {list(state.entities.keys())}")
                print(f"  Active scene: {state.active_scene}")
                if state.combat:
                    print(f"  Combat: round={state.combat.round}, "
                          f"order={state.combat.initiative.order}")
        else:
            # Fall back to creating a fresh session
            state = create_session(seed=seed)
            if pack_data is not None:
                state.pack_data = pack_data

        # Load driver script
        if driver_script_file.exists() and args.driver_script is None:
            args.driver_script = str(driver_script_file)
    else:
        # No scenario -- create fresh session
        state = create_session(seed=seed)
        if pack_data is not None:
            state.pack_data = pack_data

    # Load driver
    if args.driver_script:
        driver = ScriptedDriver.from_file(args.driver_script)
    else:
        # Default driver: always end turn
        driver = ScriptedDriver.from_choices(["end_turn"] * 100)

    # Enter combat if requested and not already in combat
    if args.combat and state.entities and state.combat is None:
        entity_ids = list(state.entities.keys())
        state, combat_events = initialize_combat(state, entity_ids)
        if args.verbose:
            for event in combat_events:
                print(f"[init] {event.kind.value}")

    # Run
    event_log = run_scenario(
        state, driver,
        max_steps=args.max_steps,
        verbose=args.verbose,
    )

    # Output
    output_json = canonical_json(event_log)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            # Pretty-print for readability
            json.dump(json.loads(output_json), f, indent=2)
        if args.verbose:
            print(f"\nEvent log written to {args.output}")
    else:
        if not args.verbose:
            print(output_json)


if __name__ == "__main__":
    main()
