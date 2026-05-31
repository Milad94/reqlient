"""Bulkhead (concurrency isolation) for the asynchronous client.

See ``reqlient.sync.bulkhead`` for the rationale. The async variant uses an
``asyncio.Semaphore`` and is per-event-loop / in-memory by design.
"""

import asyncio
import logging
import threading
from typing import Optional, Set

logger = logging.getLogger(__name__)


class AsyncBulkhead:
    """An asyncio-semaphore-based concurrency limiter for a single service.

    Args:
        service_name: Name of the service this bulkhead guards.
        max_concurrent: Maximum number of concurrent in-flight requests allowed.
        max_wait: Seconds to wait for a free slot before rejecting. ``0`` (the
            default) means reject immediately when full (fail fast); a positive
            value bounds how long a caller will await a slot.
    """

    def __init__(self, service_name: str, max_concurrent: int, max_wait: float = 0.0):
        if max_concurrent < 1:
            raise ValueError("max_concurrent must be >= 1")
        self.service_name = service_name
        self.max_concurrent = max_concurrent
        self.max_wait = max_wait
        self._semaphore = asyncio.Semaphore(max_concurrent)

    async def try_acquire(self) -> bool:
        """Attempt to acquire a slot. Returns True on success, False if full."""
        if self.max_wait and self.max_wait > 0:
            try:
                await asyncio.wait_for(self._semaphore.acquire(), timeout=self.max_wait)
                return True
            except asyncio.TimeoutError:
                return False

        # Immediate (non-blocking) acquire. asyncio is single-threaded, so there
        # is no await between the `locked()` check and `acquire()`, making this
        # race-free: if a slot is free, acquire() completes without suspending.
        if self._semaphore.locked():
            return False
        await self._semaphore.acquire()
        return True

    def release(self) -> None:
        """Release a previously acquired slot."""
        self._semaphore.release()


class AsyncBulkheadRegistry:
    """Registry for managing per-service async bulkheads across the application.

    Mirrors :class:`reqlient.sync.bulkhead.BulkheadRegistry`. Because bulkheads
    are pure in-memory semaphores (no Redis/I/O), ``get`` is synchronous.
    """

    _default_max_concurrent: int = 10
    _default_max_wait: float = 0.0
    _bulkheads: dict[str, AsyncBulkhead] = {}
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
    ) -> AsyncBulkhead:
        """Get or create the async bulkhead for a service (first config wins)."""
        with cls._lock:
            if service_name in cls._bulkheads:
                return cls._bulkheads[service_name]

            bulkhead = AsyncBulkhead(
                service_name=service_name,
                max_concurrent=max_concurrent
                if max_concurrent is not None
                else cls._default_max_concurrent,
                max_wait=max_wait if max_wait is not None else cls._default_max_wait,
            )
            cls._bulkheads[service_name] = bulkhead
            logger.info(
                f"Created in-memory async bulkhead for {service_name} "
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
