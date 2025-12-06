"""Synchronous REST client modules."""

from .circuit_breakers import create_shared_breaker
from .interceptors import Interceptor
from .rest_client import RestClient

__all__ = [
    "RestClient",
    "create_shared_breaker",
    "Interceptor",
]
