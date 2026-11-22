# Roadmap

## Where things stand

- Battles, growth, target selection and computer moves on ordinary, special and
  three-player boards are ported from the client to Python and checked against
  8,067 results frozen from its own code. Board effects and generation, match
  order, departures, policy, storage and transport are Python too.
- Players register a username and password with tmloader's LOGIN on the game port;
  see the [loader login contract](LOADER_AUTH_SPEC.md). The browser client that
  used the removed WebSocket gateway needs a plain WebSocket-to-TCP bridge.
- The optional [admin panel](ADMIN_PANEL.md) is read-only by default. With
  `--admin-actions` it can award Prize Center items and grant event cards, money,
  eligibility and progress, each with an audit receipt.
- Package and test checks run in CI; see [checks and releases](CI.md) and
  [validation](VALIDATION.md).

## Open work

### Native acceptance

Follow the [native test checklist](NATIVE_TEST_CHECKLIST.md) for one recorded
server and loader build: computer match, rematch, shop, deck change and reload;
then a two-player match, trade and reload. Record three-player, event, auction,
Prize Center and spectator checks separately. Headless tests do not count.

### Public hosting

`--bind` exposes the game and profile ports (see the README's "Running a public
server"). Still open: a deployment, server authentication for LOGIN (TLS or a
pinned key), an audit trail for password and disable changes, shared rate
limits across restarts, and a pre-login connection limit sized for the public.

### Guild choice and guild-aware computer decks

`economy/service.py:provision` gives every new account guild 1, and
`accounts/provisioning.py` keeps that. `initialize_profile` already lets an
unregistered guild-0 account pick guild 1..3. `game/match.py:_prepare_npcs`
calls `prepare_opponents` without a guild, so computer decks use the
unrestricted filter; with CRT seed 1, opponent 30 can draw model 83, which
guild 1 excludes.

Connect first login to the client's guild selection and pass the account's guild
into computer pool generation. Decide when the starting ten cards are generated:
generating them before the guild is chosen leaves a mismatched starter inventory.
Existing accounts keep their guild and cards.

### Generated ordinary boards and first player

`game/match.py:_select` generates a board only when `st`, `cb` or `ca` is set;
otherwise it uses an empty board with seat 0 first, so classic rooms always start
the first seat. `game/boards.py` already has the client's generator, including
walls and first-player choice. Use it for normal play with all special flags off,
keeping fixed boards for fixtures and practice. Do not change the draw order.

### Computer match stakes and forfeits

Wagers and card transfers are opt-in. `departure_supported` allows continuing
only unrated practice without stakes; other Quit/Break paths end the match, and
`MatchEconomy.abort` refunds escrow and recovers earned cards. There is no
economic forfeit for a deliberate quit. Define a computer-match preset on the
existing prize and settlement code, and treat a deliberate quit separately from
a dropped connection, timeout or storage failure. Check whether wager fields
must equal the chosen opponents' listed amounts; `_com_game` checks only the
range and policy today.

### Champion deck in the shop

`ShopOffers` can show and name the special pack, but `economy/shop.py` still
fills it from the static catalogue. How the champion and deck are chosen, when
the offer is fixed, and which card values carry over are unknown.

### Ranking and Prize Center awards

Rankings and Prize Center spending work. Weekly rankings are a server choice;
Best Rookies is empty; awards come only from administration. Qualification,
amounts, periods and rookie rules are unknown. If this server picks its own,
label them as such and keep award receipts idempotent. See
[weekly rankings](WEEKLY_RANKINGS.md).

### Zones, rooms and tables

Configured rooms appear under Local Lobby, one table each in the example; more
tables are supported. The original language zones and room presets are not
recreated; community sources describe sixteen tables per room. Keep computer
availability and fixed rules consistent with the client's menu (see
[rooms](ROOMS.md)).

### Rating history migration

Switching rating modes can leave saved history behind an account's profile; the
server now refuses to start in that case. Decide whether operators supply the
missing history or start a new epoch, then add a preview and an idempotent apply.
A single rating cannot rebuild the 32-result ring.

### Spectator synchronization

Spectating stays opt-in. The client's watch dialog has no known dismissal
acknowledgement, so delivery does not prove the client showed an update.

### Open questions about the original game

- A community description says the winner chooses a stat increase; this server
  applies the client's automatic growth. No separate choice flow has been found.
- The same source says card types cannot change, but the client's growth routine
  promotes types and this server calls it.
- Double Up is described differently from the wager doubling in `_continue`.
  Keep the current behavior until the client shows otherwise.
- Original reward and rating formulas, rookie rules, event schedules, auction fees
  and resume after a disconnect are unknown. Do not present server choices as
  original behavior.
