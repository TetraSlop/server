"""Game (IRC) listener: owns connections, shared services and table runtimes.

Listens on loopback only. The wire encryption is in protocol/wire.py.
"""

from tmserver.protocol.messages import service_notice

import asyncio
import secrets
import time
from collections import deque
from contextlib import contextmanager
from pathlib import Path

from tmserver.admin.audit import AdminAudit
from tmserver.accounts.identities import IdentityRegistry
from tmserver.economy.auctions.ledger import AuctionEconomy
from tmserver.economy.auctions.models import AuctionError
from tmserver.economy.auctions.service import AuctionService
from tmserver.profiles.characters import CharacterProfiles
from tmserver.network.chat import ChatService
from tmserver.economy.claim_transactions import auction_transaction
from tmserver.economy.commerce import Commerce
from tmserver.network.diagnostics import report_failure, write_record
from tmserver.economy.service import EconomyService
from tmserver.events.adapter import EventAdapter
from tmserver.events.cards import EventCardService
from tmserver.events.results import EventResults
from tmserver.events.rooms import EventRooms
from tmserver.events.service import EventHost
from tmserver.protocol.identities import GAME_KEY, ROOM_RAW, TRADE_RAW, player_id, service_nick
from tmserver.network.session import MAX_LINE, Session
from tmserver.network.local_accounts import account_specs, display_name
from tmserver.protocol.wire import polid_to_string, polid_unscramble
from tmserver.economy.match_ledger import MatchEconomy
from tmserver.rooms.config import configure_rooms
from tmserver.rooms.config import directory_files as ordinary_directory_files
from tmserver.rooms.table import OrdinaryTable
from tmserver.economy.prize_center import PrizeService
from tmserver.rankings.packets import METADATA_PATH
from tmserver.rankings.service import RankingService
from tmserver.rankings.policy import RatingPolicy
from tmserver.economy.result_rewards import ResultRewardService
from tmserver.rooms.service import RoomService
from tmserver.storage.validation import validate_snapshot_fields
from tmserver.storage.json import JsonStateStore
from tmserver.economy.trade_router import TradeRouter
from tmserver.economy.trade import TradeService


class Server:
    def __init__(
        self,
        log_path=None,
        state_path=None,
        *,
        watchdog=None,
        spectator=None,
        account_count=2,
        match_mode="computer",
        match_player_count=2,
        computer_menu=False,
        match_start_fields=None,
        allow_cbc=False,
        card_prizes=False,
        allow_wagers=False,
        auction_clock=time.time,
        event_configs=(),
        event_clock=time.time,
        event_tables=(),
        event_hand_provider=None,
        event_progress_policy="none",
        shop_offers=None,
        rating_mode="preserve",
        weekly_rankings=False,
        ranking_clock=time.time,
        allow_departures=False,
        ordinary_rooms=(),
        flexible_seats=False,
        store=None,
        rules_backend=None,
    ):
        if store is not None and state_path is not None:
            raise ValueError("choose either state_path or store")
        if match_player_count not in (2, 3) or (
            match_player_count == 3 and (match_mode != "versus" or account_count < 3)
        ):
            raise ValueError("three-player tables require versus mode and three enabled accounts")
        if (
            type(flexible_seats) is not bool
            or flexible_seats
            and (
                match_mode != "versus" or match_player_count != 3 or match_start_fields is not None
            )
        ):
            raise ValueError(
                "automatic seats require a three-seat versus table with a generated board"
            )
        self.account_specs = account_specs(account_count)
        # Like an injected store, the rules backend is borrowed from the caller.
        self.rules_backend = rules_backend
        self.accounts = {key.encode("ascii"): value[1] for key, value in self.account_specs.items()}
        self.connection_limit = max(8, len(self.accounts))
        self.log_file = None
        self.log_error = None
        self.sessions = set()
        self.tasks = set()
        self.nicks = {}
        self.channels = {}
        self.room_outbox = deque()
        self.room_task = None
        self.counter = 0
        self.listener = None
        self.auction_task = None
        self.storage_failed = None
        self.room = RoomService(
            player_count=match_player_count, computer_enabled=match_mode == "computer"
        )
        self.chat = ChatService()
        self.state_path = Path(state_path) if state_path else None
        # Injected stores are owned by the caller, including constructor failures.
        self.store = (
            store
            if store is not None
            else (JsonStateStore(self.state_path) if self.state_path else None)
        )
        saved = self.store.load() if self.store is not None else None
        if saved is not None:
            validate_snapshot_fields(saved)
            self.economy = EconomyService.restore(saved, seed=1, shop_offers=shop_offers)
        else:
            self.economy = EconomyService(seed=1, shop_offers=shop_offers)
        self.account_keys = set(self.economy.snapshot()["accounts"])
        self.identities = IdentityRegistry.restore(
            self.economy, None if saved is None else saved.get("identities")
        )
        for link in self.identities.snapshot()["links"]:
            self.register_hosted_account(link["account_key"])
        self.commerce = Commerce(self.economy, self.persist_changes)
        self.auction_ledger = AuctionEconomy.restore(
            self.economy, None if saved is None else saved.get("auction")
        )
        self.auction_clock = auction_clock
        self.auction = AuctionService(self.auction_ledger, clock=auction_clock)
        self.prize = PrizeService.restore(
            self.economy, None if saved is None else saved.get("prize"), seed=1
        )
        self.result_rewards = ResultRewardService.restore(
            self.economy,
            self.auction_ledger,
            None if saved is None else saved.get("result_rewards"),
        )
        self.match_economy = MatchEconomy.restore(
            self.economy, None if saved is None else saved.get("match_economy")
        )
        self.ratings = RatingPolicy(
            self.economy,
            mode=rating_mode,
            weekly=weekly_rankings,
            clock=ranking_clock,
            snapshot=None if saved is None else saved.get("ratings"),
        )
        self.ratings.validate_profiles()
        self.character_profiles = CharacterProfiles.restore(
            self.economy, None if saved is None else saved.get("character_profiles")
        )
        self.ranking = RankingService(self.economy, rating_policy=self.ratings)
        event_configs = tuple(event_configs)
        account_ids = {player_id(key) for key in self.accounts}
        if any(
            identity in account_ids
            for c in event_configs
            for identity in (c.room_id, c.init_id, c.card_id)
        ):
            raise ValueError("event service identity collides with a synthetic account")
        self.events = EventHost.restore(
            self.economy,
            event_configs,
            None if saved is None else saved.get("events"),
            clock=event_clock,
        )
        event_tables = tuple(event_tables)
        if any(c.table_id in account_ids for c in event_tables):
            raise ValueError("event table identity collides with a synthetic account")
        if any(c.cpu_cards is None and c.player_count > account_count for c in event_tables):
            raise ValueError("event table requires more enabled human accounts")
        self.event_rooms = EventRooms(self.events)
        self.event_cards = EventCardService.restore(
            self.economy,
            self.events,
            None if saved is None else saved.get("event_cards"),
        )
        self.admin_audit = AdminAudit.restore(
            self.economy,
            self.prize,
            None if saved is None else saved.get("admin_audit"),
            event_cards=self.event_cards,
        )
        self.event_routes = {
            service_nick(identity ^ GAME_KEY): (c.id, route)
            for c in event_configs
            for route, identity in (
                ("room", c.room_id),
                ("init", c.init_id),
                ("card", c.card_id),
            )
        }
        self.event_results = EventResults.restore(
            None if saved is None else saved.get("event_results")
        )
        self.event_matches = EventAdapter(
            self,
            event_tables,
            hand_provider=event_hand_provider,
            progress_policy=event_progress_policy,
            allow_observers=spectator is not None and spectator.enabled,
        )
        self.default_table = OrdinaryTable(
            self,
            self.room,
            match_mode=match_mode,
            match_player_count=match_player_count,
            computer_menu=computer_menu,
            match_start_fields=match_start_fields,
            allow_cbc=allow_cbc,
            card_prizes=card_prizes,
            allow_wagers=allow_wagers,
            allow_departures=allow_departures,
            watchdog=watchdog,
            spectator=spectator,
            flexible_seats=flexible_seats,
        )
        configure_rooms(self, ordinary_rooms)
        self.trade = TradeService(self.economy, self.commerce)
        self.trade_router = TradeRouter(self.trade, self.trade_busy)
        # Validate the full host before acquiring its log. An unavailable log
        # must still fail before startup recovery changes the durable save.
        self.log_file = Path(log_path).open("a", encoding="utf-8") if log_path else None
        # Interrupted rewards use the mapped returned-card collection path;
        # recovery is durably complete before either listener can be started.
        try:
            recovered = self.match_economy.recover_all(self.auction_ledger)
            recovered = self.result_rewards.recover_all() or recovered
            if recovered:
                self.persist()
        except BaseException:
            self._close_log()
            raise

    def trade_busy(self, peer):
        return (
            peer.closed
            or any(
                t.match.phase != "finished" and any(p.peer is peer for p in t.match.players)
                for t in self.ordinary_tables
            )
            or self.trade.status(peer) is not None
        )

    def table_for_peer(self, peer):
        return next(
            (
                t
                for t in self.ordinary_tables
                if peer in t.spectator_admissions
                or peer in t.match_reservations
                or any(p.peer is peer for p in t.match.players)
            ),
            None,
        )

    def table_for_channel(self, channel):
        return next(
            (t for t in self.ordinary_tables if t.table_channel.upper() == channel.upper()),
            None,
        )

    async def trade_updates(self, deliveries):
        # Commerce has already committed final Get and delayed deck edits.
        # Negotiation and disconnect notifications contain no durable changes.
        reloads = []
        for delivery in deliveries:
            peer = delivery.peer
            try:
                await peer.send(
                    service_notice(service_nick(TRADE_RAW), peer.nick, delivery.payload)
                )
            except (OSError, asyncio.TimeoutError):
                reloads.append(peer)
            if delivery.reload_profile:
                reloads.append(peer)
        # Rollback can follow client-side M4 reconciliation; force a fresh
        # profile on reconnect rather than continuing with divergent cards.
        for peer in set(reloads):
            await peer.close()

    def advance_auctions(self):
        if self.storage_failed is not None:
            raise OSError("state storage failed")
        now = int(self.auction_clock())
        if not 0 <= now <= 0xFFFFFFFF:
            raise AuctionError("clock exceeds native auction timestamp width")
        if not any(item.expires <= now for item in self.auction_ledger.listings()):
            return ()
        with auction_transaction(self) as auction:
            changes = auction.ledger.advance(now)
        if changes:
            self.log("auction_expiry", count=len(changes))
        return changes

    def register_hosted_account(self, key):
        """Expose saved hosted identities to bound transports, never raw logins."""
        if key not in self.account_specs:
            nick = key.encode("ascii")
            login = polid_to_string(polid_unscramble(nick)).encode("ascii")
            password = secrets.token_hex(8).encode("ascii")
            self.account_specs[key] = (login, password, self.economy.account_view(key).player_id)
            self.accounts[nick] = password
            self.connection_limit = max(8, len(self.accounts))

    def ensure_account(self, nick, *, name=None):
        key = nick.decode("ascii")
        existing = key in self.account_keys
        if existing and (name is None or self.economy.account_view(key).name == name
                         or nick in self.nicks or self.economy.is_busy(key)):
            return key
        with self.economy.edit_accounts((key,) if existing else ()) as staged:
            if not existing:
                character = self.account_specs[key][2]
                staged.provision(
                    key,
                    nn=player_id(nick),
                    player_id=character,
                    name=display_name(character),
                )
            if name is not None:
                staged.set_display_name(key, name)
            self.persist_changes(staged.snapshot()["accounts"], self.journal_snapshot())
        self.account_keys.add(key)
        return key

    def profile_file(self, account_key, name, *, owner=None):
        self.ensure_account(account_key.encode("ascii"))
        if name == METADATA_PATH:
            self.advance_rankings()
        data = self.ranking.file(account_key, name)
        if data is None:
            data = self.auction.file(account_key, name)
        if data is not None:
            return data
        if name == "b/g/ZL" or name.startswith("b/g/RL"):
            return ordinary_directory_files(self).get(name)
        if name == "b/g/PTL":
            raw = ROOM_RAW if owner is None or owner.raw_identity == 0 else owner.raw_identity
            table = self.ordinary_room_routes.get(service_nick(raw))
            if table is not None:
                return table.room.snapshot()
        data = self.event_rooms.profile_file(None if owner is None else owner.raw_identity, name)
        if data is not None:
            return data
        if name in ("b/g/TM0EventDataList", "b/g/TM0EventMemberList") and self.events.configs:
            return None
        return self.events.file(name)

    def delete_profile_file(self, account_key, name):
        self.ensure_account(account_key.encode("ascii"))
        return self.ranking.delete(account_key, name) or self.auction.delete(account_key, name)

    def advance_rankings(self):
        """Persist a weekly rollover before exposing its new ranking projection."""
        if not self.ratings.weekly:
            return False
        staged = self.ratings.stage_state(self.economy)
        if not staged.advance_week():
            return False
        self.persist_changes({}, {"ratings": staged.snapshot()})
        self.ratings.publish_state(staged)
        return True

    def snapshot(self):
        snapshot = self.economy.snapshot()
        snapshot.update(self.journal_snapshot())
        return snapshot

    def journal_snapshot(self):
        return dict(
            identities=self.identities.snapshot(),
            auction=self.auction_ledger.snapshot(),
            prize=self.prize.snapshot(),
            admin_audit=self.admin_audit.snapshot(),
            result_rewards=self.result_rewards.snapshot(),
            match_economy=self.match_economy.snapshot(),
            ratings=self.ratings.snapshot(),
            character_profiles=self.character_profiles.snapshot(),
            events=self.events.snapshot(),
            event_cards=self.event_cards.snapshot(),
            event_results=self.event_results.snapshot(),
        )

    def persist(self):
        with self.storage_write():
            if self.store is not None:
                self.store.save(self.snapshot())

    def persist_changes(self, accounts, services):
        with self.storage_write():
            if self.store is not None:
                self.store.save_changes(accounts, services)

    @contextmanager
    def storage_write(self):
        if self.storage_failed is not None:
            raise OSError("state storage failed; restart is required") from self.storage_failed
        try:
            yield
        except OSError as exc:
            # A failed COMMIT acknowledgement is ambiguous. Disconnect even
            # staged operations and reload committed state in a fresh server.
            self.storage_failed = exc
            if self.listener is not None:
                self.listener.close()
            for peer in list(self.sessions):
                peer.writer.close()
            raise

    async def room_updates(self, deliveries, *, source=None):
        if not deliveries:
            return
        completed = asyncio.get_running_loop().create_future()
        self.room_outbox.append((tuple(deliveries), completed, source or service_nick(ROOM_RAW)))
        if self.room_task is None:
            self.room_task = asyncio.create_task(self.drain_room_updates())
        if asyncio.current_task() is not self.room_task:
            await completed

    async def drain_room_updates(self):
        # A failed receiver's close may generate newer DD sequences. Queue them
        # behind the whole current broadcast instead of delivering recursively.
        try:
            while self.room_outbox:
                deliveries, completed, source = self.room_outbox.popleft()
                try:
                    for peer, payload in deliveries:
                        await self.deliver(
                            peer,
                            service_notice(source, peer.nick, payload),
                        )
                except Exception as exc:
                    if not completed.done():
                        completed.set_exception(exc)
                else:
                    if not completed.done():
                        completed.set_result(None)
        finally:
            self.room_task = None

    async def chat_updates(self, deliveries):
        for delivery in deliveries:
            await self.deliver(
                delivery.peer,
                service_notice(delivery.group, delivery.peer.nick, delivery.payload),
            )

    async def deliver(self, peer, message):
        """A failed recipient owns its disconnect; other senders stay connected."""
        try:
            await peer.send(message)
            return not peer.closed
        except (OSError, asyncio.TimeoutError) as exc:
            self.log("delivery_error", session=peer.id, reason=str(exc))
            await peer.close()
            return False

    def log(self, event, **fields):
        if self.log_file is None or self.log_error is not None:
            return
        record = {"time": time.time(), "event": event, **fields}
        try:
            write_record(self.log_file, record)
        except OSError as error:
            self.log_error = error
            report_failure("Game", error)

    async def start(self, port=51241):
        if self.storage_failed is not None:
            raise OSError("restart the server after correcting state storage")
        self.advance_rankings()
        self.listener = await asyncio.start_server(self.accept, "127.0.0.1", port, limit=MAX_LINE)
        for table in self.ordinary_tables:
            table.start_table_tasks()
        self.auction_task = asyncio.create_task(self.run_auctions())
        self.event_matches.start()
        port = self.listener.sockets[0].getsockname()[1]
        self.log("listening", address="127.0.0.1", port=port)
        return port

    async def run_auctions(self):
        while self.storage_failed is None:
            await asyncio.sleep(1)
            try:
                self.advance_auctions()
            except OSError as exc:
                self.log("auction_storage_error", reason=str(exc))

    async def accept(self, reader, writer, *, access=None):
        if self.storage_failed is not None or len(self.sessions) >= self.connection_limit:
            writer.close()
            return
        if access is not None:
            try:
                access.check()
                if access.account_key not in self.account_specs or access.game is not None:
                    raise ValueError("play session cannot open another game connection")
            except ValueError as error:
                self.log("connection_rejected", reason=str(error))
                writer.close()
                return
        self.counter += 1
        task = asyncio.current_task()
        self.tasks.add(task)
        session = Session(self, reader, writer, self.counter, access=access)
        if access is not None:
            access.game = session
        self.sessions.add(session)
        try:
            await session.run()
        except (
            ValueError,
            OSError,
            asyncio.TimeoutError,
            asyncio.IncompleteReadError,
        ) as error:
            self.log("connection_error", session=session.id, reason=str(error))
        finally:
            try:
                await session.close()
            finally:
                self.tasks.discard(task)

    async def stop(self):
        if self.listener:
            self.listener.close()
        if self.auction_task is not None:
            self.auction_task.cancel()
            await asyncio.gather(self.auction_task, return_exceptions=True)
            self.auction_task = None
        for table in self.ordinary_tables:
            await table.stop_table_tasks()
        await self.event_matches.stop()
        # Python 3.13 waits for accepted transports too: close those before
        # awaiting listener shutdown, including an otherwise healthy survivor.
        for session in list(self.sessions):
            await session.close()
        if self.listener:
            await self.listener.wait_closed()
        for table in self.ordinary_tables:
            if table.match_reservations:
                table.reset_match()
            # Cleanup can enqueue observer retirement after the first stop.
            await table.stop_table_tasks()
        if self.tasks:
            await asyncio.gather(*list(self.tasks), return_exceptions=True)
        if self.room_task is not None:
            await self.room_task
        self._close_log()

    def _close_log(self):
        stream, self.log_file = self.log_file, None
        if stream is not None:
            try:
                stream.close()
            except OSError as error:
                if self.log_error is None:
                    self.log_error = error
                    report_failure("Game", error)
