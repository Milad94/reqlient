"""
Comprehensive tests for error handling and error context.
"""

from datetime import datetime

from reqflow.core.errors import (
    AuthenticationError,
    AuthorizationError,
    ConnectionError,
    ErrorContext,
    RequestError,
    RequestValidationError,
    ResourceNotFoundError,
    ResponseValidationError,
    RestClientError,
    RetryableError,
    ServerError,
    StatusCodeError,
    TimeoutError,
)
from reqflow.core.request_response import RequestContext, ResponseContext


class TestErrorContext:
    """Test ErrorContext dataclass."""

    def test_error_context_creation(self):
        """Test creating an ErrorContext."""
        request_context = RequestContext(
            method="GET",
            url="https://api.example.com/v1/users/1",
            headers={"Authorization": "Bearer token"},
            params={"page": "1"},
            data={"test": "data"},
        )

        response_context = ResponseContext(
            status_code=404,
            headers={"Content-Type": "application/json"},
            data={"error": "Not found"},
            request=request_context,
        )

        error = ValueError("Test error")
        context = ErrorContext(
            timestamp=datetime.now(),
            request_url=request_context.url,
            request_method=request_context.method,
            request_headers=request_context.headers,
            request_params=request_context.params,
            request_data=request_context.data,
            response_status=response_context.status_code,
            response_headers=response_context.headers,
            response_data=response_context.data,
            error_message=str(error),
            error_type=type(error).__name__,
            retry_count=2,
        )

        assert context.request_url == "https://api.example.com/v1/users/1"
        assert context.request_method == "GET"
        assert context.request_headers == {"Authorization": "Bearer token"}
        assert context.request_params == {"page": "1"}
        assert context.request_data == {"test": "data"}
        assert context.response_status == 404
        assert context.response_headers == {"Content-Type": "application/json"}
        assert context.response_data == {"error": "Not found"}
        assert context.error_message == "Test error"
        assert context.error_type == "ValueError"
        assert context.retry_count == 2

    def test_error_context_without_response(self):
        """Test ErrorContext without response context."""
        request_context = RequestContext(
            method="GET",
            url="https://api.example.com/v1/users/1",
            headers={},
            params=None,
            data=None,
        )

        error = ConnectionError("Connection failed", context=None)
        context = ErrorContext(
            timestamp=datetime.now(),
            request_url=request_context.url,
            request_method=request_context.method,
            request_headers=request_context.headers,
            request_params=request_context.params,
            request_data=request_context.data,
            response_status=None,
            response_headers=None,
            response_data=None,
            error_message=str(error),
            error_type=type(error).__name__,
        )

        assert context.response_status is None
        assert context.response_headers is None
        assert context.response_data is None


class TestRestClientError:
    """Test RestClientError base class."""

    def test_error_with_context(self):
        """Test RestClientError with context."""
        context = ErrorContext(
            timestamp=datetime.now(),
            request_url="https://api.example.com/v1/users/1",
            request_method="GET",
            request_headers={},
            request_params=None,
            request_data=None,
            response_status=500,
            response_headers=None,
            response_data=None,
            error_message="Server error",
            error_type="ServerError",
        )

        error = RestClientError("An error occurred", context=context)

        assert error.message == "An error occurred"
        assert error.context == context
        assert str(error) is not None
        assert "GET" in str(error)
        assert "https://api.example.com/v1/users/1" in str(error)

    def test_error_without_context(self):
        """Test RestClientError without context."""
        error = RestClientError("An error occurred", context=None)

        assert error.message == "An error occurred"
        assert error.context is None
        assert str(error) == "An error occurred"

    def test_error_string_representation(self):
        """Test error string representation."""
        context = ErrorContext(
            timestamp=datetime.now(),
            request_url="https://api.example.com/v1/users/1",
            request_method="GET",
            request_headers={},
            request_params=None,
            request_data=None,
            response_status=404,
            response_headers=None,
            response_data=None,
            error_message="Not found",
            error_type="ResourceNotFoundError",
            retry_count=1,
        )

        error = RestClientError("Resource not found", context=context)
        error_str = str(error)

        assert "Resource not found" in error_str
        assert "GET" in error_str
        assert "https://api.example.com/v1/users/1" in error_str
        assert "404" in error_str or "Status: 404" in error_str


class TestSpecificErrors:
    """Test specific error types."""

    def test_authentication_error(self):
        """Test AuthenticationError."""
        error = AuthenticationError("Authentication failed", context=None)
        assert isinstance(error, RestClientError)
        assert str(error) == "Authentication failed"

    def test_authorization_error(self):
        """Test AuthorizationError."""
        error = AuthorizationError("Authorization failed", context=None)
        assert isinstance(error, RestClientError)
        assert str(error) == "Authorization failed"

    def test_resource_not_found_error(self):
        """Test ResourceNotFoundError."""
        error = ResourceNotFoundError("Resource not found", context=None)
        assert isinstance(error, RestClientError)
        assert str(error) == "Resource not found"

    def test_connection_error(self):
        """Test ConnectionError."""
        error = ConnectionError("Connection failed", context=None)
        assert isinstance(error, RetryableError)
        assert isinstance(error, RestClientError)
        assert str(error) == "Connection failed"

    def test_timeout_error(self):
        """Test TimeoutError."""
        error = TimeoutError("Request timed out", context=None)
        assert isinstance(error, RetryableError)
        assert isinstance(error, RestClientError)
        assert str(error) == "Request timed out"

    def test_server_error(self):
        """Test ServerError."""
        error = ServerError("Server error", context=None)
        assert isinstance(error, RetryableError)
        assert isinstance(error, RestClientError)
        assert str(error) == "Server error"

    def test_retryable_error(self):
        """Test RetryableError."""
        error = RetryableError("Retryable error", context=None)
        assert isinstance(error, RestClientError)
        assert str(error) == "Retryable error"

    def test_request_validation_error(self):
        """Test RequestValidationError."""
        error = RequestValidationError("Validation failed", context=None)
        assert isinstance(error, RestClientError)
        assert str(error) == "Validation failed"

    def test_response_validation_error(self):
        """Test ResponseValidationError."""
        error = ResponseValidationError("Validation failed", context=None)
        assert isinstance(error, RestClientError)
        assert str(error) == "Validation failed"

    def test_status_code_error(self):
        """Test StatusCodeError."""
        error = StatusCodeError("Unexpected status code", context=None)
        assert isinstance(error, RestClientError)
        assert str(error) == "Unexpected status code"

    def test_request_error(self):
        """Test RequestError."""
        error = RequestError("Request failed", context=None)
        assert isinstance(error, RestClientError)
        assert str(error) == "Request failed"


class TestErrorInheritance:
    """Test error inheritance hierarchy."""

    def test_retryable_errors(self):
        """Test that retryable errors inherit from RetryableError."""
        connection_error = ConnectionError("Connection failed", context=None)
        timeout_error = TimeoutError("Timeout", context=None)
        server_error = ServerError("Server error", context=None)

        assert isinstance(connection_error, RetryableError)
        assert isinstance(timeout_error, RetryableError)
        assert isinstance(server_error, RetryableError)
        assert isinstance(connection_error, RestClientError)
        assert isinstance(timeout_error, RestClientError)
        assert isinstance(server_error, RestClientError)

    def test_non_retryable_errors(self):
        """Test that non-retryable errors don't inherit from RetryableError."""
        auth_error = AuthenticationError("Auth failed", context=None)
        validation_error = RequestValidationError("Validation failed", context=None)

        assert not isinstance(auth_error, RetryableError)
        assert not isinstance(validation_error, RetryableError)
        assert isinstance(auth_error, RestClientError)
        assert isinstance(validation_error, RestClientError)
