# S09 - Exploration to Combat Mid-Move

Validates that `change_mode` can fire during an exploration action. The party
moves into a zone containing a hidden ambusher. A trigger fires that
transitions the mode from Exploration to Combat, rolls initiative for all
entities, and interrupts the remaining movement. The log shows the ambush
trigger, `ModeChanged` from Exploration to Combat, initiative rolls, and a
new turn start.

Exercises invariants: SC1 (scene/mode transitions), SC2 (initiative ordering),
C4 (clock reset on mode change).
