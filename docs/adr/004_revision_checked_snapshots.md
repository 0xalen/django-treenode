# ADR 004: Revision-checked tree snapshots

## Decision

An active registered tree model can opt into a dedicated SafeMemoryCache or
SafeRedisCache alias. Reads first obtain the authoritative database revision and
then read only the immutable key containing the model identity, alias, schema
fingerprint, and revision.

On a miss, PostgreSQL uses a READ COMMITTED transaction and SQLite uses BEGIN
IMMEDIATE to acquire rows and verify the same revision. A bounded rebuild publishes
only complete codec bytes with add-if-absent. Active transactions and any cache,
decode, or rebuild failure return database rows without cache mutation.

## Rejected approaches

Deleting malformed data was rejected because it can delete a concurrent repair.
Replacing a snapshot key was rejected because a stale builder can overwrite a newer
publication. Falling back to the legacy cache was rejected because it can select a
pickle serializer outside the safe transport boundary.

## Consequences

Old revision keys expire naturally. Cache publication is never required for commit
success. Legacy cache behavior is unchanged unless the explicit safe alias is used
for an active registered model.
