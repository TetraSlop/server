"""Prize Center requests and the award queue.

Save economy.snapshot() and this service's snapshot() together before replying.
Awards are redeemed before ExcInit, because the client has no award
acknowledgement. Purchases debit points and transfer cards only at Get. Server
choice: awards come only from grant_award, for administration. Layout:
TetraPackets flows/prize-center.md.
"""

import random
import re
from copy import copy, deepcopy
from dataclasses import asdict, dataclass, replace

from tmserver.economy.errors import EconomyError
from tmserver.economy.packets import named, parse_named
from tmserver.cards.edits import apply_card_edits, parse_edit_records


class PrizeError(EconomyError):
    pass


def _integer(value, maximum=0xFFFFFFFF):
    if (
        not isinstance(value, str)
        or not re.fullmatch(r"[0-9]{1,10}", value)
        or int(value) > maximum
    ):
        raise PrizeError("invalid Prize Center integer")
    return int(value)


def _fields(value, names):
    if set(value) != set(names):
        raise PrizeError("unexpected or missing Prize Center fields")


def _hex(value, maximum):
    if len(value) > maximum * 2 or len(value) % 2 or not re.fullmatch(r"[0-9A-Fa-f]*", value):
        raise PrizeError("invalid Prize Center string")
    data = bytes.fromhex(value)
    if b"\0" in data:
        raise PrizeError("embedded string terminator")
    return data


@dataclass(frozen=True)
class Award:
    id: str
    top30: int = 0
    lucky: int = 0
    rookies: int = 0
    lucky_cards: tuple = (65535, 65535, 65535)


@dataclass
class _Purchase:
    pack: int
    cost: int
    incoming: tuple
    cards: list
    names: dict
    reply: bytes


@dataclass
class _Session:
    number: int
    token: object
    entry_reply: tuple
    lucky_cards: tuple
    pending: _Purchase | None = None


class PrizeService:
    def __init__(self, economy, *, seed=None, raw_rng=None):
        self.economy = economy
        self._rng = random.Random(seed)
        self._raw_rng = raw_rng
        self._pending_awards = {}
        self._known_awards = {}
        self._last = {}
        self._active = {}
        self._closed = {}

    def stage_state(self, economy, key):
        """Keep draws and working purchases isolated until the joint save."""
        staged = copy(self)
        staged.economy = economy
        staged._rng = deepcopy(self._rng)
        staged._pending_awards = dict(self._pending_awards)
        staged._known_awards = dict(self._known_awards)
        staged._last = dict(self._last)
        staged._active = dict(self._active)
        staged._closed = dict(self._closed)
        if key in self._known_awards:
            staged._known_awards[key] = set(self._known_awards[key])
        if key in self._active:
            row = self._active[key]
            pending = row.pending
            if pending is not None:
                pending = replace(pending, cards=list(pending.cards), names=dict(pending.names))
            staged._active[key] = replace(row, pending=pending)
        return staged

    def publish_state(self, staged):
        self._rng = staged._rng
        self._pending_awards = staged._pending_awards
        self._known_awards = staged._known_awards
        self._last = staged._last
        self._active = staged._active
        self._closed = staged._closed

    def _draw(self):
        # Bind to this service's generator, including a staged service copy.
        return self._raw_rng() if self._raw_rng is not None else self._rng.randrange(32768)

    def is_active(self, key):
        return key in self._active

    def can_replay_quit(self, key, payload):
        """Only the last completed visit's exact Quit may use an idle receipt.

        B2 is shared with shops and auction collections. Their active sessions
        take precedence even when their independently allocated byte matches.
        """
        if (
            key not in self._closed
            or self.economy.is_busy(key)
            or not payload.startswith((b"GTM0GB2", b"GTM0Gb2"))
        ):
            return False
        _, subtype, number, sections = parse_named(payload)
        return (
            subtype == 0x17
            and number == self._closed[key]
            and sections == {"Quit": {"No": str(number)}}
        )

    def status(self, key):
        session = self._active.get(key)
        return (
            None
            if session is None
            else dict(
                session=session.number,
                points=session.token.points,
                pending_pack=None if session.pending is None else session.pending.pack,
            )
        )

    @staticmethod
    def _award(award_id, top30, lucky, rookies, lucky_cards):
        if not isinstance(award_id, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", award_id):
            raise PrizeError("award requires a stable bounded policy ID")
        if any(type(v) is not int or not 0 <= v <= 65535 for v in (top30, lucky, rookies)):
            raise PrizeError("award exceeds original WORD field")
        cards = tuple(lucky_cards)
        if len(cards) != 3 or any(
            type(v) is not int or v not in range(250) and v != 65535 for v in cards
        ):
            raise PrizeError("invalid three Lucky Card model IDs")
        return Award(award_id, top30, lucky, rookies, cards)

    def grant_award(
        self, key, award_id, *, top30=0, lucky=0, rookies=0, lucky_cards=(65535, 65535, 65535)
    ):
        """Trusted input only. A repeated durable ID never grants points twice.

        One pending award bundle per account keeps LC and the three WORD awards
        coherent. A caller must wait for redemption before queuing another ID.
        """
        self.economy.account_view(key)
        award = self._award(award_id, top30, lucky, rookies, lucky_cards)
        if award_id in self._known_awards.get(key, ()):
            return False
        if key in self._pending_awards:
            raise PrizeError("account already has a pending award bundle")
        self._pending_awards[key] = award
        self._known_awards.setdefault(key, set()).add(award_id)
        return True

    @staticmethod
    def _init(number, points, award=None, lucky_cards=(65535, 65535, 65535)):
        awards = (0, 0, 0) if award is None else (award.top30, award.lucky, award.rookies)
        # This initialization byte is status: 1 means failure, not session1.
        return named(
            0xB2,
            0x13,
            0,
            f"@ExcInit=/N={number}/PP="
            + "|".join(map(str, (points,) + awards))
            + "/LC="
            + "|".join(map(str, lucky_cards)),
        )

    def _entry(self, key, subtype, header_session, sections):
        if subtype != 0:
            raise PrizeError("invalid CvReq subtype")
        _fields(sections, ("CvReq",))
        f = sections["CvReq"]
        _fields(f, ("NN", "HID", "Dm", "Vol", "CN"))
        view = self.economy.account_view(key)
        if (
            not re.fullmatch(r"[0-9A-Fa-f]{16}", f["NN"])
            or int(f["NN"], 16) != view.nn
            or _hex(f["CN"], 16) != view.name
        ):
            raise PrizeError("CvReq identity differs from authenticated account")
        _integer(f["HID"])
        _integer(f["Dm"], 255)
        _integer(f["Vol"], 65535)
        current = self._active.get(key)
        if current is not None:
            if current.pending is not None:
                raise PrizeError("cannot reinitialize an unaccepted prize purchase")
            return list(current.entry_reply)
        token = self.economy.reserve_prize(key)
        award = self._pending_awards.get(key)
        number = self._last.get(key, 0) % 255 + 1
        lucky_cards = (65535, 65535, 65535) if award is None else award.lucky_cards
        replies = (
            named(0x71, 0, header_session, "@CvEnter=/EN=1"),
            self._init(number, token.points, award, lucky_cards),
        )
        try:
            token = self.economy.commit_prize(
                token, credit=0 if award is None else award.top30 + award.lucky + award.rookies
            )
        except Exception:
            self.economy.release_prize(token)
            raise
        self._pending_awards.pop(key, None)
        self._last[key] = number
        self._active[key] = _Session(number, token, replies, lucky_cards)
        self._closed.pop(key, None)
        return list(replies)

    def handle(self, key, payload):
        if not payload.startswith((b"GTM0G70", b"GTM0GB2", b"GTM0Gb2")):
            return None
        family, subtype, number, sections = parse_named(payload)
        if family == 0x70:
            return self._entry(key, subtype, number, sections)
        session = self._active.get(key)
        if session is None:
            if self.can_replay_quit(key, payload):
                return [named(0xB2, 0x17, number, f"@Quit=/N={number}/D=0/B=0")]
            return None
        if number != session.number:
            raise PrizeError("stale Prize Center session")
        if subtype == 0x14:
            _fields(sections, ("Buy",))
            _fields(sections["Buy"], ("No",))
            pack_id = _integer(sections["Buy"]["No"], 255)
            if session.pending is not None:
                return (
                    [session.pending.reply]
                    if session.pending.pack == pack_id
                    else [named(0xB2, 0x14, number, "@Card=/S=0")]
                )
            if not 45 <= pack_id <= 74:
                return [named(0xB2, 0x14, number, "@Card=/S=0")]
            pack = self.economy.catalog.packs[pack_id]
            if pack["price"] > session.token.points:
                return [named(0xB2, 0x14, number, "@Card=/S=0")]
            if pack["card_count"] != 1:
                raise PrizeError("decoded prize catalogue differs from original one-card flow")
            model = self.economy.catalog.select(pack_id, session.token.account.guild, self._draw)
            card = self.economy.catalog.generate(model, self._draw)
            reply = named(0xB2, 0x14, number, "@Card=/S=1@N0=/D=" + "|".join(map(str, card.full())))
            session.pending = _Purchase(
                pack_id,
                pack["price"],
                (card,),
                list(session.token.account.cards) + [card],
                dict(session.token.account.deck_names),
                reply,
            )
            return [reply]
        if subtype in (0x15, 0x17):
            token = "Get" if subtype == 0x15 else "Quit"
            _fields(sections, (token,))
            _fields(sections[token], ("No",))
            if _integer(sections[token]["No"], 255) != number:
                raise PrizeError("body session differs from envelope")
            if subtype == 0x15:
                if session.pending is not None:
                    p = session.pending
                    session.token = self.economy.commit_prize(
                        session.token,
                        add=p.incoming,
                        cards=p.cards,
                        deck_names=p.names,
                        debit=p.cost,
                        allow_discard=True,
                    )
                    session.pending = None
                    # A subsequent entry retry must not reset the balance to the
                    # original pre-purchase amount or re-display redeemed awards.
                    session.entry_reply = (
                        session.entry_reply[0],
                        self._init(number, session.token.points, lucky_cards=session.lucky_cards),
                    )
                return []
            if session.pending is not None:
                raise PrizeError("accept or reconnect before leaving a pending prize purchase")
            self.economy.release_prize(session.token)
            del self._active[key]
            self._closed[key] = number
            return [named(0xB2, 0x17, number, f"@Quit=/N={number}/D=0/B=0")]
        if subtype not in (0x10, 0x22):
            raise PrizeError("unsupported Prize Center operation")
        self._edit(session, subtype, sections)
        return []

    def _edit(self, session, subtype, sections):
        token = "Erase" if subtype == 0x10 else "Decks"
        if token not in sections:
            raise PrizeError("missing edit section")
        fields = sections[token]
        if subtype == 0x10:
            mode = _integer(fields.get("Mode"), 2)
            if mode == 2:
                if sections != {"Erase": {"Mode": "2"}}:
                    raise PrizeError("Mode2 carries no card list")
                return
            if mode != 1:
                raise PrizeError("unsupported Erase mode")
            _fields(fields, ("Mode", "C"))
        else:
            _fields(fields, ("C",))
        values, renamed = parse_edit_records(sections, token, number=_integer, error=PrizeError)
        pending = session.pending
        current = session.token.account.cards if pending is None else pending.cards
        names = session.token.account.deck_names if pending is None else pending.names
        cards, _ = apply_card_edits(current, values, discard=subtype == 0x10, error=PrizeError)
        names = dict(names) | renamed
        if pending is None:
            session.token = self.economy.commit_prize(
                session.token, cards=cards, deck_names=names, allow_discard=True
            )
        else:
            pending.cards, pending.names = cards, names

    def disconnect(self, key):
        self._closed.pop(key, None)
        session = self._active.pop(key, None)
        if session is None:
            return False
        self.economy.release_prize(session.token)
        return True

    def snapshot(self):
        keys = set(self._known_awards) | set(self._last)
        return {
            "version": 1,
            "accounts": {
                key: {
                    "last_session": self._last.get(key, 0),
                    "known_awards": sorted(self._known_awards.get(key, ())),
                    "pending": (
                        None
                        if key not in self._pending_awards
                        else asdict(self._pending_awards[key])
                    ),
                }
                for key in sorted(keys)
            },
        }

    @classmethod
    def restore(cls, economy, snapshot=None, *, seed=None, raw_rng=None):
        result = cls(economy, seed=seed, raw_rng=raw_rng)
        if snapshot is None:
            return result
        try:
            if not isinstance(snapshot, dict) or set(snapshot) != {"version", "accounts"}:
                raise PrizeError("unsupported Prize Center snapshot fields")
            if snapshot["version"] != 1 or not isinstance(snapshot["accounts"], dict):
                raise PrizeError("unsupported Prize Center snapshot")
            for key, data in snapshot["accounts"].items():
                if not isinstance(data, dict) or set(data) != {
                    "last_session",
                    "known_awards",
                    "pending",
                }:
                    raise PrizeError("unsupported Prize Center account fields")
                economy.account_view(key)
                last = data["last_session"]
                if type(last) is not int or not 0 <= last <= 255:
                    raise PrizeError("invalid persisted prize session")
                ids = data["known_awards"]
                if not isinstance(ids, list) or len(ids) != len(set(ids)):
                    raise PrizeError("invalid persisted award IDs")
                for award_id in ids:
                    result._award(award_id, 0, 0, 0, (65535,) * 3)
                result._known_awards[key] = set(ids)
                result._last[key] = last
                if data["pending"] is not None:
                    a = data["pending"]
                    if not isinstance(a, dict) or set(a) != {
                        "id",
                        "top30",
                        "lucky",
                        "rookies",
                        "lucky_cards",
                    }:
                        raise PrizeError("unsupported pending prize award fields")
                    award = result._award(
                        a["id"], a["top30"], a["lucky"], a["rookies"], a["lucky_cards"]
                    )
                    if award.id not in result._known_awards[key]:
                        raise PrizeError("pending prize award lacks its durable ID")
                    result._pending_awards[key] = award
        except (TypeError, ValueError, KeyError) as exc:
            if isinstance(exc, PrizeError):
                raise
            raise PrizeError("invalid Prize Center snapshot") from exc
        return result
