"""Tests for core utility helpers."""

from datetime import UTC, datetime, timedelta
from email.utils import format_datetime

from reqlient.core.utils import parse_retry_after, sanitize_sensitive_data


class TestParseRetryAfter:
    """Tests for the Retry-After header parser."""

    def test_none_headers(self):
        assert parse_retry_after(None) is None

    def test_missing_header(self):
        assert parse_retry_after({"Content-Type": "application/json"}) is None

    def test_delta_seconds(self):
        assert parse_retry_after({"Retry-After": "120"}) == 120.0

    def test_case_insensitive_header_name(self):
        assert parse_retry_after({"retry-after": "5"}) == 5.0

    def test_negative_seconds_clamped_to_zero(self):
        assert parse_retry_after({"Retry-After": "-10"}) == 0.0

    def test_http_date_in_future(self):
        future = datetime.now(UTC) + timedelta(seconds=30)
        delay = parse_retry_after({"Retry-After": format_datetime(future)})
        assert delay is not None
        # Allow a little slack for execution time.
        assert 25 <= delay <= 31

    def test_http_date_in_past_clamped_to_zero(self):
        past = datetime.now(UTC) - timedelta(seconds=30)
        assert parse_retry_after({"Retry-After": format_datetime(past)}) == 0.0

    def test_garbage_value_returns_none(self):
        assert parse_retry_after({"Retry-After": "not-a-date"}) is None


class TestSanitizeSensitiveData:
    """Smoke tests confirming sanitization still works alongside the new helper."""

    def test_redacts_known_sensitive_keys(self):
        out = sanitize_sensitive_data({"password": "hunter2", "name": "ok"})
        assert out["password"] == "********"
        assert out["name"] == "ok"
