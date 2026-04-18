# S07 - Level Up Fighter to 4

Validates the pack-scripted `LevelUpFlow`. A fighter (entity 1) reaches
level 4 and walks through the level-up steps: HP increase, ability score
increase vs feat choice, and feature grants. The log shows `LevelUpStarted`,
a `LevelUpStep` for each decision point, and `LevelUpCompleted`.

Exercises invariants: L1 (level-up flow correctness).
