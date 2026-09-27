# Items for review when the user returns

The user asked for autonomous progress toward full emulation on September 26,
2026. Questions are recorded here rather than interrupting that work. No desktop
control or game launches are authorized by this note.

Use [local play](../LOCAL_PLAY.md) and the current
[native checklist](../NATIVE_TEST_CHECKLIST.md) for the packaged projects.
The examples below are relative to the `tmserver` checkout. Research-era
scratch launchers are not the packaged entry points.

## Native checks requiring the user

- Full 16-byte character names need a loader/native bounds review. Original
  DE/PD serialization scans beyond that fixed name field into following
  metadata, and the room receiver copies 16 bytes without a terminator while
  reporting -8404. Synthetic server-wire admission and bounded table/event
  labels work, but do not establish native sender or rendered safety for these
  names. Preserve canonical identity; do not trim guessed metadata suffixes.
  See [original source evidence](ROOM_NAME_PROVENANCE.md). No native change was
  made; ordinary names up to 15 bytes pass the original sender/receiver controls.
- Custom fixed CPU rooms: the hosted Computer menu always sends departure
  rule gs0, so room configuration now requires gs0 whenever a fixed room has
  a CPU table. Unlike human fixed rooms, Computer's rule editor stays editable
  and clears double-up when opened; a conflicting edit is still rejected by
  the server. Confirm presentation before adopting these custom rooms broadly.
  The supplied CPU room remains custom, and supplied fixed rooms are human-only.
  See COMPUTER_ROOM_RULES.md for original instruction evidence.
- After importing a copy of the save into PostgreSQL, run
  `.venv/Scripts/tmserver` with `TM_DATABASE_URL` set, following
  [persistence setup](../PERSISTENCE_DESIGN.md). This starts only the services;
  the native client must be connected separately for these checks. `Play.cmd`
  selects JSON explicitly and does not use that PostgreSQL environment variable.
  Check shop Buy/Get, sale, deck rename, a two-account trade, and inventory/balance
  after relaunch. Encrypted PostgreSQL protocol tests pass; graphical behavior
  remains untested.
- Check Play Data after two human matches: VS. Player Games should advance
  once per completed round and survive login; CPU matches should preserve it.
  Original receiver/importer/display checks and encrypted two/three-player
  rematches pass. VS. Rating components remain zero until a hosted formula
  is established; Memorable Win is preserved pending its selection policy.
- Check `--mode versus --players auto`: Start with two humans, then leave and
  start a new table with three. The board should change from16 to25cells; a
  late join during the start handshake should fail without losing connection.
  Also try Start just as a third guest joins: the guest should receive the
  invitation after admission completes, in both auto and fixed-three tables.
- Play another CPU match and a rematch after the original battle/growth/AI
  integration. The earlier successful native match predates those changes.
- Verify the result collection survives exiting/reopening the game. Encrypted
  socket tests cover persistence; actual scene parity needs user observation.
- Exercise purchase/sale and a two-account trade in the native UI.
- Exercise auction exhibit/bid, returned/won-card collection and Prize Center.
- Check explicit special-board CPU/versus rounds, including shuffle and spinners.
  Menu toggles now use the recovered original board generator for2/3players.
- Check configured event room entry, matchmaking, borrowed hands, event result
  and return to the room. `--events examples/events.example.json` selects a local fixture;
  encrypted2/3-player tests pass, but the actual presentation remains unobserved.
- Check the original Computer menu:30 asset-backed opponents, reconfiguration,
  rematches and leaving. Also check a three-account human table and its25-cell board.
- Check the newly supported one-human/two-NPC Computer menu: choose two distinct
  opponents, complete its25-cell/15-placement game and rematch, then reconfigure
  to one opponent and back. Check prizes/wagers when enabled and a spectator
  joining mid-game. Frozen original sender vectors, original observer receive
  instructions and encrypted full-game/recovery checks pass; rendered scenes
  remain unobserved.
- Check opt-in ordinary card prizes, overflow discards and delayed deck editing;
  then wagers/double-up and the displayed balance after result and rematch.
- Check explicit `--ratings local-history` D2 display; lower ratings are better
  in the recovered offline arithmetic. RN is the Memorable Win display value;
  its historical selection rule remains unknown, so its saved value is preserved.
  Disabling this policy while playing advances the profile sequence without its
  history; re-enabling then requires explicit history migration, not a silent reset.
- Check configured special shop pack labels and FFX pack purchases. Names are
  bounded to7 bytes because the original longer-name path truncates unusually.
- Saved table/game defaults now persist. Passwords selected for durable saving
  currently require at most9 bytes: the original importer copies10 bytes without
  clearing the tail. Current table advertisements have a separate8-byte field.
- Current table passwords now work within that8-byte field. Verify the native
  correct/incorrect password dialogs; original execution proves the local comparison.
- Spectator preview is opt-in: the original bootstrap waits for a user-dismissed
  dialog without notifying the server. Configurable delays cannot prove that
  the dialog has closed; live rendering/pacing needs a user check.
- Check the standalone tmloader refactor and short-click buffering when useful.
- Check two/three-player event spectators and late ordinary observers after a
  practice departure. Mode8 room entry works with zero owned cards; a separate
  historical tournament broadcast feed remains unproved.
- Check three-player CBC rewards, inventory-full discard and Returned Card
  recovery. Encrypted rounds and original export routines pass headlessly.
- Check opt-in practice Quit/Break, CPU replacement, invalid/loss rules and
  return-to-room presentation. Economic/rated departures still abort/refund.
- Check additional ordinary rooms through `--rooms examples/rooms.example.json`: enter
  each room, select its table, return to the directory and change rooms.
  Simultaneous encrypted rounds and original directory/PTL consumers pass.
- Check two tables in one room with `--rooms examples/shared_rooms.example.json`:
  table numbers, shared roster, returning from one table and entering another.
- Check the result screen's ranking privacy choice and its value after login.
  The original choice/send/import and ranking-name projection pass headlessly.
- Check multiple table proposals within the same event room, including one
  group leaving while another continues. Groups use distinct TblNo/TblId values.
- Check ordinary-to-event and event-to-event room changes: the old roster
  should lose the player, while saved event progress remains. Full destinations
  preserve the current membership. --accounts 5 also permits an observer with
  two simultaneous two-human event games; --list-accounts prints the login IDs.

- Check native pre-start host removal, voluntary GameQT/GameExit, ownership
  transfer and returning to the room. Original dialog/roster/host consumers
  and encrypted replacement rounds pass; QT2 kick success and oldest-seat
  ownership transfer are compatible local choices, not historical captures.
- Check explicit room PC departure. It removes only the authenticated roster
  entry; PART of the room channel alone does not remove it when starting play.
- Check two/three-human table entry after the post-GameEA roster fix: names
  should update as each guest joins and Start should enable after their ready
  status arrives. Also inspect another table's members without joining it.
- Check a full/busy-table join rejection and retry after a seat becomes free.
  Full lobbies now use the original table-full code -32870 for GameEA/PLAYACK.
  Unavailable GameEA/ECA use -32871; unavailable PLAYACK uses -32869. Original
  label selection is verified in ADMISSION_ERRORS.md; native dialogs remain
  a user check.
- Check the owner Observe settings: Impossible should block observation;
  No Comments Allowed should admit a watcher with no chat panel. Both native
  watch-setup predicates execute correctly with production WatchInfo bytes.
  Configured event tables now advertise Possible when spectators are enabled.
- Watch a match launched from the original Computer menu. Long NPC names
  should display through the client's own catalogue, including unknown-name
  thresholds based on the viewer's card level. The89 original receiver cases
  and encrypted NPC spectator round cover this without native rendering.

## Policy that the original client cannot establish alone

An optional policy choice remains whether to expose the original offline
VS. Rating generator. It uses random deltas and can treat tied seats differently;
it is separate from the current average-rating ring. Preserve VS. Rating by
default unless a separate local policy is deliberately selected. The historical
hosted formula remains unknown; see [source evidence](VS_RATING_SOURCES.md).

The captured native room/table/chat/selection replay now covers independent
HID0 and character1. Recheck current Computer/event/observer scenes when at the
desktop; their full native admission fields are now accepted and authenticated.

- Historical hosted RNG seeds, ranked rating formulas, reward schedules,
  auction fees and event scheduling. The30 NPC identities, setup records and
  original deck-generation routines are now recovered from installed assets.
- Event `wins`/`rounds` progression and retry delays
  are explicit local policy options. Default event progression is disabled;
  automatic mission completion and historical rewards are not inferred.
- Default practice preserves money and ownership. Opt-in card transfers use
  recovered original winner-choice rules. Opt-in versus payouts conserve a
  shared stake pot; CPU payouts reuse the original offline formula. These are
  explicit replacement-server policies, not proof of historical online payouts.
- Reconnect currently aborts an incomplete round and reloads committed state.
  There is no proved participant resume handshake in the recovered protocol.
- Exact two/three-seat settings remain available. The new `--players auto`
  option freezes two or three current humans at Start, matching the native
  minimum-two gate, and generates the matching board. Historical allocation
  policy is still unknown; see AUTO_TABLES.md for the explicit local choice.
- Human desktop validation and historical-server parity must not be counted as
  complete merely because a packet schema or isolated consumer test passes.
- CBC overflow recovery uses the existing Returned Card
  collection UI for earned cards left unaccepted by a disconnected match. This
  is an explicit local recovery policy, with no auction fees or invented resume
  handshake. Cards and replay IDs must remain durable until accepted.

## Compatibility scope

Target: original PC Tetra Master 1.6.11.0 and its POL game/profile services.
The browser port remains paused. Original installed game files remain unchanged;
authorized server and loader development lives in the `tmserver` and `tmloader`
GitHub checkouts. Changes are local and have not been pushed.
