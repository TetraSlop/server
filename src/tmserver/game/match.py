"""Match state machine for two- and three-player games.

Covers admission, card selection, turns, battles, growth, special boards and
results; wagers and prizes go through optional callbacks. Each human acknowledges
a turn's battle, growth and effects before the next. The caller owns IRC identity
and framing and sends each Delivery from one table-service identity. Layout:
TetraPackets flows/match-admission.md, flows/tables.md,
flows/results-and-prizes.md and irc/G/43.md.
"""

import re
from typing import Callable

from tmserver.cards.catalog import Catalog
from tmserver.game import special_rules as special
from tmserver.game.ai import Personality
from tmserver.game.backends import RulesBackend
from tmserver.game.boards import generate_board
from tmserver.game.departure_rules import next_turn, result_ranking, result_slots
from tmserver.game.errors import MatchError, RulesExecutionError
from tmserver.game.models import (
    DEFAULT_GAME_RULES,
    DEFAULT_TABLE_RULES,
    BoardCard,
    Card,
    Delivery,
    FinalCards,
    FinalPrizes,
    ObserverPlayer,
    ObserverSnapshot,
    Player,
)
from tmserver.game.opponents import OPPONENTS, OpponentError, opponent, prepare_opponents
from tmserver.game.packets import decode, numbers, packet
from tmserver.game.prizes import (
    PrizeCard,
    PrizeError,
    PrizeLedger,
    choose_computer_prize,
)
from tmserver.game.rules import CRTLCG, GrowthBaseline
from tmserver.game.original.runtime import NativeRuntime
from tmserver.protocol.admission import validate_admission
from tmserver.rankings.stats import PracticeStats
from tmserver.rooms.metadata import TableMetadataError, table_info
from tmserver.rooms.rules import RoomRules

DIRECTIONS = ((0, -1), (1, -1), (1, 0), (1, 1), (0, 1), (-1, 1), (-1, 0), (-1, -1))


class MatchService:
    def __init__(
        self,
        mode="computer",
        seed=1,
        selected_card_validator: Callable | None = None,
        ai_cards=None,
        catalog=None,
        draw: Callable | None = None,
        ai_personality=Personality(),
        start_fields=None,
        allow_cbc=False,
        computer_menu=False,
        player_count=2,
        card_prizes=False,
        allow_wagers=False,
        round_authorizer=None,
        payout_provider=None,
        rating_provider=None,
        allow_departures=False,
        npc_nickname_id=0x200,
        flexible_seats=False,
        second_npc_nickname_id=None,
        room_rules=None,
        rules_backend: RulesBackend | None = None,
    ):
        if mode not in ("computer", "versus"):
            raise ValueError("mode must be computer or versus")
        self.mode = mode
        if rules_backend is not None and any(
            not callable(getattr(rules_backend, name, None))
            for name in ("calculate_battle", "choose_move", "choose_special_move", "grow_card", "choose_target")
        ):
            raise ValueError("rules backend requires battle, growth, target, ordinary and special AI calculations")
        self.rules_backend = rules_backend if rules_backend is not None else NativeRuntime()
        self._rules_failure = None
        if type(npc_nickname_id) is not int or not 0 < npc_nickname_id < 1 << 64:
            raise ValueError("NPC nickname identity must be a nonzero uint64")
        self.npc_nickname_id = npc_nickname_id
        if second_npc_nickname_id is None:
            second_npc_nickname_id = npc_nickname_id % 0xFFFFFFFFFFFFFFFF + 1
        if (
            type(second_npc_nickname_id) is not int
            or not 0 < second_npc_nickname_id < 1 << 64
            or second_npc_nickname_id == npc_nickname_id
        ):
            raise ValueError("second NPC nickname identity must be a distinct nonzero uint64")
        self.second_npc_nickname_id = second_npc_nickname_id
        if type(card_prizes) is not bool:
            raise ValueError("card_prizes must be boolean")
        self.card_prizes = card_prizes
        if type(allow_wagers) is not bool:
            raise ValueError("allow_wagers must be boolean")
        if any(
            f is not None and not callable(f)
            for f in (round_authorizer, payout_provider, rating_provider)
        ):
            raise ValueError("economic hooks must be callable")
        if allow_wagers and (round_authorizer is None or payout_provider is None):
            raise ValueError("wagers require durable round authorization and a payout provider")
        self.allow_wagers = allow_wagers
        if room_rules is not None and not isinstance(room_rules, RoomRules):
            raise ValueError("room_rules must be RoomRules or None")
        if room_rules is not None and not allow_wagers and (room_rules.bm or room_rules.du):
            raise ValueError("room wagers/double-up require economic hooks")
        self.room_rules = room_rules
        self.round_authorizer = round_authorizer
        self.payout_provider = payout_provider
        self.rating_provider = rating_provider
        if type(allow_departures) is not bool:
            raise ValueError("allow_departures must be boolean")
        self.allow_departures = allow_departures
        self.authorized_round_id = None
        self.round_stake = 0
        if (
            type(player_count) is not int
            or player_count not in (2, 3)
            or (player_count == 3 and mode != "versus" and not computer_menu)
        ):
            raise ValueError("three-player mode requires humans or the original computer menu")
        if (
            type(flexible_seats) is not bool
            or flexible_seats
            and (mode != "versus" or player_count != 3 or start_fields is not None)
        ):
            raise ValueError(
                "automatic seats require a three-seat versus table with a generated board"
            )
        self.flexible_seats = flexible_seats
        self.seat_capacity = 1 if mode == "computer" else player_count
        self._seats_frozen = False
        self.player_count = player_count
        self.board_width = player_count + 2
        self.board_size = self.board_width**2
        self.last_sequence = 5 * player_count - 1
        if type(computer_menu) is not bool or (computer_menu and mode != "computer"):
            raise ValueError("original computer menu requires computer mode")
        self.computer_menu = computer_menu
        self.npc_id = None
        self.computer_view = 0  # Original P+D9 menu preference, distinct from gs/P+E9.
        self.rng = CRTLCG(seed)
        self.draw = draw if draw is not None else self.rng
        if catalog is None:
            catalog = Catalog()
        self.catalog = catalog
        self.baselines = tuple(
            GrowthBaseline.from_catalog(row, catalog.prices) for row in catalog.cards
        )
        if not isinstance(ai_personality, Personality):
            raise ValueError("ai_personality must be a native_ai.Personality")
        self.ai_personality = ai_personality
        self.explicit_start_fields = start_fields is not None
        self.start_board = special.start_board(
            (0,) * self.board_size if start_fields is None else start_fields
        )
        if len(self.start_board.cells) != self.board_size:
            raise ValueError("board shape must match the player count")
        if type(allow_cbc) is not bool:
            raise ValueError("allow_cbc must be boolean")
        self.allow_cbc = allow_cbc
        if 18 in self.start_board.fields and not allow_cbc:
            raise ValueError("CBC materialization requires generated-card reward persistence")
        if self.start_board.blocks > self.board_size - 5 * player_count:
            raise ValueError("configured board must leave five placement cells per player")
        self.special_flags = dict(
            st=int(any(3 <= f <= 5 for f in self.start_board.fields)),
            cb=int(any(f in (6, 7, 8, 17, 18) for f in self.start_board.fields)),
            ca=int(any(9 <= f <= 16 for f in self.start_board.fields)),
        )
        if room_rules is not None:
            configured = room_rules.as_dict()
            if self.explicit_start_fields and any(
                configured[key] != value for key, value in self.special_flags.items()
            ):
                raise ValueError("room rules must match the explicitly configured board")
            self.special_flags = {key: configured[key] for key in self.special_flags}
        self.selected_card_validator = selected_card_validator
        if ai_cards is None:
            ai_cards = [
                Card(i, 40 + i * 3, 0, 35, 30, 1, a) for i, a in enumerate((1, 4, 16, 64, 85))
            ]
        # Economy Card has the same attributes but tuple-valued full()/reduced().
        self.ai_cards = tuple(Card.from_card(c) for c in ai_cards)
        if len(self.ai_cards) != 5:
            raise ValueError("five AI cards required")
        self.players = []
        self.phase = "lobby"
        self.sequence = 0
        self.active = 0
        self.board = [None] * self.board_size
        self.acks = set()
        self.pending_move = None
        self.source = None
        self.markers = {}
        self.targets = []
        self.ready = set()
        self.continues = {}
        self.completed_matches = 0
        self.owner_peer = None
        self.game_rules = dict(DEFAULT_GAME_RULES) if room_rules is None else room_rules.as_dict()
        self.game_rules.update(self.special_flags)
        self.table_rules = dict(DEFAULT_TABLE_RULES)
        self.settings_applied = False
        self.settings_revision = 0
        self.saved_preferences = {}
        self._pending_battle = None
        self._final_snapshots = {}
        self._final_order = ()
        self._start_offered = False
        self._terminal_replies = set()
        self.kicked_peers = set()
        self._kick_receipts = {}
        self.lobby_exit_receipts = {}
        self.viewer_peers = set()
        self._current_turn_packet = None
        self._prizes = None
        self._final_prizes = None
        self._prizes_confirmed = False
        self._held_prize_deliveries = []
        self._prize_tclist_owners = ()
        self._computer_prize_packets = ()
        self._departures = []
        self._departure_result_peers = set()
        self._result_sequence = None

    def _shared_r(self):
        """Server choice: eight CRT low bytes, in transmitted order."""
        return tuple(self.draw() & 255 for _ in range(8))

    def _backend_call(self, operation, *args, **kwargs):
        if self._rules_failure is not None:
            raise self._rules_failure
        try:
            return getattr(self.rules_backend, operation)(*args, **kwargs)
        except Exception as error:
            # A failed native call can consume randomness. End the round before
            # adapter cleanup; neither retry nor departure substitution is safe.
            self.phase = "finished"
            self.pending_move = None
            self.cancel_pending()
            self._rules_failure = RulesExecutionError(f"Rules backend {operation} failed: {error}")
            raise self._rules_failure from error

    def _effect_cells(self):
        cells = []
        for index, c in enumerate(self.board):
            if c is None:
                cells.append(special.Cell())
                continue
            cells.append(
                special.Cell(
                    model=c.card.model if c.card is not None else c.tile_id,
                    card=c.card,
                    base_owner=c.original_owner if c.base_owner is None else c.base_owner,
                    owner=c.owner,
                    arrows=c.card.arrows if c.card is not None else c.tile_arrows,
                    property=c.property,
                    modifier=c.modifier,
                    direction=c.direction,
                    marker=self.markers.get(index, 0),
                    identity=(
                        (c.original_owner, c.original_slot) if c.original_slot is not None else None
                    ),
                )
            )
        return tuple(cells)

    def _adopt_effects(self, cells):
        self.board = [
            (
                None
                if c.model == 0xFFFF
                else BoardCard(
                    c.card,
                    c.owner,
                    c.identity[0] if c.identity is not None else c.base_owner,
                    c.identity[1] if c.identity is not None else None,
                    c.property,
                    c.modifier,
                    c.model if c.card is None else None,
                    c.direction,
                    c.arrows if c.card is None else 0,
                    c.base_owner,
                )
            )
            for c in cells
        ]
        self.markers = {i: c.marker for i, c in enumerate(cells) if c.marker}

    @staticmethod
    def _r_text(values):
        return "|".join(map(str, values))

    @staticmethod
    def _choose_cards(player, cards):
        player.selected = tuple(cards)
        player.hand = list(cards)
        player.hand_slots = list(range(len(cards)))
        player.updated = list(cards)

    def final_cards(self, peer):
        """Result available before ResultData delivery; root commits it idempotently."""
        self._seat(peer)
        return self._final_snapshots.get(peer)

    @property
    def prize_choice(self):
        return None if self._prizes is None else self._prizes.pending

    def final_prizes(self):
        """Immutable completed round ownership; survives Continue until selection."""
        return self._final_prizes

    @property
    def prize_commit_required(self):
        return (
            self.phase == "result" and self._final_prizes is not None and not self._prizes_confirmed
        )

    def confirm_prize_commit(self, round_id):
        """Root calls only after atomic durable ownership/reward commit.

        Releases held ResultData (automatic rounds) or last GetSelect (chosen
        rounds) once. No financial or inventory mutation occurs in this class.
        """
        if (
            type(round_id) is not int
            or self._final_prizes is None
            or self._final_prizes.round_id != round_id
            or self.phase != "result"
        ):
            raise MatchError("prize confirmation is not for the current completed round")
        if self._prizes_confirmed:
            return []
        self._prizes_confirmed = True
        out = self._held_prize_deliveries
        self._held_prize_deliveries = []
        return out

    def _clear_prizes(self):
        self._prizes = None
        self._final_prizes = None
        self._prizes_confirmed = False
        self._held_prize_deliveries = []
        self._prize_tclist_owners = ()
        self._computer_prize_packets = ()

    def _prize_gate(self, out):
        if self.prize_commit_required:
            self._held_prize_deliveries.extend(out)
            return []
        return out

    def _prepare_prizes(self):
        if not self.card_prizes:
            return
        owners = {
            (c.original_owner, c.original_slot): c.owner
            for c in self.board
            if c is not None
            and c.original_owner < self.player_count
            and c.original_slot is not None
        }
        hands = tuple(
            tuple(
                PrizeCard(slot, p.updated[slot], owners.get((seat, slot), seat))
                for slot in self._final_order[seat]
            )
            for seat, p in enumerate(self.players)
        )
        self._prize_tclist_owners = tuple(tuple(c.current_owner for c in hand) for hand in hands)
        self._prizes = PrizeLedger(self._scores(), hands)
        self._prizes_confirmed = False
        self._held_prize_deliveries = []
        self._computer_prize_packets = self._advance_computer_prizes()

    def _advance_computer_prizes(self):
        """Resolve consecutive CPU choices until a human or settlement is reached."""
        computer_packets = []
        while self._prizes.pending is not None:
            choice = self._prizes.pending
            if self.players[choice.winner].peer is not None:
                break
            selected = choose_computer_prize(choice, self.draw)
            self._prizes.select(choice.winner, selected, choice_id=choice.choice_id)
            computer_packets.append(
                packet(0x43, 0x0F, choice.winner, f"@GetSelect=/E=0/H={selected}")
            )
        if self._prizes.complete:
            self._final_prizes = FinalPrizes(self.completed_matches, self._prizes.settlement())
        return tuple(computer_packets)

    def _get_select(self, peer, msg):
        if not self.card_prizes:
            return None
        if self.phase != "result" or self._prizes is None:
            return []
        choice = self._prizes.pending
        if choice is None:
            return []
        seat = self._seat(peer)
        if peer not in self.ready or msg.argument != seat:
            raise MatchError("prize selection requires this participant's TCList and seat")
        hand = numbers(msg.sections["GetSelect"].get("H"), 1)[0]
        try:
            self._prizes.select(seat, hand, choice_id=choice.choice_id)
        except PrizeError as exc:
            raise MatchError(str(exc)) from exc
        out = self._emit(packet(0x43, 0x0F, seat, f"@GetSelect=/E=0/H={hand}"))
        for payload in self._advance_computer_prizes():
            out += self._emit(payload)
        return self._prize_gate(out)

    def observer_snapshot(self):
        """Immutable state copy; adapter holds its match lock around this call.

        No RNG, acknowledgement or delivery is generated. This does not prove
        a safe mid-battle WatchInfo bootstrap or passive-observer pacing.
        """
        players = tuple(
            ObserverPlayer(
                p.peer,
                p.name,
                p.nickname_id,
                p.character_id,
                p.rating,
                p.points,
                tuple(p.hand),
                tuple(p.hand_slots),
                p.selected,
                p.departed,
                p.substitute,
                (p.npc_id if self.computer_menu and p.peer is None and not p.departed else 0),
            )
            for p in self.players
        )
        return ObserverSnapshot(
            self.phase,
            self.completed_matches + int(self.phase in ("selection", "turn", "battle")),
            self.sequence,
            self.active,
            players,
            tuple(self.game_rules.items()),
            self.start_board.fields,
            self._effect_cells(),
            tuple(self._scores()),
            self._current_turn_packet if self.phase in ("turn", "battle") else None,
            self.table_rules["in"],
        )

    @property
    def pending_work(self):
        return self.phase == "battle" and self._pending_battle is not None

    def cancel_pending(self):
        """Discard scheduled combat work when the adapter discards this table."""
        self._pending_battle = None

    def advance(self, max_battles=1):
        """Run at most 1..16 battle packets per human, then yield to the adapter."""
        if type(max_battles) is not int or not 1 <= max_battles <= 16:
            raise ValueError("max_battles must be in 1..16")
        out = []
        for _ in range(max_battles):
            if not self.pending_work:
                break
            target, self._pending_battle = self._pending_battle, None
            out += self._battle(target)
        return out

    def register(
        self,
        peer,
        character_id=1,
        name=b"LocalPlayer",
        nickname_id=1,
        rating=300,
        balance=10000,
        points=1,
        result_stats=None,
        collection_size=0,
    ):
        if peer is None:
            raise MatchError("None is reserved for the computer seat")
        for i, p in enumerate(self.players):
            if p.peer == peer:
                return i
        if not self.accepting_players:
            raise MatchError("table full or match already running")
        if not 0 < character_id <= 0xFFFFFFFF or any(
            p.character_id == character_id for p in self.players
        ):
            raise MatchError("each human needs a distinct profile character ID")
        if isinstance(name, str):
            name = name.encode("ascii")
        if not name or len(name) > 16 or any(c < 32 or c > 126 for c in name):
            raise MatchError("name must contain 1..16 printable ASCII bytes")
        if not 0 <= nickname_id <= 0xFFFFFFFFFFFFFFFF:
            raise MatchError("nickname identity outside uint64")
        if type(balance) is not int or not 0 <= balance <= 99_999_999:
            raise MatchError("balance must be an authenticated bounded money amount")
        if result_stats is None:
            result_stats = PracticeStats(rating=rating)
        if not isinstance(result_stats, PracticeStats):
            raise MatchError("result_stats must be an authenticated PracticeStats snapshot")
        if type(collection_size) is not int or not 0 <= collection_size <= 65535:
            raise MatchError("collection size must fit the original WORD")
        if type(points) is not int or not 0 <= points <= 65535:
            raise MatchError("collection points must fit the original WORD")
        player = Player(
            peer,
            character_id,
            name[:15],  # Client labels need a terminator; account identity keeps all 16 bytes.
            nickname_id,
            result_stats.rating,
            balance,
            points,
            result_stats=result_stats,
            collection_size=collection_size,
        )
        self._check_playack(self.players + [player])
        self.players.append(player)
        self._kick_receipts.pop(nickname_id, None)
        return len(self.players) - 1

    def _seat(self, peer):
        for i, p in enumerate(self.players):
            if p.peer == peer:
                return i
        raise MatchError("register the peer before dispatching table traffic")

    def forget_control_peer(self, peer):
        """Closed transport objects cannot retry or re-enter this table."""
        self.kicked_peers.discard(peer)
        self.viewer_peers.discard(peer)
        for key in tuple(self.lobby_exit_receipts):
            if key[0] is peer:
                self.lobby_exit_receipts.pop(key)
        for target, owner in tuple(self._kick_receipts.items()):
            if owner is peer:
                self._kick_receipts.pop(target)

    def reset(self):
        """Reuse the published table after all clients have left its old match."""
        if self.phase not in ("finished", "lobby"):
            raise MatchError("cannot reset an active match")
        self.players = [p for p in self.players if p.peer is not None]
        for p in self.players:
            self._choose_cards(p, ())
            p.requested = p.accepted = False
        self.phase = "lobby"
        if self.flexible_seats:
            self._seats_frozen = False
            self._resize_lobby(self.seat_capacity)
        self.sequence = self.active = 0
        self.board = [None] * self.board_size
        self.acks.clear()
        self.pending_move = self.source = None
        self.markers.clear()
        self.targets.clear()
        self.ready.clear()
        self.continues.clear()
        self.owner_peer = None
        self.settings_applied = False
        self.npc_id = None
        self.cancel_pending()
        self._final_snapshots.clear()
        self._final_order = ()
        self._start_offered = False
        self._terminal_replies.clear()
        self.kicked_peers.clear()
        self._kick_receipts.clear()
        self.lobby_exit_receipts.clear()
        self.viewer_peers.clear()
        self._clear_prizes()
        self.authorized_round_id = None
        self.round_stake = 0
        self._departures = []
        self._departure_result_peers = set()
        self._result_sequence = None
        for p in self.players:
            p.departed = False
            p.substitute = 0

    @property
    def owner_id(self):
        if self.owner_peer is not None:
            return self.players[self._seat(self.owner_peer)].nickname_id
        return self.players[0].nickname_id if self.players else 0

    @property
    def ready_to_start(self):
        count_ready = (
            2 <= len(self.players) <= self.seat_capacity
            if self.flexible_seats
            else len(self.players) == self.player_count
        )
        return self.phase == "lobby" and self.settings_applied and count_ready

    @property
    def accepting_players(self):
        return (
            self.phase == "lobby"
            and len(self.players) < self.seat_capacity
            and not (self.flexible_seats and self._seats_frozen)
        )

    def _resize_lobby(self, count):
        self.player_count = count
        self.board_width = count + 2
        self.board_size = self.board_width**2
        self.last_sequence = 5 * count - 1
        self.start_board = special.start_board((0,) * self.board_size)
        self.board = [None] * self.board_size

    def _freeze_seats(self):
        if not self.flexible_seats or self._seats_frozen:
            return
        if self.phase != "lobby" or not 2 <= len(self.players) <= self.seat_capacity:
            raise MatchError("automatic table start requires two or three humans")
        self._resize_lobby(len(self.players))
        self._seats_frozen = True

    @property
    def start_pending(self):
        return self.phase == "lobby" and (
            self._start_offered or any(p.requested for p in self.players)
        )

    def rules_body(self):
        return (
            "@Tet="
            + "".join(f"/{k}={v}" for k, v in self.game_rules.items())
            + "@Tab="
            + "".join(f"/{k}={v}" for k, v in self.table_rules.items())
        )

    @staticmethod
    def _parse_settings(msg):
        expected = {"Tet", "Tab"} | ({"Save"} if msg.primary == 0x24 else set())
        if set(msg.sections) != expected:
            raise MatchError("settings require complete Tet and Tab sections")
        result = []
        domains = [
            dict(bm=(0, 2000), du=(0, 1), st=(0, 1), cb=(0, 1), ca=(0, 1), gs=(0, 2), tl=(0, 5)),
            {
                "in": (0, 2),
                "lu": (0, 99999),
                "ll": (0, 99999),
                "au": (0, 99999),
                "al": (0, 99999),
                "co": (0, 8),
                "pa": (0, 1),
            },
        ]
        for name, domain in zip(("Tet", "Tab"), domains):
            fields = msg.sections[name]
            password = name == "Tab" and numbers(fields.get("pa"), 1) == [1]
            if set(fields) != set(domain) | ({"pw"} if password else set()):
                raise MatchError("missing/unsupported table setting field")
            values = {key: numbers(fields[key], 1)[0] for key in domain}
            if any(not lo <= values[key] <= hi for key, (lo, hi) in domain.items()):
                raise MatchError("table setting outside recovered UI domain")
            if password:
                pw = fields["pw"]
                if not re.fullmatch(r"(?:[0-9A-Fa-f]{2}){0,12}", pw) or b"\0" in bytes.fromhex(pw):
                    raise MatchError("password exceeds the native twelve-byte field")
                values["pw"] = pw.upper()
            result.append(values)
        game, table = result
        if table["ll"] > table["lu"] or table["al"] > table["au"]:
            raise MatchError("table lower bound exceeds upper bound")
        return game, table

    def _check_room_rules(self, game):
        if self.room_rules is None:
            return
        expected = self.room_rules.as_dict()
        # Directory rules describe the starting stake. A server-authorized
        # Continue may double it; configuration cannot choose another amount.
        expected["bm"] = self.game_rules["bm"]
        if game != expected:
            raise MatchError("game settings conflict with configured room rules")

    def _settings(self, peer, msg):
        if self.phase != "lobby" or msg.subtype != 0 or msg.argument != 0:
            raise MatchError("table settings are only accepted before a match")
        game, table = self._parse_settings(msg)
        if msg.primary == 0x24:
            flags = msg.sections["Save"]
            if set(flags) != {"Tbl", "Game"}:
                raise MatchError("Save requires Tbl and Game flags")
            flags = {key: numbers(value, 1)[0] for key, value in flags.items()}
            if any(x not in (0, 1) for x in flags.values()) or not any(flags.values()):
                raise MatchError("invalid Save flags")
            old = dict(self.saved_preferences.get(peer, {}))
            if flags["Tbl"]:
                old["Tab"] = table
            if flags["Game"]:
                old["Tet"] = game
            self.saved_preferences[peer] = old
            return []
        owner = self.owner_peer if self.owner_peer is not None else self.players[0].peer
        if peer != owner:
            raise MatchError("only the admitted table owner may change current rules")
        self._check_room_rules(game)
        if self.start_pending:
            # GameReady may already be queued for a guest, and Req returns the
            # rules that GameOK accepts. Neither can be revised by a later Apply.
            if self.settings_applied and (game, table) == (self.game_rules, self.table_rules):
                return []
            raise MatchError("current rules cannot change during the start handshake")
        # Saving default preferences does not activate them in this practice
        # table. Current settings require features the match engine implements.
        if not self.allow_wagers and any(game[key] for key in ("bm", "du")):
            raise MatchError("wagers/double-up require economic hooks")
        if self.explicit_start_fields and any(
            game[key] != value for key, value in self.special_flags.items()
        ):
            raise MatchError("special rule flags must match the explicitly configured board")
        if (table["au"], table["al"]) != (300, 100):
            raise MatchError("current rank bounds must match the native directory defaults 300/100")
        # The original editor can emit twelve password bytes, while TD/PTL can
        # carry eight. Validate the full advertised rule representation before
        # changing state; the native client compares this advertised value.
        try:
            table_info(1, len(self.players), self.owner_id, game, table)
        except TableMetadataError as exc:
            raise MatchError(str(exc)) from exc
        self._check_playack(self.players, game, table)
        self.owner_peer = owner
        self.game_rules, self.table_rules = game, table
        if not self.explicit_start_fields:
            self.special_flags = {key: game[key] for key in ("st", "cb", "ca")}
        self.settings_applied = True
        self.settings_revision += 1
        # Original Apply posts a local editor event immediately after 10087410;
        # no settings-specific wire acknowledgement is mapped. Adapter publishes
        # TD then admission_notice separately, after the GameEA queue cleanup.
        return []

    def admission_notice(self, peer):
        """After accepted 14, add CPU and build roster/owner notices for post-TD delivery."""
        if not self.settings_applied or self.phase != "lobby":
            return []
        self._add_ai()
        owner = self.players[self._seat(self.owner_peer)]
        owner_body = f"@GameOwner=/NN={owner.nickname_id:016X}/CN={owner.name.hex().upper()}"
        return self._emit(packet(0x41, 4, body=self.member_list(peer)), peer) + self._emit(
            packet(0x1A, body=owner_body), peer
        )

    def _humans(self):
        return [p.peer for p in self.players if p.peer is not None and not p.departed]

    def kick_lobby_member(self, peer, target):
        """Hosted pre-start removal; QT2 satisfies the native dialog without
        clearing the host's joined flag. Election/legacy server policy unknown.
        """
        if peer != self.owner_peer:
            raise MatchError("only the table owner can remove a member")
        if self.phase != "lobby" or self._start_offered or any(p.requested for p in self.players):
            raise MatchError("member removal must precede the start handshake")
        ack = packet(0x41, 6, body="@GameQA=/QT=2")
        removed = next((p for p in self.players if p.nickname_id == target), None)
        if removed is None:
            if self._kick_receipts.get(target) == peer:
                return self._emit(ack, peer)
            raise MatchError("member is not at this table")
        if removed.peer is None or removed.peer == peer:
            raise MatchError("removal requires another human member")
        owner = self.players[self._seat(peer)]
        remaining = [p for p in self.players if p is not removed]
        body = (
            f"@GameKick=/Kick={target:016X}/FN={owner.name.hex().upper()}"
            f"/QN={removed.name.hex().upper()}" + self._member_body(remaining)
        )
        notice = packet(0x41, 0x0C, body=body)  # Validate the full wire size first.
        deliveries = self._emit(ack, peer) + self._emit(notice)
        self.players = remaining
        self.kicked_peers.add(removed.peer)
        self.viewer_peers.discard(removed.peer)
        self._kick_receipts[target] = peer
        return deliveries

    def lobby_departure_supported(self):
        return (
            self.mode == "versus"
            and self.phase == "lobby"
            and self.settings_applied
            and not self._start_offered
            and not any(p.requested for p in self.players)
            and len(self.players) > 1
        )

    def leave_lobby_member(self, peer, subtype):
        """Keep a configured pre-start table; oldest remaining seat becomes host.
        This election is a server choice.
        """
        if not self.lobby_departure_supported():
            raise MatchError("table cannot preserve this departure")
        player = self.players[self._seat(peer)]
        remaining = [p for p in self.players if p is not player]
        next_owner = (
            remaining[0] if peer == self.owner_peer else self.players[self._seat(self.owner_peer)]
        )
        ack = (
            packet(0x41, 6, body="@GameQA=/QT=1")
            if subtype == 5
            else packet(0x41, 14, body="@GameEA=/Exit=1")
            if subtype == 13
            else None
        )
        roster = packet(0x41, 4, body=self._member_body(remaining))
        owner_notice = packet(
            0x1A,
            body=f"@GameOwner=/NN={next_owner.nickname_id:016X}/CN={next_owner.name.hex().upper()}/QN={player.name.hex().upper()}",
        )
        out = (self._emit(ack, peer) if ack is not None else []) + [
            Delivery(p.peer, roster) for p in remaining
        ]
        if peer == self.owner_peer:
            out += [Delivery(p.peer, owner_notice) for p in remaining]
        self.players = remaining
        self.owner_peer = next_owner.peer
        self.kicked_peers.add(peer)  # A delayed Req cannot silently reclaim a seat.
        self.viewer_peers.discard(peer)
        if ack is not None:
            self.lobby_exit_receipts[(peer, subtype)] = ack
        return out

    @property
    def departure_seats(self):
        """Chronological server seats; economic identities stay in players."""
        return tuple(self._departures)

    @staticmethod
    def _controlled(player):
        return player.peer is None or player.substitute != 0

    def departure_supported(self, peer):
        """Read-only preflight: unsupported economic/rated rounds must abort.

        The adapter may keep its legacy refund/abort behavior when this isFalse.
        It must retain departed account reservations while a supported round
        continues; p.peer remains the immutable economic identity.
        """
        self._seat(peer)
        return (
            self.allow_departures
            and self.phase in ("turn", "battle")
            and not self.card_prizes
            and not self.allow_wagers
            and self.rating_provider is None
        )

    def depart(self, peer, *, replacement=1, await_result=False):
        """Apply one active departure, emitting23 before optional24 exactly once.

        Replacement NPC1 is a server choice. Its original personality
        controls the existing remaining hand; no fresh NPC cards are generated.
        No network grace period, account penalty or historical policy is claimed.
        """
        seat = self._seat(peer)
        p = self.players[seat]
        if p.departed:
            return []
        if type(await_result) is not bool:
            raise MatchError("await_result must be boolean")
        if not self.departure_supported(peer):
            raise MatchError("departure requires active non-economic unrated practice")
        rule = self.game_rules["gs"]
        if rule == 0:
            opponent(replacement)  # Validate before any mutation.
        if rule == 2 and await_result:
            self._departure_result_peers.add(peer)
        p.departed = True
        p.substitute = replacement if rule == 0 else 0
        self._departures.append(seat)
        self.acks.discard(peer)
        if self.active == seat:
            self.pending_move = None
        out = self._emit(packet(0x43, 0x23, body=f"@GetAway=/P={seat}"))
        if rule == 0:
            out += self._emit(packet(0x43, 0x24, body=f"@Change=/P={seat}/C={replacement}"))
        else:
            p.hand = []
            p.hand_slots = []
            if rule == 1:
                for c in self.board:
                    if c is not None and c.original_owner == seat:
                        c.base_owner = 3
        # Invalid games terminate locally after the notification when only one
        # original human remains (100BD88B..E5); do not invent a loss result.
        if not self._humans() or rule == 1 and self.player_count - len(self._departures) == 1:
            self.phase = "finished"
            self.cancel_pending()
            self.pending_move = None
            return out
        if self.phase == "turn" and self._unlocked():
            return out + self._drive_turn()
        if (
            self.phase == "battle"
            and self.active == seat
            and not self.pending_work
            and self.targets
        ):
            self._pending_battle = self._choose_target()
        return out

    def _emit(self, data, peer=None):
        return [Delivery(p, data) for p in (self._humans() if peer is None else [peer])]

    def _add_ai(self):
        if self.computer_menu:
            return  # The original menu chooses an NPC after admission/Req.
        if self.mode == "computer" and len(self.players) == 1:
            cid = 2 if self.players[0].character_id != 2 else 3
            self.players.append(
                Player(
                    None,
                    cid,
                    b"Local Computer",
                    self.npc_nickname_id,
                    selected=self.ai_cards,
                    hand=list(self.ai_cards),
                    hand_slots=list(range(5)),
                    updated=list(self.ai_cards),
                )
            )

    def member_list(self, peer, include_id=False):
        self._add_ai()
        p = self.players[self._seat(peer)]
        return self._member_body(self.players, p.nickname_id if include_id else None)

    @staticmethod
    def _member_body(players, identity=None):
        join = lambda f: "|".join(f(x) for x in players)
        # Original MLNN copies into a sixteen-byte field without a length
        # check. NPC asset names can be nineteen bytes; use the same bounded
        # lobby label as the room roster. WatchInfo C restores the asset name.
        name = lambda p: (p.name[:15] if p.peer is None else p.name).hex().upper()
        return (
            "@GameML="
            + (f"/ID={identity:016X}" if identity is not None else "")
            + f"/Num={len(players)}"
            + "/MLNN="
            + join(name)
            + "/MLID="
            + join(lambda p: f"{p.nickname_id:016X}")
            + "/Po="
            + join(lambda p: "0")
            + "/Lv="
            + join(lambda p: str(p.points))
            + "/Rk="
            + join(lambda p: str(p.rating))
            + "/HN="
            + join(name)
        )

    def _check_playack(self, players, game=None, table=None):
        # Original format has no proven fragmentation or safe field truncation.
        # Reject unrepresentable combinations before admission/settings mutate.
        game = self.game_rules if game is None else game
        table = self.table_rules if table is None else table
        rules = (
            "@Tet="
            + "".join(f"/{k}={v}" for k, v in game.items())
            + "@Tab="
            + "".join(f"/{k}={v}" for k, v in table.items())
        )
        return packet(2, body="@PLAYACK=/EN=1" + rules + self._member_body(players))

    def playack(self, peer):
        return packet(2, body="@PLAYACK=/EN=1" + self.rules_body() + self.member_list(peer))

    def _authorize_round(self, stake, *, player_count=None):
        """Persist escrow before changing round state or emitting acceptance.

        round_authorizer(self,next_round_id,stake) returns every human seat's
        post-debit balance. The adapter must save the debit and refreshed tokens
        atomically before returning; exceptions emit no successful transition.
        Computer menu updates and unsuccessful/partial Continue votes do not call
        this hook. The original computer menu sends its final rule choice in 06.
        A changed computer-menu seat count is passed as player_count before the
        engine resizes, so the durable reservation uses the intended geometry.
        """
        if type(stake) is not int or not 0 <= stake <= 2000 or (stake and not self.allow_wagers):
            raise MatchError("round wager is not enabled or outside0..2000")
        round_id = self.completed_matches + 1
        if self.authorized_round_id == round_id:
            if self.round_stake != stake:
                raise MatchError("authorized round wager cannot change")
            return
        humans = {seat: p for seat, p in enumerate(self.players) if p.peer is not None}
        if any(p.balance < stake for p in humans.values()):
            raise MatchError("a participant cannot afford the next wager")
        if self.round_authorizer is None:
            balances = {seat: p.balance for seat, p in humans.items()}
        elif player_count is not None and player_count != self.player_count:
            balances = self.round_authorizer(self, round_id, stake, player_count=player_count)
        else:
            balances = self.round_authorizer(self, round_id, stake)
        if (
            not isinstance(balances, dict)
            or set(balances) != set(humans)
            or any(type(v) is not int or not 0 <= v <= 99_999_999 for v in balances.values())
        ):
            raise MatchError("round authorizer must return every human post-debit balance")
        for seat, balance in balances.items():
            humans[seat].balance = balance
        self.authorized_round_id = round_id
        self.round_stake = stake

    def _quote_result(self):
        """Actual nonnegative credits, before stats and ResultData are built.

        The provider is read-only: the adapter commits growth, statistics and
        money before delivering returned packets. Only human seats may be paid.
        """
        humans = {seat: p for seat, p in enumerate(self.players) if p.peer is not None}
        payouts = (
            {seat: 0 for seat in humans}
            if self.payout_provider is None
            else self.payout_provider(
                self,
                self.completed_matches + 1,
                tuple(self._scores()),
                tuple(p.rating for p in self.players),
            )
        )
        if (
            not isinstance(payouts, dict)
            or set(payouts) != set(humans)
            or any(
                type(v) is not int or not 0 <= v <= 99_999_999 - humans[seat].balance
                for seat, v in payouts.items()
            )
        ):
            raise MatchError("payout provider must return bounded credits for every human")
        return payouts

    def begin(self):
        if self.phase != "lobby":
            return []
        if self.computer_menu:
            raise MatchError("original computer menu begins at Req, then ComGame selection")
        self._freeze_seats()
        self._add_ai()
        if len(self.players) != self.player_count:
            raise MatchError("all configured seats required before starting")
        self._authorize_round(self.game_rules["bm"])
        self.phase = "selection"
        body = f"@VsGameInit=/N={self.player_count}/ID=" + "|".join(
            str(p.character_id) for p in self.players
        )
        body += "/CN=" + "|".join(p.name.hex().upper() for p in self.players)
        body += "/R=" + "|".join(
            str(self.game_rules[k]) for k in ("bm", "st", "cb", "ca", "tl", "du", "gs")
        )
        for i, p in enumerate(self.players):
            body += f"@No{i}=/RA={p.rating}/M={p.balance}/CP={p.points}"
        return self._emit(packet(0x43, 1, body=body))

    def _com_list(self):
        # 10102F57/1010301C write D[i] into PlPrm record i+1 rating DWORD+4.
        return "@ComList=/L=30/D=" + self._r_text(n.rating for n in OPPONENTS)

    def _com_init(self, peer):
        p = self.players[self._seat(peer)]
        rules = [self.game_rules[k] for k in ("bm", "st", "cb", "ca", "tl", "du")] + [
            self.computer_view
        ]
        body = f"@ComGameInit=/RA={p.rating}/M={p.balance}/CP={p.points}/S={p.collection_size}/R={self._r_text(rules)}"
        return packet(0x43, 2, body=body + self._com_list())

    def _prepare_npcs(self, npc_ids):
        players = []
        cid = 2
        prepared_opponents = prepare_opponents(npc_ids, self.catalog, self.draw)
        for prepared, nickname in zip(
            prepared_opponents, (self.npc_nickname_id, self.second_npc_nickname_id), strict=False
        ):
            cards = tuple(Card.from_card(c) for c in prepared.hand)
            if cid == self.players[0].character_id:
                cid += 1
            # NPC names stay in PlPrm on the original client. Never serialize
            # potentially19-byte strings in the versus CN16-byte destinations.
            p = Player(
                None,
                cid,
                prepared.opponent.name,
                nickname,
                rating=prepared.opponent.rating,
                result_stats=PracticeStats(rating=prepared.opponent.rating),
                npc_id=prepared.opponent.npc_id,
                ai_personality=prepared.opponent.personality,
            )
            self._choose_cards(p, cards)
            players.append(p)
            cid += 1
        self.players[1:] = players
        # Keep the original one-NPC API as a view of the first computer seat.
        self.npc_id = players[0].npc_id
        self.ai_personality = players[0].ai_personality
        self.ai_cards = players[0].selected

    def _com_game(self, peer, msg):
        if not self.computer_menu or self.phase != "computer_menu" or self._seat(peer) != 0:
            raise MatchError("computer configuration is outside its admitted menu")
        fields = msg.sections["ComGame"]
        if msg.argument not in (0, 2, 3) or set(msg.sections) != {"ComGame"}:
            raise MatchError("computer selection requires one human and one or two NPCs")
        if set(fields) != ({"Rule"} if msg.argument == 0 else {"Rule", "Com"}):
            raise MatchError("invalid computer configuration fields")
        rules = numbers(fields["Rule"], 7 if msg.argument == 0 else 8)
        if (
            not 0 <= rules[0] <= 2000
            or rules[5] not in (0, 1)
            or (not self.allow_wagers and (rules[0] or rules[5]))
            or not 0 <= rules[4] <= 5
            or rules[6] not in (0, 1, 2)
        ):
            raise MatchError("computer wager/double-up is disabled or menu rules are invalid")
        if any(value not in (0, 1) for value in rules[1:4]):
            raise MatchError("computer special rules require0/1 flags")
        if self.explicit_start_fields and tuple(rules[1:4]) != tuple(
            self.special_flags[k] for k in ("st", "cb", "ca")
        ):
            raise MatchError("computer rule flags must match the explicitly configured board")
        if msg.argument and rules[7] not in (0, 1, 2):
            raise MatchError("invalid computer departure rule")
        game = dict(self.game_rules)
        game.update(zip(("bm", "st", "cb", "ca", "tl", "du"), rules[:6]))
        if msg.argument:
            game["gs"] = rules[7]
        self._check_room_rules(game)
        npc_ids = ()
        if msg.argument:
            npc_ids = tuple(numbers(fields["Com"], msg.argument - 1))
            try:
                for npc_id in npc_ids:
                    opponent(npc_id)
            except OpponentError as e:
                raise MatchError(str(e)) from e
            if len(set(npc_ids)) != len(npc_ids):
                raise MatchError("computer opponents must be distinct")
            if self.explicit_start_fields and msg.argument != self.player_count:
                raise MatchError("computer player count must match the explicitly configured board")
            self._authorize_round(rules[0], player_count=msg.argument)
        # Validate all wire fields before changing configuration or drawing.
        self.game_rules.update(zip(("bm", "st", "cb", "ca", "tl", "du"), rules[:6]))
        if not self.explicit_start_fields:
            self.special_flags = {key: self.game_rules[key] for key in ("st", "cb", "ca")}
        self.computer_view = rules[6]
        self.table_rules["in"] = self.computer_view
        if msg.argument:
            self.game_rules["gs"] = rules[7]
            if msg.argument != self.player_count:
                self._resize_lobby(msg.argument)
            self._prepare_npcs(npc_ids)
            self._choose_cards(self.players[0], ())
            self.phase = "selection"
        # 100BD1DA..225 clears08/09/0A/0C/0D/1A/0F after the menu closes.
        # Wait for the human CardSelect07 rather than answering06 with Start.
        return []

    def _select(self, peer, msg):
        seat = self._seat(peer)
        fields = msg.sections["CardSelect"]
        if self.phase != "selection" or msg.argument != seat or numbers(fields.get("C"), 1) != [5]:
            raise MatchError("unexpected CardSelect phase, seat or count")
        try:
            cards = tuple(Card.parse(msg.sections[f"N{i}"]["D"]) for i in range(5))
        except KeyError as e:
            raise MatchError("all five full card sections are required") from e
        p = self.players[seat]
        if p.selected and p.selected != cards:
            raise MatchError("cannot replace an accepted selection")
        if self.selected_card_validator and not self.selected_card_validator(peer, cards):
            raise MatchError("selected cards are not in this account inventory")
        self._choose_cards(p, cards)
        complete = all(len(p.selected) == 5 for p in self.players)
        ans = "|".join(str(int(bool(p.selected))) for p in self.players)
        out = self._emit(packet(0x43, 7, body=f"@CardSelect=/Ans={ans}/Ok={int(complete)}"))
        if complete:
            self.phase = "turn"
            self.sequence = 0
            first_shared = None
            if not self.explicit_start_fields and any(
                self.game_rules[key] for key in ("st", "cb", "ca")
            ):
                generated = generate_board(
                    self.player_count,
                    self.draw,
                    **{key: self.game_rules[key] for key in ("st", "cb", "ca")},
                )
                self.start_board = special.start_board(generated.fields)
                self.active = generated.active
                first_shared = generated.shared
            else:
                self.active = 0
                if not self.explicit_start_fields:
                    self.start_board = special.start_board((0,) * self.board_size)
            self._adopt_effects(self.start_board.cells)
            self._final_snapshots.clear()
            self._final_order = ()
            self._clear_prizes()
            self.cancel_pending()
            out += self._emit(
                packet(
                    0x43,
                    8,
                    0,
                    f"@StartData=/S={self.active}/B={self.start_board.blocks}/E={self.start_board.effects}/F="
                    + self._r_text(self.start_board.fields)
                    + "/L="
                    + self._r_text([0] * self.player_count),
                )
            )
            out += self._turn(shared=first_shared)
        return out

    def _scores(self):
        return [
            sum(c is not None and c.owner == s for c in self.board)
            for s in range(self.player_count)
        ]

    def result_classes(self):
        """Original 100CBD80 ordinary and departure-specific ranking."""
        return result_ranking(self._scores(), self.game_rules["gs"], self.departure_seats).classes

    def legal_fields(self):
        """Ascending currently placeable cells, including unoccupied low tiles.

        This is board geometry only; handle still validates phase, actor, hand,
        sequence and acknowledgement barrier before accepting a placement.
        """
        return tuple(i for i, c in enumerate(self.board) if c is None or c.owner == 8)

    def _turn(self, *, shared=None):
        self.acks.clear()
        self.pending_move = None
        self.source = None
        self.markers = {}
        self.targets = []
        self.cancel_pending()
        self.phase = "turn"
        scores = "|".join(map(str, self._scores()))
        self._current_turn_packet = packet(
            0x43,
            9,
            self.sequence,
            f"@TurnData=/A={self.active}/S={scores}/R={self._r_text(self._shared_r() if shared is None else shared)}",
        )
        return self._emit(self._current_turn_packet)

    def _unlocked(self):
        return self.acks == set(self._humans())

    def _ack(self, peer, msg):
        seat = self._seat(peer)
        if msg.argument != self.sequence or self.phase not in ("turn", "battle"):
            return []  # A stale ack must never rewind the match or duplicate a turn.
        if numbers(msg.sections["TurnData"].get("Ans"), 1) != [seat]:
            raise MatchError("ack seat does not match authenticated peer")
        if peer in self.acks:
            return []
        self.acks.add(peer)
        if self.phase != "turn" or not self._unlocked():
            return []
        return self._drive_turn()

    def _drive_turn(self):
        if self.pending_move is not None:
            h, f = self.pending_move
            self.pending_move = None
            return self._place(h, f)
        player = self.players[self.active]
        if player.departed and not player.substitute:
            # Original F255 enters completed combat without touching a card.
            out = self._emit(
                packet(0x43, 0x0A, self.sequence, f"@PutCard=/A={self.active}/H=0/F=255")
            )
            return out + self._end_turn()
        if self._controlled(player):
            personality = (
                opponent(player.substitute).personality
                if player.substitute
                else player.ai_personality or self.ai_personality
            )
            if any(self.start_board.fields) or self.player_count == 3:
                move = self._backend_call(
                    "choose_special_move",
                    self.players[self.active].hand,
                    self._effect_cells(),
                    self.active,
                    self.draw,
                    personality,
                )
            else:
                move = self._backend_call(
                    "choose_move",
                    self.players[self.active].hand,
                    self.board,
                    self.active,
                    self.draw,
                    personality,
                )
            if move is None:
                raise MatchError("active computer has no legal move")
            return self._place(move.hand_index, move.field)
        return []

    def _move(self, peer, msg):
        seat = self._seat(peer)
        values = msg.sections["PutData"]
        h, f = numbers(values.get("H"), 1)[0], numbers(values.get("F"), 1)[0]
        if self.phase != "turn" or seat != self.active or msg.argument != self.sequence:
            raise MatchError("placement is not for the current active turn")
        if numbers(values.get("P"), 1) != [seat] or not 0 <= h < len(self.players[seat].hand):
            raise MatchError("invalid seat or remaining-hand index")
        if f not in self.legal_fields():
            raise MatchError("placement cell is occupied/outside board")
        if self.pending_move is not None:
            if self.pending_move == (h, f):
                return []
            raise MatchError("an early move is already buffered")
        if not self._unlocked():
            self.pending_move = (h, f)
            return []
        return self._place(h, f)

    def neighbors(self, index):
        width = self.board_width
        x, y = index % width, index // width
        for bit, (dx, dy) in enumerate(DIRECTIONS):
            xx, yy = x + dx, y + dy
            if 0 <= xx < width and 0 <= yy < width:
                yield bit, yy * width + xx

    def _classify(self):
        source = self.board[self.source]
        if source is not None and source.owner == self.active:
            self._adopt_effects(special.classify(self._effect_cells(), self.source, self.active))
        self.targets = sorted(i for i, m in self.markers.items() if m & 1)

    def _place(self, hand, cell):
        # H indexes the current compacted hand, not an immutable selection slot.
        # The active original client shifts later cards left at 100C710F..7152,
        # using 100B2A80, then clears the trailing slot at 100C7185.
        player = self.players[self.active]
        card = player.hand.pop(hand)
        original_slot = player.hand_slots.pop(hand)
        self._adopt_effects(
            special.place(
                self._effect_cells(), cell, card, self.active, (self.active, original_slot)
            )
        )
        self.source = cell
        out = self._emit(
            packet(
                0x43,
                0x0A,
                self.sequence,
                f"@PutCard=/A={self.active}/H={hand}/F={cell}/D={card.full()}",
            )
        )
        return out + self._continue_combat()

    def _continue_combat(self, immediate=True):
        self._classify()
        if not self.targets:
            self._adopt_effects(special.direct_captures(self._effect_cells(), self.active))
            return self._end_turn()
        self.phase = "battle"
        if (
            len(self.targets) == 1
            or self._controlled(self.players[self.active])
            or self.players[self.active].departed
        ):
            target = self._choose_target()
            if immediate:
                return self._battle(target)
            self._pending_battle = target
        return []

    def _combo(self, cell, winner, old_owner):
        self._adopt_effects(special.combo(self._effect_cells(), cell, winner, old_owner))

    def battle_values(self, attacker, defender, **effects):
        result = self._backend_call("calculate_battle", attacker, defender, self.draw, **effects)
        return result.attacker.wire(0)[1:], result.defender.wire(0)[1:]

    def _choose_target(self):
        return self._backend_call("choose_target", self.targets, self.draw, board_size=self.board_size)

    def _grow_card(self, card, mode, random_value):
        baseline = self.baselines[card.model]
        return self._backend_call("grow_card", card, baseline, mode, random_value)

    def _grow_board(self, board_card, mode, random_value):
        result = self._grow_card(board_card.card, mode, random_value)
        board_card.card = Card.from_card(result.card)
        if board_card.original_slot is not None:
            self.players[board_card.original_owner].updated[board_card.original_slot] = (
                board_card.card
            )

    def _battle(self, target):
        if target not in self.targets:
            raise MatchError("battle choice is not a reciprocal-arrow target")
        a, d = self.board[self.source], self.board[target]
        if a.property or d.property or a.modifier or d.modifier or d.card is None:
            av, dv = self.battle_values(
                a.card,
                d.card or Card(0, 0, 0, 0, 0, 0, 0),
                attacker_modifier=a.modifier,
                defender_modifier=d.modifier,
                attacker_property=a.property,
                defender_property=d.property,
            )
        else:
            av, dv = self.battle_values(a.card, d.card)
        if d.owner == 4:
            # Offline100CEE95..100CEECC suppresses both stat selectors on tiles.
            av = (av[0], av[1], 0, av[3])
            dv = (dv[0], dv[1], 0, dv[3])
        shared = self._shared_r()
        tuples = (
            "/A="
            + "|".join(map(str, (self.source, *av)))
            + "/D="
            + "|".join(map(str, (target, *dv)))
        )
        body = "@BattleData=" + tuples + "/R=" + self._r_text(shared)
        replacement = None
        if d.owner == 4 and d.property >> 4 == 5:
            if not self.allow_cbc:
                raise MatchError("CBC reward transaction is not enabled")
            # Recovered local materialization uses pack75/no guild filter.
            # Hosted draw placement after shared R is explicit server policy.
            model = self.catalog.select(75, 0, self.draw)
            replacement = Card.from_card(self.catalog.generate(model, self.draw))
            body += "/CBC=" + replacement.full()
        out = self._emit(packet(0x43, 0x0C, self.sequence, body))
        if d.owner == 4:
            effect = special.apply_special(
                self._effect_cells(),
                self.source,
                target,
                self.active,
                shared,
                displayed_start=(av[0] * av[3]) & 65535,
                replacement=replacement,
            )
            self._adopt_effects(effect.cells)
            return out + (
                self._continue_combat(immediate=False)
                if effect.continue_combat
                else self._end_turn()
            )
        if av[1] == dv[1]:
            # Original presentation retries the SAME target without BattleSelect.
            # Do not change owners, apply promotion, or skip the visible tie.
            self._pending_battle = target
            return out
        if av[1] > dv[1]:
            self._grow_board(a, 0, shared[0])
            old = d.owner
            d.owner = self.active
            self._combo(target, self.active, old)
            return out + self._continue_combat(immediate=False)
        self._grow_board(d, 0, shared[0])
        old = a.owner
        a.owner = d.owner
        self._combo(self.source, d.owner, old)
        return out + self._end_turn()  # Deferred direct captures are abandoned on loss.

    def _end_turn(self):
        self._adopt_effects(special.finish_turn(self._effect_cells(), self.source))
        boundary = next_turn(
            self.player_count,
            self.game_rules["gs"],
            self.departure_seats,
            self.sequence,
            self.active,
            [i for i, p in enumerate(self.players) if p.substitute],
        )
        if not boundary.complete:
            self.sequence, self.active = boundary.sequence, boundary.active
            return self._turn()
        payouts = self._quote_result()
        classes = self.result_classes()
        fully_tied = len(set(self._scores())) == 1 and not (
            self.departure_seats and self.game_rules["gs"]
        )
        humans = {seat: p for seat, p in enumerate(self.players) if p.peer is not None}
        changes = (
            None
            if self.rating_provider is None
            else self.rating_provider(self, self.completed_matches + 1, tuple(self._scores()))
        )
        if changes is not None and (not isinstance(changes, dict) or set(changes) != set(humans)):
            raise MatchError("rating provider must return every human seat")
        stats = [
            p.result_stats.next_result(
                (classes[seat] < 2) - (classes[seat] > 2),
                payouts.get(seat, 0),
                fully_tied=fully_tied,
                rating_change=None if changes is None else changes.get(seat),
                vs_player_game=self.mode == "versus",
            )
            for seat, p in enumerate(self.players)
        ]
        self._result_sequence = self.sequence
        self.sequence, self.active = boundary.sequence, boundary.active
        self.phase = "result"
        self.cancel_pending()
        self.completed_matches += 1
        self.ready.clear()
        self.continues.clear()
        shared = self._shared_r()
        for seat, p in enumerate(self.players):
            p.result_stats = stats[seat]
            p.rating = stats[seat].rating
            p.balance += payouts.get(seat, 0)
        self._complete_cards(shared)
        self._prepare_prizes()
        prefix = f"@ResultData=/R={self._r_text(shared)}/P={self.player_count}"
        result_peers = self._humans() + [
            p.peer for p in self.players if p.peer in self._departure_result_peers
        ]
        if self.player_count == 3:
            out = []
            for peer in result_peers:
                # 10104141..101044F9 reads full profile fields only for the
                # receiving ordinary player; remote sections read D2 rating.
                # Keep one complete logical packet within 447 even with long RN.
                body = prefix + "".join(
                    (
                        p.result_stats.result_section(i)
                        if p.peer == peer
                        else f"@No{i}=/D=0|0|{p.rating}|0|0|1|0"
                    )
                    for i, p in enumerate(self.players)
                )
                out += self._emit(packet(0x43, 0x0D, self._result_sequence, body), peer)
            return self._prize_gate(out)
        body = prefix + "".join(
            p.result_stats.result_section(i) for i, p in enumerate(self.players)
        )
        data = packet(0x43, 0x0D, self._result_sequence, body)
        return self._prize_gate([Delivery(peer, data) for peer in result_peers])

    def _complete_cards(self, shared):
        # 100C87E2..88D9 rebuilds each original owner's hand in board-index order.
        # Native result R indexes that rebuilt hand, not the original selection.
        classes = self.result_classes()
        orders = []
        snapshots = {}
        for seat, p in enumerate(self.players):
            if not p.updated:
                p.updated = list(p.selected)
            board_slots = [
                c.original_slot
                for c in self.board
                if c is not None and c.original_owner == seat and c.original_slot is not None
            ]
            order = (
                list(
                    result_slots(
                        p.hand_slots, board_slots, departed=p.departed, rule=self.game_rules["gs"]
                    )
                )
                if self.departure_seats
                else board_slots
            )
            # Remaining slots are needed by synthetic direct-result fixtures;
            # a complete ordinary ten-placement round has all five on board.
            if not self.departure_seats:
                order += [i for i in range(len(p.selected)) if i not in order]
            orders.append(tuple(order))
            eligible = classes[seat] < 3
            for hand_slot, original_slot in enumerate(order):
                c = p.updated[original_slot]
                result = self._grow_card(
                    c,
                    1 if eligible else 0,
                    shared[(hand_slot - 3 * seat + 2) & 7] if eligible else 255,
                )
                p.updated[original_slot] = Card.from_card(result.card)
            if p.peer is not None:
                extras = tuple(
                    c.card
                    for c in self.board
                    if c is not None
                    and c.card is not None
                    and c.original_owner == 5
                    and c.owner == seat
                )
                snapshots[p.peer] = FinalCards(
                    self.completed_matches, p.selected, tuple(p.updated), p.result_stats, extras
                )
        self._final_snapshots = snapshots
        self._final_order = tuple(orders)

    def _final_cards(self, peer):
        body = "@TCList=/H=" + self._r_text([len(order) for order in self._final_order])
        for seat, p in enumerate(self.players):
            for i, original_slot in enumerate(self._final_order[seat]):
                c = p.updated[original_slot]
                owner = self._prize_tclist_owners[seat][i] if self.card_prizes else seat
                body += f"/D{seat * 5 + i}={owner}|{c.reduced()}"
        out = self._emit(
            packet(
                0x43,
                0x1A,
                self._result_sequence if self._result_sequence is not None else self.last_sequence,
                body,
            ),
            peer,
        )
        # Remote NPC selection waits in native3C; queue it after the first TCList
        # so the original 39->3A->3B->3C path consumes authoritative hands first.
        for payload in self._computer_prize_packets:
            out += self._emit(payload, peer)
        return self._prize_gate(out)

    def _continue(self, peer, msg):
        if self.phase != "result":
            return []
        if self.card_prizes and not self._prizes_confirmed:
            raise MatchError("Continue requires completed and committed card prizes")
        seat = self._seat(peer)
        if msg.argument != seat:
            raise MatchError("Continue header is not the authenticated seat")
        conf = numbers(msg.sections["Continue"].get("Conf"), 1)[0]
        if conf not in ((0, 1) if self.computer_menu else (0,)):
            raise MatchError("alternate Continue configuration is unsupported")
        ans = numbers(msg.sections["Continue"].get("Ans"), 1)[0]
        if ans not in (1, 2) or (conf and ans != 1):
            raise MatchError("unsupported Continue answer")
        privacy = msg.sections["Continue"].get("PR")
        if privacy is not None and numbers(privacy, 1)[0] not in (0, 1):
            raise MatchError("Continue ranking privacy must be zero or one")
        if self.departure_seats:
            # The original Continue constructor preselects leave for departures.
            # No silent rematch can restore the removed network participant.
            self.phase = "finished"
            return self._emit(
                packet(
                    0x43,
                    0x11,
                    2,
                    "@Continue=/Ans="
                    + self._r_text([3] * self.player_count)
                    + "/B="
                    + self._r_text([0] * self.player_count),
                )
            )
        if ans == 2:
            self.continues[peer] = ans
            self.phase = "finished"
            return self._emit(
                packet(
                    0x43,
                    0x11,
                    2,
                    "@Continue=/Ans="
                    + self._r_text([3] * self.player_count)
                    + "/B="
                    + self._r_text([0] * self.player_count),
                )
            )
        votes = {**self.continues, peer: ans}
        if set(votes) != set(self._humans()):
            self.continues = votes
            return []
        # Both native Continue constructors double/cap the next wager before
        # asking (1010BC4B/1010BF5C); the debit follows positive acceptance only.
        stake = min(2000, self.game_rules["bm"] * (1 + self.game_rules["du"]))
        if not conf:
            self._authorize_round(stake)
        self.game_rules["bm"] = stake
        # Original Conf1 waits for ComList04, with no Continue11 wait here
        # (1010D375..1010D393,1010D5D9..1010D63C).
        out = (
            self._emit(packet(0x43, 4, body=self._com_list()))
            if conf
            else self._emit(
                packet(
                    0x43,
                    0x11,
                    1,
                    "@Continue=/Ans="
                    + self._r_text([1] * self.player_count)
                    + "/B="
                    + self._r_text([0] * self.player_count),
                )
            )
        )
        self.phase = "computer_menu" if conf else "selection"
        self.sequence = self.active = 0
        self.board = [None] * self.board_size
        self.acks.clear()
        self.pending_move = self.source = None
        self.markers.clear()
        self.targets.clear()
        self.ready.clear()
        self.continues.clear()
        self.cancel_pending()
        for p in self.players:
            self._choose_cards(
                p, self.ai_cards if p.peer is None and not self.computer_menu else ()
            )
        if self.computer_menu and not conf:
            self._prepare_npcs(tuple(p.npc_id for p in self.players[1:]))
        # Req/GameOK acceptance describes continued table membership. Only the
        # round's card choices are reset; the original returns to selection.
        return out

    def handle(self, peer, payload):
        """Return deliveries, [] for handled/no reply, or None for another service."""
        msg = decode(payload)
        if msg is None:
            return None
        seat = self._seat(peer)
        p = self.players[seat]
        if p.departed:
            return []  # Old/rebound input cannot control the substitute.
        if msg.primary != 0x43:
            return self._table_message(peer, msg, p)
        section = {
            7: "CardSelect",
            9: "TurnData",
            0x0A: "PutData",
            0x0B: "BattleSelect",
            0x0E: "Ready",
            0x11: "Continue",
            5: "Quit",
            0x27: "Break",
        }.get(msg.subtype)
        if self.card_prizes and msg.subtype == 0x0F:
            section = "GetSelect"
        if self.computer_menu and msg.subtype == 6:
            section = "ComGame"
        if section is None:
            return None
        if section not in msg.sections:
            raise MatchError("message section does not match subtype")
        if section == "ComGame":
            return self._com_game(peer, msg)
        if section == "GetSelect":
            return self._get_select(peer, msg)
        if section == "CardSelect":
            return self._select(peer, msg)
        if section == "TurnData":
            return self._ack(peer, msg)
        if section == "PutData":
            return self._move(peer, msg)
        if section == "BattleSelect":
            if (
                self.phase != "battle"
                or self.active != seat
                or self.sequence != msg.argument
                or not self._unlocked()
            ):
                raise MatchError("battle choice is not for the active turn")
            target = numbers(msg.sections[section].get("B"), 1)[0]
            if self.pending_work:
                if target == self._pending_battle:
                    return []
                raise MatchError("battle continuation already pending")
            return self._battle(target)
        if section == "Ready":
            # Result and prize presentation each send Ready. No mapped retry
            # bit distinguishes them; emitting TCList twice risks a stale list.
            if self.phase == "result" and peer not in self.ready:
                if msg.argument != self.sequence or numbers(
                    msg.sections["Ready"].get("Ans"), 1
                ) != [seat]:
                    raise MatchError("Ready is not for this participant and final turn")
                self.ready.add(peer)
                return self._final_cards(peer)
            return []
        if section == "Continue":
            return self._continue(peer, msg)
        if self.phase not in ("computer_menu", "selection", "turn", "battle", "result"):
            return []
        if section == "Quit":
            if numbers(msg.sections["Quit"].get("No"), 1) != [seat]:
                raise MatchError("Quit seat does not match the authenticated peer")
            # Quit's header is the client's MAJOR scene, not a turn sequence.
            majors = {
                "computer_menu": {0, 2, 3, 7},
                "selection": {5, 8},
                "turn": {10},
                "battle": {10},
                "result": {10, 11, 12, 13},
            }[self.phase]
            if self.phase == "turn" and self.sequence == 0 and peer not in self.acks:
                majors = majors | {8, 9}  # Client can still be closing selection.
            if msg.argument not in majors:
                return []
        elif msg.argument != 3 or numbers(msg.sections["Break"].get("ID"), 1) != [p.character_id]:
            raise MatchError("Break must identify this authenticated character with header3")
        if self.departure_supported(peer) and len(self._humans()) > 1:
            out = self.depart(peer, await_result=section == "Quit")
            return (
                self._emit(packet(0x43, 5, msg.argument, f"@Quit=/No={seat}/B=0"), peer)
                if section == "Quit"
                else []
            ) + out
        self.phase = "finished"
        self.cancel_pending()
        self._held_prize_deliveries = []  # Adapter owns interruption recovery.
        return (
            self._emit(packet(0x43, 5, msg.argument, f"@Quit=/No={seat}/B=0"), peer)
            if section == "Quit"
            else []
        )

    def _table_message(self, peer, msg, p):
        """Handle table notices, admission, preferences and discovery."""
        from tmserver.protocol.table import channel_exit, viewer_state

        viewing = viewer_state(msg)
        if viewing is not None:
            if viewing:
                self.viewer_peers.add(peer)
            else:
                self.viewer_peers.discard(peer)
            return []  # It is not spectator admission or a game pause.
        if channel_exit(msg):
            if self.phase != "lobby":
                return []  # A delayed startup cancel cannot end play.
            if self.lobby_departure_supported():
                return self.leave_lobby_member(peer, 0x20)
            self.phase = "finished"
            self.cancel_pending()
            self.viewer_peers.clear()
            return [
                d for d in self._emit(packet(65, 16, body="@GameWa=/Error=-1")) if d.peer != peer
            ]
        admission = (
            "Req"
            if msg.primary == 1 and "Req" in msg.sections
            else {1: "GameEN", 0x12: "GameENC"}.get(msg.subtype)
            if msg.primary == 0x41
            else None
        )
        if admission:
            if admission == "Req" and peer in self.kicked_peers:
                raise MatchError("removed members must explicitly re-enter the table")
            if self.phase != "lobby":
                return []
            if self.computer_menu and (
                (admission == "GameEN") or (admission == "Req" and self.owner_peer != peer)
            ):
                raise MatchError("original computer service requires GameENC before Req")
            if (admission == "Req" and p.accepted) or (admission != "Req" and p.requested):
                return []
            if admission not in msg.sections:
                raise MatchError("admission subtype requires its matching section")
            fields = msg.sections[admission]
            identity = fields.get("NN")
            try:
                validate_admission(fields, admission)
            except ValueError as exc:
                raise MatchError(str(exc)) from exc
            if identity is not None:
                if not re.fullmatch(r"[0-9A-Fa-f]{16}", identity):
                    raise MatchError("NN must be a sixteen-digit hexadecimal identity")
                if p.identity_confirmed and p.nickname_id != int(identity, 16):
                    raise MatchError("cannot change an admitted member identity")
                # A first synthetic NN can differ from the IRC account. Freeze
                # it once admitted; it is display identity, never authorization.
                p.nickname_id = int(identity, 16)
                p.identity_confirmed = True
        if msg.primary == 0x41:
            return self._lobby_control(peer, msg, p)
        if msg.primary == 1 and "Req" in msg.sections:
            p.requested = True
            out = self._emit(self.playack(peer), peer)
            if self.computer_menu:
                p.accepted = True
                self.phase = "computer_menu"
                out += self._emit(self._com_init(peer), peer)
            return out
        if msg.primary in (0x14, 0x24):
            return self._settings(peer, msg)
        if msg.primary == 0x12:
            return self._emit(packet(0x13, body=self.rules_body()), peer)
        if msg.primary == 0x2D:
            return self._emit(packet(0x2E, body="@CheckJoinTable_Ans=/Join=1/Mem=1"), peer)
        return None

    def _lobby_control(self, peer, msg, p):
        """Apply entry, roster, start and departure controls for a seated peer."""
        sub = msg.subtype
        name = {
            3: "GameM",
            5: "GameQT",
            8: "GameOK",
            10: "GameReady",
            13: "GameExit",
            15: "GameNG",
        }.get(sub)
        if name is not None and name not in msg.sections:
            raise MatchError("table-control subtype requires its matching section")
        if sub in (1, 0x12):
            if self.computer_menu and sub != 0x12:
                raise MatchError("original computer service requires GameENC admission")
            if self.owner_peer is None:
                self.owner_peer = peer
            self.kicked_peers.discard(peer)
            for receipt in tuple(self.lobby_exit_receipts):
                if receipt[0] == peer:
                    self.lobby_exit_receipts.pop(receipt)
            # EN1 takes the new-owner settings scene at 10090114..143;
            # EN2 joins the existing table and takes its local stake debit.
            admission_result = 1 if peer == self.owner_peer else 2
            return self._emit(
                packet(
                    0x41,
                    2 if sub == 1 else 0x13,
                    body=f"@GameEA=/EN={admission_result}" if sub == 1 else "@GameECA=/EN=1",
                ),
                peer,
            )
        if sub == 3:
            if self.phase != "lobby":
                return []
            return self._emit(packet(0x41, 4, body=self.member_list(peer, True)), peer)
        if sub == 0x0A:
            if (
                self.phase != "lobby"
                or self._start_offered
                or any(x.requested for x in self.players)
            ):
                return []
            if peer != (self.owner_peer if self.owner_peer is not None else self.players[0].peer):
                raise MatchError("only the table owner can offer a start")
            self._freeze_seats()
            self._start_offered = True
            return [d for d in self._emit(packet(0x41, 7)) if d.peer != peer]
        if sub == 8:
            if self.phase != "lobby":
                return []
            if not p.requested:
                raise MatchError("GameOK before Req")
            # Direct Req/GameOK clients have no preceding GameReady. Freeze
            # only after the currently seated humans have all requested.
            if self.flexible_seats and not self._seats_frozen:
                if len(self.players) < 2 or not all(x.requested for x in self.players):
                    return []
                self._freeze_seats()
            p.accepted = True
            self._add_ai()
            if (
                len(self.players) == self.player_count
                and all(x.accepted for x in self.players if x.peer is not None)
                and self.phase == "lobby"
            ):
                return self._emit(packet(0x41, 9)) + self.begin()
            return []
        if sub in (5, 0x0D, 0x0F):
            # GameQT/GameNG belong to the table/start UI. Active-game aborts
            # use43 Quit/Break first; delayed lobby exits cannot abort play.
            if self.phase not in (
                ("lobby", "computer_menu", "finished") if sub == 0x0D else ("lobby",)
            ):
                return []
            if (peer, sub) in self._terminal_replies:
                return []
            if sub == 5 and "ID" in msg.sections["GameQT"]:
                target = msg.sections["GameQT"]["ID"]
                if not re.fullmatch(r"[0-9A-Fa-f]{16}", target):
                    raise MatchError("member identity must be sixteen hexadecimal digits")
                if int(target, 16) != p.nickname_id:
                    return self.kick_lobby_member(peer, int(target, 16))
            if sub in (5, 0x0D) and self.lobby_departure_supported():
                return self.leave_lobby_member(peer, sub)
            self._terminal_replies.add((peer, sub))
            self.phase = "finished"
            self.cancel_pending()
            if sub == 5:
                return self._emit(packet(0x41, 6, body="@GameQA=/QT=1"), peer)
            if sub == 0x0D:
                return self._emit(packet(0x41, 0x0E, body="@GameEA=/Exit=1"), peer)
            return self._emit(packet(0x41, 0x10, body="@GameWa=/Error=-1"))
        return None

    def snapshot(self):
        return {
            "phase": self.phase,
            "sequence": self.sequence,
            "active": self.active,
            "player_count": self.player_count,
            "scores": self._scores(),
            "hand_counts": [len(p.hand) for p in self.players],
            "awaiting_ack_count": len(set(self._humans()) - self.acks),
            "battle_targets": list(self.targets),
            "board": [
                (
                    None
                    if c is None
                    else {
                        "model": c.card.model if c.card is not None else c.tile_id,
                        "owner": c.owner,
                    }
                )
                for c in self.board
            ],
            "completed_matches": self.completed_matches,
            "owner_id": self.owner_id,
            "computer_menu": self.computer_menu,
            "npc_id": self.npc_id,
            "authorized_round_id": self.authorized_round_id,
            "round_stake": self.round_stake,
            "pending_work": self.pending_work,
            "departure_seats": self.departure_seats,
            "settings_applied": self.settings_applied,
            "settings_revision": self.settings_revision,
            "ready_to_start": self.ready_to_start,
            "game_rules": dict(self.game_rules),
            "table_rules": dict(self.table_rules),
        }
