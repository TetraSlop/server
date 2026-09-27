# Play from the GitHub projects

The two sibling checkouts are `GitHub/tmserver` and `GitHub/tmloader`.
On this machine the runtime and current one-account save have already been
prepared. Start with **`tmserver/Play.cmd`**, rather than the earlier shortcut.

## First run

Double-click `Play.cmd`. It checks the loader's files and DLL hashes, starts the
packaged game/profile services on free loopback ports, and opens one native game
window. Enter **Local Room**, select an empty table, then choose **Computer**
for CPU play. Keep the launcher console open while playing. Close the
game window normally to stop the session and both services.

The Computer menu can select one or two distinct NPC opponents. Two opponents
use the original three-player, 25-cell game; Continue can replay that matchup or
return to configuration to change opponents/player count. This uses one game
window and needs no `--clients 3` option. The server path is covered by encrypted
tests; the new three-player menu presentation remains on the native checklist.

The launcher provides these rooms in the lobby:

| Room | Players | Rules |
| --- | --- | --- |
| Local Room | You and one or two CPU opponents | Choose settings in Computer. |
| Open Tables | Two or three humans | The table owner chooses settings. |
| Classic Rules | Two or three humans | One-minute turns, no special board flags. |
| Special Rules | Two or three humans | One-minute turns, all three special board flags. |
| Quick Play | Two or three humans | Thirty-second turns, no special board flags. |

The three fixed presets start with no wager or double-up. Their advertised rules
match the game and survive table resets; saved personal defaults do not replace
them. Computer is available in Local Room. The human rooms disable that action.
The missing room permission that previously greyed out Computer is corrected;
close the old session and restart `Play.cmd` to load the updated directory.
See [room configuration](ROOMS.md) for the fields and original-client evidence.

The loader now provides **F10** for its Dear ImGui settings panel and
**Alt+Enter** for borderless fullscreen. The window edges and maximize button
resize the display; 4:3 proportions are preserved by default. The panel offers
size presets and a stretch toggle. Close with the title-bar X, Alt+F4 or the
panel's Quit game button. Window preferences are stored per account under
`tmloader/runtime/settings/`. Music/effect levels remain in the game's Options.
These controls and the restored audio need a native check with the updated build.

`Check Setup.cmd` performs only the read-only setup check. It opens no game
window, starts no service and does not update the save. It validates saved
accounts and all service journals before checking the loader; it does not run
interrupted-match recovery.

For multiple local players, close the previous Play session and run from the
`tmserver` folder:

```powershell
.\Play.cmd --clients 2
.\Play.cmd --clients 3
```

Each window has its own synthetic account. All share the same running server.
Enter **Open Tables** in each window, join the same table and let its owner
press Start once everyone has entered. That room chooses two or three seats at
Start. LocalPlayer is the imported account; LocalPlayer2/3 are created as used.
Multiple actual game windows remain a native test, not a proven headless result.

To exercise the implemented local card-prize, CBC and wager rules:

```powershell
.\Play.cmd --rewards
.\Play.cmd --clients 2 --rewards
```

These options affect real cards and money in this local save. Ordinary launch
uses the existing no-stakes/no-card-transfer practice defaults; normal card
growth and profile progress still persist. Local online rating policy stays off.
An inherited `TM_DATABASE_URL` does not switch this launcher away from JSON.

To enable the local rating policy and weekly rankings:

```powershell
.\Play.cmd --ratings local-history --weekly-rankings
.\Play.cmd --clients 2 --rewards --ratings local-history --weekly-rankings
```

Weekly rankings count only results committed while the option is enabled.
Weeks begin Monday at 00:00 UTC: Weekly Prize Money shows this week's earned
result rewards, and Top30 shows the previous week's last committed ratings.
Best Rookies remains empty. This is an explicit local policy; existing progress
is not assigned to earlier weeks.

To pause weekly tracking while keeping the local rating history consistent,
omit only `--weekly-rankings`:

```powershell
.\Play.cmd --ratings local-history
```

Saved weekly history is retained while its weekly lists are hidden. Ordinary
launch still defaults to `--ratings preserve`, which keeps saved ratings and
does not record local rating results. After enabling local-history, keep passing
`--ratings local-history` for continued play; playing in preserve mode can leave
the saved rating history inconsistent with subsequent profile progress.
Each session's `session.json` records both selected options.

## Where everything lives

- `tmloader/runtime/`: ignored, user-owned copies of the DLLs, `data/` and `pub/`.
  The working copy has the low-integrity label required by the existing loader.
- `tmloader/tmloader.ini`: ignored local configuration, relative to this checkout.
- `tmserver/var/local_state.json`: current progress for this launch setup. The
  initial import copied the old committed save; the two saves do not synchronize.
- `tmserver/var/sessions/<session>/`: server traffic, loader process output,
  endpoint/account metadata and process exit codes.
- `tmloader/runtime/logs/<session>/player-N.log`: each native loader's own log.

The console prints the session ID and server log location. A second Play launch
using the same save is rejected while the first is active. Other services and
game processes are not reused or stopped. Free ports are passed directly to the
loader, so the old fixed profile port can remain occupied. No administrator
launch is requested; the loader retains its `asInvoker` manifest.

## Recreate the setup

Build `tmloader` using its README, and install `tmserver` into its `.venv` using
the server README. Then, from `tmloader`, copy a prepared user-owned runtime:

```powershell
python -B tools/prepare_runtime.py --source C:\path\to\prepared-runtime
```

This copies only the two supported DLLs plus `data/` and `pub/`, labels only the
new ignored working directory for low-integrity writes, creates `tmloader.ini`
and verifies it with `--check`. It refuses to overwrite an existing runtime or
configuration. It does not download game files, alter the source runtime, load
the DLLs, or change Windows UAC settings.

For a fresh account, leave `var/local_state.json` absent. To bring existing
progress, copy the intended committed JSON save there before the first launch;
do not overwrite a newer save. This machine's initial import details are in
ignored `var/save-import.json`.

Proceed through [the native test checklist](NATIVE_TEST_CHECKLIST.md). A passing
setup check or a clean process exit does not establish that the title or game
screens render correctly.
