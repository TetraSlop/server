"""Pure inventory edits shared by shops, trades and reward collection.

Every service reads Erase, Decks and Sell sections with parse_edit_section, so
one rule applies everywhere. Services keep ownership of commit timing.
"""

import re
from dataclasses import replace
from functools import partial

from tmserver.checks import decimal


def deck_name(value):
    if len(value) > 34 or len(value) % 2 or re.fullmatch(r"[0-9a-fA-F]*", value) is None:
        raise ValueError("invalid deck name encoding")
    name = bytes.fromhex(value)
    if b"\0" in name:
        raise ValueError("embedded deck name terminator")
    return name


# Unsigned decimal text, the only number form the client writes.
edit_number = partial(decimal, what="edit field")


def parse_edit_section(sections, subtype, *, side=None, token=None, error=ValueError):
    """Validate an Erase (0x10) or Decks (0x22) message and read its records.

    Returns (values, renames), or None for the Erase Mode=2 completion marker.
    Mode is exactly "1" or "2", numbers are unsigned decimal, and the seat field
    P is required, and must equal side, exactly when side is given. The shop
    passes token="Sell" for its sale list, which has the Decks header shape.
    """
    token = token or ("Erase" if subtype == 0x10 else "Decks")
    try:
        head = sections[token]
        keys = {"C"} if side is None else {"P", "C"}
        if side is not None and edit_number(head.get("P")) != side:
            raise ValueError("edit side mismatch")
        if token == "Erase":
            if head.get("Mode") == "2":
                if set(head) != keys - {"C"} | {"Mode"} or set(sections) != {"Erase"}:
                    raise ValueError("Mode 2 carries no card list")
                return None
            if head.get("Mode") != "1":
                raise ValueError("unsupported Erase mode")
            keys |= {"Mode"}
        if set(head) != keys:
            raise ValueError("unexpected or missing edit header fields")
    except (ValueError, KeyError, TypeError) as exc:
        raise error(str(exc)) from exc
    return _records(sections, token, error)


def _records(sections, token, error):
    """Read the numbered card records and the optional single deck rename."""
    try:
        count = edit_number(sections[token]["C"])
        if not 0 <= count <= (13 if token == "Decks" else 15):
            raise ValueError("inventory edit exceeds original batch")
        expected = {token} | {str(i) for i in range(count)}
        renames = set(sections) - expected if token == "Decks" else set()
        if renames - {"DN1", "DN2"} or len(renames) > 1 or set(sections) != expected | renames:
            raise ValueError("unexpected or missing inventory edit sections")
        values = []
        for i in range(count):
            record = sections[str(i)]
            if set(record) != {"D"}:
                raise ValueError("invalid inventory card record")
            parts = record["D"].split("|")
            if len(parts) != (7 if token == "Decks" else 6):
                raise ValueError("incorrect inventory card tuple length")
            value = tuple(edit_number(part) for part in parts)
            if any(not 0 <= part <= (65535 if j == 0 else 255) for j, part in enumerate(value)):
                raise ValueError("inventory card field exceeds its width")
            if token == "Decks" and value[6] not in range(50) and value[6] != 255:
                raise ValueError("invalid deck slot")
            values.append(value)
        names = {}
        for section in renames:
            if set(sections[section]) != {"S"}:
                raise ValueError("invalid deck name fields")
            names[int(section[2:])] = deck_name(sections[section]["S"])
        return values, names
    except (ValueError, KeyError, TypeError) as exc:
        raise error(str(exc)) from exc


def match_owned(cards, requested, excluded=(), *, error=ValueError):
    """Wire tuples omit instance IDs and points; consume stable matching copies."""
    available = set(range(len(cards))) - set(excluded)
    indices = []
    for value in requested:
        index = next((i for i in sorted(available) if cards[i].reduced() == value), None)
        if index is None:
            raise error("card not owned or multiplicity exceeded")
        available.remove(index)
        indices.append(index)
    return indices


def apply_card_edits(cards, values, *, discard, excluded=(), error=ValueError):
    """Return new cards and matched indices without mutating the supplied cards.

    Exclusions protect trade offers from being selected. Deck assignments still
    clear the destination slot throughout the inventory, including those offers.
    """
    indices = match_owned(cards, (value[:6] for value in values), excluded, error=error)
    if discard:
        return [card for i, card in enumerate(cards) if i not in indices], indices
    slots = [value[6] for value in values if value[6] != 255]
    if len(slots) != len(set(slots)):
        raise error("duplicate deck assignment")
    updated = [replace(card, deck=255) if card.deck in slots else card for card in cards]
    for i, value in zip(indices, values):
        updated[i] = replace(updated[i], deck=value[6])
    return updated, indices
