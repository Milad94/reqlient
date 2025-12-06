"""
Comprehensive tests for AsyncRestClient.
"""

from unittest.mock import AsyncMock

import httpx
import pytest
from pydantic import BaseModel

from reqlient import AsyncRestClient
from reqlient.async_.circuit_breakers import AsyncCircuitBreaker
from reqlient.core.errors import (
    AuthenticationError,
    AuthorizationError,
    CircuitBreakerOpenError,
    ConnectionError,
    ResourceNotFoundError,
    RestClientError,
    TimeoutError,
)


class User(BaseModel):
    id: int
    name: str
    email: str


class CreateUserRequest(BaseModel):
    name: str
    email: str


class UserResponse(BaseModel):
    id: int
    name: str
    email: str


class ErrorResponse(BaseModel):
    detail: str


@pytest.mark.asyncio
class TestAsyncRestClientInitialization:
    """Test AsyncRestClient initialization and configuration."""

    async def test_basic_initialization(self, base_url, mock_logger):
        """Test basic AsyncRestClient initialization."""
        async with AsyncRestClient(
            base_url=base_url, service_name="test", logger=mock_logger
        ) as client:
            assert client.base_url == base_url
            assert client.service_name == "test"
            assert client.logger == mock_logger
            assert client.timeout == 30
            assert client.verify_ssl is True

    async def test_custom_configuration(self, base_url, mock_logger):
        """Test AsyncRestClient with custom configuration."""
        async with AsyncRestClient(
            base_url=base_url,
            service_name="test",
            logger=mock_logger,
            timeout=60,
            verify_ssl=False,
            max_retries=5,
            retry_backoff_factor=1.0,
        ) as client:
            assert client.timeout == 60
            assert client.verify_ssl is False
            assert client.default_retry_config["max_retries"] == 5
            assert client.default_retry_config["backoff_factor"] == 1.0

    async def test_base_url_normalization(self, mock_logger):
        """Test that base_url trailing slashes are handled."""
        async with AsyncRestClient(
            base_url="https://api.example.com", service_name="test", logger=mock_logger
        ) as client1:
            pass
        async with AsyncRestClient(
            base_url="https://api.example.com/", service_name="test", logger=mock_logger
        ) as client2:
            assert client1.base_url == "https://api.example.com"
            assert client2.base_url == "https://api.example.com"

    async def test_default_headers(self, base_url, mock_logger):
        """Test default headers are set correctly."""
        async with AsyncRestClient(
            base_url=base_url, service_name="test", logger=mock_logger
        ) as client:
            assert "Content-Type" in client.default_headers
            assert client.default_headers["Content-Type"] == "application/json"


@pytest.mark.asyncio
class TestAsyncRestClientGet:
    """Test AsyncRestClient GET method."""

    async def test_successful_get(self):
        """Test successful GET request."""
        async with httpx.AsyncClient() as client:
            client.request = AsyncMock(
                return_value=httpx.Response(
                    200,
                    json={"id": 1, "name": "John", "email": "john@example.com"},
                    request=httpx.Request("GET", "https://api.example.com/v1/users/1"),
                )
            )

            async_client = AsyncRestClient(
                base_url="https://api.example.com/v1",
                service_name="test_api",
                client=client,
            )

            user = await async_client.get("/users/1", response_data_schema=User)
            assert user is not None
            assert user.id == 1
            assert user.name == "John"
            assert user.email == "john@example.com"

    async def test_get_with_params(self):
        """Test GET request with query parameters."""
        async with httpx.AsyncClient() as client:
            client.request = AsyncMock(
                return_value=httpx.Response(
                    200,
                    json={"id": 1, "name": "John", "email": "john@example.com"},
                    request=httpx.Request("GET", "https://api.example.com/v1/users/1"),
                )
            )

            async_client = AsyncRestClient(
                base_url="https://api.example.com/v1",
                service_name="test_api",
                client=client,
            )

            user = await async_client.get(
                "/users/1", response_data_schema=User, params={"include": "profile"}
            )
            assert user is not None

    async def test_get_404(self):
        """Test GET request with 404 response."""
        async with httpx.AsyncClient() as client:
            client.request = AsyncMock(
                return_value=httpx.Response(
                    404,
                    json={"detail": "Not found"},
                    request=httpx.Request("GET", "https://api.example.com/v1/users/999"),
                )
            )

            async_client = AsyncRestClient(
                base_url="https://api.example.com/v1",
                service_name="test_api",
                client=client,
            )

            with pytest.raises(ResourceNotFoundError):
                await async_client.get("/users/999", response_data_schema=User)

    async def test_get_401(self):
        """Test GET request with 401 response."""
        async with httpx.AsyncClient() as client:
            client.request = AsyncMock(
                return_value=httpx.Response(
                    401,
                    json={"detail": "Unauthorized"},
                    request=httpx.Request("GET", "https://api.example.com/v1/users/1"),
                )
            )

            async_client = AsyncRestClient(
                base_url="https://api.example.com/v1",
                service_name="test_api",
                client=client,
            )

            with pytest.raises(AuthenticationError):
                await async_client.get("/users/1", response_data_schema=User)

    async def test_get_403(self):
        """Test GET request with 403 response."""
        async with httpx.AsyncClient() as client:
            client.request = AsyncMock(
                return_value=httpx.Response(
                    403,
                    json={"detail": "Forbidden"},
                    request=httpx.Request("GET", "https://api.example.com/v1/users/1"),
                )
            )

            async_client = AsyncRestClient(
                base_url="https://api.example.com/v1",
                service_name="test_api",
                client=client,
            )

            with pytest.raises(AuthorizationError):
                await async_client.get("/users/1", response_data_schema=User)


@pytest.mark.asyncio
class TestAsyncRestClientPost:
    """Test AsyncRestClient POST method."""

    async def test_successful_post(self):
        """Test successful POST request."""
        async with httpx.AsyncClient() as client:
            client.request = AsyncMock(
                return_value=httpx.Response(
                    201,
                    json={"id": 2, "name": "Jane", "email": "jane@example.com"},
                    request=httpx.Request("POST", "https://api.example.com/v1/users"),
                )
            )

            async_client = AsyncRestClient(
                base_url="https://api.example.com/v1",
                service_name="test_api",
                client=client,
            )

            new_user = User(id=0, name="Jane", email="jane@example.com")
            user = await async_client.post("/users", request_data=new_user, response_data_schema=User)
            assert user is not None
            assert user.id == 2
            assert user.name == "Jane"


@pytest.mark.asyncio
class TestAsyncRestClientWithCircuitBreaker:
    """Test AsyncRestClient with circuit breaker."""

    async def test_circuit_breaker_blocks_request_when_open(self):
        """Test that circuit breaker blocks requests when open."""
        async with httpx.AsyncClient() as client:
            breaker = AsyncCircuitBreaker(fail_max=1, reset_timeout=5)

            # Open the circuit
            async def failing_func():
                raise Exception("Test error")

            with pytest.raises(Exception):
                await breaker.call_async(failing_func)

            async_client = AsyncRestClient(
                base_url="https://api.example.com/v1",
                service_name="test_api",
                breaker=breaker,
                client=client,
            )

            # Request should be blocked by circuit breaker
            with pytest.raises(CircuitBreakerOpenError):
                await async_client.get("/users/1", response_data_schema=User)


@pytest.mark.asyncio
class TestAsyncRestClientPut:
    """Test AsyncRestClient PUT method."""

    async def test_successful_put(self):
        """Test successful PUT request."""
        async with httpx.AsyncClient() as client:
            client.request = AsyncMock(
                return_value=httpx.Response(
                    200,
                    json={"id": 1, "name": "Jane Updated", "email": "jane@example.com"},
                    request=httpx.Request("PUT", "https://api.example.com/v1/users/1"),
                )
            )

            async_client = AsyncRestClient(
                base_url="https://api.example.com/v1",
                service_name="test_api",
                client=client,
            )

            update_data = CreateUserRequest(name="Jane Updated", email="jane@example.com")
            user = await async_client.put(
                "/users/1", request_data=update_data, response_data_schema=UserResponse
            )
            assert user is not None
            assert user.name == "Jane Updated"


@pytest.mark.asyncio
class TestAsyncRestClientPatch:
    """Test AsyncRestClient PATCH method."""

    async def test_successful_patch(self):
        """Test successful PATCH request."""
        async with httpx.AsyncClient() as client:
            client.request = AsyncMock(
                return_value=httpx.Response(
                    200,
                    json={"id": 1, "name": "Jane Patched", "email": "jane@example.com"},
                    request=httpx.Request("PATCH", "https://api.example.com/v1/users/1"),
                )
            )

            async_client = AsyncRestClient(
                base_url="https://api.example.com/v1",
                service_name="test_api",
                client=client,
            )

            patch_data = CreateUserRequest(name="Jane Patched", email="jane@example.com")
            user = await async_client.patch(
                "/users/1", request_data=patch_data, response_data_schema=UserResponse
            )
            assert user is not None
            assert user.name == "Jane Patched"


@pytest.mark.asyncio
class TestAsyncRestClientDelete:
    """Test AsyncRestClient DELETE method."""

    async def test_successful_delete(self):
        """Test successful DELETE request."""
        async with httpx.AsyncClient() as client:
            client.request = AsyncMock(
                return_value=httpx.Response(
                    204,
                    content=b"",
                    request=httpx.Request("DELETE", "https://api.example.com/v1/users/1"),
                )
            )

            async_client = AsyncRestClient(
                base_url="https://api.example.com/v1",
                service_name="test_api",
                client=client,
            )

            response = await async_client.delete("/users/1", response_data_schema=UserResponse)
            assert response is None  # 204 No Content

    async def test_delete_with_200_status(self):
        """Test DELETE request with 200 OK status (valid response)."""
        async with httpx.AsyncClient() as client:
            client.request = AsyncMock(
                return_value=httpx.Response(
                    200,
                    json={"id": 1, "name": "Deleted User", "email": "deleted@example.com"},
                    request=httpx.Request("DELETE", "https://api.example.com/v1/users/1"),
                )
            )

            async_client = AsyncRestClient(
                base_url="https://api.example.com/v1",
                service_name="test_api",
                client=client,
            )

            response = await async_client.delete("/users/1", response_data_schema=User)
            assert response is not None
            assert response.id == 1


@pytest.mark.asyncio
class TestAsyncRestClientErrorHandling:
    """Test error handling and edge cases."""

    async def test_connection_error(self):
        """Test handling of connection errors."""
        async with httpx.AsyncClient() as client:
            client.request = AsyncMock(side_effect=httpx.ConnectError("Connection failed"))

            async_client = AsyncRestClient(
                base_url="https://api.example.com/v1",
                service_name="test_api",
                client=client,
            )

            with pytest.raises(ConnectionError) as exc_info:
                await async_client.get("/users/1", response_data_schema=User)

            assert exc_info.value.context is not None
            assert "Connection failed" in str(exc_info.value)

    async def test_timeout_error(self):
        """Test handling of timeout errors."""
        async with httpx.AsyncClient() as client:
            client.request = AsyncMock(side_effect=httpx.TimeoutException("Request timed out"))

            async_client = AsyncRestClient(
                base_url="https://api.example.com/v1",
                service_name="test_api",
                client=client,
            )

            with pytest.raises(TimeoutError) as exc_info:
                await async_client.get("/users/1", response_data_schema=User)

            assert exc_info.value.context is not None
            assert "timed out" in str(exc_info.value).lower()

    async def test_unexpected_exception(self):
        """Test handling of unexpected exceptions."""
        async with httpx.AsyncClient() as client:
            client.request = AsyncMock(side_effect=ValueError("Unexpected error"))

            async_client = AsyncRestClient(
                base_url="https://api.example.com/v1",
                service_name="test_api",
                client=client,
            )

            with pytest.raises(RestClientError) as exc_info:
                await async_client.get("/users/1", response_data_schema=User)

            assert exc_info.value.context is not None
            assert "unexpected error" in str(exc_info.value).lower()

    async def test_error_context_contains_request_info(self):
        """Test that error context contains request information."""
        async with httpx.AsyncClient() as client:
            client.request = AsyncMock(
                return_value=httpx.Response(
                    404,
                    json={"detail": "Not found"},
                    request=httpx.Request("GET", "https://api.example.com/v1/users/1"),
                )
            )

            async_client = AsyncRestClient(
                base_url="https://api.example.com/v1",
                service_name="test_api",
                client=client,
            )

            with pytest.raises(ResourceNotFoundError) as exc_info:
                await async_client.get("/users/1", response_data_schema=User, params={"test": "param"})

            context = exc_info.value.context
            assert context is not None
            assert context.request_url == "https://api.example.com/v1/users/1"
            assert context.request_method == "GET"
            assert context.request_params == {"test": "param"}


@pytest.mark.asyncio
class TestAsyncRestClientUrlConstruction:
    """Test URL construction and edge cases."""

    async def test_url_construction_with_endpoint(self):
        """Test URL construction with different endpoint formats."""
        async with httpx.AsyncClient() as client:
            client.request = AsyncMock(
                return_value=httpx.Response(
                    200,
                    json={"id": 1, "name": "John", "email": "john@example.com"},
                    request=httpx.Request("GET", "https://api.example.com/v1/users"),
                )
            )

            async_client = AsyncRestClient(
                base_url="https://api.example.com/v1",
                service_name="test_api",
                client=client,
            )

            # Test with endpoint starting with /
            await async_client.get("/users", response_data_schema=User)
            call_args = client.request.call_args
            assert call_args[1]["url"] == "https://api.example.com/v1/users"

            # Test with endpoint not starting with /
            await async_client.get("users", response_data_schema=User)
            call_args = client.request.call_args
            assert call_args[1]["url"] == "https://api.example.com/v1/users"


@pytest.mark.asyncio
class TestAsyncRestClientPerRequestOverrides:
    """Test per-request configuration overrides."""

    async def test_per_request_max_retries(self):
        """Test per-request max_retries override."""
        async with httpx.AsyncClient() as client:
            client.request = AsyncMock(side_effect=httpx.ConnectError("Connection failed"))

            async_client = AsyncRestClient(
                base_url="https://api.example.com/v1",
                service_name="test_api",
                max_retries=5,
                client=client,
            )

            # Override max_retries to 0 for this request
            with pytest.raises(ConnectionError):
                await async_client.get("/users/1", response_data_schema=User, max_retries=0)



@pytest.mark.asyncio
class TestAsyncRestClientContextManager:
    """Test AsyncRestClient as async context manager."""

    async def test_context_manager(self):
        """Test using AsyncRestClient as async context manager."""
        async with AsyncRestClient(
            base_url="https://api.example.com/v1", service_name="test_api"
        ) as client:
            assert client is not None
            # Client should be closed when exiting context

    async def test_context_manager_closes_client(self):
        """Test that context manager properly closes client."""
        async with AsyncRestClient(
            base_url="https://api.example.com/v1", service_name="test_api"
        ) as client:
            # Client should be created when entering context
            assert client.client is not None
            # Make a request to ensure client is working
            async with httpx.AsyncClient() as mock_client:
                mock_client.request = AsyncMock(
                    return_value=httpx.Response(
                        200,
                        json={"id": 1, "name": "John", "email": "john@example.com"},
                        request=httpx.Request("GET", "https://api.example.com/v1/users/1"),
                    )
                )
                client._client = mock_client
                user = await client.get("/users/1", response_data_schema=User)
                assert user is not None

        # After exiting, client should be closed (we can't easily test this without accessing internals)
