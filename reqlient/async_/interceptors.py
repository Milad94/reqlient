from abc import ABC

from ..core.errors import RestClientError
from ..core.request_response import RequestContext, ResponseContext


class AsyncInterceptor(ABC):
    """
    Abstract base class for async request/response interceptors.

    Interceptors allow for custom logic to be injected into the request/response
    lifecycle, enabling cross-cutting concerns like dynamic header injection,
    specialized logging, or request/response transformations.
    """

    async def on_before_request(self, request: RequestContext):
        """
        Called before the request is passed down the behavior pipeline.

        This method can be used to modify the RequestContext before it is processed
        by other behaviors like signing, caching, or retrying.

        Args:
            request: The outgoing request context.
        """

    async def on_after_response(self, response: ResponseContext):
        """
        Called after a successful response is received from the pipeline.

        This method can be used to inspect or modify the ResponseContext.

        Args:
            response: The incoming response context.
        """

    async def on_error(self, error: RestClientError):
        """
        Called when an error occurs at any point during the request pipeline.

        Args:
            error: The exception that was raised.
        """


class TraceContextInterceptor(AsyncInterceptor):
    """Injects X-Request-ID and W3C traceparent/tracestate into outbound requests."""

    async def on_before_request(self, request: RequestContext):
        try:
            from felesh_shared_package.scaffold.common.seedwork.api.metadata import get_request_id
            request_id = get_request_id()
            if request_id:
                request.headers["X-Request-ID"] = request_id
        except ImportError:
            pass
        try:
            from opentelemetry import propagate
            propagate.inject(request.headers)
        except ImportError:
            pass
