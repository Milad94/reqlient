from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Optional


@dataclass
class ErrorContext:
    """Context information for error handling and recovery."""

    timestamp: datetime
    request_url: str
    request_method: str
    request_headers: Dict[str, str]
    request_params: Optional[Dict[str, str]]
    request_data: Optional[Dict[str, Any]]
    response_status: Optional[int]
    response_headers: Optional[Dict[str, str]]
    response_data: Optional[Dict[str, Any]]
    error_message: str
    error_type: str
    retry_count: int = 0


class RestClientError(Exception):
    """Base exception for all RestClient errors, containing rich context."""

    def __init__(self, message: str, context: Optional[ErrorContext] = None):
        self.context = context
        self.message = message
        super().__init__(message)

    def __str__(self):
        """Provide a detailed string representation of the error."""
        if not self.context:
            return self.message

        details = [
            f"Error: {self.message}",
            f"  Type: {self.context.error_type}",
            f"  URL: {self.context.request_method} {self.context.request_url}",
        ]
        if self.context.response_status:
            details.append(f"  Status: {self.context.response_status}")
        if self.context.retry_count > 0:
            details.append(f"  Retries: {self.context.retry_count}")

        return "\n".join(details)


class RequestValidationError(RestClientError):
    """Raised when incoming request data fails Pydantic validation."""


class RequestError(RestClientError):
    """A generic error occurred during the HTTP request process."""


class StatusCodeError(RestClientError):
    """Raised when an unexpected HTTP status code is received."""


class RetryableError(RestClientError):
    """Base class for transient errors that can be safely retried."""


class ResponseValidationError(RestClientError):
    """Raised when outgoing response data fails Pydantic validation."""


class ConnectionError(RetryableError):
    """A network-level error occurred while trying to connect to the service."""


class TimeoutError(RetryableError):
    """The request timed out while waiting for a response from the service."""


class ServerError(RetryableError):
    """The server responded with an internal error (5xx status code)."""


class RateLimitError(RetryableError):
    """The server indicated the rate limit has been exceeded (e.g., 429 status)."""


class AuthenticationError(RestClientError):
    """The request failed due to an authentication error (e.g., 401 Unauthorized)."""


class AuthorizationError(RestClientError):
    """The request failed due to an authorization error (e.g., 403 Forbidden)."""


class ResourceNotFoundError(RestClientError):
    """The requested resource was not found on the server (e.g., 404 Not Found)."""


class CircuitBreakerOpenError(RestClientError):
    """The request was blocked because the circuit breaker is open."""


class BulkheadFullError(RestClientError):
    """The request was rejected because the service's bulkhead (concurrency
    limit) is full.

    This signals local overload — too many concurrent in-flight requests to the
    service — not a failure of the downstream service. It is intentionally NOT a
    ``RetryableError``: retrying immediately would not free a slot.
    """
