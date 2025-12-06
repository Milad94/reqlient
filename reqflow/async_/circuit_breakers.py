import logging
import os
import time
from abc import ABC, abstractmethod
from typing import Any, Callable, Dict, Optional

import redis.asyncio as aioredis
from dotenv import load_dotenv
import asyncio
from ..core.errors import CircuitBreakerOpenError

# Load environment variables from .env file
load_dotenv()

logger = logging.getLogger(__name__)

# Circuit breaker states
CLOSED = "closed"
OPEN = "open"
HALF_OPEN = "half_open"

# Redis configuration from environment variables with defaults
REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
REDIS_DB = int(os.getenv("REDIS_DB", "0"))

# Global async Redis client (singleton pattern)
_async_redis_client = None


async def _get_async_redis_client():
    """Get or create the async Redis client singleton."""
    global _async_redis_client
    if _async_redis_client is None:
        try:
            _async_redis_client = await aioredis.from_url(
                f"redis://{REDIS_HOST}:{REDIS_PORT}/{REDIS_DB}"
            )
            await _async_redis_client.ping()
            logger.info(
                f"Connected to Redis at {REDIS_HOST}:{REDIS_PORT} for async circuit breaker"
            )
        except Exception as e:
            logger.warning(
                f"Failed to connect to Redis at {REDIS_HOST}:{REDIS_PORT} for async circuit breaker. "
                f"Will use in-memory storage. Error: {e}"
            )
            _async_redis_client = None
    return _async_redis_client


class AsyncCircuitBreakerStorage(ABC):
    """Abstract base class for async circuit breaker storage."""

    @abstractmethod
    async def get_state(self, key: str) -> Optional[str]:
        """Get the circuit breaker state."""

    @abstractmethod
    async def set_state(self, key: str, state: str):
        """Set the circuit breaker state."""

    @abstractmethod
    async def increment_failure_count(self, key: str) -> int:
        """Increment failure count and return new count."""

    @abstractmethod
    async def reset_failure_count(self, key: str):
        """Reset failure count to zero."""

    @abstractmethod
    async def get_failure_count(self, key: str) -> int:
        """Get current failure count."""

    @abstractmethod
    async def set_last_failure_time(self, key: str, timestamp: float):
        """Set the last failure timestamp."""

    @abstractmethod
    async def get_last_failure_time(self, key: str) -> Optional[float]:
        """Get the last failure timestamp."""


class AsyncInMemoryStorage(AsyncCircuitBreakerStorage):
    """In-memory storage for async circuit breaker (single process only)."""

    def __init__(self, namespace: str):
        self.namespace = namespace
        self._lock = asyncio.Lock()
        self._state: Dict[str, Any] = {}

    def _get_key(self, suffix: str) -> str:
        """Generate a namespaced key."""
        return f"{self.namespace}:{suffix}"

    async def get_state(self, key: str) -> Optional[str]:
        """Get the circuit breaker state."""
        async with self._lock:
            return self._state.get(self._get_key(f"{key}:state"))

    async def set_state(self, key: str, state: str):
        """Set the circuit breaker state."""
        async with self._lock:
            self._state[self._get_key(f"{key}:state")] = state

    async def increment_failure_count(self, key: str) -> int:
        """Increment failure count and return new count."""
        async with self._lock:
            count_key = self._get_key(f"{key}:failures")
            current = self._state.get(count_key, 0)
            new_count = current + 1
            self._state[count_key] = new_count
            return new_count

    async def reset_failure_count(self, key: str):
        """Reset failure count to zero."""
        async with self._lock:
            self._state[self._get_key(f"{key}:failures")] = 0

    async def get_failure_count(self, key: str) -> int:
        """Get current failure count."""
        async with self._lock:
            return self._state.get(self._get_key(f"{key}:failures"), 0)

    async def set_last_failure_time(self, key: str, timestamp: float):
        """Set the last failure timestamp."""
        async with self._lock:
            self._state[self._get_key(f"{key}:last_failure")] = timestamp

    async def get_last_failure_time(self, key: str) -> Optional[float]:
        """Get the last failure timestamp."""
        async with self._lock:
            return self._state.get(self._get_key(f"{key}:last_failure"))


class AsyncRedisStorage(AsyncCircuitBreakerStorage):
    """Redis-backed storage for async circuit breaker (shared across processes)."""

    def __init__(self, redis_client: Any, namespace: str):
        self.redis_client = redis_client
        self.namespace = namespace

    def _get_key(self, suffix: str) -> str:
        """Generate a namespaced Redis key."""
        return f"breaker:{self.namespace}:{suffix}"

    async def get_state(self, key: str) -> Optional[str]:
        """Get the circuit breaker state."""
        try:
            value = await self.redis_client.get(self._get_key(f"{key}:state"))
            return value.decode("utf-8") if value else None
        except Exception as e:
            logger.warning(f"Failed to get state from Redis: {e}")
            return None

    async def set_state(self, key: str, state: str):
        """Set the circuit breaker state."""
        try:
            await self.redis_client.set(self._get_key(f"{key}:state"), state)
        except Exception as e:
            logger.warning(f"Failed to set state in Redis: {e}")

    async def increment_failure_count(self, key: str) -> int:
        """Increment failure count and return new count."""
        try:
            count_key = self._get_key(f"{key}:failures")
            new_count = await self.redis_client.incr(count_key)
            return new_count
        except Exception as e:
            logger.warning(f"Failed to increment failure count in Redis: {e}")
            return 0

    async def reset_failure_count(self, key: str):
        """Reset failure count to zero."""
        try:
            await self.redis_client.delete(self._get_key(f"{key}:failures"))
        except Exception as e:
            logger.warning(f"Failed to reset failure count in Redis: {e}")

    async def get_failure_count(self, key: str) -> int:
        """Get current failure count."""
        try:
            value = await self.redis_client.get(self._get_key(f"{key}:failures"))
            return int(value.decode("utf-8")) if value else 0
        except Exception as e:
            logger.warning(f"Failed to get failure count from Redis: {e}")
            return 0

    async def set_last_failure_time(self, key: str, timestamp: float):
        """Set the last failure timestamp."""
        try:
            await self.redis_client.set(self._get_key(f"{key}:last_failure"), str(timestamp))
        except Exception as e:
            logger.warning(f"Failed to set last failure time in Redis: {e}")

    async def get_last_failure_time(self, key: str) -> Optional[float]:
        """Get the last failure timestamp."""
        try:
            value = await self.redis_client.get(self._get_key(f"{key}:last_failure"))
            if value:
                return float(value.decode("utf-8"))
            return None
        except Exception as e:
            logger.warning(f"Failed to get last failure time from Redis: {e}")
            return None


class AsyncCircuitBreaker:
    """
    Async circuit breaker implementation.

    States:
    - CLOSED: Normal operation, requests pass through
    - OPEN: Circuit is open, requests fail fast
    - HALF_OPEN: Testing if service has recovered, allows one request

    Transitions:
    - CLOSED → OPEN: When failure count exceeds fail_max
    - OPEN → HALF_OPEN: After reset_timeout seconds
    - HALF_OPEN → CLOSED: On successful request
    - HALF_OPEN → OPEN: On failed request
    """

    def __init__(
        self,
        fail_max: int = 5,
        reset_timeout: int = 60,
        storage: Optional[AsyncCircuitBreakerStorage] = None,
        service_name: str = "default",
    ):
        """
        Initialize async circuit breaker.

        Args:
            fail_max: Number of failures before opening circuit
            reset_timeout: Seconds to wait before trying half-open
            storage: Storage backend (if None, uses in-memory)
            service_name: Name of the service (for namespacing)
        """
        self.fail_max = fail_max
        self.reset_timeout = reset_timeout
        self.service_name = service_name
        self.storage = storage or AsyncInMemoryStorage(namespace=service_name)
        self._lock = asyncio.Lock()

    async def _get_state(self) -> str:
        """Get current state from storage."""
        state = await self.storage.get_state(self.service_name)
        return state if state else CLOSED

    async def _set_state(self, state: str):
        """Set state in storage."""
        await self.storage.set_state(self.service_name, state)

    async def _get_failure_count(self) -> int:
        """Get current failure count."""
        return await self.storage.get_failure_count(self.service_name)

    async def _increment_failure_count(self) -> int:
        """Increment failure count."""
        return await self.storage.increment_failure_count(self.service_name)

    async def _reset_failure_count(self):
        """Reset failure count."""
        await self.storage.reset_failure_count(self.service_name)

    async def _get_last_failure_time(self) -> Optional[float]:
        """Get last failure timestamp."""
        return await self.storage.get_last_failure_time(self.service_name)

    async def _set_last_failure_time(self, timestamp: float):
        """Set last failure timestamp."""
        await self.storage.set_last_failure_time(self.service_name, timestamp)

    async def _check_state(self):
        """Check if circuit should transition based on timeout."""
        state = await self._get_state()
        if state == OPEN:
            last_failure = await self._get_last_failure_time()
            if last_failure and (time.time() - last_failure) >= self.reset_timeout:
                await self._set_state(HALF_OPEN)
                logger.info(f"Circuit breaker for {self.service_name} transitioning to HALF_OPEN")

    async def _record_success(self):
        """Record a successful request."""
        async with self._lock:
            state = await self._get_state()
            if state == HALF_OPEN:
                await self._set_state(CLOSED)
                await self._reset_failure_count()
                logger.info(
                    f"Circuit breaker for {self.service_name} closed after successful request"
                )
            elif state == CLOSED:
                # Reset failure count on success in closed state
                await self._reset_failure_count()

    async def _record_failure(self):
        """Record a failed request."""
        async with self._lock:
            await self._check_state()
            state = await self._get_state()

            if state == HALF_OPEN:
                # Failed in half-open, go back to open
                await self._set_state(OPEN)
                await self._set_last_failure_time(time.time())
                await self._reset_failure_count()
                logger.warning(
                    f"Circuit breaker for {self.service_name} opened after failure in HALF_OPEN"
                )
            elif state == CLOSED:
                # Increment failure count
                count = await self._increment_failure_count()
                await self._set_last_failure_time(time.time())

                if count >= self.fail_max:
                    await self._set_state(OPEN)
                    logger.warning(
                        f"Circuit breaker for {self.service_name} opened after {count} failures"
                    )

    async def call_async(self, func: Callable, *args, **kwargs) -> Any:
        """
        Call an async function with circuit breaker protection.

        Args:
            func: Async function or coroutine to call
            *args: Positional arguments for func
            **kwargs: Keyword arguments for func

        Returns:
            Result of func(*args, **kwargs)

        Raises:
            CircuitBreakerOpenError: If circuit is open
        """
        async with self._lock:
            await self._check_state()
            state = await self._get_state()

            if state == OPEN:
                raise CircuitBreakerOpenError(
                    f"Circuit breaker is OPEN for {self.service_name}. Request blocked."
                )

        # Call the function outside the lock
        try:
            # Call the async function/coroutine
            result = await func(*args, **kwargs)
            await self._record_success()
            return result
        except Exception:
            await self._record_failure()
            raise


async def create_shared_async_breaker(
    service_name: str, fail_max: int, reset_timeout: int
) -> AsyncCircuitBreaker:
    """
    Create an async circuit breaker with Redis storage if available.

    Args:
        service_name: Unique name for the service
        fail_max: Number of failures before opening circuit
        reset_timeout: Seconds to wait before trying half-open

    Returns:
        AsyncCircuitBreaker instance
    """
    redis_client = await _get_async_redis_client()

    if redis_client is not None:
        try:
            storage = AsyncRedisStorage(redis_client=redis_client, namespace=service_name)
            logger.info(f"Created Redis-backed async circuit breaker for {service_name}")
            return AsyncCircuitBreaker(
                fail_max=fail_max,
                reset_timeout=reset_timeout,
                storage=storage,
                service_name=service_name,
            )
        except Exception as e:
            logger.warning(
                f"Failed to create Redis-backed async circuit breaker for {service_name}. "
                f"Falling back to in-memory. Error: {e}"
            )

    # Fallback to in-memory
    logger.info(
        f"Creating in-memory async circuit breaker for {service_name}. "
        "State will not be shared across processes."
    )
    return AsyncCircuitBreaker(
        fail_max=fail_max, reset_timeout=reset_timeout, service_name=service_name
    )
