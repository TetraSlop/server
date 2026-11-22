"""Capabilities are single use, bound to immutable identities and revocable."""

from types import SimpleNamespace
import unittest

from tmserver.auth.sessions import AccessError, Sessions


class SessionsTests(unittest.TestCase):
    def setUp(self):
        self.now = 100
        self.alice = SimpleNamespace(issuer="issuer", subject="alice")
        self.bob = SimpleNamespace(issuer="issuer", subject="bob")
        self.sessions = Sessions({"alice": "A", "bob": "B"}.get, clock=lambda: self.now)

    def test_exact_owner_expiring_single_use_ticket(self):
        ticket = self.sessions.issue(self.alice)
        token, session = self.sessions.redeem(ticket)
        self.assertEqual(session.access.account_key, "A")
        with self.assertRaisesRegex(AccessError, "invalid_ticket"):
            self.sessions.redeem(ticket)
        self.assertNotIn(token, str(self.sessions.sessions))
        self.now += 3601
        with self.assertRaisesRegex(AccessError, "invalid_session"):
            self.sessions.get(token)
        self.assertTrue(session.access.revoked.is_set())

    def test_expired_ticket_and_unknown_identity(self):
        ticket = self.sessions.issue(self.alice)
        self.now += 61
        with self.assertRaisesRegex(AccessError, "invalid_ticket"):
            self.sessions.redeem(ticket)
        with self.assertRaisesRegex(AccessError, "account_not_linked"):
            self.sessions.issue(SimpleNamespace(issuer="issuer", subject="unknown"))

    def test_renewal_and_logout_cannot_target_another_user(self):
        token, session = self.sessions.redeem(self.sessions.issue(self.alice))
        with self.assertRaisesRegex(AccessError, "session_owner_mismatch"):
            self.sessions.renew(self.bob, token)
        self.now += 100
        self.sessions.renew(self.alice, token)
        self.assertEqual(session.access.deadline, self.now + 3600)
        self.sessions.revoke_subject(self.bob)
        self.assertIs(self.sessions.get(token), session)
        self.sessions.revoke_subject(self.alice)
        self.assertTrue(session.access.revoked.is_set())

    def test_duplicate_play_requires_explicit_revoke_and_new_generation(self):
        token, first = self.sessions.redeem(self.sessions.issue(self.alice))
        ticket = self.sessions.issue(self.alice)
        with self.assertRaisesRegex(AccessError, "session_conflict"):
            self.sessions.redeem(ticket)
        self.sessions.revoke(token)
        other_token, second = self.sessions.redeem(ticket)
        self.assertIsNot(first.access, second.access)
        self.assertNotEqual(token, other_token)
        first.access.profile_ciphers.append("old")
        self.assertEqual(second.access.profile_ciphers, [])
        self.sessions.close()
        self.assertTrue(second.access.revoked.is_set())

    def test_name_changes_do_not_change_owner_or_leave_tickets_and_sessions_behind(self):
        before = SimpleNamespace(issuer="issuer", subject="alice", display_name="Before")
        after = SimpleNamespace(issuer="issuer", subject="alice", display_name="After")
        old = self.sessions.issue(before)
        new = self.sessions.issue(after)
        with self.assertRaisesRegex(AccessError, "invalid_ticket"):
            self.sessions.redeem(old)
        token, session = self.sessions.redeem(new)
        self.sessions.renew(before, token)
        self.assertEqual(session.subject.display_name, "After")
        unused = self.sessions.issue(after)
        self.sessions.revoke_subject(before)
        self.assertTrue(session.access.revoked.is_set())
        self.assertFalse(self.sessions.sessions)
        with self.assertRaisesRegex(AccessError, "invalid_ticket"):
            self.sessions.redeem(unused)

    def test_new_ticket_replaces_unused_old_ticket_without_creating_session(self):
        old = self.sessions.issue(self.alice)
        new = self.sessions.issue(self.alice)
        self.assertFalse(self.sessions.sessions)
        with self.assertRaisesRegex(AccessError, "invalid_ticket"):
            self.sessions.redeem(old)
        self.sessions.redeem(new)
