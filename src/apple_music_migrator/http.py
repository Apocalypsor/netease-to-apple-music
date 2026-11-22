from __future__ import annotations

import random
import re
import sys
import time
from typing import Any

import requests


class RemoteError(RuntimeError):
    """Raised when a remote API returns an unusable response."""


class RateLimitError(RemoteError):
    """Raised after a remote API keeps returning HTTP 429."""

    def __init__(self, message: str, retry_after: float) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class HttpClient:
    _RETRYABLE_STATUS = {500, 502, 503, 504}

    def __init__(
        self,
        *,
        timeout: float,
        max_retries: int,
        user_agent: str,
        min_interval: float = 0.0,
    ) -> None:
        self.timeout = timeout
        self.max_retries = max_retries
        self.min_interval = max(0.0, min_interval)
        self._last_request_at: float | None = None
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": user_agent})

    def request(self, method: str, url: str, **kwargs: Any) -> requests.Response:
        for attempt in range(self.max_retries + 1):
            self._wait_for_request_slot()
            try:
                response = self.session.request(method, url, timeout=self.timeout, **kwargs)
            except requests.RequestException as exc:
                if attempt >= self.max_retries:
                    raise RemoteError(f"网络请求失败：{type(exc).__name__}") from exc
                _sleep_backoff(attempt)
                continue

            if response.status_code == 429:
                delay = _retry_after_seconds(response, attempt)
                if attempt >= self.max_retries:
                    raise RateLimitError(_response_error(response, url), delay)
                print(
                    f"远端限流，等待 {delay:.0f} 秒后重试 "
                    f"({attempt + 1}/{self.max_retries})...",
                    file=sys.stderr,
                )
                time.sleep(delay)
                continue

            if response.status_code not in self._RETRYABLE_STATUS:
                return response
            if attempt >= self.max_retries:
                return response
            _sleep_backoff(attempt)

        raise AssertionError("unreachable")

    def _wait_for_request_slot(self) -> None:
        if self.min_interval <= 0:
            return
        now = time.monotonic()
        if self._last_request_at is not None:
            wait = self.min_interval - (now - self._last_request_at)
            if wait > 0:
                time.sleep(wait)
        self._last_request_at = time.monotonic()

    def get_json(self, url: str, **kwargs: Any) -> dict[str, Any]:
        response = self.request("GET", url, **kwargs)
        if response.status_code >= 400:
            raise RemoteError(_response_error(response, url))
        try:
            data = response.json()
        except ValueError as exc:
            raise RemoteError(f"接口返回不是 JSON：{url}") from exc
        if not isinstance(data, dict):
            raise RemoteError(f"接口返回格式不正确：{url}")
        return data

    def post_json(self, url: str, payload: dict[str, Any], **kwargs: Any) -> None:
        response = self.request("POST", url, json=payload, **kwargs)
        if response.status_code not in {200, 201, 202, 204}:
            raise RemoteError(_response_error(response, url))


def _sleep_backoff(attempt: int) -> None:
    time.sleep(min(30.0, 1.5 * (2**attempt)) + random.uniform(0.0, 0.25))


def _retry_after_seconds(response: requests.Response, attempt: int) -> float:
    value = response.headers.get("Retry-After")
    try:
        if value and float(value) > 0:
            return min(float(value), 900.0)
    except ValueError:
        pass
    return min(900.0, 30.0 * (2**attempt))


def _response_error(response: requests.Response, url: str) -> str:
    detail = re.sub(r"\s+", " ", response.text[:300]).strip()
    detail = re.sub(r"(?i)(bearer\s+)[A-Za-z0-9._~-]+", r"\1<redacted>", detail)
    detail = re.sub(r"(?i)(media-user-token[=:]\s*)[^,;\s]+", r"\1<redacted>", detail)
    return f"请求失败 {response.status_code}：{url} ({detail})"
