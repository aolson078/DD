# S16 - Massive Damage Instant Death

Validates the pack's death rules for massive damage. An entity at full HP
(20 HP max) takes damage equal to or exceeding double their max HP (40+
damage in one hit). The pack rule triggers instant death, bypassing the
normal death saving throw flow. The log shows a
`PersistentEffectApplied Dead` event immediately after the damage.

Exercises invariants: pack-defined death rules.
