"""Encoders for the PlayOnline IRC wire format.

Reverse engineered from the PlayOnline client. Layout: TetraPackets
irc/session.md.
"""

import os
import struct

B64 = b"TSG8IncW3HFKokOg79qzeCmZs2yBYEQVAUxR5rbwi4P@jMDLtpvad0f_J1hlN6uX"  # polcore 0x10065DE0
B32 = b"N43OVHBJ1Y2C0WSXED5QFILRZMUTAPGK"  # polcore 0x10071464
POLID_CHARS = b"EFKAOYMJVNGTDSWBQLPCIRHZXU6328401795"  # polcore 0x100742EC


def b64enc(data, pad=True):
    out = bytearray()
    for i in range(0, len(data), 3):
        chunk = data[i : i + 3]
        v = int.from_bytes(chunk.ljust(3, b"\0"), "big")
        quad = bytes(B64[(v >> s) & 63] for s in (18, 12, 6, 0))
        out += quad if pad or len(chunk) == 3 else quad[: len(chunk) + 1]
    return bytes(out)


def b64dec(text):
    rev = {c: i for i, c in enumerate(B64)}
    text = text + b"T" * (-len(text) % 4)  # client pads short groups with 'T' (value 0)
    out = bytearray()
    for i in range(0, len(text), 4):
        v = 0
        for c in text[i : i + 4]:
            v = v << 6 | rev[c]
        out += v.to_bytes(3, "big")
    return bytes(out)


def b32enc(data):
    data = data + b"\0" * (-len(data) % 5)
    bits = int.from_bytes(data, "big")
    n = len(data) * 8 // 5
    return bytes(B32[(bits >> (5 * (n - 1 - i))) & 31] for i in range(n))


def _base36(value):
    digits = bytearray(8)
    for index in range(7, -1, -1):
        value, digit = divmod(value, 36)
        digits[index] = POLID_CHARS[digit]
    return bytes(digits)


def polid_scramble(polid41):
    """__sqIrcScramblePolId encode: 41-bit POL id -> 'U' + 8 chars (IRC nick)."""
    v = (polid41 & 0x1FFFFFFFFFF) | 0xC00000000000
    for k in (4, 3, 2, 1, 0):
        v ^= (v >> 8) & (0xFF << (8 * k))
    return b"U" + _base36(v & 0xFFFF3FFFFFFFFFFF)


def polid_unscramble(nick):
    v = polid_from_string(nick[1:9]) | 0xC00000000000
    for k in (0, 1, 2, 3, 4):
        v ^= (v >> 8) & (0xFF << (8 * k))
    return v & 0x1FFFFFFFFFF


def polid_from_string(s):
    """8-char POL ID string or bytes -> 41-bit value (same digit map)."""
    v = 0
    for c in s.encode() if isinstance(s, str) else s:
        v = v * 36 + POLID_CHARS.index(c)
    return v


def polid_to_string(value):
    """Canonical eight-character login for a native 41-bit POL identity."""
    if type(value) is not int or not 0 <= value <= 0x1FFFFFFFFFF:
        raise ValueError("raw POL identity exceeds 41 bits")
    return _base36(value).decode("ascii")


def checksum(data):
    """sqCalcChecksum: little-endian dword sum, short tail in the high bytes."""
    whole = len(data) & ~3
    value = sum(struct.unpack_from("<%dI" % (whole // 4), data))
    if whole < len(data):
        value += int.from_bytes(data[whole:], "little") << (8 * (4 - len(data[whole:])))
    return value & 0xFFFFFFFF


def checksum_chars(msg):
    """sqCalcChecksum + 4-char encoding appended before CRLF on every line."""
    cs = checksum(msg)
    return bytes(((cs >> s) & 0x3F) + 0x3F for s in (26, 20, 14, 8))


# --- MD5 with selectable IV (sqMd5Final zeroes the context, later hashes start from IV 0) ---
_S = [7, 12, 17, 22] * 4 + [5, 9, 14, 20] * 4 + [4, 11, 16, 23] * 4 + [6, 10, 15, 21] * 4
_K = [int(abs(__import__("math").sin(i + 1)) * 2**32) & 0xFFFFFFFF for i in range(64)]


def md5_iv(data, iv):
    msg = data + b"\x80" + b"\0" * ((55 - len(data)) % 64) + struct.pack("<Q", len(data) * 8)
    a0, b0, c0, d0 = iv
    for off in range(0, len(msg), 64):
        m = struct.unpack_from("<16I", msg, off)
        a, b, c, d = a0, b0, c0, d0
        for i in range(64):
            if i < 16:
                f, g = (b & c) | (~b & d), i
            elif i < 32:
                f, g = (d & b) | (~d & c), (5 * i + 1) % 16
            elif i < 48:
                f, g = b ^ c ^ d, (3 * i + 5) % 16
            else:
                f, g = c ^ (b | ~d), (7 * i) % 16
            f = (f + a + _K[i] + m[g]) & 0xFFFFFFFF
            a, d, c = d, c, b
            b = (b + ((f << _S[i]) | (f >> (32 - _S[i])))) & 0xFFFFFFFF
        a0, b0, c0, d0 = [(x + y) & 0xFFFFFFFF for x, y in zip((a0, b0, c0, d0), (a, b, c, d))]
    return struct.pack("<4I", a0, b0, c0, d0)


MD5_IV = (0x67452301, 0xEFCDAB89, 0x98BADCFE, 0x10325476)


class SqBlowfish:
    """sqBlowFishInit / sqBlowFishEncryptVal (non-standard key schedule, standard rounds)."""

    def __init__(self, key):
        buf = bytearray(key)
        iv = MD5_IV
        while len(buf) < 0x1000:
            digest = md5_iv(bytes(buf), iv)
            iv = (0, 0, 0, 0)
            buf += digest[: 0x1000 - len(buf)]
        be = lambda o: int.from_bytes(buf[o : o + 4], "big")
        self.p = [be(i) for i in range(18)]  # overlapping, 1-byte stride (sic)
        self.s = [[be(4 * (256 * b + j)) for j in range(256)] for b in range(4)]
        for i in range(18):
            self.p[i] ^= be(4 * i)
        for i in range(0, 18, 2):  # input block is always 0 (sic)
            self.p[i], self.p[i + 1] = self._enc_halves(0, 0)
        for b in range(4):
            for j in range(0, 256, 2):
                self.s[b][j], self.s[b][j + 1] = self._enc_halves(0, 0)

    def _f(self, x):
        s = self.s
        return (
            (((s[0][x >> 24] + s[1][(x >> 16) & 255]) & 0xFFFFFFFF) ^ s[2][(x >> 8) & 255])
            + s[3][x & 255]
        ) & 0xFFFFFFFF

    def _enc_halves(self, lo, hi):
        for i in range(16):
            x = lo ^ self.p[i]
            lo, hi = hi ^ self._f(x), x
        return hi ^ self.p[17], lo ^ self.p[16]  # (new low32, new high32)

    def encrypt_u64(self, v):
        lo, hi = self._enc_halves(v & 0xFFFFFFFF, v >> 32)
        return lo | hi << 32


def crypt64(bf, iv_u64, data):
    """sqCrypt64 with reset=1: OFB from IV per line; CR/LF never produced or altered."""
    out = bytearray()
    state = iv_u64
    for i, b in enumerate(data):
        if i % 8 == 0:
            state = bf.encrypt_u64(state)
        c = b ^ ((state >> (8 * (i % 8))) & 0xFF)
        out.append(b if b in (10, 13) or c in (10, 13) else c)
    return bytes(out)


def frame_line(msg, bf=None, iv_u64=0):
    line = msg + checksum_chars(msg) + b"\r\n"
    return crypt64(bf, iv_u64, line) if bf else line


def rsa_wrap_session_key(modulus_blob, bf_key, e=0xFFFF):
    """Build the 300-reply payload: client modulus blob (from USER) -> b64 of RSA(pad(reverse(bf_key)))."""
    n = int.from_bytes(modulus_blob, "little")
    data = bf_key[::-1]
    pad = bytes(b or 0x0A for b in os.urandom(0x1D - len(data)))
    block = b"\x00\x02" + pad + b"\x00" + data
    c = pow(int.from_bytes(block, "big"), e, n)
    return b64enc(c.to_bytes(32, "big"))
