"""Load explicit local event fixtures; never derive historical award policies."""

import json
from pathlib import Path

from tmserver.events.matchmaking import EventTableConfig
from tmserver.events.models import EventConfig, EventListing, PrizeDisplay
from tmserver.events.packets import event_data
from tmserver.protocol.identities import game_id
from tmserver.game.models import Card


def _fields(value, required, optional=()):
    if (
        not isinstance(value, dict)
        or not set(required) <= set(value)
        or set(value) - set(required) - set(optional)
    ):
        raise ValueError("missing or unknown event configuration fields")
    return dict(value)


def _text(value):
    if not isinstance(value, str):
        raise ValueError("event text must be a string")
    try:
        return value.encode("latin1")
    except UnicodeEncodeError as exc:
        raise ValueError("event text must fit the configured PC client encoding") from exc


def parse_event_configs(document):
    root = _fields(document, ("events",), ("tables", "progress_policy"))
    if not isinstance(root["events"], list):
        raise ValueError("events must be a list")
    configs = []
    for row in root["events"]:
        values = _fields(
            row,
            ("id", "listing", "data", "start", "end", "room_raw", "init_raw", "card_raw"),
            (
                "init_channel",
                "card_channel",
                "entry_answer",
                "match_enabled",
                "card_service_enabled",
            ),
        )
        listing = _fields(
            values.pop("listing"),
            ("key_region", "key_event", "description"),
            ("scalar", "flags", "start", "end"),
        )
        for key in ("key_region", "key_event", "description"):
            listing[key] = _text(listing[key])
        if "flags" in listing:
            listing["flags"] = tuple(listing["flags"])
        data = _fields(
            values.pop("data"),
            (),
            ("start", "end", "classes", "steps_per_class", "prizes", "missions"),
        )
        for key in ("start", "end"):
            if key in data:
                data[key] = tuple(data[key])
        if "classes" in data:
            data["classes"] = tuple(_text(v) for v in data["classes"])
        prizes = []
        for prize in data.pop("prizes", []):
            prize = _fields(prize, (), ("cards", "money", "description"))
            if "cards" in prize:
                prize["cards"] = tuple(prize["cards"])
            if "description" in prize:
                prize["description"] = _text(prize["description"])
            prizes.append(PrizeDisplay(**prize))
        data["prizes"] = tuple(prizes)
        if "missions" in data:
            missions = data["missions"]
            if not isinstance(missions, dict) or any(
                k not in tuple(map(str, range(8))) for k in missions
            ):
                raise ValueError("mission keys must be original bit numbers0..7")
            data["missions"] = {int(k): v for k, v in missions.items()}
        for key in ("room", "init", "card"):
            raw = values.pop(key + "_raw")
            if type(raw) is not int or not 0 <= raw <= 0x1FFFFFFFFFF:
                raise ValueError("event route must be a raw41-bit POL integer")
            values[key + "_id"] = game_id(raw)
        for key in ("init_channel", "card_channel"):
            if key in values:
                values[key] = _text(values[key])
        configs.append(
            EventConfig(listing=EventListing(**listing), data=event_data(**data), **values)
        )
    return tuple(configs)


def load_event_configs(path):
    return parse_event_configs(json.loads(Path(path).read_text(encoding="utf8")))


def parse_event_setup(document):
    """Return validated events, explicit match tables and local progress policy."""
    configs = parse_event_configs(document)
    rows = document.get("tables", [])
    if not isinstance(rows, list):
        raise ValueError("event tables must be a list")
    tables = []
    for row in rows:
        values = _fields(
            row,
            ("event_id", "table_raw", "channel"),
            ("player_count", "cpu_cards", "turn_limit", "departure_rule", "seed", "start_fields"),
        )
        raw = values.pop("table_raw")
        if type(raw) is not int or not 0 <= raw <= 0x1FFFFFFFFFF:
            raise ValueError("event table route must be a raw41-bit POL integer")
        values["table_id"] = game_id(raw)
        values["channel"] = _text(values["channel"])
        if "cpu_cards" in values:
            if not isinstance(values["cpu_cards"], list):
                raise ValueError("CPU cards must be an array")
            cards = []
            for fields in values["cpu_cards"]:
                if (
                    not isinstance(fields, list)
                    or len(fields) != 8
                    or any(type(x) is not int for x in fields)
                ):
                    raise ValueError("CPU cards require eight original integer fields")
                cards.append(Card(*fields))
            values["cpu_cards"] = tuple(cards)
        if "start_fields" in values:
            fields = values["start_fields"]
            if not isinstance(fields, list):
                raise ValueError("event board must be an array")
        tables.append(EventTableConfig(**values))
    if len({c.table_id for c in tables}) != len(tables):
        raise ValueError("duplicate configured event table")
    if {c.id for c in configs if c.match_enabled and c.entry_answer == 1} != {
        c.event_id for c in tables
    }:
        raise ValueError("each enabled event match needs at least one explicit table")
    policy = document.get("progress_policy", "none")
    if policy not in ("none", "wins", "rounds"):
        raise ValueError("invalid local event progress policy")
    return configs, tuple(tables), policy


def load_event_setup(path):
    return parse_event_setup(json.loads(Path(path).read_text(encoding="utf8")))
