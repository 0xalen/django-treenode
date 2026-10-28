# ADR 003: Safe cache transports

## Decision

SafeMemoryCache and SafeRedisCache are Django BaseCache backends. They accept only
the documented bounded primitive domain. Non-counter data uses a versioned tagged
JSON byte encoding with strict decoding. Integer counters use signed 64-bit decimal
bytes so Redis INCRBY remains native and atomic.

Redis keys begin with a configured namespace. Clear scans only that namespace and
does not flush a database shared by cache aliases. Missing and stored None remain
distinct. Decode failures are cache misses and do not delete the stored bytes.

## Rejected approaches

Pickle or Django's default cache serializers were rejected because loading stored
data can execute arbitrary object behavior. Redis database flush was rejected
because it removes keys belonging to unrelated aliases. Application-side read and
write counter arithmetic was rejected because it loses Redis atomicity.

## Consequences

SafeMemoryCache is process-local. SafeRedisCache requires the optional redis-py
dependency and propagates connection errors. A later revision-checked tree cache
may use these transports but is not enabled by them.
