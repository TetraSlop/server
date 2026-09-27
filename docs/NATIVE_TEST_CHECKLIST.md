# Native validation of the integrated projects

Status: **awaiting user testing**. The loader has not been launched by the agent.
Headless checks validate startup plumbing and encrypted requests; the following
checks need the actual game screens. Use the new `Play.cmd`, not the old shortcut.

## Room discovery and rules

Restart `Play.cmd` after the room-directory update. Confirm Local Room, Open
Tables, Classic Rules, Special Rules and Quick Play appear in the lobby.

- In Local Room, select an empty table: Computer should be available with at
  least five cards, including when the account has zero money.
- Open Tables should show room rules as "Not Set". Classic Rules should show
  one-minute turns without special board flags; Special Rules should show
  one-minute turns with all three enabled; Quick Play should show thirty-second
  turns without special board flags. Those human rooms disable Computer.
- With two or three windows, enter a fixed room and start a human match. Check
  that the displayed rules and resulting board agree. Leave and return; the
  empty table should retain that room's preset.

Original instruction probes and encrypted peers pass these directory and
game-start paths; rendered menus and actual human input remain to be checked.

## Updated loader controls

The user's integrated run reported no sound, no resizing and no window close.
The updated loader enables the native sound engine and replaces the game's
fullscreen-only handling of desktop window commands. Verify after relaunch:

- Title/menu music and selection effects are audible (check game Options levels).
- Drag the edges and maximize/restore; the game keeps 4:3 proportions and the
  pointer stays aligned, including at the corners. Black bars do not activate UI.
- F10 opens the Dear ImGui panel. Try size presets, the aspect toggle and
  borderless fullscreen. Alt+Enter toggles fullscreen without opening settings.
- Panel clicks do not operate the game. Return to game and Escape close the panel.
- The pointer is visible in settings and over the title bar. Alt+Tab and the
  Windows key switch away normally; TM does not jump back to the foreground or
  move the pointer while another window has focus. Size presets keep settings usable.
- X, Alt+F4 and Quit game each close the client on separate runs, and the launcher
  stops its services after the last client exits. Window preferences survive relaunch.

These fixes have headless regression coverage; the native checks remain pending.

## Pass 1: one window

1. **Startup:** double-click `Play.cmd`. Confirm the title is visible, mouse
   movement/clicks behave normally and room entry succeeds.
2. **CPU:** enter Local Room, select an empty table, choose Computer and an
   opponent, finish a match, then play a
   rematch. Confirm selection, turns, battles and the result screen progress.
   Also select two distinct CPU opponents: confirm the 25-cell board and all
   three turns, finish and rematch, then return to configuration and select just
   one opponent. The board should return to 16 cells with no stale second NPC.
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

During a human rematch, change the ranking hide-name option in the Continue
dialog, then finish any delayed deck edits. Repeat with the other player making
the final Continue choice. Both clients should remain connected; the next stake
should be charged once, and the privacy preference and deck edits should survive
closing and reopening. This path has encrypted regression coverage.

Finally, `.\Play.cmd --clients 3`: enter Open Tables in all windows and start a
three-player match. Confirm the 25-cell board, all three turns and result/Continue
screens. A native UI pass is still needed even though the headless game logic passes.

Auction, Prize Center, event and spectator UI checks follow these core passes;
they remain on the broader deferred-review list. Do not mark this checklist
passed merely because the process started or exited successfully.

## Operator-queued rewards

Use a disposable test save for these checks. Stop its server, then follow
[offline administration](ADMIN.md) to queue a small explicit Prize Center award
for an existing account. Relaunch with that same save, open the Prize Center,
collect the award, and reopen the game. Confirm the award appears once and the
points remain credited. Retry the same award ID while the server is stopped;
the command should report `already-recorded` and no new award should appear.

For a save with configured events and an existing member, also queue a small
event grant using its matching configuration. Verify the event collection screen
shows the specified cards and money and accepts them once. Eligibility changes
should block or restore admission without erasing progress or pending grants.
These checks verify presentation and collection; they do not validate a
historical automatic reward formula.

## Optional weekly rankings

Launch with `.\Play.cmd --rewards --ratings local-history --weekly-rankings`.
After completing ordinary matches, check the ranking menu's Weekly Prize Money
and VS. Rating lists, including the hide-name option. Top30 is populated from
the preceding UTC week after Monday rollover; old saves are not backfilled and
Best Rookies remains empty. This is a local policy, not a recovered retail rule.

Continue using `--ratings local-history` on later launches of this save, even
when omitting `--weekly-rankings` to pause weekly tracking. See
[weekly rankings](WEEKLY_RANKINGS.md) for the save-format and pause behavior.
Encrypted file/result tests pass; native navigation and presentation are pending.
