"""Provider-neutral model request and response gateway."""
from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.request
import urllib.parse
import uuid
from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol, Sequence
from infrastructure.observability import log_event

_logger = logging.getLogger('factory.model_gateway')

MAX_PROVIDER_ROUTES = 16
MAX_RETRIES_PER_ROUTE = 5


class ModelGatewayError(Exception):
    """Sanitized gateway error safe to surface in task results."""

    def __init__(self, message: str, *, retryable: bool = False, route_trace=()):
        super().__init__(message)
        self.retryable = retryable
        self.route_trace = tuple(route_trace)


@dataclass(frozen=True)
class ModelRequest:
    messages: Sequence[Mapping[str, str]]
    model: str | None = None
    max_tokens: int = 900
    timeout: float = 90
    response_format: str | Mapping[str, Any] | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)
    request_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    required_capabilities: frozenset[str] = frozenset()
    preferred_provider: str | None = None
    preferred_model: str | None = None

    def __post_init__(self):
        if self.max_tokens < 1 or not 0 < self.timeout <= 600:
            raise ValueError("model request limits are invalid")


@dataclass(frozen=True)
class ModelResponse:
    content: str
    structured: Any = None
    usage: Mapping[str, Any] = field(default_factory=dict)
    provider: str | None = None
    model: str | None = None
    request_id: str | None = None
    route_trace: tuple[str, ...] = ()


@dataclass(frozen=True)
class ProviderRoute:
    name: str
    provider: ModelProvider
    model: str | None = None
    capabilities: frozenset[str] = frozenset({'*'})
    priority: int = 100
    enabled: bool = True
    max_retries: int | None = None

    def __post_init__(self):
        if not self.name or self.priority < 0:
            raise ValueError("provider route requires a name and non-negative priority")
        if self.max_retries is not None and not 0 <= self.max_retries <= MAX_RETRIES_PER_ROUTE:
            raise ValueError("provider retries must be between 0 and 5")

    def is_available(self) -> bool:
        if not self.enabled:
            return False
        check = getattr(self.provider, 'is_available', None)
        return bool(check()) if callable(check) else True


class ModelProvider(Protocol):
    def generate(self, request: ModelRequest) -> ModelResponse: ...


class ModelGateway:
    """Routes requests across compatible providers with bounded retries/fallback."""

    def __init__(self, provider: ModelProvider | None = None, *, routes=None,
                 max_retries: int = 1, retry_delay: float = 0):
        if not 0 <= max_retries <= MAX_RETRIES_PER_ROUTE or not 0 <= retry_delay <= 30:
            raise ValueError("retry settings exceed configured bounds")
        if provider is not None and routes is not None:
            raise ValueError("pass provider or routes, not both")
        self.routes = tuple(routes or ([ProviderRoute('default', provider)] if provider else ()))
        if len(self.routes) > MAX_PROVIDER_ROUTES:
            raise ValueError("at most 16 provider routes are supported")
        self.max_retries = max_retries
        self.retry_delay = retry_delay

    def complete(self, request: ModelRequest) -> ModelResponse:
        correlation = {key: request.metadata[key] for key in
                       ('tenant_id', 'project_id', 'task_id', 'run_id', 'agent')
                       if key in request.metadata and isinstance(request.metadata[key], (str, int))}
        correlation['request_id'] = request.request_id
        required = frozenset(request.required_capabilities)
        candidates = [route for route in self.routes
                      if route.enabled and (not required or '*' in route.capabilities
                                            or required.issubset(route.capabilities))]
        candidates.sort(key=lambda route: (
            route.name != request.preferred_provider if request.preferred_provider else False,
            route.model != request.preferred_model if request.preferred_model else False,
            route.priority,
        ))
        route_trace = []
        saw_retryable = False
        for route in candidates:
            try:
                if not route.is_available():
                    route_trace.append(route.name + ':unavailable')
                    saw_retryable = True
                    log_event(_logger, logging.WARNING, 'model.route.unavailable',
                              component='model_gateway', provider=route.name,
                              outcome='unavailable', **correlation)
                    continue
            except Exception:
                route_trace.append(route.name + ':availability_error')
                saw_retryable = True
                log_event(_logger, logging.WARNING, 'model.route.availability_failed',
                          component='model_gateway', provider=route.name,
                          outcome='unavailable', **correlation)
                continue
            retries = self.max_retries if route.max_retries is None else route.max_retries
            for attempt in range(retries + 1):
                routed_request = ModelRequest(
                    messages=request.messages, model=route.model or request.model,
                    max_tokens=request.max_tokens, timeout=request.timeout,
                    response_format=request.response_format, metadata=request.metadata,
                    request_id=request.request_id,
                    required_capabilities=request.required_capabilities,
                    preferred_provider=request.preferred_provider,
                    preferred_model=request.preferred_model,
                )
                try:
                    response = route.provider.generate(routed_request)
                    if not isinstance(response, ModelResponse) or not isinstance(response.content, str):
                        raise ModelGatewayError("Provider returned an invalid response")
                    if request.response_format:
                        try:
                            structured = json.loads(response.content)
                        except (json.JSONDecodeError, TypeError) as exc:
                            raise ModelGatewayError("Provider returned invalid structured JSON") from exc
                        if not isinstance(structured, (dict, list)):
                            raise ModelGatewayError("Structured response must be a JSON object or array")
                        response = ModelResponse(
                            content=response.content, structured=structured, usage=response.usage,
                            provider=response.provider, model=response.model,
                            request_id=response.request_id or request.request_id,
                            route_trace=response.route_trace,
                        )
                    route_trace.append(route.name + ':success')
                    log_event(_logger, logging.INFO, 'model.request.completed',
                              component='model_gateway', provider=route.name,
                              model=routed_request.model, outcome='success', **correlation)
                    return ModelResponse(
                        content=response.content, structured=response.structured,
                        usage=response.usage, provider=response.provider or route.name,
                        model=response.model or routed_request.model,
                        request_id=response.request_id or request.request_id,
                        route_trace=tuple(route_trace),
                    )
                except TimeoutError:
                    route_trace.append(route.name + ':timeout')
                    saw_retryable = True
                    log_event(_logger, logging.WARNING, 'model.provider.timeout',
                              component='model_gateway', provider=route.name,
                              attempt=attempt + 1, retryable=True, outcome='timeout', **correlation)
                except ModelGatewayError as exc:
                    route_trace.append(route.name + (':retryable_error' if exc.retryable else ':provider_error'))
                    saw_retryable = saw_retryable or exc.retryable
                    log_event(_logger, logging.WARNING, 'model.provider.failed',
                              component='model_gateway', provider=route.name,
                              attempt=attempt + 1, retryable=exc.retryable,
                              error_type=type(exc).__name__, outcome='provider_error', **correlation)
                    if exc.retryable and attempt < retries:
                        log_event(_logger, logging.WARNING, 'model.request.retry',
                                  component='model_gateway', provider=route.name,
                                  attempt=attempt + 2, retryable=True,
                                  outcome='retry', **correlation)
                        if self.retry_delay:
                            time.sleep(self.retry_delay)
                        continue
                    # A provider-specific failure may be handled by the next route.
                    break
                except Exception as exc:
                    route_trace.append(route.name + ':provider_error')
                    log_event(_logger, logging.ERROR, 'model.provider.failed',
                              component='model_gateway', provider=route.name,
                              attempt=attempt + 1, retryable=False,
                              error_type=type(exc).__name__, outcome='provider_error', **correlation)
                    break
                if attempt >= retries:
                    break
                log_event(_logger, logging.WARNING, 'model.request.retry',
                          component='model_gateway', provider=route.name,
                          attempt=attempt + 2, retryable=True, outcome='retry', **correlation)
                if self.retry_delay:
                    time.sleep(self.retry_delay)
        if not candidates:
            route_trace.append('no_capability_match')
        log_event(_logger, logging.ERROR, 'model.request.exhausted',
                  component='model_gateway', request_id=request.request_id,
                  outcome='failed', retryable=saw_retryable,
                  **{key: value for key, value in correlation.items() if key != 'request_id'})
        raise ModelGatewayError('All configured model providers failed',
                                retryable=saw_retryable, route_trace=route_trace)


class OpenAICompatibleProvider:
    """Configurable HTTP adapter for OpenAI-compatible chat completion APIs."""

    def __init__(self, endpoint: str, *, model: str, api_key_env: str | None = None,
                 api_key: str | None = None, timeout: float = 90,
                 provider_name: str = "openai_compatible"):
        parsed_endpoint = urllib.parse.urlsplit(endpoint)
        if parsed_endpoint.scheme not in {"https", "http"} or not parsed_endpoint.hostname:
            raise ValueError("endpoint must use HTTP or HTTPS")
        if parsed_endpoint.username or parsed_endpoint.password:
            raise ValueError("credentials must be supplied through environment configuration")
        secret_query_keys = {"key", "api_key", "token", "access_token", "authorization"}
        if any(key.lower() in secret_query_keys for key, _ in urllib.parse.parse_qsl(parsed_endpoint.query)):
            raise ValueError("credentials must not be embedded in endpoint URLs")
        if not model.strip():
            raise ValueError("model must not be empty")
        if not 0 < timeout <= 600:
            raise ValueError("provider timeout must be between 0 and 600 seconds")
        self.endpoint = endpoint
        self.model = model
        self.api_key_env = api_key_env
        self.api_key = api_key
        self.timeout = timeout
        self.provider_name = provider_name

    def generate(self, request: ModelRequest) -> ModelResponse:
        payload: dict[str, Any] = {
            "model": request.model or self.model,
            "messages": list(request.messages),
            "max_tokens": request.max_tokens,
        }
        if request.response_format:
            payload["response_format"] = (
                request.response_format if isinstance(request.response_format, dict)
                else {"type": "json_object"}
            )
        headers = {"Content-Type": "application/json", "X-Request-ID": request.request_id}
        api_key = self.api_key or (os.environ.get(self.api_key_env) if self.api_key_env else None)
        if api_key:
            headers["Authorization"] = "Bearer " + api_key
        req = urllib.request.Request(self.endpoint, data=json.dumps(payload).encode("utf-8"),
                                     headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=min(request.timeout, self.timeout)) as result:
                parsed = json.load(result)
        except urllib.error.HTTPError as exc:
            raise ModelGatewayError("Model provider returned HTTP error",
                                    retryable=exc.code == 429 or exc.code >= 500) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise ModelGatewayError("Model provider is unavailable", retryable=True) from exc
        try:
            content = parsed["choices"][0]["message"]["content"]
            if not isinstance(content, str) or not content.strip():
                raise ValueError
            usage = parsed.get("usage") or {}
        except (KeyError, IndexError, TypeError, ValueError, AttributeError) as exc:
            raise ModelGatewayError("Model provider returned an invalid response") from exc
        return ModelResponse(content=content, usage=usage, provider=self.provider_name,
                             model=request.model or self.model, request_id=request.request_id)
