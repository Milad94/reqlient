"""
Pytest configuration and shared fixtures for reqlient tests.
"""

import json as _json
import logging
from unittest.mock import MagicMock

import httpx
import pytest
import respx
from pydantic import BaseModel

from reqlient.async_.bulkhead import AsyncBulkheadRegistry
from reqlient.async_.circuit_breakers import AsyncCircuitBreakerRegistry
from reqlient.core.config import CircuitBreakerConfig, RetryConfig
from reqlient.sync.bulkhead import BulkheadRegistry
from reqlient.sync.circuit_breakers import CircuitBreakerRegistry
from reqlient.sync.rest_client import RestClient

# ---------------------------------------------------------------------------
# requests_mock -> respx compatibility shim
#
# The sync client now uses httpx instead of requests, so the test suite mocks
# HTTP with respx. To avoid rewriting the ~60 existing call sites, this thin
# adapter exposes the small slice of the requests_mock API the tests rely on
# (get/post/put/patch/delete with json/status_code/headers/text, a list of
# sequential responses with optional {"exc": ...}, plus last_request and
# request_history) on top of a respx router.
# ---------------------------------------------------------------------------


class _RecordedRequest:
    """Wraps an httpx.Request to mirror the bits of requests_mock's request API."""

    def __init__(self, request: httpx.Request):
        self._request = request
        self.url = str(request.url)
        self.headers = request.headers

    def json(self):
        return _json.loads(self._request.content)


def _build_response(spec: dict):
    """Build an httpx.Response (or return an Exception to raise) from a spec dict."""
    exc = spec.get("exc")
    if exc is not None:
        return exc
    kwargs = {"status_code": spec.get("status_code", 200)}
    if spec.get("json") is not None:
        kwargs["json"] = spec["json"]
    if spec.get("text") is not None:
        kwargs["text"] = spec["text"]
    if spec.get("headers"):
        kwargs["headers"] = spec["headers"]
    return httpx.Response(**kwargs)


def _sequential_side_effect(specs):
    """Return a respx side_effect that walks `specs`, repeating the last (like requests_mock)."""
    responses = [_build_response(s) for s in specs]
    state = {"i": 0}

    def _next(request, *args, **kwargs):
        i = state["i"]
        if i < len(responses) - 1:
            state["i"] = i + 1
        item = responses[i]
        if isinstance(item, Exception):
            raise item
        return item

    return _next


class _RespxAdapter:
    def __init__(self, router: respx.MockRouter):
        self._router = router

    def _register(self, method, url, response_list=None, **kwargs):
        route = getattr(self._router, method.lower())(url)
        if response_list is not None:
            route.mock(side_effect=_sequential_side_effect(response_list))
        else:
            built = _build_response(kwargs)
            if isinstance(built, Exception):
                route.mock(side_effect=built)
            else:
                route.mock(return_value=built)
        return route

    def get(self, url, response_list=None, **kwargs):
        return self._register("GET", url, response_list, **kwargs)

    def post(self, url, response_list=None, **kwargs):
        return self._register("POST", url, response_list, **kwargs)

    def put(self, url, response_list=None, **kwargs):
        return self._register("PUT", url, response_list, **kwargs)

    def patch(self, url, response_list=None, **kwargs):
        return self._register("PATCH", url, response_list, **kwargs)

    def delete(self, url, response_list=None, **kwargs):
        return self._register("DELETE", url, response_list, **kwargs)

    @property
    def last_request(self):
        return _RecordedRequest(self._router.calls.last.request)

    @property
    def request_history(self):
        return [_RecordedRequest(call.request) for call in self._router.calls]

# Disable logging during tests unless explicitly needed
logging.getLogger().setLevel(logging.CRITICAL)


# Pydantic models for testing
class User(BaseModel):
    id: int
    name: str
    email: str


class CreateUserRequest(BaseModel):
    name: str
    email: str


class UserResponse(BaseModel):
    id: int
    name: str
    email: str
    created_at: str = "2024-01-01T00:00:00Z"


class ErrorResponse(BaseModel):
    detail: str
    code: str = "error"


@pytest.fixture
def user_model():
    """Sample user model for testing."""
    return User(id=1, name="John Doe", email="john@example.com")


@pytest.fixture
def create_user_request():
    """Sample create user request model."""
    return CreateUserRequest(name="Jane Doe", email="jane@example.com")


@pytest.fixture
def base_url():
    """Base URL for test API."""
    return "https://api.example.com/v1"


@pytest.fixture
def mock_logger():
    """Mock logger for testing."""
    logger = MagicMock(spec=logging.Logger)
    return logger


@pytest.fixture
def circuit_breaker():
    """Circuit breaker for testing (in-memory fallback)."""
    # Reset registry for clean state between tests
    CircuitBreakerRegistry.reset()
    return CircuitBreakerRegistry.get(service_name="test_service", fail_max=3, reset_timeout=5)


@pytest.fixture
def basic_client(base_url, mock_logger):
    """Basic RestClient instance without optional features."""
    CircuitBreakerRegistry.reset()
    return RestClient(
        base_url=base_url,
        service_name="test_service",
        logger=mock_logger,
        retry=RetryConfig(max_retries=2),
        circuit_breaker=None,  # Disable circuit breaker for basic tests
    )


@pytest.fixture
def full_featured_client(base_url, mock_logger, circuit_breaker):
    """RestClient instance with all features enabled.

    The ``circuit_breaker`` fixture pre-registers a breaker for "test_service";
    the client resolves that same shared instance from the registry.
    """
    return RestClient(
        base_url=base_url,
        service_name="test_service",
        logger=mock_logger,
        retry=RetryConfig(max_retries=3),
        circuit_breaker=CircuitBreakerConfig(fail_max=3, reset_timeout=5),
    )


@pytest.fixture
def requests_mock():
    """Provide an httpx (respx-backed) HTTP mock with a requests_mock-like API."""
    with respx.mock(assert_all_called=False) as router:
        yield _RespxAdapter(router)


@pytest.fixture
def mock_metrics_service(monkeypatch):
    """Mock MetricsService to avoid import errors."""
    mock_service = MagicMock()
    mock_service.record_metrics = MagicMock()

    # Mock the import
    import sys

    mock_module = MagicMock()
    mock_module.MetricsService = mock_service
    sys.modules["apps.utility.monitoring.monitoring_service"] = mock_module

    return mock_service


@pytest.fixture(autouse=True)
def reset_circuit_breaker_registries():
    """Reset sync/async circuit breaker and bulkhead registries before each test."""
    CircuitBreakerRegistry.reset()
    AsyncCircuitBreakerRegistry.reset()
    BulkheadRegistry.reset()
    AsyncBulkheadRegistry.reset()
    yield
    # Also reset after test to ensure clean state
    CircuitBreakerRegistry.reset()
    AsyncCircuitBreakerRegistry.reset()
    BulkheadRegistry.reset()
    AsyncBulkheadRegistry.reset()
