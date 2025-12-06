"""
Comprehensive tests for circuit breaker functionality.
"""

from unittest.mock import MagicMock, patch

import pytest
from pybreaker import CircuitBreaker

from reqflow.sync.behaviors import CircuitBreakerBehavior
from reqflow.sync.circuit_breakers import create_shared_breaker
from reqflow.core.errors import CircuitBreakerOpenError, ConnectionError
from reqflow.core.request_response import RequestContext, ResponseContext


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
        from reqflow.core.errors import ConnectionError as CustomConnectionError

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
        from reqflow.core.errors import ConnectionError as CustomConnectionError

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
        from reqflow.core.errors import ConnectionError as CustomConnectionError

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


class TestCreateSharedBreaker:
    """Test create_shared_breaker function."""

    @patch("reqflow.sync.circuit_breakers.redis_client", None)
    def test_fallback_to_in_memory_when_redis_unavailable(self):
        """Test that function falls back to in-memory breaker when Redis is unavailable."""
        breaker = create_shared_breaker(service_name="test_service", fail_max=3, reset_timeout=5)
        assert breaker is not None
        assert isinstance(breaker, CircuitBreaker)

    @patch("reqflow.sync.circuit_breakers.redis_client")
    @patch("reqflow.sync.circuit_breakers.CircuitRedisStorage")
    def test_creates_redis_breaker_when_available(self, mock_redis_storage, mock_redis_client):
        """Test that function creates Redis-backed breaker when Redis is available."""
        mock_storage = MagicMock()
        mock_redis_storage.return_value = mock_storage

        breaker = create_shared_breaker(service_name="test_service", fail_max=3, reset_timeout=5)

        assert breaker is not None
        assert isinstance(breaker, CircuitBreaker)
        mock_redis_storage.assert_called_once()

    @patch("reqflow.sync.circuit_breakers.redis_client")
    @patch("reqflow.sync.circuit_breakers.CircuitRedisStorage")
    def test_handles_redis_creation_error(self, mock_redis_storage, mock_redis_client):
        """Test that function handles Redis creation errors gracefully."""
        mock_redis_storage.side_effect = Exception("Redis error")

        # Should fall back to in-memory breaker
        breaker = create_shared_breaker(service_name="test_service", fail_max=3, reset_timeout=5)
        assert breaker is not None
        assert isinstance(breaker, CircuitBreaker)
