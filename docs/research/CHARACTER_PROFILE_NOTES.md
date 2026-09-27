# Generic character profiles

character_profiles.py handles GTM0P CR/CS/NS, PG/PO/PM and GR/GK/GG+PE. All71
PO positions are retained. Purpose and four visibility fields are partial
updates; the two NO selectors6/14 remain distinct. AN and CI update the generic
name/summary. They do not rewrite the separate TM0DataFile or ranking record.

The recovered PO layout identifies field 2 as a terminated `string[16]`, while
the native account name in TM0DataFile can occupy all 16 bytes. A default generic
profile therefore presents the first 15 bytes without changing the account name,
identity, or saved native profile. Explicit AN edits still require at most 15
bytes and are rejected rather than silently shortened. Existing generic records
are read directly; their saved name and opaque fields never depend on constructing
a fresh default. This also keeps PG, CR and GR usable for restored accounts whose
native name occupies the full 16-byte field.

Local ownership accepts each authenticated account's provisioned character ID
or its game nickname identity with its single supported character key. Another
account can read the generic public record, but cannot register or update it.
Unknown fields default to zero and are preserved on later updates. Privacy
flags follow the original UI's visibility behavior; opaque field meanings are
not invented. Historical title eligibility is not implemented.

Records persist under character_profiles in the same atomic state document as
accounts/auctions; acknowledgement follows persistence. Repeated registration
preserves previously saved settings. Tests cover independent NO values, rejected
partial updates, authorization, cold restore, original71-field PO decoding and
GR completion, plus encrypted write/read/restart.
The full-name regression covers default/public reads, registration, partial
updates, saved-name overrides and cold restore. A real encrypted connection
exercises PG/CR/GR before and after restart and verifies the entire native
account record remains unchanged. Tests use temporary saves only.

The adapter persists only changed records. Public/default reads, unknown-profile
responses, rejected foreign edits and identical retries acknowledge without
writing an unchanged journal. Changed edits still save before publication and
acknowledgement. Two transaction regressions reproduced the unnecessary writes;
the profile/transaction/delete/storage-failure batch passes18 tests.

Sources: PROFILE_CONTRACT.md and PROFILE_FIELD_SEMANTICS.md. Native editing and
the choice of locator aliases need user observation; no desktop was controlled.
