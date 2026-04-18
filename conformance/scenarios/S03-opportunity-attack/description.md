# S03 - Opportunity Attack

Validates that a reaction trigger from `OnActionDeclared(move)` correctly
pushes a reaction effect onto the stack mid-movement, and that the reaction
resolves before the movement completes. A fighter (entity 1) moves out of
a goblin's (entity 2) threatened zone. The goblin is offered a reaction,
takes the opportunity attack, deals damage, and then the fighter's move
completes.

Exercises invariants: S1 (stack LIFO ordering), S2 (reaction insertion),
E3 (single transition variant).
