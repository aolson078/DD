# 09 - Persistence and Replay

**Introduces**: `EventLog`, `EventRecord`, `Snapshot`, `SessionId`,
`canonical_json`, `replay`, `log_hash`, `integrity_verify`.
**Uses**: `00`-`08`.

## 1. Storage Model

Two kinds of artifacts are defined:

- **Event Log**: append-only sequence of canonical JSON records, one per
  line, UTF-8. The source of truth.
- **Snapshot**: the full `SessionState` serialized as canonical JSON, taken
  at a save point (`can_save(state) == true`).

A session consists of an initial snapshot plus a log. Optionally, mid-run
snapshots may be taken to accelerate replay from large logs.

```
<session_dir>/
  manifest.json            # session id, seed, versions, created_at
  snapshot.initial.json    # canonical initial SessionState
  log.ndjson               # append-only canonical event records
  snapshots/               # optional
    000001.json            # snapshot after N records
    000002.json
```

## 2. Canonical JSON

### Invariant PS1 (Canonicalization)
Canonical JSON serialization MUST:
1. Use UTF-8 with no BOM.
2. Emit object keys in lexicographic byte order.
3. Emit arrays in array order (no reordering).
4. Emit integers without leading `+`, with `-` only for negative; no
   decimal point, no exponent; bounded to `i64`.
5. Emit strings with the minimum necessary JSON escaping (`\"`, `\\`,
   `\n`, `\r`, `\t`, `\u0000`-`\u001F` for control characters). Everything
   else is emitted as UTF-8.
6. Use `null` for absent optional fields only when the schema marks the
   field explicitly nullable; otherwise, absent fields are omitted.
7. Emit booleans as `true`/`false` lowercase.
8. No whitespace inside records; exactly one `\n` separator between
   records in the Event Log (and no trailing newline at file end, unless
   the last record completed a write that left it).

### Pseudocode

```
fn canonical_json(value: JsonValue, out: &mut Writer):
    match value:
        Null: out.write("null")
        Bool(b): out.write(if b { "true" } else { "false" })
        Int(n): out.write(n.to_string())
        Str(s): out.write('"'); write_escaped(s, out); out.write('"')
        Arr(items):
            out.write('[')
            for (i, v) in items.enumerate():
                if i > 0: out.write(',')
                canonical_json(v, out)
            out.write(']')
        Obj(map):
            let keys = sort(map.keys(), utf8_bytes)
            out.write('{')
            for (i, k) in keys.enumerate():
                if i > 0: out.write(',')
                canonical_json(Str(k), out)
                out.write(':')
                canonical_json(map[k], out)
            out.write('}')
```

`write_escaped` escapes the minimal set listed in rule 5. No Unicode
escapes for printable characters.

### Invariant PS2 (Byte equivalence)
Two conformant engines MUST emit byte-identical canonical JSON for the
same `JsonValue`. The canonicalization is deterministic and has no
"equivalent" alternatives (no "pretty-printed is equivalent" clause).

## 3. Event Log Format

```
EventRecord = {
    seq: u64,                       // monotonic from 0
    at_clocks: OrderedMap<ClockTag, u64>,
    event: Event,                   // from 02 Section 6
    prev_hash: HexString,           // sha256 of prior canonical record, or all-zero for seq 0
}
```

- Each line is a canonical JSON serialization of one `EventRecord`.
- `prev_hash` chains records so log tampering invalidates every subsequent
  record's hash.

### 3.1 Appending

```
append_event(log, event):
    seq = log.last_seq + 1
    prev = sha256(canonical_bytes(log.last_record)) if log.not_empty else all_zero
    record = EventRecord { seq, at_clocks: current_clocks, event, prev_hash: prev }
    line = canonical_json(record)
    fsync_write(log.file, line + "\n")
    log.last_record = record
    log.last_seq = seq
```

- `fsync_write` is an atomic write: the Host MUST either completely persist
  the record or leave the log unchanged on crash. Partial writes are a
  fatal persistence error (the tail is truncated on startup after
  verification).

### 3.2 log_hash

```
log_hash(log) -> Sha256:
    h = Sha256::new()
    for record in log.records:
        h.update(canonical_bytes(record))
        h.update(b"\n")
    return h.finalize()
```

The golden conformance suite compares this hash against expected values.

### Invariant PS3 (Hash stability)
`log_hash` depends only on the byte content of the canonical records. It
does NOT depend on file system metadata, record timestamps, or write
ordering within an atomic batch.

## 4. Snapshots

```
Snapshot = {
    version: SemVer,
    session_id: SessionId,
    state: SessionState,
    taken_after_seq: u64,              // log record seq count at time of snapshot
    log_hash_at_snapshot: Sha256,
}
```

### 4.1 Taking a snapshot

```
take_snapshot(state, log) -> Option<Snapshot>:
    if not can_save(state):
        return None
    return Snapshot {
        version: schema_version,
        session_id: state.session_id,
        state: state,
        taken_after_seq: log.last_seq,
        log_hash_at_snapshot: log_hash(log),
    }
```

### 4.2 Restoring a snapshot

```
restore_session(snapshot, log) -> SessionState:
    verify snapshot.version in engine.supported_range
    verify log_hash(log up to snapshot.taken_after_seq) == snapshot.log_hash_at_snapshot
    state = snapshot.state
    // Replay remaining log records beyond the snapshot.
    for record in log.records_after(snapshot.taken_after_seq):
        state = replay_step(state, record)
    return state
```

### Invariant PS4 (Snapshot round-trip)
`take_snapshot` followed by canonical serialization and deserialization
yields a `SessionState` byte-identical to the original under canonical
JSON.

## 5. Replay

Replay is the core conformance oracle. Replay feeds `DriverResponse`
records from the log back into the Engine without invoking a real Driver.

```
replay(initial_snapshot, log) -> EventLog:
    state = initial_snapshot.state
    output_log = EventLog::empty()
    let mut record_iter = log.records.iter()
    let mut input = StepInput::Start
    loop:
        transition = step(state, input)
        match transition:
            Yielded(events, next):
                output_log.append_all(events)
                state = next
                input = StepInput::Start
            NeedsDecision(req, cont, next):
                output_log.append(DriverRequestEmitted { req.id, req })
                // Consume the matching driver-response record from the log
                response_record = record_iter.next_matching(DriverResponseRecorded, req.id)
                output_log.append(response_record.event)
                state = next.with_continuation(cont)
                input = StepInput::DriverResponse(req.id, response_record.event.response)
            Terminal(events):
                output_log.append_all(events)
                return output_log
            Errored(err, recoverable, next):
                output_log.append(Error(err))
                state = next
                if !recoverable: return output_log
                input = StepInput::Start
```

### Invariant PS5 (Replay equivalence)
For any `(initial_snapshot, log)` produced by a conformant Engine,
`replay(initial_snapshot, log)` yields an `EventLog` whose canonical
serialization is byte-identical to the input `log`. Replay is a pure
function; failure is the Engine's bug, not replay's.

## 6. Tick Replay

Tick inputs are not driver responses; they are Host decisions. For replay,
ticks are embedded in the Event Log as `ClockTicked` records. Replay reads
`ClockTicked` entries and emits synthetic `Tick(clock_tag)` inputs into
the Engine when it encounters one. The Engine's reaction must produce the
same `ClockTicked` record plus whatever follow-on events the tick caused.

### Invariant PS6 (Tick determinism)
Between two `ClockTicked` records in the log, replay feeds exactly the
Driver responses recorded in that interval. No extra inputs are allowed.

## 7. Integrity Verification

```
integrity_verify(log) -> Result<(), IntegrityError>:
    prev = all_zero
    for record in log.records:
        check record.seq is monotonic
        check record.prev_hash == prev
        prev = sha256(canonical_bytes(record))
    return Ok
```

Any mismatch is `FatalError::CorruptedState`. The Host SHOULD run
`integrity_verify` on session open.

## 8. Error Records in the Log

`Errored` transitions produce `Error` records (see `01-execution-model.md`
Section 6.1 and 6.2). These are first-class log entries; replay must
reproduce them. A conformant Engine that replays a log containing an error
MUST produce that error at the same seq.

## 9. Performance Notes (non-normative)

- Logs grow linearly; snapshots bound replay cost. Hosts should snapshot
  at every scene transition for responsive resume.
- `log_hash` is streamable; Hosts can maintain a running hash incrementally
  as records are appended.
- Canonical serialization is not the fastest; Hosts may use alternate
  in-memory representations during stepping and canonicalize only at
  append time.
