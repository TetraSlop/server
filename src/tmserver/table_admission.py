"""Negative table replies chosen from original admission/error scene paths."""

from tmserver.match_service import packet


def rejected(section, *, full):
    # -32870 maps to the client's table-full text for GameEA and PLAYACK.
    # GameECA has a separate not-ready branch at -32871. PLAYACK -32869
    # is the generic start failure; -32872 specifically reports cancellation.
    if section == "Req":
        return packet(2, body=f"@PLAYACK=/EN={-32870 if full else -32869}")
    if section == "GameEN":
        return packet(65, 2, body=f"@GameEA=/EN={-32870 if full else -32871}")
    if section == "GameENC":
        return packet(65, 19, body="@GameECA=/EN=-32871")
    raise ValueError("not a table admission section")
