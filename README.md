# tmserver

[![Checks](https://github.com/sruon/tmserver/actions/workflows/checks.yml/badge.svg)](https://github.com/sruon/tmserver/actions/workflows/checks.yml)

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
- [tmloader](https://github.com/sruon/tmloader), which starts the client and
  points it at this server.
- Python 3.13 or newer, on Windows or Linux.
- PostgreSQL 16 or newer, which holds the save (see
  [PostgreSQL storage](docs/DATABASE.md)).

## Get started

1. Install the server:

   ```powershell
   python -m venv .venv
   .venv/Scripts/python -m pip install -e ".[postgres,admin,auth]"
   ```

   On Linux the scripts are in `.venv/bin`.

2. Create a database and bring its schema up to date, as described in
   [PostgreSQL storage](docs/DATABASE.md):

   ```powershell
   $env:TM_DATABASE_URL = 'postgresql+psycopg://USER:PASSWORD@127.0.0.1:5432/tetramaster'
   .venv/Scripts/tmserver-db upgrade
   ```

3. Start the server, then point [tmloader](https://github.com/sruon/tmloader) at
   its game and profile ports. One to three game windows can connect to the same
   server.

   ```powershell
   .venv/Scripts/tmserver --log-dir var
   ```

Logs go to `var/` in the working directory. Run `tmserver --help` for rooms,
events, human tables, prizes and ratings.

## Features and options

- [Rooms](docs/ROOMS.md): Local Room, Open Tables, Classic, Special and Quick Play.
  The Computer menu offers one or two of the 30 computer opponents.
- [Weekly rankings](docs/WEEKLY_RANKINGS.md), off by default.
- [Offline administration](docs/ADMIN.md) with `tmserver-admin`: inspect saves,
  queue prizes and correct event progress.
- The optional [admin panel](docs/ADMIN_PANEL.md), a browser view of the running
  server. It is read-only unless started with `--admin-actions`.
- [Discord sign-in](docs/AUTHENTICATION.md) through Supabase, with automatic
  accounts and WebSocket game streams for tmloader. The
  [loader contract](docs/LOADER_AUTH_SPEC.md) describes the interface.

Listeners bind to loopback. Hosting the server for others needs a TLS reverse
proxy, and public hosting has not been done yet.

The protocol is documented in [TetraPackets](https://github.com/sruon/TetraPackets).
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
