"""Synchronous REST client modules."""

from .circuit_breakers import CircuitBreakerRegistry
from .interceptors import Interceptor
from .rest_client import RestClient

__all__ = [
    "RestClient",
    "CircuitBreakerRegistry",
    "Interceptor",
]
