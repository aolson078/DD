# S01 - Damage Resistance

Validates that the `ScaleNumeric(1,2)` transformer correctly halves damage
when a matching resistance persistent effect is active. A goblin (entity 2)
with `resistance:fire` takes 12 fire damage from a firebolt cast by a wizard
(entity 1). The expected event sequence shows the attack hitting, the damage
effect pushed at 12, the transformer reducing it to 6, and the goblin's HP
decreasing by 6.

Exercises invariants: T1 (transformer ordering), T2 (transformer application),
S4 (stack resolution for damage), D2 (RNG determinism), E7 (RNG discipline).
