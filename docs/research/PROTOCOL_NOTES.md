# Native protocol milestone — 2026-09-26

This preserves the early transport/startup milestone. Later gameplay, shop,
trade, event and persistence work is tracked in
[INTEGRATION_STATUS.md](INTEGRATION_STATUS.md) and
[EMULATION_COVERAGE.md](EMULATION_COVERAGE.md).

Addresses refer to TM 1.6.11.0 or the local PC polcore research image at preferred
base 0x10000000. The harness relocates them relative to each loaded module.
This is an implementation notebook for the synthetic local test, not a full
specification of the retired service.

## Verified native behavior

The original protected TM DLL completes its normal online startup, authenticates,
loads handle/character/status and save records, accepts service metadata and player
registration, displays the LocalPlayer confirmation, and reaches its logged-in title.
Selecting Play reconnects game IRC and reads the zone file. The original client
renders **Tetra Master Game Lobby / English Zones / Local Lobby**.
Selecting this zone also loads and renders **Local Room** in the room list.

Actual captures: `handle_confirmation.bmp`, `online_title.bmp`, `zone_list.bmp`,
`room_list.bmp`.
The last native probe's zone read check returned **1**. Room entry, card inventory
and a multiplayer match have not been verified or implemented. The zone's displayed
one player / one room values are synthetic metadata.

The separate browser experiment visibly renders the original title and animated
card demo at http://127.0.0.1:8766/jit/?fastboot=1. It uses the offline title wrapper,
not this protocol startup. Its Wine 11/WebGL runtime and WASM JIT are described in
`../boxedwine/README.md`; the hosted-server connection is still future work.

## Transport

- Game IRC uses TCP **51241**, profile TCP **51220**. Both servers bind loopback.
  Main Viewer IRC on 51240 is not implemented.
- Common table 132 (RVA 0x13e20) opens Game IRC: host, ID, password, client label,
  mode, callback block, cipher storage, queue storage, queue count. Actual TM args
  include gi000.pol.com, 46.49, mode 8, count 256. The harness substitutes loopback
  and synthetic EEEEEEEF / tm-local-test credentials.
- Native INET initializer 0x1000fe20 must run; WSAStartup alone is insufficient.
  Table 153 pumps MessageService during open. Numeric 422 makes table 133 return
  1 and native session state 13.
- Challenge: 25 bytes, custom base32, 40 characters; timestamp BE32 at 0, IPv4 at 4,
  BE16 port at 20. Initial lines carry four checksum characters before CRLF.
- USER contains the client's 256-bit RSA modulus, custom base64, little endian.
  Numeric 300 wraps the reversed 8-byte session key using exponent 65535.
- SqBlowfish has a custom MD5-derived schedule. Per-line OFB starts with the first
  8 modulus bytes as the little-endian IV. CR and LF are preserved by the cipher.
- NICK contains scrambled account ID, MD5(challenge + password), and client info.
  Successful authentication is required before routing.
- Table 144 joins a channel string. Table 148 emits NOTICE with args
  (slot, target-low, target-high, channel, payload).
- JOIN/PART, PING/PONG, QUIT, authenticated NOTICE/PRIVMSG relay are implemented.
  Service messages are handled before generic relay. Unknown services are logged.

`native_irc.log` and `native_traffic.jsonl` record the separate original-polcore
transport probe. `startup_traffic.jsonl` records actual TM startup traffic.
Append-only logs preserve earlier failed attempts too.

## Native command codec

TM 0x101a3620 / 0x101a3640 builds `<XX>` + BEL + payload + BEL. Embedded fields
use ACK where observed. The validator is 0x101a2d50; lookup is 0x101a2dd0.
The dictionary at 0x10246298 has 103 entries / 102 distinct tokens; duplicate PO
indices 0x60 and 0x65 both resolve to 0x60. All 206 original-DLL numeric/string
fixtures match `tm_protocol.py`. Token names alone do not establish semantics.

TM's service envelope begins GTM0G + two hex opcode digits + two reserved digits
+ two arg2 digits + two arg3 digits. The native send layer limits its inner payload
to 469 bytes; ordinary named bodies are bounded to447 bytes. The original sender
can slice a larger body, but the inspected ordinary receive path does not join
those slices. This is not an unimplemented generic fragmentation protocol; use
the recovered application-specific pages. See [the original evidence and its
limits](NAMED_PACKET_LIMITS.md). The Python command parser deliberately enforces
strict envelopes.

## Startup service messages

### Directory/version metadata

Observed request target USH6MZJA7:

```text
GTM0GE1000000@TeachDV=
```

Implemented reply:

```text
GTM0GE2000000@TeachDVAns=/D=1/V=1/AC=1|1/RK=1|1/Shm=0000000000000001|1|1
```

The equals sign after TeachDVAns is required. Native 0x100812e0 parses the reply;
0x100ab080 reads numeric elements separated by | and fields ending / or @.
Native trace confirms LN, AC, RK and SH globals contain these fixture values.

| Field | Verified destination |
|---|---|
| D, V | byte 0x1028bb68, word 0x102b2950 |
| AC | byte 0x102836cc, word 0x102836a8 |
| RK | byte 0x102795e0, word 0x102af4c0 |
| Shm | qword 0x1024f590, byte 0x10298248, word 0x1027a8f0 |

D/V are used as file directory/version inputs later; exact retail data values and
the complete service deployment topology are not recovered.

### Player initialization and registration

After the synthetic save loads, the game sends opcode 0x80 / @Init= to UE7QN1N9G,
the fixture Shm service ID 1. Reply 0x81 preserves the request suffix and includes
`@InitAns=/Ans=1`. Native 0x10088210 accepts 1 as existing player; 2 requests reload.

Actual player registration target UUIRT71SR receives GTM0P followed by:
- CR: `0x0000000000000001` ACK `1`
- AN: `LocalPlayer`
- CI: `1` ACK `Card Level 0`

The service validates those command records, retains per-session player state,
and replies GTM0P + `<CS>` BEL BEL. Native 0x101a6110 accepts command ID 0x4b,
sets 0x10323f38 to 2 and 0x10323f3c to 1; startup advances through scenes 22–24.
NS (0x4c) follows the failure path. No invented room/match replies are emitted.

## Profile service framing

The PC open request is 40 plaintext bytes: byte 0 zero, byte 1 zero for encrypted
mode, word 4 = 1. The 24-byte open response begins 0x81 and supplies nonce LE32
at offset 20. Subsequent request header is 40 bytes, with command at 1, subcommand
at 2, object size LE32 at 4, and MD5(ID8 + password + nonceLE) at 24.

Each header resets the cipher. Request objects continue after byte 40; response
objects continue after the 24-byte response header. Object checksum is the native
LE32-word sum, with partial trailing bytes shifted into the high end. Request
sizes, connection count and timeout are bounded.

| Operation | Implemented synthetic result |
|---|---|
| 0/9 | count + one 0x88-byte handle record |
| 1/3 | count + one 0x68-byte attached TM character |
| 2/3 | empty list |
| 4/6 | checked 0x7c-byte my-status record |
| 4/5 | update initial 8 status bytes; checked nonce result |
| 4/7 | acknowledge observed zero-body operation |
| 3/0 | file slice at requested offset/maximum + checksum |
| 3/3 | empty file list, observed for O/m/ |

Missing files return error 0x7e; unsupported operations return error 1 and are
logged. Other families and persistence remain incomplete.

### Viewer profile context lifetime

Without a full Viewer, the wrapper seeds the profile context from the initial
authenticated Game IRC session once, then loads handle/character/status through
table entries 171/175/193 and their native checks.

Selecting Play closes and reopens Game IRC, while profile state survives.
Polcore 0x1001a170 copies only the 0x68-byte cipher context: **+0x48 is a pointer
to the separate 0x1000-byte S-box matrix** (confirmed in 0x10064220 / 0x10064300).
A shallow copy led to invalid encrypted profile requests after slot reuse.
The wrapper now preserves both context and matrix; ProfileServer retains the
corresponding original key. Native b/g/ZL read succeeds after reconnect.
pp000.pol.com is mapped to loopback inside the wrapper; external connections fail.

## Synthetic data

`U/g/TM0DataFile` is 0x3028 bytes. Leading DWORD is 300, matching the native write
at 0x10114e87, with its exact meaning still unproven. Byte 0x3b = 1 selects Warriors
Guild through the native loader; remaining unmapped fields are zero.
This is an empty collection fixture, not a recovered retail save.

`b/g/ZL` is 0x848 bytes. Native 0x1008d402 reads a DWORD count at +0x40 and
0x40-byte records starting +0x48 (up to 32). The single record contains:
- DWORDs +0 and +4 = 1; the actual UI shows player count and room count.
- +0x0c = EN; +0x0e through +0x10 = AAA (unrestricted decoded filter values).
- NUL-terminated Local Lobby at +0x11.
- NUL-terminated IRC host at +0x2c, used by 0x10085c80 / 0x10085cf0. Fixture:
  127.0.0.1. Native trace confirms the host pointer is 0x102b29e4 for zone 0.
- Other unknown fields zero.

0x1008d180 filters decoded restriction bytes; zero values accept this test player.
ZoneLoad 0x10085ad0 / 0x101a1700 requests 0x848 bytes. RoomLoad 0x10085b90 /
0x101a1820 requests up to 0xc848 bytes of `b/g/RL%03d`.

The actual zone-0 request is `b/g/RL000`. Consumer 0x1008e065 reads a DWORD count
at +0x40 and records at +0x48 with stride 0xc8 (up to 256). A single fixture record
sets qword +0 = 1, +0x28 = ASCII A (fixed-room branch), name Local Room* at +0x2a,
and four encoded rule bytes AAAA at +0x6d. Other fields remain zero. The client
accepts the encrypted file (native read check 1) and renders Local Room, zero
players and zero open tables. The zero capacity/rule fields are incomplete.
It then opens another Game IRC connection using the selected zone's host. The
latest probe verifies host 127.0.0.1, native OpenCheck 1, and zone-connection task
states 7 -> 8 -> 10. Room entry, rule loading, channel join and gameplay have not
yet been verified.

## Reproduce and remaining work

Run `python -B -m unittest discover -s server -p "test_*.py" -v` from the parent.
Latest result: **24 tests passed**. Run `python -B server/run_startup_probe.py --advance`
for the bounded original-client test. It queues ordinary native Yes and Play UI
events, leaving all protocol checks intact. Interactive `run_local_game.py`
omits these test actions and keeps both services alive until the client closes.
`--enter-zone` additionally selects the displayed zone via its original button
event (0x0300006a, dispatched to 0x03000329) and extends the probe to 60 seconds.

Next: room-list layout and joining, then collection data, matchmaking and rules.
Shops, auctions, rankings, durable saves, main Viewer session support and a hosted
browser bridge remain unimplemented. The current browser and server milestones
are separate, and do not establish playable multiplayer.
