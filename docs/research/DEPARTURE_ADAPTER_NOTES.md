# Opt-in active match departures

The standalone runner's --departures option enables recovered practice rules:
gs0 replaces a missing seat with original NPC1 control over its retained hand;
gs1 removes the seat and ends without a result when only one participant remains;
gs2 removes the seat and assigns its recovered losing result at a legal boundary.
Two- and three-player rules use the original ranking/skip formulas.

Queued cleanup runs after match publication releases its lock. A send failure
inside publication therefore cannot deadlock by recursively closing a session.
PART, EOF and an explicit Quit cannot apply the same departure twice. The account
reservation stays with the table until result/abort completion; reconnect cannot
claim that account while its replacement is still playing. Automatic collection
recovery handles departed recipients and watchdog barriers include survivors only.

A connected voluntary gs2 quitter must still receive terminal ResultData:
original1010F63E waits in childstate8 before state9 consumes its queued Quit reply.
It receives no participant turn/result acknowledgement obligations. A dead socket
gets no such extra delivery. This prevents the native leave scene waiting forever.

Shutdown, storage failure and application timeout abort rather than substitute.
Original selected cards remain authoritative for durable profile updates; partial
presentation hands do not imply permanent card deletion. Wagers, card prizes and
optional ratings still use the established abort/refund path when somebody leaves:
their partial-result ownership/economic policy is not proved.

Fifteen encrypted adapter tests cover all rules, two/three players, substitution,
forfeit/invalid results, live voluntary Quit, PART replay, failed sends under the
match lock, deadlines, account retention/reconnect and shutdown with a live
survivor. See NATIVE_DEPARTURE_NOTES.md for original-code evidence and limits.
