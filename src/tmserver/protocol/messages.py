"""Complete service replies within the original encrypted IRC frame."""

import re

MAX_LINE = 512
FRAME_OVERHEAD = 6  # Four checksum bytes and CRLF.
MAX_SERVICE_PAYLOAD = 469


def service_payload(payload):
    """Budget the whole GTM0 message, including its family prefix."""
    if len(payload) > MAX_SERVICE_PAYLOAD or any(c in payload for c in (0, 10, 13)):
        raise ValueError("service reply exceeds frame budget or contains line controls")
    return payload


def service_notice(source, recipient, payload):
    """Keep native sender extraction and reserve room for full POL nicknames.

    Local service/account nicknames are nine bytes. Short test/diagnostic names
    are safe too; longer or IRC-delimited names must fail before service work.
    """
    for nick in (source, recipient):
        if re.fullmatch(rb"[A-Za-z0-9_-]{1,9}", nick) is None:
            raise ValueError("service NOTICE requires nicknames of at most nine bytes")
    message = b":" + source + b"!p@local NOTICE " + recipient + b" :" + service_payload(payload)
    if len(message) + FRAME_OVERHEAD > MAX_LINE:
        raise ValueError("service NOTICE exceeds frame budget")
    return message
