import logging
import threading
from datetime import datetime
from typing import Generic, List, Optional, Set, Type, Union
from urllib.parse import urljoin

import requests
from pybreaker import CircuitBreaker

from .circuit_breakers import CircuitBreakerRegistry
from .behaviors import (
    Behavior,
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
from ..core.errors import (
    ErrorContext,
    RestClientError,
)
from .interceptors import Interceptor
from ..core.request_response import RequestContext, RequestT, ResponseContext, ResponseT

_thread_local = threading.local()


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
        logger: Optional[logging.Logger] = None,
        default_headers: Optional[dict[str, str]] = None,
        timeout: int = 30,
        verify_ssl: bool = True,
        max_retries: int = 3,
        retry_backoff_factor: float = 0.5,
        retry_status_codes: Optional[Set[int]] = None,
        breaker: Optional[CircuitBreaker] = None,
        interceptors: Optional[List[Interceptor]] = None,
        use_circuit_breaker: bool = True,
    ):
        """
        Initialize the RestClient with base configuration.

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
            breaker: An optional circuit breaker instance. If not provided and use_circuit_breaker
                    is True, one will be obtained from CircuitBreakerRegistry using service_name.
            interceptors: An optional list of interceptors to hook into the request/response cycle.
            use_circuit_breaker: Whether to use a circuit breaker. If True and no breaker is provided,
                                one will be obtained from CircuitBreakerRegistry. Default is True.
        """
        self.base_url = base_url.rstrip("/")
        self.service_name = service_name
        self.logger = logger or logging.getLogger(__name__)
        self.default_headers = default_headers or {"Content-Type": "application/json"}
        self.timeout = timeout
        self.verify_ssl = verify_ssl

        # Resolve circuit breaker: use provided, get from registry, or None
        if breaker is not None:
            resolved_breaker = breaker
        elif use_circuit_breaker:
            resolved_breaker = CircuitBreakerRegistry.get(service_name)
        else:
            resolved_breaker = None

        # Store default behaviors to be configured per-request
        self.default_retry_config = {
            "max_retries": max_retries,
            "backoff_factor": retry_backoff_factor,
            "retry_status_codes": retry_status_codes or {408, 429, 500, 502, 503, 504},
        }

        # Build separate pipelines for read and write operations
        pipeline_params = {
            "logger": self.logger,
            "breaker": resolved_breaker,
            "timeout": self.timeout,
            "verify_ssl": self.verify_ssl,
            "max_retries": max_retries,
            "retry_backoff_factor": retry_backoff_factor,
            "retry_status_codes": retry_status_codes or {408, 429, 500, 502, 503, 504},
            "interceptors": interceptors,
        }

        # Read pipeline: optimized for GET/HEAD
        self.read_pipeline = self.__build_read_pipeline(**pipeline_params)

        # Write pipeline: optimized for POST/PUT/PATCH/DELETE with idempotency
        self.write_pipeline = self.__build_write_pipeline(**pipeline_params)

    @property
    def session(self) -> requests.Session:
        """Thread-safe access to a session per thread"""
        if not hasattr(_thread_local, "session"):
            _thread_local.session = requests.Session()
        return _thread_local.session

    def __build_read_pipeline(
        self,
        logger: logging.Logger,
        timeout: int,
        verify_ssl: bool,
        breaker: Optional[CircuitBreaker],
        max_retries: int,
        retry_backoff_factor: float,
        retry_status_codes: Set[int],
        interceptors: Optional[List[Interceptor]],
    ) -> Behavior:
        """
        Build the read pipeline for GET and HEAD requests.
        
        Read pipeline (built backwards from HTTP):
        1. HttpBehavior - makes HTTP call
        2. LoggingBehavior - logs request/response
        3. StatusCodeValidationBehavior - validates status codes
        4. RetryBehavior - retries on network/server errors
        5. ResponseValidationBehavior - validates response schema (outside retry)
        6. CircuitBreakerBehavior - circuit breaker
        7. RequestValidationBehavior - validates request schema
        8. InterceptorBehavior - outermost layer
        """
        # 1. HttpBehavior - innermost, makes the actual HTTP call
        pipeline: Behavior = HttpBehavior(self.session, timeout, verify_ssl)
        
        # 2. LoggingBehavior - wraps HTTP to log before and after
        pipeline = LoggingBehavior(logger=logger, next_behavior=pipeline)
        
        # 3. StatusCodeValidationBehavior - validates status codes after logging
        pipeline = StatusCodeValidationBehavior(next_behavior=pipeline)
        
        # 4. RetryBehavior - wraps HTTP + logging + status validation (retries these)
        pipeline = RetryBehavior(
            max_retries=max_retries,
            backoff_factor=retry_backoff_factor,
            retry_status_codes=retry_status_codes,
            next_behavior=pipeline,
        )
        
        # 5. ResponseValidationBehavior - validates response schema (outside retry, not retried)
        pipeline = ResponseValidationBehavior(response_data_schema=None, next_behavior=pipeline)
        
        # 6. CircuitBreakerBehavior - wraps retry logic
        if breaker:
            pipeline = CircuitBreakerBehavior(breaker=breaker, next_behavior=pipeline)
        
        # 7. RequestValidationBehavior - validates request schema at the start
        pipeline = RequestValidationBehavior(request_data_schema=None, next_behavior=pipeline)
        
        # 8. InterceptorBehavior - outermost layer for custom hooks
        if interceptors:
            pipeline = InterceptorBehavior(interceptors=interceptors, next_behavior=pipeline)

        return pipeline

    def __build_write_pipeline(
        self,
        logger: logging.Logger,
        timeout: int,
        verify_ssl: bool,
        breaker: Optional[CircuitBreaker],
        max_retries: int,
        retry_backoff_factor: float,
        retry_status_codes: Set[int],
        interceptors: Optional[List[Interceptor]],
    ) -> Behavior:
        """
        Build the write pipeline for POST, PUT, PATCH, and DELETE requests.
        
        Write pipeline (built backwards from HTTP):
        1. HttpBehavior - makes HTTP call
        2. LoggingBehavior - logs request/response
        3. StatusCodeValidationBehavior - validates status codes
        4. RetryBehavior - retries on network/server errors
        5. ResponseValidationBehavior - validates response schema (outside retry)
        6. CircuitBreakerBehavior - circuit breaker
        7. IdempotencyHeaderBehavior - adds idempotency headers for POST/PUT/DELETE
        8. RequestValidationBehavior - validates request schema
        9. InterceptorBehavior - outermost layer
        """
        # 1. HttpBehavior - innermost, makes the actual HTTP call
        pipeline: Behavior = HttpBehavior(self.session, timeout, verify_ssl)
        
        # 2. LoggingBehavior - wraps HTTP to log before and after
        pipeline = LoggingBehavior(logger=logger, next_behavior=pipeline)
        
        # 3. StatusCodeValidationBehavior - validates status codes after logging
        pipeline = StatusCodeValidationBehavior(next_behavior=pipeline)
        
        # 4. RetryBehavior - wraps HTTP + logging + status validation (retries these)
        pipeline = RetryBehavior(
            max_retries=max_retries,
            backoff_factor=retry_backoff_factor,
            retry_status_codes=retry_status_codes,
            next_behavior=pipeline,
        )
        
        # 5. ResponseValidationBehavior - validates response schema (outside retry, not retried)
        pipeline = ResponseValidationBehavior(response_data_schema=None, next_behavior=pipeline)
        
        # 6. CircuitBreakerBehavior - wraps retry logic
        if breaker:
            pipeline = CircuitBreakerBehavior(breaker=breaker, next_behavior=pipeline)
        
        # 7. IdempotencyHeaderBehavior - adds idempotency headers (only in write pipeline)
        pipeline = IdempotencyHeaderBehavior(next_behavior=pipeline)
        
        # 8. RequestValidationBehavior - validates request schema at the start
        pipeline = RequestValidationBehavior(request_data_schema=None, next_behavior=pipeline)
        
        # 9. InterceptorBehavior - outermost layer for custom hooks
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
            data=request_data.model_dump(by_alias=True) if request_data else None,
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
