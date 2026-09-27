# Additional ordinary rooms

Pass --rooms rooms.example.json to run the default room plus additional
ordinary rooms. Each configured room has its own roster and replay sequence.
Each table has its own match engine, lock, timers, departure queue and observer writers.
The example adds a two-human room and a computer room. Use --accounts 4
to run two two-human matches simultaneously.

The configuration contains a rooms array. Each row requires name, room_raw,
table_raw, room_channel and table_channel; mode defaults to versus and
players defaults to 2. Computer mode needs two seats; versus accepts 2 or 3.
An optional tables array adds more tables in that same room. Each entry needs
table_raw and table_channel, with optional mode and players. The primary table
is number1; additional tables are numbered2 onward. The room supports127
playable tables plus its trade service, matching the original128-record cap.
shared_rooms.example.json demonstrates two human tables in one room.
Raw identities must fit the original 41-bit POL namespace. Names and
channels must fit the original binary fields. All configured routes are
checked against accounts, default services, event services and other rooms.
Names in Local Lobby must also remain distinct.

Additional tables share the authenticated accounts, economy, results,
auction ledger, rating policy and one atomic state save. Wager/card-prize,
rating, CBC and departure opt-ins remain host policy. A player cannot claim
another ordinary table while retaining a seat or observer admission.
A successful authenticated idle-room DE removes the player's previous roster
entry across ordinary and event rooms, and clears transient event admission.
Durable event progress remains. Active players and observers must leave their
current table (or E selection) before changing rooms. A full or invalid
destination preserves the old membership.

Directory ZL/RL files include configured rooms, live member counts and
occupied-table counts and the actual configured table capacity. PTL reads route by the original FileOwner identity,
and room deltas retain each room's source and sequence. Shared-room tables
broadcast through one contiguous sequence to the same roster. Each CPU table
uses a distinct NPC nickname and clears only its own NPC entry. Default CLI board
overrides currently apply to the default room; additional rooms use their
native menu-driven board settings.

The original directory projection and PTL importer execute successfully on
generated multiroom data. Encrypted tests run two simultaneous human rounds,
two simultaneous CPU rounds, account persistence, room-specific PTL/replay,
cross-table rejection, isolated abort/refund, PART, timeout, failed sends,
and idle room migration. Native scene selection remains a deferred user check.

The tables are prepublished in PTL, as required by the recovered client entry
path. The original client reveals no separate table-creation command; historical
dynamic allocation remains unknown. Production accounts and historical regional
capacity policy are not supplied by this option.
