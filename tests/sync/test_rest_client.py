"""
Comprehensive tests for RestClient.
"""

import time
from unittest.mock import MagicMock, patch

import httpx
import pytest
from pydantic import BaseModel, ValidationError

from reqlient.core.errors import (
    AuthenticationError,
    AuthorizationError,
    ConnectionError,
    ResourceNotFoundError,
    RestClientError,
    StatusCodeError,
    TimeoutError,
)
from reqlient.sync.rest_client import RestClient


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


class TestRestClientInitialization:
    """Test RestClient initialization and configuration."""

    def test_basic_initialization(self, base_url, mock_logger):
        """Test basic RestClient initialization."""
        client = RestClient(base_url=base_url, service_name="test", logger=mock_logger)
        assert client.base_url == base_url
        assert client.service_name == "test"
        assert client.logger == mock_logger
        assert client.timeout == 30
        assert client.verify_ssl is True

    def test_custom_configuration(self, base_url, mock_logger):
        """Test RestClient with custom configuration."""
        client = RestClient(
            base_url=base_url,
            service_name="test",
            logger=mock_logger,
            timeout=60,
            verify_ssl=False,
            max_retries=5,
            retry_backoff_factor=1.0,
        )
        assert client.timeout == 60
        assert client.verify_ssl is False
        assert client.default_retry_config["max_retries"] == 5
        assert client.default_retry_config["backoff_factor"] == 1.0

    def test_base_url_normalization(self, mock_logger):
        """Test that base_url trailing slashes are handled."""
        client1 = RestClient(
            base_url="https://api.example.com", service_name="test", logger=mock_logger
        )
        client2 = RestClient(
            base_url="https://api.example.com/", service_name="test", logger=mock_logger
        )
        assert client1.base_url == "https://api.example.com"
        assert client2.base_url == "https://api.example.com"

    def test_default_headers(self, base_url, mock_logger):
        """Test default headers are set correctly."""
        client = RestClient(base_url=base_url, service_name="test", logger=mock_logger)
        assert "Content-Type" in client.default_headers
        assert client.default_headers["Content-Type"] == "application/json"

    def test_custom_default_headers(self, base_url, mock_logger):
        """Test custom default headers."""
        custom_headers = {"X-Custom-Header": "value", "Content-Type": "application/xml"}
        client = RestClient(
            base_url=base_url,
            service_name="test",
            logger=mock_logger,
            default_headers=custom_headers,
        )
        assert client.default_headers == custom_headers


class TestRestClientGet:
    """Test GET request functionality."""

    def test_successful_get(self, basic_client, requests_mock):
        """Test successful GET request."""
        requests_mock.get(
            "https://api.example.com/v1/users/1",
            json={"id": 1, "name": "John Doe", "email": "john@example.com"},
            status_code=200,
        )

        response = basic_client.get("/users/1", response_data_schema=User)
        assert response is not None
        assert response.id == 1
        assert response.name == "John Doe"
        assert response.email == "john@example.com"

    def test_get_with_params(self, basic_client, requests_mock):
        """Test GET request with query parameters."""
        requests_mock.get(
            "https://api.example.com/v1/users?page=1&limit=10",
            json={"id": 1, "name": "John", "email": "john@example.com"},
            status_code=200,
        )

        response = basic_client.get(
            "/users", response_data_schema=User, params={"page": "1", "limit": "10"}
        )
        assert response is not None

    def test_get_with_custom_headers(self, basic_client, requests_mock):
        """Test GET request with custom headers."""
        requests_mock.get(
            "https://api.example.com/v1/users/1",
            json={"id": 1, "name": "John", "email": "john@example.com"},
        )

        response = basic_client.get("/users/1", response_data_schema=User, headers={"X-Custom": "value"})
        assert response is not None
        assert requests_mock.last_request.headers["X-Custom"] == "value"

    def test_get_404_error(self, basic_client, requests_mock):
        """Test GET request with 404 error."""
        requests_mock.get("https://api.example.com/v1/users/999", status_code=404)

        with pytest.raises(ResourceNotFoundError) as exc_info:
            basic_client.get("/users/999", response_data_schema=User)

        assert exc_info.value.context is not None
        assert exc_info.value.context.response_status == 404

    def test_get_401_error(self, basic_client, requests_mock):
        """Test GET request with 401 error."""
        requests_mock.get("https://api.example.com/v1/users/1", status_code=401)

        with pytest.raises(AuthenticationError) as exc_info:
            basic_client.get("/users/1", response_data_schema=User)

        assert exc_info.value.context is not None
        assert exc_info.value.context.response_status == 401

    def test_get_403_error(self, basic_client, requests_mock):
        """Test GET request with 403 error."""
        requests_mock.get("https://api.example.com/v1/users/1", status_code=403)

        with pytest.raises(AuthorizationError) as exc_info:
            basic_client.get("/users/1", response_data_schema=User)

        assert exc_info.value.context is not None
        assert exc_info.value.context.response_status == 403

    def test_get_unexpected_status_code(self, basic_client, requests_mock):
        """Test GET request with unexpected status code."""
        requests_mock.get("https://api.example.com/v1/users/1", status_code=418)

        with pytest.raises(StatusCodeError) as exc_info:
            basic_client.get("/users/1", response_data_schema=User)
        
        assert exc_info.value.context is not None
        assert exc_info.value.context.response_status == 418

    def test_get_with_201_status(self, basic_client, requests_mock):
        """Test GET request with 201 status code (still successful)."""
        requests_mock.get(
            "https://api.example.com/v1/users/1",
            json={"id": 1, "name": "John", "email": "john@example.com"},
            status_code=201,
        )

        response = basic_client.get("/users/1", response_data_schema=User)
        assert response is not None
        assert response.id == 1

    def test_get_204_no_content(self, basic_client, requests_mock):
        """Test GET request with 204 No Content."""
        requests_mock.get("https://api.example.com/v1/users/1", status_code=204)

        response = basic_client.get("/users/1", response_data_schema=User)
        assert response is None

    def test_get_url_construction(self, basic_client, requests_mock):
        """Test URL construction handles edge cases."""
        # Test with endpoint starting with /
        requests_mock.get(
            "https://api.example.com/v1/users",
            json={"id": 1, "name": "John", "email": "john@example.com"},
        )
        basic_client.get("/users", response_data_schema=User)
        assert requests_mock.last_request.url == "https://api.example.com/v1/users"

        # Test with endpoint not starting with /
        requests_mock.get(
            "https://api.example.com/v1/users",
            json={"id": 1, "name": "John", "email": "john@example.com"},
        )
        basic_client.get("users", response_data_schema=User)
        assert requests_mock.last_request.url == "https://api.example.com/v1/users"

        # Test with base_url having trailing slash
        client2 = RestClient(
            base_url="https://api.example.com/", service_name="test", logger=MagicMock()
        )
        requests_mock.get(
            "https://api.example.com/v1/users",
            json={"id": 1, "name": "John", "email": "john@example.com"},
        )
        client2.get("/v1/users", response_data_schema=User)
        assert requests_mock.last_request.url == "https://api.example.com/v1/users"


class TestRestClientPost:
    """Test POST request functionality."""

    def test_successful_post(self, basic_client, requests_mock, create_user_request):
        """Test successful POST request."""
        requests_mock.post(
            "https://api.example.com/v1/users",
            json={"id": 1, "name": "Jane Doe", "email": "jane@example.com"},
            status_code=201,
        )

        response = basic_client.post(
            "/users", request_data=create_user_request, response_data_schema=UserResponse
        )
        assert response is not None
        assert response.id == 1
        assert response.name == "Jane Doe"

    def test_post_with_params(self, basic_client, requests_mock, create_user_request):
        """Test POST request with query parameters."""
        requests_mock.post(
            "https://api.example.com/v1/users",
            json={"id": 1, "name": "Jane", "email": "jane@example.com"},
            status_code=201,
        )

        response = basic_client.post(
            "/users",
            request_data=create_user_request,
            response_data_schema=UserResponse,
            params={"notify": "true"},
        )
        assert response is not None

    def test_post_validation_error(self, basic_client, requests_mock):
        """Test POST request with invalid request data."""
        invalid_request = CreateUserRequest(name="", email="invalid-email")

        # This should pass validation (Pydantic will validate), but let's test the flow
        requests_mock.post("https://api.example.com/v1/users", json={"id": 1}, status_code=201)

        # If request validation fails, it should raise RequestValidationError
        # But Pydantic will validate at model creation, so we need to test differently
        # Response validation will fail because {"id": 1} doesn't match UserResponse
        from reqlient.core.errors import ResponseValidationError

        with pytest.raises(ResponseValidationError):
            basic_client.post(
                "/users", request_data=invalid_request, response_data_schema=UserResponse, max_retries=0
            )

    def test_post_response_validation_error(self, basic_client, requests_mock, create_user_request):
        """Test POST request with invalid response data."""
        requests_mock.post(
            "https://api.example.com/v1/users", json={"invalid": "data"}, status_code=201
        )

        from reqlient.core.errors import ResponseValidationError

        with pytest.raises(ResponseValidationError):
            basic_client.post(
                "/users",
                request_data=create_user_request,
                response_data_schema=UserResponse,
                max_retries=0,
            )


class TestRestClientPut:
    """Test PUT request functionality."""

    def test_successful_put(self, basic_client, requests_mock, create_user_request):
        """Test successful PUT request."""
        requests_mock.put(
            "https://api.example.com/v1/users/1",
            json={"id": 1, "name": "Jane Updated", "email": "jane@example.com"},
            status_code=200,
        )

        response = basic_client.put(
            "/users/1", request_data=create_user_request, response_data_schema=UserResponse
        )
        assert response is not None
        assert response.name == "Jane Updated"


class TestRestClientPatch:
    """Test PATCH request functionality."""

    def test_successful_patch(self, basic_client, requests_mock, create_user_request):
        """Test successful PATCH request."""
        requests_mock.patch(
            "https://api.example.com/v1/users/1",
            json={"id": 1, "name": "Jane Patched", "email": "jane@example.com"},
            status_code=200,
        )

        response = basic_client.patch(
            "/users/1", request_data=create_user_request, response_data_schema=UserResponse
        )
        assert response is not None
        assert response.name == "Jane Patched"


class TestRestClientDelete:
    """Test DELETE request functionality."""

    def test_successful_delete(self, basic_client, requests_mock):
        """Test successful DELETE request."""
        requests_mock.delete("https://api.example.com/v1/users/1", status_code=204)

        response = basic_client.delete("/users/1", response_data_schema=UserResponse)
        assert response is None  # 204 No Content

    def test_delete_with_200_status(self, basic_client, requests_mock):
        """Test DELETE request with 200 OK status (valid response)."""
        requests_mock.delete(
            "https://api.example.com/v1/users/1",
            json={"id": 1, "name": "John", "email": "john@example.com"},
            status_code=200,
        )

        response = basic_client.delete("/users/1", response_data_schema=User)
        assert response is not None
        assert response.id == 1


class TestRestClientErrorHandling:
    """Test error handling and edge cases."""

    def test_connection_error(self, basic_client, requests_mock):
        """Test handling of connection errors."""
        requests_mock.get(
            "https://api.example.com/v1/users/1",
            exc=httpx.ConnectError("Connection failed"),
        )

        with pytest.raises(ConnectionError) as exc_info:
            basic_client.get("/users/1", response_data_schema=User)

        assert exc_info.value.context is not None
        assert "Connection failed" in str(exc_info.value)

    def test_timeout_error(self, basic_client, requests_mock):
        """Test handling of timeout errors."""
        requests_mock.get(
            "https://api.example.com/v1/users/1",
            exc=httpx.TimeoutException("Request timed out"),
        )

        with pytest.raises(TimeoutError) as exc_info:
            basic_client.get("/users/1", response_data_schema=User)

        assert exc_info.value.context is not None
        assert "timed out" in str(exc_info.value).lower()

    def test_server_error_500(self, basic_client, requests_mock):
        """Test handling of 500 server errors."""
        requests_mock.get("https://api.example.com/v1/users/1", status_code=500)

        # 500 is in retry_status_codes, so it will retry
        # But if retries are exhausted, it should raise an error
        with pytest.raises(Exception):  # Could be RetryableError or StatusCodeError
            basic_client.get("/users/1", response_data_schema=User, max_retries=0)

    def test_unexpected_exception(self, basic_client, requests_mock):
        """Test handling of unexpected exceptions."""
        requests_mock.get("https://api.example.com/v1/users/1", exc=ValueError("Unexpected error"))

        with pytest.raises(RestClientError) as exc_info:
            basic_client.get("/users/1", response_data_schema=User)

        assert exc_info.value.context is not None
        assert "unexpected error" in str(exc_info.value).lower()

    def test_error_context_contains_request_info(self, basic_client, requests_mock):
        """Test that error context contains request information."""
        requests_mock.get("https://api.example.com/v1/users/1", status_code=404)

        with pytest.raises(ResourceNotFoundError) as exc_info:
            basic_client.get("/users/1", response_data_schema=User, params={"test": "param"})

        context = exc_info.value.context
        assert context is not None
        assert context.request_url == "https://api.example.com/v1/users/1"
        assert context.request_method == "GET"
        assert context.request_params == {"test": "param"}
        assert context.response_status == 404


class TestRestClientRetry:
    """Test retry functionality."""

    def test_retry_on_500_error(self, basic_client, requests_mock):
        """Test that 500 errors trigger retries."""
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

        response = basic_client.get("/users/1", response_data_schema=User, max_retries=2)
        assert response is not None
        assert len(requests_mock.request_history) == 3

    def test_retry_exhausted(self, basic_client, requests_mock):
        """Test that retries are exhausted after max attempts."""
        requests_mock.get("https://api.example.com/v1/users/1", status_code=500)

        with pytest.raises(Exception):  # RetryableError or similar
            basic_client.get("/users/1", response_data_schema=User, max_retries=2)

        # Should have tried max_retries + 1 times (initial + retries)
        assert len(requests_mock.request_history) == 3

    def test_retry_backoff(self, basic_client, requests_mock):
        """Test that retry backoff is applied."""
        requests_mock.get(
            "https://api.example.com/v1/users/1",
            [
                {"status_code": 500},
                {
                    "json": {"id": 1, "name": "John", "email": "john@example.com"},
                    "status_code": 200,
                },
            ],
        )

        start_time = time.time()
        response = basic_client.get(
            "/users/1", response_data_schema=User, max_retries=1, retry_backoff_factor=0.1
        )
        elapsed = time.time() - start_time

        assert response is not None
        # Should have waited at least the backoff time
        assert elapsed >= 0.1

    def test_no_retry_on_non_retryable_status(self, basic_client, requests_mock):
        """Test that non-retryable status codes don't trigger retries."""
        requests_mock.get("https://api.example.com/v1/users/1", status_code=400)

        with pytest.raises(StatusCodeError):
            basic_client.get("/users/1", response_data_schema=User, max_retries=2)

        # Should only try once
        assert len(requests_mock.request_history) == 1

    def test_per_request_retry_override(self, basic_client, requests_mock):
        """Test per-request retry override."""
        requests_mock.get("https://api.example.com/v1/users/1", status_code=500)

        # Override max_retries to 0 for this request
        with pytest.raises(Exception):
            basic_client.get("/users/1", response_data_schema=User, max_retries=0)

        # Should only try once (no retries)
        assert len(requests_mock.request_history) == 1


class TestRestClientThreadSafety:
    """Test thread safety of RestClient."""

    def test_thread_local_session(self, basic_client):
        """Test that each thread gets its own session."""
        import threading

        sessions = []

        def get_session():
            sessions.append(basic_client.session)

        thread1 = threading.Thread(target=get_session)
        thread2 = threading.Thread(target=get_session)

        thread1.start()
        thread2.start()
        thread1.join()
        thread2.join()

        # Each thread should have its own session
        assert len(sessions) == 2
        assert sessions[0] is not sessions[1]


class TestRestClientTransport:
    """Test httpx transport configuration: TLS verification and redirects.

    These lock in behavior that differs between ``requests`` (the previous HTTP
    backend) and ``httpx``: httpx does not follow redirects by default and does
    not accept ``verify`` as a per-request argument, so both must be configured
    on the client at construction time.
    """

    def test_verify_ssl_false_passed_to_httpx_client(self, base_url, mock_logger):
        """verify_ssl=False must be forwarded to the underlying httpx.Client."""
        with patch("reqlient.sync.rest_client.httpx.Client") as mock_client_cls:
            RestClient(
                base_url=base_url,
                service_name="test_verify_off",
                logger=mock_logger,
                verify_ssl=False,
                use_circuit_breaker=False,
            )

        mock_client_cls.assert_called_once()
        kwargs = mock_client_cls.call_args.kwargs
        assert kwargs["verify"] is False
        # Redirect-following is always enabled to match requests' default behavior.
        assert kwargs["follow_redirects"] is True

    def test_verify_ssl_true_by_default(self, base_url, mock_logger):
        """TLS verification must be on unless explicitly disabled."""
        with patch("reqlient.sync.rest_client.httpx.Client") as mock_client_cls:
            RestClient(
                base_url=base_url,
                service_name="test_verify_on",
                logger=mock_logger,
                use_circuit_breaker=False,
            )

        assert mock_client_cls.call_args.kwargs["verify"] is True

    def test_verify_ssl_applied_to_ssl_context(self, base_url, mock_logger):
        """verify_ssl must actually disable certificate verification on the client."""
        import ssl

        secure = RestClient(
            base_url=base_url,
            service_name="secure",
            logger=mock_logger,
            verify_ssl=True,
            use_circuit_breaker=False,
        )
        insecure = RestClient(
            base_url=base_url,
            service_name="insecure",
            logger=mock_logger,
            verify_ssl=False,
            use_circuit_breaker=False,
        )

        secure_ctx = secure.session._transport._pool._ssl_context
        insecure_ctx = insecure.session._transport._pool._ssl_context

        assert secure_ctx.verify_mode == ssl.CERT_REQUIRED
        assert secure_ctx.check_hostname is True
        assert insecure_ctx.verify_mode == ssl.CERT_NONE
        assert insecure_ctx.check_hostname is False

    def test_clients_do_not_share_a_session(self, base_url, mock_logger):
        """Each client must own its httpx.Client so per-client verify/timeout are honored.

        Regression guard: the client used to be cached in a module-global
        thread-local shared across every instance, which silently leaked the
        first instance's verify_ssl/timeout to all later instances.
        """
        client_a = RestClient(
            base_url=base_url,
            service_name="client_a",
            logger=mock_logger,
            verify_ssl=True,
            use_circuit_breaker=False,
        )
        client_b = RestClient(
            base_url=base_url,
            service_name="client_b",
            logger=mock_logger,
            verify_ssl=False,
            use_circuit_breaker=False,
        )

        assert client_a.session is not client_b.session

    def test_follows_redirects(self, basic_client, requests_mock):
        """A 3xx redirect must be followed through to the final response."""
        requests_mock.get(
            "https://api.example.com/v1/users/1",
            status_code=302,
            headers={"Location": "https://api.example.com/v1/users/2"},
        )
        requests_mock.get(
            "https://api.example.com/v1/users/2",
            json={"id": 2, "name": "Redirected", "email": "redirected@example.com"},
        )

        response = basic_client.get("/users/1", response_data_schema=User)

        assert response is not None
        assert response.id == 2
        assert response.name == "Redirected"
        # Both the original URL and the redirect target should have been requested.
        assert len(requests_mock.request_history) == 2
        assert requests_mock.request_history[-1].url == "https://api.example.com/v1/users/2"
