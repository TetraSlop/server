# Original computer opponents and hosted menu

`native_opponents.py` ports the installed client's built-in NPC records and local
normal-menu pool/hand construction. `MatchService(computer_menu=True)` uses
these records in a hosted computer-menu route with one human and one or two
distinct NPCs. The default
`mode='computer'` without this flag retains the existing versus-table practice
facade. No native window, native process, or historical server was run for this
pass. The original instructions are exercised in an emulator with seeded state.

## Records and card selection

The roster is **PlPrm.BIN**, loaded at10188C1D..10188C42 into102BB630. CoPrm.BIN,
the separate126-record collection/rating title table at102BB634, is not this
roster. Original decompressor1017B6C0 produces a2964-byte PlPrm object with
header `(636,31,15,0)`:31 records including reserved record0, each20 bytes.
The encoded and decoded SHA256 values are embedded and checked by the tests.

| Record offset | Established use |
|---|---|
| WORD+0 | Initial record points; do not assume permanent NPC collection total |
| WORD+2 | Local collection-point threshold for showing the real NPC name |
| DWORD+4 | Rating |
| bytes+8,+9,+A | Original AI score chance, preference, preference chance |
| byte+B | Catalogue pack selector |
| DWORD+C,+10 | Relocated name and flavor-text pointers, respectively |

NPC setup10110970 and update10110A70 copy the AI values/pack/name and use
record0's hidden name when local points fall below WORD+2. The30 names,
personalities, ratings and packs are exposed as immutable `OPPONENTS`.
`opponent(id)` accepts1..30. `known_name(points)` describes name visibility.
The native selector also checks the same threshold at100EC74F..100EC76E before
accepting a click; the hosted server currently retains its existing1..30 ID
validation and does not add a separate collection-point admission policy.

`prepare_opponents(ids,catalog,raw_rng,guild=0)` runs the recovered local
normal-menu stages:100EC14F..100EC1C0 generates ten cards from each selected
NPC's pack before any hand selection;100D9DE0's local chooser then retains pool
index0 and chooses four random remaining indices without replacement for each
seat. It returns immutable selected5 and unused5 tuples per NPC.
`prepare_opponent(id,...)` delegates a single-element batch and retains its
previous exact single-NPC behavior. IDs, distinctness, batch size1/2 and guild
are validated before generation consumes any randomness.
The opening/demo constructor uses a different selection route, so this is not
labeled a universal NPC hand algorithm. Network mode skips the local chooser;
using its exact port as the hosted deck policy is an explicit local choice.
Guild/filter0 and regenerating the ten-card pool on each rematch are likewise
hosted choices, not recovered historical server distribution. Pool construction
and forced hand selection run consecutively on the server. Intervening original
scene or board-generation draws and optional failed-frame selection polls are
not reproduced by this isolated-stage composition. No identical complete offline
match stream or historical hosted RNG schedule is claimed.

Six module tests verify all asset records,90 original setup executions,
120 complete original ten-card-loop plus five-card-selection executions, and
10 full original incoming message executions. Each complete generated hand and
all consumed raw RNG draws agree. Original card art lookup is substituted;
the actual compact generation, pool removal and card import execute.

The combined fixture `tests/fixtures/native_opponent_batches.json` adds eight
original instruction cases: two single-NPC cases and six two-NPC cases, multiple
CRT seeds and all four guild filters. It executes the complete selected-NPC
pool loop100EC12D..100EC1C2, followed by local100D9DE0(1) with no intervening
scene calls. Both original hands and both unused pools agree in every server
Card field, including stats, points, arrows and deck. The fixture retains raw
compact16 bytes; sale/local-index/state bookkeeping bytes are evidence only,
since they are not fields of the server Card. Draw counts, a digest of every
supplied draw and final CRT state are checked independently.

The fixture's `image_sha256` is
`422e46ab2ad6fd02db570a2384424dd1540b197e1ccd7e3da2645ca2b40b1879`:
the reconstructed, RVA-indexed `TM_emulated_mapped.dll` code image loaded by the
established `economy_vectors.Machine`/`Generator` Unicorn harness. This is separate
from `native_opponents.DECODED_SHA256`,
`356040c4c76d790db4a22447de7ef4eadba89ab99c6599df4988b2124772a7a3`,
which hashes the decompressed PlPrm.BIN data asset. `SOURCE_SHA256` hashes that
asset's encoded input. Neither asset checksum identifies the executable image.

This fixes a demonstrated ordering difference in the previous two-NPC helper:
seed57/NPC1+30 used210 draws when it generated and selected each NPC in turn;
the original two-pool-then-selection composition uses214 and different hands.
The original chooser exits immediately when P+EA is nonzero (100D9DE0..100D9DEB),
so this establishes local-reference behavior rather than retail server deck policy.
The one-NPC stage order and output are unchanged.

`research/build_npc_batch_fixture.py --research-dir <external-research-directory>
--output tests/fixtures/native_opponent_batches.json` reproduces the fixture using
the existing external Unicorn harness and decoded image/assets. It writes only
the specified output. Ordinary portable tests consume the frozen fixture and
require neither original assets nor Unicorn. The fixture does not prove where
all other scene/board RNG draws occur between the two original stages.

## Original menu wire contract

The entry chain below is recovered from code. A native local session captured
the transport order `GameENC -> GameECA -> PART room -> CheckJoinTable -> Req ->
JOIN table -> room PD`. The room-channel PART changes chat membership only:
the room roster, pending computer admission and account reservation must survive
it. Clearing them changes Req into a legacy versus entry and rejects the next
PD with "presence update requires room admission".

`test_ordinary_room_part.py` replays that order over encrypted transport through
ComList, NPC choice, card selection and the first turn. A successful DE into a
different ordinary room may instead release an idle lobby seat; it must retain
other waiting players and reject navigation during a start handshake or game.
Full or invalid destination rooms must retain the previous admission.

1. Alternate admission10095610 sends GameENC41/12 and requires GameECA41/13.
   It starts JOIN and sends primary01 Req at10095939 before JOIN completes.
   Its primary02 PLAYACK poll10095AB6 is once-only and ignores the result.
2. Completion posts local UI event0300115B at10096101. Controller1005A727
   enters1005A8A9 and starts fade100FA0C0, whose100FB2AA posts04000057.
   The current controller's1005C410..1005C444 invokes wrapper1005CEB0 with
   computer mode0/alternate0. Wrapper1005CF92..1005CFBE forwards these into
   game constructor100B9A00.
3. Constructor100B9A89..100B9A9C derives G+172/P+EA from the service global.
   Its network computer branch100B9D1B..100B9EB0 disables local simulation,
   enters major2, and100BCC28 calls100BAD30, whose sub1 receives43/02.
4. `@ComGameInit=/RA=rating/M=money/CP=points/S=collectionSize/R=...`
   learns the service sender and writes player stats/menu options
   (10102D21..10102ED1). R is `bm|st|cb|ca|tl|du|P.D9`; **P.D9 is not
   quitting-a-game rule gs/P.E9**. Its user-facing label remains unresolved;
   editor table10234408 contains0,1,2. Preserve it separately.
5. `@ComList=/L=count/D=ratings` updates sequential NPC ratings:
   D[i] writes PlPrm base+18hex+i*14hex, exactly record i+1 DWORD+4
   (10102F57/1010301C). It is not a list of arbitrary selectable IDs.
   Subtype02 can contain Init and List together; subtype04 requires the
   remembered sender. The service publishes all30 original default ratings.
6. Client43/06 header0 sends seven Rule values to save menu choices.
   Header2 sends eight Rule values (the eighth is gs/P.E9) and one `/Com=id`;
   header3 sends the same rules and `/Com=first|second` for two NPCs.
   Original serializer10106F3A..101072E2 and UI100EE95B/100EBBE9 establish
   these forms. The service accepts header2/3 with exactly one/two distinct IDs.
7. After choosing opponents the menu posts04000051. Parent100BD15C enters
   selection and100BD1DA..100BD225 flushes queued08,09,0A,0C,0D,1A,0F.
   Therefore06 has **no invented reply** and the service waits for the
   subsequent human07 CardSelect before sending status, StartData and TurnData.

The remaining placement/battle/result contract uses the existing two/three-seat
engine. Conf0/Ans1 returns ordinary Continue and fresh selection. Conf1/Ans1
instead sends only ComList04 and returns to the menu: the original1010D375
sender and1010D5D9 receiver establish that distinct gate. Old final snapshots
remain available until the next completed selection. Human collection ownership,
growth, CBC reward commits and reservations remain adapter/economy responsibilities.

## Three-player menu evidence and implementation

This is a concrete native menu path, not merely an unused count-based serializer:

- Parent event0400002E dispatches through the table at100EBE64/100EBE4C to
  100EBA49. Its100EBA96 sets selector child+11A to2 opponents. The alternative
  event0400002D reaches100EB9D7 and sets that count to1 at100EBA24.
- Selecting the first opponent again clears the first slot at100EC77B..100EC7A4;
  it cannot fill both slots with the same ID. IDs remain the original1..30 roster.
- Confirmation100EC110..100EC1C0 initializes one human and the selected number
  of NPC records.100EC1C2..100EC1E4 writes P+E5=count+1 and P+E6=count-1:
  two opponents therefore mean3 players and board mode1 (25 cells).
- It posts04000037 at100EC1FD; the parent's event table selects100EBB1D,
  which reaches100EBBD0..100EBBE9 and sends43/06 with header=P+E5 when online.
- Serializer10107248..101072C0 visits P+1CA with stride30hex for P+E5-1
  NPC IDs. ComGameInit/ComList have no additional three-player fields: the
  native menu itself selects the count before CardSelect/StartData.

`tests/fixtures/three_player_computer_sender.json` freezes original sender
execution for header2 with NPC1 and header3 with NPC1/3 and30/1. It records the
decoded-image SHA256, exact entry, seeded rules and event addresses. The same
Unicorn sender fixture supplied the original instructions; only transport/string
runtime boundaries were replaced. This is instruction evidence, not a rendered
menu replay.

Each server NPC now retains its own ID, original personality, name, rating and
fresh five-card hand. Header3 uses25 cells and15 placements; header2 uses16 and10.
Conf0 regenerates every selected NPC hand. Conf1 retains the completed result
until the next CardSelect and allows2→3 or3→2 reconfiguration. Explicit board
layouts must match the requested count, and invalid counts/IDs/rules are rejected
before stake authorization, RNG draws or configuration changes.

The original single-NPC `npc_id`, `ai_cards` and `ai_personality` attributes remain
views of the first NPC. Observer snapshots carry each seat's actual NPC ID.
Card-prize processing resumes automatic NPC choices after a human GetSelect;
the final human/NPC confirmations remain behind the existing durable settlement
barrier. CPU choices made before a human is ready are queued after TCList.

## Adapter API and limitations

Construct `MatchService(mode='computer',computer_menu=True,...)`; register the
authenticated human with the added `collection_size` argument. GameENC before
Req is required; Req returns PLAYACK plus ComGameInit/ComList and changes phase
to `computer_menu`, without a GameOK gate. The adapter must treat that phase as
an admitted configuration phase for timeout/disconnect/reservation handling.
The public `npc_id` is None until06 selects a named opponent. All previously
supported board/economy guards still apply. Wager/double-up and card prizes are
opt-in and retain the documented local economic policies. Without an explicit
layout, enabling special-rule flags invokes the recovered board generator;
flags0 retain the hosted empty-board default. Explicit board flags and size
must match the configured board.

`npc_nickname_id` identifies the first NPC; optional `second_npc_nickname_id`
identifies the second. The default derives the next distinct nonzero uint64.
The room adapter supplies separate stable identities for both seats.
`_authorize_round(stake, player_count=target)` forwards that keyword to the
durable authorizer only when a menu choice changes the count. The hook runs
before resizing, so reservation/account state can be saved for the intended
geometry while a failed save leaves the existing menu intact. Rematches and
unchanged counts retain the original three-argument authorizer call.

Several original NPC names are19 bytes. Versus initialization1010264E..10102656
copies CN into fixed16-byte slots without bounding the copy. **Do not serialize
these full names through VsGameInit or ordinary spectator CN fields.** The
computer menu resolves PlPrm name pointers instead. MatchService never emits
VsGameInit in this mode. The opt-in spectator route uses the NPC-specific
WatchInfo contract and bounded lobby labels; see [SPECTATOR_NOTES.md](SPECTATOR_NOTES.md).

The original MatchService instruction fixtures compare the06 serializer with
the consumer and the02 parser with the producer. Portable tests in
`tests/test_match_service.py` retain the one-NPC admission/rematch/reconfiguration
coverage. `tests/test_three_player_computer.py` adds frozen sender messages,
per-seat personalities and observer IDs, complete15-placement games, generated
and explicit25-cell boards,2↔3 transitions, failed-save atomicity and mixed
human/NPC prize choices. The encrypted adapter flows are covered in
`tests/test_three_player_computer_integration.py`. These establish module and
wire behavior, not native rendered-menu compatibility or a supported offline
release mode.
