# 11 - Conformance Suite

**Uses**: `00`-`10`.

An implementation of the Engine is **conformant** if and only if, for every
scenario in this suite, running the Engine against the scenario's
`(initial_state, seed, driver_script)` produces an Event Log whose SHA-256
equals the scenario's `expected_log_sha256`, at the declared
`(pack_schema_version, sfs_version)` pair.

The suite is the spec's executable form. If a change to an earlier file
would alter a golden hash without a deliberate version bump, either the
change is wrong or the hash needs to be regenerated and documented.

## 1. Layout

```
conformance/
  suite.json                   # index of scenarios with metadata and hashes
  packs/
    srd-5e-lite/               # the sample pack (d20-shaped)
    trivia/                    # minimal "empty" pack for skeleton tests
  scenarios/
    S01_damage_resistance/
      description.md
      initial_state.json        # canonical SessionState
      driver_script.json        # ordered DriverResponse records
      tick_schedule.json        # ordered Tick inputs
      expected_log.ndjson       # the golden log (for debugging)
      expected_log.sha256       # the single authoritative value
    S02_advantage_canceled/
    S03_opportunity_attack/
    S04_counterspell/
    S05_concentration_broken/
    S06_short_rest_hit_dice/
    S07_level_up_fighter_4/
    S08_social_check_with_item_bonus/
    S09_exploration_to_combat_mid_move/
    S10_pack_load_missing_sfs/
    S11_replay_equivalence/
    S12_invalid_driver_choice_recovered/
    S13_persistent_effect_expiry_on_tick/
    S14_auras_stacking/
    S15_temporary_hp_absorbtion/
    S16_massive_damage_instant_death/
    S17_area_effect_save_half/
    S18_summon_inserted_into_initiative/
    S19_long_rest_full_recovery/
    S20_scene_transition_during_rest/
```

## 2. Scenario Format

```
Scenario = {
    id: String,                       // e.g. "S01_damage_resistance"
    description: String,              // human-readable, one sentence
    pack_ids: [PackId],               // packs to load
    seed: [u64; 4],
    initial_state: SessionState,      // canonical JSON
    driver_script: [DriverResponseRecord],
    tick_schedule: [TickRecord],
    expected_log_sha256: HexString,
    expected_pack_schema_version: SemVer,
    expected_sfs_version: SemVer,
    max_steps: u32,                   // bound to prevent runaway tests
}
DriverResponseRecord = { ordinal: u32, response: DriverResponse }
TickRecord = { ordinal: u32, clock: ClockTag }
```

- `ordinal` references the driver-request / tick ordinal produced by the
  Engine. For drivers, `ordinal` matches the Nth `DriverRequestEmitted`
  event. For ticks, `ordinal` marks the `step()` call at which the Host
  injects the tick instead of `Start`.

## 3. Execution Protocol

```
run_scenario(scenario):
    load packs from packs/
    engine = Engine::new(supported_versions)
    state = engine.load_initial_state(scenario.initial_state)
    state.rng = seed_rng(scenario.seed)
    log = EventLog::empty()
    driver = ScriptedDriver::from(scenario.driver_script)
    tick = TickCursor::from(scenario.tick_schedule)
    step_count = 0
    input = Start
    loop:
        if step_count > scenario.max_steps: fail("max_steps exceeded")
        if tick.next_at(step_count):
            input = Tick(tick.clock)
            tick.advance()
        transition = step(state, input)
        match transition:
            Yielded(events, next):
                log.append_all(events)
                state = next
                input = Start
            NeedsDecision(req, cont, next):
                log.append(DriverRequestEmitted { req.id, req })
                resp = driver.next_for(req)
                log.append(DriverResponseRecorded { req.id, resp })
                state = next.with_continuation(cont)
                input = DriverResponse(req.id, resp)
            Terminal(events):
                log.append_all(events)
                break
            Errored(err, recoverable, next):
                log.append(Error(err))
                state = next
                if !recoverable: break
                input = Start
        step_count += 1
    actual_hash = log_hash(log)
    assert actual_hash == scenario.expected_log_sha256
```

## 4. Goldens (descriptions and pseudocode traces)

Each scenario below is listed with its purpose, the core behavior it
validates, and an abbreviated trace. Full `expected_log.ndjson` files ship
alongside.

### S01 - Damage Resistance
Validates: `Transformer` `ScaleNumeric(1,2)` applied to a damage effect
with matching type. A goblin takes 12 fire damage from a firebolt while
under a `resistance:fire` persistent effect. Expected log shows
`AttackResolved.hit=true`, `EffectPushed Damage(12 fire)`,
transformation to 6, `ResourceChanged hp -6`.

### S02 - Advantage Canceled by Disadvantage
Validates: `RE1` modifier collection + advantage/disadvantage cancellation.
A rogue rolls a Stealth check with `advantage` from cover and
`disadvantage` from a persistent "blessed target" effect. Log shows a
single straight d20 roll (two groups consumed for neither mode, cancel
resolved before the sfs.roll call).

### S03 - Opportunity Attack
Validates: reaction trigger from `OnActionDeclared(move)` pushing a
reaction effect onto the stack mid-movement, resolved before movement
completes. Log shows `ActionDeclared Move`, `ReactionOffered`,
`ReactionTaken`, damage on the mover, then the move completes.

### S04 - Counterspell
Validates: reaction interrupting a spell on the stack. A wizard casts
firebolt; an enemy wizard reacts with counterspell; the counterspell
effect reaches `Resolving` first and removes the firebolt frame from the
stack. Log shows both effects pushed, reactions resolved top-down,
firebolt removed, no damage.

### S05 - Concentration Broken
Validates: `P1` concentration uniqueness + break-on-damage. A cleric
concentrates on Bless; the cleric takes damage; concentration save is
triggered via a CON save; the save fails; Bless's persistent effect is
removed with `PersistentEffectRemoved` reason ConcentrationBroken.

### S06 - Short Rest with Hit Dice
Validates: pack-driven prompt loop for hit-die spending. A fighter short
rests; the Driver spends 2 hit dice; recovery events show two
`sfs.roll.hit_die` calls and matching `sfs.heal.dice` applications, then
a `RestCompleted` record.

### S07 - Level Up Fighter to 4
Validates: pack-scripted `LevelUpFlow`. Fighter reaches level 4 and walks
through ability-score-increase vs feat choice, HP increase, feature
grants. Log shows `LevelUpStarted`, a `LevelUpStep` for each step's
choice, `LevelUpCompleted`.

### S08 - Social Check with Item Bonus
Validates: modifier collection including equipped-item bonuses. A bard
with an Instrument of Charisma attempts Persuasion; modifiers include
proficiency, stat, item bonus, and an active bardic inspiration; log
shows the sum exactly.

### S09 - Exploration to Combat Mid-Move
Validates: `change_mode` during an exploration action. The party moves
into a zone with a hidden ambusher; the Engine fires a trigger that
transitions mode to Combat, rolls initiative, and interrupts the
remaining movement. Log shows ambush trigger, `ModeChanged`, initiative
rolls, new turn start.

### S10 - Pack Load Failure: Missing SFS
Validates: fatal error on unregistered SFS function. Load a pack whose
manifest requires `sfs.not.real`; assert the engine halts with
`FatalError::UnknownSfsFunction` and the log contains a single `Error`
record.

### S11 - Replay Equivalence
Validates: `PS5`. Take scenario S03's produced log; run replay against
the same initial state; assert the produced log's SHA-256 matches the
original exactly.

### S12 - Invalid Driver Choice Recovered
Validates: recoverable error path. Driver returns a choice id not in
`legal_choices`; Engine emits `Error(IllegalChoice)`; Engine re-prompts;
Driver returns a valid choice; scenario continues. Log records the full
exchange.

### S13 - Persistent Effect Expiry on Tick
Validates: expiry predicate `TimeElapsed { clock: "round", amount: 10 }`.
A 1-minute-duration buff (10 rounds) expires exactly on the tenth
`ClockTicked(round)` record.

### S14 - Aura Stacking
Validates: two overlapping auras granting modifiers; `RE1` sums without
deduplication unless triggers explicitly prevent stacking. The sample
pack defines one "paladin aura" with an anti-stack trigger; the scenario
shows a second paladin entering the zone and the anti-stack trigger
removing the earlier aura's effect.

### S15 - Temporary HP Absorption
Validates: `TempHP` transformer redirecting damage before HP reduction.
An entity with 5 temp HP takes 8 damage; log shows temp HP emptied
first, then 3 HP reduction.

### S16 - Massive Damage Instant Death
Validates: pack's death rules. An entity at full HP takes damage
exceeding max HP by a margin; pack rule triggers instant death;
`PersistentEffectApplied Dead`.

### S17 - Area Effect with Save for Half
Validates: AOE targeting all in a zone, each independently saves, those
who succeed get damage scaled by `ScaleNumeric(1,2)`, those who fail get
full. Log shows one `EffectPushed`, multiple save rolls,
per-target transformer differences.

### S18 - Summon Inserted Into Initiative
Validates: `InsertionRule::AtBottom`. A wizard casts Summon; the summoned
entity is inserted at the bottom of initiative; log shows
`EntityCreated` and the initiative order update.

### S19 - Long Rest Full Recovery
Validates: `PR3` rest atomicity. Long rest recovers HP, spell slots, and
removes certain persistent effects; log shows all recovery events in one
`Yielded` batch plus `RestCompleted`.

### S20 - Scene Transition During Rest
Validates: clean interaction between `can_save` and rest. A rest is
initiated; a `SceneTransition` is scheduled; the Engine completes the
rest first, then transitions. Log order shows rest events before
scene-exit events.

## 5. Authoring Discipline

- Golden hashes are generated by a reference implementation (to be
  produced outside this cycle) and pinned in `suite.json`. If no reference
  exists yet, hashes are marked `TBD` and filled in by the first
  conformant implementation, which then doubles as the reference for
  subsequent implementations.
- Every scenario's `description.md` explains WHICH invariant it exercises.
  Scenarios with no explicit invariant reference are not merged.
- Scenarios are additive across minor spec versions. Removing or
  renumbering a scenario requires a major version bump.
- Every invariant in files `01`-`09` MUST be exercised by at least one
  scenario. A coverage table lives at the top of `suite.json`:
  `invariant_coverage: { "E1": ["S11"], "E5": ["S11"], "S2": ["..."], ... }`
  and is enforced by a linter.

## 6. Linters

Two linters are specified (their implementation is out of scope for this
spec cycle but their behavior is normative):

### 6.1 Pack linter
- Validates every pack in `packs/` against `pack-v1.schema.json`.
- Verifies all `requires_sfs_functions` entries exist in SFS v1.
- Verifies no dangling ids.
- Verifies `checksum` matches.

### 6.2 Suite linter
- For each scenario, verifies the schema + sfs versions declared are in
  range.
- For each scenario, verifies `max_steps > 0`.
- Enforces `invariant_coverage` completeness over invariants E1-E7,
  D1-D4, S1-S4, R1-R2, T1-T2, P1, L1, RE1-RE3, SC1-SC3, C1-C4, PR1-PR3,
  PS1-PS6.
- Reports any invariant with no scenario coverage as a lint failure.

## 7. Implementability Gate

Before declaring this spec locked at 1.0.0:

1. An independent reviewer (human or separately-scoped LLM agent) is
   handed this repository and nothing else.
2. The reviewer is asked to implement the smallest conformant Engine in
   a language of their choice that passes scenarios S01, S03, S05, S11,
   S12, and S17. (Chosen because they exercise the four hard problems
   and replay.)
3. Every ambiguity the reviewer reports is a spec bug. Each bug is
   resolved by backfilling the offending file (including this one if the
   scenario is wrong) and re-running the gate with the same scenario
   set.
4. The gate closes when a single reviewer can produce a passing
   implementation without asking any clarifying questions.

Until the gate closes, the spec is at version `1.0.0-draft.N` where N
increments per gate iteration. Only after the gate closes is the spec
retagged `1.0.0`.
