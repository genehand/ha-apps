# Authentication & Token Management

Greenroom uses a **dual token system** to manage Spotify authentication:

1. **OAuth tokens** - for initial authentication and reconnection
2. **librespot session credentials** - for fast reconnection to existing sessions

## Overview

| Token Type | Lifetime | Purpose | Managed By |
|---|---|---|---|
| OAuth `access_token` | 1 hour | Initial auth, reconnection after long disconnections | Greenroom (with background refresh) |
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

## Background OAuth Refresh

To ensure seamless reconnection after long disconnections (e.g., overnight), Greenroom spawns a background task that:

- Runs continuously, independent of Spotify connection state
- Checks OAuth token expiry every 5 minutes
- Refreshes when token expires within 10 minutes
- Saves refreshed tokens to the credentials file
- Operates silently (no notifications to the main daemon)

This ensures OAuth tokens are always fresh for reconnection attempts, even after 8+ hours of disconnection. When the user starts playing Spotify in the morning, the reconnection attempt uses a valid OAuth token (or falls back to cached librespot session credentials).

## Token Management Strategy

### OAuth Tokens (Our Responsibility)

- Stored in `/data/greenroom_token.json` (1-hour lifetime)
- Used for initial authentication and reconnection after session drops
- Background task checks expiry every 5 minutes, refreshes when within 10 min of expiry
- Proactive refresh before expiry + reactive refresh on connection failure

### librespot Session Credentials (librespot's Responsibility)

- Stored in librespot's cache (`reusable_auth_credentials`)
- Session-specific auth blob returned by Spotify after successful OAuth auth
- Used for fast reconnection to same session (avoids re-authenticating with OAuth)
- Managed by TokenProvider via Mercury keymaster API
- Completely separate from OAuth tokens

## Reconnection Flow

1. **Try cached librespot session credentials** (fast path)
   - Uses `reusable_auth_credentials` from librespot's cache
   - Works if the session is still valid on Spotify's side

2. **Fall back to OAuth tokens from file** (with refresh if needed)
   - Load OAuth credentials from `/data/greenroom_token.json`
   - Check if expired, refresh proactively if needed
   - Use refreshed token for connection

3. **Reactive refresh on failure**
   - If "Bad credentials" error, try refreshing OAuth token once
   - Retry connection with fresh token
   - Only then clear credentials and notify user

4. **Session maintenance**
   - Session stays alive via WebSocket keepalives
   - Reconnect only on connection failures, not token expiry
   - OAuth tokens kept fresh by background task even when disconnected

## Why Two Token Systems?

**OAuth tokens** are used for the initial "handshake" with Spotify. After successful authentication, Spotify returns **session credentials** that are specific to that session and can be reused for fast reconnection without re-authenticating.

This is similar to how web applications work:

- Initial login uses credentials (OAuth tokens)
- After login, a session cookie is issued (session credentials)
- Subsequent requests use the session cookie, not the original credentials

The dual system provides:

- **Fast reconnection** (cached session credentials)
- **Recovery path** (OAuth refresh when all else fails)
- **Seamless overnight operation** (background OAuth refresh)

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