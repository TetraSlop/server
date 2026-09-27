# Tetra Master in a browser: practical options

Research date: 2026-09-26. Recommendation, not a completed browser port. No deployment or emulator compatibility test was performed for this note.

The strongest long-term direction is a TypeScript browser client connected to the same hosted game state used by the original Windows client. Preserve the native wrapper as a behavioral reference. Keep the recovered POL transport in an adapter, so the website does not have to reproduce PlayOnline login, encryption, or Windows interfaces.

The quickest way to demonstrate the existing client inside a browser is to stream a hosted Windows session. A short Boxedwine experiment is also worthwhile if running the original binary on each visitor's device is a priority; its compatibility and performance are unverified for this game.

## What exists today

- The original 32-bit TM DLL renders its real title and Options screens through a scratch Windows wrapper. The wrapper supplies COM startup, D3D8 compatibility, input, and an explicit offline scene transition.
- The separate Python server has legacy transport/command-envelope code. Native polcore handshake and JOIN/NOTICE were tested by the parent task. This is not yet a profile service or playable match server.
- The application command payloads, online startup, card data, match rules, and persistence still need reconstruction. A website does not remove those unknowns.

## Comparison

| Approach | Reuses | Main remaining work | Assessment |
|---|---|---|---|
| Browser-native TypeScript client | Extracted assets, observed layouts, recovered rules and server behavior | Render/input/UI implementation, asset pipeline, rules and account service | Recommended product direction; best control over touch, resizing, accessibility and hosting cost |
| Original DLL in browser x86/Wine emulation | Original UI, game code and assets | Loader/COM/protection compatibility, D3D8, network bridge, downloads and performance | Worth a bounded compatibility experiment; cannot promise it works |
| Hosted native Windows client streamed to browser | Current working wrapper almost directly | Capturing graphics/input, session allocation, isolation and cleanup; still needs game server | Fastest fidelity demonstration; operational cost grows with concurrent client sessions |
| Recompile/lift DLL to WASM | Potentially selected recovered functions | Recover source or implement binary translation plus Windows services | Not a small wrapper project; use WASM selectively after functions are understood |

### Browser-native client

Start at the original 640x480 logical resolution, with proportional scaling and mapped pointer coordinates. Recreate the title, menus, card collection and board using TypeScript and PixiJS's WebGL renderer. Ordinary HTML works well for login, settings and accessible forms. PixiJS currently recommends WebGL for production; its WebGPU backend still has browser inconsistencies. Plain Canvas 2D is sufficient for the earliest board prototype. [PixiJS renderer documentation](https://pixijs.com/8.x/guides/components/renderers)

Use an asset manifest containing stable original resource IDs, hashes, dimensions and atlas rectangles. Decode archives into textures, font metadata, audio and structured card data as their formats become understood. The native texture hook can help identify and compare resources, but a capture of one scene will miss unvisited assets and metadata. Build an importer for user-provided files or use assets the project has permission to distribute; do not make bundling the original DLL or assets a prerequisite for an initial public prototype.

The browser sends actions, such as joining a room or playing a card. The hosted server validates them, applies rules and returns events or a snapshot. Put accounts, collections, decks, match results and transactions in server storage. Keep preferences and optional imported assets locally. Use sequence numbers, action IDs, versioned rules and reconnect snapshots; preserve server-side randomness in match records so discovered behavior can be replayed and compared. Do not assume the PlayOnline game's rules exactly match another Tetra Master version.

### Browser emulation

[Boxedwine](https://github.com/danoon2/Boxedwine) runs 32-bit Windows applications through Wine and has a WebAssembly build. It advertises Direct3D support generally, but explicitly calls out slow web builds and imperfect web audio. That is a credible candidate, not proof this D3D8/COM/protected DLL works. Its [build instructions](https://github.com/danoon2/Boxedwine/blob/master/docs/How-To-Build-Boxedwine.md) document an Emscripten target.

Test only the already-working offline title/Options wrapper first. Pass criteria: real title image, working click into Options, stable rendering, usable loading time, clean shutdown, then a connection to the test server. Adapt the wrapper's Windows-specific integrity handling for the emulated environment if required, without changing the Windows release. If startup/protection or graphics becomes a substantial emulator project, stop the experiment and keep browser-native implementation primary.

[v86](https://github.com/copy/v86) supports older Windows guests and translates x86 code to WASM at runtime, but its documented graphics device is VGA/SVGA with Bochs VBE extensions. That does not establish accelerated D3D8 compatibility. It also introduces an OS image and more runtime dependencies. Boxedwine is the more targeted first experiment for this Win32 application; v86 is a secondary preservation route.

### Hosted native streaming

Run the current wrapper in a separate hosted Windows session per player and forward display/audio/input. [Apache Guacamole](https://guacamole.apache.org/doc/gug/introduction.html) provides browser access to VNC/RDP sessions and embeddable client APIs. Test actual D3D8 capture under the chosen remote-session configuration before treating this as viable. A WebRTC capture pipeline could later improve animation delivery if needed.

This preserves the original UI and requires less frontend reconstruction. It still needs session scheduling, reconnect, cleanup and separation between players. It spends server CPU/memory and streaming bandwidth for each active client. The session host must contain the necessary authorized game files. It is attractive for a small private demo or a reference service, less attractive as the default architecture for a broadly accessible website.

### Why Emscripten alone is insufficient

[Emscripten](https://emscripten.org/docs/introducing_emscripten/about_emscripten.html) compiles portable C/C++/LLVM-based code to WASM. The recovered TM.dll is an x86 Windows binary with COM, Direct3D8 and other native dependencies; it is not a source project that can simply be passed to that compiler. A custom binary translator or Windows API emulator would be a separate, substantial project. Once an asset decoder or pure rule function has been reconstructed into testable source, compiling that component to WASM could be useful.

## Reuse the server work through two interfaces

```text
Original TM client -> legacy POL/IRC adapter --+
                                              +-> account/lobby/match services -> database
Browser client -> HTTPS + WebSocket adapter --+
```

Keep the current Python code while the protocol and game behavior are being established. Move recovered semantic behavior out of line parsing into shared service methods; there is no need to rewrite the backend in TypeScript just because the frontend uses it. The browser can use normal website accounts, with any legacy ID mapping kept inside the native adapter.

Ordinary websites cannot open the original raw TCP connection to port 51241. A browser-native client should use WSS directly; an emulated original client would need a fixed-destination WebSocket-to-TCP bridge. This bridge solves transport only, not unknown game messages. [Emscripten networking documentation](https://emscripten.org/docs/porting/networking.html)

## Suggested proof-of-concept order

1. **Recover one real online path.** Complete native profile/bootstrap far enough to enter a room, and record a minimal application message exchange. This anchors both clients to verified semantics.
2. **Browser visual slice.** Import a small authorized asset set, reproduce title/Options and one card view, and compare screenshots and hit regions against the native wrapper. This can proceed independently of full matches.
3. **Shared lobby.** Add WSS beside the native adapter. Demonstrate a browser and native client seeing the same room membership using the same underlying state. This is a more useful milestone than two unrelated demos.
4. **One complete match.** Use a fixed small card set and the recovered rule subset. Both clients submit moves; the server owns outcomes. Capture a replay and verify disconnect/rejoin restores the same state.
5. **Persistence and breadth.** Save collections/results, then expand rules, menus, sound, shop and other features as their behavior is verified.

A Boxedwine title-screen experiment or one streamed Windows session can run as an optional feasibility spike before committing to browser-native rendering. Neither should delay the protocol and rule work shared by every approach. Calendar estimates for a faithful port would be speculative until the first complete native match and asset formats are understood.
