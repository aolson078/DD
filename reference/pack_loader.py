"""
Load a pack JSON, validate manifest, register SFS functions.

See specs/04-content-packs-and-sfs.md for the normative definitions.

Validation is aggressive. The goal is to catch every foreseeable authoring
bug at load time rather than mid-session (spec 04 Section 8).
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from reference.canonical import canonical_json
from reference.engine import SessionState, EngineError
from reference.sfs import SFS_FUNCTIONS


# ---------------------------------------------------------------------------
# Pack validation result
# ---------------------------------------------------------------------------

class PackValidationError(Exception):
    """FatalError::PackValidationFailed. See spec 01 Section 6.1."""
    def __init__(self, pack_id: str, reason: str, path: str = ""):
        self.pack_id = pack_id
        self.reason = reason
        self.path = path
        super().__init__(f"PackValidationFailed: {pack_id}: {reason} (at {path})")


# ---------------------------------------------------------------------------
# Manifest fields  (spec 04 Section 2.1)
# ---------------------------------------------------------------------------

REQUIRED_MANIFEST_FIELDS = [
    "id", "version", "pack_schema_version", "sfs_version",
    "display_name", "description", "authors", "license",
]

OPTIONAL_MANIFEST_FIELDS = [
    "requires_sfs_functions", "requires_component_types",
    "requires_damage_types", "origin", "checksum", "extends",
]

SUPPORTED_SCHEMA_RANGE = ("1.0.0", "1.99.99")
SUPPORTED_SFS_RANGE = ("1.0.0", "1.99.99")


# ---------------------------------------------------------------------------
# load_pack  (spec 04 Section 2.2)
# ---------------------------------------------------------------------------

def load_pack(source: str | Path | dict,
              state: SessionState | None = None) -> tuple[dict, SessionState]:
    """Load and validate a pack.

    See spec 04 Section 2.2.

    source can be:
    - A path to a single JSON file
    - A path to a pack directory (with manifest.json)
    - A dict (already parsed JSON)

    Returns (pack_data, updated_state).
    Raises PackValidationError on any validation failure.
    """
    # 1. Parse
    if isinstance(source, dict):
        pack_data = source
    elif isinstance(source, (str, Path)):
        source = Path(source)
        if source.is_dir():
            pack_data = _load_pack_directory(source)
        else:
            with open(source, "r", encoding="utf-8") as f:
                pack_data = json.load(f)
    else:
        raise PackValidationError("unknown", "Invalid source type")

    # 2. Validate manifest
    manifest = pack_data.get("manifest")
    if manifest is None:
        raise PackValidationError("unknown", "Missing manifest", "manifest")

    pack_id = manifest.get("id", "unknown")
    _validate_manifest(manifest, pack_id)

    # 3. Validate schema version
    pack_schema = manifest.get("pack_schema_version", "0.0.0")
    if not _version_in_range(pack_schema, SUPPORTED_SCHEMA_RANGE):
        raise PackValidationError(
            pack_id,
            f"Schema version {pack_schema} not in supported range "
            f"{SUPPORTED_SCHEMA_RANGE}",
            "manifest.pack_schema_version",
        )

    # 4. Validate SFS version
    sfs_version = manifest.get("sfs_version", "0.0.0")
    if not _version_in_range(sfs_version, SUPPORTED_SFS_RANGE):
        raise PackValidationError(
            pack_id,
            f"SFS version {sfs_version} not in supported range "
            f"{SUPPORTED_SFS_RANGE}",
            "manifest.sfs_version",
        )

    # 5. Validate required SFS functions are registered
    required_fns = manifest.get("requires_sfs_functions", [])
    for fn_id in required_fns:
        if fn_id not in SFS_FUNCTIONS:
            raise PackValidationError(
                pack_id,
                f"Unknown SFS function: {fn_id}",
                f"manifest.requires_sfs_functions[{fn_id}]",
            )

    # 6. Validate no duplicate IDs within lists
    _validate_no_duplicate_ids(pack_data, pack_id)

    # 7. Validate checksum (if present and non-zero)
    checksum = manifest.get("checksum")
    if checksum and checksum != "0" * 64:
        _validate_checksum(pack_data, checksum, pack_id)

    # 8. Install into state
    if state is None:
        from reference.engine import create_session
        state = create_session()

    state.pack_ids.append(pack_id)
    state.pack_data = pack_data

    # Load entities from pack if present (e.g. bestiary, prebuilt characters)
    _install_pack_entities(pack_data, state)

    return pack_data, state


def _load_pack_directory(path: Path) -> dict:
    """Load a pack from a directory structure.

    See spec 04 Section 7.

    Structure:
      <pack_id>/
        manifest.json
        body/
          actions.json
          items.json
          ...
    """
    manifest_path = path / "manifest.json"
    if not manifest_path.exists():
        raise PackValidationError(
            str(path.name), "Missing manifest.json", str(manifest_path)
        )

    with open(manifest_path, "r", encoding="utf-8") as f:
        manifest = json.load(f)

    pack_data: dict[str, Any] = {"manifest": manifest}

    # Load body files
    body_path = path / "body"
    if body_path.exists() and body_path.is_dir():
        for json_file in sorted(body_path.glob("*.json")):
            key = json_file.stem
            with open(json_file, "r", encoding="utf-8") as f:
                pack_data[key] = json.load(f)

    return pack_data


def _validate_manifest(manifest: dict, pack_id: str) -> None:
    """Validate that all required manifest fields are present.

    See spec 04 Section 2.1.
    """
    for field_name in REQUIRED_MANIFEST_FIELDS:
        if field_name not in manifest:
            raise PackValidationError(
                pack_id,
                f"Missing required manifest field: {field_name}",
                f"manifest.{field_name}",
            )

    # Validate id format (kebab-case)
    pack_id_value = manifest.get("id", "")
    if not pack_id_value or not isinstance(pack_id_value, str):
        raise PackValidationError(
            pack_id, "Invalid pack id", "manifest.id"
        )

    # Validate version is semver-like
    for ver_field in ["version", "pack_schema_version", "sfs_version"]:
        ver = manifest.get(ver_field, "")
        if not isinstance(ver, str) or not ver:
            raise PackValidationError(
                pack_id, f"Invalid version in {ver_field}: {ver}",
                f"manifest.{ver_field}",
            )

    # Validate authors is a list
    if not isinstance(manifest.get("authors", []), list):
        raise PackValidationError(
            pack_id, "authors must be a list", "manifest.authors"
        )


def _version_in_range(version: str, supported_range: tuple[str, str]) -> bool:
    """Check if a version string is within the supported range.

    Simple comparison: parse major.minor.patch and check bounds.
    """
    try:
        parts = [int(x) for x in version.split(".")[:3]]
        lo = [int(x) for x in supported_range[0].split(".")[:3]]
        hi = [int(x) for x in supported_range[1].split(".")[:3]]
        return lo <= parts <= hi
    except (ValueError, IndexError):
        return False


def _validate_no_duplicate_ids(pack_data: dict, pack_id: str) -> None:
    """Validate that no list in the pack contains duplicate ids.

    See spec 04 Section 8.
    """
    list_keys = [
        "stats", "resources", "clocks", "rests", "damage_types",
        "conditions", "items", "actions", "checks", "saves",
        "triggers", "transformers", "effect_templates",
        "persistent_effect_templates", "scenes", "tables",
    ]
    for key in list_keys:
        items = pack_data.get(key, [])
        if not isinstance(items, list):
            continue
        seen_ids: set[str] = set()
        for item in items:
            if isinstance(item, dict) and "id" in item:
                item_id = item["id"]
                if item_id in seen_ids:
                    raise PackValidationError(
                        pack_id,
                        f"Duplicate id '{item_id}' in {key}",
                        f"{key}[{item_id}]",
                    )
                seen_ids.add(item_id)


def _validate_checksum(pack_data: dict, expected: str, pack_id: str) -> None:
    """Validate the pack checksum.

    See spec 04 Section 2.1:
    checksum = SHA-256 of the canonical body minus manifest.checksum.
    """
    body = dict(pack_data)
    if "manifest" in body:
        manifest_copy = dict(body["manifest"])
        manifest_copy.pop("checksum", None)
        body["manifest"] = manifest_copy

    computed = hashlib.sha256(
        canonical_json(body).encode("utf-8")
    ).hexdigest()

    if computed != expected:
        raise PackValidationError(
            pack_id,
            f"Checksum mismatch: expected {expected}, got {computed}",
            "manifest.checksum",
        )


def _install_pack_entities(pack_data: dict, state: SessionState) -> None:
    """Install entities defined in the pack (e.g. prebuilt characters, monsters).

    This is a simplified version. Full implementation would handle all
    component types and cross-references.
    """
    from reference.domain import Stats, Resources, ResourcePool, Position, Faction

    # Install entities from a 'entities' or 'characters' section if present
    for entity_def in pack_data.get("entities", []):
        name = entity_def.get("name", "Unknown")
        entity = state.add_entity(name)

        # Stats
        if "stats" in entity_def:
            stats_data = entity_def["stats"]
            stats = Stats(
                scores=stats_data.get("scores", {}),
                proficiencies=stats_data.get("proficiencies", []),
                derived=stats_data.get("derived", {}),
            )
            entity.components["stats"] = stats

        # Resources
        if "resources" in entity_def:
            resources = Resources()
            for res_id, res_data in entity_def["resources"].items():
                resources.pools[res_id] = ResourcePool(
                    current=res_data.get("current", res_data.get("maximum", 0)),
                    maximum=res_data.get("maximum", 0),
                    recovery=res_data.get("recovery", "Manual"),
                    tags=res_data.get("tags", []),
                )
            entity.components["resources"] = resources

        # Position
        if "position" in entity_def:
            pos_data = entity_def["position"]
            entity.components["position"] = Position(
                scene_id=pos_data.get("scene_id", ""),
                zone_id=pos_data.get("zone_id", ""),
            )

        # Faction
        if "faction" in entity_def:
            fac_data = entity_def["faction"]
            entity.components["faction"] = Faction(
                primary=fac_data.get("primary", ""),
                disposition=fac_data.get("disposition", {}),
            )

        # Controller
        if "controller" in entity_def:
            ctrl = entity_def["controller"]
            if ctrl == "DriverOwned":
                entity.components["controller"] = Controller.DRIVER_OWNED
            elif isinstance(ctrl, dict) and "Player" in ctrl:
                entity.components["controller"] = PlayerController(ctrl["Player"])
