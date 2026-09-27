# Local chat service

chat_service.py implements42/01 entry,42/02 acknowledgement,42/04 membership
pages and42/08 public/private text validation. It uses the original constant
last-page header across an ascending group, one increasing signed counter,
at most17 pages and at most447 body bytes. Outgoing display names are1..15
printable bytes; canonical account names can occupy the full16-byte profile
field. Entry and relay validate that full name before deriving its first15
bytes for presentation. Neither saved names nor authenticated identities change.

The distinction follows the original client: both senders copy the complete
NUL-terminated name at10294BE0 (1007EF2D..1007EF5D and1007F302..1007F332), but
the display handler rejects a16-byte name before the TAB separator
(1000CF97..1000CFE7). CHAT_LIFECYCLE.md and its original-code vectors demonstrate
acceptance at15 bytes and rejection at16. The incoming message prefix therefore
allows1..16 printable bytes, then is discarded in favor of the authenticated
account's display name. Empty, overlong and control-byte names remain rejected;
truncation does not conceal an invalid final byte. Non-ASCII account-name policy
is unchanged and remains a separate locale question.

Raw text after /Dt=#CHAT#name<TAB> is preserved, including slashes, at signs and
locale bytes. The account's stored name supplies the outgoing display name.
Header0 is channel/public and1 is direct/private, matching original senders.
Group membership starts with explicit chat entry and ends on matching channel
PART or disconnect. Repeated entry returns the same current roster group.

All128 supported accounts fit at the worst supported counter2147483647 with
maximum-length display names:16 ascending pages,8 entries per page, a constant
final-page header15, and417/418-byte bodies. Roster identity stays the complete
NN even when two shortened names collide. The same worst case fits136 entries
in17 pages;137 requires an eighteenth page and is rejected. This is a serializer
boundary, not an increase to the supported account pool.

Eight portable tests cover entry/replay/leave, malformed text and names, page
bounds, and actual encrypted two-account public/private relay. The full-name
regression exercises freshly provisioned and restored accounts, reconnects,
spoofed incoming names and roster cleanup, checking that native profile bytes,
the complete server snapshot and saved file bytes stay unchanged. The archived
original-x86 fixture separately exercises generated multi-page lists and text.

Sources: protocol_map/CHAT_SUPPLEMENT.md and CHAT_LIFECYCLE.md. Membership and
counter generation are explicit local policy. These tests do not establish
native rendering, legacy locale conversion or historical service scheduling.
