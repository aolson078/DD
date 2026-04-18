# S17 - Area Effect with Save for Half

Validates AOE targeting all entities in a zone, where each target
independently saves. Those who succeed have their damage scaled by
`ScaleNumeric(1,2)` (save for half), while those who fail take full damage.
A wizard casts fireball hitting three targets. The log shows one
`EffectPushed`, multiple independent save rolls, and per-target damage
with transformer differences based on save results.

Exercises invariants: S4 (stack resolution for damage), T2 (transformer
application per target).
