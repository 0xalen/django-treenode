class CacheError(Exception):
    pass


class CircularReferenceError(ValueError):
    pass


class SafeCodecError(ValueError):
    pass


class SafeRegistrationError(ValueError):
    pass


class SafeActivationError(RuntimeError):
    pass


class SafeRoutingError(RuntimeError):
    pass
