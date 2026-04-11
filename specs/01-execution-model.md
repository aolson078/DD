# 01 - Execution Model

**Introduces**: `Transition`, `DriverRequest`, `DriverResponse`, `SessionState`,
`EngineError`, `FatalError`, `RecoverableError`, `Rng`, `step`.
**Uses**: everything in `00-glossary-and-layers.md`.

This file defines how the Engine runs. Every other file assumes this model.

## 1. The Engine is a Pure State Machine

The Engine exposes exactly one primary operation:

```
step(state: SessionState, input: StepInput) -> Transition
```

- `state` is immutable; `step` returns a new state inside the `Transition`.
- `step` performs no I/O, no time reads, no threading, no global mutation.
- The only randomness is via an `Rng` embedded in `state`. The Host seeds it
  and hands it in; the Engine never instantiates its own.
- `step` is a total function: every `(state, input)` pair maps to exactly one
  `Transition`. Undefined cases are `FatalError`s and must be enumerated.

### Invariant E1 (Purity)
Calling `step(s, i)` twice in different processes with the same `s` and `i`
MUST yield the same `Transition` byte-for-byte. This is the foundation of the
conformance suite.

### Invariant E2 (No backdoors)
The Engine MUST NOT accept any input channel other than `step`'s parameters.
No callbacks, no observer registration, no event buses owned by the Engine.

## 2. StepInput

```
StepInput =
  | Start                              // kicks off a newly-loaded state
  | DriverResponse(request_id, value)  // answers a prior NeedsDecision
  | Tick(clock_tag)                    // advances a named Clock by one unit
  | Halt                               // graceful shutdown request
```

- `request_id` references a previously-yielded `DriverRequest`. Mismatched
  ids produce a `FatalError::ProtocolViolation`.
- `Tick` is the Host's only way to advance time. The Engine never reads a
  wall clock; Hosts choose when to emit ticks. In turn-based play, the Host
  typically ticks `round` between turns; `minute`/`hour`/`day` ticks are
  Host-chosen (e.g. during Downtime).

## 3. Transition

```
Transition =
  | Yielded(events: EventBatch, next_state: SessionState)
  | NeedsDecision(request: DriverRequest, continuation: Continuation, next_state: SessionState)
  | Terminal(final_events: EventBatch)
  | Errored(error: EngineError, recoverable: bool, next_state: SessionState)
```

### Yielded
The Engine produced one or more Events, state advanced, and the Host can call
`step` again with `Start` or another input without Driver interaction. Example:
applying a persistent effect's expiry at tick time.

### NeedsDecision
The Engine requires a Driver response before it can continue. The Host:
1. Appends `DriverRequestEmitted{request_id, request}` to the Event Log.
2. Invokes the Driver (synchronously or asynchronously - the Host's choice).
3. Appends `DriverResponseRecorded{request_id, response}` to the Event Log.
4. Calls `step(next_state, DriverResponse(request_id, response))`.

The `continuation` is opaque Engine state carried through the round trip. The
Host MUST NOT inspect or mutate it. Continuations MUST be fully serializable
as part of `SessionState` for save/resume; they MUST NOT capture file handles,
closures, or references outside `SessionState`.

### Terminal
Session is over. The Host should persist the log and stop calling `step`.

### Errored
An error occurred. If `recoverable == true`, the next `step` call with a
valid input will re-prompt (typically re-issuing the prior `NeedsDecision`
with reduced `legal_choices`). If `recoverable == false`, the Engine is
halted for this session; the Host must construct a new session to continue.

### Invariant E3 (Single variant)
Exactly one `Transition` variant is returned per `step` call.

### Invariant E4 (Event batches are non-empty except for NeedsDecision)
`Yielded.events` and `Terminal.final_events` MUST contain at least one Event.
`NeedsDecision` carries no events (events produced *before* the decision are
flushed in a preceding `Yielded`).

## 4. The Driver Contract

The Driver answers four request kinds. The Engine NEVER calls anything else
on the Driver.

```
DriverRequest =
  | PromptDecision { context: DecisionContext, legal_choices: [Choice], prompt_kind: PromptKind }
  | Adjudicate     { proposal: ActionProposal, rules_context: RulesContext }
  | Narrate        { events: EventBatch }               // ack-only, logged
  | RequestRoll    { spec: RollSpec, reason: RollReason }
```

```
DriverResponse =
  | ChoiceResponse(Choice)
  | RulingResponse(Ruling)
  | Ack
  | RollResponse(RollResult)
```

The `prompt_kind` tag discriminates the kind of decision (action selection,
target selection, reaction opt-in, level-up step, ...) so that Driver
implementations can dispatch without parsing `context` freeform.

### 4.1 PromptDecision

- `legal_choices` is never empty. If the Engine computes an empty set, it
  MUST emit `FatalError::NoLegalChoices` instead.
- The Driver MUST return one element of `legal_choices` (by its `id` field).
  Returning a non-member is a `RecoverableError::IllegalChoice`.
- Ties and defaults are not the Engine's responsibility. If a default is
  desired, the pack includes a `choice_default: Choice.id` in the context and
  the Host policy may use it.

### 4.2 Adjudicate

- Used only when a pack or player proposes an action not fully resolved by
  the rules. The Engine passes the full `rules_context` (visible state,
  relevant rules fragments) and the proposal.
- The Ruling is one of:
  - `Allow { modifier: Option<Modifier> }` - proceed, optionally with a
    numeric bonus/penalty.
  - `Deny { reason: String }` - prevent the action; logged.
  - `Modify { patch: ActionPatch }` - proceed with a structured modification.
- `ActionPatch` is a constrained set of mutations (change target, alter cost,
  add/remove effect refs). It MUST NOT introduce new effects not referenced
  by the pack. Any patch violating this is a `RecoverableError::IllegalPatch`.

### 4.3 Narrate

- Fire-and-forget semantic. The Driver MAY return prose; the Engine stores it
  as `Narration{driver_text}` in the Event Log for Host display.
- The Engine does NOT wait on narration. A Host that wants blocking narration
  can choose to block `step` externally.

### 4.4 RequestRoll

- The Engine issues `RequestRoll` when it wants a roll attributable to the
  Driver (e.g., the Host delegates physical dice to a human player).
- The Host MAY short-circuit by not forwarding to the Driver and instead
  using the Engine's `Rng` directly. In that case the Host MUST still
  synthesize a `DriverResponse(RollResponse(result))` and append it to the
  log so replay is byte-identical.
- If `state.rng_policy == EngineOnly`, the Engine resolves all rolls
  internally and never emits `RequestRoll`.

## 5. Determinism

### Invariant E5 (Replay equivalence)
Given the tuple `(initial_state, seed, ordered_driver_responses)`, any
conformant Engine MUST produce an Event Log whose SHA-256 matches the
golden hash.

### Invariant E6 (Log shape)
The Event Log is a sequence of canonicalized JSON records (see
`09-persistence-and-replay.md` for the canonical form). Canonicalization
rules include sorted keys, stable number formatting, and no trailing
whitespace. Two conformant Engines MUST emit byte-identical canonical
records.

### Invariant E7 (RNG discipline)
- The `Rng` exposes one operation: `next_u64(rng) -> (u64, rng)`.
- Every roll uses a documented consumption schedule (number of `next_u64`
  calls and their interpretation). The schedule is in `02-domain-model.md`.
- The Engine MUST NOT branch on wall-clock values, map ordering, or hash
  collisions. All orderings are by explicit key (`entity_id`, `effect_id`,
  source timestamp).

## 6. Error Model

Errors are divided into two tiers. Every error type is enumerated here;
no catch-all variant exists.

### 6.1 FatalError (halts the session)

```
FatalError =
  | PackValidationFailed { pack_id, reason }
  | UnknownSfsFunction { name, pack_id }
  | SchemaVersionMismatch { pack_version, engine_range }
  | InvariantViolation { invariant_id, where, snapshot }
  | ProtocolViolation { detail }     // mismatched request_id, etc.
  | NoLegalChoices { at, kind }
  | RngExhausted                     // reserved; cannot occur with u64 rng
  | CorruptedState { detail }
```

- On FatalError the Host SHOULD persist the Event Log including the error
  record, then stop stepping.
- `InvariantViolation` MUST carry the invariant id (`E1`..`E7` or others
  defined in later files) and enough context to diagnose post-hoc. It is a
  bug in the Engine itself, not in the pack or the Driver.

### 6.2 RecoverableError (re-prompted)

```
RecoverableError =
  | IllegalChoice { request_id, provided, expected_set }
  | IllegalPatch { request_id, reason }
  | IllegalRoll { request_id, reason }
  | PrerequisiteUnmet { action_id, missing }
  | ResourceExhausted { resource_id, needed, available }
  | OutOfRange { action_id, needed, available }
  | OutOfLineOfSight { action_id, from, to }
  | TargetInvalid { action_id, reason }
```

Recoverable errors are emitted as `Errored(..., recoverable: true)` and MUST
be followed by re-issuing the failing `DriverRequest` with a tightened
`legal_choices` (or the same request if the Driver itself is confused). The
Engine MUST NOT loop forever: after a pack-configured number of failed
retries (`max_recoverable_retries`, default 3) it escalates to
`FatalError::ProtocolViolation`.

### Invariant E8 (Error logging)
Every `EngineError` is appended to the Event Log as an `Error{tier, kind,
detail}` record before `Errored` is returned. Replay MUST reproduce the
same error sequence.

## 7. The Reference Host Loop (normative pseudocode)

```
fn run(session: SessionState, driver: Driver) -> EventLog {
    let mut state = session;
    let mut log = state.event_log_so_far();
    let mut input = StepInput::Start;
    loop {
        match step(state, input) {
            Yielded(events, next) => {
                log.append(events);
                state = next;
                input = StepInput::Start;
            }
            NeedsDecision(req, cont, next) => {
                log.append(Event::DriverRequestEmitted(req.id(), req.clone()));
                let resp = driver.respond(req);
                log.append(Event::DriverResponseRecorded(req.id(), resp.clone()));
                state = next.with_continuation(cont);
                input = StepInput::DriverResponse(req.id(), resp);
            }
            Terminal(events) => {
                log.append(events);
                return log;
            }
            Errored(err, recoverable, next) => {
                log.append(Event::Error(err.clone()));
                state = next;
                if !recoverable { return log; }
                input = StepInput::Start; // engine will re-issue request
            }
        }
    }
}
```

Conformant Hosts MAY restructure this loop (async, multi-session scheduling)
but MUST preserve the ordering of `DriverRequestEmitted` and
`DriverResponseRecorded` in the log and MUST call `step` with the corrected
input after each transition.

## 8. Open Questions

*(None at lock time. This section is required in drafts but must be empty
before the file is considered locked.)*
