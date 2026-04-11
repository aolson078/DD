# 00 - Glossary and Layers

This document locks the vocabulary every other spec file uses. If a term is
not defined here, it must not appear in the other files. If a term is defined
here, it must be used consistently with this definition everywhere else.

## Layering

The spec describes four concentric layers. Dependencies flow inward only.

```
+-----------------------------------------------------------+
|                         HOST                              |
|  event loop, UI, network, persistence, clock, RNG source  |
|                                                           |
|  +-----------------------------------------------------+  |
|  |                      DRIVER                         |  |
|  |   human / scripted / LLM - answers engine requests  |  |
|  |                                                     |  |
|  |  +-----------------------------------------------+  |  |
|  |  |                   ENGINE                      |  |  |
|  |  |   pure state machine, no I/O, no threads      |  |  |
|  |  |                                               |  |  |
|  |  |  +-----------------------------------------+  |  |  |
|  |  |  |                DOMAIN                   |  |  |  |
|  |  |  |   types only: entities, effects, etc.   |  |  |  |
|  |  |  +-----------------------------------------+  |  |  |
|  |  +-----------------------------------------------+  |  |
|  +-----------------------------------------------------+  |
+-----------------------------------------------------------+
```

**Domain** holds only data types. No behavior.
**Engine** interprets domain data and produces `Transition`s. Pure.
**Driver** is a contract the host implements to answer engine requests.
**Host** embeds everything and owns I/O, scheduling, persistence, UI.

A module at layer N may only reference terms from layers `<= N`.

## Glossary

Terms are listed alphabetically. Every term is either a **type**, a
**relationship**, or a **process**. Type names are `PascalCase`; process names
are `snake_case`.

- **Action** (type): a declarative unit of intent performed by an Entity during
  its turn or reaction window. An Action has prerequisites, resource costs, a
  set of Effect pushes, and a post-resolution result. Actions are pure data;
  their semantics are given by the Engine and the SFS functions they reference.

- **Adjudicate** (process): a Driver request issued by the Engine when an
  Entity proposes an Action that is not fully covered by the pack's rules.
  The Driver returns a `Ruling` (allow/deny/modify) which the Engine applies.

- **Agent**: synonym for Driver. Avoid; use "Driver".

- **Attack Roll** (process): a specialization of `Check` where the target is
  another Entity's Armor Class component.

- **Check** (type): a `Challenge{roll, target, modifiers, on_success,
  on_failure}`. The fundamental primitive for any dice-vs-number resolution.

- **Choice** (type): the Driver's response to a `PromptDecision`. A Choice
  identifies exactly one element of the `legal_choices` set offered by the
  request, plus optional metadata (free text, sub-choices).

- **Clock** (type): a named time scale. The spec defines `round`, `minute`,
  `hour`, `day`; packs may declare additional clocks. Durations and recovery
  rules reference clock tags.

- **Component** (type): a typed piece of state attached to an Entity. Examples:
  `Stats`, `Inventory`, `Position`, `Resources`, `PersistentEffects`. Entities
  are open maps of Components (entity-component composition, not inheritance).

- **Conformant** (property): an implementation of the Engine is conformant iff
  it produces the expected event-log hashes for every scenario in the
  conformance suite at a declared `(pack_schema, sfs)` version pair.

- **Continuation** (type): the opaque part of `NeedsDecision(request,
  continuation)` the Host round-trips back to the Engine along with the
  Driver's response. Conformant Engines must treat Continuations as opaque and
  must not require them to be persistable across processes - replay uses the
  event log, not Continuations.

- **Damage Instance** (type): a specific delivery of damage with amount, type,
  source, and optional tags. Subject to `Transformer`s before resolution.

- **Decision** (type): a tagged union over all possible Driver responses:
  `Choice | Ruling | RollResult | Ack`.

- **Domain**: see "Layering".

- **Driver** (role): the external decision-maker. Fulfills the Driver Contract
  defined in `01-execution-model.md`.

- **Effect** (type): a declarative description of a change to game state. May
  be instantaneous (damage, heal) or persistent (condition, aura, ongoing
  save). Effects are pushed onto the Effect Stack, subject to Transformers,
  and resolved in LIFO order.

- **Effect Stack** (type): the ordered list of pending Effects awaiting
  resolution. Specified in `03-effects-and-stack.md`.

- **Engine** (role): the pure state machine. See "Layering".

- **Entity** (type): an identity plus a map of Components. The unit of agency
  and targeting in the simulation.

- **Event** (type): an immutable record appended to the Event Log when the
  Engine transitions state. Events are the ground truth of "what happened".

- **Event Log** (type): the append-only, ordered sequence of Events for a
  session. The log's SHA-256 is the conformance oracle.

- **Fatal Error** (type): an Engine-halting error. See "Recoverable Error" for
  the contrast. Enumerated in `01-execution-model.md`.

- **Host** (role): the process embedding the Engine and a Driver. See
  "Layering".

- **Initiative** (type): a per-Combat-mode ordering of Entities by a rolled
  tiebroken value. Specified in `07-combat-mode.md`.

- **Invariant**: a property the Engine must maintain across every transition.
  Invariants are enumerated alongside the types they guard.

- **Legal Choices** (type): the Engine-computed set of valid Choices attached
  to a `PromptDecision`. The Driver must return a member of this set or the
  Engine treats the response as a Recoverable Error.

- **Mode** (type): a tag on a Scene selecting which rules subset is active.
  Defined: `Exploration | Social | Combat | Downtime | Custom(id)`.

- **NeedsDecision** (Transition variant): see "Transition".

- **Pack** (type): a Content Pack. A bundle of data declaring stats,
  resources, conditions, items, actions, abilities, bestiary, progression, and
  a manifest of required SFS functions. Specified in
  `04-content-packs-and-sfs.md`.

- **Persistent Effect** (type): an Effect with a duration, held on an Entity
  (or the Scene) until an expiry predicate fires.

- **PromptDecision** (Driver request): an Engine-issued request for the
  Driver to select one of the Engine-provided `legal_choices`.

- **Recoverable Error** (type): an error the Engine surfaces without halting.
  The Engine re-issues the triggering request with a rejection reason.

- **Resource** (type): a mutable pool attached to an Entity with a maximum, a
  current value, and a recovery rule keyed to a Clock or a Rest type.

- **Ruling** (type): the Driver's response to `Adjudicate`. One of
  `Allow(modifier?) | Deny(reason) | Modify(patch)`.

- **Scene** (type): a bounded gameplay context: a location, a cast of
  Entities, a Mode, available Actions. The unit of gameplay state transitions.

- **SFS** (Standard Function Set): the versioned registry of named host
  functions Packs are allowed to reference. Specified in
  `04-content-packs-and-sfs.md`.

- **Stack**: when used alone, means the Effect Stack.

- **Step** (process): one call to the Engine's `step(state, input) ->
  Transition`. The smallest unit of Engine progress.

- **Terminal** (Transition variant): marks session end.

- **Transformer** (type): a pure function attached to an Effect that rewrites
  it before resolution. Immunity, resistance, vulnerability, and "half on
  save" are Transformers.

- **Transition** (type): the return value of `step`. Exactly one of
  `Yielded | NeedsDecision | Terminal | Errored`.

- **Trigger** (type): a condition on game events that, when true, pushes a
  specified Effect onto the stack. Examples: `OnDamageDealt`, `OnSaveFailed`,
  `AtStartOfTurn`.

- **Turn** (type): a Combat-mode unit in which one Entity has priority. A
  Round is a sequence of Turns covering every Entity in initiative order.

- **Yielded** (Transition variant): the Engine produced events and is ready to
  step again without Driver input.

## Terms Deliberately Not Used

These terms are banned from the spec because they are imprecise or
system-specific. If you want to use one, define a replacement here first.

- "Character sheet" - use `Entity` + its `Stats`, `Inventory`, etc. Components.
- "Game state" - use either `SessionState`, `SceneState`, or the Event Log,
  depending on what you mean.
- "Plugin" - packs are not plugins; they cannot ship code. Use `Pack`.
- "Script" - ambiguous between `driver_script` (in the conformance suite) and
  "scripting language". Disambiguate at every use site.
- "Condition" without qualification - use `Persistent Effect of kind
  Condition` or, in prose, "Condition (a named persistent effect)".

## Cross-Reference Rules

- Each spec file lists its "Introduces" (types first defined there) and
  "Uses" (types imported from earlier files) in a header table.
- A type may only be introduced in one file.
- No forward references. If file `05` needs a type from `07`, one of the two
  files is wrong.
