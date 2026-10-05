"""Shared HTTP for collectors: one polite user agent, retries on transient failures, and per-service credentials."""

import os
import shutil
import subprocess
import time
from functools import cache

import httpx

UA = "signal-monitor/0.1 (private-company research)"
RETRY_STATUS = {429, 500, 502, 503, 504}
MAX_WAIT = 900  # never sleep longer than 15 minutes on one rate-limit reply
SEC_MIN_INTERVAL = 0.12  # SEC fair access: at most 10 requests a second
_last_sec_call = 0.0


class SourceUnavailable(Exception):
    """The source can't run here (e.g. a missing API key). collect reports it as skipped, not as a failure."""


def rate_limit_wait(r: httpx.Response) -> float | None:
    """Seconds to wait when the server says we're rate limited: 429, or GitHub's 403 carrying rate-limit headers."""
    if r.status_code not in (403, 429):
        return None
    if (after := r.headers.get("Retry-After", "").strip()).isdigit():
        return float(after)
    if r.headers.get("x-ratelimit-remaining") == "0" and (reset := r.headers.get("x-ratelimit-reset", "")).isdigit():
        return max(1.0, float(reset) - time.time())
    if r.status_code == 403 and "secondary rate limit" in r.text:  # GitHub sends no header; its docs say wait minutes
        return 120.0
    return 30.0 if r.status_code == 429 else None  # a plain 403 is a real refusal: don't retry it


def request(method: str, url: str, *, attempts: int = 4, timeout: float = 60, headers=None, **kw) -> httpx.Response:
    """httpx request with our user agent, retrying rate limits, 5xx and dropped connections with backoff."""
    headers = {"User-Agent": UA, **(headers or {})}
    for attempt in range(attempts):
        last = attempt == attempts - 1
        try:
            r = httpx.request(method, url, headers=headers, timeout=timeout, follow_redirects=True, **kw)
        except httpx.TransportError:
            if last:
                raise
        else:
            wait = rate_limit_wait(r)
            if (wait is None and r.status_code not in RETRY_STATUS) or last:
                r.raise_for_status()
                return r
            if wait is not None:
                time.sleep(min(wait, MAX_WAIT))
                continue
        time.sleep(min(2**attempt, 30))
    raise AssertionError("unreachable")


def get(url: str, **kw) -> httpx.Response:
    return request("GET", url, **kw)


def post(url: str, **kw) -> httpx.Response:
    return request("POST", url, **kw)


def env_key(name: str, why: str) -> str:
    """An API key from the environment, or SourceUnavailable naming what it unlocks."""
    if value := os.environ.get(name, "").strip():
        return value
    raise SourceUnavailable(f"set {name} to enable this source ({why})")


@cache
def github_token() -> str | None:
    """GITHUB_TOKEN if set (CI provides one), else the local `gh auth` login, else None (unauthenticated)."""
    if token := os.environ.get("GITHUB_TOKEN", "").strip():
        return token
    if shutil.which("gh"):
        out = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, check=False)
        return out.stdout.strip() or None
    return None


def github_get(url: str, **kw) -> httpx.Response:
    token = github_token()
    auth = {"Authorization": f"Bearer {token}"} if token else {}
    return get(url, headers={"Accept": "application/vnd.github+json", **auth, **kw.pop("headers", {})}, **kw)


def sec_get(url: str, **kw) -> httpx.Response:
    """SEC EDGAR requires a User-Agent naming a person and contact email, and at most 10 requests a second."""
    global _last_sec_call
    agent = env_key("SEC_USER_AGENT", 'SEC EDGAR fair access, e.g. "Jane Doe jane@example.com"')
    time.sleep(max(0.0, _last_sec_call + SEC_MIN_INTERVAL - time.monotonic()))
    _last_sec_call = time.monotonic()
    return get(url, headers={"User-Agent": agent, **kw.pop("headers", {})}, **kw)
