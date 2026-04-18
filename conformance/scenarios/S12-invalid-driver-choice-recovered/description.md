# S12 - Invalid Driver Choice Recovered

Validates the recoverable error path. The driver returns a choice id not
present in `legal_choices`. The engine emits `Error(IllegalChoice)` as a
recoverable error, then re-prompts the driver with the same (or tightened)
legal choices. On the second attempt, the driver returns a valid choice and
the scenario continues normally. The log records the full error-and-recovery
exchange.

Exercises invariants: E3 (single transition variant), E8 (error logging).
