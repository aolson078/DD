"""
ScriptedDriver that replays from a driver_script.json.

See specs/01-execution-model.md Section 4 for the Driver contract.

The Driver answers four request kinds:
- PromptDecision -> ChoiceResponse
- Adjudicate -> RulingResponse
- Narrate -> Ack
- RequestRoll -> RollResponse

A ScriptedDriver feeds predetermined responses from a JSON file, enabling
deterministic replay and testing.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class ScriptedDriver:
    """A Driver that replays responses from a JSON script.

    See spec 01 Section 4 and Section 7 (Reference Host Loop).

    The script is a list of response entries, consumed in order.
    Each entry has:
    - request_type: the DriverRequest type this responds to
    - response: the DriverResponse value

    Example script:
    [
        {"request_type": "PromptDecision", "response": {"id": "attack_melee"}},
        {"request_type": "PromptDecision", "response": {"id": "end_turn"}},
        {"request_type": "Narrate", "response": "ack"}
    ]
    """

    def __init__(self, script: list[dict] | None = None):
        self.script: list[dict] = script or []
        self.index: int = 0
        self.log: list[dict] = []

    @classmethod
    def from_file(cls, path: str | Path) -> ScriptedDriver:
        """Load a driver script from a JSON file."""
        with open(path, "r", encoding="utf-8") as f:
            script = json.load(f)
        if not isinstance(script, list):
            raise ValueError(f"Driver script must be a JSON array, got {type(script)}")
        return cls(script=script)

    @classmethod
    def from_choices(cls, choices: list[str]) -> ScriptedDriver:
        """Create a driver that makes the given choices in order.

        Convenience constructor for simple test scenarios.
        """
        script = [
            {"request_type": "PromptDecision", "response": {"id": c}}
            for c in choices
        ]
        return cls(script=script)

    def respond(self, request: dict) -> Any:
        """Respond to a DriverRequest.

        See spec 01 Section 4.

        Consumes the next entry from the script. If the script is exhausted,
        returns a default response based on the request type.
        """
        request_type = request.get("type", "")
        request_id = request.get("request_id", 0)

        self.log.append({
            "index": self.index,
            "request": request,
        })

        if self.index < len(self.script):
            entry = self.script[self.index]
            self.index += 1

            response = entry.get("response")
            self.log[-1]["response"] = response
            return response

        # Default responses when script is exhausted
        return self._default_response(request)

    def _default_response(self, request: dict) -> Any:
        """Generate a default response when the script is exhausted.

        For PromptDecision: pick the last choice (typically EndTurn).
        For Narrate: return Ack.
        For RequestRoll: let the engine handle it.
        For Adjudicate: Allow.
        """
        request_type = request.get("type", "")

        if request_type == "PromptDecision":
            choices = request.get("legal_choices", [])
            if choices:
                # Default to last choice (often EndTurn)
                return choices[-1]
            return {"id": "end_turn"}

        if request_type == "Narrate":
            return "ack"

        if request_type == "Adjudicate":
            return {"ruling": "Allow"}

        if request_type == "RequestRoll":
            return None  # Engine should use its own RNG

        return "ack"

    @property
    def exhausted(self) -> bool:
        """True if all script entries have been consumed."""
        return self.index >= len(self.script)

    @property
    def remaining(self) -> int:
        """Number of script entries remaining."""
        return max(0, len(self.script) - self.index)
