# Loader login contract

Players sign in with a username and password of their own choosing, over the
game's own TCP port. There is no HTTPS gateway, sign-in provider or invite
step: the first LOGIN for an unknown username creates the account and the
server assigns it a POL ID. This document is the contract between tmserver and
tmloader; change both together.

## Why the loader logs in first

The original client never sends its password to the server. The Game IRC NICK
carries `md5(challenge + password)` and each profile request carries
`md5(ID8 + password + nonce)`. The server cannot learn a new player's password
from those, and a stored password hash cannot check them. So the loader sends
the username and password itself, once, on a short LOGIN connection. The server
checks the password against a salted scrypt hash, or registers the username,
and answers with the account's POL ID and a random **game password**. polcore
then logs in with that POL ID and game password, exactly as it does with a
local account.

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
4. Send `LOGIN <USERNAME> :<password>`. It must be the first encrypted line,
   within 30 seconds of connecting. One LOGIN per connection.
5. Read one line. The server then closes the connection.
   - `:srv 900 <POL_ID> <GAME_PASSWORD> :created`: a new account was registered.
   - `:srv 900 <POL_ID> <GAME_PASSWORD> :signed_in`: the password matched.
   - `:srv 901 <CODE> :login refused`: see the codes below.
6. On 900, start polcore with `POL_ID` and `GAME_PASSWORD` in place of a local
   pol-id and password. Its NICK and profile digests use `GAME_PASSWORD`.

`src/tmserver/protocol/wire.py` implements the framing, RSA wrap and cipher;
`tests/support/irc.py` (`Client.exchange` and `Client.login`) does steps 1 to 5.

The POL ID is assigned once per username and never changes, but the loader need
not store it: every LOGIN returns it. The game password is 14 hex characters,
kept in server memory only. It stays valid for game and profile reconnects until
the next LOGIN for that account or a server restart. Never store it. If polcore's
login is refused or the connection closes during NICK, run LOGIN again.

## Limits

- **Username:** 1 to 15 characters from `A-Z a-z 0-9 _ - .`, so no spaces (it is
  a middle IRC argument). Unique ignoring ASCII case: `Alice` and `alice` are the
  same account, and the spelling from registration is kept. It is also the
  account's display name, which every allowed username fits.
- **Password:** 1 to 15 printable ASCII characters (0x20 to 0x7E). Spaces are
  allowed because it is the trailing argument. polcore takes at most 15
  characters: its Game IRC open (Viewer entry 132) copies the password and its
  NUL into a 16-byte buffer.
- **Assigned POL ID:** random and unused, with a raw value from 2^16 to 2^31 - 1
  (`EEEEFWIQ` to `EE5PIE5C`). polcore sends that value as the profile GID and
  derives NN from it, so the account stores the raw value as its player ID. The
  save holds the player ID in 32 bits, and polcore writes it as a signed 32-bit
  decimal in the GTM0P `<CR>` locator, so it stays below 2^31. It never collides with the built-in
  test accounts or service identities.
- **Rate limits** (per server process, reset on restart): 5 failed logins per
  username and 20 per IP address in 15 minutes, and 5 new accounts per IP address
  per hour.

## Error codes

| Code | Loader behavior |
| --- | --- |
| `invalid_request` | Malformed line, username or password. Fix the input; do not retry. |
| `invalid_credentials` | The username exists and the password is wrong. |
| `account_locked` | An operator disabled the account. Contact the operator. |
| `account_connected` | That account already has a live game session. Retry after it disconnects; a dead session times out within about two minutes. |
| `rate_limited` | Too many failures or new accounts from this address or for this username. Back off. |
| `unavailable` | Storage problem on the server. Retry with bounded backoff. |

There is no separate "username taken" code. LOGIN both registers and signs in,
so a taken username means the password is checked, and a wrong one returns
`invalid_credentials`. A mistyped username creates a new account, so show the
player the `created` result.

## Security

The key exchange encrypts the password against passive listeners, but the
server is not authenticated: an attacker who can intercept the connection can
read it. Only TLS or a pinned server key would fix that. Players should not reuse
a password from elsewhere. The server never logs the LOGIN line or its reply.

The built-in test accounts (`EEEEEEEF` and the others `tmserver --list-accounts`
prints, password `tm-local-test`) skip LOGIN and sign in directly, but only from
a loopback address.
