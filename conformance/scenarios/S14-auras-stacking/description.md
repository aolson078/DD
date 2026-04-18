# S14 - Aura Stacking

Validates that two overlapping auras interact correctly with anti-stack
triggers. Two paladins (entities 1 and 2) each have a "paladin aura" that
grants a modifier. The pack defines an anti-stack trigger on this aura type.
When paladin 2 enters paladin 1's aura zone, the anti-stack trigger fires
and removes the earlier aura's effect, leaving only one instance active.
The log shows both `PersistentEffectApplied` events and then one
`PersistentEffectRemoved` with reason `AntiStackTriggered`.

Exercises invariants: RE3 (stacking/anti-stack rules).
