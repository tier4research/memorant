"""Context Tuner error types."""

from __future__ import annotations


class RecoveryCorruptionError(Exception):
    """Raised when a recovery record contains corrupt or invalid data.

    Attributes:
        recovery_id: The ID of the corrupt recovery record.
        field: The name of the corrupt field (e.g. 'original_messages').
        original_error: The underlying parse error, if any.
    """

    def __init__(
        self,
        recovery_id: str,
        field: str,
        original_error: Exception | None = None,
    ):
        self.recovery_id = recovery_id
        self.field = field
        self.original_error = original_error
        msg = (
            f"Recovery record {recovery_id!r} has corrupt field {field!r}"
        )
        if original_error:
            msg += f": {original_error}"
        super().__init__(msg)
