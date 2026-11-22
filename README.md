# tmserver

[![Checks](https://github.com/TetraSlop/server/actions/workflows/checks.yml/badge.svg)](https://github.com/TetraSlop/server/actions/workflows/checks.yml)

Tetra Master is the card game from Final Fantasy IX, which Square Enix also ran
as an online game on PlayOnline. Its online service has closed. tmserver is a
Python server that replaces the PlayOnline game and profile services the PC
client talked to, so the original client can play again: computer and human
matches, two- and three-player boards, the shop, trading, auctions, the Prize
Center, rankings, events and spectators.

This is an unofficial fan project. It is not affiliated with or endorsed by
Square Enix.

## What you need

- Your own copy of the game. tmserver ships none of the game's files and reads
  none of them: the client's battle, growth and computer-player rules are ported
  to Python, and the card catalogue it uses is in
  `src/tmserver/data/economy_catalog.json`.
- [tmloader](https://github.com/TetraSlop/loader), which starts the client and
  points it at this server.
- [Docker](https://docs.docker.com/get-docker/) with Compose. The server runs
  from the `ghcr.io/tetraslop/server` image, with PostgreSQL 17 beside it.

## Get started

From a checkout of this repository:

```sh
docker compose up --detach
```

This starts PostgreSQL 17, keeping the save in the `db` volume, and the server.
On a checkout, Compose builds the image from source; `docker compose pull`
fetches the released image instead. The server brings the database schema up
to date each time it starts, then listens on the game port 51241 and the
profile port 51220 of 127.0.0.1. Point [tmloader](https://github.com/TetraSlop/loader)
at that address. Each player signs in with a username and password; the first
login creates the account (see [Accounts](#accounts)). Any number of players can
connect, and a table seats two or three.

Set `POSTGRES_PASSWORD` in a `.env` file next to `compose.yaml` before the first
start. The database is reachable only from the Compose network, and the password
only takes effect when the `db` volume is created.

Server options go in the server service's `command`, for example
`command: ["--mode", "versus", "--players", "auto"]` in a `compose.override.yaml`.
Run `docker compose run --rm --no-deps --entrypoint tmserver server --help` for
rooms, events, human tables, prizes and ratings. `docker compose logs server`
shows the server's output; its traffic logs are in `/var/lib/tmserver/var` in
the container. Stop the server with `docker compose stop server` before you run
an offline tool, such as `docker compose run --rm --entrypoint tmserver-admin server --help`.

### Run from source

For development, use Python 3.13 or newer on Windows or Linux and a PostgreSQL 16
or newer database (see [PostgreSQL storage](docs/DATABASE.md)):

```sh
python -m venv .venv
.venv/bin/python -m pip install -e ".[postgres,admin]"   # .venv/Scripts on Windows
export TM_DATABASE_URL='postgresql+psycopg://USER:PASSWORD@127.0.0.1:5432/tetramaster'
.venv/bin/tmserver-db upgrade
.venv/bin/tmserver --log-dir var
```

## Accounts

Players create their own accounts. tmloader sends a username and password to
the game port once before the game starts. An unknown username becomes a new
account with that password and a POL ID the server assigns; later logins must
match the password. The server stores only a salted scrypt hash. The [loader login contract](docs/LOADER_AUTH_SPEC.md)
describes the exchange, the limits and the error codes. Operators can set a
password, or disable and re-enable an account, with
[`tmserver-admin`](docs/ADMIN.md#accounts-and-passwords).

The built-in test accounts that `tmserver --list-accounts` prints share the
password `tm-local-test`. They are accepted from this machine only.

## Running a public server

Copy `compose.yaml` from this repository to the server. Next to it, create a
`.env` file that publishes both ports on every address:

```sh
POSTGRES_PASSWORD=a-long-random-password
TM_PUBLISH_ADDRESS=0.0.0.0
```

Then start the released image and open both TCP ports (51241 and 51220) in the
firewall:

```sh
docker compose pull
docker compose up --detach
```

To upgrade, run the same two commands again. Compose replaces the server
container, and the new one upgrades the schema before it listens.
Players then point tmloader at the server's address. Before you do this:

- Login is encrypted against eavesdroppers but the server is not authenticated,
  so someone who can intercept traffic can read passwords. Tell players not to
  reuse a password.
- Failed-login and registration limits are per process and reset on restart.
  They count by source address; Docker Engine on Linux keeps the players'
  addresses, while Docker Desktop shows every player as the same address.
- The built-in test accounts sign in from loopback only, which excludes
  connections through Docker's published ports.
- Keep the admin panel on loopback. In the container it can listen on all
  addresses only when you publish its port on the host's loopback; see the
  [admin panel](docs/ADMIN_PANEL.md#in-the-container).
- Back up the PostgreSQL database; it holds every account and password hash,
  for example with `docker compose exec db pg_dump -U tetramaster tetramaster`.

## Features and options

- [Rooms](docs/ROOMS.md): Local Room, Open Tables, Classic, Special and Quick Play.
  The Computer menu offers one or two of the 30 computer opponents.
- [Weekly rankings](docs/WEEKLY_RANKINGS.md), off by default.
- [Offline administration](docs/ADMIN.md) with `tmserver-admin`: inspect saves,
  queue prizes and correct event progress.
- The optional [admin panel](docs/ADMIN_PANEL.md), a browser view of the running
  server. It is read-only unless started with `--admin-actions`.
- Self-registered accounts over the game port; see [Accounts](#accounts).

The protocol is documented in [TetraPackets](https://github.com/TetraSlop/packets).
Where the client does not reveal what the original server did, such as reward and
rating formulas, this server makes its own choice; [rules provenance](docs/RULES_PROVENANCE.md)
lists which is which. What is still open is in the [roadmap](docs/ROADMAP.md).

## Development

- [Architecture](docs/ARCHITECTURE.md): where the code for each feature lives.
- [Checks and releases](docs/CI.md): test lanes, CI and the release workflow.
- [Validation](docs/VALIDATION.md): what the tests cover and what they do not.

## Licence

Copyright (C) 2026 sruon

tmserver is licensed under the [GNU General Public License v3.0](LICENSE).

The licence covers this repository's code and documentation only. It does not
cover the Tetra Master game client, its DLLs or its data, which belong to their
owners. None of the game's files are distributed here; the card catalogue in
`src/tmserver/data/economy_catalog.json` is decoded from the client's data and is
not covered by the licence either.
