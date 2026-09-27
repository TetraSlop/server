# Offline reward administration

`tmserver-admin` lists saved accounts, queues explicit rewards, and changes an
existing event member's eligibility. It uses the same validators and atomic
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
the command, or use `python -m tmserver.admin` from the configured source checkout.

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

## Queue a Prize Center award

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

## Queue event cards or money

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
