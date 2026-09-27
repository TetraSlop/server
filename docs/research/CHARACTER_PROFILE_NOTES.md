# Generic character profiles

character_profiles.py handles GTM0P CR/CS/NS, PG/PO/PM and GR/GK/GG+PE. All71
PO positions are retained. Purpose and four visibility fields are partial
updates; the two NO selectors6/14 remain distinct. AN and CI update the generic
name/summary. They do not rewrite the separate TM0DataFile or ranking record.

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

Sources: PROFILE_CONTRACT.md and PROFILE_FIELD_SEMANTICS.md. Native editing and
the choice of locator aliases need user observation; no desktop was controlled.
