# Windowed mouse correction

User symptom: the in-game pointer changes direction around UI controls while the
Windows pointer remains normal. The fallback input bridge was reporting desktop
pointer differences as relative axes, with no alignment to the software cursor.
It also kept a stale baseline across loss of foreground focus.

Original TM 1.6.11.0 behavior:

- Mouse format1020C9B4 is relative,20 bytes;10185B10 stores device deltas.
- Input object102FF570 has signed16 cursor X/Y at+2C/+2E.
- Update10189270 integrates relative movement at10189360, clamping to640x480.
- Positive signed DWORD+14 animates toward the target at+10/+12 instead.
  UI focus can select this path independently of the Windows pointer.
- There is also a conditional hover-attraction branch in1018BCF0, but its
  enable field has not been shown to be active. This patch does not alter it.

The wrapper now samples only while its client area is foreground and under the
pointer, maps client coordinates to the640x480 backbuffer, and invalidates stale
tracking on focus, entry, resize and device-acquisition transitions. A physical
move or new mouse press cancels the software animation and reports target minus
current software cursor as relative movement. The original integration, bounds
and button processing remain responsible for applying it. A stationary mouse
does not cancel keyboard-driven cursor movement. The Windows pointer is not warped.

Validation: server/test_input_bridge.cmd compiles and executes the actual bridge
against simulated OS samples, checking focus/outside-window transitions, resize,
scaled movement, direction, reacquisition, software-cursor realignment, keyboard
handoff and click handling. The separate startup wrapper build passes.
Another26 cases execute the original client instructions: device read and cursor
integration, animation cancellation/preservation, bounds and button edges. See
../protocol_map/mouse_input_vectors.json. These are isolated original-code checks,
not native visual tests by themselves. The user subsequently confirmed normal
cursor behavior in the actual client.

A later correction latches WM_LBUTTONDOWN/WM_RBUTTONDOWN until one fallback input
poll, preserving a click that begins and ends between asynchronous button samples.
Focus loss clears stale presses. The actual C++ test verifies a released-before-
poll press appears for one poll only. Native behavior of this extra fix is unverified.

The extra update is staged as tm_startup_probe.next.exe. run_local_game.py promotes
it on the next successful launch after acquiring both service ports. Desktop
control is stopped at the user's request. Build staged updates with
server/build_startup_probe.cmd --pending.
