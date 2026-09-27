# Generic profile scalar output bounds

This note records original-code constraints relevant to preserving and emitting
generic `GTM0P/PO` records and applying `GR` privacy updates. These fields remain
separate from native save data and ranking formulas.

All addresses refer to the reconstructed, memory-layout TM code image with
SHA-256 `422e46ab2ad6fd02db570a2384424dd1540b197e1ccd7e3da2645ca2b40b1879`.
The instructions were inspected read-only; no native application was launched.

## Four opaque float fields

PO fields **12, 13, 14 and 15** decode into record offsets **80, 84, 88 and
8C**. Their application meanings remain unknown.

- `101A3995..101A39CB` loops over these four fields. It calls `101A31F0` at
  `101A39AD` and stores the result with `fstp dword ptr [eax]` at `101A39B9`.
  This is signed IEEE float32 storage. No nonnegative or application-specific
  clamp appears in this loop.
- `101A31F0` locates the requested field and calls `101A3180`, which identifies
  its delimiter bounds and passes them to `101A2F70`.
- `101A2F70` reserves **0x80 bytes** of stack text space. It copies characters
  into that buffer at `101A2FA4`; the loop is controlled by the field's input
  end, with no observed comparison against the buffer capacity. It appends NUL
  at `101A3002`, then calls CRT `atof` at `101A3008` (`101E4381`).
- The character checks at `101A2FCD..101A2FF5` accept digits, `+`, `-`, `E`/`e`
  and `.`. Other characters take the parser-error branch at `101A301B`.
  This whitelist is not a complete grammar: final numeric interpretation is
  delegated to `atof`.

A **127-byte maximum numeric text length** leaves space for the terminator in
the original 128-byte buffer. This is a local safe-output bound derived from
that implementation, not a recovered historical server validation rule.
Bounding the total PO message alone does not enforce this per-field limit.

Requiring conversion to produce a **finite float32** is also a local safety
rule. It prevents restored text from emitting an overflowing value while
preserving valid negative values, float32 rounding and subnormal values. It
does not establish a historical formula or a gameplay range for these opaque
fields. The server's existing decimal-only syntax is a narrower accepted
format than the original character whitelist; this evidence does not require
adding exponent or leading-plus input support.

The external `profile_contract_vectors.py` harness replaces CRT `atof` with
Python `float` while executing the original surrounding parser and stores.
Its successful field-placement cases therefore **do not prove original CRT
overflow, malformed-number or special-value behavior**. No such behavior is
claimed here, and an overlong input was not executed against the original
stack buffer.

## Privacy selector identity and native output

The original editor's literal at `102362CC` is:

```text
<PP>(%d)<NO>(6,%d)<NO>(14,%d)<VO>(%d)<FO>(%d)
```

`1016A598..1016A5D2` loads the decoded profile bytes and formats that template;
`1016A5EC` passes it to the `GR` constructor `101A5E60`. The constructor
normalizes parentheses and commas to the actual BEL/ACK wire delimiters.
The selectors are literal canonical decimal **6** and **14**, with these
distinct destinations:

| Update | PO field | Record offset | Meaning |
| --- | --- | --- | --- |
| `PP` | 3 | 1C | Purpose, native choices 0–5 |
| `NO(6,…)` | 47 | CF | Card Level visibility |
| `NO(14,…)` | 55 | D7 | Title visibility |
| `VO` | 40 | C8 | Average Rank visibility |
| `FO` | 8 | 2A | Overall Player Profile visibility |

The editor save branch `101674EB..10167546` produces exactly **1/private** or
**3/public** for the four visibility bytes. Loading them at
`10168022..101680B7` treats exactly 1 as private and other byte values as public,
normalizing public values to 3. That read-back behavior does not establish
additional legitimate editor writes such as 0 or 2.

The native sender does not produce `06` or `014`. If the server retains its
existing leading-zero decimal acceptance, those spellings still designate the
same numeric selectors. Duplicate detection must use the parsed selector
identity, while preserving the two genuinely distinct `NO(6,…)` and
`NO(14,…)` updates. No alternative selector meanings or privacy-tag aliases
were established by this bounded source inspection.

The existing external `PROFILE_CONTRACT.md` and
`profile_contract_vectors.py` already preserve the editor visibility and
71-field layout evidence. This note adds the explicit safe float-text boundary
and records the relevant selector implications without duplicating those probes.
