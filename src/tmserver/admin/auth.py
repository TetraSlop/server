"""Local operator authentication and a bounded, revocable session lifetime."""

import hmac
import secrets
import time

from starlette_admin.auth import AdminUser, AuthProvider, LoginFailed

SESSION_SECONDS = 15 * 60
MAX_SESSIONS = 64


class OperatorAuth(AuthProvider):
    def __init__(self, username, password, *, clock=time.monotonic):
        super().__init__()
        if not isinstance(username, str) or not username.strip():
            raise ValueError("an explicit operator username is required")
        if not isinstance(password, str) or not password:
            raise ValueError("an explicit operator password is required")
        self.username = username
        self._password = password.encode("utf-8")
        self.clock = clock
        self._sessions = {}

    async def login(self, username, password, remember_me, request):
        valid_name = hmac.compare_digest(username.encode("utf-8"), self.username.encode("utf-8"))
        valid_password = hmac.compare_digest(password.encode("utf-8"), self._password)
        if not (valid_name and valid_password):
            raise LoginFailed("The operator username or password is incorrect.")
        now = self.clock()
        self._sessions = {sid: end for sid, end in self._sessions.items() if end > now}
        self._sessions.pop(request.session.get("operator_sid"), None)
        if len(self._sessions) >= MAX_SESSIONS:
            self._sessions.pop(next(iter(self._sessions)))
        sid = secrets.token_urlsafe(32)
        self._sessions[sid] = now + SESSION_SECONDS
        request.session.clear()
        request.session["operator_sid"] = sid

    async def authenticate(self, request):
        sid = request.session.get("operator_sid")
        if isinstance(sid, str) and self._sessions.get(sid, 0) > self.clock():
            return AdminUser(username=self.username)
        if isinstance(sid, str):
            self._sessions.pop(sid, None)
        request.session.clear()
        return None

    async def logout(self, request):
        sid = request.session.get("operator_sid")
        if isinstance(sid, str):
            self._sessions.pop(sid, None)
        request.session.clear()
