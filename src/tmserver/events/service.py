"""Hosted event admission, membership and durable progress state."""

import re
import time
from copy import copy
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from tmserver.checks import STABLE_ID
from tmserver.economy.errors import EconomyError
from tmserver.economy.packets import named, parse_named

from .models import (
    EventAdmission,
    EventMember,
)
from .packets import (
    SIZES,
    _id,
    _identity,
    _keys,
    _number,
    event_data,
    event_list,
    event_members,
    integer,
    text,
    time_reply,
)


_SAVED = ConfigDict(strict=True, extra="forbid")


class _SavedMember(BaseModel):
    model_config = _SAVED
    key: str
    identity: int
    name: Annotated[str, Field(pattern=r"\A(?:[0-9a-fA-F]{2}){1,15}\z")]
    steps: int
    completed_missions: int
    eligibility: int


class _SavedUpdate(BaseModel):
    model_config = _SAVED
    key: str
    id: Annotated[str, Field(pattern=rf"\A{STABLE_ID}\z")]
    steps_delta: Annotated[int, Field(ge=0, le=0x7FFFFFFF)]
    completed_missions: Annotated[int, Field(ge=0, le=0xFFFFFFFF)]


class _SavedEvent(BaseModel):
    model_config = _SAVED
    id: str
    configuration: str
    members: list[_SavedMember]
    updates: list[_SavedUpdate]


class _EventSnapshot(BaseModel):
    model_config = _SAVED
    version: Annotated[int, Field(ge=1, le=1)]
    events: list[_SavedEvent]


# Empty, compatible browse files until a tournament is explicitly hosted.
EMPTY_FILES = {
    "b/g/TM0EventList": event_list(),
    "b/g/TM0EventDataList": event_data(),
    "b/g/TM0EventMemberList": event_members(),
}


class EventHost:
    """Authenticated event control and durable member/progress storage.

    Network adapters persist snapshot jointly with accounts before replies.
    DE membership validation/routing and match reservation remain adapter work.
    This object never accepts progress facts from the wire or grants a prize.
    """

    def __init__(self, economy, configs=(), *, clock=time.time):
        self.economy = economy
        self.clock = clock
        configs = tuple(configs)
        self.configs = {config.id: config for config in configs}
        if len(self.configs) != len(configs):
            raise EconomyError("duplicate event ID")
        event_list(tuple(config.listing for config in configs))
        self._members = {c.id: {} for c in configs}
        self._updates = {c.id: {} for c in configs}
        self._present = {}
        self._admissions = {}

    def stage_state(self, economy):
        """Membership, progress facts and admissions are immutable map values."""
        staged = copy(self)
        staged.economy = economy
        staged._members = {event: dict(rows) for event, rows in self._members.items()}
        staged._updates = {event: dict(rows) for event, rows in self._updates.items()}
        staged._present = dict(self._present)
        staged._admissions = dict(self._admissions)
        return staged

    def publish_state(self, staged):
        self._members = staged._members
        self._updates = staged._updates
        self._present = staged._present
        self._admissions = staged._admissions

    def config(self, event_id):
        try:
            return self.configs[event_id]
        except KeyError as exc:
            raise EconomyError("unknown hosted event") from exc

    def _now(self):
        return integer(int(self.clock()), signed=True)

    def event_for_service(self, identity):
        for c in self.configs.values():
            for route, value in (("room", c.room_id), ("init", c.init_id), ("card", c.card_id)):
                if value == identity:
                    return c.id, route
        return None

    def file(self, path, event_id=None):
        if path == "b/g/TM0EventList":
            return event_list(tuple(c.listing for c in self.configs.values()))
        if path not in SIZES:
            return None
        if event_id is None:
            if self.configs:
                raise EconomyError("event file owner is ambiguous")
            return EMPTY_FILES[path]
        config = self.config(event_id)
        if path == "b/g/TM0EventDataList":
            return config.data
        return event_members(tuple(self._members[event_id].values()))

    def member(self, key, event_id):
        self.config(event_id)
        self.economy.account_view(key)
        return self._members[event_id].get(key)

    def admit_room(self, key, event_id):
        """Call only after authenticated DE succeeds, then send D0 from room."""
        config = self.config(event_id)
        view = self.economy.account_view(key)
        old = self._members[event_id].get(key)
        # Existing members can return after end to collect durable grants.
        denied = (
            old is None and self._now() >= config.end or old is not None and old.eligibility < 0
        )
        denied = denied or old is None and len(self._members[event_id]) >= 256
        if denied:
            return [named(0xD0, 0, 0, "@EventEn=/Ans=-32870")]
        text(view.name, 16, empty=False)
        member = EventMember(
            view.nn,
            view.name[:15],  # The member list has a terminated 16-byte display field.
            0 if old is None else old.steps,
            0 if old is None else old.completed_missions,
            0 if old is None else old.eligibility,
        )
        # An identity cannot appear twice under different authenticated keys.
        if any(
            k != key and m.identity == member.identity for k, m in self._members[event_id].items()
        ):
            raise EconomyError("event member identity already belongs to another account")
        self._members[event_id][key] = member
        self._present[key] = event_id
        self._admissions.pop(key, None)
        return [named(0xD0, 0, 0, "@EventEn=/Ans=1")]

    def disconnect(self, key):
        self._present.pop(key, None)
        self._admissions.pop(key, None)

    def leave_room(self, key, event_id):
        if self._present.get(key) == event_id:
            self.disconnect(key)

    def admission(self, key):
        return self._admissions.get(key)

    def complete_admission(self, key):
        """Adapter consumes a pending admission after reserving/registering match."""
        return self._admissions.pop(key, None)

    def _auth(self, key, f, with_name=False, with_gid=False):
        view = self.economy.account_view(key)
        if _identity(f["NN"]) != view.nn:
            raise EconomyError("event NN differs from authenticated account")
        if with_name:
            value = f["CN"]
            if (
                not re.fullmatch(r"[0-9A-Fa-f]{0,32}", value)
                or len(value) % 2
                or bytes.fromhex(value) != view.name
            ):
                raise EconomyError("event CN differs from authenticated account")
            _number(f["HID"], 0xFFFFFFFF)
            _number(f["Dm"], 255)
            _number(f["Vol"], 65535)
        if with_gid and _identity(f["GID"]) not in (view.player_id, view.nn):
            raise EconomyError("event GID is not an owned local character alias")
        return view

    def authenticate_entry(self, key, payload, *, route):
        """Identity-only validation for a delivery service's entry replay."""
        family, subtype, sequence, sections = parse_named(payload)
        if family not in (0x90, 0x98):
            raise EconomyError("not an event entry request")
        kind = "card" if family == 0x90 else "init"
        token = "CardReq" if family == 0x90 else "EInitReq"
        if route != kind or subtype != 0:
            raise EconomyError("event entry on wrong service/subtype")
        _keys(sections, (token,))
        fields = sections[token]
        _keys(fields, ("NN", "HID", "Dm", "Vol", "CN") + (("GID",) if family == 0x98 else ()))
        return sequence, self._auth(key, fields, True, family == 0x98)

    def handle(self, key, payload, *, event_id, route):
        if payload[:7].upper() not in (b"GTM0G90", b"GTM0G98", b"GTM0GD1"):
            return None
        config = self.config(event_id)
        family, subtype, sequence, sections = parse_named(payload)
        if family == 0xD1:
            if subtype != 4 or route != "room":
                raise EconomyError("EventTimeReq belongs to the event room route")
            _keys(sections, ("EventTimeReq",))
            _keys(sections["EventTimeReq"], ("NN",))
            self._auth(key, sections["EventTimeReq"])
            return [time_reply(config.start, config.end, self._now())]
        kind = "card" if family == 0x90 else "init"
        sequence, view = self.authenticate_entry(key, payload, route=route)
        member = self._members[event_id].get(key)
        now = self._now()
        answer = 1 if family == 0x90 else config.entry_answer
        enabled = config.card_service_enabled if family == 0x90 else config.match_enabled
        if (
            member is None
            or not enabled
            or family == 0x98
            and (self._present.get(key) != event_id or now < config.start)
        ):
            answer = -32870
        elif member.eligibility < 0:
            answer = -32872 if family == 0x98 else -32870
        elif family == 0x98 and now >= config.end:
            answer = -32968
        elif self.economy.is_busy(key):
            answer = -32870
        if answer > 0 and (family == 0x90 or answer == 2 or len(view.cards) >= 5):
            self._admissions[key] = EventAdmission(
                key, event_id, kind, None if family == 0x90 else 4 if answer == 1 else 8, member
            )
        else:
            self._admissions.pop(key, None)
        # Mode4/Card0 opens shortage when <5 cards. Mode8 skips owned selection.
        body = f"@CardEnt=/Ans={answer}" if family == 0x90 else f"@EInitEnt=/Card=0/Ans={answer}"
        return [named(0x91 if family == 0x90 else 0x99, 0, sequence, body)]

    def progress_update(self, key, event_id, update_id):
        """Read immutable trusted facts without advancing or creating progress."""
        self.config(event_id)
        return self._updates[event_id].get((key, update_id))

    def update_progress(self, key, event_id, update_id, *, steps_delta=0, completed_missions=0):
        """Trusted engine/policy facts only; replay-safe and never grants assets.

        completed_missions contains newly proven bits, not opaque client claims.
        The caller defines the local policy; this module knows no threshold rule.
        """
        config = self.config(event_id)
        _id(update_id)
        integer(steps_delta)
        integer(completed_missions)
        if steps_delta > 0x7FFFFFFF or completed_missions & ~config.mission_mask:
            raise EconomyError("progress exceeds configured display domain")
        member = self.member(key, event_id)
        if member is None:
            raise EconomyError("progress requires a durable member")
        identity = (key, update_id)
        facts = (steps_delta, completed_missions)
        old = self._updates[event_id].get(identity)
        if old is not None:
            if old != facts:
                raise EconomyError("progress ID replay has different facts")
            return False
        if member.eligibility < 0:
            raise EconomyError("excluded event member cannot progress")
        value = member.steps + steps_delta
        if value > 0x7FFFFFFF:
            raise EconomyError("event steps overflow native signed sorter")
        self._members[event_id][key] = EventMember(
            member.identity,
            member.name,
            value,
            member.completed_missions | completed_missions,
            member.eligibility,
        )
        self._updates[event_id][identity] = facts
        return True

    def set_eligibility(self, key, event_id, value):
        """Trusted administration; negative values exclude/reject this member."""
        integer(value, signed=True)
        member = self.member(key, event_id)
        if member is None:
            raise EconomyError("eligibility requires a durable member")
        self._members[event_id][key] = EventMember(
            member.identity, member.name, member.steps, member.completed_missions, value
        )
        if value < 0:
            self._admissions.pop(key, None)

    def time_update(self, event_id):
        c = self.config(event_id)
        return named(0xD1, 3, 0, f"@EventTime=/Start={c.start}/End={c.end}/Now={self._now()}")

    def time_notice(self, event_id, subtype, *, close=None, ecm=None):
        c = self.config(event_id)
        if subtype == 1:
            body = f"/Start={c.start}"
        elif subtype == 2:
            body = f"/End={c.end}"
        elif subtype == 8:
            integer(close, signed=True)
            body = f"/Close={close}"
            if ecm is not None:
                integer(ecm, signed=True)
                body += f"/ECM={ecm}"
        else:
            raise EconomyError("unsupported event time notice")
        return named(0xD1, subtype, 0, "@EventTime=" + body)

    def snapshot(self):
        return _EventSnapshot(
            version=1,
            events=[
                _SavedEvent(
                    id=eid,
                    configuration=self.configs[eid].fingerprint(),
                    members=[
                        _SavedMember(
                            key=key,
                            identity=m.identity,
                            name=m.name.hex(),
                            steps=m.steps,
                            completed_missions=m.completed_missions,
                            eligibility=m.eligibility,
                        )
                        for key, m in sorted(rows.items())
                    ],
                    updates=[
                        _SavedUpdate(
                            key=key, id=uid, steps_delta=delta, completed_missions=missions
                        )
                        for (key, uid), (delta, missions) in sorted(self._updates[eid].items())
                    ],
                )
                for eid, rows in sorted(self._members.items())
            ],
        ).model_dump()

    @classmethod
    def restore(cls, economy, configs=(), snapshot=None, *, clock=time.time):
        result = cls(economy, configs, clock=clock)
        if snapshot is None:
            return result
        try:
            saved = _EventSnapshot.model_validate(snapshot)
            seen = set()
            for row in saved.events:
                eid = row.id
                config = result.config(eid)
                if eid in seen or row.configuration != config.fingerprint():
                    raise EconomyError("saved event configuration differs")
                seen.add(eid)
                for record in row.members:
                    key = record.key
                    view = economy.account_view(key)
                    if key in result._members[eid] or record.identity != view.nn:
                        raise EconomyError("saved event member owner differs")
                    member = EventMember(
                        record.identity,
                        bytes.fromhex(record.name),
                        record.steps,
                        record.completed_missions,
                        record.eligibility,
                    )
                    if member.completed_missions & ~config.mission_mask:
                        raise EconomyError("saved member has disabled mission")
                    result._members[eid][key] = member
                # The member file codec bounds identities, names and counters.
                event_members(tuple(result._members[eid].values()))
                totals = {key: [0, 0] for key in result._members[eid]}
                for update in row.updates:
                    key = update.key
                    identity = (key, update.id)
                    if key not in totals or identity in result._updates[eid]:
                        raise EconomyError("invalid progress update owner/ID")
                    facts = (update.steps_delta, update.completed_missions)
                    if facts[1] & ~config.mission_mask:
                        raise EconomyError("invalid saved progress facts")
                    totals[key][0] += facts[0]
                    totals[key][1] |= facts[1]
                    result._updates[eid][identity] = facts
                for key, m in result._members[eid].items():
                    if totals[key] != [m.steps, m.completed_missions]:
                        raise EconomyError("saved progress differs from update history")
        except (KeyError, ValueError, TypeError, AttributeError) as exc:
            if isinstance(exc, EconomyError):
                raise
            raise EconomyError("invalid event snapshot") from exc
        return result
