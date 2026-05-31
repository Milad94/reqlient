from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, TypeVar

from pydantic import BaseModel

RequestT = TypeVar("RequestT", bound=BaseModel)
ResponseT = TypeVar("ResponseT", bound=BaseModel)


@dataclass
class RequestContext:
    """Context for request processing."""

    method: str
    url: str
    headers: dict[str, str]
    params: dict[str, str] | None
    data: dict[str, Any] | None
    start_time: datetime = field(default_factory=datetime.now)
    context: dict[str, Any] = field(default_factory=dict)
    request_data_schema: Any | None = None  # Type[RequestT] for validation
    response_data_schema: Any | None = None  # Type[ResponseT] for validation


@dataclass
class ResponseContext:
    """Context for response processing."""

    status_code: int
    headers: dict[str, str]
    data: dict[str, Any] | None
    request: RequestContext
    end_time: datetime = field(default_factory=datetime.now)

    @property
    def duration(self) -> float:
        """Calculate the duration of the request in seconds."""
        return (self.end_time - self.request.start_time).total_seconds()

    def to_dict(self) -> dict[str, Any]:
        """Convert the context to a dictionary, excluding the request object."""
        return {
            "status_code": self.status_code,
            "headers": dict(self.headers),
            "data": self.data,
            "end_time": self.end_time.isoformat(),
        }
