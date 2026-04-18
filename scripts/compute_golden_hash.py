#!/usr/bin/env python3
"""
Compute the SHA-256 golden hash for a conformance scenario's event log.

Usage:
    python scripts/compute_golden_hash.py \
        --pack packs/srd-5e-lite/pack.json \
        --scenario conformance/scenarios/S01-damage-resistance/

The hash follows the log_hash algorithm from specs/09-persistence-and-replay.md
Section 3.2:
    h = SHA256()
    for each record:
        h.update(canonical_bytes(record))
        h.update(b"\\n")
    return h.hexdigest()

Each record is serialized using canonical JSON (sorted keys, no whitespace,
minimal escaping) per specs/09 Section 2 (Invariant PS1).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

# Ensure repo root is on sys.path
_SCRIPT_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _SCRIPT_DIR.parent
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


def compute_log_hash(event_log: list[dict]) -> str:
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


def run_and_hash(
    pack_path: Path,
    scenario_path: Path,
    max_steps: int = 1000,
    verbose: bool = False,
) -> tuple[str, list[dict]]:
    """Load pack + scenario, run engine to completion, return (hash, events)."""
    # Load pack
    state = create_session()
    pack_data, _ = load_pack(pack_path, state)
    pack_data = _add_scenario_actions_to_pack(pack_data)

    # Load scenario initial state
    initial_state_file = scenario_path / "initial_state.json"
    state = load_initial_state(initial_state_file, pack_data)

    # Load driver script
    driver_script_file = scenario_path / "driver_script.json"
    if driver_script_file.exists():
        driver = ScriptedDriver.from_file(driver_script_file)
    else:
        driver = ScriptedDriver.from_choices(["end_turn"] * 100)

    # Run
    event_log = run_scenario(state, driver, max_steps=max_steps, verbose=verbose)

    # Compute hash
    golden_hash = compute_log_hash(event_log)

    return golden_hash, event_log


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compute SHA-256 golden hash for a scenario event log"
    )
    parser.add_argument(
        "--pack", type=str, required=True,
        help="Path to pack JSON file",
    )
    parser.add_argument(
        "--scenario", type=str, required=True,
        help="Path to scenario directory",
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
        "--print-events", action="store_true",
        help="Print each canonical event record",
    )
    args = parser.parse_args()

    pack_path = Path(args.pack)
    scenario_path = Path(args.scenario)

    golden_hash, event_log = run_and_hash(
        pack_path, scenario_path,
        max_steps=args.max_steps,
        verbose=args.verbose,
    )

    if args.print_events:
        for record in event_log:
            print(canonical_json(record))
        print()

    print(f"{golden_hash}")


if __name__ == "__main__":
    main()
