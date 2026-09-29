# Original-code rules prototype

The optional `tmserver.game.original.native.NativeRules` backend executes original
TM x86 instructions directly in 32-bit Windows Python through a small C++23 DLL.
It covers battle calculation, card growth, target selection, and ordinary
two-player COM hand/placement decisions.
There is no emulator, IPC, game window or game startup. Python supplies validated
inputs, random draws and result types; the original code calculates the results.

The live server defaults to Python. An explicit `native-hybrid` option now
injects this component into ordinary and event matches. The earlier Unicorn
backend remains available as a separate research tool; serving native rules
requires no emulator or server language change.

## Build and run the native bridge

Requirements: Windows, 32-bit Python 3.13 or newer, CMake 3.29 or newer, and MSVC
with x86 C++ build tools and C++23 support. The validated build uses Visual Studio
2026 / MSVC 19.51. The bridge has no third-party C++ dependencies and links the
MSVC runtime statically. Both Python and the bridge must be 32-bit.

From the checkout, using a regular 32-bit Python installation:

```powershell
cmake -S native/original_rules -B build/original-rules -A Win32
cmake --build build/original-rules --config Release
$python32 = 'C:/path/to/32-bit/python.exe'
& $python32 -m pip install -e .
$env:TM_ORIGINAL_IMAGE = 'C:/path/to/TM_emulated_mapped.dll'
$env:TM_NATIVE_BRIDGE = (Resolve-Path build/original-rules/Release/tm_original_rules.dll).Path
& $python32 -B -m tools.probe_original_rules --image $env:TM_ORIGINAL_IMAGE --bridge $env:TM_NATIVE_BRIDGE --output results/native-rules-prototype.json
& $python32 -B -m unittest research.native_backend_tests -v
```

The development checkout also has an isolated, ignored 32-bit Python 3.13.15
embedded runtime at `build/python-x86/python.exe`, already configured to import
the checkout. It can run the final two commands without installing anything.
It is not part of the repository or wheel. The existing research image can be
located from the development archive record:

```powershell
$archive = (Get-Content research/migration.json -Raw | ConvertFrom-Json).source
$env:TM_ORIGINAL_IMAGE = Join-Path $archive 'TM_emulated_mapped.dll'
```

The ignored x86 environment also has the pinned admin extra installed. An actual
combined native/admin CLI check passes encrypted game/profile requests,
authenticated inspection and timed shutdown without importing an emulator;
see `results/native-admin-win32-cli-acceptance.json`. Normal installations must
still install `tmserver[admin]` explicitly before enabling the panel.

Native sources and the C ABI live in [native/original_rules](../native/original_rules).
Use clang-format 22 with the root `.clang-format` when editing them.

The Windows CI job builds the Win32 bridge and runs this image-free check:

```powershell
& $python32 -m tools.check_native_bridge build/original-rules/Release/tm_original_rules.dll
```

It checks ABI 2 exports, ctypes record sizes/offsets and closed-state/input guards
without loading an original image or installing third-party Python packages.
Original-routine comparisons still require the external image. The CI job is
configured in the checkout; remote execution has not been performed.

## Serve with the native backend

After building the bridge and supplying the verified image:

```powershell
& $python32 -m tmserver --state var/native-state.json --rules-backend native-hybrid --rules-image $env:TM_ORIGINAL_IMAGE --rules-bridge $env:TM_NATIVE_BRIDGE
```

Both paths are required explicitly. Architecture, ABI, image identity and address
mapping are checked before opening a save or any listener. The CLI owns the
native context until the HTTP, profile and game services have drained. An
embedding application instead supplies `rules_backend=engine` to `Server` or
`MatchService` and retains responsibility for closing that borrowed context.
The current bridge ABI is **2**. Rebuild the bridge when updating the Python
adapter; an ABI 1 DLL is rejected before resolving the new exports.

The name **native-hybrid** is intentional: original instructions calculate
battles, card growth, target choice and ordinary two-player placement AI.
Special-tile or three-player placement AI
uses the existing Python implementation, selected from the live match shape.
Turn order, cards, fields, progression, transactions and rewards remain Python.
This integration does not remove the reconstructed algorithms or imply original
hosted reward formulas have been recovered.

A native calculation failure aborts its round through the normal release path.
There is no automatic retry through Python after random draws may have been
consumed. Event failures are isolated to the affected round; unrelated tables
continue. The native code still shares the process and has no crash isolation.

## Required code image

Both backends require this exact external image; the Python adapter verifies its
size and SHA-256 before handing its bytes to the native engine:

```text
Size:   3,518,464 bytes
SHA256: 422e46ab2ad6fd02db570a2384424dd1540b197e1ccd7e3da2645ca2b40b1879
```

This is an already-unpacked, RVA-indexed memory image. The loader's
`TM_native.dll` is still packed and is deliberately rejected. Ordinary PE section
mapping does not reconstruct the required instructions. No original binary is
added to the server repository or its wheel by this change.

The bridge reserves the original `0x10000000` address range, copies the image,
redirects the CRT `rand()` entry to the supplied callback, and adds returns at the
ends of the isolated hand-selection and target-selection fragments. A growth
presentation helper is replaced with a return that preserves its stack contract;
growth arithmetic still executes original instructions. It makes code executable/read-only,
keeps globals writable, and does not invoke DllMain or resolve game imports.
If that address range is unavailable, initialization fails without overwriting it.
Only these known routines are supported; this is not a general Windows DLL loader.
The low-level C ABI trusts its caller to supply the verified image and valid buffers.

## Library interface and lifetime

```python
from tmserver.game.original.native import NativeRules
from tmserver.game.rules import Card, CRTLCG

with NativeRules("C:/path/to/TM_emulated_mapped.dll",
                 "build/original-rules/Release/tm_original_rules.dll") as engine:
    card = Card(1, 50, 0, 40, 30, 1, 255)
    result = engine.calculate_battle(card, card, CRTLCG(11))
    move = engine.choose_move((card,) * 5, (None,) * 16, 0, CRTLCG(11))
```

`calculate_battle` accepts the same card-like inputs, signed modifiers and property
bytes as `game.rules.calculate_battle`, returning its `BattleResult`. `choose_move`
accepts the same hand, board, owner and personality as `game.ai.choose_move`,
returning `Move` or `None`. `grow_card` accepts a card, `GrowthBaseline`, mode and
random value, returning `GrowthResult`; its default x87 precision is 64 bits and
53-bit precision is also supported. `choose_target` accepts ordered candidates,
a random callback and board size 16 or 25. It returns the chosen field or `None`.
`last_draws` records successful random draws from the
latest operation, including one interrupted by a callback error. Shared validation
helpers are reused; Python calculation and scoring functions are not called.

Native contexts share one fixed-address image and allow **one operation per
process at a time**. Interleaved contexts are supported; concurrent or reentrant
calls fail with `OriginalCodeError`. Use a context manager or call `close()` to
release a context. Closing the last one frees the image. Every operation restores
writable image data and scratch memory before seeding inputs; match state and RNG
state belong to the caller.
Growth saves and restores the caller's x87 floating-point state, including when
an original routine raises a guarded exception. The growth scratch catalogue row
uses the caller's baseline while retaining the output card's original model ID.

Callback exceptions are captured in Python, signaled through the C boundary, then
re-raised after native execution returns. Failed operations may already have
consumed random draws; retrying requires restoring the caller's saved RNG state.
The bridge catches structured exceptions around the original calls, but native
execution has no instruction budget or crash isolation. A bad native memory write
can affect its Python host. A separate process remains an option if isolation is
needed; bitness alone no longer requires one.

## Verified scope and measurements

The ABI 2 extension passes 25 Win32 tests, including nine complete ordinary,
special, CBC, three-player and event round replays. Packet streams, cards,
snapshots and random consumption match the Python implementation. Independent
original-instruction checks compare 6,248 growth cases using the actual catalogue
lookup, 700 target cases across every supported seat, and both sole-target
classifiers. Additional native cases cover 144 custom baselines, 360 target
boundaries, interleaved contexts, reentry and floating-point restoration after a
guarded exception. These checks do not open the game.

Run the independent comparison with the existing external research harness:

```powershell
.venv/Scripts/python -B -m unittest research.native_primitive_oracle_tests -v
```

That research process uses Unicorn as an independent original-instruction oracle;
the server's native bridge does not. See the test module for the required image,
bridge and external harness paths. The following measurements describe the
earlier two-operation prototype, not a new ABI 2 benchmark.

On September 28, 2026, the native bridge passed 15 executable-dependent tests:

- 1,168 battles matched every result field and exact random draw order.
- 256 AI cases matched decisions and draws, including empty hands, blocked boards,
  owner changes and personality branches.
- Three complete COM rounds, against NPCs 1, 3 and 30, emitted identical packets,
  final cards, snapshots and final RNG state when these two functions replaced
  their Python counterparts inside the actual `MatchService` during the test.
- Callback errors, partial draws, KeyboardInterrupt, reentry, concurrent busy
  rejection, interleaved state reset, reference counts and reopening passed.
- A fresh process opened the native engine without importing Unicorn or the
  emulated backend. ABI and unsupported-host checks also passed.

Representative 1,000-call measurements, including validation and memory reset,
using 32-bit Python 3.13.15 / Release MSVC bridge / Windows 11:

| Operation | Python median | Native original median | Native original p95 |
| --- | ---: | ---: | ---: |
| Battle calculation | 0.0045 ms | 0.0317 ms | 0.0371 ms |
| Ordinary AI move | 0.0252 ms | 0.0420 ms | 0.0471 ms |

First construction, including adapter import and image verification, took about
27 ms. Python remains faster on these small routines. The purpose of calling the
original code is fidelity and potentially replacing reconstructed rules, not a
speed advantage over the existing Python implementation. These are single-machine
microbenchmarks, not multiplayer capacity measurements.

The earlier Unicorn run on 64-bit Python 3.13.15 measured 0.1276 ms per battle and
0.3100 ms per AI move (200-call medians). Interpreter architecture and sample sizes
differ, so this is context rather than a controlled native/emulated speed ratio.

These measurements describe the initial prototype. The subsequent integration
uses constructor injection rather than test-time replacement of module functions.
See [VALIDATION.md](VALIDATION.md) for current native replay, failure-isolation,
portable and installed-package results.

## Optional emulated backend

The cross-platform implementation uses Unicorn APIs; its executable tests have
been run on Windows. No native Linux deployment has been validated.

```powershell
.venv/Scripts/python -m pip install -e ".[original]"
.venv/Scripts/python -B -m tools.probe_original_rules --image $env:TM_ORIGINAL_IMAGE
.venv/Scripts/python -B -m unittest research.original_backend_tests -v
```

Omitting `--bridge` selects Unicorn. For direct use, construct
`tmserver.game.original.backend.OriginalRules(image)`. Its battle and move
signatures match `NativeRules`, but it owns an independent emulated context.
Growth and target selection remain optional backend capabilities; the emulated
backend does not implement them. Existing two-method backends continue to work.
Separate instances support concurrent workers. Each entry has a default 200,000
guest-instruction limit and must reach its exact return/stop address. AI uses two
entries. This does not impose a wall-clock deadline on Python callbacks.

## Remaining work

Special-tile/three-player placement AI, NPC hand generation,
rewards and the match state machine still use Python. The COM replay is a hybrid
engine test, not an all-native game or a new rendered-client check. Historical
online RNG scheduling and reward policies remain unknown.

The native option currently adds code while Python remains available for portable
serving and comparison; net deletion has not been demonstrated. Making native
rules the default would require broader rule coverage, a reproducible
unpacking/distribution path and deployment validation. Startup/shutdown integration
is implemented. Native Linux execution
or a Wine worker would be separate, untested deployment work. Rewriting the
server in C++ is not necessary to use this bridge.
