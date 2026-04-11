# 10 - Threat Model

**Uses**: `00`-`09`.

This file enumerates the adversarial scenarios the spec must resist and
names the concrete mitigation each relies on. Every mitigation traces to
a requirement stated in an earlier file.

## 1. Trust Boundaries

```
+---------+    untrusted    +--------+   semi-trusted    +--------+
| Pack    | --------------> | Engine | <---------------- | Driver |
+---------+                  +--------+                   +--------+
                               ^
                               | trusted
                               |
                             +------+
                             | Host |
                             +------+
```

- **Pack**: untrusted. Authors may be malicious or buggy. The Engine
  validates at load and constrains at runtime.
- **Driver**: semi-trusted. A human Driver is not adversarial but may
  fumble; an LLM Driver may produce nonsense; a scripted Driver is under
  the Host's control. The Engine assumes all Driver inputs are hostile for
  safety and only trusts them for semantics.
- **Host**: trusted. The Host embeds the Engine and owns storage and I/O.
  A compromised Host compromises the session regardless.

## 2. Threats

### T1. Malicious pack: code execution

**Attack**: An author ships a pack that tries to run arbitrary code on the
Host.

**Mitigations**:
- `04-content-packs-and-sfs.md` Section 1: no DSL, no executable content
  in packs. Packs are pure data.
- SFS is the only callable interface; the Engine dispatches by name to
  Host-registered functions. A pack cannot introduce new code paths.
- `JsonFilter` grammar (`03-effects-and-stack.md` Appendix A) is finite,
  non-recursive beyond depth 16, and has no function-call form.
- Derived-stat arithmetic tree (`05-rules-engine.md` Section 8) has fixed
  operators and depth bound.

**Residual risk**: A buggy SFS implementation in the Host can still be
exploited via specially crafted arguments. Mitigation is the SFS
`SfsFunctionSignature.input` JSON Schema, which the Engine validates
before dispatch.

### T2. Malicious pack: resource exhaustion

**Attack**: A pack declares triggers that fire in infinite loops, or
effects with unbounded dice counts, or persistent effects that never
expire.

**Mitigations**:
- Invariant S2: `MAX_STACK_DEPTH = 128`. Overflow is fatal.
- Pack load validation checks that `RollSpec.dice[i].count <= 1_000`
  (configurable, default 1000) and nesting depth on JsonFilter.
- Persistent effect `Duration` is bounded; `UntilDispelled` is allowed but
  the Engine tracks a `MAX_PERSISTENT_EFFECTS_PER_ENTITY` (default 64).
- `max_recoverable_retries` in `01-execution-model.md` Section 6.2 bounds
  recovery loops.
- SFS functions carry `effects` declarations (`04` Section 4); functions
  producing events not in their declared list are fatal, closing the
  trigger-storm loophole.

**Residual risk**: A pack can still push close to the bounds without
tripping them. The Host may enforce lower limits via a config. These are
performance risks, not correctness risks.

### T3. Malicious pack: schema or id confusion

**Attack**: A pack declares an id that shadows a core id (e.g.
`sfs.damage.typed` as a pack-local id) to hijack dispatch.

**Mitigations**:
- SFS function ids are namespaced with reserved prefix `sfs.`. Pack load
  rejects any pack-local definition using that prefix except for
  `requires_sfs_functions` references.
- Pack ids are namespaced by author prefix; cross-pack id conflicts are
  detected at load.
- `extends` must be acyclic.

### T4. Malicious pack: information leakage

**Attack**: A pack attempts to exfiltrate data about the session via a
narration-style SFS call.

**Mitigations**:
- SFS functions are Host-registered; a Host that does not register
  network-calling SFS functions is immune. The spec's SFS v1 contains no
  function with network effects.
- `Narrate` driver requests contain only events the Engine produced; the
  pack cannot inject arbitrary payload into narration. The Driver decides
  what to display.

### T5. Adversarial driver: illegal choices

**Attack**: An LLM Driver returns choice ids that are not in
`legal_choices`, or returns malformed `Ruling` objects.

**Mitigations**:
- Invariant in `01` Section 4.1: illegal choices are `RecoverableError::
  IllegalChoice`, re-prompted with tightened `legal_choices`.
- Bounded retries via `max_recoverable_retries` (default 3) escalate to
  fatal protocol violation.
- `ActionPatch` is a constrained mutation set; invalid patches are
  rejected and the original proposal proceeds or is denied.

### T6. Adversarial driver: metagaming / cheating

**Attack**: A Driver inspects hidden state (other players' hands,
monster stats they should not see) to bias decisions.

**Mitigations**:
- OUT OF SCOPE for the Engine. Information hiding is the Host's
  responsibility: the Host MUST project a filtered view of
  `DecisionContext` when prompting drivers that should not see hidden
  state. The spec exposes `Knowledge` components and a reference
  projection function, but does not enforce usage.

This is called out here so Hosts know they must do this themselves.

### T7. Log tampering

**Attack**: An attacker mutates the Event Log between sessions to rewrite
history.

**Mitigations**:
- `09-persistence-and-replay.md` Section 3: records carry `prev_hash`;
  tampering invalidates subsequent hashes.
- `integrity_verify` called on session open detects tampering.
- Conformance hashes in `11` detect post-hoc edits that preserve
  `prev_hash` within a segment: the final log hash still diverges from
  the golden.

**Residual risk**: An attacker who controls the Host can rewrite the log
and snapshots consistently. Cryptographic signatures are OUT OF SCOPE for
this spec; a Host may add them above the Engine layer.

### T8. Replay drift

**Attack**: An implementation bug produces correct-looking logs that
diverge from the golden hashes on replay.

**Mitigations**:
- Invariants E1, E5, E6, PS5 jointly require byte-identical replay.
- Conformance suite runs replay explicitly in one of its goldens and
  compares hashes.
- Canonical JSON rules (PS1) close "equivalent-but-different-bytes"
  loopholes.

### T9. RNG manipulation

**Attack**: A malicious Host or Driver intervenes to bias rolls.

**Mitigations**:
- The seed is part of `SessionState`; changing it changes the log hash
  and fails conformance.
- `RequestRoll` responses are logged verbatim; a Host that "helps" by
  rolling favorably still fails the conformance hash.
- This is a "can't be done invisibly" mitigation, not a "can't be done"
  mitigation. Adversarial Hosts can always alter state locally.

### T10. SFS version skew

**Attack**: A pack targets a different SFS version than the host
provides, leading to subtle behavioral differences.

**Mitigations**:
- `04` Section 3: engines declare `supported_sfs_range`; packs declare
  `sfs_version`; mismatch is fatal at load.
- Minor version bumps are additive only; a pack targeting v1.0 still
  loads on v1.1.
- Major bumps require explicit migration paths.

### T11. Resource underflow / overflow

**Attack**: An action pushes a resource below its declared bound.

**Mitigations**:
- `02-domain-model.md` Section 2.2: resource bounds are enforced on every
  spend / grant. Violations are `RecoverableError::ResourceExhausted` or
  fatal (depending on which bound and the pack's policy).
- Integer arithmetic is `i32` / `i64` with overflow checks; wrapping is a
  `FatalError::InvariantViolation`.

### T12. Protocol confusion (mismatched continuations)

**Attack**: A Host feeds a stale `DriverResponse` with a stale
`request_id` after a restart.

**Mitigations**:
- `01` Section 3: `request_id` mismatch is
  `FatalError::ProtocolViolation`.
- `open_continuation` and `recoverable_retry_count` are part of
  `SessionState`, so they round-trip through snapshots.

## 3. Mitigations Matrix

| Threat | Primary mitigation | Spec ref |
|---|---|---|
| T1  Code execution   | No DSL, SFS only                        | 04.1, 03.A |
| T2  Exhaustion       | Stack depth, dice limits, retry caps    | 03.S2, 01.6.2 |
| T3  Id shadowing     | Namespaced ids, load validation         | 04.8        |
| T4  Info leakage     | SFS v1 has no network effects           | 04.5        |
| T5  Illegal choices  | Recoverable errors, retry cap           | 01.6.2      |
| T6  Metagaming       | Host projects filtered context (OoS)    | -           |
| T7  Log tampering    | prev_hash chain, integrity_verify       | 09.3, 09.7  |
| T8  Replay drift     | Canonical JSON, replay invariants       | PS1, PS5    |
| T9  RNG bias         | Seed in state, responses logged         | E5, E7      |
| T10 Version skew     | Load-time version check                 | 04.3        |
| T11 Resource bounds  | Checked at every mutation               | 02.2.2      |
| T12 Protocol confusion | request_id checks                     | 01.3        |

## 4. Non-Threats (explicitly out of scope)

- Cryptographic log authenticity (the Host may add signatures).
- Secure multi-party play (trusted Host model).
- Denial-of-service from the Driver side (the Host controls scheduling).
- Side-channel attacks (timing, cache) against the Engine.
- Compromise of an LLM Driver via prompt injection: the Host is
  responsible for its prompt strategies; the spec's contribution is that
  a compromised Driver still cannot produce illegal states because the
  Engine validates every response.
