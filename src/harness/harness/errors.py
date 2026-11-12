"""Error types for harness configuration loading and validation.

The harness configuration schema is defined in ARCHITECTURE.md section 4 and
implemented in :mod:`harness.config`. Every failure raised while loading or
validating harness configuration derives from :class:`ConfigError`, so
callers can catch a single exception family:

- :class:`ConfigLoadError` — the YAML file itself could not be loaded:
  file not found, unreadable, invalid YAML syntax, or a non-mapping document.
- :class:`ConfigValidationError` — the merged YAML/defaults/env values failed
  model validation (unknown keys, bad types, missing required fields).

:class:`ConfigValidationError` deliberately subclasses :class:`ValueError`
too (the harness config contract requires ``pytest.raises(ValueError)`` to
match validation failures). Plain :class:`ConfigError` covers the load-or-
resolve failures: the fail-fast ``${VAR}`` check for unset environment
variables and the belt-and-braces empty-``api_key`` guard.
"""

__all__ = ["ConfigError", "ConfigLoadError", "ConfigValidationError"]


class ConfigError(Exception):
    """Base error for harness configuration problems.

    Attributes:
        key: dotted config key path the error relates to (e.g. ``"api_key"``,
            ``"router.model"``); for load errors this is the YAML file path.
        message: human-readable description of the problem (also what
            ``str(exc)`` returns).
    """

    def __init__(self, key: str, message: str) -> None:
        self.key = key
        self.message = message
        super().__init__(message)

    def __str__(self) -> str:
        return self.message


class ConfigLoadError(ConfigError):
    """YAML config could not be loaded: missing file, unreadable, or invalid YAML."""


class ConfigValidationError(ConfigError, ValueError):
    """Merged configuration failed model validation.

    Subclasses both :class:`ConfigError` and :class:`ValueError` so that
    ``pytest.raises(ValueError)`` works for harness config validation
    failures (ARCHITECTURE.md section 4 contract).
    """
