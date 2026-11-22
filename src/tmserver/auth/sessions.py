"""Short-lived capabilities owned by one gateway event loop; never saved or logged."""

from dataclasses import dataclass, field
import hashlib
import secrets
import time

from tmserver.network.access import AccountAccess


TICKET_SECONDS = 60
SESSION_SECONDS = 3600
MAX_SESSIONS = 128


class AccessError(ValueError):
    def __init__(self, code, status=401):
        super().__init__(code)
        self.code, self.status = code, status


def token_key(token):
    if not isinstance(token, str) or not 32 <= len(token) <= 128 or not token.isascii():
        raise AccessError("invalid_token")
    return hashlib.sha256(token.encode("ascii")).digest()


@dataclass
class PlaySession:
    subject: object
    access: AccountAccess
    streams: dict = field(default_factory=lambda: {"game": 0, "profile": 0})


class Sessions:
    """Subjects come from one verifier, so the subject ID alone names a user.

    lookup maps a subject ID to its linked account key, or None.
    """

    def __init__(self, lookup, *, clock=time.monotonic):
        self.lookup, self.clock = lookup, clock
        self.tickets, self.sessions = {}, {}

    def account(self, subject):
        key = self.lookup(subject.subject)
        if key is None:
            raise AccessError("account_not_linked", 403)
        return key

    def prune(self):
        now = self.clock()
        self.tickets = {key: row for key, row in self.tickets.items() if row[1] > now}
        for key, session in list(self.sessions.items()):
            if session.access.deadline <= now or session.access.revoked.is_set():
                session.access.revoke()
                del self.sessions[key]

    def issue(self, subject):
        self.account(subject)
        self.prune()
        self.tickets = {key: row for key, row in self.tickets.items()
                        if row[0].subject != subject.subject}
        if len(self.tickets) >= MAX_SESSIONS:
            raise AccessError("capacity", 503)
        ticket = secrets.token_urlsafe(32)
        self.tickets[token_key(ticket)] = subject, self.clock() + TICKET_SECONDS
        return ticket

    def redeem(self, ticket):
        self.prune()
        row = self.tickets.get(token_key(ticket))
        if row is None:
            raise AccessError("invalid_ticket")
        subject, _ = row
        account = self.account(subject)
        if any(session.access.account_key == account for session in self.sessions.values()):
            raise AccessError("session_conflict", 409)
        if len(self.sessions) >= MAX_SESSIONS:
            raise AccessError("capacity", 503)
        session = PlaySession(subject, AccountAccess(account, self.clock() + SESSION_SECONDS,
                                                    clock=self.clock))
        token = secrets.token_urlsafe(32)
        self.sessions[token_key(token)] = session
        del self.tickets[token_key(ticket)]
        return token, session

    def get(self, token):
        self.prune()
        session = self.sessions.get(token_key(token))
        if session is None:
            raise AccessError("invalid_session")
        return session

    def renew(self, subject, token):
        session = self.get(token)
        if session.subject.subject != subject.subject:
            raise AccessError("session_owner_mismatch", 403)
        session.access.deadline = self.clock() + SESSION_SECONDS
        return session

    def revoke(self, token):
        session = self.get(token)
        session.access.revoke()
        self.prune()

    def revoke_subject(self, subject):
        self.account(subject)
        for session in self.sessions.values():
            if session.subject.subject == subject.subject:
                session.access.revoke()
        self.tickets = {key: row for key, row in self.tickets.items()
                        if row[0].subject != subject.subject}
        self.prune()

    def close(self):
        for session in self.sessions.values():
            session.access.revoke()
        self.sessions.clear()
        self.tickets.clear()
