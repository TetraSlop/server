# Offline reward administration

`tmserver-admin` inspects saved accounts, queues explicit rewards, and records
event progress or eligibility as audited actions. It uses the same validators and atomic
transactions as the game server. It needs the `tmserver[postgres]` packages and
the same database as the server.

Stop the server first. The command takes the same PostgreSQL writer lock as the
server, so an active server blocks it.

Every invocation needs the database URL, from `TM_DATABASE_URL` or
`--database-url URL`; prefer the environment variable to keep credentials out of
command history. There is no account creation, and an empty database is
rejected. Install the updated package to get the command, or use
`python -m tmserver.cli.admin` from the configured source checkout.

The examples below assume `TM_DATABASE_URL` is set.

## Accounts and passwords

Players register themselves on their first LOGIN (see the
[loader login contract](LOADER_AUTH_SPEC.md)). An operator can set a new
password, or refuse and re-allow LOGIN:

```powershell
'new password' | tmserver-admin account-password --account-key UGIRSKKH3
tmserver-admin account-disable --account-key UGIRSKKH3
tmserver-admin account-enable --account-key UGIRSKKH3
```

`account-password` reads one line, 1 to 15 printable ASCII characters, from
standard input so the password stays out of the command line. It keeps the
account's disabled flag. Disabling stops new LOGINs; a player already connected
keeps playing until the server restarts. These commands are not audited. They
apply only to accounts that registered with LOGIN, so not to the built-in test
accounts or to accounts from the retired Discord sign-in.

## Select the saved account

```powershell
tmserver-admin accounts
```

The JSON output contains `account_key`, `username`, `pol_id`, `player_id`,
`name`, and `guild` for each saved account. `username` is what the player types
into tmloader, or null for accounts without a login. Copy the exact
`account_key` into the commands below.
Character names are display information and cannot select an owner. The saved
key can differ from the synthetic login ID shown by `tmserver --list-accounts`.

For any save containing configured events, also supply the same complete
configuration used by its server:

```powershell
tmserver-admin --events .\examples\events.example.json accounts
```

The configured event IDs must exactly match the saved IDs, and each saved event
fingerprint must match. This tool does not add, remove, or reconfigure events.
Place storage and `--events` options before the command name.

## Inspect an account and its pending rewards

```powershell
tmserver-admin account --account-key KEY
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
Like other offline commands, it needs exclusive writer ownership, so stop the
server first. Reading the summary does
not collect rewards, enroll members, recover interrupted matches, advance
rankings, or write the save. Pending rewards stay separate from owned cards,
money and Prize Center points. Normal server startup is responsible for
committing interrupted-match recovery.

## Audited actions

Every change is an audited action. It records the exact request, operator,
reason and outcome with the change itself, in one commit. Each action takes the
exact account key, a stable `--request-id`, `--operator` and `--reason`:

```powershell
$audit = @('--account-key', 'KEY', '--request-id', 'operator-request-001', '--operator', 'local-admin', '--reason', 'Tournament award')
tmserver-admin prize-award @audit --id local-award-001 --top30 500
tmserver-admin operator-receipt --request-id operator-request-001
```

The command prints the receipt. New actions require an idle account; the
running panel applies the same rule.

Keep the same request ID for a retry. Request IDs are global across all actions.
An exact retry returns the original receipt without applying anything again,
even after the player collected the reward, after a restart, or while the
account is busy. A retry with different facts (amounts, reason, operator,
source or target) under the same request ID is rejected instead of reusing it.
CLI and browser requests record their distinct sources.

A storage error can mean the acknowledgement was lost after the commit. Stop
using a failed server/store, reload it, and look up the original request ID with
`operator-receipt` before deciding whether to retry. Do not substitute a new ID
for an uncertain outcome. The change and receipt always commit together; a
failure before commit leaves both unchanged. PostgreSQL needs
`tmserver-db upgrade` for the audit journal.

## Queue a Prize Center award

```powershell
tmserver-admin prize-award @audit --id local-award-001 --top30 500
```

`--top30`, `--lucky`, and `--rookies` each accept 0 through 65,535 and default
to zero. Optional `--lucky-cards "5,9,65535"` supplies exactly three model IDs:
0 through 249, or 65,535 for no card. These are the existing Lucky Card display
fields; they do not grant inventory cards.

The award is queued for the account's next Prize Center visit. Its normal
collection flow credits points using the recovered 99,999 point cap; the receipt
shows the points before the award and after redemption. Only one unredeemed
award bundle may exist per account. `--id` names the award; it is scoped to the
account. An award ID that already exists, including one queued by an older
version without a receipt, is rejected rather than attributed to a new request.
IDs contain 1–128 letters, digits, periods, underscores, colons, or hyphens.

## Queue event cards or money

```powershell
tmserver-admin --events .\examples\events.example.json event-grant @audit --event festival --id local-grant-001 --money 250 --card "0,12,0,10,10,5"
```

This queues explicit assets for the event's normal collection screen. It does
not edit the account's balance or owned cards, and does not rewrite event
progress. The account must already be a saved member of that event.

`--money` accepts 0 through 99,999,999 and defaults to zero. Repeat `--card` for
at most 30 cards; each argument contains six decimal integers in this order:

```text
MODEL,ATTACK,TYPE,PHYSICAL,MAGICAL,ARROWS
```

Model is 0–249, type is 0–3, and the remaining values are bytes. At least one
combat stat must be nonzero. Arrows is the original direction bitmask. The
catalogue computes the card's points and validates the complete card; deck is
always 255 (unassigned). The receipt shows each complete card, the member's
eligibility and whether collection was enabled.

Excluded members and events with collection disabled can receive queued grants.
Applying a grant does not change eligibility, enable collection, create a
schedule, or invent a reward formula. The existing collection rules decide when
the player may accept it. Full inventory does not discard a grant: the
collection flow presents its overflow editor and commits only accepted batches.
Money above the account's available capacity also remains pending. A grant ID
that already exists for this account and event is rejected.

Prize-only audit journals keep version 1. The first event grant uses version 2,
preserving existing Prize receipts unchanged.

## Correct event eligibility or progress

```powershell
tmserver-admin --events .\examples\events.example.json event-eligibility @audit --event festival --value -1
tmserver-admin --events .\examples\events.example.json event-progress @audit --event festival --id local-progress-001 --steps 2 --completed-missions 1
```

`--value` is a signed 32-bit integer. Negative values exclude the member from
event admission and collection; zero restores ordinary eligibility.

`event-progress` adds `--steps` (nonnegative; the total must fit 0 through
2,147,483,647) and sets additional `--completed-missions` bits, a decimal
bitmask: 1 for the first mission, 2 for the second, 4 for the third, or their
sum. Only configured mission bits are accepted and existing bits stay set. Both
default to zero. `--id` names the update; an update ID that already exists is
rejected. Progress cannot advance an excluded member.

Both commands require a registered, idle member and record the member's state
before and after in the receipt, with status `applied`. They preserve
membership and pending grants, never enroll a missing member, and do not grant
cards, money or Prize Center points. The first correction upgrades the audit
journal to version 3, preserving v1/v2 receipts. Older server versions cannot
read v3 saves.

The opt-in live panel exposes the same actions; see [panel setup](ADMIN_PANEL.md).
Its web operator comes from the authenticated session.

## Commit behavior

The database must already contain state and use the current schema. Its existing
session advisory lock excludes the running server and other cooperative writers.
Database credentials are not included in success output.

All commands validate the saved economy before acting. Success JSON is printed
only after the store operation succeeds; a failed save produces a nonzero exit
without success output. Unrelated journals, account data, ranking periods and
pending recovery claims remain intact. The tool does not start listeners,
recover matches, settle auctions, advance ranking weeks, or infer historical
retail reward rules.
