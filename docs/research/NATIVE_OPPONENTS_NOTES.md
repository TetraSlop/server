# Original computer opponents and hosted menu

`native_opponents.py` ports the installed client's built-in NPC records and local
normal-menu pool/hand construction. `MatchService(computer_menu=True)` now uses
these records in an explicit hosted computer-menu route. The default
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
`opponent(id)` accepts1..30. `known_name(points)` describes name visibility;
it does not claim that the threshold is a server-side admission rule.

`prepare_opponent(id,catalog,raw_rng,guild=0)` runs the local normal-menu policy:
100EC17C..100EC1A2 generates ten cards from the NPC's pack;100D9DE0's local
selection retains pool index0 first and then chooses four random remaining
indices without replacement. It returns immutable selected5 and unused5 tuples.
The opening/demo constructor uses a different selection route, so this is not
labeled a universal NPC hand algorithm. Network mode skips the local chooser;
using its exact port as the hosted deck policy is an explicit local choice.
Guild/filter0 and regenerating the ten-card pool on each rematch are likewise
hosted choices, not recovered historical server distribution.

Six module tests verify all asset records,90 original setup executions,
120 complete original ten-card-loop plus five-card-selection executions, and
10 full original incoming message executions. Each complete generated hand and
all consumed raw RNG draws agree. Original card art lookup is substituted;
the actual compact generation, pool removal and card import execute.

## Original menu wire contract

The entry chain is code-addressed, not a graphics-driven native replay:

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
   Header2 sends eight Rule values (the eighth is gs/P.E9) and one `/Com=id`.
   Original serializer10106F3A..101072E2 and UI100EE95B/100EBBE9 establish
   both forms. Three-player header3 is deliberately rejected in this version.
7. After choosing opponents the menu posts04000051. Parent100BD15C enters
   selection and100BD1DA..100BD225 flushes queued08,09,0A,0C,0D,1A,0F.
   Therefore06 has **no invented reply** and the service waits for the
   subsequent human07 CardSelect before sending status, StartData and TurnData.

The rest of the two-seat placement/battle/result contract is the existing exact
engine. Conf0/Ans1 returns ordinary Continue and fresh selection. Conf1/Ans1
instead sends only ComList04 and returns to the menu: the original1010D375
sender and1010D5D9 receiver establish that distinct gate. Old final snapshots
remain available until the next completed selection. Human collection ownership,
growth, CBC reward commits and reservations remain adapter/economy responsibilities.

## Adapter API and limitations

Construct `MatchService(mode='computer',computer_menu=True,...)`; register the
authenticated human with the added `collection_size` argument. GameENC before
Req is required; Req returns PLAYACK plus ComGameInit/ComList and changes phase
to `computer_menu`, without a GameOK gate. The adapter must treat that phase as
an admitted configuration phase for timeout/disconnect/reservation handling.
The public `npc_id` is None until06 selects a named opponent. All previously
supported board/economy guards still apply. Wager/double-up stay disabled.
Explicit board flags must match the configured board; no random layout policy
is invented from the original rule menu.

Several original NPC names are19 bytes. Versus initialization1010264E..10102656
copies CN into fixed16-byte slots without bounding the copy. **Do not serialize
these full names through VsGameInit or ordinary spectator CN fields.** The
computer menu resolves PlPrm name pointers instead. MatchService never emits
VsGameInit in this mode; observer support for this route remains disabled until
the NPC-specific WatchInfo contract is connected. Metadata/room advertisement
and encrypted transport integration are separate adapter work.

Four focused MatchService tests execute the original06 serializer against the
new consumer and the original02 parser against the new producer. They also
cover invalid-choice atomicity, proper admission, nine complete rounds across
NPC1/3/30, regenerated rematches, Conf1 reconfiguration, and final-snapshot
lifetime. These establish module behavior and original numeric/parser parity,
not native menu compatibility or a supported offline release mode.
