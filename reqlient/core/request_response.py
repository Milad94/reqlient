from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Optional, TypeVar

from pydantic import BaseModel

RequestT = TypeVar("RequestT", bound=BaseModel)
ResponseT = TypeVar("ResponseT", bound=BaseModel)


@dataclass
class RequestContext:
    """Context for request processing."""

    method: str
    url: str
    headers: Dict[str, str]
    params: Optional[Dict[str, str]]
    data: Optional[Dict[str, Any]]
    start_time: datetime = field(default_factory=datetime.now)
    context: Dict[str, Any] = field(default_factory=dict)
    request_data_schema: Optional[Any] = None  # Type[RequestT] for validation
    response_data_schema: Optional[Any] = None  # Type[ResponseT] for validation


@dataclass
class ResponseContext:
    """Context for response processing."""

    status_code: int
    headers: Dict[str, str]
    data: Optional[Dict[str, Any]]
    request: RequestContext
    end_time: datetime = field(default_factory=datetime.now)

    @property
    def duration(self) -> float:
        """Calculate the duration of the request in seconds."""
        return (self.end_time - self.request.start_time).total_seconds()

    def to_dict(self) -> Dict[str, Any]:
        """Convert the context to a dictionary, excluding the request object."""
        return {
            "status_code": self.status_code,
            "headers": dict(self.headers),
            "data": self.data,
            "end_time": self.end_time.isoformat(),
        }
