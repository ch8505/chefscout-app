"""Download recipe pages safely.

- http(s) only, public addresses only (no localhost / home network: the model chooses the URLs).
- Every redirect hop is checked too.
- Size and time limits.
- Uses the Windows certificate store (truststore), so filters such as NetFree that install their own
  certificate keep working.
"""

from __future__ import annotations

import ipaddress
import logging
import socket
import ssl
from urllib.parse import urlparse

import httpx

from chefscout.core.errors import ToolError

log = logging.getLogger(__name__)

MAX_BYTES = 3_000_000
TIMEOUT = httpx.Timeout(10.0, connect=5.0)
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; ChefScout/0.1; personal cooking assistant)",
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "he-IL,he;q=0.9,en;q=0.5",
}


class UnsafeUrl(ToolError):
    """The URL points somewhere we must not go."""


def check_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise UnsafeUrl(f"not an http(s) url: {url}")
    try:
        infos = socket.getaddrinfo(parsed.hostname, parsed.port or 443, proto=socket.IPPROTO_TCP)
    except socket.gaierror as e:
        raise ToolError(f"cannot resolve {parsed.hostname}") from e
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global:
            raise UnsafeUrl(f"refusing non-public address for {parsed.hostname}")


def _ssl_context() -> ssl.SSLContext | bool:
    try:
        import truststore

        return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    except ImportError:  # pragma: no cover
        return True


class PageFetcher:
    def __init__(self) -> None:
        self._client = httpx.Client(
            headers=HEADERS,
            timeout=TIMEOUT,
            follow_redirects=True,
            max_redirects=5,
            verify=_ssl_context(),
            event_hooks={"request": [lambda request: check_url(str(request.url))]},
        )

    def get(self, url: str) -> str:
        check_url(url)
        try:
            with self._client.stream("GET", url) as resp:
                if resp.status_code >= 400:
                    raise ToolError(f"site answered {resp.status_code}")
                chunks, size = [], 0
                for chunk in resp.iter_bytes():
                    size += len(chunk)
                    if size > MAX_BYTES:
                        raise ToolError("page too large")
                    chunks.append(chunk)
                encoding = resp.encoding or "utf-8"
        except httpx.HTTPError as e:
            raise ToolError(f"could not load page ({type(e).__name__})") from e
        return b"".join(chunks).decode(encoding, errors="replace")

    def close(self) -> None:
        self._client.close()
