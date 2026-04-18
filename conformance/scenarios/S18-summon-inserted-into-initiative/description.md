# S18 - Summon Inserted Into Initiative

Validates `InsertionRule::AtBottom` for summoned entities. A wizard (entity 1)
casts a summon spell during combat. The summoned entity is created with a new
unique entity id and inserted at the bottom of the initiative order. The log
shows `EntityCreated` followed by the initiative order update reflecting the
new entity at the end.

Exercises invariants: D1 (unique entity ids).
