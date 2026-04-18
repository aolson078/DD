# S02 - Advantage Canceled by Disadvantage

Validates that when an entity has both advantage and disadvantage on a roll,
they cancel out and a single straight d20 is rolled. A rogue (entity 1)
attempts a Stealth check with advantage from cover and disadvantage from a
"blessed target" persistent effect on the enemy. The log must show a single
straight d20 roll with no extra dice consumed, confirming the cancellation
happens before the SFS roll call.

Exercises invariants: RE1 (modifier collection), D2 (RNG determinism),
E7 (RNG discipline).
