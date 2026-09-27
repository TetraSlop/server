# Local ranking service

`ranking_service.py` implements the recovered ranking request, response and
binary-file contract. `test_ranking_service.py` passes **18 tests**; five test
methods execute original x86, with subcases for every ranking category and for
empty/nonempty results. No native game, listener, UI, installed file or existing
repository was changed by these tests. The independent module performs no
filesystem writes and never mutates economy accounts.

## Adapter API

```python
ranking = RankingService(economy,
    historical_stats=None, update_time=0, max_snapshots=64)

replies = ranking.handle(authenticated_account_key, payload)
# None = unrelated; otherwise send every reply from the request's ranking
# service identity, using the ordinary IRC NOTICE envelope.

data = ranking.file(authenticated_account_key, requested_path)
# bytes or None. ProfileServer applies its existing offset/maximum/checksum.

handled = ranking.delete(authenticated_account_key, requested_path)
# Recognized generated-result cleanup; idempotent even if already absent.
```

The service accepts `GTM0R` with exactly `RR(category)` then
`PI(identity,directory,version,selector)`. PI is an ACK-separated payload: hex
`0x` plus16 digits, decimal u8, decimal u16, decimal u32. Identity must match
the authenticated economy account's game identity. This original initializer
sets selector0; unsupported nonzero selectors and malformed/other-owner
requests raise `RankingError`, without storing a result. Directory/version
are validated widths, not used to invent unsupported data versions.

Success is `GTM0R` with `RF(filename),LN(record_count)`. Unknown category returns
mapped `RG(-8811)`. A full generated-file cache returns `RG(-8809)`, the existing
rank-file-write-failure error, without evicting an older unread file. Other
GTM0R vocabulary such as GO is left unhandled; its name does not establish a
request schema.

Normal client request is original `101A6CB0`, sender `1019E9C0`; callback
`101A6BD0` accepts RF/RG only while pending1, then poll `101A6E70` completes.
The exact initializer `101A69B0` retains rank-service identity separately from
profile-file owner identity; do not create a new arbitrary nick for responses.
The adapter replies from the target to which the client sent RR.

## Immutable result files and cleanup

Each successful query publishes an account-scoped immutable snapshot under a
unique short path such as `U/g/TM0_RANKLIST_00000001`. The snapshot exists before
RF is returned. Querying again cannot change a previously announced file;
querying another account cannot expose its result. New queries incorporate
current committed account data. Up to64 result files per account are retained,
with a configurable explicit bound1..1024. There is no silent eviction or TTL.

Deleting an exact RF name removes only that file. Original `101A2320` always
constructs `U/g/TM0_RANKLIST` during ranking-entry cleanup, while the reader
accepts a supplied RF name. This canonical deletion therefore clears the
requesting account's generated ranking namespace. It is an explicit adapter
policy that reconciles immutable query snapshots with that fixed cleanup
name. Other accounts' files remain present. All result snapshots are transient
and disappear on service restart; committed economy data remains the source
for new queries. No real filesystem paths are opened from request strings.

## Binary files and local data policy

Result records are0xE8 bytes with no additional header. Reader `101A6F70` uses
`maximum=count*0xE8` and `offset=first*0xE8`. It accepts at most100 rows in the
rank UI. Only proven rank-consumer fields are populated; all other bytes are
zero in this synthetic dataset, not renamed as undocumented profile fields.

| Field | Result offset | Source/policy |
|---|---:|---|
|Name|00..0F|Saved name, display limited to15 bytes plus NUL. Original101752F7 copies until NUL; a16-byte nonterminated field is unsafe. This never renames the account.|
|Game identity / character key|10 /18|Committed account identity and character ID; both participate in own-row matching.|
|Grand Total prize money, RR0|94|Committed TM0DataFile+50 cumulative result reward. Not current balance or lifetime shop proceeds.|
|Weekly prize money, RR1|9C|Explicit historical `weekly_prize`; otherwise no row in this category.|
|VS. Rating, RR2|A0|Committed TM0DataFile+30 rating.|
|Top30 / Best Rookies, RR3/4|A4|Explicit historical `last_week_rating`; rookies additionally require explicit `rookie=True`.|
|Hide-name / rating-qualified flags|C0 /C2|Explicit optional history flags; local defaults false/true. No unsupported HNSS-to-ranking mapping is inferred.|
|Previous ranks RR0..4|C6,C7,C5,C4,C3|Explicit history tuple;0 unknown. Values1..127 are representable positive signed bytes.|

Current VS. Rating and Grand Total use known saved fields and include
provisioned synthetic accounts. Rows sort by descending score, then game
identity, character key and account key. Equal scores stay adjacent, allowing
the original UI to assign shared ranks. Qualification false excludes a row
from RR2 instead of placing it mid-file: the original category0 UI stops its
whole projection when it sees C2==0. Top30 is locally capped at30; other result
lists at100. These eligibility, cap and tie-order choices are explicit local
policy, not recovered retail-server algorithms.

When the server explicitly enables `local-history` ratings, it supplies the
live `RatingPolicy` to `RankingService`. Rating categories then sort ascending:
the recovered offline ring gives a first two-player winner198 and loser201.
Prize-money categories retain descending order. Current-rating eligibility
requires at least one committed local result whose ring rating and sequence
still match the saved profile. Fresh rating0 accounts, quote-only enrollment
and stale rings after playing with ratings disabled are unranked. This does not
reset profiles or fabricate historical data. Supplied historical rating scores
must use the selected rating scale; their existing explicit eligibility rules
remain in force. Preserved mode keeps its previous ordering and eligibility.

The September27 regression reproduces loser201 ahead of winner198 before this
fix. Five local-policy ranking tests cover enrollment, commits, ties, history,
money order, immutable results, restart, explicit disqualification and stale
rings. Combined ranking/rating suites pass36 cases, including encrypted two-
and three-player rounds/rematches plus server reload.

`RankingHistory` is a frozen optional record supplied by the integration:

```python
RankingHistory(weekly_prize=None, last_week_rating=None,
    rookie=False, anonymous=False, qualified=True,
    previous_ranks=(0, 0, 0, 0, 0))  # RR order0..4
```

No last-week aggregate, automatic calendar rollover, award, rank movement or
rookie eligibility is fabricated from a current profile. With no history,
RR1/3/4 return valid RF+LN0 and an empty file. Prize Center is a separate
service/UI and is not assigned a fake rank category.

Shared `b/g/TM0RkData` is28 bytes, seven little-endian DWORDs. Offsets0/10hex
remain zero/opaque. Offset4 is the displayed denominator shared by VS. Rating
and Grand Total (all provisioned accounts); offset8 counts accounts with weekly
history; offset14hex counts those with last-week rating; offset18hex counts
the explicit rookie subset. These are eligible population denominators before
row caps, not advertised file lengths. OffsetC is `update_time`, default0,
which causes the original UI to omit date text. A configured value is passed
through unchanged; the module does not infer historical timestamps/timezones
or the original weekly tally schedule.

The complete original file and display evidence is in
`protocol_map/AUCTION_RANK_CONTRACT.md`, `FILE_COVERAGE.md` and
`RESULT_REWARD_SEQUENCE.md`.

## Validation

Run `python -B -m unittest test_ranking_service -v` from the server directory.
All18 tests pass, including these original-code checks:

- Request constructor101A6CB0 produces the exact supported RR+PI for IDs0..4;
  only final send, runtime formatting and ordinary harness boundaries are
  substituted.
- Generated RF/LN with nonempty and empty files passes callback101A6BD0 and
  poll101A6E70; original101A6F70 requests the announced filename with exact
  record byte length/offset. Generated RG(-8811) returns the same error.
- Original projection101752A8..101754F8 consumes every generated category,
  including metric offsets, anonymity, tied display ranks and own-row identity.
- Original metadata import101756E3..10175724 consumes the exact28-byte file
  and publishes the six expected display denominators and timestamp.

Other tests cover persisted account restore, score/balance distinction,
historical empty results, two-account isolation, immutable older snapshots,
exact/canonical cleanup, cache exhaustion, input validation, bounded names,
qualification, deterministic ties and100/30 result limits. The original
reader harness emits an existing ResourceWarning from its read-only binary
loader; all emulator assertions execute and pass.

These checks establish wire/file and consumer compatibility under seeded
state. Native ranking navigation and request routing remain untested. No
historical server aggregation/prize policy or persistence of optional history
is claimed. Root integration owns dispatch and profile-file plumbing.
