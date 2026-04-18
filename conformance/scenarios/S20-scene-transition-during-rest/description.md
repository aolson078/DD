# S20 - Scene Transition During Rest

Validates the clean interaction between `can_save` and rest flow. A rest is
initiated in one scene. A `SceneTransition` is scheduled (via tick or pack
trigger). The engine completes the rest first, then transitions to the new
scene. The log shows all rest events (recovery, effect removal,
`RestCompleted`) appearing before the scene-exit/scene-enter events,
confirming the rest is not interrupted by the scene change.

Exercises invariants: SC1 (scene transitions), SC3 (save point interaction
with scene transitions).
