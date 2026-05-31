"""
reqlient - A resilient HTTP client library for Python.

A production-grade, extensible, and resilient HTTP client for Python,
designed for reliable communication with external REST APIs.
"""

# Import from subpackages
from .async_ import (
    AsyncCircuitBreaker,
    AsyncCircuitBreakerRegistry,
    AsyncInterceptor,
    AsyncRestClient,
    TraceContextInterceptor,
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
    CircuitBreakerRegistry,
    Interceptor,
    RestClient,
)

__all__ = [
    # Main clients
    "RestClient",
    "AsyncRestClient",
    # Circuit breaker registries
    "CircuitBreakerRegistry",
    "AsyncCircuitBreakerRegistry",
    # Circuit breakers (async)
    "AsyncCircuitBreaker",
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
    "TraceContextInterceptor",
    # Request/Response
    "RequestContext",
    "ResponseContext",
]

__version__ = "0.2.0"
