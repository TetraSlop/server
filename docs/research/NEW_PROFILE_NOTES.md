# Fresh profiles and guild registration

Fresh accounts now retain the original client's startup preferences: SE volume2 (Medium), BGM volume2 (Medium), trade requests1 (Accept), chat lines5, chat transparency60. The former zero-filled template muted both audio channels and declined trades. Supplied profile templates and saved accounts keep all their existing preferences.

## Proven initialization boundary

`1014B3DC` calls startup initializer `100A7B90`. Its original instructions `100A7CAF..100A7CFD` set the twelve options globals at `102B5E8C` to `[0,0,2,2,1,0,0,0,0,5,60,0]`. Importer `1014D9F0` maps the changed defaults to profile bytes B3/B4/B5 and102/103. Chat line fallback8 is used for invalid imported values, and is distinct from startup default5.

The profile's first DWORD300 is a diagnostic value written by `10114E87`; loader `101011F0` does not enforce or read it. Rating comes directly from profile DWORD30, sequence from WORD44, and guild from byte3B. Constructor `10100F10` initializes the in-memory player to rating200, sequence0, guild0 and a neutral rating ring, but a subsequent profile read overwrites rating and sequence. The optional not-found startup read also loads the existing cache; it does not create a complete default profile. These facts do not recover the historical online server's first-account rating, currency, cards, or preferences.

Accordingly, the replacement server retains its existing **local policy**: rating0, sequence0, preselected guild1,10,000T and ten generated Starter's Pack cards. `PracticeStats()` has a300 fixture default, but production account statistics are seeded by `PracticeStats.from_profile`, which reads the actual rating0. Header300 must never be used as that seed. Explicit local-history ratings enrollment remains a separate opt-in policy.

## Original guild selection path

After initial success event03001F50, `1014C866..1014C882` imports byte3B into global102B5F6C in online mode. Guild0 opens picker constructor `1014CBE0` and parent state13; guild1/2/3 advances to state15. Picker dispatcher `1014FD60` sets the chosen guild from events03001388/89/8A to1/2/3 (`1015013F`, `101501CD`, `1015018E`). Confirm03000019 posts0300115D when picker+160 is nonzero, invoking the reinitialization wrapper `1014D2D0` and controller `100910B0`.

Sender `10087F10` produces family80, caller-selected subtype, argument0:

`@Init=/NN=<hex64>/HID=0/Dm=<u8>/Vol=<u16>/CN=<hexbytes>/GID=<hex64>/L=<signed locale>/GLD=<u8>`

NN is the game identity, GID is the selected character identity, CN is the character name. Native captured startup packets use canonical character GID1 and GLD0. Directory/volume are profile-location metadata, not ownership or a request to replace account data. Locale is preserved as a validated signed integer; this implementation does not infer an unsupported public locale enum or use it to reset profile fields.

`EconomyService.initialize_profile(key,payload)` binds NN, GID and CN to the authenticated provisioned account, validates the exact field set and native widths, and permits guild0→1/2/3 only when the account is idle. GLD0 is a read-only probe even for an existing nonzero guild: the original initial controller sends before its concurrent profile read finishes. It returns Ans1. Nonzero same-guild retries are read-only and return Ans2; a different nonzero guild is rejected. Registration changes only the account's guild and projected profile byte3B. It neither grants another starter pack nor resets money, statistics, saved preferences, decks, or opaque bytes.

The adapter persists before emitting family81 with the request subtype and argument0. Ans2 makes initial controller10090A40 strictly reread `U/g/TM0DataFile` after its first read completes. Reinitialization controller100910B0 strictly rereads for any positive answer. Repeating Ans2 is therefore safe after lost replies or reconnect. Persistence failure uses the existing fail-closed server behavior: no success response, last durable save intact, reconnect required. Validation failures close the game connection instead of inventing historical negative business codes.

Guild0 provisioning remains opt-in for trusted setup; ordinary new accounts still use guild1. The new handler does not turn arbitrary wire requests into account creation, change a registered guild, or infer historical registration rewards. The native UI has not been launched for this pass.

## Validation

`test_new_profile.py` has nine tests, including original x86 bootstrap options, full profile import, guild0 picker gate, all three choice events, confirmation routing, both InitAns2 strict-read branches, and actual encrypted game/profile TCP registration. It verifies imported/saved preferences and rating/sequence preservation, exact same-guild replays after restart, identity/schema/conflicting-guild rejection without mutation, busy-account exclusion, and an injected atomic-save failure with no success acknowledgement.

Original-code tests use Unicorn with rendering/object registration/transport stubs inherited from the existing protocol fixtures. The guild decision, choice stores, option initializer/importer, named answer parser and strict file-request code execute from the original mapped DLL. TCP tests use the replacement game and profile listeners on ephemeral loopback ports; no desktop control, game launch, or installed-file writes occur.
