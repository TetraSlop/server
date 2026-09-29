"""Load explicit local event fixtures; never derive historical award policies."""

import json
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, TypeAdapter

from tmserver.events.models import EventConfig, EventListing, EventTableConfig, PrizeDisplay
from tmserver.events.packets import event_data
from tmserver.game.models import Card
from tmserver.protocol.identities import game_id


def _text(value):
    if not isinstance(value, str):
        raise ValueError("event text must be a string")
    try:
        return value.encode("latin1")
    except UnicodeEncodeError as exc:
        raise ValueError("event text must fit the configured PC client encoding") from exc


_CLIENT_TEXT = Annotated[bytes, BeforeValidator(_text)]
_OBJECT = TypeAdapter(dict[str, Any])
_INTEGERS = TypeAdapter(list[int])
_RAW_ROUTE = TypeAdapter(Annotated[int, Field(ge=0, le=0x1FFFFFFFFFF)])
_CPU_CARDS = TypeAdapter(list[Annotated[list[int], Field(min_length=8, max_length=8)]])
_TABLES = TypeAdapter(list[dict[str, Any]])
_POLICY = TypeAdapter(Literal["none", "wins", "rounds"])


class _EventData(BaseModel):
    """JSON inputs for the packed event-data file; byte layout stays in packets."""

    model_config = ConfigDict(strict=True, extra="forbid")
    start: list[int] = Field(default_factory=lambda: [0] * 5)
    end: list[int] = Field(default_factory=lambda: [0] * 5)
    classes: list[_CLIENT_TEXT] = Field(default_factory=list)
    steps_per_class: int = 1
    prizes: list[dict[str, Any]] = Field(default_factory=list)
    missions: dict[Literal["0", "1", "2", "3", "4", "5", "6", "7"], int] = Field(
        default_factory=dict
    )

    def packed(self):
        values = self.model_dump(exclude={"prizes", "missions"})
        values["missions"] = {int(key): value for key, value in self.missions.items()}
        values["prizes"] = []
        for row in self.prizes:
            prize = _decode_text(row, "description")
            if "cards" in prize:
                prize["cards"] = tuple(_INTEGERS.validate_python(prize["cards"], strict=True))
            values["prizes"].append(PrizeDisplay(**prize))
        return event_data(**values)


class _Document(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    events: list[dict[str, Any]]
    # parse_event_configs historically reads events alone. Setup validates the
    # optional table and policy sections when that broader entry point is used.
    tables: Any = Field(default_factory=list)
    progress_policy: Any = "none"


def _decode_text(row, *names):
    values = _OBJECT.validate_python(row, strict=True)
    for name in names:
        if name in values:
            values[name] = _text(values[name])
    return values


def _route(values, name):
    if name + "_id" in values:
        raise ValueError("event configuration routes must use raw POL fields")
    try:
        raw = values.pop(name + "_raw")
    except KeyError as exc:
        raise ValueError(f"missing event route {name}_raw") from exc
    values[name + "_id"] = game_id(_RAW_ROUTE.validate_python(raw, strict=True))


def _events(rows):
    configs = []
    for row in rows:
        values = _decode_text(row, "init_channel", "card_channel")
        try:
            listing = _decode_text(values.pop("listing"), "key_region", "key_event", "description")
            if "flags" in listing:
                listing["flags"] = tuple(_INTEGERS.validate_python(listing["flags"], strict=True))
            values["listing"] = EventListing(**listing)
            values["data"] = _EventData.model_validate(values["data"]).packed()
        except KeyError as exc:
            raise ValueError(f"missing event configuration field {exc.args[0]}") from exc
        for name in ("room", "init", "card"):
            _route(values, name)
        configs.append(EventConfig(**values))
    return tuple(configs)


def parse_event_configs(document):
    return _events(_Document.model_validate(document).events)


def load_event_configs(path):
    return parse_event_configs(json.loads(Path(path).read_text(encoding="utf8")))


def parse_event_setup(document):
    """Return validated events, explicit match tables and local progress policy."""
    root = _Document.model_validate(document)
    configs = _events(root.events)
    tables = []
    for row in _TABLES.validate_python(root.tables, strict=True):
        values = _decode_text(row, "channel")
        _route(values, "table")
        if "cpu_cards" in values:
            values["cpu_cards"] = tuple(
                Card(*fields)
                for fields in _CPU_CARDS.validate_python(values["cpu_cards"], strict=True)
            )
        if "start_fields" in values:
            values["start_fields"] = _INTEGERS.validate_python(values["start_fields"], strict=True)
        tables.append(EventTableConfig(**values))
    if len({c.table_id for c in tables}) != len(tables):
        raise ValueError("duplicate configured event table")
    if {c.id for c in configs if c.match_enabled and c.entry_answer == 1} != {
        c.event_id for c in tables
    }:
        raise ValueError("each enabled event match needs at least one explicit table")
    return configs, tuple(tables), _POLICY.validate_python(root.progress_policy, strict=True)


def load_event_setup(path):
    return parse_event_setup(json.loads(Path(path).read_text(encoding="utf8")))
