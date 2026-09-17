"""AI provider boundary.

InvestigationContext -> AI Provider -> InvestigationAnalysis

Kept intentionally small: one abstract interface, concrete Anthropic,
OpenAI, and Gemini implementations, and a factory that reads
configuration. Credentials never leave the backend - the browser never
talks to the AI provider directly. The rest of Nextrace depends only on
the AIProvider interface, never on any specific vendor SDK directly.
"""

import os
from abc import ABC, abstractmethod

import anthropic
import httpx2
import openai
from google import genai
from google.genai import errors as genai_errors
from google.genai import types as genai_types

from app.services.ai.analysis import InvestigationAnalysis
from app.services.ai.context import InvestigationContext
from app.services.ai.prompts import SYSTEM_PROMPT, build_user_message

AI_PROVIDER_ENV_VAR = "AI_PROVIDER"
AI_API_KEY_ENV_VAR = "AI_API_KEY"
AI_MODEL_ENV_VAR = "AI_MODEL"
DEFAULT_PROVIDER = "anthropic"
DEFAULT_ANTHROPIC_MODEL = "claude-opus-5"
DEFAULT_OPENAI_MODEL = "gpt-6-astra"
DEFAULT_GEMINI_MODEL = "gemini-3.8-flash"

# 401/403-shaped status codes within Gemini's single ClientError class -
# split out only so the AIProviderError message can be as specific as the
# Anthropic/OpenAI providers' (which have dedicated AuthenticationError /
# RateLimitError exception classes; Gemini's SDK bundles all 4xx into one).
_GEMINI_AUTH_STATUS_CODES = (401, 403)
_GEMINI_RATE_LIMIT_STATUS_CODE = 429


class AIProviderError(Exception):
    """Raised whenever the AI layer cannot produce a usable analysis.

    The message is always safe to return to an API client - it never
    contains raw provider exception text, API keys, or other credentials.

    `retryable` classifies the failure for callers that want to retry a
    bounded number of times (see app/services/analysis_history.py) without
    needing to know any vendor-specific SDK exception type: True only for
    failures that are genuinely transient (timeout, connection failure,
    provider 5xx) - never for authentication, configuration, rate-limit, or
    malformed-request failures, which retrying would not fix.
    """

    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


class AIProvider(ABC):
    @abstractmethod
    def generate(self, context: InvestigationContext) -> InvestigationAnalysis:
        raise NotImplementedError


class AnthropicProvider(AIProvider):
    def __init__(self, api_key: str | None = None, model: str | None = None) -> None:
        # `.strip()` matters: an env var present but set to "" or whitespace
        # (e.g. a copied .env.example line left blank) must be treated as
        # "not configured", not as a literal empty/whitespace API key.
        resolved_key = (
            api_key or os.getenv(AI_API_KEY_ENV_VAR) or os.getenv("ANTHROPIC_API_KEY") or ""
        ).strip()
        if not resolved_key:
            raise AIProviderError(
                "AI analysis is not configured: set AI_API_KEY (or ANTHROPIC_API_KEY)."
            )

        # Likewise, AI_MODEL="" must fall back to the default rather than
        # sending an empty model string to the provider.
        resolved_model = (model or os.getenv(AI_MODEL_ENV_VAR) or "").strip()
        self.model = resolved_model or DEFAULT_ANTHROPIC_MODEL
        self._client = anthropic.Anthropic(api_key=resolved_key)

    def generate(self, context: InvestigationContext) -> InvestigationAnalysis:
        try:
            response = self._client.messages.parse(
                model=self.model,
                max_tokens=2048,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": build_user_message(context)}],
                output_format=InvestigationAnalysis,
            )
        except anthropic.APITimeoutError as exc:
            raise AIProviderError("AI provider request timed out.", retryable=True) from exc
        except anthropic.AuthenticationError as exc:
            raise AIProviderError("AI provider rejected the configured credentials.") from exc
        except anthropic.RateLimitError as exc:
            raise AIProviderError("AI provider rate limit exceeded. Try again shortly.") from exc
        except anthropic.APIConnectionError as exc:
            raise AIProviderError("Could not connect to the AI provider.", retryable=True) from exc
        except anthropic.APIStatusError as exc:
            raise AIProviderError(
                f"AI provider returned an error (status {exc.status_code}).",
                retryable=exc.status_code >= 500,
            ) from exc
        except Exception as exc:
            raise AIProviderError("AI provider request failed.") from exc

        parsed = response.parsed_output
        if parsed is None:
            raise AIProviderError("AI provider returned a response that could not be parsed.")

        return parsed


class OpenAIProvider(AIProvider):
    def __init__(self, api_key: str | None = None, model: str | None = None) -> None:
        # Same blank-vs-unset handling as AnthropicProvider - a present but
        # empty/whitespace env var means "not configured", not a literal key.
        resolved_key = (
            api_key or os.getenv(AI_API_KEY_ENV_VAR) or os.getenv("OPENAI_API_KEY") or ""
        ).strip()
        if not resolved_key:
            raise AIProviderError(
                "AI analysis is not configured: set AI_API_KEY (or OPENAI_API_KEY)."
            )

        resolved_model = (model or os.getenv(AI_MODEL_ENV_VAR) or "").strip()
        self.model = resolved_model or DEFAULT_OPENAI_MODEL
        self._client = openai.OpenAI(api_key=resolved_key)

    def generate(self, context: InvestigationContext) -> InvestigationAnalysis:
        try:
            response = self._client.responses.parse(
                model=self.model,
                input=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": build_user_message(context)},
                ],
                text_format=InvestigationAnalysis,
            )
        except openai.APITimeoutError as exc:
            raise AIProviderError("AI provider request timed out.", retryable=True) from exc
        except openai.AuthenticationError as exc:
            raise AIProviderError("AI provider rejected the configured credentials.") from exc
        except openai.RateLimitError as exc:
            raise AIProviderError("AI provider rate limit exceeded. Try again shortly.") from exc
        except openai.APIConnectionError as exc:
            raise AIProviderError("Could not connect to the AI provider.", retryable=True) from exc
        except openai.APIStatusError as exc:
            raise AIProviderError(
                f"AI provider returned an error (status {exc.status_code}).",
                retryable=exc.status_code >= 500,
            ) from exc
        except Exception as exc:
            raise AIProviderError("AI provider request failed.") from exc

        # output_parsed is None both on a refusal and on any other case the
        # SDK could not produce a schema-conforming result - either way,
        # that's an AI provider/analysis failure, not something to silently
        # paper over with fabricated defaults.
        parsed = response.output_parsed
        if parsed is None:
            raise AIProviderError("AI provider returned a response that could not be parsed.")

        return parsed


class GeminiProvider(AIProvider):
    def __init__(self, api_key: str | None = None, model: str | None = None) -> None:
        # Same blank-vs-unset handling as the other providers - a present
        # but empty/whitespace env var means "not configured", not a
        # literal key.
        resolved_key = (
            api_key or os.getenv(AI_API_KEY_ENV_VAR) or os.getenv("GEMINI_API_KEY") or ""
        ).strip()
        if not resolved_key:
            raise AIProviderError(
                "AI analysis is not configured: set AI_API_KEY (or GEMINI_API_KEY)."
            )

        resolved_model = (model or os.getenv(AI_MODEL_ENV_VAR) or "").strip()
        self.model = resolved_model or DEFAULT_GEMINI_MODEL
        self._client = genai.Client(api_key=resolved_key)

    def generate(self, context: InvestigationContext) -> InvestigationAnalysis:
        try:
            response = self._client.models.generate_content(
                model=self.model,
                contents=build_user_message(context),
                config=genai_types.GenerateContentConfig(
                    system_instruction=SYSTEM_PROMPT,
                    response_mime_type="application/json",
                    response_schema=InvestigationAnalysis,
                ),
            )
        except genai_errors.ClientError as exc:
            # All 4xx - never retryable (auth, rate limit, and any other
            # client-side/malformed-request status alike).
            if exc.code in _GEMINI_AUTH_STATUS_CODES:
                raise AIProviderError(
                    "AI provider rejected the configured credentials."
                ) from exc
            if exc.code == _GEMINI_RATE_LIMIT_STATUS_CODE:
                raise AIProviderError(
                    "AI provider rate limit exceeded. Try again shortly."
                ) from exc
            raise AIProviderError(
                f"AI provider returned an error (status {exc.code})."
            ) from exc
        except genai_errors.ServerError as exc:
            # 5xx - e.g. "model is currently experiencing high demand",
            # empirically observed from this exact provider - transient.
            raise AIProviderError(
                f"AI provider returned an error (status {exc.code}).", retryable=True
            ) from exc
        except httpx2.TimeoutException as exc:
            raise AIProviderError("AI provider request timed out.", retryable=True) from exc
        except httpx2.RequestError as exc:
            raise AIProviderError("Could not connect to the AI provider.", retryable=True) from exc
        except genai_errors.APIError as exc:
            raise AIProviderError(
                f"AI provider returned an error (status {exc.code})."
            ) from exc
        except Exception as exc:
            raise AIProviderError("AI provider request failed.") from exc

        # .parsed is only populated when the response conforms to the
        # requested response_schema - anything else (including a refusal
        # or a schema mismatch) leaves it None, which is a failure, never
        # something to paper over with fabricated defaults.
        parsed = response.parsed
        if not isinstance(parsed, InvestigationAnalysis):
            raise AIProviderError("AI provider returned a response that could not be parsed.")

        return parsed


def get_ai_provider() -> AIProvider:
    """Resolve the configured AI provider from environment variables.

    Raises AIProviderError if AI analysis is not configured or the
    configured provider name is unsupported - callers (the API endpoint)
    turn this into a controlled HTTP error rather than a crash.
    """
    # An unset OR blank AI_PROVIDER both mean "use the default", so a
    # leftover "AI_PROVIDER=" line in .env doesn't turn into a confusing
    # "unsupported provider ''" error.
    provider_name = (os.getenv(AI_PROVIDER_ENV_VAR) or "").strip().lower() or DEFAULT_PROVIDER

    if provider_name == "anthropic":
        return AnthropicProvider()

    if provider_name == "openai":
        return OpenAIProvider()

    if provider_name == "gemini":
        return GeminiProvider()

    raise AIProviderError(f"Unsupported AI provider configured: '{provider_name}'.")
