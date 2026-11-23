"""Apply server-side credential masking at the HTTP presentation boundary."""

import os

from reposherlock.api.errors import ApiError
from reposherlock.investigation.redaction import MASK, redact


class Privacy:
    def __init__(self, session_token: str) -> None:
        self.session_token = session_token

    def secrets(self) -> tuple[str, ...]:
        return tuple(value for value in (self.session_token, os.environ.get("GEMINI_API_KEY", "").strip()) if value)

    def text(self, value: str) -> str:
        for secret in self.secrets():
            value = value.replace(secret, MASK)
        return redact(value)

    def data(self, value):
        if isinstance(value, str):
            return self.text(value)
        if isinstance(value, dict):
            return {key: self.data(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return type(value)(self.data(item) for item in value)
        return value

    def reject_credentials(self, *values: str) -> None:
        if any(secret in value for secret in self.secrets() for value in values):
            raise ApiError(422, "credential_in_input", "Remove credentials from the request or evidence and retry.")
        try:
            for value in values:
                value.encode("utf-8")
        except UnicodeError:
            raise ApiError(422, "invalid_request", "Text fields must contain valid Unicode.") from None
