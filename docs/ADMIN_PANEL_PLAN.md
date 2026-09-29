# Admin panel and remaining plan

Requested 2026-09-27. The first deliverable is a local, authenticated operator
panel for the running server. The read-only panel is implemented as an optional
install extra; the CLI and panel share account/claim definitions. Audited Prize
Center awards, event grants, eligibility and progress corrections use shared
application functions.

## Run it

Install `tmserver[admin]` (or `pip install -e ".[admin]"` in the checkout).
Pass `--admin-port 8080 --admin-password-file C:/private/operator-password.txt`
to the normal server command, then open `http://127.0.0.1:8080/admin/`.
The file contains one password line; trailing line endings are removed. An
explicit file takes precedence over the alternative `TM_ADMIN_PASSWORD`
environment variable. The username defaults to `admin`; change it with
`--admin-user`. Credentials are never printed in startup messages.

Without `--admin-port`, no HTTP listener starts and no admin framework is imported.
Port zero selects an available local port, reported after successful startup.
This panel uses the existing running server; do not start a second server over
the same save just to enable it. The offline `tmserver-admin` command remains
available for saves that are not currently owned by a running server.

The panel is read-only by default. Add `--admin-actions` to enable **Queue prize
award**, **Queue event grant** and **Correct event membership**, also linked from
account details. For Prize
Center, choose an exact account, point amounts,
optional Lucky Card displays and a reason. Preview shows the exact request before
confirmation. A successful commit displays a durable receipt with its request ID,
operator and facts. Refreshing or retrying that request returns the same receipt.
New awards require an idle account; this is an explicit administration policy.
Lucky Card displays do not grant inventory cards, and prize points are not money.
See [offline administration](ADMIN.md) for the equivalent audited CLI commands.

Event grants select an existing member's exact account and configured event.
Enter money and optional cards through named fields; **Add card row** preserves
the draft and request ID. Preview shows canonical card names, stats and points,
plus the member's eligibility and whether collection is enabled. Confirmation
queues the grant for normal game collection. The account's balance and inventory
do not change at this step. Exact retries keep the same receipt after partial or
complete collection. No automatic event schedule or reward formula is added.

Membership corrections select **Eligibility** or **Progress**. Eligibility sets
the signed status; negative values exclude entry and collection. Progress adds
trusted steps and completed mission bits with a stable progress reference. The
preview shows every member field before and after; confirmation commits the event
change and its audit receipt together. Corrections require an idle, registered
member. They do not award assets. A changed member or event configuration invalidates
an unconfirmed preview. Exact retries return the original receipt even after later
corrections or restart, without applying it again.

**Receipt history** is available even when actions are disabled. Search by exact
request ID, account, action, operator, source or reason; sort by saved time or
open a read-only detail page. With actions enabled, **Open receipt** shows the
original typed confirmation. History survives collection and restart. After a
storage failure, history reports that reload is required instead of presenting
an empty or potentially stale result.

PostgreSQL users must first stop the owning server and run `tmserver-db upgrade`
to revision `0002_admin_audit`. Existing accounts and Prize Center records remain
unchanged. JSON saves accept an absent audit journal and add it when needed.

## Product scope

| View | First version |
| --- | --- |
| Overview | Server revision, uptime, storage health, connected accounts and room/table occupancy. |
| Accounts | Search by exact account key or display name, with identity, balance, inventory and rating-policy status. Names never select mutation owners. |
| Pending claims | Prize Center awards, event grants, auction payments/refunds, won/returned cards, unaccepted match results and interrupted-round state. |
| Rooms and events | Configured room rules, Computer availability, membership and table occupancy. Account details retain event progress in the shared query. |
| Receipt history | Searchable retained operator requests, reasons, sources and commit times; typed receipt links when actions are enabled. |
| Diagnostics | Storage/logging status in the overview. A detailed failure browser is deferred. |

Show claimable assets separately from owned inventory and spendable money. A
pending match result can relate to a still-open round; the shared query already
excludes handed-off/recovered seats from round claims. Preserve that separation
instead of adding the same assets into a misleading grand total. Account details
include named inventory cards, combat fields, arrows, deck assignments and the
server's rating policy. Prize points, money and card deliveries have separate
columns; lucky-card display IDs are not counted as inventory grants.
Each page identifies its capture time and refreshes explicitly. Empty and failed
states must be clear. The panel needs to be usable without knowing packet names.

## Framework and ownership

The optional `admin` extra uses Starlette Admin 1.0.1, Starlette 1.7.0 and Uvicorn
0.54.0. Starlette Admin supplies authentication integration, CSRF handling,
navigation, read-only model views, tables, search, sorting and bundled assets.
Jinja templates customize capture times, empty states, login and account inventory.
No ORM or separate database model is attached to the panel.

The game host owns the one writable state. Start the optional HTTP application
beside the existing listeners in that same process/event loop, with one worker.
The CLI's async context owns the embedded Uvicorn listener and stops HTTP before
the protocol listeners and storage. Uvicorn does not install competing process
signal handlers. The HTTP app receives the
already constructed host; it must not construct another Server or open a second
writer over the same save. Keep the panel off by default and bind its first
release to loopback on an explicitly configured port.

Shared read projections live in
[admin/queries.py](../src/tmserver/admin/queries.py). They return detached values
without I/O, collection, expiry, recovery or enrollment. Capture these values on
the host event loop before rendering or awaiting. The existing offline CLI keeps
its exclusive writer lease and explicit save/database selection.

Under `src/tmserver/admin/`, `server.py` attaches the panel to the shared
`network/http.py` listener owner, `web.py` owns the application
and origin boundary, `auth.py` operator access, `views.py` framework projections,
and `templates/` the page extensions. Static assets come from the framework.
`awards.py` and `event_grants.py` adapt forms; `actions.py` and `event_actions.py`
perform shared preview/commit operations. `audit.py` validates immutable requests
and durable receipts with Pydantic.
HTTP routes translate inputs and errors; feature services own game rules and
transaction coordinators own commits. Package templates/static files as runtime
resources and include them in R3's wheel verification.

## Access and audited actions

An explicit operator credential is required even on loopback. Framework
[session middleware](https://www.starlette.io/middleware/#sessionmiddleware)
provides signed HttpOnly cookies; signed contents remain readable, so never
store credentials or account data in them. Sessions expire after 15 minutes and
logout revokes copied cookies too; at most 64 sessions are retained. Host/Origin
must match the actual loopback address and port. Responses are private/no-store.
Login and logout require same-origin POSTs with framework CSRF protection.
Generic game write/import/export routes are disabled, including direct API calls.
Explicit action enablement permits only Prize Center, event grant and membership
preview and confirmation POSTs, under the same login, origin and CSRF checks.
Remote administration and multiple operator roles belong to a later hosted
deployment milestone with TLS and a deliberate identity provider.

Prize Center awards, event grants and membership corrections share application
functions from CLI and web.
The authenticated session supplies the web operator; forms cannot substitute
another actor. Routes never edit SQL tables, profile byte
arrays or journals directly.

Every action needs an exact account key, a preview, a reason, a stable request ID,
and an audit receipt committed with the change. Prize Center now commits its
award and `admin_audit` journal together; event grants commit `event_cards` and
`admin_audit` without writing accounts or progress. Eligibility and progress commit
`events` and `admin_audit` without changing accounts, cards or queued rewards. Audit
journal v3 accepts older v1/v2 receipts; no additional SQL migration is required.
Future actions must use the same joint
commit rule; a separate audit write after the feature commit would not suffice.
Existing receipt rules continue
to control retries; a browser retry must not issue a fresh grant. Show success
only after durable commit. Reject or defer actions incompatible with an account's
active match/trade/reservations using the same feature rules. A stale preview
must be detected or regenerated before application.

A storage error means the process cannot confirm the outcome. The page retains
the request ID and gives no success or false "not found" result. Restart the
server to reload its save, then inspect the same receipt or retry exactly the
same request. Do not create a fresh award to compensate for an uncertain result.
For an event grant, keep the failure page open and use its sign-in link after
restart. Sign in in that separate tab, then return to check the receipt or retry
the original request. The form retains its facts and updates its form token;
old tokens remain invalid.
Receipts remain available after player redemption, and downgrading the database
cannot silently discard them.

Direct inventory editing, rating-history repair, arbitrary database editing and
server restart controls are separate capabilities requiring their own semantics.
The first panel does not need them to be useful.

## Delivery checkpoints

1. **Shared queries:** CLI and future routes agree on claims, partial collection,
   account ownership and missing optional journals. Inspection changes no state.
2. **Read-only panel:** overview, account list/details and claim views work on a
   disposable JSON fixture and a disposable PostgreSQL fixture, while encrypted
   game sessions continue normally. Authentication and session expiry are tested.
3. **Packaged acceptance:** install the wheel without the checkout, load every
   template/asset, and check the pages with a browser against disposable data.
   An ordinary installation without the admin extra still starts normally.
4. **Audited actions:** cover unauthorized/CSRF requests, duplicate submissions,
   conflicting previews, busy accounts and storage failure. Test that failed
   commits publish neither game state nor receipts. A lost commit acknowledgement
   requires reload and exact retry because durable storage may already contain both.

Checkpoints 1–4 have source, authentication, actual HTTP/encrypted-protocol and
installed-wheel checks. Headless browser checks use disposable state. Disposable
PostgreSQL checks cover reads, atomic awards, rollback, lost acknowledgements,
restart/retry, upgrades and downgrade protection. See
[VALIDATION.md](VALIDATION.md) for exact test results and outstanding checks.
Native game acceptance remains separately user-operated.

## Completed membership checkpoint and next boundary

Eligibility and progress now use staged `EventHost` operations shared by CLI and
panel. Previews bind the member identity, before/after state and configuration.
Exclusion removes pending admission only after persistence succeeds. Progress
preserves its event/account/update-ID scope, mission mask and overflow checks.
Historical receipts survive subsequent corrections and restart; neither action
edits accounts or rewards. JSON, real PostgreSQL and installed-package checks
cover these contracts. See [validation](VALIDATION.md).

The next hosted-account work is durable identity enrollment and provisioning,
currently outside the operator panel. Define how a verified Discord user receives
a new account and how an operator deliberately links an existing save before
adding account-management forms. Never infer ownership from a display name.
Keep public administration, multiple roles and rating repair separate from the
completed local operator workflow.
