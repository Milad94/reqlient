import logging
import os

import redis
from dotenv import load_dotenv
from pybreaker import CircuitBreaker, CircuitRedisStorage

# Load environment variables from .env file
load_dotenv()

logger = logging.getLogger(__name__)

# Redis configuration from environment variables with defaults
REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
REDIS_DB = int(os.getenv("REDIS_DB", "0"))

# A shared Redis connection pool is crucial for performance.
# It allows reusing connections instead of creating a new one for every request.
redis_pool = None
redis_client = None

try:
    redis_pool = redis.ConnectionPool(host=REDIS_HOST, port=REDIS_PORT, db=REDIS_DB)
    redis_client = redis.Redis(connection_pool=redis_pool)
    # Test the connection
    redis_client.ping()
except (redis.ConnectionError, redis.TimeoutError, Exception) as e:
    logger.warning(
        f"Failed to connect to Redis at {REDIS_HOST}:{REDIS_PORT}. "
        f"Circuit breaker state will not be shared across processes. Error: {e}"
    )
    redis_pool = None
    redis_client = None


def create_shared_breaker(service_name: str, fail_max: int, reset_timeout: int) -> CircuitBreaker:
    """
    Creates a CircuitBreaker instance whose state is stored in Redis.
    This ensures that the breaker's state is shared across all application
    processes and threads.

    If Redis is unavailable, falls back to an in-memory circuit breaker
    (state will not be shared across processes).

    Args:
        service_name: A unique name for the service the breaker protects (e.g., "payment_api").
                      This is used to namespace the Redis key.
        fail_max: The number of failures required to open the circuit.
        reset_timeout: The number of seconds to wait before moving to half-open.

    Returns:
        A configured CircuitBreaker instance. If Redis is available, it will be
        multi-process safe. Otherwise, it will use in-memory storage.

    Raises:
        ValueError: If Redis connection fails and fallback is not possible.
    """
    if redis_client is not None:
        try:
            # The namespace ensures that keys for different breakers don't collide in Redis.
            # The initial state is managed by pybreaker automatically based on what's in Redis,
            # so we don't need to specify it here.
            state_storage = CircuitRedisStorage(
                redis_client=redis_client, namespace=f"breaker:{service_name}"
            )
            return CircuitBreaker(
                fail_max=fail_max, reset_timeout=reset_timeout, state_storage=state_storage
            )
        except Exception as e:
            logger.warning(
                f"Failed to create Redis-backed circuit breaker for {service_name}. "
                f"Falling back to in-memory breaker. Error: {e}"
            )
            # Fall through to in-memory breaker

    # Fallback to in-memory circuit breaker if Redis is not available
    logger.info(
        f"Creating in-memory circuit breaker for {service_name}. "
        "State will not be shared across processes."
    )
    return CircuitBreaker(fail_max=fail_max, reset_timeout=reset_timeout)


# --- Define your singleton, shared breakers for each external service here ---

# Example: Breaker for a payment service. Opens after 5 failures, half-opens after 60 seconds.
# payment_api_breaker = create_shared_breaker(service_name="payment_api", fail_max=5, reset_timeout=60)

# Example: Breaker for a notification service. More sensitive, opens after 3 failures.
# notification_api_breaker = create_shared_breaker(service_name="notification_api", fail_max=3, reset_timeout=45)

# Add other breakers for other services as needed.
# another_service_breaker = create_shared_breaker(service_name="another_service", fail_max=10, reset_timeout=120)
