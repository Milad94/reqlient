"""
Comprehensive tests for circuit breaker functionality.
"""

from unittest.mock import MagicMock, patch

import pytest
from pybreaker import CircuitBreaker

from reqlient.core.errors import CircuitBreakerOpenError, ConnectionError
from reqlient.core.request_response import RequestContext, ResponseContext
from reqlient.sync.behaviors import CircuitBreakerBehavior
from reqlient.sync.circuit_breakers import CircuitBreakerRegistry


class TestCircuitBreakerBehavior:
    """Test CircuitBreakerBehavior."""

    def test_passes_through_when_closed(self):
        """Test that requests pass through when circuit is closed."""
        breaker = CircuitBreaker(fail_max=3, reset_timeout=5)
        mock_next = MagicMock()
        mock_response = ResponseContext(
            status_code=200,
            headers={},
            data={},
            request=RequestContext(
                method="GET",
                url="https://api.example.com/v1/users/1",
                headers={},
                params=None,
                data=None,
            ),
        )
        mock_next.handle.return_value = mock_response

        behavior = CircuitBreakerBehavior(breaker=breaker, next_behavior=mock_next)
        request = RequestContext(
            method="GET",
            url="https://api.example.com/v1/users/1",
            headers={},
            params=None,
            data=None,
        )

        result = behavior.handle(request)
        assert result == mock_response

    def test_opens_on_failures(self):
        """Test that circuit opens after too many failures."""
        breaker = CircuitBreaker(fail_max=2, reset_timeout=5)
        mock_next = MagicMock()
        from reqlient.core.errors import ConnectionError as CustomConnectionError

        error = CustomConnectionError("Connection failed", context=None)
        mock_next.handle.side_effect = error

        behavior = CircuitBreakerBehavior(breaker=breaker, next_behavior=mock_next)
        request = RequestContext(
            method="GET",
            url="https://api.example.com/v1/users/1",
            headers={},
            params=None,
            data=None,
        )

        # First failure - circuit still closed
        with pytest.raises(ConnectionError):
            behavior.handle(request)

        # Second failure - circuit opens (fail_max=2 means opens after 2 failures)
        # The circuit opens during this call, so pybreaker raises CircuitBreakerError
        # which gets converted to CircuitBreakerOpenError
        with pytest.raises(CircuitBreakerOpenError) as exc_info:
            behavior.handle(request)

        assert exc_info.value.context is not None

        # Third call - circuit is already open, should fail fast
        with pytest.raises(CircuitBreakerOpenError) as exc_info2:
            behavior.handle(request)

        assert exc_info2.value.context is not None
        # Should not have called the next behavior (fail fast)
        assert mock_next.handle.call_count == 2  # Only called on first 2 attempts

    def test_raises_circuit_breaker_error_when_open(self):
        """Test that CircuitBreakerOpenError is raised when circuit is open."""
        breaker = CircuitBreaker(fail_max=1, reset_timeout=5)
        mock_next = MagicMock()
        from reqlient.core.errors import ConnectionError as CustomConnectionError

        error = CustomConnectionError("Connection failed", context=None)
        mock_next.handle.side_effect = error

        behavior = CircuitBreakerBehavior(breaker=breaker, next_behavior=mock_next)
        request = RequestContext(
            method="GET",
            url="https://api.example.com/v1/users/1",
            headers={},
            params=None,
            data=None,
        )

        # With fail_max=1, the circuit opens when the first failure occurs
        # pybreaker may raise CircuitBreakerError immediately when threshold is reached
        # which gets converted to CircuitBreakerOpenError
        with pytest.raises(CircuitBreakerOpenError) as exc_info:
            behavior.handle(request)

        assert exc_info.value.context is not None
        # The next behavior was called once before circuit opened
        assert mock_next.handle.call_count == 1

        # Next request should also fail fast with CircuitBreakerOpenError
        with pytest.raises(CircuitBreakerOpenError) as exc_info2:
            behavior.handle(request)

        assert exc_info2.value.context is not None
        # Should not have called the next behavior again (fail fast)
        assert mock_next.handle.call_count == 1

    def test_re_raises_retryable_errors(self):
        """Test that retryable errors are re-raised."""
        breaker = CircuitBreaker(fail_max=3, reset_timeout=5)
        mock_next = MagicMock()
        from reqlient.core.errors import ConnectionError as CustomConnectionError

        error = CustomConnectionError("Connection failed", context=None)
        mock_next.handle.side_effect = error

        behavior = CircuitBreakerBehavior(breaker=breaker, next_behavior=mock_next)
        request = RequestContext(
            method="GET",
            url="https://api.example.com/v1/users/1",
            headers={},
            params=None,
            data=None,
        )

        with pytest.raises(ConnectionError):
            behavior.handle(request)


class TestCircuitBreakerRegistry:
    """Test CircuitBreakerRegistry class."""

    def setup_method(self):
        """Reset registry before each test."""
        CircuitBreakerRegistry.reset()

    def teardown_method(self):
        """Reset registry after each test."""
        CircuitBreakerRegistry.reset()

    def test_get_creates_new_breaker(self):
        """Test that get() creates a new breaker for unknown service."""
        breaker = CircuitBreakerRegistry.get("new_service", fail_max=3, reset_timeout=5)
        assert breaker is not None
        assert isinstance(breaker, CircuitBreaker)

    def test_get_returns_same_breaker_for_same_service(self):
        """Test that get() returns the same breaker for the same service name."""
        breaker1 = CircuitBreakerRegistry.get("same_service", fail_max=3, reset_timeout=5)
        breaker2 = CircuitBreakerRegistry.get("same_service", fail_max=10, reset_timeout=60)
        # Should be the exact same instance
        assert breaker1 is breaker2

    def test_configure_sets_defaults(self):
        """Test that configure() sets default values."""
        CircuitBreakerRegistry.configure(default_fail_max=10, default_reset_timeout=120)
        assert CircuitBreakerRegistry.is_configured()

    def test_reset_clears_breakers(self):
        """Test that reset() clears all breakers."""
        CircuitBreakerRegistry.get("service1")
        CircuitBreakerRegistry.get("service2")
        assert len(CircuitBreakerRegistry.get_registered_services()) == 2

        CircuitBreakerRegistry.reset()
        assert len(CircuitBreakerRegistry.get_registered_services()) == 0
        assert not CircuitBreakerRegistry.is_configured()

    def test_get_registered_services(self):
        """Test that get_registered_services() returns correct services."""
        CircuitBreakerRegistry.get("service_a")
        CircuitBreakerRegistry.get("service_b")

        services = CircuitBreakerRegistry.get_registered_services()
        assert services == {"service_a", "service_b"}

    @patch.object(CircuitBreakerRegistry, "_redis_client", None)
    def test_fallback_to_in_memory_when_redis_unavailable(self):
        """Test that registry falls back to in-memory breaker when Redis is unavailable."""
        CircuitBreakerRegistry.reset()
        breaker = CircuitBreakerRegistry.get("test_service", fail_max=3, reset_timeout=5)
        assert breaker is not None
        assert isinstance(breaker, CircuitBreaker)


class TestRedisBackedCircuitBreaker:
    """Verify that a configured Redis-backed sync breaker actually uses Redis."""

    def setup_method(self):
        CircuitBreakerRegistry.reset()

    def teardown_method(self):
        CircuitBreakerRegistry.reset()

    def test_redis_configured_breaker_uses_redis_storage(self):
        """Regression: a wrong CircuitRedisStorage constructor call used to make
        every sync breaker silently fall back to in-memory even when Redis was
        configured. A configured breaker must now use CircuitRedisStorage."""
        from pybreaker import CircuitRedisStorage

        import reqlient.sync.circuit_breakers as cb_mod

        fake_redis = MagicMock()
        fake_redis.ping.return_value = True
        fake_redis.get.return_value = None  # no existing state stored

        # ``redis`` is now imported lazily (it is an optional extra), so patch the
        # lazy importer to hand back a stub module instead of the real package.
        fake_redis_module = MagicMock()
        fake_redis_module.from_url.return_value = fake_redis

        with patch.object(cb_mod, "_import_redis", return_value=fake_redis_module):
            CircuitBreakerRegistry.configure(redis_url="redis://localhost:6379/0")
            breaker = CircuitBreakerRegistry.get("redis_service", fail_max=3, reset_timeout=5)

        assert isinstance(breaker, CircuitBreaker)
        # The breaker's storage must be Redis-backed and wired to our client.
        assert isinstance(breaker._state_storage, CircuitRedisStorage)
        assert breaker._state_storage._redis is fake_redis
