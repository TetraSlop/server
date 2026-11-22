# Loader authentication contract, v1

Login uses Discord through Supabase Auth. The server side is implemented and
tested, and sign-in and display-name import work through tmloader. Public TLS
deployment and a complete hosted match in the game client are still to do. This
document is the contract between tmserver and tmloader; change both together.

## User experience and ownership

Add a hosted-server connection mode alongside existing direct local connections.
The user selects a configured HTTPS server and clicks **Sign in with Discord**,
completes sign-in in their normal browser, then clicks **Play**. A remembered login can
refresh silently. The original game never receives a Supabase access/refresh
token. Do not store credentials in tmloader.ini, command-line arguments, logs,
screenshots or crash metadata. Use Windows credential storage for remembered
refresh credentials, scoped to server origin and Supabase project. Prefer a
small launcher/auth owner outside the legacy game process and its low-integrity
runtime. Ordinary account/window preferences can continue using their existing
storage.

The server maps the verified project issuer and Supabase user UUID to a saved
game account, automatically creating it on the first Play request by default.
Discord usernames, display names and email addresses never determine ownership.
On Play the server imports the Discord display name from Supabase's verified
Discord identity into the game profile, falling back to the Discord username.
The current game requires a normalized ASCII label of at most 15 bytes. This is
server-owned profile presentation: do not submit a name override or replace the
native account ID with a Discord username. No loader protocol change is required.
The loader needs no registration endpoint or account selector. The server saves
ownership and starter inventory together; retries and restarts reuse that account.
An operator can close new registration with `auto_register: false`. Existing
manual bindings can be imported without resetting saves. Returned
POL ID/account key are authoritative. Do not let a local account selector override
them in hosted mode. The returned legacy password is a compatibility credential,
not permission to enter the hosted service.

## Discover and sign in

Fetch `GET {server_origin}/v1/auth/config` over certificate-validated HTTPS.
Do not follow cross-origin redirects when credentials are involved. The response:

```json
{
  "protocol": 1,
  "provider": "discord",
  "supabase_url": "https://PROJECT.supabase.co",
  "publishable_key": "sb_publishable_...",
  "ticket_ttl_seconds": 60,
  "session_ttl_seconds": 3600,
  "stream_subprotocol": "tm-stream-v1",
  "max_chunk_bytes": 65536
}
```

Only protocol 1 and provider `discord` are supported by this launcher contract.
Show a configuration error for other values, not a provider-selection menu or an
email/password fallback. No Discord client secret, bot token, service-role key
or secret Supabase key belongs in the loader.
The server rejects secret/service-role Supabase keys. Supabase settings are public metadata;
they do not grant a game session.

Use Supabase's PKCE flow (not its separate OAuth-server `/oauth/token` API):

1. Generate a fresh random PKCE verifier, 43–128 RFC 7636 unreserved characters.
   Base64url encoding 32 random bytes without padding is suitable. Compute
   `challenge = base64url(SHA256(verifier))`, without padding.
2. Start a callback listener on IPv4 loopback only. Use an ephemeral port and a
   fresh random callback path, e.g. `/callback/<nonce>`, and accept only that path
   for the pending attempt. Register the corresponding loopback redirect pattern
   in Supabase. Bound the attempt lifetime and close the listener afterward.
3. Open the system browser at the project endpoint below. Percent-encode query
   values. Use the configured Discord provider:

   `GET {supabase_url}/auth/v1/authorize?provider=discord&redirect_to={callback_url}&code_challenge={challenge}&code_challenge_method=s256`

4. Accept the returned `code` once at the pending callback. Reject missing codes,
   unexpected callbacks and expired attempts. Provider errors cancel the attempt;
   do not interpret them as a usable session.
5. Exchange it with `POST {supabase_url}/auth/v1/token?grant_type=pkce`, headers
   `apikey: {publishable_key}` and `Content-Type: application/json`:

   `{"auth_code":"RETURNED_CODE","code_verifier":"ORIGINAL_VERIFIER"}`

6. Keep the returned `access_token`, `refresh_token` and expiry. To refresh, POST
   to `/auth/v1/token?grant_type=refresh_token` with the same headers and
   `{"refresh_token":"..."}`. Serialize refresh attempts and atomically replace
   both tokens from the result; refresh tokens can rotate. Never silently fall
   back to an implicit flow or accept an unverified local JWT as a game login.

Supabase owns browser identity/recovery. tmserver verifies the supplied access
token against the configured project's `/auth/v1/user`; it never trusts a user
ID supplied by the loader. Provider availability is controlled in Supabase: enable
Discord and disable other sign-in methods for this project. Automatic registration
requires a linked Discord identity in the verified response. Existing saved
owners retain access. The gateway does not infer the most recent login provider
from user-editable profile metadata. A live configured project is needed to
accept the browser part of this flow.

The operator registers `https://<project-ref>.supabase.co/auth/v1/callback` in
Discord's OAuth settings and keeps the Discord client secret in Supabase's
Discord provider settings. This is distinct from the loader's loopback callback,
which is allowlisted in Supabase. See [server setup](AUTHENTICATION.md).

## Establish play

All JSON POSTs require `Content-Type: application/json`. Bodies are bounded to
16 KiB. Do not place credentials in URLs, cookies or WebSocket subprotocols.

| Request | Credential | Body | Success |
| --- | --- | --- | --- |
| `POST /v1/play/tickets` | `Authorization: Bearer SUPABASE_ACCESS_TOKEN` | `{"protocol":1}` | 201: `ticket`, `expires_in:60` |
| `POST /v1/play/sessions` | None; ticket is the capability | `{"ticket":"..."}` | 201: play-session response below |
| `POST /v1/play/renew` | Supabase access token in Authorization | `{"session_token":"..."}` | 200: `expires_in:3600` |
| `DELETE /v1/play/session` | Play-session token in Authorization | None | 204; revoke this play session and close all its streams |
| `DELETE /v1/play/account-session` | Supabase access token in Authorization | None | 204; revoke this user's existing play session and outstanding tickets |

The session response is:

```json
{
  "protocol": 1,
  "session_token": "OPAQUE_RANDOM_CAPABILITY",
  "expires_in": 3600,
  "account_key": "UGITW5T4U",
  "legacy": {"pol_id": "EEEEEEEF", "password": "tm-local-test"},
  "streams": {
    "game": "wss://tm.example.com/v1/streams/game",
    "profile": "wss://tm.example.com/v1/streams/profile"
  }
}
```

Tickets are single use. Requesting a new ticket replaces an unused previous one.
Only one play session per account is allowed; a second redemption returns 409.
Do not automatically disconnect another session on that response. Offer an
explicit **Disconnect other session** action using `account-session`, then obtain
a fresh ticket. This also recovers a lost successful session-creation response.
Play-session tokens are process-local and never persisted by the server; restart
invalidates them. Refreshing Supabase credentials alone does not renew play:
call `/v1/play/renew` with the refreshed Supabase access token before play expiry.
The play token remains unchanged on renewal; the authenticated owner must match.

## Tunnel the original connections

Intercept the native client's game and profile connections locally. For each
native connection, open one WebSocket to the corresponding returned URL with
(browsers authenticate differently; see [Browser clients](#browser-clients)):

```text
Authorization: Bearer OPAQUE_RANDOM_CAPABILITY
Sec-WebSocket-Protocol: tm-stream-v1
```

Require the server to select `tm-stream-v1`. Use binary messages only, each
1–65,536 bytes. Messages are ordered stream chunks, with no extra header, JSON,
base64, compression requirement or packet-boundary meaning. Preserve the original
encrypted bytes exactly. Split large writes; coalesce/split reads normally.
Support backpressure and bounded queues. WebSocket continuation frames must be
reassembled by the client library before exposing messages. Standard ping/pong
control frames are transport keepalives, separate from original IRC PING/PONG.

The server permits one game stream and up to four concurrent profile streams
per play session. Profile calls routinely use a fresh connection, so each needs
its own authenticated WebSocket. Initialize the Viewer's POL ID and legacy
password from the response before either service starts, as the existing loader
already does. Complete original game login, including accepted encrypted NICK,
before encrypted profile requests; the key exchange alone is insufficient.
Server profile keys are isolated to this play-session generation.

Closing a native socket closes its WebSocket. Remote WebSocket closure becomes
EOF/error on the associated native socket. An ordinary native game reconnect may
open a fresh game WebSocket with the same unexpired play token after the old one
has closed; validated profile keys remain within that play session. Never replay
buffered writes or silently splice a new WebSocket into an existing legacy
encrypted stream. This prototype does not resume a disconnected match.

## Failures and logout

Handled API errors are `{"error":"CODE"}` with `Cache-Control: no-store`:

| Status/code | Loader behavior |
| --- | --- |
| 401 `invalid_token` | Refresh Supabase credentials once when used as the bearer; otherwise require sign-in. |
| 401 `invalid_ticket` | Obtain a new ticket; an old one expired or was consumed. |
| 401 `invalid_session` | End affected local connections and establish a new play session. |
| 403 `account_not_linked` | New registration is closed or the identity is from another project; do not choose another account. |
| 403 `discord_required` | Ask the user to sign in with Discord; user metadata is not proof of a linked Discord identity. |
| 403 `session_owner_mismatch` | Stop renewal; credentials belong to a different user. |
| 409 `session_conflict` | Offer the explicit disconnect action described above. |
| 503 `auth_unavailable`, `storage_unavailable`, `capacity` | Retry with bounded backoff; never bypass authentication. |
| 400/403/413/415/408 other codes | Surface a configuration/request error; do not retry indefinitely. |

Unknown paths (404), unsupported methods (405), proxy failures and failed
WebSocket upgrades can return non-JSON bodies. Check status and content type
before decoding JSON and show a bounded, safe error for those responses.

WebSocket denial before upgrade returns HTTP 403 (invalid session, wrong origin,
channel/subprotocol, or occupied channel). An established stream closes with
4401 for expiry/revocation, 1003 for text, 1009 for invalid chunk size, or 1011
for a transport failure. Native protocol rejection can close normally with 1000;
the loader must still pass EOF to the game. A 4401 is not transparent reconnect.

Logout revokes the play session, closes local connections and removes remembered
credentials. Supabase sign-out is separate; use its documented logout API if the
user also requests provider-session revocation. Never log access/refresh/play
tokens, tickets, auth headers or callback codes.

## Browser clients

A browser (WebAssembly) client uses the same discovery, Supabase sign-in, tickets,
play sessions, renewal, logout and stream bytes as the loader. Only two things
differ, because a browser cannot set headers on a WebSocket upgrade.

**Origin allowlist.** The operator lists the page origins that may call the
gateway in the auth config:

```json
{"browser_origins": ["https://play.example.com"]}
```

Entries are HTTPS origins with no path, at most 16. For development, plain HTTP
is also accepted for `127.0.0.1` and `localhost` (e.g. `http://localhost:5173`).
The list is empty by default, which disables browser clients: requests then
behave exactly as described above, byte for byte. A request with an `Origin`
that is neither `public_origin` nor on the list is refused with 403
`invalid_origin`, before any credential is checked.

**CORS.** Requests from a listed origin get CORS responses on every `/v1/auth`
and `/v1/play` route: preflights for `GET`, `POST` and `DELETE` with
`Authorization` and `Content-Type`, cached for 600 seconds, and
`Access-Control-Allow-Origin: <origin>` with `Vary: Origin` on responses,
errors included. Credentials mode is not supported; send `fetch` with the
default `credentials: "same-origin"` or `"omit"`. No cookies are used. Requests
from other origins get no CORS headers.

**Stream authentication.** Open the returned `game` or `profile` URL with
`new WebSocket(url, "tm-stream-v1")` and `binaryType = "arraybuffer"`. Do not put
the play token in the URL or subprotocol. The server recognizes a browser stream
by a listed `Origin` and no `Authorization` header. It accepts the upgrade, then
waits up to 5 seconds for the first message, which must be a **text** message
whose content is exactly the play-session token (no `Bearer` prefix, no JSON).
After that message the stream is the same ordered binary byte stream as the
loader's; binary stream bytes may follow immediately without waiting for a reply.
The server sends no acknowledgement. For the game stream, the original server
greeting is the first thing it sends.

A browser stream that fails after the upgrade closes with code 1008 and one of
these reasons, which replace the loader's pre-upgrade 403:

| Reason | Meaning |
| --- | --- |
| `invalid_token` | The first message was binary, empty or not a token shape. |
| `invalid_session` | The token is unknown, expired, revoked, or from before a restart. |
| `channel_busy` | The game stream, or all four profile streams, are already open. |
| `auth_timeout` | No first message within 5 seconds. |
| `storage_unavailable` | The server lost storage while waiting. |

A wrong path, a missing `tm-stream-v1` subprotocol, an unlisted Origin, or more
than 32 browser streams waiting for their first message are still refused before
the upgrade (HTTP 403, which a browser reports as a failed connection). Once
authenticated, the close codes in [Failures and logout](#failures-and-logout)
apply unchanged. The token stays reusable for reconnects until the play session
expires or is revoked, exactly as with the header.

Why a first message and not a token in the URL: a URL, even a single-use stream
ticket in one, is written to reverse-proxy and CDN access logs and to browser and
error-reporting tooling, while a WebSocket message is only ever inside TLS. The
first message adds no new credential, endpoint or server state; the browser
already holds the play token it would use to mint tickets. Its cost is a few
seconds of unauthenticated accepted socket, which the deadline and the 32-stream
cap bound. The Origin allowlist is the cross-site WebSocket defense: a page on
another origin cannot open a browser stream at all.

Keep the play token in memory only; never write it, the Supabase tokens or
tickets to `localStorage`, URLs or logs. Use Supabase's browser PKCE flow with a
redirect back to the page in place of the loopback callback; allowlist that page's
redirect URL in Supabase.

## Loader acceptance

- Real browser sign-in, cancel, timeout, refresh and next-launch remembered login.
- Original title/profile load and ordinary COM play through both WSS routes.
- Multiple sequential profile requests and native game reconnect under one play
  session; changed account/password cannot access another save.
- Expiry, logout and server restart become clean native disconnects; no payload
  replay or cross-session profile-key reuse.
- A second launcher gets a conflict; explicit takeover closes the first.
- No credentials in configuration files, process command lines or diagnostic logs.

For local development the server has an explicit `--auth-insecure-loopback` flag,
which advertises `http://127.0.0.1:PORT` and `ws://127.0.0.1:PORT`. Support this only
as an explicit development setting for numeric loopback; hosted mode requires
HTTPS/WSS with normal certificate validation. Raw legacy ports stay loopback-only.

References: [Supabase PKCE](https://supabase.com/docs/guides/auth/sessions/pkce-flow),
[Auth OpenAPI](https://github.com/supabase/auth/blob/master/openapi.yaml),
[token/session lifecycle](https://supabase.com/docs/guides/auth/sessions),
[native OAuth guidance](https://www.rfc-editor.org/info/rfc8252/).
