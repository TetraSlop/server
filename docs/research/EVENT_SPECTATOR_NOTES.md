# Ordinary and event observer preview

The opt-in --spectator-preview now handles two- and three-player ordinary
tables and configured playable event tables. --accounts 4 permits three humans
and an observer; the configurable2..128 account pool also supports observers
alongside multiple simultaneous tables.

Event admission binds the authenticated identity, event-room membership and
table channel before Req/PLAYACK/Watch. Observers cannot claim participant
seats, send moves or release participant acknowledgement barriers. A distinct
writer queue carries each observer's gameplay stream outside the event lock;
failed/slow observers do not hold that lock or stop participant gameplay.
Participant abort and table replacement retire the associated observers.
Event room roster/control output still uses the shared event sender.

ExitCh, PART and disconnect retire the observer's sender as well as its queued
packets and admission. Rewatching the same active event gets a new sender owner;
late completion, failure or final cleanup from the old writer cannot acknowledge
the new bootstrap, remove its task, or close its connection. Retired writers
remain tracked until completion so shutdown can cancel and await them. Encrypted
re-entry tests block an old gameplay write across ExitCh/PART and exercise both
normal cancellation and late completion/failure. This fixes transport lifecycle
ordering; it does not establish a native observer presentation timing policy.

Public Start/Turn/Put/Battle/GetAway/Change/Result packets are streamed in order.
Private EventCard, selected hands and owned-card TCList are excluded. Original
role4 EventResult receive tests preserve personal profile state and send no ACK.
Encrypted tests complete both board sizes, verify unchanged observer profiles,
reject forged actions/admission, and isolate slow/failed observer writers.

Ordinary late snapshots now carry Q=1 for departed seats and C for the actual
replacement NPC. Original WatchInfo converts Q=1 into status2, updates its
departure counter and invokes the original NPC setup for C. G explicitly
preserves the original number of seats. Live sequence validation follows the
original inactive-seat skipping rule instead of requiring every turn number
to differ by one. CBC-generated cards may exceed the selected-hand count;
the actual native message-size limit still applies to dense WatchCard snapshots.

The original WatchInfo parser assigns departure rank presets in seat order;
it does not transmit arbitrary departure chronology. This is a client
bootstrap limitation, not evidence of a recoverable historical policy.

These are parser and encrypted-transport checks. The original bootstrap waits
for a user-dismissed dialog without sending a presentation-complete ACK.
Configured pacing remains a preview heuristic, so native rendering is still
a deferred user check. Standalone mode8 rooms currently have no match feed;
no historical broadcast routing is invented.
