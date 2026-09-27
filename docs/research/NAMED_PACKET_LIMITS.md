# Named-message limits and original evidence

The 447-byte body limit is intentional. The recovered ordinary receive path
treats each incoming C string as a complete named message. Arbitrarily splitting
a larger body would deliver separate messages to the game, not reconstruct the
original body. Application paging for chat rosters, cards and other mapped lists
is a separate contract.

Original constructor/finalizer functions `100A97C0` and `100A9A20` can copy a
447-character slice from a larger input. For a 448-character input, their first
and second slices carry identical eight-character headers: no fragment number
or count is encoded. The ordinary dequeue/parser at `100825A0` processes only
one queue entry; two slices with matching headers remain separate messages.

Header characters2..3 are ignored by this receive path. The regular builder
writes00 there, but the original heartbeat literal uses0D. Its meaning remains
unknown. Fixed-position trailer reads likewise do not establish a required
checksum or fragment protocol. Do not add invented trailers or reassembly.

Portable copies of the prior isolated original-instruction results are retained:

- [Builder vectors](../../tests/fixtures/protocol/envelope_vectors.json) cover
  normal output and the446/447/448-character boundary.
- [Receive vectors](../../tests/fixtures/protocol/envelope_review_vectors.json)
  cover ten seeded queue/parser cases, including ignored header bytes, separate
  slices, multiple sections and raw chat text.

These are archived results, not a fresh execution or native network capture.
Their original scripts, assembly and full `ENVELOPE_REVIEW.md` remain in the
external `protocol_map` directory under the source recorded in
[research/migration.json](../../research/migration.json). The original binary
was not modified. This evidence bounds the inspected ordinary path; it is not
a claim that every possible client path lacks reassembly.
