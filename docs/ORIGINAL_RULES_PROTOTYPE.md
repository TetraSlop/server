# Native original-code rules

The server executes original TM x86 instructions through the C++23 bridge and
32-bit Windows Python. This is the required gameplay path. The Python battle,
growth, target-choice and placement-scoring implementations and the Unicorn
backend have been removed.

Native execution covers battles, card growth, target selection, and COM hand and
placement decisions on ordinary, special-tile and three-player boards. Python
retains match state, turn sequencing, board effects, room policy, transactions,
rewards, authentication and transport. Calling client code does not recover
historical hosted reward formulas.

## Build and configure

Use Windows, Python 3.13 or newer **32-bit**, CMake 3.29 or newer, and MSVC x86
C++ build tools with C++23 support. The validated compiler is MSVC 19.51. The
bridge links the MSVC runtime statically and has no third-party C++ dependencies.

The default JSON persistence needs no extra driver.
For optional PostgreSQL on Windows x86, supply a **32-bit libpq** client library
for plain Psycopg and retain MSVC build tools for Greenlet's source build. The
current Psycopg binary extra has no Win32 wheel, so the Windows PostgreSQL extra
intentionally uses plain Psycopg. PostgreSQL itself can run separately on any
supported server platform; only the client library must match Python's bitness.

```powershell
cmake -S native/original_rules -B build/original-rules -A Win32
cmake --build build/original-rules --config Release
$python32 = 'C:/path/to/32-bit/python.exe'
& $python32 -m pip install -e .
$env:TM_ORIGINAL_IMAGE = 'C:/path/to/TM_emulated_mapped.dll'
$env:TM_NATIVE_BRIDGE = (Resolve-Path build/original-rules/Release/tm_original_rules.dll).Path
& $python32 -m tmserver --state var/state.json
```

The CLI also accepts `--rules-image` and `--rules-bridge` instead of environment
variables. There is no backend selector or Python fallback. Missing paths,
unsupported host architecture, the wrong ABI, or an invalid image fail before
opening the game listener. Install `.[auth]` or `.[admin]` when enabling those
optional services.

The development checkout has an ignored x86 interpreter at
`build/python-x86/python.exe`. Its existing external image can be located with:

```powershell
$archive = (Get-Content research/migration.json -Raw | ConvertFrom-Json).source
$env:TM_ORIGINAL_IMAGE = Join-Path $archive 'TM_emulated_mapped.dll'
```

These paths describe this development machine, not a packaged distribution.
Neither the server wheel nor the native bridge archive includes the original
executable image. A native Linux gameplay deployment is not supported by this
Windows bridge.

## Required image and ABI

The adapter requires an already-unpacked, RVA-indexed memory image with exactly:

```text
Size:   3,518,464 bytes
SHA256: 422e46ab2ad6fd02db570a2384424dd1540b197e1ccd7e3da2645ca2b40b1879
```

The loader's packed `TM_native.dll` is not interchangeable with this image.
The adapter checks size and SHA-256 before calling native code. The current
bridge ABI is **3**; rebuild the bridge when updating the adapter. ABI 2 cannot
represent the special tile state and 25-cell boards now required for native COM.

The bridge reserves the original `0x10000000` range, copies the verified image,
hooks its CRT random function, and terminates the isolated selection fragments
at their known boundaries. It does not invoke DllMain, resolve game imports,
launch a game window or communicate with an emulator. Address conflicts fail
initialization without overwriting the occupied mapping.

Sources and the C ABI are in [native/original_rules](../native/original_rules).
Use clang-format 22 with the repository `.clang-format` when editing them.

## Lifetime and calls

```python
from tmserver.game.original.native import NativeRules
from tmserver.game.rules import Card, CRTLCG

with NativeRules("C:/path/to/TM_emulated_mapped.dll",
                 "build/original-rules/Release/tm_original_rules.dll") as engine:
    card = Card(1, 50, 0, 40, 30, 1, 255)
    result = engine.calculate_battle(card, card, CRTLCG(11))
    move = engine.choose_move((card,) * 5, (None,) * 16, 0, CRTLCG(11))
```

`choose_special_move` accepts the existing `special_rules.Cell` board model and
preserves tile properties and spinner directions on 16- or 25-cell boards.
`grow_card` accepts a `GrowthBaseline`, mode and random byte; x87 precisions 53
and 64 are supported. `choose_target` preserves candidate order. `last_draws`
records successfully consumed random values, including a partial failed call.

The CLI owns its context until services drain. Embedding applications may pass a
`rules_backend=engine` context and retain responsibility for closing it. Direct
rules entry points use the process-owned native context configured through the
two environment variables; they never substitute Python formulas.

Contexts share a fixed-address image and permit one operation per process at a
time. Interleaved contexts are supported; concurrent and reentrant calls raise
`OriginalCodeError`. Every operation resets writable image state and scratch
memory. Growth preserves the caller's x87 state even after a guarded exception.
Match state and random state remain owned by Python.

Callback exceptions cross the C boundary as a failure status, then are reraised
in Python. A calculation failure aborts the affected round through the normal
recovery path; consumed random draws are not retried through another backend.
The bridge catches structured exceptions but runs in the Python process, with
no instruction budget or process-level crash isolation.

## Verification

The image-free CI check builds the bridge and verifies ABI 3 record layouts,
exports, initialization guards and closed-state behavior:

```powershell
& $python32 -m tools.check_native_bridge build/original-rules/Release/tm_original_rules.dll
```

With the original image configured, run actual native acceptance checks:

```powershell
& $python32 -B -m unittest research.native_backend_tests -v
& $python32 -B -m tools.probe_original_rules --image $env:TM_ORIGINAL_IMAGE --bridge $env:TM_NATIVE_BRIDGE --output results/native-rules.json
```

The native-only regression corpus freezes 8,067 result/draw cases: 1,168 battles,
256 ordinary moves, 6,248 growth cases, 360 targets and 35 special/three-player
moves. The latter are a compact subset of 768 captured compatibility cases that
all matched the original x86 engine before the Python scorer was removed.
Expectations are static data and hashes, not another rules implementation.

The 17 acceptance tests also cover input guards before randomness, callback
errors and partial consumption, reentry, concurrent busy rejection, context
lifetime, global-state reset and x87 restoration. These checks run without
launching the game. Ordinary CI does not include the copyrighted image, so the
ABI check alone does not claim to execute the original routines.

Earlier emulator comparisons and prototype timings remain historical evidence
in Git history and `docs/research/`. Their runnable emulator harnesses were
retired; there is no emulator dependency to install.
