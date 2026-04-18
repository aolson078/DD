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
from reference.combat import initialize_combat
from reference.canonical import canonical_json


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

    # Create session
    state = create_session(seed=seed)

    # Load pack
    if args.pack:
        pack_path = Path(args.pack)
        if not pack_path.exists():
            print(f"Error: Pack not found: {pack_path}", file=sys.stderr)
            sys.exit(1)
        _, state = load_pack(pack_path, state)
        if args.verbose:
            print(f"Loaded pack: {state.pack_ids}")

    # Load scenario
    if args.scenario:
        scenario_path = Path(args.scenario)
        if scenario_path.exists():
            # Load scenario entities, scene, etc.
            scenario_file = scenario_path / "scenario.json"
            if scenario_file.exists():
                with open(scenario_file, "r") as f:
                    scenario_data = json.load(f)
                # Merge scenario entities into state
                if "entities" in scenario_data:
                    if state.pack_data is None:
                        state.pack_data = {}
                    state.pack_data["entities"] = scenario_data["entities"]
                    from reference.pack_loader import _install_pack_entities
                    _install_pack_entities(state.pack_data, state)

    # Load driver
    if args.driver_script:
        driver = ScriptedDriver.from_file(args.driver_script)
    else:
        # Default driver: always end turn
        driver = ScriptedDriver.from_choices(["end_turn"] * 100)

    # Enter combat if requested
    if args.combat and state.entities:
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
