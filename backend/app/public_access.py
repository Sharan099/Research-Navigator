"""Anonymous browser capabilities for a public demo, not account authentication."""

import hashlib
import hmac
import secrets
import time
from http.cookies import SimpleCookie

import anyio
from starlette.responses import JSONResponse

from .models import Conversation

COOKIE = "__Host-research-session"
MAX_AGE = 30 * 86400


def issue_cookie(secret):
    value = f"{secrets.token_hex(32)}.{int(time.time())}"
    signature = hmac.new(secret.encode(), value.encode(), hashlib.sha256).hexdigest()
    return value + "." + signature


def visitor_hash(cookie, secret):
    try:
        identifier, timestamp, signature = cookie.split(".")
        value = identifier + "." + timestamp
        expected = hmac.new(secret.encode(), value.encode(), hashlib.sha256).hexdigest()
        age = time.time() - int(timestamp)
        if len(identifier) == 64 and 0 <= age <= MAX_AGE and hmac.compare_digest(signature, expected):
            return hashlib.sha256(identifier.encode()).hexdigest()
    except (AttributeError, ValueError, TypeError):
        pass
    return None


def read_visitor(request, secret):
    return visitor_hash(request.cookies.get(COOKIE), secret)


class VisitorPolicy:
    def __init__(self, app, settings, sessions):
        self.app, self.settings, self.sessions = app, settings, sessions

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or self.settings.app_mode != "public_demo":
            return await self.app(scope, receive, send)
        path = scope["path"]
        if path == "/metrics":
            return await JSONResponse({"detail": "Not found"}, status_code=404)(scope, receive, send)
        protected = path.startswith("/api/conversations") or path == "/api/search"
        if not protected:
            return await self.app(scope, receive, send)
        cookies = SimpleCookie()
        try:
            cookies.load(dict(scope.get("headers", [])).get(b"cookie", b"").decode("latin1"))
            token = cookies.get(COOKIE)
            owner = visitor_hash(token.value if token else None, self.settings.session_secret)
        except Exception:
            owner = None
        if not owner:
            return await JSONResponse(
                {"detail": "Enable cookies and reload, or open the application directly."}, status_code=401
            )(scope, receive, send)
        scope.setdefault("state", {})["visitor_hash"] = owner
        parts = path.strip("/").split("/")
        if len(parts) >= 3 and parts[:2] == ["api", "conversations"]:

            def owned():
                with self.sessions() as session:
                    row = session.get(Conversation, parts[2])
                    return row is not None and row.visitor_hash == owner

            if not await anyio.to_thread.run_sync(owned):
                return await JSONResponse({"detail": "Conversation not found"}, status_code=404)(scope, receive, send)
        return await self.app(scope, receive, send)
