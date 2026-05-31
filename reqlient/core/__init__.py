"""Core shared modules used by both sync and async clients."""

from .config import (
    DEFAULT_RETRY_STATUS_CODES,
    BulkheadConfig,
    CircuitBreakerConfig,
    RetryConfig,
    TransportConfig,
)
from .errors import (
    AuthenticationError,
    AuthorizationError,
    BulkheadFullError,
    CircuitBreakerOpenError,
    ConnectionError,
    ErrorContext,
    RateLimitError,
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
from .request_response import RequestContext, ResponseContext

__all__ = [
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
    # Config
    "TransportConfig",
    "RetryConfig",
    "CircuitBreakerConfig",
    "BulkheadConfig",
    "DEFAULT_RETRY_STATUS_CODES",
    # Request/Response
    "RequestContext",
    "ResponseContext",
]
