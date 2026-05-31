from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any

from pydantic import BaseModel


def parse_retry_after(headers: dict[str, str] | None) -> float | None:
    """Parse the ``Retry-After`` header into a delay in seconds.

    Per RFC 9110 the value is either a non-negative integer number of seconds or
    an HTTP-date. Returns the delay in seconds (clamped to ``>= 0``), or ``None``
    if the header is absent or cannot be parsed.
    """
    if not headers:
        return None

    # Header names may arrive with any casing depending on the HTTP layer.
    value: str | None = None
    for k, v in headers.items():
        if k.lower() == "retry-after":
            value = v
            break
    if value is None:
        return None

    value = value.strip()
    # delta-seconds form
    try:
        return max(0.0, float(int(value)))
    except ValueError:
        pass

    # HTTP-date form
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if when is None:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    delta = (when - datetime.now(UTC)).total_seconds()
    return max(0.0, delta)


def sanitize_sensitive_data(data: Any, sensitive_fields: set[str] | None = None) -> Any:
    """Recursively sanitizes sensitive data in nested dictionaries, lists, and Pydantic models.

    It replaces the values of fields matching a predefined list of sensitive keys with '********'.
    The list of sensitive keys can be extended by the caller.

    Args:
        data: The data structure (dict, list, Pydantic model) to sanitize.
        sensitive_fields: An optional set of strings representing keys to sanitize.
                          If not provided, a default set of common sensitive keys is used.

    Returns:
        The sanitized data structure.
    """
    if sensitive_fields is None:
        sensitive_fields = {
            "password",
            "token",
            "secret",
            "key",
            "authorization",
            "api_key",
            "apikey",
            "access_token",
            "refresh_token",
            "credit_card",
            "card_number",
            "cvv",
            "ssn",
            "social_security",
        }

    if isinstance(data, dict):
        return {
            k: "********"
            if k.lower() in sensitive_fields
            else sanitize_sensitive_data(v, sensitive_fields)
            for k, v in data.items()
        }
    elif isinstance(data, list):
        return [sanitize_sensitive_data(item, sensitive_fields) for item in data]
    elif isinstance(data, BaseModel):
        return sanitize_sensitive_data(data.model_dump(), sensitive_fields)
    return data
