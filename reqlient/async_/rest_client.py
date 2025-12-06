import logging
from datetime import datetime
from typing import Generic, List, Optional, Set, Type
from urllib.parse import urljoin

import httpx

from .behaviors import (
    AsyncBehavior,
    AsyncCircuitBreakerBehavior,
    AsyncHttpBehavior,
    AsyncIdempotencyHeaderBehavior,
    AsyncInterceptorBehavior,
    AsyncLoggingBehavior,
    AsyncRequestDataSchemaValidationBehavior,
    AsyncResponseDataSchemaValidationBehavior,
    AsyncRetryBehavior,
    AsyncStatusCodeValidationBehavior,
)
from .circuit_breakers import AsyncCircuitBreaker, AsyncCircuitBreakerRegistry
from .interceptors import AsyncInterceptor
from ..core.errors import (
    ErrorContext,
    RestClientError,
)
from ..core.request_response import RequestContext, RequestT, ResponseContext, ResponseT


class AsyncRestClient(Generic[RequestT, ResponseT]):
    """
    An async robust REST API client with enhanced error handling, validation, logging, timing, and automatic retry support.

    Features:
        - Comprehensive error handling with specific error types and contextual information
        - Automatic error recovery strategies for different types of failures
        - Validates request and response data using Pydantic models
        - Logs all requests and responses, including timing and error details
        - Retries failed requests automatically for transient errors
        - Supports async context manager protocol for proper cleanup
    """

    def __init__(
        self,
        base_url: str,
        service_name: str,
        logger: Optional[logging.Logger] = None,
        default_headers: Optional[dict[str, str]] = None,
        timeout: int = 30,
        verify_ssl: bool = True,
        max_retries: int = 3,
        retry_backoff_factor: float = 0.5,
        retry_status_codes: Optional[Set[int]] = None,
        breaker: Optional[AsyncCircuitBreaker] = None,
        interceptors: Optional[List[AsyncInterceptor]] = None,
        client: Optional[httpx.AsyncClient] = None,
        use_circuit_breaker: bool = True,
    ):
        """
        Initialize the AsyncRestClient with base configuration.

        Args:
            base_url: Base URL for all requests
            service_name: The name of the service, used for logging and circuit breaker registry.
            logger: Logger instance to use for request/response logging
            default_headers: Default headers to include in all requests
            timeout: Request timeout in seconds
            verify_ssl: Whether to verify SSL certificates
            max_retries: Maximum number of retry attempts for transient errors
            retry_backoff_factor: Multiplier for exponential backoff between retry attempts
            retry_status_codes: Set of HTTP status codes that should trigger a retry
            breaker: An optional async circuit breaker instance. If not provided and use_circuit_breaker
                    is True, one will be obtained from AsyncCircuitBreakerRegistry using service_name.
            interceptors: An optional list of async interceptors to hook into the request/response cycle.
            client: Optional httpx.AsyncClient instance. If not provided, one will be created.
            use_circuit_breaker: Whether to use a circuit breaker. If True and no breaker is provided,
                                one will be obtained from AsyncCircuitBreakerRegistry. Default is True.
        """
        self.base_url = base_url.rstrip("/")
        self.service_name = service_name
        self.logger = logger or logging.getLogger(__name__)
        self.default_headers = default_headers or {"Content-Type": "application/json"}
        self.timeout = timeout
        self.verify_ssl = verify_ssl
        self._use_circuit_breaker = use_circuit_breaker
        self._explicit_breaker = breaker

        # Store default behaviors to be configured per-request
        self.default_retry_config = {
            "max_retries": max_retries,
            "backoff_factor": retry_backoff_factor,
            "retry_status_codes": retry_status_codes or {408, 429, 500, 502, 503, 504},
        }

        # Create or use provided httpx client
        self._client = client
        self._client_owned = client is None

        # Store pipeline params for lazy initialization
        self._pipeline_params = {
            "logger": self.logger,
            "timeout": self.timeout,
            "verify_ssl": self.verify_ssl,
            "max_retries": max_retries,
            "retry_backoff_factor": retry_backoff_factor,
            "retry_status_codes": retry_status_codes or {408, 429, 500, 502, 503, 504},
            "interceptors": interceptors,
        }

        # Pipelines will be built lazily or in __aenter__
        self._read_pipeline: Optional[AsyncBehavior] = None
        self._write_pipeline: Optional[AsyncBehavior] = None

    @property
    def client(self) -> httpx.AsyncClient:
        """Get or create the httpx async client."""
        if self._client is None:
            self._client = httpx.AsyncClient(verify=self.verify_ssl, timeout=self.timeout)
            self._client_owned = True
        return self._client

    async def _resolve_breaker(self) -> Optional[AsyncCircuitBreaker]:
        """Resolve circuit breaker: use provided, get from registry, or None."""
        if self._explicit_breaker is not None:
            return self._explicit_breaker
        elif self._use_circuit_breaker:
            return await AsyncCircuitBreakerRegistry.get(self.service_name)
        return None

    async def _ensure_pipelines_built(self):
        """Ensure pipelines are built (requires async for registry access)."""
        if self._read_pipeline is None or self._write_pipeline is None:
            breaker = await self._resolve_breaker()
            params = {**self._pipeline_params, "breaker": breaker}
            self._read_pipeline = self._build_read_pipeline(**params)
            self._write_pipeline = self._build_write_pipeline(**params)

    @property
    def read_pipeline(self) -> AsyncBehavior:
        """Get read pipeline (must call _ensure_pipelines_built first)."""
        if self._read_pipeline is None:
            raise RuntimeError("Pipelines not initialized. Use 'async with' context manager.")
        return self._read_pipeline

    @property
    def write_pipeline(self) -> AsyncBehavior:
        """Get write pipeline (must call _ensure_pipelines_built first)."""
        if self._write_pipeline is None:
            raise RuntimeError("Pipelines not initialized. Use 'async with' context manager.")
        return self._write_pipeline

    async def __aenter__(self):
        """Async context manager entry."""
        await self._ensure_pipelines_built()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit - close client if we own it."""
        await self.aclose()

    async def aclose(self):
        """Close the httpx client if we own it."""
        if self._client_owned and self._client is not None:
            await self._client.aclose()
            self._client = None

    def _build_read_pipeline(
        self,
        logger: logging.Logger,
        timeout: int,
        verify_ssl: bool,
        breaker: Optional[AsyncCircuitBreaker],
        max_retries: int,
        retry_backoff_factor: float,
        retry_status_codes: Set[int],
        interceptors: Optional[List[AsyncInterceptor]],
    ) -> AsyncBehavior:
        """
        Build the read pipeline for GET and HEAD requests.
        
        Read pipeline (built backwards from HTTP):
        1. AsyncHttpBehavior - makes HTTP call
        2. AsyncLoggingBehavior - logs request/response
        3. AsyncStatusCodeValidationBehavior - validates status codes
        4. AsyncRetryBehavior - retries on network/server errors
        5. AsyncResponseValidationBehavior - validates response schema (outside retry)
        6. AsyncCircuitBreakerBehavior - circuit breaker
        7. AsyncRequestDataSchemaValidationBehavior - validates request data schema
        8. AsyncInterceptorBehavior - outermost layer
        """
        # 1. AsyncHttpBehavior - innermost, makes the actual HTTP call
        # Pass a lambda to get the client dynamically
        pipeline: AsyncBehavior = AsyncHttpBehavior(lambda: self.client, timeout, verify_ssl)
        
        # 2. AsyncLoggingBehavior - wraps HTTP to log before and after
        pipeline = AsyncLoggingBehavior(logger=logger, next_behavior=pipeline)
        
        # 3. AsyncStatusCodeValidationBehavior - validates status codes after HTTP
        pipeline = AsyncStatusCodeValidationBehavior(next_behavior=pipeline)
        
        # 4. AsyncRetryBehavior - wraps HTTP + logging + status validation (retries these)
        pipeline = AsyncRetryBehavior(
            max_retries=max_retries,
            backoff_factor=retry_backoff_factor,
            retry_status_codes=retry_status_codes,
            next_behavior=pipeline,
        )
        
        # 5. AsyncResponseDataSchemaValidationBehavior - validates response data schema (outside retry, not retried)
        pipeline = AsyncResponseDataSchemaValidationBehavior(response_data_schema=None, next_behavior=pipeline)
        
        # 6. AsyncCircuitBreakerBehavior - wraps retry logic
        if breaker:
            pipeline = AsyncCircuitBreakerBehavior(breaker=breaker, next_behavior=pipeline)
        
        # 7. AsyncRequestDataSchemaValidationBehavior - validates request schema at the start
        pipeline = AsyncRequestDataSchemaValidationBehavior(request_schema=None, next_behavior=pipeline)
        
        # 8. AsyncInterceptorBehavior - outermost layer for custom hooks
        if interceptors:
            pipeline = AsyncInterceptorBehavior(interceptors=interceptors, next_behavior=pipeline)

        return pipeline

    def _build_write_pipeline(
        self,
        logger: logging.Logger,
        timeout: int,
        verify_ssl: bool,
        breaker: Optional[AsyncCircuitBreaker],
        max_retries: int,
        retry_backoff_factor: float,
        retry_status_codes: Set[int],
        interceptors: Optional[List[AsyncInterceptor]],
    ) -> AsyncBehavior:
        """
        Build the write pipeline for POST, PUT, PATCH, and DELETE requests.
        
        Write pipeline (built backwards from HTTP):
        1. AsyncHttpBehavior - makes HTTP call
        2. AsyncLoggingBehavior - logs request/response
        3. AsyncStatusCodeValidationBehavior - validates status codes
        4. AsyncRetryBehavior - retries on network/server errors
        5. AsyncResponseDataSchemaValidationBehavior - validates response data schema (outside retry)
        6. AsyncCircuitBreakerBehavior - circuit breaker
        7. AsyncIdempotencyHeaderBehavior - adds idempotency headers for POST/PUT/DELETE
        8. AsyncRequestDataSchemaValidationBehavior - validates request schema
        9. AsyncInterceptorBehavior - outermost layer
        """
        # 1. AsyncHttpBehavior - innermost, makes the actual HTTP call
        # Pass a lambda to get the client dynamically
        pipeline: AsyncBehavior = AsyncHttpBehavior(lambda: self.client, timeout, verify_ssl)
        
        # 2. AsyncLoggingBehavior - wraps HTTP to log before and after
        pipeline = AsyncLoggingBehavior(logger=logger, next_behavior=pipeline)
        
        # 3. AsyncStatusCodeValidationBehavior - validates status codes after HTTP
        pipeline = AsyncStatusCodeValidationBehavior(next_behavior=pipeline)
        
        # 4. AsyncRetryBehavior - wraps HTTP + logging + status validation (retries these)
        pipeline = AsyncRetryBehavior(
            max_retries=max_retries,
            backoff_factor=retry_backoff_factor,
            retry_status_codes=retry_status_codes,
            next_behavior=pipeline,
        )
        
        # 5. AsyncResponseDataSchemaValidationBehavior - validates response data schema (outside retry, not retried)
        pipeline = AsyncResponseDataSchemaValidationBehavior(response_data_schema=None, next_behavior=pipeline)
        
        # 6. AsyncCircuitBreakerBehavior - wraps retry logic
        if breaker:
            pipeline = AsyncCircuitBreakerBehavior(breaker=breaker, next_behavior=pipeline)
        
        # 7. AsyncIdempotencyHeaderBehavior - adds idempotency headers (only in write pipeline)
        pipeline = AsyncIdempotencyHeaderBehavior(next_behavior=pipeline)
        
        # 8. AsyncRequestDataSchemaValidationBehavior - validates request data schema at the start
        pipeline = AsyncRequestDataSchemaValidationBehavior(request_data_schema=None, next_behavior=pipeline)
        
        # 9. AsyncInterceptorBehavior - outermost layer for custom hooks
        if interceptors:
            pipeline = AsyncInterceptorBehavior(interceptors=interceptors, next_behavior=pipeline)

        return pipeline

    def __create_error_context(
        self,
        request_context: RequestContext,
        error: Exception,
        response_context: Optional[ResponseContext] = None,
    ) -> ErrorContext:
        """Create a detailed error context for logging and error reporting."""
        return ErrorContext(
            timestamp=datetime.now(),
            request_url=request_context.url,
            request_method=request_context.method,
            request_headers=request_context.headers,
            request_params=request_context.params,
            request_data=request_context.data,
            response_status=response_context.status_code if response_context else None,
            response_headers=response_context.headers if response_context else None,
            response_data=response_context.data if response_context else None,
            error_message=str(error),
            error_type=type(error).__name__,
        )

    async def __request(
        self,
        method: str,
        endpoint: str,
        response_data_schema: Type[ResponseT],
        request_data: Optional[RequestT] = None,
        params: Optional[dict[str, str]] = None,
        headers: Optional[dict[str, str]] = None,
        # Per-request overrides
        max_retries: Optional[int] = None,
        retry_backoff_factor: Optional[float] = None,
    ) -> Optional[ResponseT]:
        """
        Execute an HTTP request by processing it through the behavior pipeline.

        Args:
            method: HTTP method (GET, POST, etc.)
            endpoint: API endpoint
            response_data_schema: Pydantic model for the response
            request_data: Pydantic model instance for the request data
            params: URL parameters
            headers: Request headers
            max_retries: Per-request override for maximum retry attempts.
            retry_backoff_factor: Per-request override for retry backoff factor.

        Returns:
            The validated response as a Pydantic model.

        Raises:
            RequestValidationError: If request data fails validation.
            ResponseValidationError: If response data fails validation.
            CircuitBreakerOpenError: If the circuit is open.
            AuthenticationError: For 401 Unauthorized errors.
            AuthorizationError: For 403 Forbidden errors.
            ResourceNotFoundError: For 404 Not Found errors.
            ConnectionError: For network-related errors.
            TimeoutError: If the request times out.
            RateLimitError: For 429 Too Many Requests errors.
            ServerError: For 5xx server-side errors.
            StatusCodeError: For other 4xx client errors.
            RestClientError: For any other client-related errors.
        """
        # Ensure pipelines are initialized
        await self._ensure_pipelines_built()

        # Properly join base_url and endpoint, handling edge cases like double slashes
        base = self.base_url.rstrip("/") + "/"
        endpoint_clean = endpoint.lstrip("/")
        url = urljoin(base, endpoint_clean)
        final_headers = {**self.default_headers, **(headers or {})}

        # Serialize request data if provided
        request_data_dict = None
        request_data_schema = None
        if request_data is not None:
            request_data_dict = request_data.model_dump(by_alias=True)
            request_data_schema = type(request_data)

        request_context = RequestContext(
            method=method,
            url=url,
            headers=final_headers,
            params=params,
            data=request_data_dict,
            context={
                "max_retries": max_retries,
                "retry_backoff_factor": retry_backoff_factor,
            },
            request_data_schema=request_data_schema,
            response_data_schema=response_data_schema,
        )

        # Route to appropriate pipeline based on HTTP method
        # Per-request overrides are passed via context
        response_context = None
        try:
            if method in ["GET", "HEAD"]:
                response_context = await self.read_pipeline.handle(request_context)
            else:  # POST, PUT, PATCH, DELETE
                response_context = await self.write_pipeline.handle(request_context)

            # The AsyncResponseValidationBehavior has already validated and returned the data as a dict.
            # If data is None (e.g., 204), return None. Otherwise, construct the Pydantic model.
            if response_context.data is None:
                return None
            # Response data is already validated by ValidationBehavior, just construct the model
            return response_data_schema.model_validate(response_context.data)

        except RestClientError as e:
            # For our custom errors, preserve existing context if it has response info,
            # otherwise create/update context with available response_context
            if e.context is None or (
                e.context.response_status is None and response_context is not None
            ):
                # Only create/update context if it doesn't exist or doesn't have response status
                e.context = self.__create_error_context(request_context, e, response_context)
            raise e
        except Exception as e:
            # For unexpected errors, wrap them in a generic RestClientError.
            context = self.__create_error_context(request_context, e, response_context)
            raise RestClientError(f"An unexpected error occurred: {str(e)}", context=context)

    async def get(
        self,
        endpoint: str,
        response_data_schema: Type[ResponseT],
        params: Optional[dict[str, str]] = None,
        headers: Optional[dict[str, str]] = None,
        max_retries: Optional[int] = None,
        retry_backoff_factor: Optional[float] = None,
    ) -> Optional[ResponseT]:
        """
        Make a GET request

        Args:
            endpoint: API endpoint
            response_data_schema: The Pydantic model to validate the response against.
            params: URL parameters
            headers: Additional headers
            max_retries: Override instance max_retries for this request
            retry_backoff_factor: Override instance retry_backoff_factor for this request

        Returns:
            Validated response data as Pydantic model
        """
        return await self.__request(
            method="GET",
            endpoint=endpoint,
            response_data_schema=response_data_schema,
            request_data=None,
            params=params,
            headers=headers,
            max_retries=max_retries,
            retry_backoff_factor=retry_backoff_factor,
        )

    async def post(
        self,
        endpoint: str,
        request_data: RequestT,
        response_data_schema: Type[ResponseT],
        params: Optional[dict[str, str]] = None,
        headers: Optional[dict[str, str]] = None,
        max_retries: Optional[int] = None,
        retry_backoff_factor: Optional[float] = None,
    ) -> Optional[ResponseT]:
        """Make a POST request"""
        return await self.__request(
            method="POST",
            endpoint=endpoint,
            response_data_schema=response_data_schema,
            request_data=request_data,
            params=params,
            headers=headers,
            max_retries=max_retries,
            retry_backoff_factor=retry_backoff_factor,
        )

    async def put(
        self,
        endpoint: str,
        request_data: RequestT,
        response_data_schema: Type[ResponseT],
        params: Optional[dict[str, str]] = None,
        headers: Optional[dict[str, str]] = None,
        max_retries: Optional[int] = None,
        retry_backoff_factor: Optional[float] = None,
    ) -> Optional[ResponseT]:
        """Make a PUT request"""
        return await self.__request(
            method="PUT",
            endpoint=endpoint,
            response_data_schema=response_data_schema,
            request_data=request_data,
            params=params,
            headers=headers,
            max_retries=max_retries,
            retry_backoff_factor=retry_backoff_factor,
        )

    async def delete(
        self,
        endpoint: str,
        response_data_schema: Type[ResponseT],
        params: Optional[dict[str, str]] = None,
        headers: Optional[dict[str, str]] = None,
        max_retries: Optional[int] = None,
        retry_backoff_factor: Optional[float] = None,
    ) -> Optional[ResponseT]:
        """Make a DELETE request"""
        return await self.__request(
            method="DELETE",
            endpoint=endpoint,
            response_data_schema=response_data_schema,
            request_data=None,
            params=params,
            headers=headers,
            max_retries=max_retries,
            retry_backoff_factor=retry_backoff_factor,
        )

    async def patch(
        self,
        endpoint: str,
        request_data: RequestT,
        response_data_schema: Type[ResponseT],
        params: Optional[dict[str, str]] = None,
        headers: Optional[dict[str, str]] = None,
        max_retries: Optional[int] = None,
        retry_backoff_factor: Optional[float] = None,
    ) -> Optional[ResponseT]:
        """Make a PATCH request"""
        return await self.__request(
            method="PATCH",
            endpoint=endpoint,
            response_data_schema=response_data_schema,
            request_data=request_data,
            params=params,
            headers=headers,
            max_retries=max_retries,
            retry_backoff_factor=retry_backoff_factor,
        )
