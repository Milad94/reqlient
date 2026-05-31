"""Tests for the asynchronous bulkhead (concurrency isolation) pattern."""

import asyncio
from unittest.mock import AsyncMock

import httpx
import pytest
from pydantic import BaseModel

from reqlient import (
    AsyncBulkhead,
    AsyncBulkheadRegistry,
    AsyncRestClient,
    BulkheadConfig,
    BulkheadFullError,
    CircuitBreakerConfig,
)
from reqlient.async_.behaviors import AsyncBulkheadBehavior
from reqlient.core.errors import ServerError
from reqlient.core.request_response import RequestContext, ResponseContext


class User(BaseModel):
    id: int
    name: str
    email: str


def _request(method="GET", url="https://api.example.com/v1/users/1"):
    return RequestContext(method=method, url=url, headers={}, params=None, data=None)


def _response(request, status_code=200):
    return ResponseContext(
        status_code=status_code,
        headers={},
        data={"id": 1, "name": "John", "email": "john@example.com"},
        request=request,
    )


def _mock_client():
    """An httpx.AsyncClient whose .request returns a canned 200 response."""
    client = httpx.AsyncClient()
    client.request = AsyncMock(
        return_value=httpx.Response(
            200,
            json={"id": 1, "name": "John", "email": "john@example.com"},
            request=httpx.Request("GET", "https://api.example.com/v1/users/1"),
        )
    )
    return client


@pytest.mark.asyncio
class TestAsyncBulkhead:
    """Test the AsyncBulkhead primitive."""

    async def test_acquire_and_release(self):
        bulkhead = AsyncBulkhead("svc", max_concurrent=1)
        assert await bulkhead.try_acquire() is True
        assert await bulkhead.try_acquire() is False
        bulkhead.release()
        assert await bulkhead.try_acquire() is True

    async def test_respects_max_concurrent(self):
        bulkhead = AsyncBulkhead("svc", max_concurrent=3)
        assert all([await bulkhead.try_acquire() for _ in range(3)])
        assert await bulkhead.try_acquire() is False

    async def test_invalid_max_concurrent(self):
        with pytest.raises(ValueError):
            AsyncBulkhead("svc", max_concurrent=0)

    async def test_max_wait_blocks_then_rejects(self):
        bulkhead = AsyncBulkhead("svc", max_concurrent=1, max_wait=0.1)
        assert await bulkhead.try_acquire() is True  # occupy
        start = asyncio.get_event_loop().time()
        assert await bulkhead.try_acquire() is False  # waits then rejects
        assert asyncio.get_event_loop().time() - start >= 0.1

    async def test_max_wait_acquires_when_slot_freed(self):
        bulkhead = AsyncBulkhead("svc", max_concurrent=1, max_wait=2.0)
        assert await bulkhead.try_acquire() is True  # occupy

        async def waiter():
            return await bulkhead.try_acquire()

        task = asyncio.ensure_future(waiter())
        await asyncio.sleep(0.1)  # let the waiter start blocking
        bulkhead.release()  # free the slot
        assert await task is True


@pytest.mark.asyncio
class TestAsyncBulkheadRegistry:
    """Test the AsyncBulkheadRegistry."""

    async def test_get_returns_same_instance_per_service(self):
        a = AsyncBulkheadRegistry.get("svc", max_concurrent=5)
        b = AsyncBulkheadRegistry.get("svc")
        assert a is b
        assert b.max_concurrent == 5

    async def test_configure_defaults(self):
        AsyncBulkheadRegistry.configure(default_max_concurrent=7, default_max_wait=1.5)
        bulkhead = AsyncBulkheadRegistry.get("svc")
        assert bulkhead.max_concurrent == 7
        assert bulkhead.max_wait == 1.5

    async def test_reset_clears_registry(self):
        AsyncBulkheadRegistry.get("svc", max_concurrent=2)
        AsyncBulkheadRegistry.reset()
        assert AsyncBulkheadRegistry.get_registered_services() == set()


@pytest.mark.asyncio
class TestAsyncBulkheadBehavior:
    """Test the AsyncBulkheadBehavior pipeline stage."""

    async def test_passes_through_when_slot_available(self):
        bulkhead = AsyncBulkhead("svc", max_concurrent=2)
        request = _request()
        mock_next = AsyncMock()
        mock_next.handle = AsyncMock(return_value=_response(request))

        behavior = AsyncBulkheadBehavior(bulkhead=bulkhead, next_behavior=mock_next)
        result = await behavior.handle(request)

        assert result.status_code == 200
        mock_next.handle.assert_awaited_once_with(request)
        # Both slots free again after handling.
        assert await bulkhead.try_acquire() and await bulkhead.try_acquire()

    async def test_rejects_when_full(self):
        bulkhead = AsyncBulkhead("svc", max_concurrent=1)
        assert await bulkhead.try_acquire() is True  # occupy

        mock_next = AsyncMock()
        mock_next.handle = AsyncMock()
        behavior = AsyncBulkheadBehavior(bulkhead=bulkhead, next_behavior=mock_next)

        with pytest.raises(BulkheadFullError) as exc_info:
            await behavior.handle(_request())

        mock_next.handle.assert_not_awaited()
        assert exc_info.value.context is not None

    async def test_releases_slot_on_downstream_error(self):
        bulkhead = AsyncBulkhead("svc", max_concurrent=1)
        mock_next = AsyncMock()
        mock_next.handle = AsyncMock(side_effect=ServerError("boom"))

        behavior = AsyncBulkheadBehavior(bulkhead=bulkhead, next_behavior=mock_next)
        with pytest.raises(ServerError):
            await behavior.handle(_request())

        assert await bulkhead.try_acquire() is True  # released


@pytest.mark.asyncio
class TestAsyncRestClientBulkhead:
    """Integration tests for bulkhead wiring in AsyncRestClient."""

    async def test_request_rejected_when_bulkhead_full(self):
        async with _mock_client() as client:
            async_client = AsyncRestClient(
                base_url="https://api.example.com/v1",
                service_name="bh_async",
                client=client,
                circuit_breaker=None,
                bulkhead=BulkheadConfig(max_concurrent=1),
            )
            await async_client._ensure_pipelines_built()

            bulkhead = AsyncBulkheadRegistry.get("bh_async")
            assert await bulkhead.try_acquire() is True  # occupy the slot

            with pytest.raises(BulkheadFullError):
                await async_client.get("/users/1", response_data_schema=User)

            bulkhead.release()
            assert await async_client.get("/users/1", response_data_schema=User) is not None

    async def test_bulkhead_full_does_not_trip_circuit_breaker(self):
        async with _mock_client() as client:
            async_client = AsyncRestClient(
                base_url="https://api.example.com/v1",
                service_name="bh_async_cb",
                client=client,
                circuit_breaker=CircuitBreakerConfig(fail_max=1, reset_timeout=5),
                bulkhead=BulkheadConfig(max_concurrent=1),
            )
            await async_client._ensure_pipelines_built()

            bulkhead = AsyncBulkheadRegistry.get("bh_async_cb")
            assert await bulkhead.try_acquire() is True  # occupy the slot

            with pytest.raises(BulkheadFullError):
                await async_client.get("/users/1", response_data_schema=User)

            bulkhead.release()
            # Breaker never saw the bulkhead rejection, so it stays closed.
            assert await async_client.get("/users/1", response_data_schema=User) is not None
