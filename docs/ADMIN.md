# Offline reward administration

`tmserver-admin` inspects saved accounts, queues explicit rewards, and records
event progress or eligibility. It uses the same validators and atomic
transactions as the game server. JSON administration needs only the normal
Python installation; PostgreSQL needs the optional `tmserver[postgres]` packages.

Stop the server or close the Local Play session first. The command holds the
same writer lease as the normal launcher, so an active server blocks it. Keep
the JSON `.writer.lock` and `.lock` sidecars in place; their existence does not
mean the save is busy, and removing them can split the locks. Older launchers
and custom scripts that do not acquire the lease must also be stopped.

Every invocation requires an explicit `--state PATH` or `--database-url URL`.
There is no default save, automatic database selection, or account creation.
An absent save/database state is rejected. Install the updated package to get
the command, or use `python -m tmserver.cli.admin` from the configured source checkout.

## Select the saved account

```powershell
tmserver-admin --state .\var\local_state.json accounts
```

The JSON output contains `account_key`, `player_id`, `name`, and `guild` for
each saved account. Copy the exact `account_key` into the commands below.
Character names are display information and cannot select an owner. The saved
key can differ from the synthetic login ID shown by `tmserver --list-accounts`.

For any save containing configured events, also supply the same complete
configuration used by its server:

```powershell
tmserver-admin --state .\var\local_state.json --events .\examples\events.example.json accounts
```

The configured event IDs must exactly match the saved IDs, and each saved event
fingerprint must match. This tool does not add, remove, or reconfigure events.
Place storage and `--events` options before the command name.

## Inspect an account and its pending rewards

```powershell
tmserver-admin --state .\var\local_state.json account --account-key KEY
```

The summary shows the saved identity, money balance, card count, Prize Center
points, any pending Prize Center award, and existing event memberships. Event
entries include eligibility, steps, the completed-mission bitmask, and remaining
unclaimed card and money totals. After partial collection, these totals exclude
what was already accepted or credited. A missing Prize Center award is `null`;
an account with no event memberships has an empty event list.

The same summary also includes outstanding claims, separately from owned assets:

- `auction_claims` contains remaining payment/refund amounts, won/returned card
  counts, and individual receipts with stable IDs and card records. Partially
  credited receipts retain their ID and report only the remaining money.
- `event_claims` identifies each unclaimed grant by event and grant ID, with its
  remaining card and money counts. Fully collected grants are omitted.
- `pending_result` describes the unaccepted result reward ID, seat and extra
  card count, or is `null`. A restored result is recovery-only; this does not
  imply that the original game can resume it.
- `match_rounds` lists the account's unfinished rounds, including escrowed money
  and earned cards not yet handed to result collection. Completed payouts are
  already in the account balance. Cards handed to `pending_result`, or recovered
  into Returned Cards, are not counted again here.

An empty claim list does not mean an active auction has expired. Inspection
never advances auction time. The shared `tmserver.admin.queries.account_summary`
function produces the same detached read model without a CLI or web dependency.

Supply the matching `--events` configuration when the save contains events.
The same command accepts `--database-url`. Like other offline commands, it needs
exclusive writer ownership, so stop the server first. Reading the summary does
not collect rewards, enroll members, recover interrupted matches, advance
rankings, or write the save. Pending rewards stay separate from owned cards,
money and Prize Center points.

## Preview interrupted-match recovery

```powershell
tmserver-admin --state .\var\local_state.json recovery
```

This runs the server's existing startup recovery rules on independent in-memory
copies. It reports `interrupted_rounds` and `unaccepted_results`, plus accounts
whose money or Returned Cards queue would change. For each such account, it
shows the old/new balance, refunded money, newly returned cards and the total
Returned Cards count after recovery. A result with no extra cards still counts
as an unaccepted result, even when it produces no account entry.

Unfinished stakes are refunded; completed results keep their recorded payouts.
Earned, unaccepted cards go through the existing Returned Cards collection flow.
Already accepted rewards are not returned again. This command neither writes
the save nor advances auction expiry, ranking weeks, or event progress. It
requires the same event configuration and writer ownership as other commands.
Normal server startup remains responsible for actually committing recovery.

## Preview and audit a Prize Center award

The audited flow records the exact request, operator, reason and original result
with the queued award in one commit. Preview first, then confirm its digest:

```powershell
$award = @('--account-key', 'KEY', '--id', 'local-award-001', '--request-id', 'operator-request-001', '--operator', 'local-admin', '--reason', 'Tournament award', '--top30', '500')
tmserver-admin --state .\var\local_state.json prize-preview @award
tmserver-admin --state .\var\local_state.json prize-apply @award --digest DIGEST_FROM_PREVIEW
tmserver-admin --state .\var\local_state.json operator-receipt --request-id operator-request-001
```

Preview does not write. It shows current points and the points available after
normal redemption, including the 99,999 cap. Confirmation fails if relevant
account or Prize Center state has changed. New audited awards require an idle
account; the running panel applies that same rule. Lucky Card selections remain
display values and never grant inventory cards.

Keep the same request ID for a retry. IDs are global across operator actions;
award references remain scoped to their account. An exact committed retry returns
the original receipt even after redemption or while that account is busy. Changed
amounts, reason, operator, source or target conflict instead of silently reusing
an ID. CLI and browser requests record their distinct sources. An older award
without an audit receipt cannot be attributed retroactively to a new request.

A storage error can mean the acknowledgement was lost after the commit. Stop
using a failed server/store, reload it, and look up the original request ID before
deciding whether to retry. Do not substitute a new ID for an uncertain outcome.
The award and receipt always commit together; a failure before commit leaves
both unchanged. PostgreSQL needs `tmserver-db upgrade` for the audit journal.

## Legacy Prize Center award command

The earlier command remains compatible for existing local scripts, without the
new preview, reason or operator audit receipt:

```powershell
tmserver-admin --state .\var\local_state.json prize-award --account-key KEY --id local-award-001 --top30 500
```

`--top30`, `--lucky`, and `--rookies` each accept 0 through 65,535 and default
to zero. Optional `--lucky-cards "5,9,65535"` supplies exactly three model IDs:
0 through 249, or 65,535 for no card. These are the existing Lucky Card display
fields; they do not independently grant inventory cards.

The award is queued for the account's next Prize Center visit. Its normal
collection flow credits points using the recovered 99,999 point cap. Only one
unredeemed award bundle may exist per account.

Use a stable `--id` for one logical award and repeat it after an uncertain
outcome. A new award reports `"status": "queued"`; a known ID reports
`"status": "already-recorded"` and never grants again, even after redemption.
The existing Prize Center journal retains IDs rather than historical amounts,
so that second status does **not** assert that the repeated amounts match.
Changing the values does not replace an award. IDs contain 1–128 letters,
digits, periods, underscores, colons, or hyphens.

## Preview and audit event cards or money

The audited event flow queues explicit assets for the event's normal collection
screen. It does not edit the account's balance or owned cards:

```powershell
$grant = @('--account-key', 'KEY', '--event', 'festival', '--id', 'local-grant-001', '--request-id', 'operator-event-001', '--operator', 'local-admin', '--reason', 'Event participation award', '--money', '250', '--card', '0,12,0,10,10,5')
tmserver-admin --state .\var\local_state.json --events .\examples\events.example.json event-grant-preview @grant
tmserver-admin --state .\var\local_state.json --events .\examples\events.example.json event-grant-apply @grant --digest DIGEST_FROM_PREVIEW
tmserver-admin --state .\var\local_state.json --events .\examples\events.example.json operator-receipt --request-id operator-event-001
```

Preview shows each complete card, including catalogue-derived points and deck
255, the member's eligibility, and whether collection is enabled. It changes
no saved state. Confirmation records the grant and operator receipt in one
commit, without rewriting the account or event progress. The account must be
an existing event member and idle when a new grant is queued.

Excluded members and events with collection disabled can receive queued grants;
the preview makes that status explicit. Applying a grant does not change
eligibility, enable collection, create a schedule, or invent a reward formula.
The existing collection rules decide when the player may accept it.

Request IDs are shared with Prize Center administration. Exact retries return
the original receipt even after partial or full collection, after restart, or
while the account is busy. Changed facts conflict. A legacy grant cannot acquire
a new operator attribution through this command. Keep the original request ID
after an uncertain storage outcome and reload before inspecting or retrying it.

Prize-only audit journals retain version 1. The first audited event grant uses
version 2, preserving existing Prize receipts unchanged. PostgreSQL still uses
schema revision `0002_admin_audit`; no additional migration is required.

The card and money limits below apply to both audited and legacy grants.

## Legacy event grant command

This command remains available for existing scripts. It has no preview, operator
reason or audit receipt:

```powershell
tmserver-admin --state .\var\local_state.json --events .\examples\events.example.json event-grant --account-key KEY --event festival --id local-grant-001 --money 250 --card "0,12,0,10,10,5"
```

The account must already be a saved member of that event. `--money` accepts
0 through 99,999,999 and defaults to zero. Repeat `--card` for at most 30 cards;
each argument contains six decimal integers in this order:

```text
MODEL,ATTACK,TYPE,PHYSICAL,MAGICAL,ARROWS
```

Model is 0–249, type is 0–3, and the remaining values are bytes. At least one
combat stat must be nonzero. Arrows is the original direction bitmask. The
catalogue computes the card's points and validates the complete card; deck is
always 255 (unassigned). These are explicit card attributes, without a random
generation or automatic award rule.

The grant stays pending for the normal event collection screen. Full inventory
does not discard it: the existing collection flow presents its overflow editor
and commits only accepted batches. Money above the account's available capacity
also remains pending. Existing eligible members may collect after the event
ends, under the server's documented local recovery policy.

An exact replay of the same account/event/grant ID and contents reports
`already-recorded`. Reusing that ID with different cards or money is rejected.
Cards and money are not transferred directly by this command.

## Change event eligibility

```powershell
tmserver-admin --state .\var\local_state.json --events .\examples\events.example.json event-eligibility --account-key KEY --event festival --value -1
tmserver-admin --state .\var\local_state.json --events .\examples\events.example.json event-eligibility --account-key KEY --event festival --value 0
```

The value must be a signed 32-bit integer. Negative values exclude the existing
member from event admission and collection; zero restores ordinary eligibility.
The command preserves membership, progress and pending grants. It neither
enrolls a missing member nor changes progress. Output reports `updated` or
`unchanged`, with the stored value.

## Record explicit event progress

```powershell
tmserver-admin --state .\var\local_state.json --events .\examples\events.example.json event-progress --account-key KEY --event festival --id local-progress-001 --steps 2 --completed-missions 1
```

This records an operator's decision for an existing,
eligible event member. `--steps` adds a nonnegative number of steps; the total
must fit 0 through 2,147,483,647. `--completed-missions` is a decimal bitmask of
additional completed missions: 1 for the first bit, 2 for the second, 4 for the
third, or their sum. Only configured mission bits are accepted. Existing
completed bits remain set. Both options default to zero.

Use one stable `--id` per logical update. An exact retry reports `already-recorded`
without adding steps twice; changing that ID's facts is rejected. A fresh update
reports `recorded`. Progress and its replay receipt are saved together. This
command does not grant cards, money or Prize Center points, and does not infer
historical mission triggers. Inspect the resulting state with `account`.

## Preview and audit event membership corrections

Prefer the audited commands for operator work. They require the same exact account
and event as the legacy commands, plus a stable request ID, operator and reason.
For example, preview a progress correction:

```powershell
tmserver-admin --state .\var\local_state.json --events .\examples\events.example.json event-progress-preview --account-key KEY --event EVENT --id correction-1 --steps 3 --completed-missions 0 --request-id request-1 --operator admin --reason "Correct participation"
```

Review `before` and `after`, then repeat with `event-progress-apply` and
`--digest DIGEST_FROM_PREVIEW`, keeping all other arguments unchanged. Use
`event-eligibility-preview` / `event-eligibility-apply` with `--value SIGNED_INT`
instead of `--id`, `--steps` and `--completed-missions` to change eligibility.
Negative values exclude the member; zero allows them. An eligibility request does
not need a separate update ID.

Both actions require a registered, idle member. Progress cannot advance an
excluded member, overflow the displayed step count, or set unconfigured mission
bits. The member and audit receipt commit together, with status `applied`; cards,
money and queued rewards are unchanged. If the member or event configuration
changes between preview and confirmation, obtain a fresh preview.

Read the retained receipt with `operator-receipt --request-id request-1`. Exact
retries return the original before/after facts without applying them again, even
after later corrections or restart. A request ID cannot be reused for another
operator, action or set of facts. An existing legacy progress ID cannot be assigned
a new audit receipt. Legacy `event-progress` and `event-eligibility` commands remain
available, but do not record an operator/reason history.

The opt-in live panel exposes the same operations under **Correct event membership**;
see [panel setup](ADMIN_PANEL.md). Its web operator comes from the authenticated
session. Existing audit journals upgrade to v3 on the first membership correction,
preserving v1/v2 receipts. Older server versions cannot read v3 saves.

## PostgreSQL and commit behavior

Use the same commands with explicit database selection, for example:

```powershell
tmserver-admin --database-url $env:TM_DATABASE_URL accounts
```

The database must already contain state and use the current schema. Its existing
session advisory lock excludes the running server and other cooperative writers.
Database credentials are not included in success output.

All commands validate the saved economy before acting. Success JSON is printed
only after the store operation succeeds; a failed save produces a nonzero exit
without success output. Unrelated journals, account data, ranking periods and
pending recovery claims remain intact. The tool does not start listeners,
recover matches, settle auctions, advance ranking weeks, or infer historical
retail reward rules.
