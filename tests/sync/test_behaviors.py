"""
Comprehensive tests for all behaviors in the pipeline.
"""

from unittest.mock import MagicMock, patch

import pytest
import requests
from pydantic import BaseModel

from reqflow.sync.behaviors import (
    HttpBehavior,
    IdempotencyHeaderBehavior,
    InterceptorBehavior,
    LoggingBehavior,
    RequestValidationBehavior,
    ResponseValidationBehavior,
    RetryBehavior,
    StatusCodeValidationBehavior,
)
from reqflow.core.errors import (
    AuthenticationError,
    AuthorizationError,
    ConnectionError,
    RequestValidationError,
    ResourceNotFoundError,
    ResponseValidationError,
    RetryableError,
    StatusCodeError,
    TimeoutError,
)
from reqflow.sync.interceptors import Interceptor
from reqflow.core.request_response import RequestContext, ResponseContext


class User(BaseModel):
    id: int
    name: str
    email: str


class CreateUserRequest(BaseModel):
    name: str
    email: str


class TestLoggingBehavior:
    """Test LoggingBehavior."""

    def test_logs_request_and_response(self, mock_logger):
        """Test that requests and responses are logged."""
        mock_next = MagicMock()
        mock_response = ResponseContext(
            status_code=200,
            headers={},
            data={"id": 1, "name": "John", "email": "john@example.com"},
            request=RequestContext(
                method="GET",
                url="https://api.example.com/v1/users/1",
                headers={},
                params=None,
                data=None,
            ),
        )
        mock_next.handle.return_value = mock_response

        behavior = LoggingBehavior(logger=mock_logger, next_behavior=mock_next)
        request = RequestContext(
            method="GET",
            url="https://api.example.com/v1/users/1",
            headers={},
            params=None,
            data=None,
        )

        result = behavior.handle(request)

        assert result == mock_response
        assert mock_logger.info.call_count == 2  # Request and response
        # Verify logging was called (exact string matching is fragile)
        assert len(mock_logger.info.call_args_list) == 2

    def test_logs_errors(self, mock_logger):
        """Test that errors are logged."""
        mock_next = MagicMock()
        error = AuthenticationError("Auth failed", context=None)
        mock_next.handle.side_effect = error

        behavior = LoggingBehavior(logger=mock_logger, next_behavior=mock_next)
        request = RequestContext(
            method="GET",
            url="https://api.example.com/v1/users/1",
            headers={},
            params=None,
            data=None,
        )

        with pytest.raises(AuthenticationError):
            behavior.handle(request)

        mock_logger.error.assert_called()


class TestRequestValidationBehavior:
    """Test RequestValidationBehavior."""

    def test_validates_request_data(self):
        """Test that request data is validated."""
        mock_next = MagicMock()
        mock_response = ResponseContext(
            status_code=200,
            headers={},
            data={"id": 1, "name": "John", "email": "john@example.com"},
            request=RequestContext(
                method="POST",
                url="https://api.example.com/v1/users",
                headers={},
                params=None,
                data=None,
            ),
        )
        mock_next.handle.return_value = mock_response

        behavior = RequestValidationBehavior(
            request_data_schema=CreateUserRequest, next_behavior=mock_next
        )
        request = RequestContext(
            method="POST",
            url="https://api.example.com/v1/users",
            headers={},
            params=None,
            data={"name": "John", "email": "john@example.com"},
        )

        result = behavior.handle(request)
        assert result == mock_response

    def test_request_validation_error(self):
        """Test that invalid request data raises RequestValidationError."""
        mock_next = MagicMock()

        behavior = RequestValidationBehavior(
            request_data_schema=CreateUserRequest, next_behavior=mock_next
        )
        request = RequestContext(
            method="POST",
            url="https://api.example.com/v1/users",
            headers={},
            params=None,
            data={"invalid": "data"},  # Missing required fields
        )

        with pytest.raises(RequestValidationError) as exc_info:
            behavior.handle(request)

        assert exc_info.value.context is not None

    def test_skips_validation_for_none_data(self):
        """Test that None request data is not validated."""
        mock_next = MagicMock()
        mock_response = ResponseContext(
            status_code=200,
            headers={},
            data={"id": 1},
            request=RequestContext(
                method="GET",
                url="https://api.example.com/v1/users/1",
                headers={},
                params=None,
                data=None,
            ),
        )
        mock_next.handle.return_value = mock_response

        behavior = RequestValidationBehavior(
            request_data_schema=CreateUserRequest, next_behavior=mock_next
        )
        request = RequestContext(
            method="GET",
            url="https://api.example.com/v1/users/1",
            headers={},
            params=None,
            data=None,
        )

        result = behavior.handle(request)
        assert result == mock_response


class TestResponseValidationBehavior:
    """Test ResponseValidationBehavior."""

    def test_validates_response_data(self):
        """Test that response data is validated."""
        mock_next = MagicMock()
        mock_response = ResponseContext(
            status_code=200,
            headers={},
            data={"id": 1, "name": "John", "email": "john@example.com"},
            request=RequestContext(
                method="GET",
                url="https://api.example.com/v1/users/1",
                headers={},
                params=None,
                data=None,
            ),
        )
        mock_next.handle.return_value = mock_response

        behavior = ResponseValidationBehavior(
            response_data_schema=User, next_behavior=mock_next
        )
        request = RequestContext(
            method="GET",
            url="https://api.example.com/v1/users/1",
            headers={},
            params=None,
            data=None,
        )

        result = behavior.handle(request)
        assert result == mock_response

    def test_response_validation_error(self):
        """Test that invalid response data raises ResponseValidationError."""
        mock_next = MagicMock()
        mock_response = ResponseContext(
            status_code=200,
            headers={},
            data={"invalid": "data"},  # Missing required fields
            request=RequestContext(
                method="GET",
                url="https://api.example.com/v1/users/1",
                headers={},
                params=None,
                data=None,
            ),
        )
        mock_next.handle.return_value = mock_response

        behavior = ResponseValidationBehavior(
            response_data_schema=User, next_behavior=mock_next
        )
        request = RequestContext(
            method="GET",
            url="https://api.example.com/v1/users/1",
            headers={},
            params=None,
            data=None,
        )

        with pytest.raises(ResponseValidationError) as exc_info:
            behavior.handle(request)

        assert exc_info.value.context is not None

    def test_handles_none_response_data(self):
        """Test that None response data (204) is handled correctly."""
        mock_next = MagicMock()
        mock_response = ResponseContext(
            status_code=204,
            headers={},
            data=None,
            request=RequestContext(
                method="DELETE",
                url="https://api.example.com/v1/users/1",
                headers={},
                params=None,
                data=None,
            ),
        )
        mock_next.handle.return_value = mock_response

        behavior = ResponseValidationBehavior(
            response_data_schema=User, next_behavior=mock_next
        )
        request = RequestContext(
            method="DELETE",
            url="https://api.example.com/v1/users/1",
            headers={},
            params=None,
            data=None,
        )

        result = behavior.handle(request)
        assert result.data is None


class TestRetryBehavior:
    """Test RetryBehavior."""

    def test_retries_on_retryable_status_code(self):
        """Test that retryable status codes trigger retries."""
        mock_next = MagicMock()
        # First call returns 500, second returns 200
        mock_next.handle.side_effect = [
            ResponseContext(
                status_code=500,
                headers={},
                data=None,
                request=RequestContext(
                    method="GET",
                    url="https://api.example.com/v1/users/1",
                    headers={},
                    params=None,
                    data=None,
                ),
            ),
            ResponseContext(
                status_code=200,
                headers={},
                data={"id": 1, "name": "John", "email": "john@example.com"},
                request=RequestContext(
                    method="GET",
                    url="https://api.example.com/v1/users/1",
                    headers={},
                    params=None,
                    data=None,
                ),
            ),
        ]

        behavior = RetryBehavior(
            max_retries=2,
            backoff_factor=0.01,
            retry_status_codes={500, 502, 503},
            next_behavior=mock_next,
        )
        request = RequestContext(
            method="GET",
            url="https://api.example.com/v1/users/1",
            headers={},
            params=None,
            data=None,
        )

        result = behavior.handle(request)
        assert result.status_code == 200
        assert mock_next.handle.call_count == 2

    def test_retries_on_exception(self):
        """Test that retryable exceptions trigger retries."""
        mock_next = MagicMock()
        # First call raises ConnectionError, second succeeds
        mock_next.handle.side_effect = [
            ConnectionError("Connection failed", context=None),
            ResponseContext(
                status_code=200,
                headers={},
                data={"id": 1, "name": "John", "email": "john@example.com"},
                request=RequestContext(
                    method="GET",
                    url="https://api.example.com/v1/users/1",
                    headers={},
                    params=None,
                    data=None,
                ),
            ),
        ]

        behavior = RetryBehavior(
            max_retries=2, backoff_factor=0.01, retry_status_codes={500}, next_behavior=mock_next
        )
        request = RequestContext(
            method="GET",
            url="https://api.example.com/v1/users/1",
            headers={},
            params=None,
            data=None,
        )

        result = behavior.handle(request)
        assert result.status_code == 200
        assert mock_next.handle.call_count == 2

    def test_exhausts_retries(self):
        """Test that retries are exhausted after max attempts."""
        mock_next = MagicMock()
        mock_next.handle.return_value = ResponseContext(
            status_code=500,
            headers={},
            data=None,
            request=RequestContext(
                method="GET",
                url="https://api.example.com/v1/users/1",
                headers={},
                params=None,
                data=None,
            ),
        )

        behavior = RetryBehavior(
            max_retries=2, backoff_factor=0.01, retry_status_codes={500}, next_behavior=mock_next
        )
        request = RequestContext(
            method="GET",
            url="https://api.example.com/v1/users/1",
            headers={},
            params=None,
            data=None,
        )

        with pytest.raises(RetryableError):
            behavior.handle(request)

        # Should have tried max_retries + 1 times
        assert mock_next.handle.call_count == 3

    def test_no_retry_on_success(self):
        """Test that successful responses are not retried."""
        mock_next = MagicMock()
        mock_response = ResponseContext(
            status_code=200,
            headers={},
            data={"id": 1, "name": "John", "email": "john@example.com"},
            request=RequestContext(
                method="GET",
                url="https://api.example.com/v1/users/1",
                headers={},
                params=None,
                data=None,
            ),
        )
        mock_next.handle.return_value = mock_response

        behavior = RetryBehavior(
            max_retries=2, backoff_factor=0.01, retry_status_codes={500}, next_behavior=mock_next
        )
        request = RequestContext(
            method="GET",
            url="https://api.example.com/v1/users/1",
            headers={},
            params=None,
            data=None,
        )

        result = behavior.handle(request)
        assert result == mock_response
        assert mock_next.handle.call_count == 1


class TestIdempotencyHeaderBehavior:
    """Test IdempotencyHeaderBehavior."""

    def test_adds_idempotency_header_for_post(self):
        """Test that idempotency header is added for POST requests."""
        mock_next = MagicMock()
        mock_response = ResponseContext(
            status_code=201,
            headers={},
            data={"id": 1},
            request=RequestContext(
                method="POST",
                url="https://api.example.com/v1/users",
                headers={},
                params=None,
                data=None,
            ),
        )
        mock_next.handle.return_value = mock_response

        behavior = IdempotencyHeaderBehavior(next_behavior=mock_next)
        request = RequestContext(
            method="POST",
            url="https://api.example.com/v1/users",
            headers={},
            params=None,
            data={"name": "John"},
        )

        result = behavior.handle(request)
        assert "X-Idempotency-Key" in request.headers
        assert len(request.headers["X-Idempotency-Key"]) > 0
        assert result == mock_response

    def test_adds_idempotency_header_for_put(self):
        """Test that idempotency header is added for PUT requests."""
        mock_next = MagicMock()
        mock_response = ResponseContext(
            status_code=200,
            headers={},
            data={"id": 1},
            request=RequestContext(
                method="PUT",
                url="https://api.example.com/v1/users/1",
                headers={},
                params=None,
                data=None,
            ),
        )
        mock_next.handle.return_value = mock_response

        behavior = IdempotencyHeaderBehavior(next_behavior=mock_next)
        request = RequestContext(
            method="PUT",
            url="https://api.example.com/v1/users/1",
            headers={},
            params=None,
            data={"name": "John"},
        )

        result = behavior.handle(request)
        assert "X-Idempotency-Key" in request.headers
        assert result == mock_response

    def test_adds_idempotency_header_for_delete(self):
        """Test that idempotency header is added for DELETE requests."""
        mock_next = MagicMock()
        mock_response = ResponseContext(
            status_code=200,
            headers={},
            data={"id": 1},
            request=RequestContext(
                method="DELETE",
                url="https://api.example.com/v1/users/1",
                headers={},
                params=None,
                data=None,
            ),
        )
        mock_next.handle.return_value = mock_response

        behavior = IdempotencyHeaderBehavior(next_behavior=mock_next)
        request = RequestContext(
            method="DELETE",
            url="https://api.example.com/v1/users/1",
            headers={},
            params=None,
            data=None,
        )

        result = behavior.handle(request)
        assert "X-Idempotency-Key" in request.headers
        assert result == mock_response

    def test_does_not_add_idempotency_header_for_get(self):
        """Test that idempotency header is NOT added for GET requests."""
        mock_next = MagicMock()
        mock_response = ResponseContext(
            status_code=200,
            headers={},
            data={"id": 1},
            request=RequestContext(
                method="GET",
                url="https://api.example.com/v1/users/1",
                headers={},
                params=None,
                data=None,
            ),
        )
        mock_next.handle.return_value = mock_response

        behavior = IdempotencyHeaderBehavior(next_behavior=mock_next)
        request = RequestContext(
            method="GET",
            url="https://api.example.com/v1/users/1",
            headers={},
            params=None,
            data=None,
        )

        result = behavior.handle(request)
        assert "X-Idempotency-Key" not in request.headers
        assert result == mock_response

    def test_does_not_overwrite_existing_idempotency_header(self):
        """Test that existing idempotency header is not overwritten."""
        mock_next = MagicMock()
        mock_response = ResponseContext(
            status_code=201,
            headers={},
            data={"id": 1},
            request=RequestContext(
                method="POST",
                url="https://api.example.com/v1/users",
                headers={},
                params=None,
                data=None,
            ),
        )
        mock_next.handle.return_value = mock_response

        behavior = IdempotencyHeaderBehavior(next_behavior=mock_next)
        custom_key = "my-custom-idempotency-key"
        request = RequestContext(
            method="POST",
            url="https://api.example.com/v1/users",
            headers={"X-Idempotency-Key": custom_key},
            params=None,
            data={"name": "John"},
        )

        result = behavior.handle(request)
        assert request.headers["X-Idempotency-Key"] == custom_key
        assert result == mock_response


class TestStatusCodeValidationBehavior:
    """Test StatusCodeValidationBehavior."""

    def test_validates_expected_status(self):
        """Test that expected status codes pass validation."""
        mock_next = MagicMock()
        mock_response = ResponseContext(
            status_code=200,
            headers={},
            data={},
            request=RequestContext(
                method="GET",
                url="https://api.example.com/v1/users/1",
                headers={},
                params=None,
                data=None,
            ),
        )
        mock_next.handle.return_value = mock_response

        behavior = StatusCodeValidationBehavior(next_behavior=mock_next)
        request = RequestContext(
            method="GET",
            url="https://api.example.com/v1/users/1",
            headers={},
            params=None,
            data=None,
        )

        result = behavior.handle(request)
        assert result == mock_response

    def test_raises_401_error(self):
        """Test that 401 raises AuthenticationError."""
        mock_next = MagicMock()
        mock_response = ResponseContext(
            status_code=401,
            headers={},
            data={},
            request=RequestContext(
                method="GET",
                url="https://api.example.com/v1/users/1",
                headers={},
                params=None,
                data=None,
            ),
        )
        mock_next.handle.return_value = mock_response

        behavior = StatusCodeValidationBehavior(next_behavior=mock_next)
        request = RequestContext(
            method="GET",
            url="https://api.example.com/v1/users/1",
            headers={},
            params=None,
            data=None,
        )

        with pytest.raises(AuthenticationError) as exc_info:
            behavior.handle(request)

        assert exc_info.value.context is not None
        assert exc_info.value.context.response_status == 401

    def test_raises_403_error(self):
        """Test that 403 raises AuthorizationError."""
        mock_next = MagicMock()
        mock_response = ResponseContext(
            status_code=403,
            headers={},
            data={},
            request=RequestContext(
                method="GET",
                url="https://api.example.com/v1/users/1",
                headers={},
                params=None,
                data=None,
            ),
        )
        mock_next.handle.return_value = mock_response

        behavior = StatusCodeValidationBehavior(next_behavior=mock_next)
        request = RequestContext(
            method="GET",
            url="https://api.example.com/v1/users/1",
            headers={},
            params=None,
            data=None,
        )

        with pytest.raises(AuthorizationError) as exc_info:
            behavior.handle(request)

        assert exc_info.value.context is not None
        assert exc_info.value.context.response_status == 403

    def test_raises_404_error(self):
        """Test that 404 raises ResourceNotFoundError."""
        mock_next = MagicMock()
        mock_response = ResponseContext(
            status_code=404,
            headers={},
            data={},
            request=RequestContext(
                method="GET",
                url="https://api.example.com/v1/users/1",
                headers={},
                params=None,
                data=None,
            ),
        )
        mock_next.handle.return_value = mock_response

        behavior = StatusCodeValidationBehavior(next_behavior=mock_next)
        request = RequestContext(
            method="GET",
            url="https://api.example.com/v1/users/1",
            headers={},
            params=None,
            data=None,
        )

        with pytest.raises(ResourceNotFoundError) as exc_info:
            behavior.handle(request)

        assert exc_info.value.context is not None
        assert exc_info.value.context.response_status == 404

    def test_raises_status_code_error(self):
        """Test that unexpected status codes raise StatusCodeError."""
        mock_next = MagicMock()
        mock_response = ResponseContext(
            status_code=418,
            headers={},
            data={},
            request=RequestContext(
                method="GET",
                url="https://api.example.com/v1/users/1",
                headers={},
                params=None,
                data=None,
            ),
        )
        mock_next.handle.return_value = mock_response

        behavior = StatusCodeValidationBehavior(next_behavior=mock_next)
        request = RequestContext(
            method="GET",
            url="https://api.example.com/v1/users/1",
            headers={},
            params=None,
            data=None,
        )

        with pytest.raises(StatusCodeError) as exc_info:
            behavior.handle(request)

        assert exc_info.value.context is not None
        assert exc_info.value.context.response_status == 418


class TestHttpBehavior:
    """Test HttpBehavior."""

    def test_makes_http_request(self, requests_mock):
        """Test that HttpBehavior makes actual HTTP requests."""
        requests_mock.get(
            "https://api.example.com/v1/users/1",
            json={"id": 1, "name": "John", "email": "john@example.com"},
        )

        session = requests.Session()
        behavior = HttpBehavior(session=session, timeout=30, verify_ssl=True)
        request = RequestContext(
            method="GET",
            url="https://api.example.com/v1/users/1",
            headers={},
            params=None,
            data=None,
        )

        response = behavior.handle(request)
        assert response.status_code == 200
        assert response.data == {"id": 1, "name": "John", "email": "john@example.com"}

    def test_handles_connection_error(self):
        """Test that connection errors are handled."""
        session = MagicMock()
        session.request.side_effect = requests.exceptions.ConnectionError("Connection failed")

        behavior = HttpBehavior(session=session, timeout=30, verify_ssl=True)
        request = RequestContext(
            method="GET",
            url="https://api.example.com/v1/users/1",
            headers={},
            params=None,
            data=None,
        )

        with pytest.raises(ConnectionError) as exc_info:
            behavior.handle(request)

        assert exc_info.value.context is not None

    def test_handles_timeout_error(self):
        """Test that timeout errors are handled."""
        session = MagicMock()
        session.request.side_effect = requests.exceptions.Timeout("Request timed out")

        behavior = HttpBehavior(session=session, timeout=30, verify_ssl=True)
        request = RequestContext(
            method="GET",
            url="https://api.example.com/v1/users/1",
            headers={},
            params=None,
            data=None,
        )

        with pytest.raises(TimeoutError) as exc_info:
            behavior.handle(request)

        assert exc_info.value.context is not None

    def test_handles_json_decode_error(self, requests_mock):
        """Test that non-JSON responses are handled."""
        requests_mock.get(
            "https://api.example.com/v1/users/1",
            text="Not JSON",
            headers={"Content-Type": "text/plain"},
        )

        session = requests.Session()
        behavior = HttpBehavior(session=session, timeout=30, verify_ssl=True)
        request = RequestContext(
            method="GET",
            url="https://api.example.com/v1/users/1",
            headers={},
            params=None,
            data=None,
        )

        response = behavior.handle(request)
        assert response.status_code == 200
        assert response.data == {"raw_content": "Not JSON"}

    def test_handles_empty_response(self, requests_mock):
        """Test that empty responses are handled."""
        requests_mock.get("https://api.example.com/v1/users/1", status_code=204)

        session = requests.Session()
        behavior = HttpBehavior(session=session, timeout=30, verify_ssl=True)
        request = RequestContext(
            method="GET",
            url="https://api.example.com/v1/users/1",
            headers={},
            params=None,
            data=None,
        )

        response = behavior.handle(request)
        assert response.status_code == 204
        assert response.data is None


class TestInterceptorBehavior:
    """Test InterceptorBehavior."""

    def test_calls_interceptors(self):
        """Test that interceptors are called."""
        mock_interceptor = MagicMock(spec=Interceptor)
        mock_next = MagicMock()
        mock_response = ResponseContext(
            status_code=200,
            headers={},
            data={},
            request=RequestContext(
                method="GET",
                url="https://api.example.com/v1/users/1",
                headers={},
                params=None,
                data=None,
            ),
        )
        mock_next.handle.return_value = mock_response

        behavior = InterceptorBehavior(interceptors=[mock_interceptor], next_behavior=mock_next)
        request = RequestContext(
            method="GET",
            url="https://api.example.com/v1/users/1",
            headers={},
            params=None,
            data=None,
        )

        result = behavior.handle(request)
        assert result == mock_response
        mock_interceptor.on_before_request.assert_called_once_with(request)
        mock_interceptor.on_after_response.assert_called_once_with(mock_response)

    def test_calls_interceptors_on_error(self):
        """Test that interceptors are called on error."""
        mock_interceptor = MagicMock(spec=Interceptor)
        mock_next = MagicMock()
        error = AuthenticationError("Auth failed", context=None)
        mock_next.handle.side_effect = error

        behavior = InterceptorBehavior(interceptors=[mock_interceptor], next_behavior=mock_next)
        request = RequestContext(
            method="GET",
            url="https://api.example.com/v1/users/1",
            headers={},
            params=None,
            data=None,
        )

        with pytest.raises(AuthenticationError):
            behavior.handle(request)

        mock_interceptor.on_before_request.assert_called_once_with(request)
        mock_interceptor.on_error.assert_called_once_with(error)
