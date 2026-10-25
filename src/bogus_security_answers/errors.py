"""Errors whose messages are safe to show in the window."""


class AppError(Exception):
    """Base error with a message that can be shown to the user."""


class PasswordError(AppError):
    """The master password does not meet the rules."""


class UnlockError(AppError):
    """The vault could not be opened."""


class VaultError(AppError):
    """The vault file could not be read or written."""


class ValidationError(AppError):
    """A field the user entered cannot be saved."""


class CollisionError(AppError):
    """An answer is already used in the same question group."""

    def __init__(self, answer: str, canonical_key: str) -> None:
        self.answer = answer
        self.canonical_key = canonical_key
        super().__init__(
            f"The answer '{answer}' is already used for the '{canonical_key}' question group."
        )


class UniquenessExhausted(AppError):
    """Faker could not find an unused answer in the allowed number of tries."""

    def __init__(self) -> None:
        super().__init__(
            "Couldn't find a unique answer; try again or enter one manually."
        )


class ProviderError(AppError):
    """The AI provider could not be reached or returned an unusable response."""
