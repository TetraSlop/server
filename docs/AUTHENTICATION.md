# Hosted authentication prototype

Discord is the selected login method, using Supabase Auth for the browser flow.
The server has an opt-in launcher gateway. It verifies access tokens
against the configured Supabase project's Auth service, exchanges a short-lived
one-use launch ticket for a revocable play session, and carries the original
game/profile byte streams over authenticated WebSockets. Both legacy handlers
enforce the play session's account and expiry before operating on game state.
Profile encryption contexts are scoped to that play-session generation.

The first authenticated **Play** request automatically creates a saved game
account for a verified Supabase user with a linked Discord identity. Browser
sign-in alone does not contact tmserver. Ownership is stored by project issuer
and Supabase UUID, alongside the account in one atomic commit. Returning users
keep that account after server restarts and Discord name changes. A new account
receives the existing starter policy: ten cards and 10,000 gil. Supabase identities
do not grant admin rights.

Legacy operator bindings can be imported without changing an existing save.
Hosted accounts require an authenticated stream even on the private raw game and
profile listeners; returned compatibility credentials alone cannot log them in.
Unbound local test accounts and the local operator panel keep their existing flow.

On Play, the server uses Discord's display name from the verified Supabase
identity (`global_name`, falling back to the username). It saves the label before
the native client reads its profiles, including for existing pilot accounts.
The current gameplay name field is limited to 15 printable ASCII bytes: accents
are normalized, unsupported glyphs are omitted, and longer names are shortened.
If no usable Discord name exists, a new account uses `PlayerN` and an existing
account retains its saved label. Connected or busy
accounts keep their current name until a later idle Play; renewal never renames
an active player. Name changes cannot change ownership, renewal or logout rights.
Supabase stores provider profile data from Discord sign-in; a later Discord name
change may require signing in again to refresh that data.
Provider field mapping follows [Supabase's Discord adapter](https://github.com/supabase/auth/blob/master/internal/api/provider/discord.go).

## Configure

Install the optional dependencies:

```powershell
.venv/Scripts/python -m pip install -e ".[auth]"
```

Copy [examples/auth.json](../examples/auth.json) to an operator-managed location.
Set the actual project URL, public publishable/anon key, public HTTPS gateway
origin. Never provide a service-role/secret key. `auto_register` defaults to
`true`; no bindings or per-player configuration are needed. Set it to `false`
and restart to close new registrations while allowing existing saved owners to
play. `--accounts N` controls the local test pool, not hosted registrations.

To retain an existing pilot save, keep its old `bindings` object for the first
startup with this version. The gateway imports those links into the save; then
the bindings may be removed from configuration. A link cannot reassign a saved
owner or take another identity's account. Legacy bindings must refer to enabled
local accounts (`tmserver --list-accounts`); disconnect the account before
importing its ownership. Do not attach saves by email or display name.

JSON saves need no manual migration. PostgreSQL operators must stop the server
and run `tmserver-db upgrade` to revision `0003_identities` before restarting.
The migration retains account data and adds the identity journal. Durable
ownership must travel with the save when moving to a new server.

```powershell
.venv/Scripts/tmserver --state var/local_state.json --auth-config C:/private/tm-auth.json --auth-port 8081
```

This starts a loopback-only HTTP/WebSocket listener. The configured public origin
must reach it through a TLS reverse proxy forwarding the original Host and
WebSocket upgrade. Expose only that HTTPS/WSS gateway; original game/profile ports
remain private loopback services. Do not route this public origin to the separate
admin panel. The HTTP owner is shared with the existing admin listener, with
bounded startup/shutdown; both can run in the same game-owner process.

The ordinary server defaults to practice without stakes. Add `--wagers` when
testing COM matches with a gil wager or double-up selected in the native menu;
without it, nonzero stakes are rejected. Wager support persists deductions and
settlement in the selected save. It is independent of authentication.

For an explicitly local loader test, add `--auth-insecure-loopback`. It advertises
the actual numeric loopback address/port using HTTP/WS. Supabase verification
still requires the real configured HTTPS project: there is no production fake
identity endpoint, shared development bearer token or verification bypass.

## Discord setup

1. Create an application in the Discord Developer Portal. Register
   `https://<project-ref>.supabase.co/auth/v1/callback` as its OAuth2 redirect URI.
2. In Supabase **Authentication → Sign In / Providers → Discord**, enable Discord
   and save the application's client ID and client secret. Disable other sign-in
   methods for this project. The Discord client secret stays in Supabase; neither
   tmserver nor tmloader needs it. No Discord bot or bot token is required.
3. In Supabase's redirect URL configuration, allowlist the loader's loopback
   callback pattern, `http://127.0.0.1:*/callback/*`. The current loader uses a
   random port and callback nonce. This is a separate redirect: Discord returns to Supabase,
   then Supabase returns an authorization code to the loader. The loader uses
   PKCE to exchange that code, as described in the loader contract.
4. Leave `auto_register` enabled. A user's first **Play** creates their account;
   later launches reuse it without operator enrollment.

The gateway advertises `provider: "discord"`. New registration requires a Discord
entry in the verified Auth response's `identities` collection; user-editable
metadata cannot satisfy that requirement. Existing durable owners and explicitly
imported legacy bindings retain access. Available login methods are configured
in Supabase. A public project key identifies the project, not the player.

See the official [Supabase Discord setup guide](https://supabase.com/docs/guides/auth/social-login/auth-discord).

## Contract and testing

Give the loader owner [LOADER_AUTH_SPEC.md](LOADER_AUTH_SPEC.md). It defines the
raw Supabase PKCE exchange, versioned launcher endpoints, exact responses,
WebSocket stream framing, refresh/revoke behavior and acceptance checklist.

```powershell
.venv/Scripts/python -m pip install -e ".[dev,auth]"
.venv/Scripts/python -m tools.check_tests auth
.venv/Scripts/python -m tools.build_wheel
.venv/Scripts/python -m tools.check_wheel dist/tmserver-0.1.0-py3-none-any.whl --auth
```

Tests substitute only the provider's verified identity boundary. They exercise
actual local HTTP/WebSocket listeners, original encrypted game/profile requests,
account isolation, tickets, expiry, renewal, revocation and shutdown. Separate
HTTP mock tests cover Supabase's exact endpoint/headers, public-key validation,
rejected credentials, timeouts and provider failure. This does not constitute a
live Supabase/browser sign-in or rendered-game acceptance.

## Deliberate prototype limits

- Ownership is durable, but identity linking/reassignment, account deletion and
  recovery administration remain future account-management work. Do not edit
  ownership journals by hand or use a display name to claim an old save.
- Tickets and play sessions are in memory under one process. A restart revokes
  them. Multiple workers/hosts and durable session coordination are not supported.
- One active play session per account; one game and four profile streams each.
  Lost session-creation acknowledgements can be recovered by explicit own-account
  revocation and a fresh ticket. No transparent match resume or payload replay.
- Revoking a play session closes both transports. Supabase provider logout and
  game-session logout are separate operations; provider changes are checked on
  the next ticket or renewal, not continuously during every game message.
- The user has confirmed real Discord sign-in and display-name import through
  the separately owned loader. A captured wagered COM request completes an encrypted
  hosted match replay, including settlement, exit and saved balance. A complete
  native hosted match still needs user acceptance.
- Hosted TLS/proxy configuration and public admission rate limits remain before
  public deployment. The pilot uses the user's existing Supabase project; no
  public server deployment has been created.
