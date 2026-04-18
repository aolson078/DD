# S04 - Counterspell

Validates that a reaction can interrupt a spell on the effect stack. A wizard
(entity 1) casts firebolt targeting an enemy wizard (entity 2). Entity 2
reacts with counterspell, which pushes onto the stack above the firebolt.
The counterspell resolves first (LIFO), succeeds, and removes the firebolt
frame from the stack. No damage is dealt.

Exercises invariants: S1 (stack LIFO ordering), S2 (reaction insertion),
S3 (stack frame removal by effect).
