# Viewer mailbox enumeration

Native startup traffic contains profile command3/subcommand3 requests for
`O/m/`. The0x1A0-byte checked request uses owner+8, pathname+0x10 and maximum
WORD+0x190. Original GetMessageList100277BB..10027817 leaves request padding
uninitialized; the parser must not require those bytes to be zero.

The local service has no hosted messages. It returns an empty mailbox for the
authenticated raw POL owner, directory0/volume0, and rejects unknown folders or
other owners. It does not enumerate filesystem paths or invent message records.

An important correction to the earlier transport notes: original GetMessageList
1002789C reads the eight-byte count without a checksum, but even count0 reaches
10027A36..10027A46 and requests a further four checked bytes. The empty mailbox
reply therefore has12 bytes: eight zero count bytes followed by the zero running
checksum. The previous eight-byte-only reply was incomplete for this receiver.
Generic GetFileCount/GetFileList stop after the eight-byte empty count; those
are separate consumers of the same profile opcode. Connections are one-shot.

`profile_enumeration_vectors.py/json` executes all three original empty-list
consumers at capacities1/100 and records their exact receive sizes/check flags.
Transport reads and ticket lifecycle sinks are replaced. Five tests additionally
cover encrypted fragmented requests, two captured native requests with stale
padding, owner/path isolation, checksum/shape errors, and shutdown while a
profile connection is open. The shutdown path closes accepted transports before
waiting for listener closure, avoiding the Python3.13 active-connection wait.
