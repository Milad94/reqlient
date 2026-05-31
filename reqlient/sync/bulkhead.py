"""Bulkhead (concurrency isolation) for the synchronous client.

A bulkhead caps the number of concurrent in-flight requests to a single service
so that a slow or failing dependency cannot exhaust local resources (threads,
sockets, connection-pool slots) and starve calls to other services.

Unlike the circuit breaker, bulkhead state is intentionally **in-memory and
per-process**: it protects *this* process's local resources, so there is nothing
to share across processes (a distributed semaphore would add a network round-trip
to the hot path and protect the wrong thing). Bulkheads are kept per-thread-safe
via a counting semaphore.
"""

import logging
import threading
from typing import Optional, Set

logger = logging.getLogger(__name__)


class Bulkhead:
    """A semaphore-based concurrency limiter for a single service.

    Args:
        service_name: Name of the service this bulkhead guards.
        max_concurrent: Maximum number of concurrent in-flight requests allowed.
        max_wait: Seconds to wait for a free slot before rejecting. ``0`` (the
            default) means reject immediately when full (fail fast); a positive
            value bounds how long a caller will block waiting for a slot.
    """

    def __init__(self, service_name: str, max_concurrent: int, max_wait: float = 0.0):
        if max_concurrent < 1:
            raise ValueError("max_concurrent must be >= 1")
        self.service_name = service_name
        self.max_concurrent = max_concurrent
        self.max_wait = max_wait
        self._semaphore = threading.BoundedSemaphore(max_concurrent)

    def try_acquire(self) -> bool:
        """Attempt to acquire a slot. Returns True on success, False if full."""
        if self.max_wait and self.max_wait > 0:
            return self._semaphore.acquire(blocking=True, timeout=self.max_wait)
        return self._semaphore.acquire(blocking=False)

    def release(self) -> None:
        """Release a previously acquired slot."""
        self._semaphore.release()


class BulkheadRegistry:
    """Registry for managing per-service bulkheads across the application.

    Configure once at application startup, then bulkheads are automatically
    created and shared when RestClient instances use the same service_name.

    Example:
        # Configure defaults once at startup
        BulkheadRegistry.configure(default_max_concurrent=20, default_max_wait=0.0)

        # RestClient auto-resolves a bulkhead when use_bulkhead=True
        client = RestClient(
            base_url="https://api.example.com",
            service_name="user_api",
            use_bulkhead=True,
            max_concurrent_requests=10,
        )

        # Or get one explicitly
        bulkhead = BulkheadRegistry.get("payments", max_concurrent=5)
    """

    _default_max_concurrent: int = 10
    _default_max_wait: float = 0.0
    _bulkheads: dict[str, Bulkhead] = {}
    _configured: bool = False
    _lock: threading.Lock = threading.Lock()

    @classmethod
    def configure(
        cls,
        default_max_concurrent: int = 10,
        default_max_wait: float = 0.0,
    ) -> None:
        """Set registry-wide defaults. Call once at application startup."""
        cls._default_max_concurrent = default_max_concurrent
        cls._default_max_wait = default_max_wait
        cls._configured = True

    @classmethod
    def get(
        cls,
        service_name: str,
        max_concurrent: Optional[int] = None,
        max_wait: Optional[float] = None,
    ) -> Bulkhead:
        """Get or create the bulkhead for a service.

        If a bulkhead already exists for ``service_name`` it is returned as-is
        (first configuration wins), so all clients targeting the same service
        share one concurrency limit.
        """
        with cls._lock:
            if service_name in cls._bulkheads:
                return cls._bulkheads[service_name]

            bulkhead = Bulkhead(
                service_name=service_name,
                max_concurrent=max_concurrent
                if max_concurrent is not None
                else cls._default_max_concurrent,
                max_wait=max_wait if max_wait is not None else cls._default_max_wait,
            )
            cls._bulkheads[service_name] = bulkhead
            logger.info(
                f"Created in-memory bulkhead for {service_name} "
                f"(max_concurrent={bulkhead.max_concurrent}, max_wait={bulkhead.max_wait})"
            )
            return bulkhead

    @classmethod
    def reset(cls) -> None:
        """Reset the registry. Clears all bulkheads and defaults (for tests)."""
        with cls._lock:
            cls._bulkheads.clear()
            cls._default_max_concurrent = 10
            cls._default_max_wait = 0.0
            cls._configured = False

    @classmethod
    def is_configured(cls) -> bool:
        """Check whether the registry has been configured."""
        return cls._configured

    @classmethod
    def get_registered_services(cls) -> Set[str]:
        """Get the set of service names that have bulkheads registered."""
        return set(cls._bulkheads.keys())
