# S06 - Short Rest with Hit Dice

Validates the pack-driven prompt loop for hit-die spending during a short rest.
A fighter (entity 1) at reduced HP initiates a short rest. The driver chooses
to spend 2 hit dice. The log shows two `sfs.roll.hit_die` calls, matching
`sfs.heal.dice` applications, and a final `RestCompleted` event.

Exercises invariants: PR1 (rest mechanics), C3 (clock interaction with rest).
