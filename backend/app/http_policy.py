"""Local-only request policy and streaming-safe, content-free HTTP telemetry."""

import json
from time import monotonic
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from .telemetry import logger, request_id


class LocalPolicy:
    def __init__(self, app, metrics, max_active_requests=5, public_host=""):
        self.app, self.metrics = app, metrics
        self.max_active_requests = max_active_requests
        self.active = 0
        self.public_host = public_host

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = dict(scope.get("headers", []))
        try:
            correlation = str(UUID(headers.get(b"x-request-id", b"").decode()))
        except (ValueError, UnicodeError):
            correlation = str(uuid4())
        token = request_id.set(correlation)
        started, status = monotonic(), 500

        async def tracked(message):
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                message["headers"] = [*message.get("headers", []), (b"x-request-id", correlation.encode())]
            await send(message)

        try:
            await self._dispatch(scope, receive, tracked)
        finally:
            route = getattr(scope.get("route"), "path", "unmatched")
            method = (
                scope["method"]
                if scope["method"] in {"GET", "POST", "PATCH", "DELETE", "PUT", "OPTIONS", "HEAD"}
                else "OTHER"
            )
            self.metrics.increment("http_requests", {"route": route, "method": method, "status": str(status)})
            self.metrics.observe("http_seconds", monotonic() - started, {"route": route, "method": method})
            logger.info(
                json.dumps(
                    {
                        "event": "http_request",
                        "request_id": correlation,
                        "route": route,
                        "method": method,
                        "status": status,
                        "duration_ms": round((monotonic() - started) * 1000),
                    }
                )
            )
            request_id.reset(token)

    async def _dispatch(self, scope, receive, send):
        headers = dict(scope.get("headers", []))
        host = headers.get(b"host", b"").decode("latin1")
        try:
            authority = urlsplit("//" + host)
            hostname = authority.hostname if not authority.username and not authority.password else None
        except ValueError:
            hostname = None
        testing = (scope.get("client") or (None,))[0] == "testclient"
        allowed = {"localhost", "127.0.0.1", "::1"} | ({"testserver"} if testing else set())
        if self.public_host:
            allowed.add(self.public_host)

        async def reject(status, message):
            body = json.dumps({"detail": message}).encode()
            await send(
                {"type": "http.response.start", "status": status, "headers": [(b"content-type", b"application/json")]}
            )
            await send({"type": "http.response.body", "body": body})

        if hostname not in allowed:
            return await reject(400, "Only local host names are accepted.")
        admitted = scope["method"] == "POST" and (scope["path"] == "/api/search" or scope["path"].endswith("/messages"))
        if admitted and self.active >= self.max_active_requests:
            return await reject(429, "The local request limit is busy. Retry shortly.")
        if scope["method"] in {"POST", "PATCH", "DELETE", "PUT"}:
            origin = headers.get(b"origin")
            if origin:
                try:
                    parsed_origin = urlsplit(origin.decode("latin1"))
                    same_origin = parsed_origin.netloc == host and parsed_origin.scheme in {"http", "https"}
                    if self.public_host and hostname == self.public_host:
                        same_origin = parsed_origin.netloc == host and parsed_origin.scheme == "https"
                except ValueError:
                    same_origin = False
                if not same_origin:
                    return await reject(403, "Cross-origin changes are not allowed.")
            body, size = [], 0
            while True:
                chunk = await receive()
                if chunk["type"] == "http.disconnect":
                    return
                size += len(chunk.get("body", b""))
                if size > 64_000:
                    return await reject(413, "The request body exceeds the local limit.")
                body.append(chunk)
                if not chunk.get("more_body"):
                    break
            if size and not headers.get(b"content-type", b"").split(b";", 1)[0] == b"application/json":
                return await reject(415, "Use application/json for request content.")
            index = 0
            upstream_receive = receive

            async def replay():
                nonlocal index
                if index < len(body):
                    value = body[index]
                    index += 1
                    return value
                return await upstream_receive()

            receive = replay
        if admitted and self.active >= self.max_active_requests:
            return await reject(429, "The local request limit is busy. Retry shortly.")
        if admitted:
            self.active += 1

        async def observed(message):
            if message["type"] == "http.response.start":
                message["headers"] = [
                    *message.get("headers", []),
                    (b"x-content-type-options", b"nosniff"),
                    (b"referrer-policy", b"same-origin"),
                    (
                        b"content-security-policy",
                        (
                            "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
                            "connect-src 'self'; frame-ancestors "
                            + ("https://huggingface.co" if self.public_host else "'none'")
                            + "; base-uri 'none'; form-action 'self'"
                        ).encode(),
                    ),
                ]
            await send(message)

        try:
            await self.app(scope, receive, observed)
        finally:
            if admitted:
                self.active -= 1
