"""
Pytest configuration and shared fixtures for reqlient tests.
"""

import logging
from unittest.mock import MagicMock

import pytest
import requests_mock as requests_mock_module
from pydantic import BaseModel

from reqlient.sync.circuit_breakers import CircuitBreakerRegistry
from reqlient.async_.circuit_breakers import AsyncCircuitBreakerRegistry
from reqlient.sync.rest_client import RestClient

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
        max_retries=2,
        use_circuit_breaker=False,  # Disable auto circuit breaker for basic tests
    )


@pytest.fixture
def full_featured_client(base_url, mock_logger, circuit_breaker):
    """RestClient instance with all features enabled."""
    return RestClient(
        base_url=base_url,
        service_name="test_service",
        logger=mock_logger,
        breaker=circuit_breaker,
        max_retries=3,
    )


@pytest.fixture
def requests_mock():
    """Provide requests_mock fixture for HTTP mocking."""
    with requests_mock_module.Mocker() as m:
        yield m


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
    """Reset both sync and async circuit breaker registries before each test."""
    CircuitBreakerRegistry.reset()
    AsyncCircuitBreakerRegistry.reset()
    yield
    # Also reset after test to ensure clean state
    CircuitBreakerRegistry.reset()
    AsyncCircuitBreakerRegistry.reset()
