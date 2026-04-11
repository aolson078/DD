# 04 - Content Packs and the Standard Function Set

**Introduces**: `Pack`, `PackManifest`, `PackSchemaVersion`, `SfsVersion`,
`SfsFunctionId`, `SfsFunctionSignature`, `SfsRegistry`, `PackValidationResult`,
and the SFS v1 enumeration.
**Uses**: `00`, `01`, `02`, `03`.

## 1. Why no DSL

Packs carry rules data but never executable code. This is non-negotiable.
Reasons:

- An embedded DSL is a language-design project of its own. Getting evaluation
  semantics, error paths, and sandboxing right is a multi-month undertaking
  that we are not going to do in this cycle.
- Executable packs are an attack surface. A curated content format is not.
- An LLM driving the Engine already needs a tool schema. The SFS is that
  schema. Inventing a second interface (a DSL) doubles the surface for no
  gain.
- Versioning is clean: packs declare which SFS they target; engines declare
  which SFS they implement; either they match or the pack is rejected at
  load.

The cost is that rich custom behavior requires an SFS version bump. This is
accepted. The SFS is intentionally large enough (about 40 functions in v1)
to cover d20-shaped gameplay end-to-end.

## 2. Pack Format

Packs are canonical JSON documents. Top-level structure:

```
Pack = {
    manifest: PackManifest,
    stats: [StatDef],
    resources: [ResourceDef],
    clocks: [ClockDef],
    rests: [RestDef],
    damage_types: [DamageTypeDef],
    conditions: [ConditionDef],
    items: [ItemTemplate],
    ability_scores: [AbilityScoreDef],     // proficiency tables, etc.
    classes: [ClassDef],
    races: [RaceDef],
    backgrounds: [BackgroundDef],
    feats: [FeatDef],
    spells: [SpellDef],
    bestiary: [MonsterDef],
    actions: [ActionDef],
    checks: [CheckDef],
    saves: [SaveDef],
    triggers: [TriggerDef],
    transformers: [TransformerDef],
    effect_templates: [EffectTemplateDef],
    persistent_effect_templates: [PersistentEffectTemplateDef],
    progression: ProgressionConfig,
    scenes: [SceneTemplate],               // starter content; optional
    tables: [RollTable],                   // for random generation
    l10n: OrderedMap<Locale, OrderedMap<String, String>>,   // optional
}
```

- Every `*Def` has a unique id within its category and a `display_name`.
- Unknown top-level keys are rejected at validation time.
- Locales are optional; engines not implementing localization ignore them.

### 2.1 PackManifest

```
PackManifest = {
    id: PackId,                            // stable kebab-case, e.g. "srd-5e-lite"
    version: SemVer,                       // pack content version
    pack_schema_version: SemVer,           // "1.0.0" for this spec
    sfs_version: SemVer,                   // e.g. "1.0.0"
    requires_sfs_functions: [SfsFunctionId],
    requires_component_types: [ComponentTypeId],
    requires_damage_types: [DamageType],
    display_name: String,
    description: String,
    authors: [String],
    license: String,
    origin: Option<String>,                // URL or free text
    checksum: Sha256,                      // of the canonical body minus manifest.checksum
    extends: [PackId],                     // optional dependency on other packs
}
```

- `pack_schema_version` is checked against the engine's
  `supported_schema_range`. Mismatch is `FatalError::SchemaVersionMismatch`.
- `sfs_version` is checked against the host's registered SFS. Minor-version
  backwards compat is allowed: a pack targeting 1.0 may run on engines
  supporting 1.1.
- `checksum` is verified before loading. Failure is
  `FatalError::PackValidationFailed{reason: "checksum"}`.

### 2.2 Load Sequence

```
load_pack(bytes, registry) -> PackValidationResult:
    json = canonical_parse(bytes)             // reject unknown keys
    body = json without manifest.checksum
    if sha256(canonicalize(body)) != json.manifest.checksum:
        fatal(PackValidationFailed { reason: "checksum mismatch" })
    check pack_schema_version in engine_supported_range
    check sfs_version in registry.supported_range
    for fn_id in manifest.requires_sfs_functions:
        if fn_id not in registry:
            fatal(UnknownSfsFunction { name: fn_id })
        check signature version
    validate cross-references (actions referencing nonexistent effect_templates, etc.)
    validate JsonFilter depth <= 16 everywhere
    validate no cycles in extends graph
    install pack into SessionState
    return Ok
```

### 2.3 Cross-reference validation

At load time the Engine walks every id reference and verifies the target
exists:

- `ActionDef.effects[]` -> `effect_templates[].id`
- `ActionDef.cost[]` -> `resources[].id`
- `SpellDef.effects[]` -> `effect_templates[].id`
- `ClassDef.features[].grants` -> `triggers[]`, `transformers[]`,
  `actions[]`, `persistent_effect_templates[]`
- `TriggerDef.effect_template` -> `effect_templates[].id`
- `EffectTemplateDef.body.SfsCall.function` -> registered SFS function

Any dangling reference is `FatalError::PackValidationFailed` with the
offending path.

## 3. Versioning

- **Pack schema version** is a semver attached to this spec. This file is
  `1.0.0`. Breaking changes (removing a field, changing a type) bump major.
  Additive changes (new optional field, new `*Def` category) bump minor.
  Editorial changes bump patch.
- **SFS version** is a separate semver attached to the SFS registry. SFS
  minor bumps add functions without removing existing ones. SFS major
  bumps remove or change function signatures.
- An engine declares `supported_schema_range = (min, max)` and
  `supported_sfs_range = (min, max)`. Typical engines support the current
  and previous major.
- A pack is portable across hosts iff `pack.manifest.requires_sfs_functions`
  is a subset of every target host's SFS registry at the declared version.

## 4. The SFS Registry

```
SfsRegistry = OrderedMap<SfsFunctionId, SfsFunctionEntry>
SfsFunctionEntry = {
    id: SfsFunctionId,              // e.g. "sfs.damage.typed"
    since: SemVer,                  // first version with this signature
    signature: SfsFunctionSignature,
    doc: String,
}
SfsFunctionSignature = {
    input: JsonSchema,              // the args shape
    output: JsonSchema,             // the return shape (may be Unit)
    effects: [EffectDescriptor],    // "may append Events of these kinds"
    purity: Purity,
}
Purity = Pure | Stateful          // Pure may be memoized by replay; Stateful may not
```

- The registry is the single source of truth for what a pack may reference.
- `effects` is a declared list of event kinds a function may produce. The
  Engine enforces this: if an SFS call produces an undeclared event kind,
  it is a `FatalError::InvariantViolation`.
- `purity` is a performance hint used by replay. Conformance is defined by
  event-log equivalence, not by purity.

## 5. SFS v1 Enumeration

Every function below is part of SFS v1.0.0. Signatures are written in
prose plus a compact JSON-schema-like shorthand. The full JSON schemas
ship alongside this file at `specs/sfs/v1/<function_id>.json` (see
Section 7).

### 5.1 Rolls and dice

| Id | Purpose |
|---|---|
| `sfs.roll.d20` | Roll a d20 with mode (straight/adv/dis) and modifier, return `RollResult`. |
| `sfs.roll.generic` | Roll any `RollSpec`, return `RollResult`. |
| `sfs.roll.initiative` | Initiative roll for an entity. |
| `sfs.roll.hit_die` | Spend one hit die during short rest. |
| `sfs.roll.table` | Sample a `RollTable` by id. |

### 5.2 Checks and saves

| Id | Purpose |
|---|---|
| `sfs.check.skill` | Skill check against a DC. |
| `sfs.check.contested` | Two entities roll; higher wins. |
| `sfs.save.ability` | Saving throw against a DC. |
| `sfs.save.concentration` | Concentration save on a source entity. |
| `sfs.save.death` | Death saving throw. |

### 5.3 Attacks and damage

| Id | Purpose |
|---|---|
| `sfs.attack.melee` | Resolve a melee attack (to-hit, crit, damage). |
| `sfs.attack.ranged` | Resolve a ranged attack. |
| `sfs.attack.spell` | Resolve a spell attack. |
| `sfs.damage.typed` | Apply typed damage to a target (runs transformers). |
| `sfs.damage.from_effect` | Apply damage encoded in an `EffectNode`. |
| `sfs.crit.check` | Evaluate whether a d20 roll is a crit (pack rule). |

### 5.4 Healing and resources

| Id | Purpose |
|---|---|
| `sfs.heal.flat` | Heal a flat amount. |
| `sfs.heal.dice` | Heal by rolling dice (potions, cures). |
| `sfs.temp_hp.grant` | Grant temporary HP (installs a persistent effect). |
| `sfs.resource.spend` | Spend a resource; recoverable error on exhaustion. |
| `sfs.resource.grant` | Increase a resource up to its maximum. |
| `sfs.resource.set_max` | Change a resource's maximum (e.g., level up HP). |

### 5.5 Conditions and persistent effects

| Id | Purpose |
|---|---|
| `sfs.condition.apply` | Apply a pack-defined condition. |
| `sfs.condition.remove` | Remove a condition by id. |
| `sfs.effect.apply_persistent` | Apply a `PersistentEffectTemplate`. |
| `sfs.effect.remove_persistent` | Remove persistent effects matching a filter. |
| `sfs.effect.dispel` | Remove effects flagged as dispellable. |

### 5.6 Movement and positioning

| Id | Purpose |
|---|---|
| `sfs.move.to_zone` | Move an entity to a zone (pays movement, triggers OoA). |
| `sfs.move.to_offset` | Move to a grid offset (grid scenes only). |
| `sfs.move.force` | Forced movement (pull/push), ignores reactions. |
| `sfs.position.teleport` | Teleport, no OoA. |
| `sfs.position.line_of_sight` | Query whether A can see B. |

### 5.7 Knowledge and visibility

| Id | Purpose |
|---|---|
| `sfs.knowledge.update` | Update a knowing entity's view of another. |
| `sfs.visibility.compute` | Recompute visibility after movement. |

### 5.8 Actions and reactions

| Id | Purpose |
|---|---|
| `sfs.action.can_take` | Prerequisite check for an action. |
| `sfs.action.spend_economy` | Mark action/bonus action/reaction as spent. |
| `sfs.reaction.offer` | Programmatically open a reaction window (rarely needed; usually implicit). |

### 5.9 Rests and time

| Id | Purpose |
|---|---|
| `sfs.rest.short` | Execute a short rest (pack rules). |
| `sfs.rest.long` | Execute a long rest. |
| `sfs.time.advance` | Advance a clock by N ticks. |

### 5.10 Progression

| Id | Purpose |
|---|---|
| `sfs.xp.award` | Award XP to entities. |
| `sfs.progression.check` | Check whether level-up is available. |
| `sfs.progression.apply_level` | Apply a level-up step result. |

SFS v1 totals 41 functions. Every one has a JSON schema and a short
behavioral description shipped alongside.

## 6. Declarative Definitions

Each top-level pack category is defined as a JSON object with enumerated
fields. Below is the shape of the main ones; full JSON Schemas ship in
`specs/schemas/pack-v1.schema.json`.

### 6.1 ActionDef

```
ActionDef = {
    id: ActionId,
    display_name: String,
    kind: ActionKind,                     // Action | BonusAction | Reaction | FreeAction | Movement
    prerequisites: [Prerequisite],        // proficiency, resource, reach, etc.
    cost: [ResourceCost],
    targeting: TargetingSpec,
    effects: [EffectTemplateRef],
    adjudication: AdjudicationHint,       // NeverAdjudicate | AdjudicateOnAmbiguity | AlwaysAdjudicate
    tags: OrderedSet<ActionTag>,
}
```

### 6.2 EffectTemplateDef

```
EffectTemplateDef = {
    id: EffectTemplateId,
    display_name: String,
    body: EffectBody,
    scaling: Option<ScalingSpec>,          // e.g. "per spell slot above base"
    default_transformers: [TransformerRef],
    tags: OrderedSet<EffectTag>,
}
```

### 6.3 TriggerDef

```
TriggerDef = {
    id: TriggerId,
    owner_scope: OwnerScope,               // Entity | Scene
    on: TriggerCondition,
    effect_template: EffectTemplateRef,
    cost: Option<ResourceCost>,
    priority: i32,
    one_shot: bool,
    reaction: bool,
}
```

### 6.4 TransformerDef, ConditionDef, ItemTemplate, ClassDef, SpellDef, MonsterDef, ...

Every additional `*Def` follows the same pattern: an id, a display name,
pack-scoped data, references to other pack ids, and no executable content.

### 6.5 ProgressionConfig

```
ProgressionConfig = {
    xp_table: [XpRow],                     // level -> xp threshold
    level_up_steps: OrderedMap<ClassId, [LevelUpStep]>,
    rest_types: [RestDef],
    death_rules: DeathRules,
    exhaustion_rules: ExhaustionRules,
}
LevelUpStep = {
    id: StepId,
    prompt_kind: PromptKind,               // ChooseSubclass, ChooseASI, ChooseSpells, ...
    legal_choices_fn: SfsFunctionId,       // returns [Choice]
    apply_fn: SfsFunctionId,               // applies the chosen result
}
```

- Level-up is a pack-scripted `PromptDecision` sequence. The Engine walks
  `level_up_steps`, calls `legal_choices_fn` to build the legal set, issues
  `PromptDecision`, then calls `apply_fn` with the chosen id. No pack code;
  only SFS function references.

## 7. File Layout on Disk

A pack directory is optional. Packs may be a single JSON file or a folder.
When a folder:

```
<pack_id>/
  manifest.json
  body/
    actions.json
    items.json
    spells.json
    ...
  assets/           # optional, unused by the Engine
```

Canonicalization for checksum purposes concatenates `body/` files in
lexicographic order by filename before hashing.

## 8. Thrust of Validation

Pack validation is aggressive. The goal is to catch every foreseeable
authoring bug at load time rather than mid-session. A conformant Engine
rejects:

- Unknown top-level keys
- Dangling references to ids
- SFS function mismatches (name, version, signature)
- Filter expressions exceeding nesting depth
- Negative `maximum` on a resource (unless `BoundBelow` is also set)
- Cycles in `extends` between packs
- Effect templates whose body references undeclared damage types
- Trigger/transformer priorities outside `i32` range (automatic in JSON,
  but flagged as an advisory at load)
- Duplicate ids within any list
- `PackManifest.checksum` mismatch

Every rejection is `FatalError::PackValidationFailed{pack_id, reason}` with
a human-readable reason and a JSON path to the offending node.

## 9. Evolution

To extend SFS in a minor version:
1. Add new `SfsFunctionEntry` to the registry with `since = <new minor>`.
2. Engines supporting the new minor publish an SFS registry with the entry.
3. Packs targeting the new minor may reference the function; packs
   targeting the old minor continue to load unchanged.

To extend the pack schema in a minor version:
1. Add an optional field or a new `*Def` category to this spec.
2. Update JSON Schemas in `specs/schemas/pack-v1.schema.json` (additive only
   for minor bumps).
3. Existing packs load unchanged because unknown fields are still rejected
   at the old schema version; the new minor has the new fields as optional.

Major bumps require a migration path documented in a dedicated migration
notes file shipped with the bump.
