# Native test checklist

Status: basic play works (title screen, mouse, computer matches, sound and
settings). The current build has not been checked.

These checks are for one recorded server and loader build. Headless tests cover
startup and encrypted requests; what the client shows needs a person playing.
Record the server and loader revisions, the deployed loader executable's hash
and the server's log folder with the results. A newer build on disk does not
mean it was the one running.

## Setup

Set `TM_DATABASE_URL` to a disposable test database (see
[PostgreSQL storage](DATABASE.md)), then start the server with three local
accounts and the supplied rooms:

```powershell
.venv/Scripts/tmserver --accounts 3 --rooms examples/auto_rooms.example.json --log-dir var/checklist
```

Start one tmloader window per player against the server's game and profile
ports, each with a different local login from `tmserver --accounts 3
--list-accounts`. See the tmloader documentation for its options. Add the
server options named in each pass below.

Start with Pass 1 and Pass 2 below: a computer match and rematch, a shop and deck
change and a reload, then a two-player match, a trade and a reload. Keep the
event, auction, spectator and three-player checks separate, so a passing basic
run does not imply they were tried.

## Room discovery and rules

Restart the server after the room-directory update. Confirm Local Room, Open
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

The loader enables the native sound engine and handles desktop window commands.
Check the complete set below on the build being accepted:

- Title/menu music and selection effects are audible (check game Options levels).
- Drag the edges and maximize/restore; the game keeps 4:3 proportions and the
  pointer stays aligned, including at the corners. Black bars do not activate UI.
- F10 opens the Dear ImGui panel. Try size presets, the aspect toggle and
  borderless fullscreen. Alt+Enter toggles fullscreen without opening settings.
- Panel clicks do not operate the game. Return to game and Escape close the panel.
- The pointer is visible in settings and over the title bar. Alt+Tab and the
  Windows key switch away normally; TM does not jump back to the foreground or
  move the pointer while another window has focus. Size presets keep settings usable.
- X, Alt+F4 and Quit game each close the client on separate runs. Window
  preferences survive relaunch.

These fixes have headless regression coverage and the earlier user confirmations
above. A complete pass of these controls on the recorded candidate remains pending.

## Pass 1: one window

1. **Startup:** start the server and one loader window. Confirm the title is visible, mouse
   movement/clicks behave normally and room entry succeeds.
2. **CPU:** enter Local Room, select an empty table, choose Computer and an
   opponent, finish a match, then play a
   rematch. Confirm selection, turns, battles and the result screen progress.
   Also select two distinct CPU opponents: confirm the 25-cell board and all
   three turns, finish and rematch, then return to configuration and select just
   one opponent. The board should return to 16 cells with no stale second NPC.
3. **Shop:** note money and card count, buy a pack and accept its cards. Sell one
   unwanted card and change a deck name or assignment. Confirm visible changes.
4. **Reload:** close the game normally, restart the server and loader, and check that the
   money, inventory, deck change and completed-match progress survived.

Report the first failing step before continuing. A useful short report is:
`log folder; step; last visible screen/message; expected behavior; what happened`.
The server writes its traffic logs to the `--log-dir` folder.

## Pass 2: two windows

Close the first window, then start two loader windows against the same server.

1. Enter Open Tables in both windows. Join the same table; names should appear
   and Start should become available. Finish a match and a rematch.
2. Leave the table and enter trading in both windows. Exchange one card from
   each account, confirm on both sides, and complete collection. Check that
   each account received exactly one intended card and lost its offered card.
3. Close both windows, restart the server and both loaders, and confirm both inventories and
   the human-match counter survived. Verify that the two accounts stay distinct.

## Pass 3: rewards, then three players

Restart the server with `--card-prizes --allow-cbc --wagers`. Check a CPU result
with these reward options in one window, then human winner-card selection and
configured stakes in two. Note cards/money before and after, accept rewards, close and reopen.
Check that collected rewards appear once. These tests change the local economy.

During a human rematch, change the ranking hide-name option in the Continue
dialog, then finish any delayed deck edits. Repeat with the other player making
the final Continue choice. Both clients should remain connected; the next stake
should be charged once, and the privacy preference and deck edits should survive
closing and reopening. This path has encrypted regression coverage.

Finally, with three loader windows, enter Open Tables in all windows and start a
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

Start the server with `--card-prizes --allow-cbc --wagers --ratings local-history
--weekly-rankings`.
After completing ordinary matches, check the ranking menu's Weekly Prize Money
and VS. Rating lists, including the hide-name option. Top 30 is populated from
the preceding UTC week after Monday rollover; old saves are not backfilled and
Best Rookies remains empty. This is a local policy, not a recovered retail rule.

Continue using `--ratings local-history` on later launches of this save, even
when omitting `--weekly-rankings` to pause weekly tracking. See
[weekly rankings](WEEKLY_RANKINGS.md) for the save-format and pause behavior.
Encrypted file/result tests pass; native navigation and presentation are pending.
