# Native validation of the integrated projects

Status: **awaiting user testing**. The loader has not been launched by the agent.
Headless checks validate startup plumbing and encrypted requests; the following
checks need the actual game screens. Use the new `Play.cmd`, not the old shortcut.

## Pass 1: one window

1. **Startup:** double-click `Play.cmd`. Confirm the title is visible, mouse
   movement/clicks behave normally and room entry succeeds.
2. **CPU:** enter Computer, choose an opponent, finish a match, then play a
   rematch. Confirm selection, turns, battles and the result screen progress.
3. **Shop:** note money and card count, buy a pack and accept its cards. Sell one
   unwanted card and change a deck name or assignment. Confirm visible changes.
4. **Reload:** close the game normally, run `Play.cmd` again and check that the
   money, inventory, deck change and completed-match progress survived.

Report the first failing step before continuing. A useful short report is:
`session ID; step; last visible screen/message; expected behavior; what happened`.
The session ID appears in the launch console; log paths are in LOCAL_PLAY.md.

## Pass 2: two windows

Close the first session, then run `.\Play.cmd --clients 2` from `tmserver`.

1. Enter Open Tables in both windows. Join the same table; names should appear
   and Start should become available. Finish a match and a rematch.
2. Leave the table and enter trading in both windows. Exchange one card from
   each account, confirm on both sides, and complete collection. Check that
   each account received exactly one intended card and lost its offered card.
3. Close both windows, rerun the same command and confirm both inventories and
   the human-match counter survived. Verify that the two accounts stay distinct.

## Pass 3: rewards, then three players

Use `.\Play.cmd --rewards` to check a CPU result with local reward options, then
`.\Play.cmd --clients 2 --rewards` for human winner-card selection and configured
stakes. Note cards/money before and after, accept rewards, close and reopen.
Check that collected rewards appear once. These tests change the local economy.

Finally, `.\Play.cmd --clients 3`: enter Open Tables in all windows and start a
three-player match. Confirm the 25-cell board, all three turns and result/Continue
screens. A native UI pass is still needed even though the headless game logic passes.

Auction, Prize Center, event and spectator UI checks follow these core passes;
they remain on the broader deferred-review list. Do not mark this checklist
passed merely because the process started or exited successfully.
