# Local chat service

chat_service.py implements42/01 entry,42/02 acknowledgement,42/04 membership
pages and42/08 public/private text validation. It uses the original constant
last-page header across an ascending group, one increasing signed counter,
at most17 pages and at most447 body bytes. Names are1..15 printable bytes.

Raw text after /Dt=#CHAT#name<TAB> is preserved, including slashes, at signs and
locale bytes. The account's stored name supplies the outgoing display name.
Header0 is channel/public and1 is direct/private, matching original senders.
Group membership starts with explicit chat entry and ends on matching channel
PART or disconnect. Repeated entry returns the same current roster group.

Five tests cover entry/replay/leave, malformed text, page bounds, original-x86
consumption of generated multi-page lists and text, and actual encrypted
two-account public/private relay plus disconnect roster refresh.

Sources: protocol_map/CHAT_SUPPLEMENT.md and CHAT_LIFECYCLE.md. Membership and
counter generation are explicit local policy. These tests do not establish
native rendering, legacy locale conversion or historical service scheduling.
