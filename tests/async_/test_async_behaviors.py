"""
Comprehensive tests for all async behaviors in the pipeline.
"""

from unittest.mock import AsyncMock

import httpx
import pytest
from pydantic import BaseModel

from reqlient.async_.behaviors import (
    AsyncHttpBehavior,
    AsyncIdempotencyHeaderBehavior,
    AsyncInterceptorBehavior,
    AsyncLoggingBehavior,
    AsyncRequestDataSchemaValidationBehavior,
    AsyncResponseDataSchemaValidationBehavior,
    AsyncRetryBehavior,
    AsyncStatusCodeValidationBehavior,
)
from reqlient.async_.interceptors import AsyncInterceptor
from reqlient.core.errors import (
    AuthenticationError,
    ConnectionError,
    RequestValidationError,
    ResourceNotFoundError,
    ResponseValidationError,
    TimeoutError,
)
from reqlient.core.request_response import RequestContext, ResponseContext


class User(BaseModel):
    id: int
    name: str
    email: str


class CreateUserRequest(BaseModel):
    name: str
    email: str


@pytest.mark.asyncio
class TestAsyncLoggingBehavior:
    """Test AsyncLoggingBehavior."""

    async def test_logs_request_and_response(self, mock_logger):
        """Test that requests and responses are logged."""
        mock_next = AsyncMock()
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

        behavior = AsyncLoggingBehavior(logger=mock_logger, next_behavior=mock_next)
        request = RequestContext(
            method="GET",
            url="https://api.example.com/v1/users/1",
            headers={},
            params=None,
            data=None,
        )

        result = await behavior.handle(request)

        assert result == mock_response
        assert mock_logger.info.call_count == 2  # Request and response

    async def test_logs_errors(self, mock_logger):
        """Test that errors are logged."""
        mock_next = AsyncMock()
        error = AuthenticationError("Auth failed", context=None)
        mock_next.handle.side_effect = error

        behavior = AsyncLoggingBehavior(logger=mock_logger, next_behavior=mock_next)
        request = RequestContext(
            method="GET",
            url="https://api.example.com/v1/users/1",
            headers={},
            params=None,
            data=None,
        )

        with pytest.raises(AuthenticationError):
            await behavior.handle(request)

        # Error should be logged (may be called multiple times if context is logged separately)
        assert mock_logger.error.call_count >= 1


@pytest.mark.asyncio
class TestAsyncRetryBehavior:
    """Test AsyncRetryBehavior."""

    async def test_retries_on_retryable_error(self):
        """Test that retry behavior retries on retryable errors."""

        mock_next = AsyncMock()
        call_count = 0

        async def failing_handler(request):
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise ConnectionError("Connection failed", context=None)
            return ResponseContext(
                status_code=200,
                headers={},
                data={},
                request=request,
            )

        mock_next.handle.side_effect = failing_handler

        behavior = AsyncRetryBehavior(
            max_retries=3, backoff_factor=0.01, retry_status_codes=set(), next_behavior=mock_next
        )
        request = RequestContext(
            method="GET",
            url="https://api.example.com/v1/users/1",
            headers={},
            params=None,
            data=None,
        )

        result = await behavior.handle(request)
        assert result.status_code == 200
        assert call_count == 2

    async def test_stops_after_max_retries(self):
        """Test that retry stops after max retries."""
        mock_next = AsyncMock()
        error = ConnectionError("Connection failed", context=None)
        mock_next.handle.side_effect = error

        behavior = AsyncRetryBehavior(
            max_retries=2, backoff_factor=0.01, retry_status_codes=set(), next_behavior=mock_next
        )
        request = RequestContext(
            method="GET",
            url="https://api.example.com/v1/users/1",
            headers={},
            params=None,
            data=None,
        )

        with pytest.raises(ConnectionError):
            await behavior.handle(request)

        assert mock_next.handle.call_count == 3  # Initial + 2 retries


@pytest.mark.asyncio
class TestAsyncHttpBehavior:
    """Test AsyncHttpBehavior."""

    async def test_makes_http_request(self):
        """Test that HTTP request is made."""
        async with httpx.AsyncClient() as client:
            # Mock the request method
            original_request = client.request
            mock_response = httpx.Response(
                200,
                json={"id": 1, "name": "John", "email": "john@example.com"},
                request=httpx.Request("GET", "https://api.example.com/v1/users/1"),
            )
            client.request = AsyncMock(return_value=mock_response)

            behavior = AsyncHttpBehavior(lambda: client, timeout=30, verify_ssl=True)
            request = RequestContext(
                method="GET",
                url="https://api.example.com/v1/users/1",
                headers={},
                params=None,
                data=None,
            )

            result = await behavior.handle(request)
            assert result.status_code == 200
            assert result.data == {"id": 1, "name": "John", "email": "john@example.com"}

    async def test_handles_connection_error(self):
        """Test that connection errors are handled."""
        async with httpx.AsyncClient() as client:
            client.request = AsyncMock(side_effect=httpx.ConnectError("Connection failed"))

            behavior = AsyncHttpBehavior(lambda: client, timeout=30, verify_ssl=True)
            request = RequestContext(
                method="GET",
                url="https://api.example.com/v1/users/1",
                headers={},
                params=None,
                data=None,
            )

            with pytest.raises(ConnectionError):
                await behavior.handle(request)

    async def test_handles_timeout_error(self):
        """Test that timeout errors are handled."""
        async with httpx.AsyncClient() as client:
            client.request = AsyncMock(side_effect=httpx.TimeoutException("Timeout"))

            behavior = AsyncHttpBehavior(lambda: client, timeout=30, verify_ssl=True)
            request = RequestContext(
                method="GET",
                url="https://api.example.com/v1/users/1",
                headers={},
                params=None,
                data=None,
            )

            with pytest.raises(TimeoutError):
                await behavior.handle(request)


@pytest.mark.asyncio
class TestAsyncStatusCodeValidationBehavior:
    """Test AsyncStatusCodeValidationBehavior."""

    async def test_validates_expected_status(self):
        """Test that expected status codes pass validation."""
        mock_next = AsyncMock()
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

        behavior = AsyncStatusCodeValidationBehavior(next_behavior=mock_next)
        request = RequestContext(
            method="GET",
            url="https://api.example.com/v1/users/1",
            headers={},
            params=None,
            data=None,
        )

        result = await behavior.handle(request)
        assert result == mock_response

    async def test_raises_error_on_401(self):
        """Test that 401 raises AuthenticationError."""
        mock_next = AsyncMock()
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

        behavior = AsyncStatusCodeValidationBehavior(next_behavior=mock_next)
        request = RequestContext(
            method="GET",
            url="https://api.example.com/v1/users/1",
            headers={},
            params=None,
            data=None,
        )

        with pytest.raises(AuthenticationError):
            await behavior.handle(request)

    async def test_raises_error_on_404(self):
        """Test that 404 raises ResourceNotFoundError."""
        mock_next = AsyncMock()
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

        behavior = AsyncStatusCodeValidationBehavior(next_behavior=mock_next)
        request = RequestContext(
            method="GET",
            url="https://api.example.com/v1/users/1",
            headers={},
            params=None,
            data=None,
        )

        with pytest.raises(ResourceNotFoundError):
            await behavior.handle(request)


@pytest.mark.asyncio
class TestAsyncInterceptorBehavior:
    """Test AsyncInterceptorBehavior."""

    async def test_calls_interceptors(self):
        """Test that interceptors are called."""
        mock_next = AsyncMock()
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

        class TestInterceptor(AsyncInterceptor):
            def __init__(self):
                self.before_called = False
                self.after_called = False

            async def on_before_request(self, request):
                self.before_called = True

            async def on_after_response(self, response):
                self.after_called = True

        interceptor = TestInterceptor()
        behavior = AsyncInterceptorBehavior(interceptors=[interceptor], next_behavior=mock_next)
        request = RequestContext(
            method="GET",
            url="https://api.example.com/v1/users/1",
            headers={},
            params=None,
            data=None,
        )

        await behavior.handle(request)

        assert interceptor.before_called is True
        assert interceptor.after_called is True

    async def test_calls_error_interceptor_on_error(self):
        """Test that error interceptor is called on error."""
        mock_next = AsyncMock()
        error = AuthenticationError("Auth failed", context=None)
        mock_next.handle.side_effect = error

        class TestInterceptor(AsyncInterceptor):
            def __init__(self):
                self.error_called = False
                self.error_instance = None

            async def on_error(self, error):
                self.error_called = True
                self.error_instance = error

        interceptor = TestInterceptor()
        behavior = AsyncInterceptorBehavior(interceptors=[interceptor], next_behavior=mock_next)
        request = RequestContext(
            method="GET",
            url="https://api.example.com/v1/users/1",
            headers={},
            params=None,
            data=None,
        )

        with pytest.raises(AuthenticationError):
            await behavior.handle(request)

        assert interceptor.error_called is True
        assert interceptor.error_instance == error
