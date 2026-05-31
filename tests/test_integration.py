"""
Integration tests for the full request pipeline.
"""

from unittest.mock import MagicMock

import httpx
import pytest
from pydantic import BaseModel

from reqlient import (
    CircuitBreakerConfig,
    CircuitBreakerOpenError,
    CircuitBreakerRegistry,
    Interceptor,
    ResourceNotFoundError,
    RestClient,
    RetryConfig,
)


class User(BaseModel):
    id: int
    name: str
    email: str


class CreateUserRequest(BaseModel):
    name: str
    email: str


class TestFullPipeline:
    """Test the complete request pipeline with all behaviors."""

    def setup_method(self):
        """Reset registry before each test."""
        CircuitBreakerRegistry.reset()

    def teardown_method(self):
        """Reset registry after each test."""
        CircuitBreakerRegistry.reset()

    def test_successful_request_with_all_features(self, requests_mock):
        """Test a successful request with all features enabled."""
        requests_mock.get(
            "https://api.example.com/v1/users/1",
            json={"id": 1, "name": "John Doe", "email": "john@example.com"},
            status_code=200,
        )

        logger = MagicMock()

        client = RestClient(
            base_url="https://api.example.com/v1",
            service_name="test",
            logger=logger,
            retry=RetryConfig(max_retries=2),
            circuit_breaker=CircuitBreakerConfig(),
        )

        response = client.get("/users/1", response_data_schema=User)

        assert response is not None
        assert response.id == 1
        assert response.name == "John Doe"

    def test_retry_with_circuit_breaker(self, requests_mock):
        """Test retry logic with circuit breaker."""
        # First two requests fail, third succeeds
        requests_mock.get(
            "https://api.example.com/v1/users/1",
            [
                {"status_code": 500},
                {"status_code": 500},
                {
                    "json": {"id": 1, "name": "John", "email": "john@example.com"},
                    "status_code": 200,
                },
            ],
        )


        client = RestClient(
            base_url="https://api.example.com/v1",
            service_name="test",
            logger=MagicMock(),
            retry=RetryConfig(max_retries=2, backoff_factor=0.01),
            circuit_breaker=CircuitBreakerConfig(),
        )

        response = client.get("/users/1", response_data_schema=User)

        assert response is not None
        assert len(requests_mock.request_history) == 3  # Initial + 2 retries

    def test_circuit_breaker_opens_after_failures(self, requests_mock):
        """Test that circuit breaker opens after too many failures."""
        requests_mock.get("https://api.example.com/v1/users/1", status_code=500)

        client = RestClient(
            base_url="https://api.example.com/v1",
            service_name="test",
            logger=MagicMock(),
            retry=RetryConfig(max_retries=0),  # No retries to speed up circuit opening
            circuit_breaker=CircuitBreakerConfig(fail_max=2, reset_timeout=5),
        )

        # Fail twice to open circuit
        with pytest.raises(Exception):
            client.get("/users/1", response_data_schema=User, max_retries=0)

        with pytest.raises(Exception):
            client.get("/users/1", response_data_schema=User, max_retries=0)

        # Third request should fail fast with CircuitBreakerOpenError
        with pytest.raises(CircuitBreakerOpenError):
            client.get("/users/1", response_data_schema=User, max_retries=0)

    def test_interceptors_with_full_pipeline(self, requests_mock):
        """Test interceptors work with full pipeline."""

        class TestInterceptor(Interceptor):
            def __init__(self):
                self.before_called = False
                self.after_called = False

            def on_before_request(self, request):
                self.before_called = True
                request.headers["X-Custom"] = "value"

            def on_after_response(self, response):
                self.after_called = True

            def on_error(self, error):
                pass

        requests_mock.get(
            "https://api.example.com/v1/users/1",
            json={"id": 1, "name": "John", "email": "john@example.com"},
        )

        interceptor = TestInterceptor()
        client = RestClient(
            base_url="https://api.example.com/v1",
            service_name="test",
            logger=MagicMock(),
            interceptors=[interceptor],
        )

        response = client.get("/users/1", response_data_schema=User)

        assert response is not None
        assert interceptor.before_called is True
        assert interceptor.after_called is True
        assert requests_mock.last_request.headers["X-Custom"] == "value"

    def test_error_handling_with_interceptors(self, requests_mock):
        """Test error handling with interceptors."""

        class ErrorInterceptor(Interceptor):
            def __init__(self):
                self.errors = []

            def on_before_request(self, request):
                pass

            def on_after_response(self, response):
                pass

            def on_error(self, error):
                self.errors.append(error)

        requests_mock.get("https://api.example.com/v1/users/1", status_code=404)

        interceptor = ErrorInterceptor()
        client = RestClient(
            base_url="https://api.example.com/v1",
            service_name="test",
            logger=MagicMock(),
            interceptors=[interceptor],
        )

        with pytest.raises(ResourceNotFoundError):
            client.get("/users/1", response_data_schema=User)

        assert len(interceptor.errors) == 1
        assert isinstance(interceptor.errors[0], ResourceNotFoundError)

    def test_validation_error_not_retried(self, requests_mock):
        """Test that response validation errors are NOT retried."""
        # Server returns invalid response data
        requests_mock.get(
            "https://api.example.com/v1/users/1",
            json={"invalid": "data"},
            status_code=200,
        )

        client = RestClient(
            base_url="https://api.example.com/v1",
            service_name="test",
            logger=MagicMock(),
            retry=RetryConfig(max_retries=3, backoff_factor=0.01),
        )

        # Should raise ResponseValidationError without retrying
        from reqlient.core.errors import ResponseValidationError

        with pytest.raises(ResponseValidationError):
            client.get("/users/1", response_data_schema=User)

        # Should only make one request (no retries)
        assert len(requests_mock.request_history) == 1

    def test_connection_error_with_retry_and_breaker(self, requests_mock):
        """Test connection error handling with retry and circuit breaker."""
        requests_mock.get(
            "https://api.example.com/v1/users/1",
            [
                {"exc": httpx.ConnectError("Connection failed")},
                {
                    "json": {"id": 1, "name": "John", "email": "john@example.com"},
                    "status_code": 200,
                },
            ],
        )


        client = RestClient(
            base_url="https://api.example.com/v1",
            service_name="test",
            logger=MagicMock(),
            retry=RetryConfig(max_retries=1, backoff_factor=0.01),
            circuit_breaker=CircuitBreakerConfig(),
        )

        response = client.get("/users/1", response_data_schema=User)

        assert response is not None
        assert response.id == 1

    def test_per_request_overrides(self, requests_mock):
        """Test that per-request overrides work correctly."""
        requests_mock.get(
            "https://api.example.com/v1/users/1",
            json={"id": 1, "name": "John", "email": "john@example.com"},
        )

        client = RestClient(
            base_url="https://api.example.com/v1",
            service_name="test",
            logger=MagicMock(),
            retry=RetryConfig(max_retries=3),
        )

        # First request
        client.get("/users/1", response_data_schema=User)
        assert len(requests_mock.request_history) == 1

        # Second request with custom retry settings
        client.get("/users/1", response_data_schema=User, max_retries=0)
        assert len(requests_mock.request_history) == 2

    def test_201_status_code(self, requests_mock):
        """Test handling 201 status code (still successful)."""
        requests_mock.get(
            "https://api.example.com/v1/users/1",
            json={"id": 1, "name": "John", "email": "john@example.com"},
            status_code=201,
        )

        client = RestClient(
            base_url="https://api.example.com/v1",
            service_name="test",
            logger=MagicMock(),
        )

        response = client.get("/users/1", response_data_schema=User)
        assert response is not None
        assert response.id == 1

    def test_204_no_content_response(self, requests_mock):
        """Test handling 204 No Content responses."""
        requests_mock.delete("https://api.example.com/v1/users/1", status_code=204)

        client = RestClient(
            base_url="https://api.example.com/v1",
            service_name="test",
            logger=MagicMock(),
        )

        response = client.delete("/users/1", response_data_schema=User)

        assert response is None

    def test_url_construction_edge_cases(self, requests_mock):
        """Test URL construction handles various edge cases."""
        requests_mock.get(
            "https://api.example.com/v1/users",
            json={"id": 1, "name": "John", "email": "john@example.com"},
        )

        client = RestClient(
            base_url="https://api.example.com/v1",
            service_name="test",
            logger=MagicMock(),
        )

        # Test with endpoint starting with /
        client.get("/users", response_data_schema=User)
        assert requests_mock.last_request.url == "https://api.example.com/v1/users"

        # Test with endpoint not starting with /
        client.get("users", response_data_schema=User)
        assert requests_mock.last_request.url == "https://api.example.com/v1/users"

        # Test with base_url having trailing slash
        client2 = RestClient(
            base_url="https://api.example.com/",
            service_name="test",
            logger=MagicMock(),
        )
        client2.get("/v1/users", response_data_schema=User)
        assert requests_mock.request_history[-1].url == "https://api.example.com/v1/users"


class TestRealWorldScenarios:
    """Test real-world usage scenarios."""

    def test_api_client_pattern(self, requests_mock):
        """Test a typical API client wrapper pattern."""

        class UserServiceClient:
            def __init__(self, rest_client: RestClient):
                self._client = rest_client

            def get_user(self, user_id: int) -> User:
                return self._client.get(f"/users/{user_id}", response_data_schema=User)

            def create_user(self, name: str, email: str) -> User:
                request_data = CreateUserRequest(name=name, email=email)
                return self._client.post("/users", request_data=request_data, response_data_schema=User)

        requests_mock.get(
            "https://api.example.com/v1/users/123",
            json={"id": 123, "name": "John", "email": "john@example.com"},
        )
        requests_mock.post(
            "https://api.example.com/v1/users",
            json={"id": 456, "name": "Jane", "email": "jane@example.com"},
            status_code=201,
        )

        base_client = RestClient(
            base_url="https://api.example.com/v1",
            service_name="user_service",
            logger=MagicMock(),
        )

        user_client = UserServiceClient(base_client)

        # Get user
        user = user_client.get_user(123)
        assert user.id == 123
        assert user.name == "John"

        # Create user
        new_user = user_client.create_user("Jane", "jane@example.com")
        assert new_user.id == 456
        assert new_user.name == "Jane"

    def test_error_recovery_scenario(self, requests_mock):
        """Test error recovery in a real scenario."""
        # Simulate transient failures followed by success
        requests_mock.get(
            "https://api.example.com/v1/users/1",
            [
                {"status_code": 500},
                {"status_code": 503},
                {
                    "json": {"id": 1, "name": "John", "email": "john@example.com"},
                    "status_code": 200,
                },
            ],
        )

        client = RestClient(
            base_url="https://api.example.com/v1",
            service_name="test",
            logger=MagicMock(),
            retry=RetryConfig(max_retries=3, backoff_factor=0.01),
        )

        # Should eventually succeed after retries
        user = client.get("/users/1", response_data_schema=User)

        assert user is not None
        assert user.id == 1
        assert len(requests_mock.request_history) == 3

    def test_rate_limiting_scenario(self, requests_mock):
        """Test handling rate limiting."""
        requests_mock.get(
            "https://api.example.com/v1/users/1",
            [
                {"status_code": 429, "headers": {"Retry-After": "1"}},
                {
                    "json": {"id": 1, "name": "John", "email": "john@example.com"},
                    "status_code": 200,
                },
            ],
        )

        client = RestClient(
            base_url="https://api.example.com/v1",
            service_name="test",
            logger=MagicMock(),
            retry=RetryConfig(max_retries=2, backoff_factor=0.1),
        )

        # Should retry on 429 and eventually succeed
        user = client.get("/users/1", response_data_schema=User)

        assert user is not None
        assert user.id == 1
