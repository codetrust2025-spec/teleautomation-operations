"""Compressed and cacheable responses for the dashboard (12 Oct).

Nothing compressed anything: nginx proxies to this app with `gzip_proxied`
off, and the app sent every byte as it was. Each dashboard open downloaded
686 KB of JavaScript and 576 KB of CSS, uncompressed and with no caching
headers, and every list came back as raw JSON. On the office links that was
most of the wait for a first load.

* Text the browser asks to have gzipped (JSON, JavaScript, CSS, HTML, SVG,
  plain text) of 1 KB or more is gzipped. Files people download -- resumes,
  proof images, PDFs, CSV exports -- are passed through untouched, as are
  partial (Range) responses and anything already encoded.
* `/assets/` files carry a content hash in their name, so they are cached for
  a year; a new deploy names new files, and `index.html` stays `no-cache`, so
  a release is still picked up on the next load.

Plain ASGI, so it holds no opinion about the framework version underneath.
"""
from __future__ import annotations

import gzip
from collections import OrderedDict

COMPRESSIBLE_TYPES = (
    "application/json",
    "application/javascript",
    "text/javascript",
    "text/css",
    "text/html",
    "text/plain",
    "image/svg+xml",
)
MINIMUM_SIZE = 1024
ASSET_CACHE_CONTROL = b"public, max-age=31536000, immutable"
_ASSET_CACHE_LIMIT = 32


def _header(headers: list, name: bytes) -> bytes | None:
    for key, value in headers:
        if key.lower() == name:
            return value
    return None


def _accepts_gzip(scope) -> bool:
    for key, value in scope.get("headers") or []:
        if key.lower() == b"accept-encoding":
            return b"gzip" in value.lower()
    return False


class CompressionMiddleware:
    def __init__(self, app, *, minimum_size: int = MINIMUM_SIZE, level: int = 6):
        self.app = app
        self.minimum_size = minimum_size
        self.level = level
        # Compressed asset bodies by path and ETag: the same few files are sent
        # to every browser after a deploy, so each is compressed once.
        self._assets: OrderedDict[tuple[str, bytes], bytes] = OrderedDict()

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        path = scope.get("path") or ""
        is_asset = path.startswith("/assets/")
        wants_gzip = _accepts_gzip(scope)
        start: dict | None = None
        chunks: list[bytes] = []
        compressing = False

        async def wrapped_send(message):
            nonlocal start, compressing
            if message["type"] == "http.response.start":
                headers = list(message.get("headers") or [])
                status = message.get("status", 200)
                if is_asset and status == 200 and _header(headers, b"cache-control") is None:
                    headers.append((b"cache-control", ASSET_CACHE_CONTROL))
                content_type = (_header(headers, b"content-type") or b"").decode("latin-1").lower()
                compressing = (
                    wants_gzip
                    and status == 200
                    and _header(headers, b"content-encoding") is None
                    and _header(headers, b"content-range") is None
                    and content_type.startswith(COMPRESSIBLE_TYPES)
                )
                start = {**message, "headers": headers}
                if not compressing:
                    await send(start)
                return
            if message["type"] != "http.response.body" or not compressing:
                await send(message)
                return
            chunks.append(message.get("body", b""))
            if message.get("more_body", False):
                return
            await self._send_compressed(start, b"".join(chunks), path if is_asset else "", send)

        await self.app(scope, receive, wrapped_send)

    async def _send_compressed(self, start: dict, body: bytes, asset_path: str, send) -> None:
        headers = [(k, v) for k, v in start["headers"] if k.lower() != b"content-length"]
        if len(body) < self.minimum_size:
            await send({**start, "headers": headers + [(b"content-length", str(len(body)).encode())]})
            await send({"type": "http.response.body", "body": body})
            return
        compressed = None
        key = None
        etag = _header(headers, b"etag")
        if asset_path and etag:
            key = (asset_path, etag)
            compressed = self._assets.get(key)
        if compressed is None:
            compressed = gzip.compress(body, compresslevel=self.level, mtime=0)
            if key is not None:
                self._assets[key] = compressed
                while len(self._assets) > _ASSET_CACHE_LIMIT:
                    self._assets.popitem(last=False)
        vary = _header(headers, b"vary")
        headers = [(k, v) for k, v in headers if k.lower() != b"vary"]
        vary_value = b"Accept-Encoding" if not vary else (vary if b"accept-encoding" in vary.lower() else vary + b", Accept-Encoding")
        headers += [
            (b"content-encoding", b"gzip"),
            (b"vary", vary_value),
            (b"content-length", str(len(compressed)).encode()),
        ]
        await send({**start, "headers": headers})
        await send({"type": "http.response.body", "body": compressed})
