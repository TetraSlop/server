# Coverage

What the server implements for each part of the client, and what still needs
checking. It counts implemented behavior, not message names. The client is PC
Tetra Master 1.6.11.0. "Native" checks need a person at the game client; see the
[native test checklist](NATIVE_TEST_CHECKLIST.md). Which rules come from the
client and which are this server's choice is in [rules provenance](RULES_PROVENANCE.md).
Test results are in [validation](VALIDATION.md).

| Area | Current implementation | Next coverage needed |
|---|---|---|
| Game transport | Challenge/RSA, original Blowfish/checksum, authenticated IRC, channels and validated chat/trade relay; player-forged service controls blocked | Wider native error/retry coverage |
| Profile transport | Account-bound authentication, encrypted files, handles/characters/status, bounded cipher retention; original empty file/message enumeration including the mailbox checksum trailer | Wider native Viewer retry/error coverage |
| Rooms | Directory/PTL, entry, sequenced player/table deltas, replay, rules metadata; multiple rooms/tables with independent matches and shared durable accounts; fixed room presets and per-table Computer permission, verified through original consumers and encrypted startup ([configuration](ROOMS.md)) | Native scene checks; historical lineup/allocation and rule-enforcement policy remain unknown |
| Ordinary matches | Original 30-opponent computer menu, two/three human seats and optional automatic sizing at Start, native battle/ties/combos/AI, card growth, Ready/Continue/rematch; opt-in wagers/double-up and human/CPU card prizes; opt-in practice departure rules; independent handle/character admission and captured native startup replay | Native validation; partial-result economic/rated departures remain abort/refund |
| Match durability | Atomic growth/statistics, CBC and ordinary prizes, staged result edits, durable stakes/payouts, crash recovery through Returned Cards | Wider delayed-editor and interrupted-delivery edge coverage |
| Recovery | Application deadlines, cancellation, reservations, clean account rebind, ordered broadcasts | Historical participant resume is unproved; current policy aborts unfinished round |
| Shop | Catalog generation, buy/Get, sale/discard/decks, durable inventory; configured SP/SN/CMP/HD offers and original pauper/guild visibility | Native purchase/sale/offer UI exercise |
| Trade | Consent/entry, offers/confirmation, atomic paired commit, rollback/resync | Native paired UI exercise |
| Options | All 12 recovered settings saved before 85 acknowledgement; result Continue/PR saves ranking privacy; selective Tet/Tab defaults persist on 24; current representable 8-byte table passwords | Native reread of options/defaults/password/result dialogs; editor 12-byte password cannot round-trip in 8-byte metadata |
| Chat | 42 entry, bounded roster pages, authenticated names, raw public/private text | Native rendering/timing and wider locale coverage |
| Rankings | All 5 query categories, metadata, saved hide-name preference, isolated immutable files, delete, original consumers; optional durable UTC-week prize totals and previous-week local ratings from future play | Native menu checks; historical calendar, rookie and award policies remain unknown |
| Auctions | Durable ledger, query/file service, bids/refunds/expiry and original two-stage card collection | Native transaction UI checks; historical fee/quote policy |
| Prize Center | Award IDs, point credits, all 30 catalogue exchanges, atomic Get and restart | Native UI checks; historical automatic award policy |
| Operator administration | Shared account/claim inspection and an optional authenticated panel; audited Prize Center awards and event grants with preview, atomic receipts and exact retries; legacy progress/eligibility commands; JSON and PostgreSQL writer exclusion | Native collection of operator-queued rewards; audited eligibility/progress adapters; no automatic historical award rule |
| Character profiles | Authenticated CR/PG/GR, all 71 PO fields, partial/privacy updates and persistence | Unknown historical score/title mapping; native menu checks |
| Special boards | F0..18 effects, CPU scoring, generated CBC cards, complete encrypted matches and durable rewards; original menu-driven 2/3p board generation with 1024 original comparisons | Native scene checks |
| Events | Configured rooms and multiple matchmaking tables per event, exact directory keys, owner-routed files, authenticated admission/time/selection/exit, durable progress and F-card collection; encrypted 2/3p E matches and observers; original mode8 nonplaying admission | Native event scenes and historical mission/reward/broadcast policy |
| Optional ratings | Original 32-entry offline ring, explicit enrollment, D2/profile/journal atomically persisted; encrypted 2/3p and CPU rounds; RN display proven as Memorable Win and preserved | Historical online rating and Memorable Win selection policies remain unknown |
| Spectators | Original 2/3-player snapshot/live preview for ordinary, Computer-menu NPC and configured event tables; complete native Req fields, Observe choices, original NPC name/level handling, bounded independent gameplay queues and encrypted full matches | Opt-in only; bootstrap dialog has no presentation acknowledgement; finite native packet size bounds dense snapshots |
| Three-player games | 25 cells and 15 moves, three humans or one human/two original NPCs, original rank formula, all-seat receivers, encrypted rematches and 2↔3 Computer-menu reconfiguration, generated/custom special boards, practice departures, original card-prize choices and CBC reward acceptance/overflow/recovery | Native UI checks; configured server layouts retain their explicit seat-count constraints |
