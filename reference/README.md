# D&D Simulator Reference Implementation

A minimal reference implementation of the D&D simulator engine as specified in the `specs/` directory. This is intended as a guide for spec implementers, not a production engine.

## What this covers

This implementation covers the following spec areas:

| Spec file | Coverage |
|---|---|
| `01-execution-model.md` | `step()` function, all four Transition variants, StepInput dispatch, error model basics |
| `02-domain-model.md` | Entity, Stats, Resources, RollSpec, RollResult, DieKind, Events, canonical JSON |
| `03-effects-and-stack.md` | EffectStack structure, phase progression (Queued/AwaitingReactions/Resolving/Resolved) |
| `04-content-packs-and-sfs.md` | Pack loading, manifest validation, SFS function registry |
| `05-rules-engine.md` | `resolve_check`, `resolve_attack`, `gather_modifiers`, roll resolution per D2 |
| `07-combat-mode.md` | CombatState, initiative rolling, turn flow (all four phases), action selection loop |

### SFS v1 functions implemented

- `sfs.roll.d20` -- Roll a d20 with mode and modifier
- `sfs.roll.generic` -- Roll any RollSpec
- `sfs.roll.initiative` -- Initiative roll for an entity
- `sfs.damage.typed` -- Apply typed damage to a target
- `sfs.heal.flat` -- Heal a flat amount
- `sfs.resource.spend` -- Spend a resource
- `sfs.resource.grant` -- Increase a resource
- `sfs.attack.melee` -- Resolve a melee attack

### What is NOT covered

- Scenes and zones (`06-scenes-and-zones.md`)
- Level-up and progression (`08-progression.md`)
- Full persistence and replay (`09-persistence-and-replay.md`)
- Conformance test suite (`10-conformance.md`)
- Full trigger/reaction scanning (skeleton only)
- Transformers (resistance, vulnerability, immunity)
- Concentration tracking
- Opportunity attacks
- Multi-pack loading with `extends`
- Full cross-reference validation at load time
- Most SFS functions (36 of 44 are stubs)

## File structure

```
reference/
  engine.py        # Core engine: step(), Transition, SessionState
  domain.py        # Data types: Entity, Component, RollSpec, RollResult, Event
  effects.py       # EffectStack, resolution loop phases
  rules.py         # resolve_check, resolve_attack, gather_modifiers, roll resolution
  combat.py        # CombatState, initiative, turn flow
  sfs.py           # SFS v1 function implementations (8 of 44)
  pack_loader.py   # Load and validate pack JSON
  driver.py        # ScriptedDriver for deterministic replay
  rng.py           # Seeded RNG (xoshiro256**) with D2 consumption schedule
  canonical.py     # Canonical JSON serialization for event log hashing
  run_scenario.py  # CLI entry point
  tests/
    test_rng.py        # RNG determinism and D2 schedule tests
    test_canonical.py  # Canonical JSON output tests
    test_step.py       # step() behavior, SFS functions, combat, driver tests
```

## Requirements

- Python 3.10+ (uses `match` statements and `X | Y` type unions)
- No external dependencies

## How to run

### Run tests

```bash
# From the repository root:
python -m pytest reference/tests/ -v

# Or with unittest:
python -m unittest discover reference/tests/ -v
```

### Run a scenario

```bash
# Basic run with default settings (auto-ends):
python -m reference.run_scenario -v

# With a pack file:
python -m reference.run_scenario --pack path/to/pack.json -v

# With a pack and enter combat:
python -m reference.run_scenario --pack path/to/pack.json --combat -v

# With a driver script:
python -m reference.run_scenario --pack path/to/pack.json \
    --driver-script path/to/driver_script.json -v

# Custom seed:
python -m reference.run_scenario --seed 1 2 3 4 -v

# Output event log to file:
python -m reference.run_scenario --pack path/to/pack.json -o events.json
```

### Pack format

A minimal pack JSON:

```json
{
  "manifest": {
    "id": "my-pack",
    "version": "1.0.0",
    "pack_schema_version": "1.0.0",
    "sfs_version": "1.0.0",
    "display_name": "My Pack",
    "description": "A test pack",
    "authors": ["Author"],
    "license": "MIT"
  },
  "entities": [
    {
      "name": "Fighter",
      "stats": {
        "scores": {"str": 16, "dex": 14, "con": 14, "int": 10, "wis": 12, "cha": 8},
        "derived": {"initiative_bonus": 2, "armor_class": 18}
      },
      "resources": {
        "hp": {"current": 45, "maximum": 45}
      },
      "faction": {"primary": "party"},
      "controller": "DriverOwned"
    }
  ],
  "actions": [
    {
      "id": "attack_melee",
      "display_name": "Melee Attack",
      "kind": "Action"
    }
  ]
}
```

### Driver script format

```json
[
  {"request_type": "PromptDecision", "response": {"id": "attack_melee"}},
  {"request_type": "PromptDecision", "response": {"id": "end_turn"}}
]
```

## Key design decisions

1. **Purity**: The engine is a pure function. `step(state, input) -> Transition`. No I/O, no threads, no clock reads. See spec 01, Invariant E1.

2. **Immutability**: `step` deep-copies state internally. The caller's state is never mutated. New state is returned inside the Transition.

3. **RNG discipline**: Uses xoshiro256** with the exact consumption schedule from Invariant D2. Die mapping: `value = (u64_raw % n) + 1`. No rejection sampling.

4. **Canonical JSON**: Event log records use sorted keys, no whitespace, minimal escaping. Two conformant engines must produce byte-identical output per Invariant D4.

5. **No DSL**: Packs carry data, not code. All behavior is via SFS function references. See spec 04, Section 1.
