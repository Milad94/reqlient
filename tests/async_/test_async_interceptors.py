"""
Comprehensive tests for async interceptors.
"""

import pytest

from reqlient.async_.interceptors import AsyncInterceptor
from reqlient.core.request_response import RequestContext, ResponseContext


@pytest.mark.asyncio
class TestAsyncInterceptor:
    """Test AsyncInterceptor base class and implementations."""

    async def test_interceptor_base_class(self):
        """Test that AsyncInterceptor is an abstract base class."""
        # AsyncInterceptor has default implementations, so it can be instantiated
        # But it's meant to be subclassed
        interceptor = AsyncInterceptor()
        assert interceptor is not None

    async def test_custom_interceptor_implementation(self):
        """Test implementing a custom async interceptor."""

        class CustomAsyncInterceptor(AsyncInterceptor):
            def __init__(self):
                self.before_called = False
                self.after_called = False
                self.error_called = False
                self.error_instance = None

            async def on_before_request(self, request: RequestContext):
                self.before_called = True
                request.headers["X-Custom-Header"] = "custom-value"

            async def on_after_response(self, response: ResponseContext):
                self.after_called = True

            async def on_error(self, error):
                self.error_called = True
                self.error_instance = error

        interceptor = CustomAsyncInterceptor()
        assert interceptor.before_called is False
        assert interceptor.after_called is False
        assert interceptor.error_called is False

        # Test on_before_request
        request = RequestContext(
            method="GET",
            url="https://api.example.com/v1/users/1",
            headers={},
            params=None,
            data=None,
        )
        await interceptor.on_before_request(request)

        assert interceptor.before_called is True
        assert request.headers["X-Custom-Header"] == "custom-value"

        # Test on_after_response
        response = ResponseContext(
            status_code=200,
            headers={},
            data={},
            request=request,
        )
        await interceptor.on_after_response(response)

        assert interceptor.after_called is True

        # Test on_error
        error = Exception("Test error")
        await interceptor.on_error(error)

        assert interceptor.error_called is True
        assert interceptor.error_instance == error

    async def test_interceptor_modifies_request(self):
        """Test that interceptor can modify request."""

        class HeaderAsyncInterceptor(AsyncInterceptor):
            async def on_before_request(self, request: RequestContext):
                request.headers["X-Request-ID"] = "12345"
                request.headers["X-User-ID"] = "67890"

        interceptor = HeaderAsyncInterceptor()
        request = RequestContext(
            method="POST",
            url="https://api.example.com/v1/users",
            headers={"Content-Type": "application/json"},
            params=None,
            data=None,
        )

        await interceptor.on_before_request(request)

        assert request.headers["X-Request-ID"] == "12345"
        assert request.headers["X-User-ID"] == "67890"
        assert request.headers["Content-Type"] == "application/json"

    async def test_interceptor_modifies_response(self):
        """Test that interceptor can access response."""

        class LoggingAsyncInterceptor(AsyncInterceptor):
            def __init__(self):
                self.response_statuses = []

            async def on_after_response(self, response: ResponseContext):
                self.response_statuses.append(response.status_code)

        interceptor = LoggingAsyncInterceptor()
        request = RequestContext(
            method="GET",
            url="https://api.example.com/v1/users/1",
            headers={},
            params=None,
            data=None,
        )

        response1 = ResponseContext(status_code=200, headers={}, data={}, request=request)
        response2 = ResponseContext(status_code=201, headers={}, data={}, request=request)

        await interceptor.on_after_response(response1)
        await interceptor.on_after_response(response2)

        assert interceptor.response_statuses == [200, 201]

    async def test_interceptor_handles_errors(self):
        """Test that interceptor can handle errors."""

        class ErrorLoggingAsyncInterceptor(AsyncInterceptor):
            def __init__(self):
                self.errors = []

            async def on_error(self, error):
                self.errors.append(error)

        interceptor = ErrorLoggingAsyncInterceptor()

        error1 = ValueError("Error 1")
        error2 = RuntimeError("Error 2")

        await interceptor.on_error(error1)
        await interceptor.on_error(error2)

        assert len(interceptor.errors) == 2
        assert error1 in interceptor.errors
        assert error2 in interceptor.errors

    async def test_multiple_interceptors(self):
        """Test that multiple async interceptors work together."""

        class AsyncInterceptor1(AsyncInterceptor):
            async def on_before_request(self, request: RequestContext):
                request.headers["X-Interceptor-1"] = "value1"

        class AsyncInterceptor2(AsyncInterceptor):
            async def on_before_request(self, request: RequestContext):
                request.headers["X-Interceptor-2"] = "value2"

        interceptor1 = AsyncInterceptor1()
        interceptor2 = AsyncInterceptor2()

        request = RequestContext(
            method="GET",
            url="https://api.example.com/v1/users/1",
            headers={},
            params=None,
            data=None,
        )

        await interceptor1.on_before_request(request)
        await interceptor2.on_before_request(request)

        assert request.headers["X-Interceptor-1"] == "value1"
        assert request.headers["X-Interceptor-2"] == "value2"
