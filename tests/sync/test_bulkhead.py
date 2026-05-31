"""Tests for the synchronous bulkhead (concurrency isolation) pattern."""

import threading
import time
from unittest.mock import MagicMock

import pytest

from reqlient import (
    Bulkhead,
    BulkheadConfig,
    BulkheadFullError,
    BulkheadRegistry,
    CircuitBreakerConfig,
    RestClient,
    ServerError,
)
from reqlient.core.request_response import RequestContext, ResponseContext
from reqlient.sync.behaviors import BulkheadBehavior

from ..conftest import User


def _request(method="GET", url="https://api.example.com/v1/users/1"):
    return RequestContext(method=method, url=url, headers={}, params=None, data=None)


def _response(request, status_code=200):
    return ResponseContext(
        status_code=status_code,
        headers={},
        data={"id": 1, "name": "John", "email": "john@example.com"},
        request=request,
    )


class TestBulkhead:
    """Test the Bulkhead primitive."""

    def test_acquire_and_release(self):
        bulkhead = Bulkhead("svc", max_concurrent=1)
        assert bulkhead.try_acquire() is True
        # Second acquire fails — only one slot.
        assert bulkhead.try_acquire() is False
        bulkhead.release()
        # Slot is free again.
        assert bulkhead.try_acquire() is True

    def test_respects_max_concurrent(self):
        bulkhead = Bulkhead("svc", max_concurrent=3)
        assert all(bulkhead.try_acquire() for _ in range(3))
        assert bulkhead.try_acquire() is False

    def test_invalid_max_concurrent(self):
        with pytest.raises(ValueError):
            Bulkhead("svc", max_concurrent=0)

    def test_max_wait_blocks_then_rejects(self):
        bulkhead = Bulkhead("svc", max_concurrent=1, max_wait=0.1)
        assert bulkhead.try_acquire() is True  # occupy the only slot
        start = time.monotonic()
        assert bulkhead.try_acquire() is False  # waits ~max_wait, then rejects
        assert time.monotonic() - start >= 0.1

    def test_max_wait_acquires_when_slot_freed(self):
        bulkhead = Bulkhead("svc", max_concurrent=1, max_wait=2.0)
        assert bulkhead.try_acquire() is True  # occupy

        results = []

        def waiter():
            results.append(bulkhead.try_acquire())

        thread = threading.Thread(target=waiter)
        thread.start()
        time.sleep(0.1)  # let the waiter start blocking
        bulkhead.release()  # free the slot; waiter should grab it
        thread.join(timeout=2.0)

        assert results == [True]


class TestBulkheadRegistry:
    """Test the BulkheadRegistry."""

    def test_get_returns_same_instance_per_service(self):
        a = BulkheadRegistry.get("svc", max_concurrent=5)
        b = BulkheadRegistry.get("svc")
        assert a is b
        # First configuration wins.
        assert b.max_concurrent == 5

    def test_distinct_services_get_distinct_bulkheads(self):
        a = BulkheadRegistry.get("a", max_concurrent=2)
        b = BulkheadRegistry.get("b", max_concurrent=4)
        assert a is not b
        assert {"a", "b"}.issubset(BulkheadRegistry.get_registered_services())

    def test_configure_defaults(self):
        BulkheadRegistry.configure(default_max_concurrent=7, default_max_wait=1.5)
        bulkhead = BulkheadRegistry.get("svc")
        assert bulkhead.max_concurrent == 7
        assert bulkhead.max_wait == 1.5

    def test_reset_clears_registry(self):
        BulkheadRegistry.get("svc", max_concurrent=2)
        BulkheadRegistry.reset()
        assert BulkheadRegistry.get_registered_services() == set()


class TestBulkheadBehavior:
    """Test the BulkheadBehavior pipeline stage."""

    def test_passes_through_when_slot_available(self):
        bulkhead = Bulkhead("svc", max_concurrent=2)
        request = _request()
        mock_next = MagicMock()
        mock_next.handle.return_value = _response(request)

        behavior = BulkheadBehavior(bulkhead=bulkhead, next_behavior=mock_next)
        result = behavior.handle(request)

        assert result.status_code == 200
        mock_next.handle.assert_called_once_with(request)
        # Slot released after handling — both slots free again.
        assert bulkhead.try_acquire() and bulkhead.try_acquire()

    def test_rejects_when_full(self):
        bulkhead = Bulkhead("svc", max_concurrent=1)
        assert bulkhead.try_acquire() is True  # occupy the only slot

        mock_next = MagicMock()
        behavior = BulkheadBehavior(bulkhead=bulkhead, next_behavior=mock_next)

        with pytest.raises(BulkheadFullError) as exc_info:
            behavior.handle(_request())

        # The rest of the pipeline must not run when the bulkhead is full.
        mock_next.handle.assert_not_called()
        assert exc_info.value.context is not None

    def test_releases_slot_on_downstream_error(self):
        bulkhead = Bulkhead("svc", max_concurrent=1)
        mock_next = MagicMock()
        mock_next.handle.side_effect = ServerError("boom")

        behavior = BulkheadBehavior(bulkhead=bulkhead, next_behavior=mock_next)
        with pytest.raises(ServerError):
            behavior.handle(_request())

        # Slot must be released even though the downstream raised.
        assert bulkhead.try_acquire() is True


class TestRestClientBulkhead:
    """Integration tests for bulkhead wiring in RestClient."""

    def test_bulkhead_disabled_by_default(self, base_url, mock_logger, requests_mock):
        requests_mock.get(
            "https://api.example.com/v1/users/1",
            json={"id": 1, "name": "John", "email": "john@example.com"},
        )
        client = RestClient(
            base_url=base_url,
            service_name="no_bulkhead",
            logger=mock_logger,
            circuit_breaker=None,
        )
        # No bulkhead registered when no bulkhead config is passed.
        assert "no_bulkhead" not in BulkheadRegistry.get_registered_services()
        assert client.get("/users/1", response_data_schema=User) is not None

    def test_request_rejected_when_bulkhead_full(self, base_url, mock_logger, requests_mock):
        requests_mock.get(
            "https://api.example.com/v1/users/1",
            json={"id": 1, "name": "John", "email": "john@example.com"},
        )
        client = RestClient(
            base_url=base_url,
            service_name="bh_service",
            logger=mock_logger,
            circuit_breaker=None,
            bulkhead=BulkheadConfig(max_concurrent=1),
        )

        # Occupy the single slot on the same shared bulkhead instance.
        bulkhead = BulkheadRegistry.get("bh_service")
        assert bulkhead.try_acquire() is True

        with pytest.raises(BulkheadFullError):
            client.get("/users/1", response_data_schema=User)

        # After releasing, the request succeeds — proving the slot gates the call.
        bulkhead.release()
        assert client.get("/users/1", response_data_schema=User) is not None

    def test_bulkhead_full_does_not_trip_circuit_breaker(self, base_url, mock_logger, requests_mock):
        """Placement check: a full bulkhead is outside the breaker, so it must
        not be counted as a downstream failure (which would open the breaker)."""
        requests_mock.get(
            "https://api.example.com/v1/users/1",
            json={"id": 1, "name": "John", "email": "john@example.com"},
        )
        # fail_max=1: a single counted failure would open the circuit.
        client = RestClient(
            base_url=base_url,
            service_name="bh_cb",
            logger=mock_logger,
            circuit_breaker=CircuitBreakerConfig(fail_max=1, reset_timeout=5),
            bulkhead=BulkheadConfig(max_concurrent=1),
        )

        bulkhead = BulkheadRegistry.get("bh_cb")
        assert bulkhead.try_acquire() is True  # occupy the only slot

        with pytest.raises(BulkheadFullError):
            client.get("/users/1", response_data_schema=User)

        bulkhead.release()

        # If the bulkhead rejection had tripped the breaker, this would raise
        # CircuitBreakerOpenError. It succeeds because the breaker never saw it.
        assert client.get("/users/1", response_data_schema=User) is not None
