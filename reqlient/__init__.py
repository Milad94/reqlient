"""
reqlient - A resilient HTTP client library for Python.

A production-grade, extensible, and resilient HTTP client for Python,
designed for reliable communication with external REST APIs.
"""

# Import from subpackages
from .async_ import (
    AsyncBulkhead,
    AsyncBulkheadRegistry,
    AsyncCircuitBreaker,
    AsyncCircuitBreakerRegistry,
    AsyncInterceptor,
    AsyncRestClient,
    TraceContextInterceptor,
)
from .core import (
    DEFAULT_RETRY_STATUS_CODES,
    AuthenticationError,
    AuthorizationError,
    BulkheadConfig,
    BulkheadFullError,
    CircuitBreakerConfig,
    CircuitBreakerOpenError,
    ConnectionError,
    ErrorContext,
    RateLimitError,
    RequestContext,
    RequestError,
    RequestValidationError,
    ResourceNotFoundError,
    ResponseContext,
    ResponseValidationError,
    RestClientError,
    RetryableError,
    RetryConfig,
    ServerError,
    StatusCodeError,
    TimeoutError,
    TransportConfig,
)
from .sync import (
    Bulkhead,
    BulkheadRegistry,
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
    # Bulkheads
    "Bulkhead",
    "BulkheadRegistry",
    "AsyncBulkhead",
    "AsyncBulkheadRegistry",
    # Config
    "TransportConfig",
    "RetryConfig",
    "CircuitBreakerConfig",
    "BulkheadConfig",
    "DEFAULT_RETRY_STATUS_CODES",
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
    "BulkheadFullError",
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

__version__ = "0.5.0"
