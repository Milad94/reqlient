import asyncio
import logging
import time
from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from ..core.errors import CircuitBreakerOpenError, RetryableError

if TYPE_CHECKING:
    import redis.asyncio as aioredis

logger = logging.getLogger(__name__)


def _import_aioredis() -> Any:
    """Import the optional ``redis.asyncio`` dependency, with a helpful error if missing.

    ``redis`` is an optional extra (``reqlient[redis]``); it is only needed when a
    Redis URL is supplied for shared circuit-breaker state. Importing it lazily
    keeps the base install working without redis installed.
    """
    try:
        import redis.asyncio as aioredis
    except ImportError as e:  # pragma: no cover - exercised only without the extra
        raise ImportError(
            "Redis-backed circuit breakers require the optional 'redis' dependency. "
            "Install it with: pip install reqlient[redis]"
        ) from e
    return aioredis

# Circuit breaker states
CLOSED = "closed"
OPEN = "open"
HALF_OPEN = "half_open"


class AsyncCircuitBreakerStorage(ABC):
    """Abstract base class for async circuit breaker storage."""

    @abstractmethod
    async def get_state(self, key: str) -> str | None:
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
    async def get_last_failure_time(self, key: str) -> float | None:
        """Get the last failure timestamp."""


class AsyncInMemoryStorage(AsyncCircuitBreakerStorage):
    """In-memory storage for async circuit breaker (single process only)."""

    def __init__(self, namespace: str):
        self.namespace = namespace
        self._lock = asyncio.Lock()
        self._state: dict[str, Any] = {}

    def _get_key(self, suffix: str) -> str:
        """Generate a namespaced key."""
        return f"{self.namespace}:{suffix}"

    async def get_state(self, key: str) -> str | None:
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

    async def get_last_failure_time(self, key: str) -> float | None:
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

    async def get_state(self, key: str) -> str | None:
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

    async def get_last_failure_time(self, key: str) -> float | None:
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
        storage: AsyncCircuitBreakerStorage | None = None,
        service_name: str = "default",
        exclude: tuple[type[Exception], ...] | None = None,
    ):
        """
        Initialize async circuit breaker.

        Args:
            fail_max: Number of failures before opening circuit
            reset_timeout: Seconds to wait before trying half-open
            storage: Storage backend (if None, uses in-memory)
            service_name: Name of the service (for namespacing)
            exclude: Exception types that should NOT count as failures.
                     If None, only RetryableError subclasses trip the breaker.
        """
        self.fail_max = fail_max
        self.reset_timeout = reset_timeout
        self.service_name = service_name
        self.storage = storage or AsyncInMemoryStorage(namespace=service_name)
        self.exclude = exclude
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

    async def _get_last_failure_time(self) -> float | None:
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

    def _should_count_as_failure(self, exc: Exception) -> bool:
        """Determine if an exception should count as a circuit breaker failure.

        If exclude is set, any exception matching those types is NOT a failure.
        Otherwise, only RetryableError subclasses (ConnectionError, TimeoutError,
        ServerError, RateLimitError) count as failures — client errors like
        StatusCodeError, AuthenticationError, etc. are ignored.
        """
        if self.exclude is not None:
            return not isinstance(exc, self.exclude)
        return isinstance(exc, RetryableError)

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
        except Exception as e:
            if self._should_count_as_failure(e):
                await self._record_failure()
            raise


class AsyncCircuitBreakerRegistry:
    """
    A registry for managing async circuit breakers across the application.

    Configure once at application startup, then breakers are automatically
    created and shared when AsyncRestClient instances use the same service_name.

    Example:
        # Configure once at startup (e.g., FastAPI lifespan)
        await AsyncCircuitBreakerRegistry.configure(
            redis_url="redis://localhost:6379/0",
            default_fail_max=5,
            default_reset_timeout=60
        )

        # AsyncRestClient automatically gets breaker from registry
        async with AsyncRestClient(
            base_url="https://api.example.com",
            service_name="user_api",  # Breaker auto-resolved
        ) as client:
            ...

        # Or get breaker explicitly with custom settings
        breaker = await AsyncCircuitBreakerRegistry.get("payments", fail_max=3)
    """

    _redis_url: str | None = None
    _default_fail_max: int = 5
    _default_reset_timeout: int = 60
    _breakers: dict[str, AsyncCircuitBreaker] = {}
    _redis_client: "aioredis.Redis | None" = None
    _configured: bool = False

    @classmethod
    async def configure(
        cls,
        redis_url: str | None = None,
        default_fail_max: int = 5,
        default_reset_timeout: int = 60,
    ) -> None:
        """
        Configure the async circuit breaker registry.

        Call this once at application startup before creating any AsyncRestClient instances.

        Args:
            redis_url: Redis URL for shared state (e.g., "redis://localhost:6379/0").
                      If None, breakers will use in-memory storage (not shared across processes).
            default_fail_max: Default number of failures before opening circuit.
            default_reset_timeout: Default seconds to wait before trying half-open.
        """
        cls._redis_url = redis_url
        cls._default_fail_max = default_fail_max
        cls._default_reset_timeout = default_reset_timeout
        cls._configured = True

        # Initialize Redis client if URL provided
        if redis_url:
            try:
                aioredis = _import_aioredis()
                cls._redis_client = await aioredis.from_url(redis_url)
                await cls._redis_client.ping()  # type: ignore[misc]  # redis-py sync/async union
                logger.info(f"AsyncCircuitBreakerRegistry connected to Redis at {redis_url}")
            except Exception as e:
                logger.warning(
                    f"Failed to connect to Redis at {redis_url}. "
                    f"Circuit breakers will use in-memory storage. Error: {e}"
                )
                cls._redis_client = None
        else:
            cls._redis_client = None
            logger.info(
                "AsyncCircuitBreakerRegistry configured without Redis. "
                "Circuit breakers will use in-memory storage."
            )

    @classmethod
    async def get(
        cls,
        service_name: str,
        fail_max: int | None = None,
        reset_timeout: int | None = None,
    ) -> AsyncCircuitBreaker:
        """
        Get or create an async circuit breaker for a service.

        If a breaker already exists for the service_name, returns the existing instance.
        Otherwise, creates a new one with the specified or default settings.

        Args:
            service_name: Unique name for the service (e.g., "user_api", "payments").
            fail_max: Number of failures before opening circuit (uses default if None).
            reset_timeout: Seconds to wait before trying half-open (uses default if None).

        Returns:
            AsyncCircuitBreaker instance for the service.
        """
        if service_name in cls._breakers:
            return cls._breakers[service_name]

        breaker = cls._create_breaker(
            service_name=service_name,
            fail_max=fail_max or cls._default_fail_max,
            reset_timeout=reset_timeout or cls._default_reset_timeout,
        )
        cls._breakers[service_name] = breaker
        return breaker

    @classmethod
    def _create_breaker(
        cls,
        service_name: str,
        fail_max: int,
        reset_timeout: int,
    ) -> AsyncCircuitBreaker:
        """Create a new async circuit breaker instance."""
        if cls._redis_client is not None:
            try:
                storage = AsyncRedisStorage(redis_client=cls._redis_client, namespace=service_name)
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

        # Fallback to in-memory circuit breaker
        logger.info(
            f"Created in-memory async circuit breaker for {service_name}. "
            "State will not be shared across processes."
        )
        return AsyncCircuitBreaker(
            fail_max=fail_max,
            reset_timeout=reset_timeout,
            service_name=service_name,
        )

    @classmethod
    def reset(cls) -> None:
        """
        Reset the registry. Clears all breakers and configuration.

        Useful for testing to ensure clean state between tests.
        """
        cls._breakers.clear()
        cls._redis_client = None
        cls._redis_url = None
        cls._default_fail_max = 5
        cls._default_reset_timeout = 60
        cls._configured = False

    @classmethod
    def is_configured(cls) -> bool:
        """Check if the registry has been configured."""
        return cls._configured

    @classmethod
    def get_registered_services(cls) -> set[str]:
        """Get the set of service names that have breakers registered."""
        return set(cls._breakers.keys())
