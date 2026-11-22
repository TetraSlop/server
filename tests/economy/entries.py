"""Client entry envelopes shared by the economy tests."""

from tmserver.economy.packets import named


def _entry(family, subtype, section, view):
    fields = f"/NN={view.nn:016X}/HID=0/Dm=1/Vol=1/CN={view.name.hex()}"
    return named(family, subtype, 0, f"@{section}={fields}")


def shop_entry(economy, key):
    return _entry(0xB0, 0, "ShReq", economy.account_view(key))


def collection_request(economy, key):
    return _entry(0xB0, 1, "ShReq", economy.account_view(key))


def prize_entry(economy, key):
    return _entry(0x70, 0, "CvReq", economy.account_view(key))
