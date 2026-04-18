"""
Canonical JSON serialization for event log hashing.

See specs/02-domain-model.md Section 7, Invariant D4.

Rules:
1. Objects are emitted with keys sorted by UTF-8 byte order.
2. Strings use minimal-escape JSON (no \\u00XX for printable ASCII).
3. Numbers are integers only.
4. Whitespace: exactly one \\n between records; no whitespace inside records.
5. Arrays preserve authorial order.
6. Unknown fields are rejected by conformant parsers.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any


def canonical_json(obj: Any) -> str:
    """Serialize an object to canonical JSON.

    See specs/02 Section 7, Invariant D4:
    - Keys sorted by UTF-8 byte order
    - Minimal-escape strings
    - No whitespace inside records
    - Arrays preserve order
    """
    return json.dumps(
        _sort_keys_recursively(obj),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def _sort_keys_recursively(obj: Any) -> Any:
    """Recursively sort dict keys for canonical form."""
    if isinstance(obj, dict):
        return {k: _sort_keys_recursively(v) for k, v in sorted(obj.items())}
    if isinstance(obj, list):
        return [_sort_keys_recursively(item) for item in obj]
    return obj


def canonical_hash(record: Any) -> str:
    """Compute SHA-256 of a canonical JSON record.

    See specs/02 Section 7, Invariant D4 and specs/01 Invariant E5.
    """
    text = canonical_json(record)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def update_rolling_hash(current_hash: str, record: Any) -> str:
    """Update the rolling SHA-256 hash with a new canonical record.

    See specs/02 Section 5: event_log_hash_so_far is a rolling SHA-256
    of the canonical Event Log up to the last appended record.
    """
    record_json = canonical_json(record)
    combined = current_hash + "\n" + record_json
    return hashlib.sha256(combined.encode("utf-8")).hexdigest()
