# S05 - Concentration Broken

Validates the concentration uniqueness invariant (P1) and the break-on-damage
mechanic. A cleric (entity 1) is concentrating on Bless. On the skeleton's
(entity 2) turn, it attacks the cleric with a shortsword. The attack hits,
dealing damage and triggering a CON saving throw to maintain concentration.
The save fails, and Bless's persistent effect is removed with a
`PersistentEffectRemoved` event carrying reason `ConcentrationBroken`.

Exercises invariants: P1 (concentration uniqueness), R2 (save resolution),
E4 (non-empty event batches).
