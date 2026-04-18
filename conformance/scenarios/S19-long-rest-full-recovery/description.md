# S19 - Long Rest Full Recovery

Validates PR3 (rest atomicity). A long rest recovers HP to maximum, restores
spell slots, restores partial hit dice, and removes certain persistent effects
marked with `OnRest(long)` expiry. All recovery events are emitted in one
`Yielded` batch, followed by `RestCompleted`. The scenario verifies that
partial state is never observable -- the rest is atomic.

Exercises invariants: PR1 (rest mechanics), PR2 (resource recovery rules),
PR3 (rest atomicity).
