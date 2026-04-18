# S10 - Pack Load Failure: Missing SFS

Validates the fatal error path when a pack requires an unregistered SFS
function. Loading a pack whose manifest references `sfs.not.real` causes
the engine to halt immediately with `FatalError::UnknownSfsFunction`. The
log contains a single `Error` record and no further events.

Exercises invariants: E8 (error logging).
