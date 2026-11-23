"""Provider interface and a bounded Google Gemini Developer API adapter."""

import os
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from time import sleep
from typing import Protocol

DEFAULT_MODEL = "gemini-3.8-flash"
GEMINI_ENDPOINT = "https://generativelanguage.googleapis.com/"
TRANSIENT_CODES = frozenset({408, 429, 500, 502, 503, 504})
MAX_ATTEMPTS = 3


class ProviderError(RuntimeError):
    """A provider configuration, transport, or response failure."""

    def __init__(self, message: str, *, usage: "TokenUsage | None" = None,
                 code: str | None = None) -> None:
        super().__init__(message)
        self.usage = usage
        self.code = code


def require_remote_permission(allow_remote_code: bool) -> None:
    if allow_remote_code is not True:
        raise ValueError("Live investigation requires --allow-remote-code. Use --dry-run to preview locally.")


@dataclass(frozen=True)
class TokenUsage:
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None


@dataclass(frozen=True)
class ProviderReply:
    text: str
    provider: str
    model: str
    usage: TokenUsage | None = None


class LLMProvider(Protocol):
    def generate(
        self, payload: str, *, instruction: str, schema: dict, allow_remote_code: bool,
    ) -> ProviderReply: ...


@dataclass(frozen=True)
class GeminiProvider:
    api_key: str = field(repr=False)
    model: str = DEFAULT_MODEL
    timeout_seconds: int = 30
    before_request: Callable[[], None] | None = field(default=None, repr=False, compare=False)
    additional_secrets: tuple[str, ...] = field(default=(), repr=False, compare=False)

    @classmethod
    def from_environment(cls) -> "GeminiProvider":
        key = os.environ.get("GEMINI_API_KEY", "").strip()
        if not key:
            raise ProviderError("Set GEMINI_API_KEY in your environment for a live investigation.")
        return cls(key, configured_model())

    def __post_init__(self) -> None:
        if not self.api_key.strip():
            raise ProviderError("Set GEMINI_API_KEY in your environment for a live investigation.")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", self.model):
            raise ProviderError("GEMINI_MODEL must contain a valid Gemini model ID.")
        if self.api_key in self.model:
            raise ProviderError("GEMINI_MODEL must not contain the configured credential.")
        if type(self.timeout_seconds) is not int or not 1 <= self.timeout_seconds <= 120:
            raise ValueError("Gemini timeout must be between 1 and 120 seconds.")

    def generate(
        self, payload: str, *, instruction: str, schema: dict, allow_remote_code: bool,
    ) -> ProviderReply:
        config = {
            "system_instruction": instruction, "response_mime_type": "application/json",
            "response_json_schema": schema, "candidate_count": 1, "max_output_tokens": 4096,
            "automatic_function_calling": {"disable": True},
        }
        response = self._request(payload, config, payload, allow_remote_code, MAX_ATTEMPTS)
        text = response.text
        usage = response_usage(response)
        if not text or not text.strip():
            raise ProviderError("Gemini returned no investigation text.", usage=usage)
        if self.contains_credential(text):
            raise ProviderError("Gemini returned text containing the configured credential.", usage=usage)
        return ProviderReply(text, "gemini", self.model, usage)

    def contains_credential(self, text: str) -> bool:
        return any(secret and secret in text for secret in (self.api_key, *self.additional_secrets))

    def _request(self, contents, config: dict, outbound: str, consent: bool, attempts: int):
        """Shared authenticated transport for the two distinct Gemini operations."""
        require_remote_permission(consent)
        if self.contains_credential(outbound):
            raise ProviderError("The approved payload contains the configured Gemini credential.")
        try:
            import httpx
            from google import genai
            from google.genai import errors, types
            from google.genai.client import DebugConfig
        except ImportError:
            raise ProviderError("Install RepoSherlock's [investigation] extra for live Gemini requests.") from None

        try:
            with httpx.Client(timeout=self.timeout_seconds, trust_env=False, follow_redirects=False) as transport:
                with genai.Client(
                    api_key=self.api_key, enterprise=False, debug_config=DebugConfig(client_mode=None),
                    http_options=types.HttpOptions(
                        base_url=GEMINI_ENDPOINT, api_version="v1beta",
                        httpx_client=transport, timeout=self.timeout_seconds * 1000,
                        # SDK retries can log raw exceptions. Retry in this adapter.
                        retry_options=types.HttpRetryOptions(attempts=1),
                    ),
                ) as client:
                    for attempt in range(attempts):
                        if self.before_request is not None:
                            self.before_request()
                        try:
                            return client.models.generate_content(
                                model=self.model, contents=contents, config=config,
                            )
                        except errors.APIError as error:
                            message = _http_error(error.code)
                            code = "provider_quota" if error.code == 429 else "provider_unavailable"
                            if error.code not in TRANSIENT_CODES:
                                code = "provider_auth" if error.code in (401, 403) else "provider_failure"
                                raise ProviderError(message, code=code) from None
                        except httpx.TimeoutException:
                            message = "Gemini request timed out."
                            code = "provider_timeout"
                        except httpx.ConnectError:
                            message = "Could not connect securely to Gemini."
                            code = "provider_unavailable"
                        if attempt + 1 == attempts:
                            suffix = f" Tried {attempts} times." if attempts > 1 else ""
                            raise ProviderError(message + suffix, code=code) from None
                        sleep(0.25 * 2 ** attempt)
        except ProviderError:
            raise
        except Exception:
            # The SDK or HTTP response can carry credentials and source text.
            raise ProviderError("Gemini request failed. Check the network and SDK configuration.") from None


def response_usage(response) -> TokenUsage | None:
    metadata = response.usage_metadata
    return TokenUsage(
        metadata.prompt_token_count, metadata.candidates_token_count, metadata.total_token_count,
    ) if metadata is not None else None


def configured_model() -> str:
    return os.environ.get("GEMINI_MODEL", "").strip() or DEFAULT_MODEL


def _http_error(code: int) -> str:
    if code in (401, 403):
        return f"Gemini rejected the credential or access (HTTP {code})."
    if code == 404:
        return "Gemini model was not found (HTTP 404). Check GEMINI_MODEL for this account."
    if code == 400:
        return "Gemini rejected the request (HTTP 400). Check model support for structured output."
    if code == 429:
        return "Gemini rate limit or quota was reached (HTTP 429)."
    return f"Gemini returned HTTP {code}." if type(code) is int else "Gemini returned a request error."
