"""Load configured events from JSON."""

import json
from pathlib import Path
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field

from tmserver.events.models import EventConfig, EventListing, EventTableConfig, PrizeDisplay, Text
from tmserver.events.packets import event_data
from tmserver.game.models import Card
from tmserver.protocol.identities import game_id

_STRICT = ConfigDict(strict=True, extra="forbid")
# The JSON names raw POL routes; the configuration models hold game-domain IDs.
_Route = Annotated[int, Field(ge=0, le=0x1FFFFFFFFFF), AfterValidator(game_id)]


def _given(model):
    """Only the fields the document set, so the rest take the dataclass defaults."""
    return {name: getattr(model, name) for name in model.model_fields_set}


class _EventData(BaseModel):
    """JSON inputs for the packed event-data file; byte layout stays in packets."""

    model_config = _STRICT
    start: list[int] = Field(default_factory=lambda: [0] * 5)
    end: list[int] = Field(default_factory=lambda: [0] * 5)
    classes: list[Text] = Field(default_factory=list)
    steps_per_class: int = 1
    prizes: list[PrizeDisplay] = Field(default_factory=list)
    missions: dict[Literal["0", "1", "2", "3", "4", "5", "6", "7"], int] = Field(
        default_factory=dict
    )

    def packed(self):
        return event_data(
            start=self.start,
            end=self.end,
            classes=self.classes,
            steps_per_class=self.steps_per_class,
            prizes=self.prizes,
            missions={int(key): value for key, value in self.missions.items()},
        )


class _Event(BaseModel):
    model_config = _STRICT
    id: str
    listing: EventListing
    data: _EventData
    start: int
    end: int
    room_raw: _Route
    init_raw: _Route
    card_raw: _Route
    init_channel: Text = None
    card_channel: Text = None
    entry_answer: int = None
    match_enabled: bool = None
    card_service_enabled: bool = None

    def config(self):
        values = _given(self)
        for name in ("room", "init", "card"):
            values[name + "_id"] = values.pop(name + "_raw")
        values["data"] = self.data.packed()
        return EventConfig(**values)


class _Table(BaseModel):
    model_config = _STRICT
    event_id: str
    table_raw: _Route
    channel: Text
    player_count: int = None
    cpu_cards: list[Annotated[list[int], Field(min_length=8, max_length=8)]] = None
    turn_limit: int = None
    departure_rule: int = None
    seed: int = None
    start_fields: list[int] = None

    def config(self):
        values = _given(self)
        values["table_id"] = values.pop("table_raw")
        if "cpu_cards" in values:
            values["cpu_cards"] = tuple(Card(*fields) for fields in values["cpu_cards"])
        return EventTableConfig(**values)


class _Document(BaseModel):
    model_config = _STRICT
    events: list[_Event]
    tables: list[_Table] = Field(default_factory=list)
    progress_policy: Literal["none", "wins", "rounds"] = "none"


def parse_event_setup(document):
    """Return validated events, explicit match tables and local progress policy."""
    try:
        text = json.dumps(document)
    except TypeError as exc:
        raise ValueError("event configuration must be plain JSON values") from exc
    # JSON mode reads arrays into tuple fields and strings into latin1 text,
    # while strict validation still rejects booleans, floats and strings for ints.
    root = _Document.model_validate_json(text)
    configs = tuple(event.config() for event in root.events)
    tables = tuple(table.config() for table in root.tables)
    if len({c.table_id for c in tables}) != len(tables):
        raise ValueError("duplicate configured event table")
    if {c.id for c in configs if c.match_enabled and c.entry_answer == 1} != {
        c.event_id for c in tables
    }:
        raise ValueError("each enabled event match needs at least one explicit table")
    return configs, tables, root.progress_policy


def load_event_setup(path):
    return parse_event_setup(json.loads(Path(path).read_text(encoding="utf8")))
