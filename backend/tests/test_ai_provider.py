from datetime import datetime, timezone

import anthropic
import httpx2
import openai
import pytest
from google.genai import errors as genai_errors

import app.services.ai.provider as provider_module
from app.models.event import Event
from app.services.ai.analysis import InvestigationAnalysis
from app.services.ai.context import build_investigation_context
from app.services.ai.provider import (
    AIProviderError,
    AnthropicProvider,
    GeminiProvider,
    OpenAIProvider,
    get_ai_provider,
)

# These tests never make a real network call or require a paid API key -
# the underlying SDK client's parse method is always replaced with a fake
# before `generate()` is invoked.


class _FakeParseResponse:
    def __init__(self, parsed_output):
        self.parsed_output = parsed_output


class _FakeMessagesNamespace:
    def __init__(self, parse_fn):
        self.parse = parse_fn


class _FakeAnthropicClient:
    def __init__(self, parse_fn):
        self.messages = _FakeMessagesNamespace(parse_fn)


class _FakeOpenAIParseResponse:
    def __init__(self, output_parsed):
        self.output_parsed = output_parsed


class _FakeResponsesNamespace:
    def __init__(self, parse_fn):
        self.parse = parse_fn


class _FakeOpenAIClient:
    def __init__(self, parse_fn):
        self.responses = _FakeResponsesNamespace(parse_fn)


class _FakeGeminiResponse:
    def __init__(self, parsed):
        self.parsed = parsed


class _FakeGeminiModelsNamespace:
    def __init__(self, generate_content_fn):
        self.generate_content = generate_content_fn


class _FakeGeminiClient:
    def __init__(self, generate_content_fn):
        self.models = _FakeGeminiModelsNamespace(generate_content_fn)


@pytest.fixture(autouse=True)
def _fast_genai_client_constructor(monkeypatch):
    """genai.Client(api_key=...) construction is consistently slow in this
    environment (~2s per call - unrelated to network I/O; reproduced with
    an obviously fake key and no assertions about connectivity, so it is
    the SDK's own client-construction path, not something Nextrace code
    does). GeminiProvider.__init__ still calls the real constructor to
    exercise its actual config-resolution logic, but every test then
    replaces `provider._client` with a fake anyway (identical to the
    Anthropic/OpenAI provider tests above) - so patching the constructor
    itself to something instantaneous costs no coverage, only wall-clock
    time, and keeps this test file from taking ~40s longer than it needs
    to for no correctness benefit.
    """
    monkeypatch.setattr(
        provider_module.genai,
        "Client",
        lambda **kwargs: _FakeGeminiClient(lambda **kw: _FakeGeminiResponse(None)),
    )


def _event(event_id, event_type="incident"):
    event = Event(
        service="payment-service",
        environment="production",
        event_type=event_type,
        timestamp=datetime.now(timezone.utc),
        severity="info",
        source="application",
        message="msg",
        event_metadata=None,
        created_at=datetime.now(timezone.utc),
    )
    event.id = event_id
    return event


def _sample_context():
    target = _event(1)
    return build_investigation_context(target, [target], [], [])


def _fake_request() -> httpx2.Request:
    return httpx2.Request("POST", "https://api.anthropic.com/v1/messages")


def _fake_openai_request() -> httpx2.Request:
    return httpx2.Request("POST", "https://api.openai.com/v1/responses")


# --- Configuration ---


def test_provider_raises_when_api_key_missing(monkeypatch):
    monkeypatch.delenv("AI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    with pytest.raises(AIProviderError):
        AnthropicProvider()


def test_provider_accepts_explicit_api_key():
    provider = AnthropicProvider(api_key="test-key")
    assert provider.model  # a default model was resolved


def test_provider_uses_configured_model_env_var(monkeypatch):
    monkeypatch.setenv("AI_MODEL", "claude-sonnet-5")
    provider = AnthropicProvider(api_key="test-key")
    assert provider.model == "claude-sonnet-5"


def test_get_ai_provider_rejects_unsupported_provider_name(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "some-other-vendor")
    monkeypatch.setenv("AI_API_KEY", "test-key")

    with pytest.raises(AIProviderError):
        get_ai_provider()


def test_get_ai_provider_raises_when_unconfigured(monkeypatch):
    monkeypatch.delenv("AI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("AI_PROVIDER", raising=False)

    with pytest.raises(AIProviderError):
        get_ai_provider()


def test_get_ai_provider_selects_anthropic_by_default(monkeypatch):
    monkeypatch.delenv("AI_PROVIDER", raising=False)
    monkeypatch.setenv("AI_API_KEY", "test-key")

    provider = get_ai_provider()

    assert isinstance(provider, AnthropicProvider)


def test_get_ai_provider_selects_anthropic_case_insensitively(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "Anthropic")
    monkeypatch.setenv("AI_API_KEY", "test-key")

    provider = get_ai_provider()

    assert isinstance(provider, AnthropicProvider)


def test_get_ai_provider_selects_openai(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "openai")
    monkeypatch.setenv("AI_API_KEY", "test-key")

    provider = get_ai_provider()

    assert isinstance(provider, OpenAIProvider)


def test_get_ai_provider_selects_openai_case_insensitively(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "OpenAI")
    monkeypatch.setenv("AI_API_KEY", "test-key")

    provider = get_ai_provider()

    assert isinstance(provider, OpenAIProvider)


def test_get_ai_provider_selects_gemini(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "gemini")
    monkeypatch.setenv("AI_API_KEY", "test-key")

    provider = get_ai_provider()

    assert isinstance(provider, GeminiProvider)


def test_get_ai_provider_selects_gemini_case_insensitively(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "Gemini")
    monkeypatch.setenv("AI_API_KEY", "test-key")

    provider = get_ai_provider()

    assert isinstance(provider, GeminiProvider)


def test_blank_ai_provider_env_var_falls_back_to_default(monkeypatch):
    # A leftover "AI_PROVIDER=" line in .env should behave like it was
    # never set, not like an explicit invalid provider name.
    monkeypatch.setenv("AI_PROVIDER", "   ")
    monkeypatch.setenv("AI_API_KEY", "test-key")

    provider = get_ai_provider()

    assert isinstance(provider, AnthropicProvider)


def test_blank_api_key_env_var_is_treated_as_unconfigured(monkeypatch):
    monkeypatch.setenv("AI_API_KEY", "   ")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    with pytest.raises(AIProviderError):
        AnthropicProvider()


def test_blank_model_env_var_falls_back_to_default_model(monkeypatch):
    monkeypatch.setenv("AI_MODEL", "   ")

    provider = AnthropicProvider(api_key="test-key")

    assert provider.model  # resolved to a real default, not an empty string
    assert provider.model.strip() == provider.model


# --- Successful generation ---


def test_provider_returns_parsed_output_on_success():
    provider = AnthropicProvider(api_key="test-key")
    expected = InvestigationAnalysis(summary="A deployment preceded the incident window.")

    provider._client = _FakeAnthropicClient(lambda **kwargs: _FakeParseResponse(expected))

    result = provider.generate(_sample_context())

    assert result == expected


# --- Provider failure handling ---


def test_provider_wraps_timeout_error():
    provider = AnthropicProvider(api_key="test-key")

    def raise_timeout(**kwargs):
        raise anthropic.APITimeoutError(_fake_request())

    provider._client = _FakeAnthropicClient(raise_timeout)

    with pytest.raises(AIProviderError):
        provider.generate(_sample_context())


def test_provider_wraps_authentication_error():
    provider = AnthropicProvider(api_key="test-key")

    def raise_auth_error(**kwargs):
        response = httpx2.Response(401, request=_fake_request())
        raise anthropic.AuthenticationError("invalid x-api-key", response=response, body=None)

    provider._client = _FakeAnthropicClient(raise_auth_error)

    with pytest.raises(AIProviderError):
        provider.generate(_sample_context())


def test_provider_wraps_rate_limit_error():
    provider = AnthropicProvider(api_key="test-key")

    def raise_rate_limit(**kwargs):
        response = httpx2.Response(429, request=_fake_request())
        raise anthropic.RateLimitError("rate limited", response=response, body=None)

    provider._client = _FakeAnthropicClient(raise_rate_limit)

    with pytest.raises(AIProviderError):
        provider.generate(_sample_context())


def test_provider_wraps_connection_error():
    provider = AnthropicProvider(api_key="test-key")

    def raise_connection_error(**kwargs):
        raise anthropic.APIConnectionError(request=_fake_request())

    provider._client = _FakeAnthropicClient(raise_connection_error)

    with pytest.raises(AIProviderError):
        provider.generate(_sample_context())


def test_provider_wraps_generic_status_error():
    provider = AnthropicProvider(api_key="test-key")

    def raise_status_error(**kwargs):
        response = httpx2.Response(500, request=_fake_request())
        raise anthropic.APIStatusError("server error", response=response, body=None)

    provider._client = _FakeAnthropicClient(raise_status_error)

    with pytest.raises(AIProviderError):
        provider.generate(_sample_context())


def test_provider_error_messages_never_contain_the_api_key():
    provider = AnthropicProvider(api_key="super-secret-key-value")

    def raise_auth_error(**kwargs):
        response = httpx2.Response(401, request=_fake_request())
        raise anthropic.AuthenticationError(
            "invalid x-api-key: super-secret-key-value", response=response, body=None
        )

    provider._client = _FakeAnthropicClient(raise_auth_error)

    with pytest.raises(AIProviderError) as exc_info:
        provider.generate(_sample_context())

    assert "super-secret-key-value" not in str(exc_info.value)


def test_provider_raises_when_response_cannot_be_parsed():
    provider = AnthropicProvider(api_key="test-key")

    provider._client = _FakeAnthropicClient(lambda **kwargs: _FakeParseResponse(None))

    with pytest.raises(AIProviderError):
        provider.generate(_sample_context())


def test_provider_wraps_unexpected_exception():
    provider = AnthropicProvider(api_key="test-key")

    def raise_unexpected(**kwargs):
        raise ValueError("something unrelated broke")

    provider._client = _FakeAnthropicClient(raise_unexpected)

    with pytest.raises(AIProviderError):
        provider.generate(_sample_context())


# =========================================================================
# OpenAIProvider - mirrors the AnthropicProvider tests above exactly, using
# a fake `responses.parse` (never a real network call or paid API key).
# =========================================================================


# --- Configuration ---


def test_openai_provider_raises_when_api_key_missing(monkeypatch):
    monkeypatch.delenv("AI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    with pytest.raises(AIProviderError):
        OpenAIProvider()


def test_openai_provider_accepts_explicit_api_key():
    provider = OpenAIProvider(api_key="test-key")
    assert provider.model  # a default model was resolved


def test_openai_provider_falls_back_to_openai_api_key_env_var(monkeypatch):
    monkeypatch.delenv("AI_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    provider = OpenAIProvider()

    assert provider.model


def test_openai_provider_uses_configured_model_env_var(monkeypatch):
    monkeypatch.setenv("AI_MODEL", "gpt-5.6-sol")
    provider = OpenAIProvider(api_key="test-key")
    assert provider.model == "gpt-5.6-sol"


def test_openai_blank_api_key_env_var_is_treated_as_unconfigured(monkeypatch):
    monkeypatch.setenv("AI_API_KEY", "   ")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    with pytest.raises(AIProviderError):
        OpenAIProvider()


def test_openai_blank_model_env_var_falls_back_to_default_model(monkeypatch):
    monkeypatch.setenv("AI_MODEL", "   ")

    provider = OpenAIProvider(api_key="test-key")

    assert provider.model  # resolved to a real default, not an empty string
    assert provider.model.strip() == provider.model


# --- Successful generation ---


def test_openai_provider_returns_parsed_output_on_success():
    provider = OpenAIProvider(api_key="test-key")
    expected = InvestigationAnalysis(summary="A deployment preceded the incident window.")

    provider._client = _FakeOpenAIClient(lambda **kwargs: _FakeOpenAIParseResponse(expected))

    result = provider.generate(_sample_context())

    assert result == expected


def test_openai_provider_uses_structured_output_not_free_form_text():
    # The provider must request structured output via text_format=
    # InvestigationAnalysis - never ask the model to return arbitrary JSON
    # embedded in prose that the application then parses by hand.
    provider = OpenAIProvider(api_key="test-key")
    captured_kwargs = {}

    def capture_and_respond(**kwargs):
        captured_kwargs.update(kwargs)
        return _FakeOpenAIParseResponse(InvestigationAnalysis(summary="..."))

    provider._client = _FakeOpenAIClient(capture_and_respond)

    provider.generate(_sample_context())

    assert captured_kwargs["text_format"] is InvestigationAnalysis
    assert captured_kwargs["model"] == provider.model
    assert isinstance(captured_kwargs["input"], list)


# --- Provider failure handling ---


def test_openai_provider_wraps_timeout_error():
    provider = OpenAIProvider(api_key="test-key")

    def raise_timeout(**kwargs):
        raise openai.APITimeoutError(_fake_openai_request())

    provider._client = _FakeOpenAIClient(raise_timeout)

    with pytest.raises(AIProviderError):
        provider.generate(_sample_context())


def test_openai_provider_wraps_authentication_error():
    provider = OpenAIProvider(api_key="test-key")

    def raise_auth_error(**kwargs):
        response = httpx2.Response(401, request=_fake_openai_request())
        raise openai.AuthenticationError("invalid api key", response=response, body=None)

    provider._client = _FakeOpenAIClient(raise_auth_error)

    with pytest.raises(AIProviderError):
        provider.generate(_sample_context())


def test_openai_provider_wraps_rate_limit_error():
    provider = OpenAIProvider(api_key="test-key")

    def raise_rate_limit(**kwargs):
        response = httpx2.Response(429, request=_fake_openai_request())
        raise openai.RateLimitError("rate limited", response=response, body=None)

    provider._client = _FakeOpenAIClient(raise_rate_limit)

    with pytest.raises(AIProviderError):
        provider.generate(_sample_context())


def test_openai_provider_wraps_connection_error():
    provider = OpenAIProvider(api_key="test-key")

    def raise_connection_error(**kwargs):
        raise openai.APIConnectionError(request=_fake_openai_request())

    provider._client = _FakeOpenAIClient(raise_connection_error)

    with pytest.raises(AIProviderError):
        provider.generate(_sample_context())


def test_openai_provider_wraps_generic_status_error():
    provider = OpenAIProvider(api_key="test-key")

    def raise_status_error(**kwargs):
        response = httpx2.Response(500, request=_fake_openai_request())
        raise openai.APIStatusError("server error", response=response, body=None)

    provider._client = _FakeOpenAIClient(raise_status_error)

    with pytest.raises(AIProviderError):
        provider.generate(_sample_context())


def test_openai_provider_error_messages_never_contain_the_api_key():
    provider = OpenAIProvider(api_key="super-secret-openai-key-value")

    def raise_auth_error(**kwargs):
        response = httpx2.Response(401, request=_fake_openai_request())
        raise openai.AuthenticationError(
            "invalid api key: super-secret-openai-key-value", response=response, body=None
        )

    provider._client = _FakeOpenAIClient(raise_auth_error)

    with pytest.raises(AIProviderError) as exc_info:
        provider.generate(_sample_context())

    assert "super-secret-openai-key-value" not in str(exc_info.value)


def test_openai_provider_raises_when_response_cannot_be_parsed():
    # Covers both a model refusal and any other case where the SDK could
    # not produce a schema-conforming result - output_parsed is None
    # either way, and that must be treated as a failure, never silently
    # papered over with fabricated defaults.
    provider = OpenAIProvider(api_key="test-key")

    provider._client = _FakeOpenAIClient(lambda **kwargs: _FakeOpenAIParseResponse(None))

    with pytest.raises(AIProviderError):
        provider.generate(_sample_context())


def test_openai_provider_wraps_unexpected_exception():
    # Covers network-style/unexpected failures not modeled by a specific
    # openai.* exception class (e.g. a raw socket/timeout error escaping
    # the SDK's own retry logic) - must still become a safe AIProviderError.
    provider = OpenAIProvider(api_key="test-key")

    def raise_unexpected(**kwargs):
        raise ValueError("something unrelated broke")

    provider._client = _FakeOpenAIClient(raise_unexpected)

    with pytest.raises(AIProviderError):
        provider.generate(_sample_context())


# =========================================================================
# GeminiProvider - mirrors the AnthropicProvider/OpenAIProvider tests
# above, using a fake `models.generate_content` (never a real network call
# or paid API key).
# =========================================================================


# --- Configuration ---


def test_gemini_provider_raises_when_api_key_missing(monkeypatch):
    monkeypatch.delenv("AI_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    with pytest.raises(AIProviderError):
        GeminiProvider()


def test_gemini_provider_accepts_explicit_api_key():
    provider = GeminiProvider(api_key="test-key")
    assert provider.model  # a default model was resolved


def test_gemini_provider_falls_back_to_gemini_api_key_env_var(monkeypatch):
    monkeypatch.delenv("AI_API_KEY", raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")

    provider = GeminiProvider()

    assert provider.model


def test_gemini_provider_uses_configured_model_env_var(monkeypatch):
    monkeypatch.setenv("AI_MODEL", "gemini-3.8-pro")
    provider = GeminiProvider(api_key="test-key")
    assert provider.model == "gemini-3.8-pro"


def test_gemini_blank_api_key_env_var_is_treated_as_unconfigured(monkeypatch):
    monkeypatch.setenv("AI_API_KEY", "   ")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    with pytest.raises(AIProviderError):
        GeminiProvider()


def test_gemini_blank_model_env_var_falls_back_to_default_model(monkeypatch):
    monkeypatch.setenv("AI_MODEL", "   ")

    provider = GeminiProvider(api_key="test-key")

    assert provider.model  # resolved to a real default, not an empty string
    assert provider.model.strip() == provider.model


# --- Successful generation ---


def test_gemini_provider_returns_parsed_output_on_success():
    provider = GeminiProvider(api_key="test-key")
    expected = InvestigationAnalysis(summary="A deployment preceded the incident window.")

    provider._client = _FakeGeminiClient(lambda **kwargs: _FakeGeminiResponse(expected))

    result = provider.generate(_sample_context())

    assert result == expected


def test_gemini_provider_uses_structured_output_not_free_form_text():
    # The provider must request structured output via
    # config.response_schema=InvestigationAnalysis - never ask the model
    # to return arbitrary JSON embedded in prose that the application then
    # parses by hand.
    provider = GeminiProvider(api_key="test-key")
    captured_kwargs = {}

    def capture_and_respond(**kwargs):
        captured_kwargs.update(kwargs)
        return _FakeGeminiResponse(InvestigationAnalysis(summary="..."))

    provider._client = _FakeGeminiClient(capture_and_respond)

    provider.generate(_sample_context())

    assert captured_kwargs["model"] == provider.model
    assert isinstance(captured_kwargs["contents"], str)
    config = captured_kwargs["config"]
    assert config.response_schema is InvestigationAnalysis
    assert config.response_mime_type == "application/json"
    assert config.system_instruction  # system prompt was supplied separately from context data


# --- Provider failure handling ---


def test_gemini_provider_wraps_authentication_error():
    provider = GeminiProvider(api_key="test-key")

    def raise_auth_error(**kwargs):
        raise genai_errors.ClientError(401, {"message": "invalid API key"})

    provider._client = _FakeGeminiClient(raise_auth_error)

    with pytest.raises(AIProviderError):
        provider.generate(_sample_context())


def test_gemini_provider_wraps_permission_denied_as_authentication_error():
    provider = GeminiProvider(api_key="test-key")

    def raise_forbidden(**kwargs):
        raise genai_errors.ClientError(403, {"message": "permission denied"})

    provider._client = _FakeGeminiClient(raise_forbidden)

    with pytest.raises(AIProviderError):
        provider.generate(_sample_context())


def test_gemini_provider_wraps_rate_limit_error():
    provider = GeminiProvider(api_key="test-key")

    def raise_rate_limit(**kwargs):
        raise genai_errors.ClientError(429, {"message": "rate limited"})

    provider._client = _FakeGeminiClient(raise_rate_limit)

    with pytest.raises(AIProviderError):
        provider.generate(_sample_context())


def test_gemini_provider_wraps_generic_client_error():
    provider = GeminiProvider(api_key="test-key")

    def raise_bad_request(**kwargs):
        raise genai_errors.ClientError(400, {"message": "invalid request"})

    provider._client = _FakeGeminiClient(raise_bad_request)

    with pytest.raises(AIProviderError):
        provider.generate(_sample_context())


def test_gemini_provider_wraps_server_error():
    provider = GeminiProvider(api_key="test-key")

    def raise_server_error(**kwargs):
        raise genai_errors.ServerError(500, {"message": "internal error"})

    provider._client = _FakeGeminiClient(raise_server_error)

    with pytest.raises(AIProviderError):
        provider.generate(_sample_context())


def test_gemini_provider_wraps_timeout_error():
    provider = GeminiProvider(api_key="test-key")

    def raise_timeout(**kwargs):
        raise httpx2.TimeoutException("timed out")

    provider._client = _FakeGeminiClient(raise_timeout)

    with pytest.raises(AIProviderError):
        provider.generate(_sample_context())


def test_gemini_provider_wraps_connection_error():
    provider = GeminiProvider(api_key="test-key")

    def raise_connection_error(**kwargs):
        raise httpx2.ConnectError("connection refused")

    provider._client = _FakeGeminiClient(raise_connection_error)

    with pytest.raises(AIProviderError):
        provider.generate(_sample_context())


def test_gemini_provider_error_messages_never_contain_the_api_key():
    provider = GeminiProvider(api_key="super-secret-gemini-key-value")

    def raise_auth_error(**kwargs):
        raise genai_errors.ClientError(
            401, {"message": "invalid API key: super-secret-gemini-key-value"}
        )

    provider._client = _FakeGeminiClient(raise_auth_error)

    with pytest.raises(AIProviderError) as exc_info:
        provider.generate(_sample_context())

    assert "super-secret-gemini-key-value" not in str(exc_info.value)


def test_gemini_provider_raises_when_response_cannot_be_parsed():
    # Covers a refusal or any other case the SDK could not produce a
    # schema-conforming result for - .parsed is None either way, and that
    # must be treated as a failure, never silently papered over with
    # fabricated defaults.
    provider = GeminiProvider(api_key="test-key")

    provider._client = _FakeGeminiClient(lambda **kwargs: _FakeGeminiResponse(None))

    with pytest.raises(AIProviderError):
        provider.generate(_sample_context())


def test_gemini_provider_raises_when_response_parsed_is_not_the_expected_type():
    # .parsed can be a BaseModel | dict | Enum | None per the SDK - a raw
    # dict (partial schema conformance, not a real InvestigationAnalysis)
    # must be rejected just like None, never returned as-is.
    provider = GeminiProvider(api_key="test-key")

    provider._client = _FakeGeminiClient(
        lambda **kwargs: _FakeGeminiResponse({"summary": "not a real InvestigationAnalysis"})
    )

    with pytest.raises(AIProviderError):
        provider.generate(_sample_context())


def test_gemini_provider_wraps_unexpected_exception():
    # Covers network-style/unexpected failures not modeled by a specific
    # genai.errors.* exception class - must still become a safe
    # AIProviderError.
    provider = GeminiProvider(api_key="test-key")

    def raise_unexpected(**kwargs):
        raise ValueError("something unrelated broke")

    provider._client = _FakeGeminiClient(raise_unexpected)

    with pytest.raises(AIProviderError):
        provider.generate(_sample_context())
