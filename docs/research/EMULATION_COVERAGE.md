# Client-visible emulation worklist

This tracks implementation, not a percentage inferred from mapped message names.
The original client is PC Tetra Master 1.6.11.0. Native checks requiring the user
are in DEFERRED_REVIEW.md. Historical hosted policies remain explicitly separate.

For the packaged server's current test results, see
[validation](../VALIDATION.md) and [the iteration log](../WORK_LOG.md). The
checkpoints below retain the research history and its original test counts.

| Area | Current implementation | Next coverage needed |
|---|---|---|
| Game transport | Challenge/RSA, original Blowfish/checksum, authenticated IRC, channels and relay | Wider native error/retry coverage |
| Profile transport | Account-bound authentication, encrypted files, handles/characters/status, bounded cipher retention; original empty file/message enumeration including the mailbox checksum trailer | Wider native Viewer retry/error coverage |
| Rooms | Directory/PTL, entry, sequenced player/table deltas, replay, rules metadata; multiple configured ordinary rooms and multiple tables per room with independent matches and shared durable accounts | Native scene checks; historical allocation policy remains unknown |
| Ordinary matches | Original30-opponent computer menu, two/three human seats and optional automatic sizing at Start, native battle/ties/combos/AI, card growth, Ready/Continue/rematch; opt-in wagers/double-up and human/CPU card prizes; opt-in practice departure rules; independent handle/character admission and captured native startup replay | Native validation; partial-result economic/rated departures remain abort/refund |
| Match durability | Atomic growth/statistics, CBC and ordinary prizes, staged result edits, durable stakes/payouts, crash recovery through Returned Cards | Wider delayed-editor and interrupted-delivery edge coverage |
| Recovery | Application deadlines, cancellation, reservations, clean account rebind, ordered broadcasts | Historical participant resume is unproved; current policy aborts unfinished round |
| Shop | Catalog generation, buy/Get, sale/discard/decks, durable inventory; explicitSP/SN/CMP/HD offers and original pauper/guild visibility | Native purchase/sale/offer UI exercise |
| Trade | Consent/entry, offers/confirmation, atomic paired commit, rollback/resync | Native paired UI exercise |
| Options | All12 recovered settings saved before85 acknowledgement; result Continue/PR saves ranking privacy; selective Tet/Tab defaults persist on24; current representable8-byte table passwords | Native reread of options/defaults/password/result dialogs; editor12-byte password cannot round-trip in8-byte metadata |
| Chat |42 entry, bounded roster pages, authenticated names, raw public/private text | Native rendering/timing and wider locale coverage |
| Rankings | All5 query categories, metadata, saved hide-name preference, isolated immutable files, delete, original consumers; optional durable UTC-week prize totals and previous-week local ratings from future play | Native menu checks; historical calendar, rookie and award policies remain unknown |
| Auctions | Durable ledger, query/file service, bids/refunds/expiry and original two-stage card collection | Native transaction UI checks; historical fee/quote policy |
| Prize Center | Award IDs, point credits, all30 catalogue exchanges, atomic Get and restart | Native UI checks; historical automatic award policy |
| Operator administration | Exact saved-account lookup, explicit Prize Center awards, event grants and member eligibility through existing transactions; JSON and PostgreSQL writer exclusion | Native collection of operator-queued rewards; no automatic historical award rule |
| Character profiles | Authenticated CR/PG/GR, all71 PO fields, partial/privacy updates and persistence | Unknown historical score/title mapping; native menu checks |
| Special boards | F0..18 effects, CPU scoring, generated CBC cards, complete encrypted matches and durable rewards; original menu-driven2/3p board generation with1024 original comparisons | Native scene checks |
| Events | Configured rooms and multiple matchmaking tables per event, exact directory keys, owner-routed files, authenticated admission/time/selection/exit, durable progress and F-card collection; encrypted2/3p E matches and observers; original mode8 nonplaying admission | Native event scenes and historical mission/reward/broadcast policy |
| Optional ratings | Original32-entry offline ring, explicit enrollment, D2/profile/journal atomically persisted; encrypted2/3p andCPU rounds; RN display proven as Memorable Win and preserved | Historical online rating and Memorable Win selection policies remain unknown |
| Spectators | Original2/3-player snapshot/live preview for ordinary, Computer-menu NPC and configured event tables; complete native Req fields, Observe choices, original NPC name/level handling, bounded independent gameplay queues and encrypted full matches | Opt-in only; bootstrap dialog has no presentation acknowledgement; finite native packet size bounds dense snapshots |
| Three-player games |25cells/15moves, all3 ack/Continue barriers, original rank formula, all-seat receivers, encrypted two-round persistence, generated/custom special boards, practice departures, original card-prize choices and CBC reward acceptance/overflow/recovery | Native UI checks |

Latest clean complete checkpoint:880 tests passed in1010.618s with no failures/skips.
Preserved output: test_checkpoint_880_results.txt. This includes parallel event
tables/observers, account pool capacity, room migration and reconnect cleanup,
as well as shared ordinary rooms and combined three-player rewards.
The configurable local account pool now supports2..128 identities; default2.
This is measured
implementation coverage, not proof of complete historical-server emulation.

This checkpoint also includes authenticated pre-start kicks, configured
lobby departures/host transfer and coherent TD metadata. The focused
table/match/settings/recovery regression passed129 tests in158.329s.
Startup handshake deadlines passed47 watchdog/event checks. Viewer and
ExitCh handling and their latest observer checks are recorded separately
in ../protocol_map/TABLE_NOTICES.md and table_control_verified_results.txt
(51 tests in73.979s). Explicit PC room departure is included as well.
The completed combined run uses validation_manifest_handle_identity.json.

Later roster/admission work passed32 focused tests in43.356s in
table_admission_roster_final_results.txt. It adds post-GameEA human roster
pushes, native Start readiness, read-only lobby member queries and nonfatal
full/busy-table rejection. Twelve original-code fixtures and actual encrypted
server roster/PTL bytes exercise the original consumers and Start gate.
These changes postdate the854-test checkpoint; see ../protocol_map/TABLE_ROSTER.md.

The newer handle identity regression passed172 tests in144.988s in
handle_identity_verified_results.txt. It includes full native admission bodies
for ordinary/Computer/event/observer flows and37 unchanged captured commands
from room entry through first-turn selection. The previous full run was
interrupted when this capture exposed a real admission regression; it is not
a complete passing checkpoint. Original eligibility mapping also passes21
cases in table_eligibility_native_results.txt. A fresh full run records its
source hashes in validation_manifest_handle_identity.json.

After the880-test checkpoint, human-match statistics passed307 focused tests.
VS. Player Games advances once per ordinary human round with original WORD wrap;
CPU and borrowed event profiles preserve it. Both ResultData and stored profile
carry the same value, including rematches/restarts and opt-in rated/prize rounds.
Original display/importer execution identifies RN as Memorable Win, RT as
VS. Rating, and Prize Points Acquired as the Prize Center balance. Historical
rating components and award selection remain unknown. See RESULT_STAT_LABELS.md
in protocol_map and validation_manifest_vs_player_statistics.json.

Automatic human tables now freeze two/three seats at the owner's Start action,
with matching board size, rewards and persistence. This option passed225
focused test executions before the subsequent invitation correction. Start
invitations now wait until an unready guest completes admission, on both fixed
and automatic tables. The latest combined regression passes72 tests in90.190s;
five original-code cases separately validate admission cleanup and the complete
controller's roster/invitation intake. See AUTO_TABLES.md and
validation_manifest_start_invitation.json. These are focused checkpoints after
the880-test full run, not a newly completed whole-suite run.
