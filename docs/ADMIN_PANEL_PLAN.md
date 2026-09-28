# Admin panel plan

Requested 2026-09-27. The first deliverable is a local, authenticated operator
panel for the running server. This document is a plan; no web listener or web
dependencies have been added yet. The shared account/claim queries are being
implemented first so the CLI and panel use the same definitions.

## Product scope

| View | First version |
| --- | --- |
| Overview | Server revision, uptime, storage health, connected accounts and room/table occupancy. |
| Accounts | Search by exact account key or display name, with identity, balance, inventory and rating-policy status. Names never select mutation owners. |
| Pending claims | Prize Center awards, event grants, auction payments/refunds, won/returned cards, unaccepted match results and interrupted-round state. |
| Rooms and events | Configured rules, Computer availability, current seats/observers and event eligibility/progress. |
| Diagnostics | Bounded recent failures and receipt IDs, with sensitive transport/authentication values excluded. |

Show claimable assets separately from owned inventory and spendable money. A
pending match result can relate to a still-open round; the shared query already
excludes handed-off/recovered seats from round claims. Preserve that separation
instead of adding the same assets into a misleading grand total. Detailed
inventory and rating-policy status need additional read projections; the current
account query provides the inventory count and claim details.
Each page identifies its capture time and refreshes explicitly. Empty and failed
states must be clear. The panel needs to be usable without knowing packet names.

## Framework and ownership

Use an optional `admin` install extra with FastAPI, Jinja2 and Uvicorn. FastAPI
has an existing [Jinja2 integration](https://fastapi.tiangolo.com/advanced/templates/)
for server-rendered pages and static assets. Keep the initial interaction model
to links, search and forms; add live refresh only to views that benefit from it.
Pydantic belongs at the HTTP input/output boundary, without replacing the core
feature models or SQLAlchemy Core persistence.

The game host owns the one writable state. Start the optional HTTP application
beside the existing listeners in that same process/event loop, with one worker.
Use the framework's [lifespan support](https://fastapi.tiangolo.com/advanced/events/)
to start and stop the HTTP resources predictably. The HTTP app receives the
already constructed host; it must not construct another Server or open a second
writer over the same save. Keep the panel off by default and bind its first
release to loopback on an explicitly configured port.

Shared read projections live in
[admin/queries.py](../src/tmserver/admin/queries.py). They return detached values
without I/O, collection, expiry, recovery or enrollment. Capture these values on
the host event loop before rendering or awaiting. The existing offline CLI keeps
its exclusive writer lease and explicit save/database selection.

Proposed additions under `src/tmserver/admin/` are `web.py` for application and
route wiring, `auth.py` for operator access, and `templates/` plus `static/` for
pages. Add an action module only when the first real shared action needs it.
HTTP routes translate inputs and errors; feature services own game rules and
transaction coordinators own commits. Package templates/static files as runtime
resources and include them in R3's wheel verification.

## Access and later actions

Require an explicit operator credential even on loopback. Use a maintained
authentication/session library, a short session lifetime and logout. Framework
[session middleware](https://www.starlette.io/middleware/#sessionmiddleware)
provides signed HttpOnly cookies; signed contents remain readable, so never
store credentials or account data in them. Validate Host/Origin, keep responses
private/no-store and require CSRF protection for all state-changing requests.
Remote administration and multiple operator roles belong to a later hosted
deployment milestone with TLS and a deliberate identity provider.

After the read-only panel passes acceptance, expose the existing explicit
operator actions: queue Prize Center awards, queue event grants, update event
eligibility and record event progress. First extract their validation/transaction
paths from the CLI into shared application functions. Routes must never edit SQL
tables, profile byte arrays or journals directly.

Every action needs an exact account key, a preview, a reason, a stable request ID,
and an audit receipt committed with the change. This is additional persistence
work: add validation/storage for the audit journal and include it in the relevant
joint commit. A separate write after the feature commit would not suffice.
Existing receipt rules continue
to control retries; a browser retry must not issue a fresh grant. Show success
only after durable commit. Reject or defer actions incompatible with an account's
active match/trade/reservations using the same feature rules. A stale preview
must be detected or regenerated before application.

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
   commits leave both game state and receipts unchanged.

Start checkpoint 2 after R1/R2 fixes and R3 validation, using R7's shared query
foundation. Native game acceptance can proceed independently; this panel is
operator tooling, not the deferred browser port of Tetra Master.
