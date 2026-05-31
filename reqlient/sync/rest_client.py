import logging
import threading
from datetime import datetime
from typing import Generic, List, Optional, Type, get_origin
from urllib.parse import urljoin

import httpx
from pybreaker import CircuitBreaker
from pydantic import TypeAdapter

from ..core.config import BulkheadConfig, CircuitBreakerConfig, RetryConfig, TransportConfig
from ..core.errors import (
    ErrorContext,
    RestClientError,
)
from ..core.request_response import RequestContext, RequestT, ResponseContext, ResponseT
from .behaviors import (
    Behavior,
    BulkheadBehavior,
    CircuitBreakerBehavior,
    HttpBehavior,
    IdempotencyHeaderBehavior,
    InterceptorBehavior,
    LoggingBehavior,
    RequestValidationBehavior,
    ResponseValidationBehavior,
    RetryBehavior,
    StatusCodeValidationBehavior,
)
from .bulkhead import Bulkhead, BulkheadRegistry
from .circuit_breakers import CircuitBreakerRegistry
from .interceptors import Interceptor


class RestClient(Generic[RequestT, ResponseT]):
    """
    A robust REST API client with enhanced error handling, validation, logging, timing, and automatic retry support.

    Features:
        - Comprehensive error handling with specific error types and contextual information
        - Automatic error recovery strategies for different types of failures
        - Validates request and response data using Pydantic models
        - Logs all requests and responses, including timing and error details
        - Retries failed requests automatically for transient errors
    """

    def __init__(
        self,
        base_url: str,
        service_name: str,
        *,
        transport: TransportConfig = TransportConfig(),
        retry: Optional[RetryConfig] = RetryConfig(),
        circuit_breaker: Optional[CircuitBreakerConfig] = CircuitBreakerConfig(),
        bulkhead: Optional[BulkheadConfig] = None,
        logger: Optional[logging.Logger] = None,
        interceptors: Optional[List[Interceptor]] = None,
    ):
        """
        Initialize the RestClient.

        Args:
            base_url: Base URL for all requests.
            service_name: Name of the service; used for logging and for resolving
                the shared circuit breaker / bulkhead from their registries.
            transport: HTTP transport settings — timeout, TLS verification, and
                default headers. See TransportConfig.
            retry: Retry policy, or None to disable retries. Enabled by default.
                See RetryConfig.
            circuit_breaker: Circuit breaker policy, or None to disable it.
                Enabled by default. See CircuitBreakerConfig.
            bulkhead: Bulkhead (concurrency limit) policy, or None to disable it.
                Disabled by default. See BulkheadConfig.
            logger: Logger instance to use for request/response logging.
            interceptors: Optional interceptors hooking into the request lifecycle.
        """
        self.base_url = base_url.rstrip("/")
        self.service_name = service_name
        self.transport = transport
        self.logger = logger or logging.getLogger(__name__)
        self.default_headers = transport.default_headers or {"Content-Type": "application/json"}

        # Per-instance thread-local httpx client. This is per-instance (not a
        # module global) so that each client gets its own httpx.Client built
        # with its own verify_ssl/timeout — httpx bakes those into the client at
        # construction, so a shared client would leak one instance's settings to
        # another. It stays thread-local so each thread gets its own connection
        # pool.
        self._thread_local = threading.local()

        # Resolve the shared circuit breaker / bulkhead from their registries
        # (keyed by service_name), or None when the policy is disabled.
        resolved_breaker = (
            CircuitBreakerRegistry.get(
                service_name,
                fail_max=circuit_breaker.fail_max,
                reset_timeout=circuit_breaker.reset_timeout,
            )
            if circuit_breaker is not None
            else None
        )
        resolved_bulkhead = (
            BulkheadRegistry.get(
                service_name,
                max_concurrent=bulkhead.max_concurrent,
                max_wait=bulkhead.max_wait,
            )
            if bulkhead is not None
            else None
        )

        # Build separate pipelines for read and write operations.
        self.read_pipeline = self.__build_read_pipeline(
            breaker=resolved_breaker, bulkhead=resolved_bulkhead, retry=retry, interceptors=interceptors
        )
        self.write_pipeline = self.__build_write_pipeline(
            breaker=resolved_breaker, bulkhead=resolved_bulkhead, retry=retry, interceptors=interceptors
        )

    @property
    def session(self) -> httpx.Client:
        """Thread-safe access to a client per thread.

        ``follow_redirects=True`` preserves the redirect-following behavior that
        ``requests`` had by default (httpx does not follow redirects otherwise).
        TLS verification is configured at the client level because httpx, unlike
        ``requests``, does not accept ``verify`` as a per-request argument.
        """
        if not hasattr(self._thread_local, "session"):
            self._thread_local.session = httpx.Client(
                verify=self.transport.verify_ssl,
                timeout=self.transport.timeout,
                follow_redirects=True,
            )
        return self._thread_local.session

    def __build_read_pipeline(
        self,
        breaker: Optional[CircuitBreaker],
        bulkhead: Optional[Bulkhead],
        retry: Optional[RetryConfig],
        interceptors: Optional[List[Interceptor]],
    ) -> Behavior:
        """
        Build the read pipeline for GET and HEAD requests.

        Read pipeline (built backwards from HTTP):
        1. HttpBehavior - makes HTTP call
        2. LoggingBehavior - logs request/response
        3. StatusCodeValidationBehavior - validates status codes
        4. RetryBehavior - retries on network/server errors (when retry enabled)
        5. ResponseValidationBehavior - validates response schema (outside retry)
        6. CircuitBreakerBehavior - circuit breaker (when enabled)
        7. BulkheadBehavior - concurrency limiter, outside the breaker (when enabled)
        8. RequestValidationBehavior - validates request schema
        9. InterceptorBehavior - outermost layer
        """
        # 1. HttpBehavior - innermost, makes the actual HTTP call
        pipeline: Behavior = HttpBehavior(self.session, self.transport.timeout)

        # 2. LoggingBehavior - wraps HTTP to log before and after
        pipeline = LoggingBehavior(logger=self.logger, next_behavior=pipeline)

        # 3. StatusCodeValidationBehavior - validates status codes after logging
        pipeline = StatusCodeValidationBehavior(next_behavior=pipeline)

        # 4. RetryBehavior - wraps HTTP + logging + status validation (retries these)
        if retry is not None:
            pipeline = RetryBehavior(
                max_retries=retry.max_retries,
                backoff_factor=retry.backoff_factor,
                retry_status_codes=retry.status_codes,
                next_behavior=pipeline,
            )

        # 5. ResponseValidationBehavior - validates response schema (outside retry, not retried)
        pipeline = ResponseValidationBehavior(response_data_schema=None, next_behavior=pipeline)

        # 6. CircuitBreakerBehavior - wraps retry logic
        if breaker:
            pipeline = CircuitBreakerBehavior(breaker=breaker, next_behavior=pipeline)

        # 7. BulkheadBehavior - wraps the breaker so a full bulkhead is not counted
        #    as a breaker failure, and so validation failures don't consume a slot.
        if bulkhead:
            pipeline = BulkheadBehavior(bulkhead=bulkhead, next_behavior=pipeline)

        # 8. RequestValidationBehavior - validates request schema at the start
        pipeline = RequestValidationBehavior(request_data_schema=None, next_behavior=pipeline)

        # 9. InterceptorBehavior - outermost layer for custom hooks
        if interceptors:
            pipeline = InterceptorBehavior(interceptors=interceptors, next_behavior=pipeline)

        return pipeline

    def __build_write_pipeline(
        self,
        breaker: Optional[CircuitBreaker],
        bulkhead: Optional[Bulkhead],
        retry: Optional[RetryConfig],
        interceptors: Optional[List[Interceptor]],
    ) -> Behavior:
        """
        Build the write pipeline for POST, PUT, PATCH, and DELETE requests.

        Write pipeline (built backwards from HTTP):
        1. HttpBehavior - makes HTTP call
        2. LoggingBehavior - logs request/response
        3. StatusCodeValidationBehavior - validates status codes
        4. RetryBehavior - retries on network/server errors (when retry enabled)
        5. ResponseValidationBehavior - validates response schema (outside retry)
        6. CircuitBreakerBehavior - circuit breaker (when enabled)
        7. BulkheadBehavior - concurrency limiter, outside the breaker (when enabled)
        8. IdempotencyHeaderBehavior - adds idempotency headers for POST/PUT/DELETE
        9. RequestValidationBehavior - validates request schema
        10. InterceptorBehavior - outermost layer
        """
        # 1. HttpBehavior - innermost, makes the actual HTTP call
        pipeline: Behavior = HttpBehavior(self.session, self.transport.timeout)

        # 2. LoggingBehavior - wraps HTTP to log before and after
        pipeline = LoggingBehavior(logger=self.logger, next_behavior=pipeline)

        # 3. StatusCodeValidationBehavior - validates status codes after logging
        pipeline = StatusCodeValidationBehavior(next_behavior=pipeline)

        # 4. RetryBehavior - wraps HTTP + logging + status validation (retries these)
        if retry is not None:
            pipeline = RetryBehavior(
                max_retries=retry.max_retries,
                backoff_factor=retry.backoff_factor,
                retry_status_codes=retry.status_codes,
                next_behavior=pipeline,
            )

        # 5. ResponseValidationBehavior - validates response schema (outside retry, not retried)
        pipeline = ResponseValidationBehavior(response_data_schema=None, next_behavior=pipeline)

        # 6. CircuitBreakerBehavior - wraps retry logic
        if breaker:
            pipeline = CircuitBreakerBehavior(breaker=breaker, next_behavior=pipeline)

        # 7. BulkheadBehavior - wraps the breaker (see read pipeline for rationale).
        #    Placed inside idempotency so the key is generated before acquiring a slot.
        if bulkhead:
            pipeline = BulkheadBehavior(bulkhead=bulkhead, next_behavior=pipeline)

        # 8. IdempotencyHeaderBehavior - adds idempotency headers (only in write pipeline)
        pipeline = IdempotencyHeaderBehavior(next_behavior=pipeline)

        # 9. RequestValidationBehavior - validates request schema at the start
        pipeline = RequestValidationBehavior(request_data_schema=None, next_behavior=pipeline)

        # 10. InterceptorBehavior - outermost layer for custom hooks
        if interceptors:
            pipeline = InterceptorBehavior(interceptors=interceptors, next_behavior=pipeline)

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

    def __request(
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
            request_data: Pydantic model for the request data
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
        # Properly join base_url and endpoint, handling edge cases like double slashes
        # urljoin handles this correctly, but we need to ensure base_url ends with /
        base = self.base_url.rstrip("/") + "/"
        endpoint_clean = endpoint.lstrip("/")
        url = urljoin(base, endpoint_clean)
        final_headers = {**self.default_headers, **(headers or {})}

        # Store the type of request_data for validation
        request_data_schema = type(request_data) if request_data else None

        request_context = RequestContext(
            method=method,
            url=url,
            headers=final_headers,
            params=params,
            data=request_data.model_dump(by_alias=True, mode='json') if request_data else None,
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
                response_context = self.read_pipeline.handle(request_context)
            else:  # POST, PUT, PATCH, DELETE
                response_context = self.write_pipeline.handle(request_context)

            # The ResponseValidationBehavior has already validated and returned the data as a dict.
            # If data is None (e.g., 204), return None. Otherwise, construct the Pydantic model.
            if response_context.data is None:
                return None
            # Use TypeAdapter for generic types (e.g., list[Model]) or regular models
            if get_origin(response_data_schema) is not None or not hasattr(
                response_data_schema, "model_validate"
            ):
                adapter = TypeAdapter(response_data_schema)
                return adapter.validate_python(response_context.data)
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

    def get(
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
        return self.__request(
            method="GET",
            endpoint=endpoint,
            response_data_schema=response_data_schema,
            params=params,
            headers=headers,
            max_retries=max_retries,
            retry_backoff_factor=retry_backoff_factor,
        )

    def post(
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
        return self.__request(
            method="POST",
            endpoint=endpoint,
            response_data_schema=response_data_schema,
            request_data=request_data,
            params=params,
            headers=headers,
            max_retries=max_retries,
            retry_backoff_factor=retry_backoff_factor,
        )

    def put(
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
        return self.__request(
            method="PUT",
            endpoint=endpoint,
            response_data_schema=response_data_schema,
            request_data=request_data,
            params=params,
            headers=headers,
            max_retries=max_retries,
            retry_backoff_factor=retry_backoff_factor,
        )

    def delete(
        self,
        endpoint: str,
        response_data_schema: Type[ResponseT],
        params: Optional[dict[str, str]] = None,
        headers: Optional[dict[str, str]] = None,
        max_retries: Optional[int] = None,
        retry_backoff_factor: Optional[float] = None,
    ) -> Optional[ResponseT]:
        """Make a DELETE request"""
        return self.__request(
            method="DELETE",
            endpoint=endpoint,
            response_data_schema=response_data_schema,
            request_data=None,
            params=params,
            headers=headers,
            max_retries=max_retries,
            retry_backoff_factor=retry_backoff_factor,
        )

    def patch(
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
        return self.__request(
            method="PATCH",
            endpoint=endpoint,
            response_data_schema=response_data_schema,
            request_data=request_data,
            params=params,
            headers=headers,
            max_retries=max_retries,
            retry_backoff_factor=retry_backoff_factor,
        )
