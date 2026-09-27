# Original mode8 event entry

Ans2 in99/EInitEnt selects globalmode8. Original100FC6C0 handles event03001A43
by setting parent stage142=5/state143=4, bypassing B2/EInit and owned-card
selection. This succeeds with zero owned cards. Sending EInit anyway leaves it
unconsumed. The reservation action100742CA is disabled in mode8. The table
Observe action still constructs100B9A00 with arguments4, table identifiers,2,0.
This is an observer-room path, not a recovered computer-opponent mode.

An event with match_enabled=true and entry_answer=2 now advertises the E service
and admits through the original99/Ans2 response without a playable table or
inventory reservation. Mode4/answer1 continues to require exactly one playable
table. Rules, membership, time and F-card routes retain their existing behavior.

Twenty-four original-code cases cover admission, reservation gates, mode
preservation and observer constructor arguments. Encrypted tests cover repeat
entry with zero cards, no selection/match packets, unchanged owned profile,
restart and disconnect cleanup. The event spectator adapter now streams live
configured playable tables to authenticated room observers; see
EVENT_SPECTATOR_NOTES.md. A standalone mode8 room still has no configured match
feed: historical tournament broadcast routing is unproved, and this entry
implementation alone does not establish that routing.
