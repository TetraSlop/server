# Departure rules, result boundary, and selected-card provenance

This pass executes the unchanged PC client in an isolated x86 harness and ports
its ordinary departure transitions. No native game, desktop, or historical
server was run. The hosted disconnect trigger, grace period and replacement NPC
choice remain replacement-server policy.

## Public engine contract

`MatchService(..., allow_departures=False)` preserves the prior abort behavior by
default. With the explicit option enabled, `departure_supported(peer)` is a
read-only preflight. It is true only in turn/battle, without card prizes, wagers
or a rating callback. `depart(peer, replacement=1)` rejects unsupported modes
before mutation; repeated departures are no-ops. The same guard applies to
authenticated Quit/Break. Root owns transport timeouts and scheduling.

Each departed account keeps its `Player.peer`, selected five, updated five and
stable original-slot identities. `_humans()` contains only live delivery/ACK
peers. Root must retain all account reservations until result persistence or
abort, including disconnected peers. FinalCards remains available for every
account. No automatic reconnect, card deletion or penalty is inferred here.

The first departure sends43/23 GetAway once to each live peer. gs0 then sends43/24
Change; reversing these messages can lose the original substitution. Ordinary
continuations keep the existing turn ACK barrier. During a pending human target
choice, the departed controller is replaced by original target selection; ties
and cascades still yield through bounded `advance(max_battles=1)`. If everybody
leaves, pending work is canceled without manufacturing a result. A completed
departure round ends on Continue instead of retaining a missing participant for
rematch.

| gs | Original behavior implemented |
|---|---|
|0 Substitute|Retain remaining hand and board identity. Change installs an original NPC personality over that hand, without generating a fresh NPC deck. Hosted default NPC1 is explicit policy.|
|1 Invalid|Clear the departed presentation hand; change that seat's board base owner to3 while retaining current owner. Three-player play skips that seat. One remaining participant terminates the invalid game without ordinary ResultData.|
|2 Loss|Clear the departed presentation hand; keep original board base ownership. Skip the seat and end after the current turn when only one participant remains. Preserve original chronological rank presets.|

`BoardCard.original_owner/original_slot` always identify the economic source;
the separate `base_owner` override carries native gs1 board representation.
This prevents a neutral base-owner3 cell from becoming an invented fourth
account or losing its selected-card identity.

## Original evidence and exact rules

GetAway receive and worker details are in DISCONNECT_RECOVERY. The worker at
100C0AB0 installs status2, increments departure count once, and for gs1/2 clears
hand count19F. Departed class is `player_count - 2*departure_ordinal + 3`, lower
count0, multiplier1. gs1 also sets prize position `player_count-ordinal`, neutral
base owner3, and invalid flag16D when only one player remains. Change is usable
only after status2 with gs0 and installs the selected NPC's original personality.

At100C76A2..100C7762, both sequence and active seat advance for every skipped seat.
The round ends at sequence>=5*player_count or the gs2 one-survivor condition.
`native_departure_rules.next_turn` preserves byte sequence arithmetic and rejects
the all-skipped state which would not terminate in the original loop.

100CBD80 uses separate departure ranking branches. For each surviving seat let
`equal` count equal surviving scores including itself and `higher` count strictly
higher surviving scores. gs1 class=`equal+2*higher`, lower=`2-equal-higher`;
gs2 class=`2-player_count+equal+2*higher`, lower=`player_count-equal-higher`.
Departed score becomes0, but its chronological class preset survives. Thus a
three-player departure is not correctly modeled by assigning every departed
seat the ordinary two-player loss class. The helper also preserves perfect
multiplier and order behavior. The apparently corrective stores at100CC4C2..535
write positions1/2 back to the same records; original execution confirms they
do not swap them.

100C87E2 appends ascending board-index cards to the remaining compacted hand.
gs1 departed seats therefore rebuild zero cards; gs2 rebuilds only played cards.
Living early-result seats retain unplayed cards first. TCList H/D reflects these
actual reconstructed counts; it must not declare every presentation hand has5.
FinalCards nevertheless preserves all five authoritative selected copies. No
partial-hand transfer is enabled until the ownership policy can be settled
independently of this presentation layout.

All spinners rotate at100C7474..100C756D even when PutCard F255 means no placement.
`finish_turn(board,None)` now preserves that behavior without consuming a low1
tile which was never covered by a card.

## Inventory exit evidence and remaining limit

The menu controller is1010E810. Its voluntary exit block1010F1B0..1010F301 has
different inventory paths, tested with every0..5 remaining-card count for both
board sizes:

* gs1 refunds P+DC into balance, zeroes P+DC, compacts inventory, exports each
  valid remaining local hand card, then each board card with base_owner0. Current
  owner is irrelevant. This restores captured own cards and excludes captured
  foreign-source cards. The hand count becomes0.
* gs0 only compacts the collection in this block and enters childstate9.
* gs2 only compacts it and enters childstate8. That state waits for ResultData
  at1010F63E..1010F64D before updating local departure result fields. It is not
  evidence that a server should immediately delete all five selected cards.

The separate forced cleanup1010F933..1010FA7E in an ordinary game refunds the
stake and exports the same remaining-hand plus base_owner0 set for all gs values.
These executions prove that clearing a remote presentation hand is insufficient
evidence for permanent card loss. Final profile persistence after voluntary
gs0/gs2 departure, hosted penalties and partial-hand card-prize settlement are
still unproven. Rated/wager/card-prize departure handling therefore remains an
atomic preflight refusal, allowing the adapter's existing abort/refund path.

## Ready header correction

10107BD1 sends Ready with the current G+18D sequence, after end-turn advancement.
It is10 for a complete two-player round and15 for three players; early departure
results use their actual advanced boundary. The saved native CPU session confirms
`GTM0G43000E0A@Ready=/Ans=0` (native_runs/20260926_user_cpu_complete/traffic.jsonl,
lines234/240). MatchService now validates Ready against `sequence`, not the last
played turn9/14. The result and TCList envelopes retain their existing sequence
convention; their receive paths do not establish an equivalent header check.

## Validation

`protocol_map/departure_rules_vectors.py/json` executes1,080 ranking cases across
all local seat rotations,324 turn boundaries and72 partial-hand reconstructions.
`departure_inventory_vectors.py/json` adds72 original exit/reconciliation cases.
The three-player replacement AI comparisons execute the original scorer and full
hand/placement selector on25-cell special boards with all personalities. This
also corrected the scorer's preferred-target bonus to board_size (25 rather
than16 in this mode). Existing4x4 scoring remains unchanged.

Eleven MatchService regression groups cover all gs values, ordered notifications,
duplicate/stale input, full selected-slot provenance, original TCList receipt,
target-choice departure, bounded ties/cascades, no-placement spinner rotation,
unsupported economic/rated preflight, advanced Ready, optional ratings and packet
capacity. Original code comparisons establish local semantics, not native GUI
compatibility or historical server policy.
