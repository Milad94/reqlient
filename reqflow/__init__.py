"""
reqflow - A resilient HTTP client library for Python.

A production-grade, extensible, and resilient HTTP client for Python,
designed for reliable communication with external REST APIs.
"""

# Import from subpackages
from .async_ import (
    AsyncCircuitBreaker,
    AsyncInterceptor,
    AsyncRestClient,
    create_shared_async_breaker,
)
from .core import (
    AuthenticationError,
    AuthorizationError,
    CircuitBreakerOpenError,
    ConnectionError,
    ErrorContext,
    RateLimitError,
    RequestError,
    RequestValidationError,
    ResourceNotFoundError,
    ResponseContext,
    ResponseValidationError,
    RestClientError,
    RetryableError,
    ServerError,
    StatusCodeError,
    TimeoutError,
    RequestContext,
)
from .sync import (
    create_shared_breaker,
    Interceptor,
    RestClient,
)

__all__ = [
    # Main clients
    "RestClient",
    "AsyncRestClient",
    # Circuit breakers (sync)
    "create_shared_breaker",
    # Circuit breakers (async)
    "AsyncCircuitBreaker",
    "create_shared_async_breaker",
    # Errors
    "RestClientError",
    "RequestValidationError",
    "ResponseValidationError",
    "RequestError",
    "StatusCodeError",
    "RetryableError",
    "ConnectionError",
    "TimeoutError",
    "ServerError",
    "RateLimitError",
    "AuthenticationError",
    "AuthorizationError",
    "ResourceNotFoundError",
    "CircuitBreakerOpenError",
    "ErrorContext",
    # Interceptors (sync)
    "Interceptor",
    # Interceptors (async)
    "AsyncInterceptor",
    # Request/Response
    "RequestContext",
    "ResponseContext",
]

__version__ = "1.0.0"
