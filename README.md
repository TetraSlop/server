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
- Python 3.13 or newer, on Windows or Linux.
- PostgreSQL 16 or newer, which holds the save (see
  [PostgreSQL storage](docs/DATABASE.md)).

## Get started

1. Install the server:

   ```powershell
   python -m venv .venv
   .venv/Scripts/python -m pip install -e ".[postgres,admin]"
   ```

   On Linux the scripts are in `.venv/bin`.

2. Create a database and bring its schema up to date, as described in
   [PostgreSQL storage](docs/DATABASE.md):

   ```powershell
   $env:TM_DATABASE_URL = 'postgresql+psycopg://USER:PASSWORD@127.0.0.1:5432/tetramaster'
   .venv/Scripts/tmserver-db upgrade
   ```

3. Start the server, then point [tmloader](https://github.com/TetraSlop/loader) at
   its address. Each player signs in with a username and password; the first
   login creates the account (see [Accounts](#accounts)). Any number of players
   can connect, and a table seats two or three.

   ```powershell
   .venv/Scripts/tmserver --log-dir var
   ```

Logs go to `var/` in the working directory. Run `tmserver --help` for rooms,
events, human tables, prizes and ratings.

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

The game and profile listeners bind to 127.0.0.1 unless you say otherwise. To
let other people connect, bind them to a public IPv4 address and open both TCP
ports (51241 and 51220 by default) in the firewall:

```powershell
.venv/Scripts/tmserver --bind 0.0.0.0 --log-dir var
```

Players then point tmloader at the server's address. Before you do this:

- Login is encrypted against eavesdroppers but the server is not authenticated,
  so someone who can intercept traffic can read passwords. Tell players not to
  reuse a password.
- Failed-login and registration limits are per process and reset on restart.
- Keep the admin panel on loopback; it never binds anywhere else.
- Back up the PostgreSQL database; it holds every account and password hash.

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
