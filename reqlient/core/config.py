"""Configuration objects for the REST clients.

Resilience and transport settings are grouped into small frozen dataclasses,
one per concern, instead of a long list of flat constructor keyword arguments.
Each policy object is optional on the client: pass an instance to enable/tune it,
or ``None`` to disable it.

Because the dataclasses are frozen (immutable), a single default instance can be
shared safely as a constructor default value.
"""

from dataclasses import dataclass
from typing import Dict, FrozenSet, Optional

# Single source of truth for the default set of retryable HTTP status codes.
DEFAULT_RETRY_STATUS_CODES: FrozenSet[int] = frozenset({408, 429, 500, 502, 503, 504})


@dataclass(frozen=True)
class TransportConfig:
    """HTTP transport settings applied to the underlying httpx client.

    Args:
        timeout: Request timeout in seconds.
        verify_ssl: Whether to verify TLS certificates.
        default_headers: Headers sent with every request. Defaults to
            ``{"Content-Type": "application/json"}`` when None.
    """

    timeout: int = 30
    verify_ssl: bool = True
    default_headers: Optional[Dict[str, str]] = None


@dataclass(frozen=True)
class RetryConfig:
    """Automatic retry policy for transient failures.

    Args:
        max_retries: Maximum number of retry attempts after the first try.
        backoff_factor: Multiplier for exponential backoff between retries.
        status_codes: HTTP status codes that should trigger a retry.
    """

    max_retries: int = 3
    backoff_factor: float = 0.5
    status_codes: FrozenSet[int] = DEFAULT_RETRY_STATUS_CODES


@dataclass(frozen=True)
class CircuitBreakerConfig:
    """Circuit breaker policy.

    The breaker instance is resolved from (and shared via) the circuit breaker
    registry keyed by the client's ``service_name``.

    Args:
        fail_max: Number of failures before the circuit opens.
        reset_timeout: Seconds to wait before trying half-open.
    """

    fail_max: int = 5
    reset_timeout: int = 60


@dataclass(frozen=True)
class BulkheadConfig:
    """Bulkhead (concurrency isolation) policy.

    The bulkhead instance is resolved from (and shared via) the bulkhead registry
    keyed by the client's ``service_name``.

    Args:
        max_concurrent: Maximum number of concurrent in-flight requests allowed.
        max_wait: Seconds to wait for a free slot before raising BulkheadFullError.
            ``0`` (default) means reject immediately when full.
    """

    max_concurrent: int = 10
    max_wait: float = 0.0
