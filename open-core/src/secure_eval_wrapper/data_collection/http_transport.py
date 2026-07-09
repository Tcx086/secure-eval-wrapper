"""Small injectable HTTP boundary for public market-data adapters.

The transport contains no exchange credentials, authentication signing, retries, or import-time
network behavior.  Provider unit tests can inject an in-memory implementation of ``HttpTransport``
instead of opening a socket.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


class TransportError(RuntimeError):
    """Raised when an HTTP request cannot be completed at the transport boundary."""


@dataclass(frozen=True)
class HttpRequest:
    """Provider-built HTTP request passed to an injectable transport."""

    method: str
    url: str
    query_params: Mapping[str, str | int] = field(default_factory=dict)
    timeout: float = 10.0
    headers: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.method, str) or not self.method.strip():
            raise ValueError("HTTP method must be a non-empty string")
        if not isinstance(self.url, str) or not self.url.strip():
            raise ValueError("HTTP URL must be a non-empty string")
        if (
            isinstance(self.timeout, bool)
            or not isinstance(self.timeout, (int, float))
            or self.timeout <= 0
        ):
            raise ValueError("HTTP timeout must be positive")
        object.__setattr__(self, "method", self.method.strip().upper())


@dataclass(frozen=True)
class HttpResponse:
    """Transport response retaining status, raw bytes, decoded text, and headers."""

    status: int
    body_bytes: bytes
    headers: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if isinstance(self.status, bool) or not isinstance(self.status, int):
            raise TypeError("HTTP response status must be an integer")
        if not isinstance(self.body_bytes, bytes):
            raise TypeError("HTTP response body_bytes must be bytes")

    @property
    def body_text(self) -> str:
        """Decode the response body as UTF-8 text."""

        return self.body_bytes.decode("utf-8")


class HttpTransport(Protocol):
    """Minimal synchronous HTTP transport protocol used by provider adapters."""

    def send(self, request: HttpRequest) -> HttpResponse:
        """Send one request and return its complete response."""


class UrlLibHttpTransport:
    """Standard-library transport for explicitly enabled public-network use."""

    def send(self, request: HttpRequest) -> HttpResponse:
        """Send a request with ``urllib`` and retain non-success HTTP responses."""

        query = urlencode(tuple(request.query_params.items()))
        separator = "&" if "?" in request.url else "?"
        url = request.url if not query else f"{request.url}{separator}{query}"
        urllib_request = Request(
            url,
            method=request.method,
            headers=dict(request.headers),
        )
        try:
            with urlopen(urllib_request, timeout=request.timeout) as response:
                return HttpResponse(
                    status=response.status,
                    body_bytes=response.read(),
                    headers=dict(response.headers.items()),
                )
        except HTTPError as exc:
            return HttpResponse(
                status=exc.code,
                body_bytes=exc.read(),
                headers=dict(exc.headers.items()) if exc.headers is not None else {},
            )
        except (URLError, OSError) as exc:
            raise TransportError(f"public HTTP request failed: {exc}") from exc


__all__ = [
    "HttpRequest",
    "HttpResponse",
    "HttpTransport",
    "TransportError",
    "UrlLibHttpTransport",
]
