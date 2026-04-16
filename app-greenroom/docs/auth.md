# Authentication & Token Management

Greenroom uses a **dual token system** to manage Spotify authentication:

1. **OAuth tokens** - for initial authentication and reconnection
2. **librespot session credentials** - for fast reconnection to existing sessions

## Overview

| Token Type | Lifetime | Purpose | Managed By |
|---|---|---|---|
| OAuth `access_token` | 1 hour | Initial auth, reconnection after long disconnections | Greenroom |
| librespot `reusable_auth_credentials` | Session lifetime | Fast reconnection to same session | librespot TokenProvider |

## OAuth Token Flow

### Initial Authentication (Web UI)

1. User opens Greenroom Web UI, clicks "Connect Spotify"
2. Server shows instructions with "Open Spotify Authorization" button
3. User clicks button, Spotify OAuth opens in new tab with PKCE challenge
4. After Spotify auth, the redirect to `127.0.0.1:5588` fails (expected - localhost not accessible in add-on)
5. User copies the **entire URL** from the address bar (or just the code)
6. User pastes URL/code into the form - server extracts code automatically
7. Server exchanges code for tokens via PKCE
8. Credentials saved to `/data/greenroom_token.json`

### Token File Format

```json
{
  "access_token": "...",
  "refresh_token": "...",
  "expires_at": 1234567890,
  "scopes": ["streaming"]
}
```

## OAuth Token Refresh

### When OAuth Refresh Happens

1. **During reconnection** - Before using OAuth to connect, check if expired and refresh if needed
2. **On failure** - If connection fails with "Bad credentials", try refreshing once and retry

### Token Management Strategy

#### OAuth Tokens (Our Responsibility)

- Stored in `/data/greenroom_token.json` (1-hour lifetime)
- Used for initial authentication and fallback when session credentials fail
- Refreshed when about to use an expired token, or after connection failure
- May be stale during long connected sessions (this is fine - session credentials are used instead)

### librespot Session Credentials (librespot's Responsibility)

- Stored in librespot's cache (`reusable_auth_credentials`)
- Session-specific auth blob returned by Spotify after successful OAuth auth
- Used for fast reconnection to same session (avoids re-authenticating with OAuth)
- Managed by TokenProvider via Mercury keymaster API
- Completely separate from OAuth tokens

## Connection Strategy

### Initial Connection (Startup)

On first connection after Greenroom starts:

1. **Always use OAuth tokens** (never cached credentials)
   - Cached credentials from previous runs have a ~5 minute session limit
   - OAuth-based sessions last much longer (30+ minutes typically)
   - Slight overhead (reading file) is worth avoiding the 5-minute flakiness

2. **Refresh OAuth token if expired**
   - Check `expires_at` in token file
   - Refresh proactively before connection if needed

### Reconnection (During Session)

If the connection drops while monitoring:

1. **Try cached librespot session credentials first** (fast path)
   - Uses `reusable_auth_credentials` from librespot's cache
   - These work reliably for reconnections once "primed" by OAuth

2. **Fall back to OAuth tokens** (with refresh if needed)
   - Load OAuth credentials from `/data/greenroom_token.json`
   - Check if expired, refresh proactively if needed
   - Use refreshed token for connection

3. **Reactive refresh on failure**
   - If "Bad credentials" error, try refreshing OAuth token once
   - Retry connection with fresh token
   - Only then clear credentials and notify user

### Session Maintenance

- Session stays alive via WebSocket keepalives
- Reconnect only on connection failures, not token expiry
- OAuth tokens refreshed on-demand when needed (may be stale during long sessions)

## Why Two Token Systems?

**OAuth tokens** are used for the initial "handshake" with Spotify. After successful authentication, Spotify returns **session credentials** that are specific to that session and can be reused for fast reconnection without re-authenticating.

This is similar to how web applications work:

- Initial login uses credentials (OAuth tokens)
- After login, a session cookie is issued (session credentials)
- Subsequent requests use the session cookie, not the original credentials

The dual system provides:

- **Fast reconnection** (cached session credentials)
- **Recovery path** (OAuth refresh when all else fails, on-demand)
- **No unnecessary API calls** (only refresh when needed)

## Auth Revocation

If Spotify returns "Bad credentials" or "invalid_grant" and refresh fails:

1. Credentials are cleared from file
2. HA notification is sent to user
3. Daemon enters demo mode waiting for re-authentication

This handles cases where the user revokes access from their Spotify account settings.

## Why Not librespot-oauth?

We don't directly use the `librespot-oauth` crate because its `OAuthClientBuilder` is designed for desktop apps with localhost callbacks. Instead, we implement PKCE directly using `reqwest` in `web.rs`, which is more appropriate for the Home Assistant add-on environment (behind ingress with manual code/URL entry flow).

`librespot-oauth` is still pulled in transitively via `librespot-core` but not used directly.

## Testing OAuth Flow Locally

The manual code entry flow works without full ingress setup:

1. Run the binary with `GREENROOM_WEB_PORT=8099`
2. Navigate to `http://localhost:8099/`
3. Click "Connect Spotify" - opens instructions page
4. Click "Open Spotify Authorization" - opens Spotify OAuth in a new tab
5. After Spotify auth, the redirect to `127.0.0.1:5588` will fail (expected)
6. Copy the **entire URL** from the address bar (or just the code)
7. Paste into the form - the server will extract the code automatically
8. The credentials will be saved locally