# Native chat name bytes

The server accepted the CP932 name bytes `82 A0` in an account and preserved them
in the native profile, but chat entry closed the encrypted connection. The chat
validator alone required printable ASCII. A disposable saved account reproduced
the failure after authentication and through reload.

The original client accepts these bytes. The frozen evidence is
[chat_native_names.json](../../tests/fixtures/chat_native_names.json), captured
from the mapped original image with SHA256
`422e46ab2ad6fd02db570a2384424dd1540b197e1ccd7e3da2645ca2b40b1879`:

- The roster consumer at `0x1007E320` accepts a `42/04` record whose hexadecimal
  name is `82A0` and retains both decoded bytes.
- The presentation name gate at `0x1000CF97` reaches its accept address
  `0x1000D029` for `82A0` followed by the normal TAB separator.
- The same gate accepts a 15-byte name ending in byte `82`, while the corresponding
  16-byte spelling reaches reject address `0x1000DDD7`. This supports retaining
  the existing byte limit; it is not evidence for Unicode-aware truncation.

Heap allocation/free and locking are substituted by the research harness. The
original name consumers execute unchanged. These checks cover intake and name
extraction, without proving glyph rendering or a particular locale conversion.

The server now retains opaque bytes above ASCII while rejecting C0 controls and
DEL. It validates the full canonical name up to 16 bytes before applying the
existing 15-byte chat display limit. Sender-provided names are still replaced by
the authenticated account's display name. No Unicode conversion, identity change,
new delimiter allowance or message-budget increase is introduced.

Portable fixture, encrypted chat, reconnect/restart, profile roundtrip and adjacent
room/table/spoofing checks pass: 19 distinct focused tests. The initial encrypted
reproducer and original capture are retained under ignored
`results/roadmap-live-protocol-audit.*` and `results/roadmap-original-chat-name.*`.
