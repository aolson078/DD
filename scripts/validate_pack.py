#!/usr/bin/env python3
"""Validate packs/srd-5e-lite/pack.json against specs/schemas/pack-v1.schema.json."""

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCHEMA_PATH = ROOT / "specs" / "schemas" / "pack-v1.schema.json"
PACK_PATH = ROOT / "packs" / "srd-5e-lite" / "pack.json"

errors: list[str] = []


def err(msg: str):
    errors.append(msg)


def load_json(path: Path):
    with open(path) as f:
        return json.load(f)


schema = load_json(SCHEMA_PATH)
pack = load_json(PACK_PATH)

# ── 1. Required top-level keys ────────────────────────────────────────────
required_keys = schema["required"]  # 26 items (manifest + 25 arrays/objects)
print(f"Schema requires {len(required_keys)} top-level keys.")

for k in required_keys:
    if k not in pack:
        err(f"MISSING top-level key: '{k}'")

extra_keys = set(pack.keys()) - set(schema["properties"].keys())
for k in extra_keys:
    err(f"EXTRA top-level key not in schema: '{k}'")

# ── 2. Per-section required-field checks ──────────────────────────────────
defs = schema.get("$defs", {})


def resolve_ref(ref: str):
    """Resolve a local #/$defs/Foo reference to the schema def dict."""
    if ref.startswith("#/$defs/"):
        name = ref.split("/")[-1]
        return defs.get(name, {})
    return {}


def required_fields_of(def_name: str) -> list[str]:
    d = defs.get(def_name, {})
    return d.get("required", [])


# Map from top-level key -> $defs type name
section_def_map = {}
for key, prop in schema.get("properties", {}).items():
    if prop.get("type") == "array" and "items" in prop:
        ref = prop["items"].get("$ref", "")
        if ref.startswith("#/$defs/"):
            section_def_map[key] = ref.split("/")[-1]

print(f"Array sections to check: {list(section_def_map.keys())}")

for section, def_name in section_def_map.items():
    req = required_fields_of(def_name)
    if not req:
        continue
    items = pack.get(section, [])
    if not isinstance(items, list):
        err(f"Section '{section}' should be an array but is {type(items).__name__}")
        continue
    for idx, item in enumerate(items):
        if not isinstance(item, dict):
            err(f"{section}[{idx}]: expected object, got {type(item).__name__}")
            continue
        item_id = item.get("id", f"<index {idx}>")
        for field in req:
            if field not in item:
                err(f"{section}[{idx}] (id={item_id}): missing required field '{field}'")
        # Check properties not in the schema def
        allowed = set(defs[def_name].get("properties", {}).keys())
        if defs[def_name].get("additionalProperties") is False:
            for fk in item:
                if fk not in allowed:
                    err(f"{section}[{idx}] (id={item_id}): unexpected field '{fk}'")

# Special: manifest (object, not array)
manifest_req = required_fields_of("PackManifest")
manifest = pack.get("manifest", {})
if isinstance(manifest, dict):
    for field in manifest_req:
        if field not in manifest:
            err(f"manifest: missing required field '{field}'")
    allowed = set(defs["PackManifest"].get("properties", {}).keys())
    if defs["PackManifest"].get("additionalProperties") is False:
        for fk in manifest:
            if fk not in allowed:
                err(f"manifest: unexpected field '{fk}'")

# Special: progression (ProgressionConfig)
prog_req = required_fields_of("ProgressionConfig")
prog = pack.get("progression", {})
if isinstance(prog, dict):
    for field in prog_req:
        if field not in prog:
            err(f"progression: missing required field '{field}'")
    # death_rules
    dr_req = required_fields_of("DeathRules")
    dr = prog.get("death_rules", {})
    if isinstance(dr, dict):
        for field in dr_req:
            if field not in dr:
                err(f"progression.death_rules: missing required field '{field}'")
    # exhaustion_rules
    ex_req = required_fields_of("ExhaustionRules")
    ex = prog.get("exhaustion_rules", {})
    if isinstance(ex, dict):
        for field in ex_req:
            if field not in ex:
                err(f"progression.exhaustion_rules: missing required field '{field}'")

# ── 3. Nested required-field checks ──────────────────────────────────────

# ClassDef -> features (ClassFeature) and level_up_steps (LevelUpStepConfig)
cf_req = required_fields_of("ClassFeature")
lus_req = required_fields_of("LevelUpStepConfig")
for cidx, cls in enumerate(pack.get("classes", [])):
    cls_id = cls.get("id", f"<index {cidx}>")
    for fidx, feat in enumerate(cls.get("features", [])):
        feat_id = feat.get("id", f"<index {fidx}>")
        for field in cf_req:
            if field not in feat:
                err(f"classes[{cidx}].features[{fidx}] (class={cls_id}, feature={feat_id}): missing '{field}'")
    for sidx, step in enumerate(cls.get("level_up_steps", [])):
        step_id = step.get("id", f"<index {sidx}>")
        for field in lus_req:
            if field not in step:
                err(f"classes[{cidx}].level_up_steps[{sidx}] (class={cls_id}, step={step_id}): missing '{field}'")

# RaceDef -> traits (RacialTrait)
rt_req = required_fields_of("RacialTrait")
for ridx, race in enumerate(pack.get("races", [])):
    race_id = race.get("id", f"<index {ridx}>")
    for tidx, trait in enumerate(race.get("traits", [])):
        trait_id = trait.get("id", f"<index {tidx}>")
        for field in rt_req:
            if field not in trait:
                err(f"races[{ridx}].traits[{tidx}] (race={race_id}, trait={trait_id}): missing '{field}'")

# MonsterDef -> traits (ClassFeature)
for midx, mon in enumerate(pack.get("bestiary", [])):
    mon_id = mon.get("id", f"<index {midx}>")
    for tidx, trait in enumerate(mon.get("traits", [])):
        trait_id = trait.get("id", f"<index {tidx}>")
        for field in cf_req:
            if field not in trait:
                err(f"bestiary[{midx}].traits[{tidx}] (monster={mon_id}, trait={trait_id}): missing '{field}'")

# ResourceDef required field checks
res_req = required_fields_of("ResourceDef")
for ridx, res in enumerate(pack.get("resources", [])):
    res_id = res.get("id", f"<index {ridx}>")
    for field in res_req:
        if field not in res:
            err(f"resources[{ridx}] (id={res_id}): missing required field '{field}'")

# TriggerDef required field checks
trig_req = required_fields_of("TriggerDef")
for tidx, trig in enumerate(pack.get("triggers", [])):
    trig_id = trig.get("id", f"<index {tidx}>")
    for field in trig_req:
        if field not in trig:
            err(f"triggers[{tidx}] (id={trig_id}): missing required field '{field}'")

# TransformerDef required field checks
trans_req = required_fields_of("TransformerDef")
for tidx, trans in enumerate(pack.get("transformers", [])):
    trans_id = trans.get("id", f"<index {tidx}>")
    for field in trans_req:
        if field not in trans:
            err(f"transformers[{tidx}] (id={trans_id}): missing required field '{field}'")

# ActionDef required field checks
act_req = required_fields_of("ActionDef")
for aidx, act in enumerate(pack.get("actions", [])):
    act_id = act.get("id", f"<index {aidx}>")
    for field in act_req:
        if field not in act:
            err(f"actions[{aidx}] (id={act_id}): missing required field '{field}'")

# EffectTemplateDef required field checks
et_req = required_fields_of("EffectTemplateDef")
for eidx, et in enumerate(pack.get("effect_templates", [])):
    et_id = et.get("id", f"<index {eidx}>")
    for field in et_req:
        if field not in et:
            err(f"effect_templates[{eidx}] (id={et_id}): missing required field '{field}'")

# PersistentEffectTemplateDef required field checks
pet_req = required_fields_of("PersistentEffectTemplateDef")
for pidx, pet in enumerate(pack.get("persistent_effect_templates", [])):
    pet_id = pet.get("id", f"<index {pidx}>")
    for field in pet_req:
        if field not in pet:
            err(f"persistent_effect_templates[{pidx}] (id={pet_id}): missing required field '{field}'")

# SceneTemplate required checks and nested ZoneGraph
scene_req = required_fields_of("SceneTemplate")
for sidx, scene in enumerate(pack.get("scenes", [])):
    scene_id = scene.get("id", f"<index {sidx}>")
    for field in scene_req:
        if field not in scene:
            err(f"scenes[{sidx}] (id={scene_id}): missing required field '{field}'")
    # ZoneGraph
    zones_obj = scene.get("zones", {})
    if isinstance(zones_obj, dict):
        zg_req = required_fields_of("ZoneGraph")
        for field in zg_req:
            if field not in zones_obj:
                err(f"scenes[{sidx}].zones: missing required field '{field}'")

# RollTable required checks
rt_req2 = required_fields_of("RollTable")
for tidx, tbl in enumerate(pack.get("tables", [])):
    tbl_id = tbl.get("id", f"<index {tidx}>")
    for field in rt_req2:
        if field not in tbl:
            err(f"tables[{tidx}] (id={tbl_id}): missing required field '{field}'")
    for eidx, entry in enumerate(tbl.get("entries", [])):
        rte_req = required_fields_of("RollTableEntry")
        for field in rte_req:
            if field not in entry:
                err(f"tables[{tidx}].entries[{eidx}]: missing required field '{field}'")

# ── 4. Cross-reference checks ─────────────────────────────────────────────

# Collect id sets
effect_template_ids = {et["id"] for et in pack.get("effect_templates", []) if isinstance(et, dict)}
persistent_effect_template_ids = {pet["id"] for pet in pack.get("persistent_effect_templates", []) if isinstance(pet, dict)}
action_ids = {a["id"] for a in pack.get("actions", []) if isinstance(a, dict)}
trigger_ids = {t["id"] for t in pack.get("triggers", []) if isinstance(t, dict)}
transformer_ids = {t["id"] for t in pack.get("transformers", []) if isinstance(t, dict)}
resource_ids = {r["id"] for r in pack.get("resources", []) if isinstance(r, dict)}
stat_ids = {s["id"] for s in pack.get("stats", []) if isinstance(s, dict)}
condition_ids = {c["id"] for c in pack.get("conditions", []) if isinstance(c, dict)}
item_ids = {i["id"] for i in pack.get("items", []) if isinstance(i, dict)}
check_ids = {c["id"] for c in pack.get("checks", []) if isinstance(c, dict)}
save_ids = {s["id"] for s in pack.get("saves", []) if isinstance(s, dict)}
class_ids = {c["id"] for c in pack.get("classes", []) if isinstance(c, dict)}
race_ids = {r["id"] for r in pack.get("races", []) if isinstance(r, dict)}
spell_ids = {s["id"] for s in pack.get("spells", []) if isinstance(s, dict)}
rest_ids = {r["id"] for r in pack.get("rests", []) if isinstance(r, dict)}
clock_ids = {c["id"] for c in pack.get("clocks", []) if isinstance(c, dict)}
scene_ids = {s["id"] for s in pack.get("scenes", []) if isinstance(s, dict)}

# 4a. Action effects -> effect_templates ids
for aidx, action in enumerate(pack.get("actions", [])):
    aid = action.get("id", f"<{aidx}>")
    for eff in action.get("effects", []):
        if eff not in effect_template_ids:
            err(f"actions (id={aid}): effects reference '{eff}' not found in effect_templates")

# 4b. Condition -> persistent_effect_template ids
for cidx, cond in enumerate(pack.get("conditions", [])):
    cid = cond.get("id", f"<{cidx}>")
    pet = cond.get("persistent_effect_template")
    if pet and pet not in persistent_effect_template_ids:
        err(f"conditions (id={cid}): persistent_effect_template '{pet}' not found in persistent_effect_templates")

# 4c. Trigger effect_template -> effect_templates ids
for tidx, trig in enumerate(pack.get("triggers", [])):
    tid = trig.get("id", f"<{tidx}>")
    et = trig.get("effect_template")
    if et and et not in effect_template_ids:
        err(f"triggers (id={tid}): effect_template '{et}' not found in effect_templates")

# 4d. Class features grants -> action/trigger/transformer ids exist
for cidx, cls in enumerate(pack.get("classes", [])):
    cls_id = cls.get("id", f"<{cidx}>")
    for fidx, feat in enumerate(cls.get("features", [])):
        feat_id = feat.get("id", f"<{fidx}>")
        for aid in feat.get("grants_actions", []):
            if aid not in action_ids:
                err(f"classes[{cidx}].features (class={cls_id}, feature={feat_id}): grants_actions '{aid}' not found in actions")
        for tid in feat.get("grants_triggers", []):
            if tid not in trigger_ids:
                err(f"classes[{cidx}].features (class={cls_id}, feature={feat_id}): grants_triggers '{tid}' not found in triggers")
        for tid in feat.get("grants_transformers", []):
            if tid not in transformer_ids:
                err(f"classes[{cidx}].features (class={cls_id}, feature={feat_id}): grants_transformers '{tid}' not found in transformers")

# 4e. Spell effects -> effect_templates ids
for sidx, spell in enumerate(pack.get("spells", [])):
    sid = spell.get("id", f"<{sidx}>")
    for eff in spell.get("effects", []):
        if eff not in effect_template_ids:
            err(f"spells (id={sid}): effects reference '{eff}' not found in effect_templates")

# 4f. Item grants_actions -> action ids
for iidx, item in enumerate(pack.get("items", [])):
    iid = item.get("id", f"<{iidx}>")
    for aid in item.get("grants_actions", []):
        if aid not in action_ids:
            err(f"items (id={iid}): grants_actions '{aid}' not found in actions")
    for tid in item.get("grants_triggers", []):
        if tid not in trigger_ids:
            err(f"items (id={iid}): grants_triggers '{tid}' not found in triggers")
    for tid in item.get("grants_transformers", []):
        if tid not in transformer_ids:
            err(f"items (id={iid}): grants_transformers '{tid}' not found in transformers")

# 4g. Feat grants -> action/trigger/transformer ids
for fidx, feat in enumerate(pack.get("feats", [])):
    fid = feat.get("id", f"<{fidx}>")
    for aid in feat.get("grants_actions", []):
        if aid not in action_ids:
            err(f"feats (id={fid}): grants_actions '{aid}' not found in actions")
    for tid in feat.get("grants_triggers", []):
        if tid not in trigger_ids:
            err(f"feats (id={fid}): grants_triggers '{tid}' not found in triggers")
    for tid in feat.get("grants_transformers", []):
        if tid not in transformer_ids:
            err(f"feats (id={fid}): grants_transformers '{tid}' not found in transformers")

# 4h. Persistent effect template grants_transformers -> transformer ids
for pidx, pet in enumerate(pack.get("persistent_effect_templates", [])):
    pid = pet.get("id", f"<{pidx}>")
    for tid in pet.get("grants_transformers", []):
        if tid not in transformer_ids:
            err(f"persistent_effect_templates (id={pid}): grants_transformers '{tid}' not found in transformers")
    for tid in pet.get("grants_triggers", []):
        if tid not in trigger_ids:
            err(f"persistent_effect_templates (id={pid}): grants_triggers '{tid}' not found in triggers")

# 4i. Effect template body ApplyPersistent -> persistent_effect_template ids
for eidx, et in enumerate(pack.get("effect_templates", [])):
    eid = et.get("id", f"<{eidx}>")
    body = et.get("body", {})
    if isinstance(body, dict) and "ApplyPersistent" in body:
        tmpl = body["ApplyPersistent"].get("template")
        if tmpl and tmpl not in persistent_effect_template_ids:
            err(f"effect_templates (id={eid}): ApplyPersistent template '{tmpl}' not found in persistent_effect_templates")

# 4j. Progression exhaustion transformers -> transformer ids
prog = pack.get("progression", {})
if isinstance(prog, dict):
    ex = prog.get("exhaustion_rules", {})
    if isinstance(ex, dict):
        for epl in ex.get("effects_per_level", []):
            for tid in epl.get("transformers", []):
                if tid not in transformer_ids:
                    err(f"progression.exhaustion_rules: transformer '{tid}' not found in transformers")

# 4k. Bestiary actions -> action ids
for midx, mon in enumerate(pack.get("bestiary", [])):
    mid = mon.get("id", f"<{midx}>")
    for aid in mon.get("actions", []):
        if aid not in action_ids:
            err(f"bestiary (id={mid}): action '{aid}' not found in actions")

# 4l. Rest recovery rest_kind -> rest ids
for ridx, res in enumerate(pack.get("resources", [])):
    rid = res.get("id", f"<{ridx}>")
    rec = res.get("recovery", {})
    if isinstance(rec, dict) and "OnRest" in rec:
        rk = rec["OnRest"].get("rest_kind")
        if rk and rk not in rest_ids:
            err(f"resources (id={rid}): recovery rest_kind '{rk}' not found in rests")

# ── 5. SFS function references ────────────────────────────────────────────
sfs_pattern = re.compile(r"^[a-z][a-z0-9]*(\.[a-z][a-z0-9_]*)+$")
for fn in manifest.get("requires_sfs_functions", []):
    if not sfs_pattern.match(fn):
        err(f"manifest.requires_sfs_functions: '{fn}' is not a valid dotted SFS function id")

# ── 6. Type checks for specific fields ───────────────────────────────────

# ActionDef.targeting must be an object with required field 'kind'
targeting_req = required_fields_of("TargetingSpec")
for aidx, action in enumerate(pack.get("actions", [])):
    aid = action.get("id", f"<{aidx}>")
    tgt = action.get("targeting")
    if tgt is not None:
        if not isinstance(tgt, dict):
            err(f"actions (id={aid}): targeting should be object, got {type(tgt).__name__}")
        else:
            for field in targeting_req:
                if field not in tgt:
                    err(f"actions (id={aid}): targeting missing required field '{field}'")

# ── Summary ───────────────────────────────────────────────────────────────
if errors:
    print(f"\n=== {len(errors)} VALIDATION ERROR(S) ===")
    for e in errors:
        print(f"  ERROR: {e}")
    sys.exit(1)
else:
    print("\nAll checks passed. Zero errors.")
    sys.exit(0)
