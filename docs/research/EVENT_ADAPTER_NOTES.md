# Configured original event games

`run_server.py --events events.example.json` publishes a local event room,
its original E/F service records, and a separate playable table. This runs
the two protocol listeners without launching or controlling the client.
The example is an explicit replacement-server fixture, not a historical event.

The route is authenticated room DE/time, EInitReq98, EInitEnt99 and B2/13,
native B2/07 five-card selection, Select and EQuit, room DD table publication,
D2 proposal, D3 consent, D2 start, table GameEN,
IRC JOIN, Req/PLAYACK, GameOK,41/09 and EventGameInit. Each response uses its
actual configured room/init/table identity. Mode8 is rejected as a playable
matchmaking configuration; the recovered original playable route uses mode4.
An early proposal rejection retries with a bounded delay because the original
presentation child emits no network readiness signal. This delay is local
server scheduling, not a claimed retail timer.

Every player receives only its own five borrowed EventCard records. Normal
StartData, TurnData, PutData and battle packets then drive a complete two- or
three-player game. EventResult replaces ordinary result/growth/prize packets.
The first valid Ready emits Continue header2/Ans3; the client's duplicate
Ready uses completed turn counter10/15 and remains harmless after release.
A fresh E entry is needed for a new game. Preselection Quit cancels cleanly;
authenticated Decks edits persist, including the native delayed editor.

The default borrowed hand uses the five authenticated owned cards selected in
the E-session scene. Original code returns them locally before EQuit; the
server does not delete or return them a second time. Battle mutations affect
only the borrowed instances. No cards, money,
ordinary match statistics or ordinary rating history are modified. A host can
provide a separate trusted hand provider; an optional explicit five-card CPU
fixture is synthetic and does not identify mode8 as a CPU menu.

`event_results.py` journals authenticated outcome facts in the same durable
snapshot as event membership before EventResult is released. `progress_policy`
defaults to `none`; explicit `wins` or `rounds` increments local event steps.
It does not fabricate mission completion, money, cards or historical rewards.
Failed state writes withhold acceptance and close connections; restart imports
only the previous complete file. Temporary reservations do not survive restart.
Active disconnect/timeouts abort and release the affected event, while completed
outcomes remain durable. This is a documented local recovery policy.

Configuration uses raw41-bit service IDs and explicit table channels. Event
table IDs cannot collide with accounts or existing event/standard routes.
Three-human event tables require `--accounts 3`; ordinary table mode can remain
independently configured. The example enables two human seats. Events have
their own queue and ordered delivery worker so a slow event does not block the
ordinary table. All original/UI parity checks remain in DEFERRED_REVIEW.md.

Validation: `test_event_matches_integration.py` exercises real RSA/Blowfish
connections, source/order/private hands, complete2/3-human rounds, duplicate
Ready, joint restart state, active disconnection and a failed final save.
`test_event_results.py` covers draw/tied winners, explicit progression and CPU
facts, replay and corrupt-state rejection. Original packet/scene evidence is
in EVENT_MATCH_NOTES.md and EVENT_MATCHMAKING_NOTES.md.
