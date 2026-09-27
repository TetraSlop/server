# Optional rating integration boundary

`RatingPolicy` connects the proved ring in LOCAL_RATING_HISTORY_NOTES.md to the existing atomic card-growth/statistics boundary. Mode defaults to `preserve`. Explicit `local-history` mode enrolls a missing account with32 neutral entries **at its existing saved sequence**. Enrollment alone never changes its current profile rating, RN or unrelated bytes. Its first completed result receives the original ring calculation. This is the chosen local policy for existing profiles whose historical ring is unavailable.

Construct `RatingPolicy(economy, mode='preserve'|'local-history', snapshot=saved_or_None)`. Save `policy.snapshot()` in the same JSON document as EconomyService and the result/money ledgers. The snapshot preserves history when policy is disabled; runtime mode remains an explicit startup choice.

`quote(stable_match_id, {server_seat: authenticated_account_key}, scores, departed_seats=())` returns seat→immutable RatingQuote, or None under preserved mode. It validates every account before installing any new enrollment. Completed results are not added at quote time. Repeating the same request yields the same quote. The stable ID must contain table instance and round.

MatchService's optional `rating_provider(match, round_id, scores)` obtains those quotes. `PracticeStats.next_result(..., rating_change=quote)` verifies the native class/outcome, full-tie predicate, old/new WORD sequence, exact history replacement and established prior rating, then changes D2 alongside ordinary result statistics. RN remains opaque and unchanged. The existing one-match count policy is retained; this integration does not substitute the original offline n-1 count increment.

`commit_growth(rounds, stats_by_key, payouts_by_key=None, rating_changes_by_key={key: quote})` is the coordinator commit. It first clones and validates the whole rating journal transaction, then calls `EconomyService.commit_match_growth` with the typed proofs. Economy validates all accounts, cards, result fields and complete proof coverage before replacing any account. Only after that succeeds does the coordinator replace its journal, without an await or callback. Any failed validation leaves both economy and history unchanged. The parent adapter must still perform its atomic durable save before releasing ResultData; on a failed save it must fail-close rather than continue with in-memory state.

Without `rating_changes_by_key`, Economy retains its previous strict rating-preservation rule. With it, every account must be covered by an exact original-ring quote for one match, consistent score order and distinct seats. No generic rating setter is introduced. Typed quotes are trusted policy inputs, never accepted from client wire fields. MatchService must retain the pre-result ratings used to quote money independently of the new result ratings if later payout rules use those ratings.

Departed rounds currently raise before rating enrollment or update, because their classification/partial-settlement policy is separate. The parent routes unsupported rated departures through the existing abort/refund path. No invented skipped-result receipt advances the ring. If the policy is disabled while ordinary profile sequences advance, later re-enabling against an old saved ring rejects the mismatch; an explicit migration decision is needed. Silently resetting that ring is not supported.

Ten coordinator tests cover unchanged defaults, existing300 enrollment without reset, three-account/WORD-wrap commits, opaque/RN preservation, a second table after joint reload, stale and forged proofs, atomic final-participant rejection, coupled money/rating/growth persistence, departure rejection and mode-toggle mismatch. The scoped rating/economy/result suite passes124 tests, including the prior594 original instruction comparisons.

Server integration is available with `run_server.py --ratings local-history`.
The `ratings` journal is saved atomically with profiles, prizes and wagers.
Pre-result rating inputs are retained separately from the updated player D2
values for money-ledger consistency. Five encrypted integration tests pass:
paid/card-prize2/3-player rounds and rematches, CPU payouts, restart and failed
result saves. Failure withholds ResultData and restores the old profile/history.
