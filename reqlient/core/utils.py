from typing import Any

from pydantic import BaseModel


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
