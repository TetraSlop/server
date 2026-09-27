# Optional weekly rankings

Enable recorded local rankings when starting the server:

```powershell
.venv/Scripts/tmserver --ratings local-history --weekly-rankings
```

The integrated launcher accepts the same options:

```powershell
.\Play.cmd --ratings local-history --weekly-rankings
```

This is an explicit replacement-server policy. Weeks start Monday at00:00 UTC.
Tracking starts when enabled; existing saves do not contain dates sufficient to
reconstruct older weeks. The normal launch remains unchanged unless this option
is supplied.

| Game category | Recorded local behavior |
|---|---|
| Weekly Total prize money | Sum of completed ordinary human/CPU result rewards recorded this week. Played accounts with zero rewards are included. |
| Top30 | The previous week's last recorded local-history rating for each participant; lower values rank first. |
| Best Rookies | Empty by default. No account creation dates or rookie rules are inferred. |
| Grand Total / current VS. Rating | Existing committed profile totals and current-rating policy. |

Weekly money uses the change in the game's cumulative result-reward counter,
so shop proceeds, auction refunds and full-tie stake refunds do not count.
Accumulation wraps at the original unsigned32-bit field width. Events do not
contribute ordinary result rewards or these local ratings. No automatic awards,
weekly prize grants or historical retail policies are added.

Rollover occurs before new ranking requests, ranking metadata reads, startup
listeners and recorded results. It does not need a background timer. Missing
several weeks leaves the immediately preceding week empty. A clock moving into
an earlier week is rejected rather than rewriting already recorded periods.
An issued ranking file remains an immutable snapshot until the client deletes it.

The original ranking screen's date labels are hidden in this mode. It uses one
timestamp for both an older closed-week tally and the next update, which cannot
truthfully describe our live current-week prizes and previous-week ratings.
Only that display field is zeroed; the saved period dates and rollover still
use Monday UTC. Explicit historical timestamps outside automatic weekly mode
remain supported. See [original display evidence](research/RANKING_DISPLAY_REVIEW.md).

Period state shares the existing rating/result transaction. A failed save cannot
publish a new week or a credited result. Turning tracking off preserves its
saved data, stops recording and suppresses weekly projections. Turning it back
on resumes recorded local play; games played while disabled are not backfilled.
Keep `--ratings local-history` enabled while pausing weekly tracking. Playing
with ratings disabled advances profile results without updating the saved
rating ring; re-enabling that stale ring requires an explicit migration, as it
does without weekly rankings. The server never silently resets rating history.

Saves without weekly state retain their original ratings v1 format. Initialized
weekly state uses ratings v2 inside the existing JSON/database journal. The new
server reads both versions; an older server binary cannot read v2. PostgreSQL
needs no schema migration for this nested journal extension. Native account
bytes and original game assets are unaffected.
