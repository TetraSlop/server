# Original table password handling

The original client checks the entered password locally against its decoded
table record. Table action10047E6E checks derived+41, then constructs the
password dialog10048EC0 with the input-buffer pointer and derived+43 cached
password. Original10049742..100497EE performs a case-sensitive byte comparison.
A match sets local event03000C87; a mismatch opens03000ED8. Neither branch sends
a password authentication message. Twenty original instruction cases execute
the exact comparison, including empty, mixed-case and eight-byte strings.

MatchService now accepts current pa1 settings that round-trip through the real
49-byte table metadata. The original PTL/TD password field holds eight bytes;
shorter values use trailing asterisk padding, removed by the original decoder.
The separate host settings editor can construct twelve bytes, but those cannot
be faithfully published in this field and are rejected before settings change.
Framing bytes and an embedded nonpadding asterisk are also unrepresentable.
Rule replies and PLAYACK preserve the accepted original hexadecimal field.

This reproduces the client's lobby password feature. The password is present
in the original table metadata available to room participants; it is not a
server-side secret or an independent account-authentication mechanism. Account
identity and card ownership are still bound to the authenticated connection.

Evidence: protocol_map/table_password_vectors.py/json, original359-case table
metadata encoder coverage, test_table_password_contract.py and encrypted
test_table_settings_integration.py. Native password-dialog rendering remains a
user review item; no desktop input or game launch was performed.
