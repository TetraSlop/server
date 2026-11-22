"""Ordinary shop: B0/00 and B2 traffic.

Layout and pack generation: TetraPackets flows/shop.md, irc/G/B2.md and
client/catalogue.md.

Server choice: a new account gets 10,000 T and a ten-card Starter's Pack, and
Python's RNG drives the client's generator. Catalogue categories 1 and 2 and the
account's guild pack are for sale; Pauper packs need fewer than 5 cards; rank and
event packs are not sold. The server sends no prices: the client uses its own
catalogue and ignores Init /M.

Buy stages one transaction, including overflow up to 1030 cards. Retrying the
same pack replays its Card response. Get commits the debit, the cards and any
edits made in between, once the inventory holds at most 1000 cards. A disconnect
discards the pending transaction. Sell/Erase/Decks on an existing account commit
at once. Mode 2 is only a marker, never erase-all. The wire cannot tell a
duplicate edit from two identical ones, so delivery stays ordered and nothing is
retried automatically. A malformed message raises EconomyError and changes
nothing; the caller should log it and close or resync the connection.
Insufficient funds or an unavailable pack get the nonpositive Card response.
"""

import re
import struct

from tmserver.cards.catalog import collection_points
from tmserver.cards.edits import apply_card_edits, parse_edit_section
from tmserver.economy.errors import EconomyError
from tmserver.economy.models import MAX_MONEY, _Pending, _State, check_inventory
from tmserver.economy.packets import _hex, _int, _keys, named


def _initial_reply(account, offers):
    s = account.committed
    # Original Init treats header byte1 as failure; /N carries the session.
    return named(
        0xB2,
        0x13,
        0,
        f"@Init=/N={account.session}/RA={struct.unpack_from('<I', account.template, 0x30)[0]}"
        f"/M={s.balance}/CP={collection_points(s.cards)}/S={len(s.cards)}"
        + offers.fields(),
    )

def handle(account, family, subtype, session, sections, *, catalog, offers, draw):
    """Handle a parsed request against the caller's staged account and RNG."""
    if family == 0xB0:
        if subtype != 0 or set(sections) != {"ShReq"}:
            raise EconomyError("only ordinary shop entry is implemented")
        fields = sections["ShReq"]
        _keys(fields, ("NN", "HID", "Dm", "Vol", "CN"))
        if (
            not re.fullmatch(r"[0-9A-Fa-f]{16}", fields["NN"])
            or int(fields["NN"], 16) != account.nn
        ):
            raise EconomyError("ShReq identity does not match authenticated account")
        _int(fields["HID"])
        _int(fields["Dm"], maximum=255)
        _int(fields["Vol"], maximum=65535)
        if _hex(fields["CN"], 16) != account.name:
            raise EconomyError("ShReq name does not match provisioned account")
        if account.pending:
            raise EconomyError("cannot reinitialize a pending purchase")
        if account.session is None:
            account.last_session = account.last_session % 255 + 1
            account.session = account.last_session
        return [named(0xB1, 0, session, "@ShEnter=/EN=1"), _initial_reply(account, offers)]
    if account.session is None:
        # A retry of the last completed Quit is harmless; all edits stay closed.
        if (
            subtype == 0x17
            and session == account.last_session
            and sections == {"Quit": {"No": str(session)}}
        ):
            return [named(0xB2, 0x17, session, f"@Quit=/N={session}/D=0/B=0")]
        raise EconomyError("shop session is not active")
    if session != account.session:
        raise EconomyError("stale shop session")
    if subtype == 0x14:
        if set(sections) != {"Buy"}:
            raise EconomyError("expected Buy")
        _keys(sections["Buy"], ("No",))
        pack_id = _int(sections["Buy"]["No"], maximum=255)
        if account.pending:
            if pack_id == account.pending.pack:
                return [account.pending.reply]
            return [named(0xB2, 0x14, session, "@Card=/S=0")]
        state = account.committed
        pack = catalog.packs[pack_id] if pack_id < len(catalog.packs) else None
        available = pack_id in offers.visible(
            catalog, account.guild, state.balance, len(state.cards)
        )
        if not available or pack["price"] > state.balance:
            return [named(0xB2, 0x14, session, "@Card=/S=0")]
        incoming = [
            catalog.generate(
                catalog.select(pack_id, account.guild, draw), draw
            )
            for _ in range(pack["card_count"])
        ]
        if not 1 <= len(incoming) <= 30 or len(state.cards) + len(incoming) > 1030:
            raise EconomyError("pack exceeds working inventory capacity")
        body = f"@Card=/S={len(incoming)}" + "".join(
            f"@N{i}=/D=" + "|".join(map(str, card.full())) for i, card in enumerate(incoming)
        )
        reply = named(0xB2, 0x14, session, body)
        account.pending = _Pending(
            pack_id,
            reply,
            _State(state.balance - pack["price"], state.cards + incoming, dict(state.names)),
        )
        return [reply]
    if subtype in (0x15, 0x17):
        token = "Get" if subtype == 0x15 else "Quit"
        if set(sections) != {token}:
            raise EconomyError("expected " + token)
        _keys(sections[token], ("No",))
        if _int(sections[token]["No"], maximum=255) != session:
            raise EconomyError("body session does not match envelope")
        if subtype == 0x15:
            if account.pending:
                check_inventory(account.pending.state.cards)
                account.committed = account.pending.state
                account.pending = None
            return []
        if account.pending:
            raise EconomyError("purchase must complete before Quit")
        account.session = None
        return [named(0xB2, 0x17, session, f"@Quit=/N={session}/D=0/B=0")]
    if subtype not in (0x10, 0x16, 0x22):
        raise EconomyError("unsupported ordinary shop operation")
    _edit(account, subtype, sections, catalog)
    return []

def _edit(account, subtype, sections, catalog):
    token = {0x10: "Erase", 0x16: "Sell", 0x22: "Decks"}[subtype]
    parsed = parse_edit_section(sections, subtype, token=token, error=EconomyError)
    if parsed is None:
        return  # Recorded completion marker, not evidence of any deletion.
    values, renamed = parsed
    current = account.pending.state if account.pending else account.committed
    cards, indices = apply_card_edits(
        current.cards, values, discard=subtype != 0x22, error=EconomyError
    )
    balance = current.balance
    if subtype == 0x16:
        balance = min(
            MAX_MONEY,
            balance + sum(catalog.sale_value(current.cards[i]) for i in indices),
        )
    updated = _State(balance, cards, current.names | renamed)
    if account.pending:
        account.pending.state = updated
    else:
        account.committed = updated
