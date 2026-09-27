# Result-screen ranking privacy

Continue43/11 PR is the same setting as options84 Ran:0 displays the name,
1 hides it in ranking presentation. The result-screen choice now persists
before acknowledgement, and newly requested ranking files set their original
anonymous-display byte from the saved preference. Account names and owned
inventories remain intact. Existing ranking file snapshots remain immutable.
Explicit historical anonymous overrides still apply.

Original evidence:

- Result choice block100CBAEA: answer2 writes0 at player+D8,102B5EA0 and
  profile-working-byte10294C86; answers3/5 write1. Other tested choices
  preserve all three fields. Execution stops before child-window allocation.
- Original Continue builder10106E30, branch10107D70, emits PR from player+D8.
- Option editor1013C990..1013C99E writes the same field. Option.BIN labels
  Ran0 Display Name and Ran1 Hide Name; see ../protocol_map/RULES_SETTINGS.md.
- Profile import1014DA54..1014DA6E restores the byte; the persistent profile
  offset is B6. Original ranking projection101752A8..101754F8 uses record+C0
  to display the anonymous resource string instead of the supplied name.

../protocol_map/continue_privacy_vectors.json records26 synthetic original
executions. Tests cover both choices, original send/import, wrong seats and
invalid values, rematch/reconfigure/leave, stale result packets, saved-state
restore and save failure. Missing PR preserves the saved preference for
existing diagnostic clients; the native sender always includes it.

No new native game was launched. The visible result dialog and reread after
login remain in DEFERRED_REVIEW.md. This is ranking display privacy, not an
anonymized network protocol: original ranking rows still carry identities.
