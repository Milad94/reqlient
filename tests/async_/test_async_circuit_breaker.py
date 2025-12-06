"""
Comprehensive tests for async circuit breaker functionality.
"""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from reqlient.async_.circuit_breakers import (
    CLOSED,
    AsyncCircuitBreaker,
    AsyncCircuitBreakerRegistry,
    AsyncInMemoryStorage,
)
from reqlient.core.errors import CircuitBreakerOpenError


@pytest.mark.asyncio
class TestAsyncCircuitBreaker:
    """Test AsyncCircuitBreaker."""

    async def test_passes_through_when_closed(self):
        """Test that requests pass through when circuit is closed."""
        breaker = AsyncCircuitBreaker(fail_max=3, reset_timeout=5)

        async def success_func():
            return "success"

        result = await breaker.call_async(success_func)
        assert result == "success"

    async def test_opens_on_failures(self):
        """Test that circuit opens after too many failures."""
        breaker = AsyncCircuitBreaker(fail_max=2, reset_timeout=5)

        async def failing_func():
            raise Exception("Test error")

        # First failure
        with pytest.raises(Exception):
            await breaker.call_async(failing_func)

        # Second failure - circuit should still be closed
        with pytest.raises(Exception):
            await breaker.call_async(failing_func)

        # Third failure - circuit should open
        with pytest.raises(CircuitBreakerOpenError):
            await breaker.call_async(failing_func)

    async def test_raises_circuit_breaker_error_when_open(self):
        """Test that CircuitBreakerOpenError is raised when circuit is open."""
        breaker = AsyncCircuitBreaker(fail_max=1, reset_timeout=5)

        async def failing_func():
            raise Exception("Test error")

        # Fail once to open circuit
        with pytest.raises(Exception):
            await breaker.call_async(failing_func)

        # Next request should fail fast with CircuitBreakerOpenError
        with pytest.raises(CircuitBreakerOpenError):
            await breaker.call_async(failing_func)

    async def test_half_open_to_closed_on_success(self):
        """Test that circuit transitions from half-open to closed on success."""
        breaker = AsyncCircuitBreaker(fail_max=1, reset_timeout=0.1)

        async def failing_func():
            raise Exception("Test error")

        async def success_func():
            return "success"

        # Fail to open circuit
        with pytest.raises(Exception):
            await breaker.call_async(failing_func)

        # Wait for timeout
        await asyncio.sleep(0.2)

        # Success should close the circuit
        result = await breaker.call_async(success_func)
        assert result == "success"

        # Circuit should be closed now
        result = await breaker.call_async(success_func)
        assert result == "success"

    async def test_half_open_to_open_on_failure(self):
        """Test that circuit transitions from half-open back to open on failure."""
        breaker = AsyncCircuitBreaker(fail_max=1, reset_timeout=0.1)

        async def failing_func():
            raise Exception("Test error")

        # Fail to open circuit
        with pytest.raises(Exception):
            await breaker.call_async(failing_func)

        # Wait for timeout
        await asyncio.sleep(0.2)

        # Failure in half-open should open circuit again
        with pytest.raises(Exception):
            await breaker.call_async(failing_func)

        # Next request should fail fast
        with pytest.raises(CircuitBreakerOpenError):
            await breaker.call_async(failing_func)


@pytest.mark.asyncio
class TestAsyncInMemoryStorage:
    """Test AsyncInMemoryStorage."""

    async def test_get_set_state(self):
        """Test getting and setting state."""
        storage = AsyncInMemoryStorage(namespace="test")
        await storage.set_state("service1", CLOSED)
        state = await storage.get_state("service1")
        assert state == CLOSED

    async def test_increment_failure_count(self):
        """Test incrementing failure count."""
        storage = AsyncInMemoryStorage(namespace="test")
        count = await storage.increment_failure_count("service1")
        assert count == 1
        count = await storage.increment_failure_count("service1")
        assert count == 2

    async def test_reset_failure_count(self):
        """Test resetting failure count."""
        storage = AsyncInMemoryStorage(namespace="test")
        await storage.increment_failure_count("service1")
        await storage.reset_failure_count("service1")
        count = await storage.get_failure_count("service1")
        assert count == 0

    async def test_last_failure_time(self):
        """Test setting and getting last failure time."""
        import time

        storage = AsyncInMemoryStorage(namespace="test")
        timestamp = time.time()
        await storage.set_last_failure_time("service1", timestamp)
        retrieved = await storage.get_last_failure_time("service1")
        assert retrieved == timestamp


@pytest.mark.asyncio
class TestAsyncCircuitBreakerRegistry:
    """Test AsyncCircuitBreakerRegistry class."""

    def setup_method(self):
        """Reset registry before each test."""
        AsyncCircuitBreakerRegistry.reset()

    def teardown_method(self):
        """Reset registry after each test."""
        AsyncCircuitBreakerRegistry.reset()

    async def test_get_creates_new_breaker(self):
        """Test that get() creates a new breaker for unknown service."""
        breaker = await AsyncCircuitBreakerRegistry.get("new_service", fail_max=3, reset_timeout=5)
        assert breaker is not None
        assert isinstance(breaker, AsyncCircuitBreaker)

    async def test_get_returns_same_breaker_for_same_service(self):
        """Test that get() returns the same breaker for the same service name."""
        breaker1 = await AsyncCircuitBreakerRegistry.get("same_service", fail_max=3, reset_timeout=5)
        breaker2 = await AsyncCircuitBreakerRegistry.get("same_service", fail_max=10, reset_timeout=60)
        # Should be the exact same instance
        assert breaker1 is breaker2

    async def test_configure_sets_defaults(self):
        """Test that configure() sets default values."""
        await AsyncCircuitBreakerRegistry.configure(
            default_fail_max=10,
            default_reset_timeout=120
        )
        assert AsyncCircuitBreakerRegistry.is_configured()

    async def test_reset_clears_breakers(self):
        """Test that reset() clears all breakers."""
        # First add some breakers
        await AsyncCircuitBreakerRegistry.get("service1")
        await AsyncCircuitBreakerRegistry.get("service2")
        assert len(AsyncCircuitBreakerRegistry.get_registered_services()) == 2

        # Reset clears them
        AsyncCircuitBreakerRegistry.reset()
        assert len(AsyncCircuitBreakerRegistry.get_registered_services()) == 0
        assert not AsyncCircuitBreakerRegistry.is_configured()

    async def test_get_registered_services(self):
        """Test that get_registered_services() returns correct services."""
        await AsyncCircuitBreakerRegistry.get("service_a")
        await AsyncCircuitBreakerRegistry.get("service_b")

        services = AsyncCircuitBreakerRegistry.get_registered_services()
        assert services == {"service_a", "service_b"}

    async def test_fallback_to_in_memory_when_redis_unavailable(self):
        """Test that registry falls back to in-memory breaker when Redis is unavailable."""
        AsyncCircuitBreakerRegistry.reset()
        breaker = await AsyncCircuitBreakerRegistry.get("test_service", fail_max=3, reset_timeout=5)
        assert breaker is not None
        assert isinstance(breaker, AsyncCircuitBreaker)
