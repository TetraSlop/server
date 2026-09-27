# Tables that start with two or three humans

Use `python -B server/run_server.py --mode versus --players auto` from the
Tetra Master scratch directory. This enables three local accounts and a table
that admits up to three humans. The owner's Start action freezes the seated
participants: two use the16-cell board and ten placements; three use the25-cell
board and fifteen placements. Rule-selected special boards use that same size.

The original current-roster Start gate enables at two ready humans, and the
table advertisement has no separate fixed-size field. Previously the server's
three-seat configuration waited for all three even after the native Start
button became available. Automatic sizing provides the compatible alternative.
Existing `--players 2` and `--players 3` retain their exact-count meaning.
Explicit `--board` layouts require an exact count and reject automatic sizing.

Late admission is refused after Start without disconnecting the newcomer or
reserving their account. CheckJoinTable reports no available participant seat.
The frozen roster and board dimensions last through rematches. Leaving/resetting
the table restores its three-seat capacity. Direct Req/GameOK test clients
without GameReady freeze at their first acceptance after every seated human
has requested; normal native startup freezes earlier, at GameReady.

Start invitations also wait for each guest's post-admission readiness. The
original GameEA handler deletes queued invitations, so sending Start while a
newcomer is still joining can strand that player. The server now delivers the
newcomer's roster followed by the held invitation after their ready PD. This
applies to fixed-count tables as well. A completed direct Req or a canceled
table discards an obsolete invitation; duplicate ready messages do not resend it.

Wagers, prize choices, optional offline rating history and saved human-match
counts use the actual frozen count. There is no change to event matchmaking,
which uses its configured participant count. Automatic size is a local admission
policy supported by the recovered client gates, not a historical server capture.

Additional room/table JSON can use `"players": "auto"` as well. Enable at least
three accounts with `--accounts 3`; `auto_rooms.example.json` is a ready example.
Configuration and account selection remain explicit startup choices.

`test_flexible_seats.py` exercises owner authority, both sizes, late admission,
reset capacity, fixed-count compatibility, generated boards, direct request
fallback, paid/prize/rated rematches and persisted reloads. Five original-code
cases in `../protocol_map/auto_table_vectors.json` execute the Start gate and
the generated initialization/selection/board/turn packets for every seat.
Native graphics are not run by these checks.

Five further cases in `../protocol_map/start_invitation_vectors.json` reproduce
the dropped early invitation, then execute the original admission completion
and full controller tick with the current server's deferred roster/invitation.
All four fixed/automatic two/three-player combinations accept the invitation
and retain the complete roster. Encrypted regressions separately cover the
three-player admission race through card selection.
