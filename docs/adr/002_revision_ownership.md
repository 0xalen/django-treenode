# ADR 002: Transactional revision ownership

## Decision

Each active registered concrete tree model has one revision row per database alias.
The revision increment, model mutation, and derived tree refresh share one atomic
transaction on the validated alias. Registration is pending until both tables exist;
activation failures remain DB-only and report a SafeActivationError.

SQLite and PostgreSQL increment the revision with one `INSERT ... ON CONFLICT ...
DO UPDATE ... RETURNING` statement. The database serializes concurrent first-row
creation without a retry loop in application code.

Instance write entrypoints check the router alias before SQL. Queryset deletion and
related owner deletion use synchronous signals in Django's delete transaction. For
each registered tree relation affected by an owner delete, the owner instance-hint
route, collector queryset-origin alias, and registered tree alias must agree before
collector field-update SQL runs.
Supported bulk writes use the public tree_mutation context manager.

## Rejected approaches

Post-commit cache invalidation was rejected because a process failure after commit
can leave an accepted stale value. Global signal suppression was rejected for active
mutations because it can skip required ownership work.
Ignoring a related-owner alias mismatch was rejected because Django can apply its
foreign-key field update on the deletion alias without invoking the tree model's
write entrypoint.

## Consequences

Unregistered models preserve existing behavior. Raw SQL, direct bulk updates,
private raw deletion, external writers, and unsupported routing remain outside the
revision ownership contract.
