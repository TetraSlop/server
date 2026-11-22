"""Provider verification trusts only a successful response from the configured origin."""

import asyncio
import base64
import json
import unittest
from unittest.mock import patch

from pydantic import ValidationError


class SupabaseVerifierTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        # Keep the optional HTTP dependency out of base-suite collection.
        import httpx
        from tmserver.auth import provider

        self.httpx, self.provider = httpx, provider
        self.project = "https://fixture.supabase.co"
        self.identity = "372656c5-a384-4a7f-8988-7be72f1e009d"
        self.requests = []

    async def verifier(self, handler=None, **options):
        def respond(request):
            self.requests.append(request)
            if handler is not None:
                return handler(request)
            return self.httpx.Response(200, json={"id": self.identity})

        client = self.httpx.AsyncClient(
            transport=self.httpx.MockTransport(respond),
            follow_redirects=True,
            trust_env=False,
            auth=("irrelevant", "client-default"),
        )
        self.addAsyncCleanup(client.aclose)
        return self.provider.SupabaseVerifier(
            options.get("project_url", self.project),
            options.get("public_key", "sb_publishable_test_only"),
            client=client,
        )

    async def test_fixed_endpoint_verified_uuid_and_borrowed_client(self):
        verifier = await self.verifier(
            lambda _: self.httpx.Response(
                200,
                json={
                    "id": self.identity.upper(),
                    "email": "ignored@example.test",
                    "user_metadata": {"legacy_account": "must-not-authorize"},
                },
            )
        )
        subject = await verifier.verify("opaque-access-token")
        self.assertEqual(
            subject.model_dump(),
            {
                "issuer": self.project + "/auth/v1",
                "subject": self.identity,
            },
        )
        request = self.requests[0]
        self.assertEqual(
            (request.method, str(request.url)), ("GET", self.project + "/auth/v1/user")
        )
        self.assertEqual(request.headers["Authorization"], "Bearer opaque-access-token")
        self.assertEqual(request.headers["apikey"], "sb_publishable_test_only")
        self.assertEqual(set(request.extensions["timeout"].values()), {5.0})
        self.assertFalse(verifier._client.is_closed)
        with self.assertRaises(ValidationError):
            subject.subject = "different-user"
        with self.assertRaises(ValidationError):
            self.provider.Subject(issuer=self.project, subject=123)

    async def test_discord_display_name_uses_identity_data_and_normalizes_native_limits(self):
        for data, expected in (
            ({"global_name": "Rúna ✨", "full_name": "runa_user"}, "Runa"),
            ({"global_name": "✨", "full_name": "runa_user"}, "runa_user"),
            ({"global_name": "  Alice\r\n\0\tBob   "}, "Alice Bob"),
            ({"global_name": "12345678901234567890"}, "123456789012345"),
            ({"global_name": False, "full_name": None, "name": "legacy#1234"}, "legacy#1234"),
            ({"global_name": "✨", "full_name": {}}, None),
        ):
            with self.subTest(data=data):
                verifier = await self.verifier(lambda _: self.httpx.Response(200, json={
                    "id": self.identity,
                    "user_metadata": {"global_name": "forged name", "legacy_account": "someone else"},
                    "identities": [
                        {"provider": "email", "identity_data": {"global_name": "wrong provider"}},
                        {"provider": "discord", "identity_data": data},
                    ],
                }))
                subject = await verifier.verify("opaque-access-token")
                self.assertEqual(subject.subject, self.identity)
                self.assertEqual(subject.display_name, expected)
                self.assertTrue(subject.discord)

    async def test_editable_user_metadata_cannot_supply_a_discord_name(self):
        verifier = await self.verifier(lambda _: self.httpx.Response(200, json={
            "id": self.identity, "user_metadata": {"global_name": "Pretend Discord", "provider": "discord"},
            "identities": [{"provider": "email", "identity_data": {"full_name": "Other"}}],
        }))
        subject = await verifier.verify("token")
        self.assertIsNone(subject.display_name)
        self.assertFalse(subject.discord)

    async def test_project_origin_rejects_credentials_paths_queries_and_insecure_urls(self):
        for value in (
            "http://fixture.supabase.co",
            "https://user:password@fixture.supabase.co",
            "https://@fixture.supabase.co",
            self.project + "/auth/v1",
            self.project + "?key=secret",
            self.project + "?",
            self.project + "#fragment",
            self.project + "#",
            "",
            " " + self.project,
            self.project + "\n",
            self.project + "\\other",
            123,
            None,
        ):
            with self.subTest(url=value), self.assertRaises(ValueError):
                self.provider.https_origin(value, "invalid origin")
        self.assertEqual(
            self.provider.https_origin("https://FIXTURE.supabase.co/", "invalid origin"),
            self.project,
        )
        verifier = await self.verifier()
        self.assertEqual(verifier.issuer, self.project + "/auth/v1")

    async def test_invalid_header_values_are_rejected_without_sending_credentials(self):
        bad_values = (None, b"bytes", True, "", " ", "line\r\nheader", "snowman-\u2603", "x" * 8193)
        for value in bad_values:
            with self.subTest(key=value), self.assertRaises(ValueError):
                self.provider.validate_public_key(value)
        verifier = await self.verifier()
        for value in bad_values:
            with self.subTest(token=value), self.assertRaises(self.provider.InvalidToken):
                await verifier.verify(value)
        self.assertEqual(self.requests, [])

    async def test_only_401_and_403_are_classified_as_rejected_tokens(self):
        for status in (401, 403, 400, 404, 429, 500, 502, 503, 204):
            verifier = await self.verifier(
                lambda _, code=status: self.httpx.Response(code, text="secret response detail")
            )
            error_type = (
                self.provider.InvalidToken
                if status in (401, 403)
                else self.provider.ProviderUnavailable
            )
            with self.subTest(status=status), self.assertRaises(error_type) as raised:
                await verifier.verify("secret-access-token")
            self.assertNotIn("secret", str(raised.exception))

    async def test_public_key_classification_rejects_privileged_and_malformed_keys(self):
        def segment(value):
            return base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip("=")

        def legacy(payload, header=None):
            # The fixture signature is not verified locally: /user still authenticates each user.
            return ".".join(
                (
                    segment(header or {"alg": "HS256", "typ": "JWT"}),
                    segment(payload),
                    segment("fixture-signature"),
                )
            )

        key = legacy({"role": "anon", "iss": "supabase", "ref": "fixture"})
        self.assertEqual(self.provider.validate_public_key(key), key)
        verifier = await self.verifier(public_key=key)
        self.assertEqual((await verifier.verify("access-token")).subject, self.identity)
        self.assertEqual(self.requests[-1].headers["apikey"], key)
        self.assertEqual(
            self.provider.validate_public_key("sb_publishable_test_only"),
            "sb_publishable_test_only",
        )
        for invalid in (
            "sb_publishable_",
            "sb_secret_sensitive",
            "public-key",
            "three.parts.invalid",
            legacy({"role": "service_role"}),
            legacy({"role": "authenticated"}),
            legacy({"role": True}),
            legacy({}),
            legacy([{"role": "anon"}]),
            legacy({"role": "anon"}, {"alg": "none"}),
            legacy({"role": "anon"}, ["not-a-header"]),
            key.rsplit(".", 1)[0] + ".",
            "e30.bm90LWpzb24.c2ln",
            "a.b.c",
        ):
            with self.subTest(key=invalid), self.assertRaises(ValueError) as raised:
                self.provider.validate_public_key(invalid)
            self.assertNotIn(invalid, str(raised.exception))

    async def test_redirects_never_forward_the_bearer_to_another_endpoint(self):
        for status in (301, 302, 307, 308):
            verifier = await self.verifier(
                lambda _, code=status: self.httpx.Response(
                    code, headers={"Location": "https://other.example.test/collect-token"}
                )
            )
            before = len(self.requests)
            with self.subTest(status=status), self.assertRaises(self.provider.ProviderUnavailable):
                await verifier.verify("secret-access-token")
            self.assertEqual(len(self.requests), before + 1)

    async def test_missing_or_invalid_uuid_in_success_is_not_an_authenticated_user(self):
        for document in (
            {},
            {"id": None},
            {"id": True},
            {"id": 123},
            {"id": ""},
            {"id": "not-a-uuid"},
            {"user": {"id": self.identity}},
            [],
        ):
            verifier = await self.verifier(
                lambda _, data=document: self.httpx.Response(200, json=data)
            )
            with (
                self.subTest(document=document),
                self.assertRaises(self.provider.ProviderUnavailable),
            ):
                await verifier.verify("secret-access-token")
        verifier = await self.verifier(
            lambda _: self.httpx.Response(200, text="secret invalid JSON")
        )
        with self.assertRaises(self.provider.ProviderUnavailable) as raised:
            await verifier.verify("secret-access-token")
        self.assertNotIn("secret", str(raised.exception))

    async def test_transport_failure_and_timeout_are_sanitized_provider_errors(self):
        for error_type in (self.httpx.ConnectError, self.httpx.ReadTimeout):

            def fail(request):
                raise error_type("secret-access-token", request=request)

            verifier = await self.verifier(fail)
            with (
                self.subTest(error=error_type),
                self.assertRaises(self.provider.ProviderUnavailable) as raised,
            ):
                await verifier.verify("secret-access-token")
            self.assertNotIn("secret", str(raised.exception))
            self.assertTrue(raised.exception.__suppress_context__)

    async def test_overall_deadline_is_bounded_and_cancellation_propagates(self):
        async def delayed(_request):
            await asyncio.sleep(1)
            return self.httpx.Response(200, json={"id": self.identity})

        client = self.httpx.AsyncClient(
            transport=self.httpx.MockTransport(delayed), trust_env=False
        )
        self.addAsyncCleanup(client.aclose)
        verifier = self.provider.SupabaseVerifier(
            self.project, "sb_publishable_test_only", client=client
        )
        with patch.object(self.provider, "VERIFY_TIMEOUT", 0.01):
            with self.assertRaises(self.provider.ProviderUnavailable):
                await verifier.verify("token")
        pending = asyncio.create_task(verifier.verify("token"))
        await asyncio.sleep(0)
        pending.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await pending
