# Optional original offline rating history

This records the isolated arithmetic module. Its later opt-in hosted adapter
is implemented in [RATING_POLICY_NOTES.md](RATING_POLICY_NOTES.md); current
combined validation is in [INTEGRATION_STATUS.md](INTEGRATION_STATUS.md).

`local_rating_history.py` implements the original client's local 32-result rating arithmetic with an independent durable ledger. It is **not enabled by server or MatchService**, enrolls no existing account implicitly, and changes no profile bytes, RN, RT, money, event points or existing practice defaults. Selecting the offline formula for a hosted server is an explicit local policy; the historical online rating formula remains unknown.

## Original evidence

The player base is `P=102B63E0`. Static initialization `1010B720..1010B725` calls player constructor `10100F10` with ECX=P. Its `10100FD6..10101040` block zeroes the WORD sequence at P+86, sets rating P+4 to200, and writes eight DWORDs `02020202` to P+8C: **32 class2 entries**. This is an observed original initialization, not a guessed neutral value.

The ordinary completed-result path `100CBD80`, normal branch `100CC54E..100CC6EB`, produces each player's class at P+1A1+30*i. For scores in one consistent seat order it is:

`class = 2 + count(otherScore > ownScore) - count(otherScore < ownScore)`

| Result | Two players | Three players |
|---|---:|---:|
| Sole highest | 1 | 0 |
| Tied highest, above a third player | — | 1 |
| All tied / strictly middle | 2 / — | 2 / 2 |
| Tied lowest, below a third player | — | 3 |
| Sole lowest | 3 | 4 |

Perfect wins and zero-score losses use the same comparisons. There is no separate perfect-win multiplier in the rating ring. The separate money formula does have such a multiplier. Equal-score server-seat tie breaking used for prize presentation does not change these rating classes.

The offline update at `100CC7E8..100CC840`:

1. Reads the old sequence as an unsigned WORD and selects index `sequence &31`.
2. Replaces that byte of P+8C with the local result class.
3. Computes `rating = floor(sum(50*(entry+2) for all32 entries)/32)`.
4. Increments the WORD sequence, wrapping65535→0.

The lowest possible rating for valid classes is100, the highest300. Lower is better. A fresh two-player win yields198 after truncation; a fresh loss yields201. Thirty-two consecutive two-player wins/losses yield150/250. Three-player sole wins/losses can reach100/300. All ties replace the current slot with class2; they do not skip the history update.

`100CC6F7..100CC70C` gates this arithmetic. G+197 nonzero bypasses the normal update, and online mode P+EA nonzero jumps to the server-result branch at100CC8CA. Two original full-result cases with online modes1/2 preserve the seeded ring, rating and sequence. The hosted D2 is authoritative; the receiver's D2 store is `10104282..1010448F` as previously mapped in RESULT_REWARD_SEQUENCE.

Profile header loader `101011FA..1010130C` imports rating from TM0DataFile+30 and sequence from+44, but does not overwrite P+8C. A seeded original execution imports300/77 while retaining all32 sentinel history bytes unchanged. This proves that these scalar profile fields cannot reconstruct the client's offline ring. No opaque region of the profile is relabeled or reused as a history store.

## Integration API

Construct `LocalRatingHistory()` or `LocalRatingHistory.restore(saved_snapshot)`. `restore(None)` creates an empty, unenrolled service.

- `enroll(key, history=None, sequence=0)` explicitly adds a new account. Default history/sequence reproduces the constructor and derives rating200. Trusted imports may provide exactly32 class0..4 entries and a WORD sequence. Re-enrollment cannot overwrite an account.
- `enrolled(key)` and `state(key)` expose immutable state. State has `entries`, `sequence`, server-only `revision`, and derived `rating`.
- `quote(key, match_id, scores, seat)` reads only. Scores use authenticated engine server-seat order, two or three participants, nonnegative integers, total1..16 or1..25 respectively. The immutable quote exposes `result_class`, `before`, `after`, and `d2=after.rating`. IDs must include a stable table instance and round to prevent reuse at a second table.
- `commit(quote)` validates and applies once in memory. `commit_many(quotes)` validates one to three distinct accounts for the same match/score order and unique seats before changing any account. Stale or altered quotes reject the whole batch. A monotonically increasing internal revision prevents neutral-history/sequence-wrap ambiguity.
- Repeating an already committed account/match result returns the original receipt, even after later rounds. Reusing that ID with different scores/seat rejects. `snapshot()` records initial enrollment and ordered result receipts; restore replays and validates the ledger. Receipts are retained for lifetime deduplication; pruning is deliberately not implemented.

The module trusts the adapter to bind account keys to authenticated participants and to submit only completed ordinary matches. It does not independently decide forfeits, mission outcomes, spectator eligibility, abandoned rounds or whether a match is ranked.

## Transaction boundary and review decision

Future integration must quote before constructing ResultData, set that result's D2 and the coherent profile rating to the quoted value, advance the shared sequence **once**, and persist profile/growth/money/history in the same atomic snapshot before any success packet. Calling `commit` alone is not a disk transaction. The existing EconomyService coherent-stat validator presently preserves rating, so it must be extended deliberately if this policy is activated; this module does not bypass that boundary. Count policy also remains independent: the original offline result increments count by n-1, while existing local practice counts one completed match.

RN is a server-supplied string displayed under Memorable Win, as established by six later original-code cases in ../protocol_map/RN_DISPLAY.md. Its historical selection rule remains unknown; it is not a proven numeric-rating-to-rank-name conversion. Enrollment must not invent RN or claim a converted existing ranking. An explicit trusted history import is supported; deriving32 values from a single rating is not. The integrated opt-in policy and enrollment behavior are described in RATING_POLICY_NOTES.md.

## Validation

`protocol_map/rating_history_vectors.py/json` records **594 passing original-instruction cases**: one constructor reset,160 arithmetic cases across all classes and eight sequence boundaries,430 complete result calls spanning every partition of ten two-player scores and fifteen three-player scores at every local/server rotation, two online bypass cases, and one real profile-header import. The fixture records the mapped DLL SHA256. No rating/calculation code was hooked; the shared VM intercepts presentation/audio plumbing only. Header import executes the original string copy. No game process or network was launched for these checks.

`test_local_rating_history.py` has11 passing tests comparing all fixtures and testing explicit enrollment, read-only quote behavior, replay after later rounds/restart, stale and forged quotes, atomic three-account rejection, coherent participant order, full ring replacement, strict domains, and corrupted/duplicate durable records. Server/MatchService integration is intentionally outside this change.
