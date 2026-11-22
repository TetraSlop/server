# Documentation

Running the server:

- [Native rules](NATIVE_RULES.md): build the required Win32 bridge and supply the
  game image.
- [Rooms](ROOMS.md) and [weekly rankings](WEEKLY_RANKINGS.md).
- [Offline administration](ADMIN.md) and the optional [admin panel](ADMIN_PANEL.md).
- [Authentication](AUTHENTICATION.md) and the [loader contract](LOADER_AUTH_SPEC.md):
  Discord sign-in and the WebSocket game and profile streams.
- [Database setup](DATABASE.md): the PostgreSQL save the server requires, and the
  [persistence design](PERSISTENCE_DESIGN.md).

Working on it:

- [Architecture](ARCHITECTURE.md): where each feature lives, ownership and commit order.
- [Checks and releases](CI.md) and [validation](VALIDATION.md).
- [Native test checklist](NATIVE_TEST_CHECKLIST.md): checks that need a person at
  the game client.
- [Rules provenance](RULES_PROVENANCE.md) and [coverage](EMULATION_COVERAGE.md):
  which behavior comes from the client and which is this server's choice.
- [Roadmap](ROADMAP.md): open work.

The protocol is documented in [TetraPackets](https://github.com/sruon/TetraPackets).
