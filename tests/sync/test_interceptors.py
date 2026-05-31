"""
Comprehensive tests for interceptors.
"""

from reqlient.core.request_response import RequestContext, ResponseContext
from reqlient.sync.interceptors import Interceptor


class TestInterceptor:
    """Test Interceptor base class and implementations."""

    def test_interceptor_base_class(self):
        """Test that Interceptor is an abstract base class."""
        # Interceptor has default implementations, so it can be instantiated
        # But it's meant to be subclassed
        interceptor = Interceptor()
        assert interceptor is not None

    def test_custom_interceptor_implementation(self):
        """Test implementing a custom interceptor."""

        class CustomInterceptor(Interceptor):
            def __init__(self):
                self.before_called = False
                self.after_called = False
                self.error_called = False
                self.error_instance = None

            def on_before_request(self, request: RequestContext):
                self.before_called = True
                request.headers["X-Custom-Header"] = "custom-value"

            def on_after_response(self, response: ResponseContext):
                self.after_called = True

            def on_error(self, error):
                self.error_called = True
                self.error_instance = error

        interceptor = CustomInterceptor()
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
        interceptor.on_before_request(request)

        assert interceptor.before_called is True
        assert request.headers["X-Custom-Header"] == "custom-value"

        # Test on_after_response
        response = ResponseContext(
            status_code=200,
            headers={},
            data={},
            request=request,
        )
        interceptor.on_after_response(response)

        assert interceptor.after_called is True

        # Test on_error
        error = Exception("Test error")
        interceptor.on_error(error)

        assert interceptor.error_called is True
        assert interceptor.error_instance == error

    def test_interceptor_modifies_request(self):
        """Test that interceptor can modify request."""

        class HeaderInterceptor(Interceptor):
            def on_before_request(self, request: RequestContext):
                request.headers["X-Request-ID"] = "12345"
                request.headers["X-User-ID"] = "67890"

        interceptor = HeaderInterceptor()
        request = RequestContext(
            method="POST",
            url="https://api.example.com/v1/users",
            headers={"Content-Type": "application/json"},
            params=None,
            data=None,
        )

        interceptor.on_before_request(request)

        assert request.headers["X-Request-ID"] == "12345"
        assert request.headers["X-User-ID"] == "67890"
        assert request.headers["Content-Type"] == "application/json"

    def test_interceptor_modifies_response(self):
        """Test that interceptor can access response."""

        class LoggingInterceptor(Interceptor):
            def __init__(self):
                self.response_statuses = []

            def on_after_response(self, response: ResponseContext):
                self.response_statuses.append(response.status_code)

        interceptor = LoggingInterceptor()
        request = RequestContext(
            method="GET",
            url="https://api.example.com/v1/users/1",
            headers={},
            params=None,
            data=None,
        )

        response1 = ResponseContext(status_code=200, headers={}, data={}, request=request)
        response2 = ResponseContext(status_code=201, headers={}, data={}, request=request)

        interceptor.on_after_response(response1)
        interceptor.on_after_response(response2)

        assert interceptor.response_statuses == [200, 201]

    def test_interceptor_handles_errors(self):
        """Test that interceptor can handle errors."""

        class ErrorLoggingInterceptor(Interceptor):
            def __init__(self):
                self.errors = []

            def on_error(self, error):
                self.errors.append(error)

        interceptor = ErrorLoggingInterceptor()

        error1 = ValueError("Error 1")
        error2 = RuntimeError("Error 2")

        interceptor.on_error(error1)
        interceptor.on_error(error2)

        assert len(interceptor.errors) == 2
        assert error1 in interceptor.errors
        assert error2 in interceptor.errors

    def test_multiple_interceptors(self):
        """Test that multiple interceptors work together."""

        class Interceptor1(Interceptor):
            def on_before_request(self, request: RequestContext):
                request.headers["X-Interceptor-1"] = "value1"

        class Interceptor2(Interceptor):
            def on_before_request(self, request: RequestContext):
                request.headers["X-Interceptor-2"] = "value2"

        interceptor1 = Interceptor1()
        interceptor2 = Interceptor2()

        request = RequestContext(
            method="GET",
            url="https://api.example.com/v1/users/1",
            headers={},
            params=None,
            data=None,
        )

        interceptor1.on_before_request(request)
        interceptor2.on_before_request(request)

        assert request.headers["X-Interceptor-1"] == "value1"
        assert request.headers["X-Interceptor-2"] == "value2"
