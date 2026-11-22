# Loader login contract

Players sign in with a POL ID and a password of their own choosing, over the
game's own TCP port. There is no HTTPS gateway, sign-in provider or invite
step: the first LOGIN for an unknown POL ID creates the account. This document
is the contract between tmserver and tmloader; change both together.

## Why the loader logs in first

The original client never sends its password to the server. The Game IRC NICK
carries `md5(challenge + password)` and each profile request carries
`md5(ID8 + password + nonce)`. The server cannot learn a new player's password
from those, and a stored password hash cannot check them. So the loader sends
the password itself, once, on a short LOGIN connection. The server checks it
against a salted scrypt hash, or registers the POL ID, and answers with a random
**game password**. polcore then logs in with the POL ID and that game password,
exactly as it does with a local account.

## Sequence

One short TCP connection to the game port (default 51241), before polcore starts:

1. Read the plain line `:srv NOTICE AUTH <challenge>`. LOGIN does not use the
   challenge.
2. Generate a fresh 256-bit RSA key, as polcore does (odd modulus of at least 254
   bits). Send the plain line `USER x 8 * :<modulus>`, where `<modulus>` is the
   32-byte little-endian modulus in the wire base64 alphabet, unpadded, 43
   characters.
3. Read `:srv 300 * <wrapped>`. Decrypt it with the private key (PKCS#1 v1.5 type
   2). The 8-byte session key is the payload reversed. The cipher is
   `SqBlowfish(key)` with `iv` = the first 8 modulus bytes as a little-endian
   u64. Every later line in both directions is crypt64-encrypted with the usual
   checksum framing.
4. Send `LOGIN <POL_ID> :<password>`. It must be the first encrypted line, within
   30 seconds of connecting. One LOGIN per connection.
5. Read one line. The server then closes the connection.
   - `:srv 900 <POL_ID> <GAME_PASSWORD> :created`: a new account was registered.
   - `:srv 900 <POL_ID> <GAME_PASSWORD> :signed_in`: the password matched.
   - `:srv 901 <CODE> :login refused`: see the codes below.
6. On 900, start polcore with `POL_ID` and `GAME_PASSWORD` in place of a local
   pol-id and password. Its NICK and profile digests use `GAME_PASSWORD`.

`src/tmserver/protocol/wire.py` implements the framing, RSA wrap and cipher;
`tests/support/irc.py` (`Client.exchange` and `Client.login`) does steps 1 to 5.

The game password is 14 hex characters. The server keeps it in memory only. It
stays valid for game and profile reconnects until the next LOGIN for that POL ID
or a server restart. Never store it. If polcore's login is refused or the
connection closes during NICK, run LOGIN again.

## Limits

- **POL ID:** exactly 8 characters from `EFKAOYMJVNGTDSWBQLPCIRHZXU6328401795`
  (uppercase; each character is a base-36 digit in that order). polcore sends the
  POL ID's value as the profile's GID, and the save holds that player ID in 32
  bits, so only values from 2^16 to 2^32 - 1 can register: `EEEEFWIQ` through
  `EF5FOF5A`, in practice POL IDs that start with `EE`. Others return
  `pol_id_unavailable`; values below the range belong to the built-in test
  accounts and services. Malformed POL IDs return `invalid_request`.
- **Password:** 1 to 15 printable ASCII characters (0x20 to 0x7E). Spaces are
  allowed because it is the trailing argument. polcore takes at most 15
  characters: its Game IRC open (Viewer entry 132) copies the password and its
  NUL into a 16-byte buffer.
- **Rate limits** (per server process, reset on restart): 5 failed logins per
  POL ID and 20 per IP address in 15 minutes, and 5 new accounts per IP address
  per hour.

## Error codes

| Code | Loader behavior |
| --- | --- |
| `invalid_request` | Malformed line, POL ID or password. Fix the input; do not retry. |
| `invalid_credentials` | Wrong password for an existing POL ID. |
| `pol_id_unavailable` | Outside the registrable range or reserved. Choose another. |
| `account_locked` | The account has no password or an operator disabled it. Contact the operator. |
| `account_connected` | That POL ID already has a live game session. Retry after it disconnects; a dead session times out within about two minutes. |
| `rate_limited` | Too many failures or new accounts from this address or for this POL ID. Back off. |
| `unavailable` | Storage or capacity problem on the server. Retry with bounded backoff. |

An unknown POL ID is registered on its first LOGIN, so a mistyped POL ID creates
a new account. Show the player the `created` result.

## Security

The key exchange encrypts the password against passive listeners, but the
server is not authenticated: an attacker who can intercept the connection can
read it. Only TLS or a pinned server key would fix that. Players should not reuse
a password from elsewhere. The server never logs the LOGIN line or its reply.

The built-in test accounts (`EEEEEEEF` and the others `tmserver --list-accounts`
prints, password `tm-local-test`) skip LOGIN and sign in directly, but only from
a loopback address.
