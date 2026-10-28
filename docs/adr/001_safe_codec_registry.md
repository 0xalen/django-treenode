# ADR 001: Closed codec before registration

## Decision

Safe model registration accepts a complete immutable mapping from every persisted
field attname to a validating FieldCodec. The codec defines a bounded versioned JSON
snapshot format and validates model identity, database alias, schema fingerprint,
revision, fields, duplicate JSON members, duplicate primary keys, and parent cycles.
Codec identity includes its class and canonical configuration rather than object
identity, preserving idempotence without accepting configuration changes.

Registration is the only opt-in API. It canonicalizes concrete models and validates
the no-hint write route and migration policy without connecting to a database.
Schema table checks are deferred to revision ownership activation.

## Rejected approaches

Partial registration before field validation was rejected because a later codec
phase would need to invalidate accepted models.

Pickle, model-instance serialization, and payload-directed imports were rejected
because they do not provide a closed safe value boundary.

## Consequences

The codec requires explicit configured limits. Codec registration itself does not
enable a cache or revision ownership. Later integration consumes the codec interface
without changing its registration identity or snapshot format.
