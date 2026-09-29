"""Event reads never write; admission and account mutations still commit."""

from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock, patch

from tests.events.test_event_configuration import DOCUMENT
from tests.events.test_event_rooms import presence
from tests.network.test_server import Client
from tests.profiles.test_options_service import OFFSETS, VALUES, request as options_request
from tmserver.economy.packets import named
from tmserver.events.config import parse_event_configs
from tmserver.events.packets import time_reply
from tmserver.events.routing import dispatch_event
from tmserver.network.server import Server
from tmserver.protocol.commands import Command, encode, parse
from tmserver.protocol.messages import service_notice


def targets(host):
    return {
        route: nick for nick, (event, route) in host.event_routes.items() if event == "festival"
    }


def query(host, key, *, identity=None, subtype=4):
    identity = host.economy.account_view(key).nn if identity is None else identity
    return named(0xD1, subtype, 0, f"@EventTimeReq=/NN={identity:016X}")


class EventTimeReadTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.store = Mock()
        self.store.load.return_value = None
        self.host = Server(
            store=self.store,
            event_configs=parse_event_configs(DOCUMENT),
            event_clock=lambda: 200,
        )
        self.nick = next(iter(self.host.accounts))
        self.key = self.host.ensure_account(self.nick)
        self.peer = Mock(server=self.host, nick=self.nick, closed=False)
        self.host.deliver = AsyncMock()
        self.host.room_updates = AsyncMock()
        self.host.chat_updates = AsyncMock()
        self.routes = targets(self.host)
        self.room = self.host.event_rooms.rooms[0].room
        self.store.reset_mock()

    async def test_original_time_request_reads_busy_account_without_copying_or_saving(self):
        token = self.host.economy.reserve_match(self.key)
        before = self.host.snapshot()
        payload = query(self.host, self.key)
        for request in (payload, payload.replace(b"GTM0GD1", b"GTM0Gd1")):
            with self.subTest(request=request):
                self.store.reset_mock()
                self.assertTrue(await dispatch_event(self.peer, self.routes["room"], request))
                self.host.deliver.assert_awaited_with(
                    self.peer,
                    service_notice(self.routes["room"], self.nick, time_reply(100, 500, 200)),
                )
                self.assertEqual(len(self.store.method_calls), 0)
                self.assertEqual(self.host.snapshot(), before)
                self.assertIs(self.host.economy._reservations[self.key], token)

    async def test_identity_route_and_original_subtype_checks_still_reject_without_writes(self):
        before = self.host.snapshot()
        payload = query(self.host, self.key)
        wrong_identity = self.host.economy.account_view(self.key).nn ^ 1
        cases = [
            (self.routes["room"], query(self.host, self.key, identity=wrong_identity)),
            (self.routes["init"], payload),
            (self.routes["card"], payload),
            (self.routes["room"], query(self.host, self.key, subtype=5)),
            (self.routes["room"], named(0xD1, 4, 0, "@EventTimeReq=")),
            (self.routes["room"], payload + b"/Extra=1"),
        ]
        for route, request in cases:
            with self.subTest(route=route, request=request), self.assertRaises(ValueError):
                await dispatch_event(self.peer, route, request)
        self.assertEqual(self.store.method_calls, [])
        self.host.deliver.assert_not_awaited()
        self.assertEqual(self.host.snapshot(), before)

    async def test_room_replay_reads_history_without_copying_or_saving(self):
        view = self.host.economy.account_view(self.key)
        self.room.handle(
            self.peer,
            presence(self.room.room_id, view.nn, view.player_id, view.name),
            authenticated_identity=(view.nn, view.player_id),
        )
        token = self.host.economy.reserve_match(self.key)
        before = self.host.snapshot(), self.room.snapshot(), dict(self.room.history)
        current = self.room.sequence
        cases = (
            (current, []),
            (
                current - 1,
                [(self.peer, self.room.wrap_delta(current, self.room.history[current]))],
            ),
            (current + 1, [(self.peer, b"GTM0L" + encode([Command(b"DO", b"")]))]),
        )
        for sequence, expected in cases:
            with self.subTest(sequence=sequence):
                self.store.reset_mock()
                self.assertTrue(
                    await dispatch_event(
                        self.peer,
                        self.routes["room"],
                        b"GTM0L" + encode([Command(b"DR", str(sequence).encode())]),
                    )
                )
                self.host.room_updates.assert_awaited_with(expected, source=self.routes["room"])
                self.assertEqual(len(self.store.method_calls), 0)
                self.assertEqual(
                    (self.host.snapshot(), self.room.snapshot(), dict(self.room.history)), before
                )
                self.assertEqual(self.room.sequence, current)
                self.assertIs(self.host.economy._reservations[self.key], token)

    async def test_unhandled_families_return_false_without_saving_or_delivering(self):
        before = self.host.snapshot()
        for route in self.routes.values():
            for payload in (
                named(0xFE, 0, 0, "@Unknown="),
                b"not-a-service",
                # Options recognition was and remains case-sensitive.
                named(0x84, 0, 0, "@Opt=").lower(),
                b"GTM0L" + encode([Command(b"ZZ", b"")]),
            ):
                with self.subTest(route=route, payload=payload):
                    self.store.reset_mock()
                    self.assertFalse(await dispatch_event(self.peer, route, payload))
                    self.assertEqual(len(self.store.method_calls), 0)
        self.host.deliver.assert_not_awaited()
        self.host.room_updates.assert_not_awaited()
        self.host.chat_updates.assert_not_awaited()
        self.assertEqual(self.host.snapshot(), before)

    async def test_room_admission_departure_and_options_still_commit_before_replies(self):
        view = self.host.economy.account_view(self.key)
        order = []
        self.store.save_changes.side_effect = lambda *_: order.append("save")
        self.host.room_updates.side_effect = lambda rows, **_: order.extend(
            ["room"] if rows else []
        )
        self.host.deliver.side_effect = lambda *_: order.append("reply")
        self.assertTrue(
            await dispatch_event(
                self.peer,
                self.routes["room"],
                presence(self.room.room_id, view.nn, view.player_id, view.name),
            )
        )
        self.assertEqual(order, ["save", "room"])
        self.assertIsNotNone(self.host.events.member(self.key, "festival"))
        self.assertEqual(self.host.events._present[self.key], "festival")
        responses = self.host.room_updates.call_args.args[0]
        self.assertEqual([parse(row[1][5:])[0].token for row in responses[:2]], [b"DS", b"DD"])
        self.assertEqual(responses[2][1], named(0xD0, 0, 0, "@EventEn=/Ans=1"))
        self.assert_saved_event_state()

        order.clear()
        self.store.reset_mock()
        self.assertTrue(
            await dispatch_event(
                self.peer,
                self.routes["room"],
                b"GTM0L" + encode([Command(b"PC", f"0x{view.nn:016X}".encode())]),
            )
        )
        self.assertEqual(order, ["save"])
        self.assertNotIn(self.peer, self.room.members)
        self.assertNotIn(self.key, self.host.events._present)
        self.assertIsNotNone(self.host.events.member(self.key, "festival"))
        self.assert_saved_event_state()

        order.clear()
        self.store.reset_mock()
        self.assertTrue(
            await dispatch_event(
                self.peer, self.routes["room"], options_request(view.nn, name=view.name)
            )
        )
        self.assertEqual(order, ["save", "reply"])
        self.assertEqual(
            [self.host.economy.profile_bytes(self.key)[offset] for offset in OFFSETS],
            list(VALUES.values()),
        )
        self.host.deliver.assert_awaited_with(
            self.peer,
            service_notice(self.routes["room"], self.nick, named(0x85, 0, 0, "@OptAns=/Ans=1")),
        )
        self.assert_saved_event_state()

    async def test_card_money_and_get_still_commit_the_account_and_grant_together(self):
        self.host.events.admit_room(self.key, "festival")
        card = replace(self.host.economy.inventory(self.key)[0], deck=255)
        self.host.event_cards.grant(self.key, "festival", "earned", cards=(card,), money=25)
        before = self.host.economy.account_view(self.key)
        order = []
        self.store.save_changes.side_effect = lambda *_: order.append("save")
        self.host.deliver.side_effect = lambda *_: order.append("reply")
        self.assertTrue(
            await dispatch_event(
                self.peer,
                self.routes["card"],
                named(
                    0x90,
                    0,
                    0,
                    f"@CardReq=/NN={before.nn:016X}/HID=0/Dm=1/Vol=1/CN={before.name.hex()}",
                ),
            )
        )
        self.assertEqual(order, ["save", "reply", "reply"])
        self.assertEqual(self.host.economy.balance(self.key), before.balance + 25)
        self.assertEqual(self.host.economy.inventory(self.key), before.cards)
        self.assertEqual(
            self.host.event_cards.pending(self.key, "festival"), {"cards": 1, "money": 0}
        )
        self.assert_saved_event_state()

        number = self.host.event_cards.status(self.key)["session"]
        order.clear()
        self.store.reset_mock()
        self.assertTrue(
            await dispatch_event(
                self.peer, self.routes["card"], named(0xB2, 0x15, number, f"@Get=/No={number}")
            )
        )
        self.assertEqual(order, ["save"])
        self.assertEqual(len(self.host.economy.inventory(self.key)), len(before.cards) + 1)
        self.assertEqual(
            self.host.event_cards.pending(self.key, "festival"), {"cards": 0, "money": 0}
        )
        self.assert_saved_event_state()

    def assert_saved_event_state(self):
        self.store.save_changes.assert_called_once()
        accounts, services = self.store.save_changes.call_args.args
        self.assertEqual(accounts, {self.key: self.host.snapshot()["accounts"][self.key]})
        self.assertEqual(
            services,
            {
                name: self.host.snapshot()[name]
                for name in ("events", "event_cards", "event_results")
            },
        )


class EncryptedEventTimeReadTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.folder = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.path = self.folder / "state.json"
        self.host = Server(
            state_path=self.path,
            event_configs=parse_event_configs(DOCUMENT),
            event_clock=lambda: 200,
        )
        self.addAsyncCleanup(self.host.stop)
        port = await self.host.start(0)
        self.clients = []
        for nick in self.host.accounts:
            client = Client()
            await client.open(port, account=nick)
            self.addAsyncCleanup(client.close)
            self.assertIn(b" 422 ", await client.read())
            self.clients.append(client)
        self.routes = targets(self.host)

    async def test_encrypted_clock_query_never_touches_failed_write_path_or_other_player(self):
        before, disk = self.host.snapshot(), self.path.read_bytes()
        with patch.object(
            self.host.store,
            "save_changes",
            side_effect=OSError("write path must not be used"),
        ) as save:
            for client in self.clients:
                await client.send(
                    b"NOTICE "
                    + self.routes["room"]
                    + b" :"
                    + query(self.host, client.nick.decode()),
                )
                self.assertEqual(
                    await client.read(),
                    service_notice(self.routes["room"], client.nick, time_reply(100, 500, 200)),
                )
                await client.send(
                    b"NOTICE "
                    + self.routes["room"]
                    + b" :GTM0L"
                    + encode([Command(b"DR", b"999")]),
                )
                self.assertEqual(
                    await client.read(),
                    service_notice(
                        self.routes["room"],
                        client.nick,
                        b"GTM0L" + encode([Command(b"DO", b"")]),
                    ),
                )
                await client.send(
                    b"NOTICE " + self.routes["room"] + b" :" + named(0xFE, 0, 0, "@Unknown="),
                )
            for client in self.clients:
                await client.send(b"PING :still-live")
                self.assertEqual(await client.read(), b"PONG still-live")
            save.assert_not_called()
        self.assertIsNone(self.host.storage_failed)
        self.assertEqual(self.host.snapshot(), before)
        self.assertEqual(self.path.read_bytes(), disk)
