"""Two blunt limits on what one client can make this process do.

Neither is a security control on its own, and neither pretends to be. They are
here because the two ways to hurt this service are both cheap for a caller and
expensive for the server:

* **A very large body.** Every upload is already size-checked per file, but that
  check happens after the request has been read into memory, which is exactly
  the wrong order. `MaxBodySizeMiddleware` refuses on the declared
  `Content-Length` before any of it is read.
* **A flood of model calls.** Extraction and screening cost money and take
  seconds. `RateLimit` caps how many a single client can start per minute.

## What these limits are not

The rate limiter keeps its counters **in this process's memory**. Two workers
means two independent allowances, and a restart forgets everything. It is a
brake on accidental hammering and on a trivially cheap flood — nothing more. A
deployment that needs a real limit needs one in front of the application, in a
reverse proxy or an API gateway, where it can see every worker's traffic.

The client is identified by `X-Forwarded-For` (first hop) or the socket peer.
Both are spoofable by anyone who wants to be. That is acceptable for what this
is; it is not acceptable as the only thing standing between an attacker and a
cost, which is why the limit exists alongside — not instead of — the provider's
own quota.

The address is used for counting and nothing else. It is never stored, never
logged, and never attached to a candidate or a job.
"""

from __future__ import annotations

import time
from collections import defaultdict, deque
from collections.abc import Callable

from fastapi import Request, status
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
from starlette.types import ASGIApp

#: Requests without a body that could plausibly be large still pass through the
#: middleware; only a declared length above the cap is refused.
_METHODS_WITH_BODIES = frozenset({"POST", "PUT", "PATCH"})


class MaxBodySizeMiddleware(BaseHTTPMiddleware):
    """Refuse an oversized request before reading it.

    Starlette will happily buffer whatever it is sent. The per-file upload limit
    cannot help with that: it runs after `await request.form()`, by which point
    the bytes are already in memory. Checking the declared `Content-Length`
    first turns "the server allocates 2 GB" into "the server writes one 413".

    A client that lies about `Content-Length`, or omits it and streams, is not
    caught here — the per-file limit still catches the upload case, and a real
    deployment puts a proxy in front with its own cap.
    """

    def __init__(self, app: ASGIApp, max_bytes: int) -> None:
        super().__init__(app)
        self._max_bytes = max_bytes

    async def dispatch(self, request: Request, call_next: Callable):
        if request.method in _METHODS_WITH_BODIES:
            declared = request.headers.get("content-length")
            if declared is not None and declared.isdigit() and int(declared) > self._max_bytes:
                megabytes = self._max_bytes / (1024 * 1024)
                return JSONResponse(
                    status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                    content={
                        "code": "request_too_large",
                        "message": (
                            f"This request is larger than the {megabytes:.0f} MB limit. "
                            "Upload fewer CVs at once, or split the batch."
                        ),
                        "details": {"max_bytes": self._max_bytes},
                    },
                )
        return await call_next(request)


def client_key(request: Request) -> str:
    """Who to count this request against. Best effort, and only for counting."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


class RateLimit:
    """A per-client sliding-window cap, usable as a FastAPI dependency.

    One instance per bucket, so the expensive endpoints can be limited harder
    than the cheap ones without one starving the other:

        expensive = RateLimit("model", per_minute=10)

        @router.post("/thing", dependencies=[Depends(expensive)])

    A sliding window rather than a fixed one because a fixed window lets a
    client spend its whole allowance in the last second of one window and again
    in the first second of the next — twice the intended rate, at exactly the
    moment a retry storm would produce it.
    """

    def __init__(self, name: str, per_minute: int) -> None:
        self.name = name
        self.per_minute = per_minute
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def reset(self) -> None:
        """Forget every counter. For tests, and for nothing else."""
        self._hits.clear()

    async def __call__(self, request: Request) -> None:
        settings = request.app.state.settings
        limit = self.per_minute if self.per_minute > 0 else settings.rate_limit_per_minute
        if not settings.rate_limit_enabled or limit <= 0:
            return

        now = time.monotonic()
        window = self._hits[client_key(request)]
        while window and now - window[0] >= 60.0:
            window.popleft()

        if len(window) >= limit:
            retry_after = max(1, int(60.0 - (now - window[0])) + 1)
            raise RateLimitedError(limit=limit, retry_after_seconds=retry_after)

        window.append(now)


class RateLimitedError(Exception):
    """Raised by `RateLimit`; converted to a 429 by the handler in `core.errors`."""

    def __init__(self, *, limit: int, retry_after_seconds: int) -> None:
        super().__init__(f"rate limit of {limit}/minute exceeded")
        self.limit = limit
        self.retry_after_seconds = retry_after_seconds


#: The model-backed endpoints: requirement extraction, profile extraction and
#: matching. Each one is a paid call that takes seconds, so this is the bucket
#: that actually matters.
model_calls = RateLimit("model", per_minute=0)

#: Uploads. Cheaper than a model call but still writes files to disk.
uploads = RateLimit("upload", per_minute=0)


def reset_all_rate_limits() -> None:
    """Clear every bucket. Used by the test suite between cases."""
    model_calls.reset()
    uploads.reset()


__all__ = [
    "MaxBodySizeMiddleware",
    "RateLimit",
    "RateLimitedError",
    "client_key",
    "model_calls",
    "reset_all_rate_limits",
    "uploads",
]
