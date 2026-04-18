# S15 - Temporary HP Absorption

Validates the `TempHP` transformer that redirects damage before HP reduction.
An entity (entity 1) has 5 temporary HP and takes 8 damage. The log shows
the temp HP pool emptied first (reduced by 5 to 0), then the remaining 3
damage applied as HP reduction.

Exercises invariants: T1 (transformer ordering -- TempHP before HP).
