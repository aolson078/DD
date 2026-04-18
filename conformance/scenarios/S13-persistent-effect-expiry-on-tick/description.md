# S13 - Persistent Effect Expiry on Tick

Validates the time-based expiry predicate `TimeElapsed { clock: "round",
amount: 10 }`. A 1-minute-duration buff (10 rounds in 5e timing) is applied
to an entity. The scenario advances clocks via tick inputs. The persistent
effect expires exactly on the tenth `ClockTicked(round)` event, producing
a `PersistentEffectRemoved` with reason `TimeExpired`.

Exercises invariants: C1 (clock tick semantics), C2 (expiry predicate
evaluation).
