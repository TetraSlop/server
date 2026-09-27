# Original ordinary board generation

`native_board_generation.generate_board(player_count,raw_rng,st=0,cb=0,ca=0)`
ports100C0F16..100C11E5 and its ascending-candidate helper100D39C0. It accepts
two/three players and a callable returning raw CRT values0..32767. The frozen
GeneratedBoard contains fields, blocks, effects, spinners, active and eight
shared bytes. It performs no asset reads or I/O. The original code tested is
the local simulation pathG+171!=0 with ordinary role0; no historical hosted
generation or timing policy is claimed.

Every ordinary generation starts with one rand()%100 lookup into the exact
100-byte block table at102335B8+100*mode, where mode=player_count-2. Two-player
block counts0..6 have bucket sizes1,2,3,5,9,30,50. Three-player counts0..10 have
sizes1,1,1,3,2,3,9,15,15,25,25. Thus walls exist even when all three special
toggles are disabled in the original local policy.

ca replaces floor(B/4) of those blocks with spinners. st optionally draws a
second rand()%100 count from10233680+100*mode. Two-player effect counts0..4 have
sizes3,20,70,5,2. Three-player's literal table is0×3,1×2,2×3,3×70,4×3,5×2,6×2,
then15 additional zero bytes; the port preserves this exact ordering. It does
not normalize or redistribute those trailing zeros. A third/second draw then
chooses the initial active seat modulo player count.

For each nonspinner block, the candidate helper rescans ascending empty F
indices. With cb enabled, rand()%100<50 takes a further quarter-split draw to
choose F6,7,8,17; otherwise a draw's low bit chooses F1/2. A separate draw picks
the candidate index. Spinners first choose an empty index, then use a separate
draw's low three bits for F9..16. Low-property effects use a25/50/25 split for
F3/4/5 and another draw to choose an empty index. All selections are without
replacement; counters B/E equal their realized nonzero field classes. The
routine finally takes eight draws' low bytes into G+1A8. Ordinary generation
does not emit F18/CBC. Special role5 forcibly enables all toggles in the original;
that distinct scene override is outside this ordinary API.

The helper reproduces the original random consumption exactly, including draws
whose branches do not select a special card. Caller scheduling and seed remain
explicit hosted policy. Only0..17 fields occur; B<=6/10 leaves at least10/15
placeable cells, and low-property cells remain placeable. The shared bytes and
starting seat are data returned to the caller, not hidden global mutations.

MatchService activates this generator after every completed CardSelect set when
accepted st/cb/ca includes a1 and no explicit start_fields override was supplied.
It emits generated F/B/E/S, uses the same active seat and returned shared bytes
in the first TurnData, and starts later turns with its normal shared-R schedule.
Rematches generate fresh boards from the continuing stream. Computer-menu rules
use this same path after original NPC selection. An explicit board still validates
that its configured flags match accepted rules and keeps its existing S0 schedule.
The default all-zero flags preserve this replacement server's previous empty/S0
board and consume no generator draws. That last choice deliberately differs from
the original local flags0 wall generation; the pure helper exposes the exact
original behavior for a future explicit policy switch.

`protocol_map/board_generation_vectors.py/json` runs1,024 original executions:
all8 toggle combinations, both board sizes and64 seeds. Original candidate
scanning, branching and literal tables execute unchanged; only raw CRT draws
are supplied by the fixture. Every F/B/E/spinner/active/shared result, draw and
final RNG state agrees. No native process or desktop control is involved.
Match integration additionally checks original StartData/TurnData receivers at
each seat,64 complete human matches across every combination,12 full CPU/menu
rounds including rematch, and preservation of default/explicit-board draw order.
