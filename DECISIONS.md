# Decisions

## Safe codec and registration

The safe snapshot boundary uses a closed JSON value domain, a complete field map,
and a registered concrete model identity. Registration validates field codecs and
router migration policy without opening a database connection. A missing or
incompatible field, custom model `from_db()`, or router policy rejects registration.
Codec identity is the codec class plus canonical configuration, so equivalent
fresh codec instances are idempotent while differently configured instances fail.
Registered codecs are wrapped with their verified identity and fail closed if their
configuration changes. Mutable or hidden codec configuration, including a mutable
value nested inside class configuration, is rejected.

Reachable related deletion policies must use a known Django policy and share the
registered model's static write and migration alias. Forward foreign-key owners and
reverse relations are both checked. This prevents cross-alias related deletion from
becoming an unowned revision path without excluding valid local owner relations.

Rejected: serializing model instances or querysets. Their object behavior and
Django-version coupling make a closed data boundary impossible.

Rejected: a partial registry before codec validation. It would admit models that a
later codec phase must invalidate, making registration state unreliable.

See docs/adr/001_safe_codec_registry.md.
