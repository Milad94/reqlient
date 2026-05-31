import json
import logging
import time
import uuid
from abc import ABC, abstractmethod
from collections.abc import Collection
from datetime import datetime
from typing import Any, Generic, Optional, get_origin

import httpx
from pybreaker import CircuitBreaker
from pybreaker import CircuitBreakerError as PybreakerError
from pydantic import TypeAdapter

from ..core.errors import (
    AuthenticationError,
    AuthorizationError,
    BulkheadFullError,
    ErrorContext,
    RateLimitError,
    RequestError,
    RequestValidationError,
    ResourceNotFoundError,
    ResponseValidationError,
    RestClientError,
    RetryableError,
    ServerError,
    StatusCodeError,
    TimeoutError,
)
from ..core.errors import (
    CircuitBreakerOpenError as CustomCircuitBreakerOpenError,
)
from ..core.errors import (
    ConnectionError as CustomConnectionError,
)
from ..core.request_response import RequestContext, RequestT, ResponseContext, ResponseT
from ..core.utils import sanitize_sensitive_data
from .bulkhead import Bulkhead
from .interceptors import Interceptor


def _create_error_context(
    request: RequestContext, error: Exception, response: ResponseContext | None = None
) -> ErrorContext:
    """Create a detailed error context for logging and error reporting."""
    return ErrorContext(
        timestamp=datetime.now(),
        request_url=request.url,
        request_method=request.method,
        request_headers=request.headers,
        request_params=request.params,
        request_data=request.data,
        response_status=response.status_code if response else None,
        response_headers=response.headers if response else None,
        response_data=response.data if response else None,
        error_message=str(error),
        error_type=type(error).__name__,
    )


class Behavior(Generic[RequestT, ResponseT], ABC):
    """Base class for all behaviors in the pipeline."""

    def __init__(self, next_behavior: Optional["Behavior"] = None, **kwargs):
        self.next_behavior = next_behavior

    @abstractmethod
    def handle(self, request: RequestContext) -> ResponseContext:
        """Process the request and pass it to the next behavior."""

    def _handle_next(self, request: RequestContext) -> ResponseContext:
        if self.next_behavior:
            return self.next_behavior.handle(request)
        # This should theoretically not be reached if HttpBehavior is always last.
        raise RuntimeError("Pipeline ended unexpectedly without an HttpBehavior.")


class LoggingBehavior(Behavior):
    """Behavior for logging requests and responses."""

    def __init__(self, logger: logging.Logger, **kwargs):
        super().__init__(**kwargs)
        self.logger = logger

    def handle(self, request: RequestContext) -> ResponseContext:
        """Log the request, pass it on, and log the response or error."""
        sanitized_headers = sanitize_sensitive_data(request.headers)
        sanitized_data = sanitize_sensitive_data(request.data)
        sanitized_params = sanitize_sensitive_data(request.params)

        self.logger.info(
            f"Request: {request.method} {request.url}\n"
            f"Headers: {sanitized_headers}\n"
            f"Params: {sanitized_params}\n"
            f"Data: {sanitized_data}"
        )

        try:
            response_context: ResponseContext = self._handle_next(request)

            sanitized_response_data = sanitize_sensitive_data(response_context.data)
            self.logger.info(
                f"Response: {request.method} {request.url}\n"
                f"Status: {response_context.status_code}\n"
                f"Duration: {response_context.duration:.3f}s\n"
                f"Data: {sanitized_response_data}"
            )
            return response_context

        except RestClientError as e:
            self.logger.error(
                f"Error during request: {request.method} {request.url}\nError: {type(e).__name__}: {str(e)}"
            )
            if hasattr(e, "context"):
                self.logger.error(f"Error Context: {e.context}")
            raise


class RequestValidationBehavior(Behavior):
    """Behavior for validating request data only."""

    def __init__(self, request_data_schema: type[RequestT] | None = None, **kwargs):
        super().__init__(**kwargs)
        self.request_data_schema = request_data_schema

    def handle(self, request: RequestContext) -> ResponseContext:
        """Validate request data against Pydantic model."""
        if request.data is not None:
            # Use the request_data_schema from request context if available, otherwise fall back to instance type
            request_data_schema = (
                getattr(request, "request_data_schema", None) or self.request_data_schema
            )
            # Skip validation if request_data_schema is None or a TypeVar (not a concrete type)
            if request_data_schema is not None:
                from typing import TypeVar

                if not isinstance(request_data_schema, TypeVar):
                    try:
                        # Validate the outgoing request data
                        # Use TypeAdapter for generic types (e.g., list[Model]) or regular models
                        if get_origin(request_data_schema) is not None or not hasattr(
                            request_data_schema, "model_validate"
                        ):
                            # Generic type like list[Model] - use TypeAdapter
                            adapter = TypeAdapter(request_data_schema)
                            validated_data = adapter.validate_python(request.data)
                            request.data = adapter.dump_python(
                                validated_data, by_alias=True, mode="json"
                            )
                        else:
                            # Regular Pydantic model
                            validated_data = request_data_schema.model_validate(request.data)
                            request.data = validated_data.model_dump(by_alias=True, mode="json")
                    except Exception as e:
                        error_context = _create_error_context(request, e)
                        raise RequestValidationError(
                            f"Request validation failed: {str(e)}", context=error_context
                        )

        return self._handle_next(request)


class ResponseValidationBehavior(Behavior):
    """Behavior for validating response data only. This is outside the retry wrapper."""

    def __init__(self, response_data_schema: type[ResponseT] | None = None, **kwargs):
        super().__init__(**kwargs)
        self.response_data_schema = response_data_schema

    def handle(self, request: RequestContext) -> ResponseContext:
        """Validate response data against Pydantic model."""
        response = self._handle_next(request)

        # Only attempt to validate if there is response data and a response type is provided.
        # This correctly handles 204 No Content responses and TypeVar cases.
        if response.data is not None:
            # Use the response_data_schema from request context if available, otherwise fall back to instance type
            response_data_schema = (
                getattr(request, "response_data_schema", None) or self.response_data_schema
            )
            # Skip validation if response_data_schema is None or a TypeVar (not a concrete type)
            if response_data_schema is not None:
                from typing import TypeVar

                if not isinstance(response_data_schema, TypeVar):
                    try:
                        # Use TypeAdapter for generic types (e.g., list[Model]) or regular models
                        if get_origin(response_data_schema) is not None or not hasattr(
                            response_data_schema, "model_validate"
                        ):
                            # Generic type like list[Model] - use TypeAdapter
                            adapter = TypeAdapter(response_data_schema)
                            validated_response = adapter.validate_python(response.data)
                            response.data = adapter.dump_python(validated_response, by_alias=True)
                        else:
                            # Regular Pydantic model
                            validated_response = response_data_schema.model_validate(response.data)
                            response.data = validated_response.model_dump(by_alias=True)
                    except Exception as e:
                        error_context = _create_error_context(request, e, response)
                        raise ResponseValidationError(
                            f"Response validation failed: {str(e)}", context=error_context
                        )

        return response


class RetryBehavior(Behavior):
    """Behavior for retrying failed requests."""

    def __init__(
        self,
        max_retries: int,
        backoff_factor: float,
        retry_status_codes: Collection[int],
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.max_retries = max_retries
        self.backoff_factor = backoff_factor
        self.retry_status_codes = retry_status_codes

    def handle(self, request: RequestContext) -> ResponseContext:
        """Retry the request if it fails with a retryable error."""
        # Allow per-request override of max_retries and backoff_factor
        max_retries = request.context.get("max_retries") if request.context else None
        if max_retries is None:
            max_retries = self.max_retries

        backoff_factor = request.context.get("retry_backoff_factor") if request.context else None
        if backoff_factor is None:
            backoff_factor = self.backoff_factor

        retry_count = 0
        last_error = None

        while retry_count <= max_retries:
            try:
                response = self._handle_next(request)

                # Check if the status code indicates a retryable error
                # This happens before StatusCodeValidationBehavior raises exceptions
                # for unexpected status codes, so we can retry on specific status codes
                if response.status_code in self.retry_status_codes:
                    if retry_count >= max_retries:
                        # Max retries exceeded, raise an error
                        error_context = _create_error_context(
                            request, Exception("Max retries exceeded"), response
                        )
                        raise RetryableError(
                            f"Max retries ({max_retries}) exceeded for status code {response.status_code}",
                            context=error_context,
                        )
                    # Increment retry count and wait before retrying
                    retry_count += 1
                    wait_time = backoff_factor * (2 ** (retry_count - 1))
                    time.sleep(wait_time)
                    continue

                # Success - return the response
                return response

            except (
                CustomConnectionError,
                TimeoutError,
                ServerError,
                RateLimitError,
            ) as e:
                # These are retryable exceptions
                last_error = e
                if retry_count >= max_retries:
                    # Max retries exceeded, re-raise the last error
                    break
                retry_count += 1
                wait_time = backoff_factor * (2 ** (retry_count - 1))
                time.sleep(wait_time)
                continue

        # If we get here, we've exhausted retries
        if last_error:
            raise last_error
        error_context = _create_error_context(request, Exception("Max retries exceeded"))
        raise RetryableError(f"Max retries ({max_retries}) exceeded", context=error_context)


class IdempotencyHeaderBehavior(Behavior):
    """Behavior for adding idempotency headers to POST/PUT/DELETE requests."""

    def handle(self, request: RequestContext) -> ResponseContext:
        """Add X-Idempotency-Key header for POST/PUT/PATCH/DELETE requests if not present."""
        if (
            request.method in ["POST", "PUT", "PATCH", "DELETE"]
            and "X-Idempotency-Key" not in request.headers
        ):
            request.headers["X-Idempotency-Key"] = str(uuid.uuid4())

        return self._handle_next(request)


class HttpBehavior(Behavior):
    """Behavior for making the actual HTTP request."""

    def __init__(self, session: httpx.Client, timeout: int):
        super().__init__(None)  # This is always the last behavior
        self.session = session
        self.timeout = timeout

    def handle(self, request: RequestContext) -> ResponseContext:
        """Make the HTTP request and return a response context."""
        try:
            response = self.session.request(
                method=request.method,
                url=request.url,
                json=request.data,
                params=request.params,
                headers=request.headers,
                timeout=self.timeout,
            )

            response_data: dict[str, Any] | None = None
            if response.content:
                try:
                    response_data = response.json()
                except json.JSONDecodeError:
                    response_data = {"raw_content": response.text}

            return ResponseContext(
                status_code=response.status_code,
                headers=dict(response.headers),
                data=response_data,
                request=request,
            )
        except httpx.TimeoutException as e:
            error_context = _create_error_context(request, e)
            raise TimeoutError(f"Request timeout: {str(e)}", context=error_context)
        except httpx.ConnectError as e:
            error_context = _create_error_context(request, e)
            raise CustomConnectionError(f"Connection error: {str(e)}", context=error_context)
        except httpx.RequestError as e:
            error_context = _create_error_context(request, e)
            raise RequestError(f"Request failed: {str(e)}", context=error_context)

    def _handle_next(self, request: RequestContext) -> ResponseContext:
        # HttpBehavior is the end of the line, it should not call next.
        raise NotImplementedError("HttpBehavior should be the last in the chain.")


class BulkheadBehavior(Behavior):
    """Behavior implementing the bulkhead (concurrency isolation) pattern.

    It acquires a slot from a shared, injected :class:`Bulkhead` before letting
    the request proceed, and releases it afterwards. If no slot is available it
    raises :class:`BulkheadFullError` without calling the rest of the pipeline.

    This is placed *outside* the circuit breaker in the pipeline so that a full
    bulkhead (local overload) is never counted as a downstream failure by the
    breaker, and so requests that fail earlier (e.g. request validation) do not
    consume a concurrency slot.
    """

    def __init__(self, bulkhead: Bulkhead, **kwargs):
        super().__init__(**kwargs)
        self.bulkhead = bulkhead

    def handle(self, request: RequestContext) -> ResponseContext:
        """Acquire a concurrency slot, run the request, then release the slot."""
        if not self.bulkhead.try_acquire():
            error_context = _create_error_context(request, Exception("Bulkhead is full"))
            raise BulkheadFullError(
                f"Bulkhead for '{self.bulkhead.service_name}' is full "
                f"(max {self.bulkhead.max_concurrent} concurrent requests)",
                context=error_context,
            )
        try:
            return self._handle_next(request)
        finally:
            self.bulkhead.release()


class CircuitBreakerBehavior(Behavior):
    """
    Behavior for implementing the circuit breaker pattern.
    It uses a shared, injected CircuitBreaker instance.
    """

    def __init__(self, breaker: CircuitBreaker, **kwargs):
        super().__init__(**kwargs)
        self.breaker = breaker

    def handle(self, request: RequestContext) -> ResponseContext:
        """Wrap the request pipeline in a circuit breaker."""
        try:
            # The breaker will call the next behavior in the chain.
            return self.breaker.call(self._handle_next, request)
        except PybreakerError as e:
            # If the breaker is open, catch the error from pybreaker
            # and raise our own application-specific exception.
            error_context = _create_error_context(request, e)
            raise CustomCircuitBreakerOpenError(
                f"Circuit is open for {request.url}. Not sending request.", context=error_context
            )
        except RetryableError:
            # If a downstream error occurs that we know is a system failure
            # (like ConnectionError, TimeoutError), pybreaker will have
            # automatically counted it as a failure. We just re-raise it so
            # the RetryBehavior can catch it.
            raise


class StatusCodeValidationBehavior(Behavior):
    """Behavior for validating the HTTP status code of a response."""

    def handle(self, request: RequestContext) -> ResponseContext:
        """Validate the status code and raise specific errors for common issues."""
        response = self._handle_next(request)

        status_code = response.status_code

        # Check for specific error codes
        if status_code == 401:
            error_context = _create_error_context(
                request, Exception("Authentication failed"), response
            )
            raise AuthenticationError("Authentication failed (401)", context=error_context)
        if status_code == 403:
            error_context = _create_error_context(
                request, Exception("Authorization failed"), response
            )
            raise AuthorizationError("Authorization failed (403)", context=error_context)
        if status_code == 404:
            error_context = _create_error_context(
                request, Exception("Resource not found"), response
            )
            raise ResourceNotFoundError("Resource not found (404)", context=error_context)
        if status_code == 429:
            error_context = _create_error_context(
                request, Exception("Rate limit exceeded"), response
            )
            raise RateLimitError("Rate limit exceeded (429)", context=error_context)

        # Check for 5xx server errors
        if 500 <= status_code < 600:
            error_context = _create_error_context(request, Exception("Server error"), response)
            raise ServerError(f"Server error ({status_code})", context=error_context)

        # Check for other 4xx client errors
        if 400 <= status_code < 500:
            error_context = _create_error_context(request, Exception("Client error"), response)
            raise StatusCodeError(f"Client error ({status_code})", context=error_context)

        # 2xx and 3xx are considered successful (3xx redirects are typically handled by HTTP library)
        # 1xx informational responses are also passed through
        return response


class InterceptorBehavior(Behavior):
    """
    A behavior that executes a list of user-defined interceptors.

    This acts as a bridge between the client's internal behavior pipeline
    and external, user-provided logic.
    """

    def __init__(self, interceptors: list[Interceptor], **kwargs):
        super().__init__(**kwargs)
        self.interceptors = interceptors

    def handle(self, request: RequestContext) -> ResponseContext:
        """
        Executes interceptors before the request, after the response, and on error.
        """
        # --- Run 'on_before_request' for all interceptors ---
        for interceptor in self.interceptors:
            interceptor.on_before_request(request)

        try:
            response = self._handle_next(request)
            # --- Run 'on_after_response' for all interceptors on success ---
            for interceptor in self.interceptors:
                interceptor.on_after_response(response)
            return response
        except RestClientError as e:
            # --- Run 'on_error' for all interceptors on failure ---
            for interceptor in self.interceptors:
                interceptor.on_error(e)
            raise
