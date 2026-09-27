"""Independent ordinary-table runtime sharing the host account services."""

import asyncio
import uuid

from tmserver.admission_identity import validate_admission
from tmserver.economy_service import collection_points
from tmserver.identities import GAME_KEY, player_id, service_nick
from tmserver.match_departures import MatchDepartures
from tmserver.match_service import MatchService
from tmserver.match_service import decode as decode_match
from tmserver.match_service import packet as match_packet
from tmserver.match_transactions import match_transaction
from tmserver.match_watchdog import MatchWatchdog
from tmserver.result_rewards import ResultRewardError
from tmserver.result_stats import PracticeStats
from tmserver.spectator_service import SpectatorService
from tmserver.table_admission import rejected as reject_admission
from tmserver.table_control import channel_exit, viewer_state
from tmserver.table_preferences import parse_save
from tmserver.table_roster import TableRoster


class OrdinaryTable:
    """Own one match; access shared account and delivery services through host."""

    def initialize_table(
        self,
        host,
        room,
        *,
        match_mode="computer",
        match_player_count=2,
        computer_menu=False,
        match_start_fields=None,
        allow_cbc=False,
        card_prizes=False,
        allow_wagers=False,
        allow_departures=False,
        watchdog=None,
        spectator=None,
        flexible_seats=False,
        room_rules=None,
    ):
        self.host = host
        self.room = room
        self.table_raw = room.table_id ^ GAME_KEY
        self.table_channel = room.table_channel
        self.match_reservations = {}
        self.match_committed = set()
        self.match_lock = asyncio.Lock()
        self.match_task = None
        self.match_tasks = set()
        self.departures = MatchDepartures(self, enabled=allow_departures)
        self.match_mode = match_mode
        self.match_player_count = match_player_count
        self.flexible_seats = flexible_seats
        self.room_rules = room_rules
        self.computer_menu = computer_menu
        self.match_start_fields = match_start_fields
        self.allow_cbc = allow_cbc
        self.card_prizes = card_prizes
        self.allow_wagers = allow_wagers
        self.match_economy_round = None
        self.watchdog = watchdog if watchdog is not None else MatchWatchdog()
        self.watchdog_task = None
        self.spectators = spectator if spectator is not None else SpectatorService()
        self.spectator_admissions = set()
        self.spectator_senders = {}
        self.spectator_tasks = set()
        self.spectator_poll_task = None
        self.match = self.new_match()
        if room_rules is not None:
            self.empty_table_updates()
        self.spectators.reset(self.match)
        self.watchdog.observe(self.match, self.match)

    def new_match(self, *, computer_menu=None):
        self.table_roster = TableRoster()
        self.match_instance_id = uuid.uuid4().hex
        self.match_result_inputs = {}
        match = MatchService(
            mode=self.match_mode,
            player_count=self.match_player_count,
            computer_menu=self.computer_menu if computer_menu is None else computer_menu,
            seed=1,
            catalog=self.host.economy.catalog,
            start_fields=self.match_start_fields,
            allow_cbc=self.allow_cbc,
            card_prizes=self.card_prizes,
            flexible_seats=self.flexible_seats,
            room_rules=self.room_rules,
            allow_wagers=self.allow_wagers,
            allow_departures=self.departures.enabled,
            npc_nickname_id=0x1FF + self.room.table_number,
            second_npc_nickname_id=0x3FF + self.room.table_number,
            round_authorizer=(
                self.authorize_match_round if self.card_prizes or self.allow_wagers else None
            ),
            payout_provider=self.quote_match_result if self.allow_wagers else None,
            rating_provider=self.quote_match_rating if self.host.ratings.enabled else None,
            selected_card_validator=lambda peer, cards: (
                peer in self.match_reservations
                and self.host.economy.validate_match_selection(self.match_reservations[peer], cards)
            ),
        )
        if self.spectators.enabled:
            match.table_rules["in"] = 0
        return match

    def release_match_accounts(self):
        if not self.match_reservations:
            self.match_economy_round = None
            return
        if self.host.storage_failed is not None:
            # Recovery must reload the committed journal in a new writer. In
            # particular, do not refund a possibly committed stake in memory.
            for reservation in self.match_reservations.values():
                self.host.economy.release_match(reservation)
        else:
            keys = [peer.nick.decode("ascii") for peer in self.match_reservations]
            with match_transaction(self.host, keys) as state:
                reservations = dict(self.match_reservations)
                if (
                    self.match_economy_round is not None
                    and not state.match_economy.status(self.match_economy_round)["closed"]
                ):
                    tokens = state.match_economy.abort(
                        self.match_economy_round,
                        self.match_tokens(self.match),
                        state.auction_ledger,
                    )
                    self.refresh_result_contexts(self.match, tokens, state.result_rewards)
                    for seat, token in tokens.items():
                        reservations[self.match.players[seat].peer] = token
                for peer, reservation in reservations.items():
                    key = peer.nick.decode("ascii")
                    state.result_rewards.recover(key)
                    state.economy.release_match(reservation)
                    state.result_rewards.release_context(key)
        self.match_reservations.clear()
        self.match_economy_round = None

    def reset_match(self, *, computer_menu=None):
        self.match.cancel_pending()
        if self.match_task is not None and self.match_task is not asyncio.current_task():
            self.match_task.cancel()
        self.match_task = None
        self.release_match_accounts()
        self.match_committed.clear()
        self.match = self.new_match(computer_menu=computer_menu)
        self.spectator_admissions.clear()
        self.spectators.reset(self.match)
        self.queue_spectator((), self.match)
        self.watchdog.observe(self.match, self.match)

    def reset_table_updates(self, match):
        """Release one match generation and clear its published room state."""
        if match is not self.match:
            return []
        self.reset_match()
        return self.empty_table_updates() + self.room.clear_npcs()

    def empty_table_updates(self):
        rules = self.room_rules.as_dict() if self.room_rules is not None else None
        return self.room.set_table(0, 0, 0, rules)

    def match_tokens(self, match):
        return {
            seat: self.match_reservations[p.peer]
            for seat, p in enumerate(match.players)
            if p.peer is not None
        }

    def refresh_match_tokens(self, match, tokens):
        for seat, token in tokens.items():
            self.match_reservations[match.players[seat].peer] = token

    def refresh_result_contexts(self, match, tokens, rewards):
        for seat, token in tokens.items():
            key = match.players[seat].peer.nick.decode("ascii")
            if rewards.has_pending(key):
                raise ValueError("money refresh cannot replace pending result edits")
            try:
                rewards.refresh_reservation(key, token)
            except ResultRewardError:
                # Legitimate profile/option changes make a previous detached
                # editor obsolete. Never keep its old account capability.
                rewards.forget(key)

    def authorize_match_round(self, match, number, stake, *, player_count=None):
        round_id = f"{self.match_instance_id}:{number}"
        tokens = self.match_tokens(match)
        with match_transaction(self.host, (t.account.key for t in tokens.values())) as state:
            # The final all-yes Continue accepts the previous reward inventory
            # and deducts the next stake in the same durable transaction.
            for seat, token in tokens.items():
                key = token.account.key
                if state.result_rewards.has_pending(key):
                    tokens[seat] = state.result_rewards.commit(key)
            tokens = state.match_economy.begin(
                round_id,
                tokens,
                stake,
                mode=match.mode,
                player_count=match.player_count if player_count is None else player_count,
                money_enabled=self.allow_wagers,
            )
            self.refresh_result_contexts(match, tokens, state.result_rewards)
        self.refresh_match_tokens(match, tokens)
        self.match_economy_round = round_id
        return {
            seat: self.host.economy.match_account(token).balance for seat, token in tokens.items()
        }

    def quote_match_result(self, match, number, scores, ratings):
        round_id = f"{self.match_instance_id}:{number}"
        if round_id != self.match_economy_round:
            raise ValueError("result has no durable admitted round")
        self.match_result_inputs.setdefault(number, {})["ratings"] = tuple(ratings)
        return self.host.match_economy.quote(round_id, self.match_tokens(match), scores, ratings)

    def quote_match_rating(self, match, number, scores):
        seats = {
            seat: p.peer.nick.decode("ascii")
            for seat, p in enumerate(match.players)
            if p.peer is not None
        }
        changes = self.host.ratings.quote(
            f"{self.match_instance_id}:{number}",
            seats,
            scores,
            departed_seats=getattr(match, "departure_seats", ()),
        )
        inputs = self.match_result_inputs.setdefault(number, {})
        inputs.setdefault("ratings", tuple(p.rating for p in match.players))
        inputs["rating_changes"] = {seats[seat]: quote for seat, quote in changes.items()}
        return changes

    def settle_match_cards(self, match, round_id, transfers, state, tokens):
        """Stage ownership and result edits; the caller owns the durable commit."""
        participants = {
            seat: (tokens.get(seat), tuple(p.updated)) for seat, p in enumerate(match.players)
        }
        tokens, _ = state.match_economy.settle_cards(round_id, participants, transfers)
        state.match_economy.handoff(round_id, tokens, state.result_rewards)
        return tokens

    def commit_match_prizes(self, match):
        if not match.prize_commit_required:
            return []
        result = match.final_prizes()
        round_id = f"{self.match_instance_id}:{result.round_id}"
        if round_id != self.match_economy_round:
            raise ValueError("prize settlement has no durable admitted round")
        tokens = self.match_tokens(match)
        with match_transaction(self.host, (t.account.key for t in tokens.values())) as state:
            tokens = self.settle_match_cards(
                match, round_id, result.settlement.transfers, state, tokens
            )
        self.refresh_match_tokens(match, tokens)
        return match.confirm_prize_commit(result.round_id)

    def commit_match_results(self, match):
        rounds = []
        marks = []
        stats = {}
        for player in match.players:
            if player.peer is None:
                continue
            result = match.final_cards(player.peer)
            if result is None or (result.round_id, player.peer) in self.match_committed:
                continue
            reservation = self.match_reservations.get(player.peer)
            if reservation is None:
                raise ValueError("completed match has no account reservation")
            rounds.append((reservation, result.original, result.updated))
            stats[player.peer.nick.decode("ascii")] = result.stats
            marks.append((result.round_id, player.peer))
        if not rounds:
            return
        managed = match.card_prizes or match.allow_wagers
        inputs = self.match_result_inputs.get(marks[0][0], {})
        tokens = self.match_tokens(match)
        with match_transaction(self.host, stats) as state:
            refreshed = state.ratings.commit_growth(
                rounds,
                stats_by_key=stats,
                payouts_by_key=(
                    {key: value.current_reward for key, value in stats.items()}
                    if match.allow_wagers
                    else None
                ),
                rating_changes_by_key=inputs.get("rating_changes"),
                vs_player_game=match.mode == "versus",
            )
            for round_id, peer in marks:
                key = peer.nick.decode("ascii")
                token = refreshed[key]
                seat = next(i for i, p in enumerate(match.players) if p.peer is peer)
                tokens[seat] = token
                if not managed:
                    state.result_rewards.stage(
                        key,
                        token,
                        f"{self.match_instance_id}:{round_id}:{key}",
                        seat,
                        match.final_cards(peer).extras,
                    )
                    if match.players[seat].departed or peer.closed:
                        # No disconnected/native Quit editor will accept these.
                        # Queue any earned CBC cards durably for later collection.
                        state.result_rewards.recover(key)
            if managed:
                tokens, _ = state.match_economy.record_result(
                    self.match_economy_round,
                    tokens,
                    scores=match.observer_snapshot().scores,
                    ratings=inputs.get("ratings", tuple(p.rating for p in match.players)),
                    cbc={
                        seat: match.final_cards(p.peer).extras
                        for seat, p in enumerate(match.players)
                        if p.peer is not None
                    },
                )
                if not match.card_prizes:
                    tokens = self.settle_match_cards(
                        match, self.match_economy_round, (), state, tokens
                    )
        self.refresh_match_tokens(match, tokens)
        self.match_committed.update(marks)
        self.host.log("match_growth_committed", round=marks[0][0], players=len(marks))

    async def publish_match(self, match, responses, *, admitting=False):
        if match is not self.match:
            return
        self.watchdog.observe(match, match)
        self.commit_match_results(match)
        responses = list(responses) + self.commit_match_prizes(match)
        responses = self.table_roster.publish(match, responses)
        self.queue_spectator(
            self.spectators.publish(responses, match.observer_snapshot(), epoch=match), match
        )
        if match.phase != "finished":
            opponent = next((p for p in match.players if p.peer is None), None)
            human = next((p.peer for p in match.players if p.peer is not None), None)
            if opponent is not None and human is not None:
                updates = (
                    self.room.practice_opponents(
                        human, tuple(p for p in match.players if p.peer is None)
                    )
                    if match.computer_menu
                    else self.room.practice_opponent(human, opponent)
                )
                await self.publish_room(updates)
        if match.phase == "finished":
            updates = self.empty_table_updates()
            self.release_match_accounts()
            match.cancel_pending()
        elif match.phase != "lobby":
            updates = self.room.set_table(
                2,
                len(match.players),
                match.owner_id,
                match.game_rules,
                match.table_rules,
                computer_menu=match.computer_menu,
            )
        elif match.settings_applied:
            updates = self.room.set_table(
                1, len(match.players), match.owner_id, match.game_rules, match.table_rules
            )
        elif admitting:
            updates = self.room.set_table(
                0, len(match.players), match.owner_id, match.game_rules, match.table_rules
            )
        else:
            updates = []
        await self.publish_room(updates)
        for delivery in responses:
            if match is not self.match:
                break
            await self.deliver_table_packet(delivery.peer, delivery.payload)
        if match is self.match and match.phase == "finished":
            await self.publish_room(self.room.clear_npcs())

    async def dispatch_table(self, session, payload):
        aborted_peers = ()
        async with self.match_lock:
            if self.host.storage_failed is not None:
                raise OSError("state storage failed")
            if session.closed:
                return False
            msg = decode_match(payload)
            if msg is None:
                return False
            host = self.host
            if msg.primary == 0x41 and msg.subtype == 3:
                # Native GameM is a lobby inspection query. It must work for
                # nonmembers and while the requester is seated elsewhere.
                key = self.host.ensure_account(session.nick)
                identity = self.host.economy.account_view(key).nn
                fields = msg.sections.get("GameM")
                if (
                    msg.argument
                    or fields is None
                    or set(msg.sections) != {"GameM"}
                    or set(fields) - {"ID"}
                    or ("ID" in fields and fields["ID"].upper() != f"{identity:016X}")
                ):
                    raise ValueError("table member query must name its authenticated requester")
                response = match_packet(
                    0x41, 4, body=self.match._member_body(self.match.players, identity)
                )
                await self.deliver_table_packet(session, response)
                return True
            owner = host.table_for_peer(session)
            if owner is not None and owner is not self:
                raise ValueError("leave the current ordinary table before entering another")
            # A detached native deck-name editor can send after Continue/PART.
            # Its saved account context enforces ownership and idle-state checks.
            if msg.primary == 0x43 and msg.subtype in (0x10, 0x22):
                if session in self.spectator_admissions:
                    raise ValueError("observer cannot submit participant result edits")
                key = self.host.ensure_account(session.nick)
                with match_transaction(self.host, (key,)) as state:
                    replies = state.result_rewards.handle(key, payload)
                    token = state.result_rewards.reservation(key)
                if replies is not None:
                    if token is not None:
                        self.match_reservations[session] = token
                    return True
            admitting = (msg.primary == 1 and "Req" in msg.sections) or (
                msg.primary == 0x41 and msg.subtype in (1, 0x12)
            )
            if msg.primary == 0x41 and msg.subtype == 0x12 and self.match_mode != "computer":
                await self.deliver_table_packet(session, reject_admission("GameENC", full=False))
                return True
            registered = any(p.peer is session for p in self.match.players)
            viewing = viewer_state(msg)
            exiting_channel = channel_exit(msg)
            if not registered and session in self.spectator_admissions:
                if viewing is not None:
                    if viewing:
                        self.match.viewer_peers.add(session)
                    else:
                        self.match.viewer_peers.discard(session)
                    return True
                if exiting_channel:
                    self.detach_spectator(session)
                    return True
            if not registered and (viewing is not None or exiting_channel):
                return True
            if not registered and msg.primary == 0x41 and msg.subtype in (5, 0x0D):
                receipt = self.match.lobby_exit_receipts.get((session, msg.subtype))
                section = "GameQT" if msg.subtype == 5 else "GameExit"
                if receipt is not None and msg.sections == {section: {}}:
                    await self.deliver_table_packet(session, receipt)
                    return True
            preview_available = self.spectators.enabled and self.match.table_rules["in"] != 1
            if preview_available and not registered:
                watching = session in self.spectator_admissions
                active = self.match.phase in ("selection", "turn", "battle")
                if (
                    msg.primary == 1
                    and "Req" in msg.sections
                    and active
                    and self.table_channel in session.channels
                ):
                    fields = msg.sections["Req"]
                    account = self.host.economy.account_view(self.host.ensure_account(session.nick))
                    validate_admission(fields, "Req", nn=account.nn, name=account.name)
                    self.spectator_admissions.add(session)
                    reply = self.match.playack(self.match.players[0].peer)
                    self.queue_spectator([(session, reply)], self.match, tracked=False)
                    return True
                if msg.primary == 0x43 and (msg.subtype == 0x25 or watching):
                    if not watching or self.table_channel not in session.channels:
                        raise ValueError("watch request requires table-channel admission")
                    replies = self.spectators.handle(
                        session, payload, self.match.observer_snapshot(), epoch=self.match
                    )
                    if replies is not None:
                        self.queue_spectator(replies, self.match)
                        return True
                if watching and admitting:
                    raise ValueError("observer cannot claim a participant seat")
            if not registered and not admitting:
                # Discovery queries are read-only; they must not claim a seat.
                if msg.primary == 0x12:
                    response = match_packet(0x13, body=self.match.rules_body())
                elif msg.primary == 0x2D:
                    available = int(self.match.accepting_players)
                    join = int(
                        bool(available)
                        or (
                            preview_available
                            and self.match.phase in ("selection", "turn", "battle")
                        )
                    )
                    response = match_packet(
                        0x2E, body=f"@CheckJoinTable_Ans=/Join={join}/Mem={available}"
                    )
                else:
                    return False
                await self.deliver_table_packet(session, response)
                return True
            if not registered and msg.primary == 1 and session in self.match.kicked_peers:
                raise ValueError("removed members must explicitly re-enter the table")
            # The wire carries no table generation nonce. Require the existing
            # member to leave before any new admission can replace its table.
            if registered and self.match.phase == "finished" and admitting:
                return True
            key = self.host.ensure_account(session.nick)
            native_computer_entry = (
                self.match_mode == "computer"
                and not self.match.computer_menu
                and self.match.phase == "lobby"
                and not self.match.players
                and msg.primary == 0x41
                and msg.subtype == 0x12
            )
            if native_computer_entry:
                self.reset_match(computer_menu=True)
            match = self.match
            account = self.host.economy.account_view(key)
            if admitting:
                section = (
                    "Req" if msg.primary == 1 else "GameENC" if msg.subtype == 0x12 else "GameEN"
                )
                fields = msg.sections.get(section)
                if fields is None:
                    raise ValueError("admission requires its matching section")
                validate_admission(fields, section, nn=account.nn, name=account.name)
                capacity = 1 if self.match_mode == "computer" else self.match_player_count
                if not registered and not match.accepting_players:
                    # A valid request can race the last seat or game start. The
                    # mapped failure paths keep the transport usable; they
                    # must not be treated as malformed input and disconnect it.
                    # Use the recovered full/not-ready/generic text paths;
                    # zero and cancellation replies give misleading messages.
                    response = reject_admission(
                        section, full=match.phase == "lobby" and len(match.players) >= capacity
                    )
                    await self.deliver_table_packet(session, response)
                    self.host.log(
                        "table_admission_rejected",
                        session=session.id,
                        reason="unavailable",
                        phase=match.phase,
                    )
                    return True
            accepting_result = (
                registered
                and match.phase == "result"
                and msg.primary == 0x43
                and msg.subtype == 0x11
                and not next(p for p in match.players if p.peer is session).departed
            )
            aborting_multiplayer = (
                registered
                and msg.primary == 0x43
                and msg.subtype in (5, 0x27)
                and match.phase in ("selection", "turn", "battle", "result")
                and not match.departure_supported(session)
                and sum(p.peer is not None and not p.departed for p in match.players) > 1
            )
            revision = match.settings_revision
            old_players = list(match.players)
            old_owner = match.owner_peer
            try:
                # Validate durable round-trip constraints before the transient
                # match settings handler can change its saved-default view.
                preference_update = parse_save(payload) if msg.primary == 0x24 else None
                match.register(
                    session,
                    character_id=account.player_id,
                    name=account.name,
                    nickname_id=player_id(session.nick),
                    balance=account.balance,
                    points=collection_points(account.cards),
                    collection_size=len(account.cards),
                    result_stats=PracticeStats.from_profile(self.host.economy.profile_bytes(key)),
                )
                responses = match.handle(session, payload)
                if aborting_multiplayer and match.phase == "finished":
                    # Unsupported Quit/Break ends the engine without a terminal
                    # notice to the other humans. Reconnect them after the
                    # existing durable refund/recovery path has published.
                    aborted_peers = tuple(p.peer for p in old_players if p.peer is not None)
                    self.departures.abort(match)
                if preference_update is not None and responses is not None:
                    with self.host.commerce.edit_account(key) as economy:
                        economy.save_table_preferences(key, **preference_update)
                if accepting_result:
                    token = self.match_reservations[session]
                    with match_transaction(self.host, (key,)) as state:
                        if state.result_rewards.has_pending(key):
                            token = state.result_rewards.commit(key)
                        if responses is not None and "PR" in msg.sections["Continue"]:
                            state.result_rewards.save_continue_privacy(
                                key, int(msg.sections["Continue"]["PR"])
                            )
                    self.match_reservations[session] = token
                if (
                    registered
                    and msg.primary == 0x43
                    and msg.subtype == 7
                    and match.phase in ("selection", "turn")
                ):
                    self.host.result_rewards.forget(key)
                if not registered:
                    self.match_reservations[session] = self.host.economy.reserve_match(key)
                    self.host.result_rewards.forget(key)
            except (ValueError, OSError):
                if not registered:
                    match.players = old_players
                    match.owner_peer = old_owner
                    if native_computer_entry:
                        self.reset_match()
                raise
            if responses is None:
                return False
            # A pre-start host kick releases only the removed account. No round
            # escrow exists before the start handshake; other seats stay bound.
            for old_player in old_players:
                removed = old_player.peer
                if removed is not None and not any(p.peer is removed for p in match.players):
                    token = self.match_reservations.pop(removed, None)
                    if token is not None:
                        self.host.economy.release_match(token)
                    self.host.result_rewards.release_context(removed.nick.decode("ascii"))
            if msg.primary == 0x41 and msg.subtype in (1, 0x12):
                self.table_roster.admission(session)
            if match.settings_revision != revision:
                self.table_roster.ready.add(session)
                notices = match.admission_notice(session)
                responses += notices
            await self.publish_match(
                match, responses, admitting=admitting or len(old_players) != len(match.players)
            )
            self.host.log(
                "match_service", session=session.id, replies=len(responses), state=match.snapshot()
            )
            self.schedule_match_work(match)
        if aborted_peers:
            # Session.close can reset a table and await notifications. Never run
            # it under match_lock or close a peer that has since rebound.
            await self.close_released_peers(match, aborted_peers)
            async with self.match_lock:
                if self.match is match:
                    await self.publish_room(self.reset_table_updates(match))
        return True

    async def roster_presence(self, session, room_id):
        """Accepted room PD is the guest's native post-GameEA barrier."""
        async with self.match_lock:
            if session.closed or room_id != self.room.room_id:
                return
            match = self.match
            record = self.room.members.get(session)
            if (
                match.phase != "lobby"
                or not any(p.peer is session for p in match.players)
                or record is None
                or record[1][0x38 + 23 : 0x38 + 25] != b"DB"
            ):
                return
            self.table_roster.ready.add(session)
            await self.publish_match(match, [])

    def schedule_match_work(self, match):
        if match is not self.match or not match.pending_work:
            return
        if self.match_task is not None and not self.match_task.done():
            return
        self.match_task = asyncio.create_task(self.run_match_work(match))
        self.match_tasks.add(self.match_task)
        self.match_task.add_done_callback(self.match_tasks.discard)

    async def check_match_timeout(self):
        async with self.match_lock:
            match = self.match
            timeout = self.watchdog.expired(match, match)
            if timeout is None:
                return None
            peers = [p.peer for p in match.players if p.peer is not None]
            self.host.log(
                "match_timeout",
                reason=timeout.reason,
                phase=timeout.phase,
                sequence=timeout.sequence,
                round=timeout.round_id,
                sessions=[p.id for p in timeout.missing_peers],
            )
            # Abort incomplete work before releasing the lock. No result is
            # invented; reconnect loads the last durable completed round.
            self.departures.abort(match)
            match.cancel_pending()
        for peer in peers:
            # Closing the first peer resets this table. A replacement session
            # cannot be mistaken for another peer from the expired generation.
            if self.host.nicks.get(peer.nick) is peer:
                await peer.close()
        if self.match is match:
            async with self.match_lock:
                await self.publish_room(self.reset_table_updates(match))
        return timeout

    async def run_match_watchdog(self):
        while self.host.storage_failed is None:
            await asyncio.sleep(1)
            await self.check_match_timeout()
            try:
                if self.host is self:
                    self.host.advance_auctions()
            except OSError as exc:
                self.host.log("auction_storage_error", reason=str(exc))

    def queue_spectator(self, deliveries, epoch, *, tracked=True):
        """Enqueue without awaiting observer I/O while participant state is locked."""
        if not self.spectators.enabled:
            return
        for delivery in deliveries:
            peer, payload = (
                (delivery.peer, delivery.payload) if hasattr(delivery, "peer") else delivery
            )
            if peer.closed:
                continue
            sender = self.spectator_senders.get(peer)
            if sender is None:
                queue = asyncio.Queue(maxsize=8)
                task = asyncio.create_task(self.send_spectator(peer, queue))
                self.spectator_tasks.add(task)
                task.add_done_callback(self.spectator_tasks.discard)
                self.spectator_senders[peer] = (queue, task)
            else:
                queue = sender[0]
            try:
                queue.put_nowait((epoch, payload, tracked))
            except asyncio.QueueFull:
                self.spectators.disconnect(peer)
                self.defer_spectator_close(peer, "writer_backlog_limit")
        for peer, reason in self.spectators.take_evicted():
            self.defer_spectator_close(peer, reason)

    def defer_spectator_close(self, peer, reason):
        self.host.log("spectator_closed", session=peer.id, reason=reason)
        task = asyncio.create_task(peer.close())
        self.spectator_tasks.add(task)
        task.add_done_callback(self.spectator_tasks.discard)

    async def send_spectator(self, peer, queue):
        sender = (queue, asyncio.current_task())
        try:
            while not peer.closed and self.spectator_senders.get(peer) == sender:
                epoch, payload, tracked = await queue.get()
                if epoch is not self.match or (tracked and not self.spectators.active(peer)):
                    continue
                # Own failure cleanup so retired I/O cannot close a new admission.
                source = service_nick(self.table_raw)
                await peer.send(b":" + source + b"!p@local NOTICE " + peer.nick + b" :" + payload)
                if peer.closed:
                    return
                if tracked:
                    async with self.match_lock:
                        if self.spectator_senders.get(peer) == sender:
                            self.spectators.record_sent(peer, payload, epoch=epoch)
        except (ValueError, OSError, asyncio.TimeoutError) as exc:
            self.host.log("spectator_delivery_error", session=peer.id, reason=str(exc))
            if self.spectator_senders.get(peer) == sender:
                await peer.close()
        finally:
            if self.spectator_senders.get(peer) == sender:
                self.spectator_senders.pop(peer, None)

    async def run_spectator_poll(self):
        while self.host.storage_failed is None:
            await asyncio.sleep(0.1)
            async with self.match_lock:
                match = self.match
                self.queue_spectator(
                    self.spectators.poll(match.observer_snapshot(), epoch=match), match
                )

    def detach_spectator(self, peer):
        self.spectator_admissions.discard(peer)
        self.match.viewer_peers.discard(peer)
        self.spectators.disconnect(peer)
        sender = self.spectator_senders.pop(peer, None)
        if sender is not None and sender[1] is not asyncio.current_task():
            sender[1].cancel()

    async def run_match_work(self, match):
        try:
            while match is self.match and match.pending_work:
                # Yield between bounded original battle continuations, allowing
                # disconnects and socket traffic to run even with repeated ties.
                await asyncio.sleep(0)
                async with self.match_lock:
                    if match is not self.match or self.host.storage_failed is not None:
                        break
                    responses = match.advance(max_battles=1)
                    await self.publish_match(match, responses)
        except (ValueError, OSError, asyncio.TimeoutError) as exc:
            self.host.log("match_work_error", reason=str(exc))
            self.departures.abort(match)
            for player in list(match.players):
                if player.peer is not None:
                    await player.peer.close()
            async with self.match_lock:
                await self.publish_room(self.reset_table_updates(match))
        finally:
            if self.match_task is asyncio.current_task():
                self.match_task = None

    def table_release_updates(self, peer):
        if not any(player.peer is peer for player in self.match.players):
            reservation = self.match_reservations.pop(peer, None)
            if reservation is not None:
                self.host.economy.release_match(reservation)
            return []
        return self.reset_table_updates(self.match)

    async def release_table(self, peer):
        self.detach_spectator(peer)
        if self.departures.request(peer):
            return
        match = self.match
        affected = self.table_dependents(peer)
        await self.publish_room(self.table_release_updates(peer))
        await self.close_released_peers(match, affected)

    async def close_released_peers(self, match, peers):
        """Reconnect old table peers unless a new admission now owns them."""
        for peer in peers:
            owner = self.host.table_for_peer(peer)
            if self.host.nicks.get(peer.nick) is not peer or (
                owner is not None and (owner is not self or self.match is not match)
            ):
                continue
            # Failed cleanup may retain the old reservation. Otherwise a busy
            # account belongs to another service. Event observers hold no
            # reservation, so check their admission separately.
            retained = self.match is match and peer in self.match_reservations
            events = self.host.event_matches
            if (
                events.coordinator.participant(peer)
                or peer in events.spectators.admissions
                or (not retained and self.host.economy.is_busy(peer.nick.decode("ascii")))
            ):
                continue
            await peer.close()

    def table_dependents(self, peer):
        # Continue can finish the transient engine before its result inventory
        # is accepted. A rejected overflow or failed commit still owns table
        # reservations and must reconnect every participant after recovery.
        if (self.match.phase == "finished" and not self.match_reservations) or not any(
            p.peer is peer for p in self.match.players
        ):
            return []
        return [p.peer for p in self.match.players if p.peer is not None and p.peer is not peer]

    async def deliver_table_packet(self, peer, payload):
        source = service_nick(self.table_raw)
        message = b":" + source + b"!p@local NOTICE " + peer.nick + b" :" + payload
        return await self.host.deliver(peer, message)

    async def publish_room(self, deliveries):
        await self.host.room_updates(deliveries, source=service_nick(self.room.room_id ^ GAME_KEY))

    def start_table_tasks(self):
        self.watchdog_task = asyncio.create_task(self.run_match_watchdog())
        if self.spectators.enabled:
            self.spectator_poll_task = asyncio.create_task(self.run_spectator_poll())

    async def stop_table_tasks(self):
        await self.departures.stop()
        tasks = [t for t in (self.watchdog_task, self.spectator_poll_task) if t is not None]
        tasks += list(self.match_tasks) + list(self.spectator_tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self.watchdog_task = self.spectator_poll_task = None
