import logging
import threading
from typing import Optional, Set

import redis
from pybreaker import STATE_CLOSED, CircuitBreaker, CircuitRedisStorage

logger = logging.getLogger(__name__)


class CircuitBreakerRegistry:
    """
    A registry for managing circuit breakers across the application.

    Configure once at application startup, then breakers are automatically
    created and shared when RestClient instances use the same service_name.

    Example:
        # Configure once at startup (e.g., Django settings.py, FastAPI lifespan)
        CircuitBreakerRegistry.configure(
            redis_url="redis://localhost:6379/0",
            default_fail_max=5,
            default_reset_timeout=60
        )

        # RestClient automatically gets breaker from registry
        client = RestClient(
            base_url="https://api.example.com",
            service_name="user_api",  # Breaker auto-resolved
        )

        # Or get breaker explicitly with custom settings
        breaker = CircuitBreakerRegistry.get("payments", fail_max=3)
    """

    _redis_url: Optional[str] = None
    _default_fail_max: int = 5
    _default_reset_timeout: int = 60
    _breakers: dict[str, CircuitBreaker] = {}
    _redis_client: Optional[redis.Redis] = None
    _configured: bool = False
    _lock: threading.Lock = threading.Lock()

    @classmethod
    def configure(
        cls,
        redis_url: Optional[str] = None,
        default_fail_max: int = 5,
        default_reset_timeout: int = 60,
    ) -> None:
        """
        Configure the circuit breaker registry.

        Call this once at application startup before creating any RestClient instances.

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
                cls._redis_client = redis.from_url(redis_url)
                cls._redis_client.ping()
                logger.info(f"CircuitBreakerRegistry connected to Redis at {redis_url}")
            except Exception as e:
                logger.warning(
                    f"Failed to connect to Redis at {redis_url}. "
                    f"Circuit breakers will use in-memory storage. Error: {e}"
                )
                cls._redis_client = None
        else:
            cls._redis_client = None
            logger.info(
                "CircuitBreakerRegistry configured without Redis. "
                "Circuit breakers will use in-memory storage."
            )

    @classmethod
    def get(
        cls,
        service_name: str,
        fail_max: Optional[int] = None,
        reset_timeout: Optional[int] = None,
    ) -> CircuitBreaker:
        """
        Get or create a circuit breaker for a service.

        If a breaker already exists for the service_name, returns the existing instance.
        Otherwise, creates a new one with the specified or default settings.

        Args:
            service_name: Unique name for the service (e.g., "user_api", "payments").
            fail_max: Number of failures before opening circuit (uses default if None).
            reset_timeout: Seconds to wait before trying half-open (uses default if None).

        Returns:
            CircuitBreaker instance for the service.
        """
        with cls._lock:
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
    ) -> CircuitBreaker:
        """Create a new circuit breaker instance."""
        if cls._redis_client is not None:
            try:
                # pybreaker's CircuitRedisStorage signature is
                # (state, redis_object, namespace=..., fallback_circuit_state=...).
                # fallback_circuit_state=STATE_CLOSED makes the breaker fail open
                # (treat the circuit as closed) if Redis is unreachable at runtime.
                state_storage = CircuitRedisStorage(
                    STATE_CLOSED,
                    cls._redis_client,
                    namespace=f"breaker:{service_name}",
                    fallback_circuit_state=STATE_CLOSED,
                )
                logger.info(f"Created Redis-backed circuit breaker for {service_name}")
                return CircuitBreaker(
                    fail_max=fail_max,
                    reset_timeout=reset_timeout,
                    state_storage=state_storage
                )
            except Exception as e:
                logger.warning(
                    f"Failed to create Redis-backed circuit breaker for {service_name}. "
                    f"Falling back to in-memory. Error: {e}"
                )

        # Fallback to in-memory circuit breaker
        logger.info(
            f"Created in-memory circuit breaker for {service_name}. "
            "State will not be shared across processes."
        )
        return CircuitBreaker(fail_max=fail_max, reset_timeout=reset_timeout)

    @classmethod
    def reset(cls) -> None:
        """
        Reset the registry. Clears all breakers and configuration.

        Useful for testing to ensure clean state between tests.
        """
        with cls._lock:
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
    def get_registered_services(cls) -> Set[str]:
        """Get the set of service names that have breakers registered."""
        return set(cls._breakers.keys())
