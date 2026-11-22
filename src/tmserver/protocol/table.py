"""Original table notices with no separate server acknowledgment."""


def viewer_state(msg):
    if msg.family != 0x41 or msg.subtype != 0x11:
        return None
    if msg.session or msg.sections not in ({"Viewer": {"IN": "0"}}, {"Viewer": {"OUT": "0"}}):
        raise ValueError("Viewer requires exactly IN=0 or OUT=0")
    return "IN" in msg.sections["Viewer"]


def channel_exit(msg):
    if msg.family != 0x20:
        return False
    if msg.subtype or msg.session or msg.sections != {"ExitCh": {}}:
        raise ValueError("ExitCh requires an empty body and zero header arguments")
    return True
