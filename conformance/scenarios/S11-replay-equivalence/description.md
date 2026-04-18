# S11 - Replay Equivalence

Validates invariant PS5 (replay equivalence). Takes scenario S03's produced
event log, replays it against the same initial state and seed, and asserts
the reproduced log's SHA-256 matches the original exactly. This is the
primary determinism test -- if any ordering, RNG consumption, or
serialization differs between runs, replay will fail.

Exercises invariants: E1 (purity), E5 (replay equivalence), E6 (log shape),
D3 (state totality), D4 (canonical JSON), PS1-PS6 (persistence and replay).
