# Ordinary card-prize ledger

Implemented in match_prizes.py, independent of MatchService and account storage.
Default matches still restore original ownership through TCList and award no
ordinary card prizes. The separate MatchService card_prizes flag integrates
this ledger behind an explicit persistence barrier, documented below. This
pure module does not enable a route or debit money.

## API

PrizeLedger(scores, hands) uses wire/server-seat numbering. Each hands[seat]
has exactly five PrizeCard(original_slot, card, current_owner) records in the
client's result-hand order: ascending occupied board position among that
original owner's cards. original_slot is the stable selection identity0..4,
not the current compacted hand index. card must be an immutable service/economy
card dataclass. Stats must already include battle promotion and result growth.
CBC extras stay separate.

Construction resolves automatic awards and stops only where the original asks
a winner to choose. pending is a frozen PrizeChoice(choice_id,winner,loser,
options), whose PrizeOptions identify both the current hand_index and stable
original_slot. select(authenticated_winner,hand_index,choice_id=pending.choice_id)
accepts one eligible choice, resolves following automatic awards, and either
exposes another pending choice or completes. Invalid callers, indexes and stale
internal generations raise without mutation.

settlement() raises until complete, then returns the same frozen object:
- surviving[seat]: original selected cards that remain with that original owner,
  preserving stable slots and the compacted order. current_owner is presentation
  capture state, not permission to transfer an additional card.
- awards[seat]: all received PrizeAwards in original presentation order.
- transfers: every PrizeAward in global order. Each records winner, loser,
  losing hand_index at selection, original_slot, exact grown source_card, and
  card with deck255 for the received copy. The source deck association must be
  removed from the losing account.

transfers while incomplete is a read-only audit, not a partial commit request.
Each original selected identity appears once among final survivors and awards.
The ledger never adds awarded cards to the five-slot selected hands; the client
stores received prizes separately and only increments the displayed winner
count. A perfect winner may receive ten cards in a three-player match.

## Recovered client behavior

Original100CBD80 orders players by decreasing score, breaking equal scores by
server seat (100CCB6A..100CCBCD in the normal path).100C90EA..100C9217 examines
each ordered winner/lower-player pair. A pair is eligible only if the loser's
score is strictly lower and at least one remaining original loser card has
current owner equal to the winner. Candidates are ascending current hand index.

100C937D..100C93E2 bypasses choice for exactly one candidate or perfect flag3.
Otherwise the winner emits43/0F header=serverSeat @GetSelect=/H=index
(10107C6B). The consumer waits43/0F and uses H as the current compacted loser-hand
index; /E is consumed without semantic effect (10104E5B..10104EE1).

100CAAE3..100CAB9C removes that source card and compacts the loser's hand.
For a nonperfect result,100CABB2..100CAC6E returns other loser cards captured by
this winner to loser ownership.100CAEDD..100CAF59 also restores the winner's
remaining original cards captured by this loser. Both mutations can change
later pair eligibility.100CB117..100CB1BA repeats the same pair for a perfect
winner while the loser still has cards; otherwise it advances to the next pair.
100CADB2 clears the received card's deck association to255 before export to the
local pending-gain list100CADBA..100CADFC.

The pure ledger implements ordinary completed rounds with no departed players.
Quit-rule/departure status>=2 and winner departure cancellation follow separate
paths and require an adapter settlement policy before exposure. CBC rewards do
not enter this pair selection; they already have their own coordinator.

## Wire and commit boundaries needed for integration

1. Finish combat, compute ResultData, and commit grown original5→updated5 plus
   result statistics once under existing reservations. Keep the five original
   slots for all subsequent ownership validation.
2. Build the ledger from final captured ownership. For enabled card-prize play,
   TCList must carry those current owners and grown cards in reconstructed hand
   order, rather than the practice route's restored owners. Each first Ready
   authorizes delivery of that client's TCList. Duplicate Ready must not advance
   the ledger or resend a stale TCList.
3. A genuine choice requires the authenticated pending winner and an eligible
   H. Broadcast the original GetSelect reply to all round participants in
   order so their local copies perform the same removal. Automatic sole/perfect
   awards require no fabricated GetSelect request or reply.
4. Once the entire ledger is complete, atomically remove every transfer's grown
   source from its losing reservation and add its deck255 received copy to the
   winner's result-reward staging. Validate all identities/multiplicities before
   mutating any account. Dedupe by match-instance/round plus a durable settlement
   identity. Refresh all affected reservation tokens together.
5. Compose ordinary awards with pending CBC extras before overflow, Erase,
   Decks and Continue processing. Do not deliver a successful Continue before
   settlement and that participant's result inventory edits have committed.
   On interruption, recover an immutable committed settlement or resolve an
   explicit pending-choice policy; never silently transfer all captured cards.

There is no recovered original wire transaction/choice generation: GetSelect
contains only winner header and H. choice_id is an internal stale-job guard.
An exact replay that also happens to be a legal next pair choice from the same
winner is indistinguishable on this wire. Reliable ordered TCP plus the current
pending winner/eligible-index check prevents over-awards and unowned choices,
but does not prove intent across such identical packets. No guessed timing
barrier or new protocol field is introduced here.

## Stakes, refunds and double-up (not enabled by this module)

The separate protocol_map/wager_transfer_vectors.py executes these original
blocks and records machine-readable fixtures:
- Successful existing-table admission with GameEA EN!=1 deducts the current
  table wager at10090145..1009015C, clamping a negative balance to0.
  EN1 instead branches at10090114 to owner-configuration events03000E79/E7A
  and skips that debit. The game-rule editor stores the chosen wager at
  100504EB..100504F1. Table-configuration confirmation event2 then deducts that
  current wager at10052A02..10052A26, immediately before the settings14 sender
  at10053491. Thus a new owner is not charged the initially empty table's old
  wager and then charged again; the local owner debit precedes its14 request.
  VsGameInit /M subsequently assigns the authoritative post-debit balance at
  101028C0..101028CE. The36 owner/join branch fixtures verify both paths.
- Table cleanup100A94D4..100A9506 refunds the table wager only when its argument
  is0 and the current table ID is nonzero, capped99,999,999. This is a local
  cleanup predicate; its caller's complete disconnect semantics are not a
  durable hosted refund policy.
- Original computer selection100EC1E2..100EC1F3 deducts after NPC selection, so
  ComGameInit M is pre-debit. Ordinary admission and ComGame are distinct debit
  points.
- Both Continue constructors1010BC4B..1010BC77 and1010BF5C..1010BF88 double the
  next wager only for du1 and cap it at2000. They do this when opening the
  dialog, before the user's yes/no choice. The positive rematch event
  04000054 deducts at100BEFE9..100BF001; unlike initial admission, this block
  has no lower clamp. The UI checks affordability earlier.
- Therefore a hosted implementation must reject insufficient funds, reserve a
  round-specific stake, debit once using accepted settings before authoritative
  Init/accepted NPC selection, refund cancelled admission once, credit a completed round once,
  and debit the next wager only after all required Continue acceptances.
  A no vote must not charge that next wager.

The client offline reward reference100CC712..100CC7A6 is:
fully tied scores refund stake; otherwise let L be the count of strictly lower
scores, rating be the truncated mean other-player rating, multiplier3 for a
perfect result or1 otherwise, and factor=100+trunc((400-rating)/2).
reward=trunc(factor*(stake+100)*multiplier*L/100).
Examples at other rating300 and stake100: normal2p win300, perfect win900,
draw100, loss0; sole3p win600. This formula is proven offline client code.
Choosing it for hosted rewards would be an explicit replacement-server policy,
not recovery of the historical server. Online ResultData accepts server D1;
no original hosted award algorithm or authoritative cancellation ledger has
been recovered. Keep rating/history award policy separate from this formula.

## Validation and limits

python -B -m unittest test_match_prizes -v:6 tests pass. They include500 complete
original-x86 order/compaction comparisons across2/3 players and every local
seat, nontrivial stable slot permutations, perfect wins, tied scores, automatic
and selected awards, authority/eligibility/generation rejection and conservation.
Four additional original prize-export runs prove deck255 for source decks0,
7,49,255. Fixtures substitute resource/presentation helpers; source removal,
copy/compaction, ownership reset, pair classification and pair advancement run
the original instructions.

python -B protocol_map/wager_transfer_vectors.py:84 stake/refund/double-up,
36 owner-settings/join,588 offline payout and500 prize-order cases pass (1,208 total).
No native game process, desktop control, network round, persistent transfer or
money change was performed for this module.

## MatchService integration barrier

MatchService(card_prizes=True) enables the wire projection for two/three-human
versus or one-human computer mode, including the original computer menu.
DefaultFalse is unchanged. The computer's proved ambiguous-prize chooser is
described below; human selection continues to use the original GetSelect request.
prize_choice exposes the frozen current choice. final_prizes() returns None
until complete, then FinalPrizes(round_id,settlement), preserving that snapshot
through Continue until the next complete selection or table reset.

prize_commit_required is true for an unconfirmed complete result in phase
result. If every award is automatic (including no-award draws), the service
buffers initial ResultData. If choices are needed, it emits ResultData and
per-client TCList, then buffers the last GetSelect reply once all choices resolve.
The grown final_cards(peer) snapshots are always available at this boundary.
Root must persist growth/stats, record the result/CBC, settle all transfers and
stage combined overflow rewards in one durable ownership transaction before
calling confirm_prize_commit(round_id). That method releases buffered deliveries
once and rejects stale rounds/finished phases. A second same-round confirmation
returns an empty list. Continue rejects until this confirmation.

TCList current owners are cached before automatic/selected ledger mutations;
a slower client's first Ready receives the same original capture state as the
first client. Their local engines replay the same automatic awards and ordered
GetSelect replies. No extra Ready/Go barrier or guessed delay is introduced.

Quit still terminates the transient match and discards buffered final replies;
root owns durable interruption recovery. It must not confirm or deliver a held
packet after the table is abandoned. The engine exposes no invented cancellation
award/refund rule. The root adapter must wire recovery before enabling this flag.

Five match integration test groups cover automatic and chosen barriers, missing
confirmation, wrong winner/seat/index, duplicate messages, late Ready, original
TCList/GetSelect consumption at both seats, three-player compaction, stable slot
identity, CBC separation, snapshot lifetime and full-round/reset behavior.

## Computer prize choice

Original100CA231..100CA284 checks the winner's simulated-player byte+19A and
G+171 local-simulation flag, then applies the pacing gate
`rand()%denominator<2`. A passing poll consumes a second draw and selects
`candidate_indices[rand()%count]`. Candidate values are compacted loser-hand
indices, not ordinal positions in the eligible list. No card statistic, price,
NPC personality or model preference enters the choice. The hosted immediate
adapter uses denominator1 while retaining both successful-poll draws; this is
explicit scheduling policy rather than historical hosted timing.

`poll_computer_prize(choice,raw_rng,denominator=60)` returns None on a rejected
rate poll. `choose_computer_prize(choice,raw_rng)` uses denominator1. Both require
the frozen ambiguous PrizeChoice and preserve its candidate order. Sole-card
and perfect awards stay in PrizeLedger's earlier automatic path, consuming no
selection RNG. `protocol_map/computer_prize_vectors.py/json` contains419 original
executions:416 candidate/draw/rate cases plus3 suppressed simulation gates. The
pure port agrees on selected hand index and every consumed draw.

MatchService resolves computer choices before exposing FinalPrizes and holds
ResultData for the same durable commit barrier as other automatically completed
rounds. After confirmation, the human's first Ready receives TCList followed by
the cached `43/0F @GetSelect=/E=0/H=index` with the computer winner's header.
No choice packet is emitted for sole/perfect awards. Human wins against the
computer retain the ordinary user-choice barrier. Economy settlement gets the
computer's trusted updated five cards, with no account token or money balance.

The original39 receive block100C90A8..100C90E5 consumes TCList and leaves the
queued GetSelect untouched. After original candidate reconstruction, the
network0F wait100CA46C..100CA48F consumes that exact selected hand index. Both
menu and versus-shaped CPU fixtures execute this sequence; original instructions
and packet queues are used, not a rendered game session. Four service groups
cover CPU ambiguous/sole/perfect/draw paths, a human win, commit-before-delivery,
stable source slots, Ready duplicates and clean menu/rematch reuse.
