# Greenroom

![Greenroom logo](/app-greenroom/logo.png)

Spotify's [2026 policy change](https://developer.spotify.com/blog/2026-02-06-update-on-developer-access-and-platform-security) no longer includes Web API access for non-premium accounts.
This means the standard integrations no longer work even with paid plans like [Basic Family](https://support.spotify.com/us/article/spotify-basic/).

## What It Does

- Monitors playback from any Spotify device on your account
- Publishes real-time track info (artist, track, album art, etc) to Home Assistant via MQTT
- **No Web API needed** — uses the Connect protocol
- Works with Free, Basic, and Premium Spotify accounts

**Note**: Unfortunately it can't provide any playback control (play/pause/next track).

## Quick Start

1. Add this repository to your Home Assistant app store
2. Install and start the **Greenroom** app
3. Open the Greenroom Web UI
4. Click "Connect Spotify" and authorize your account
5. A `sensor.greenroom` entity will appear in Home Assistant

## Alternatives

You can use the Spotify Connect plugin for Music Assistant as the active playing device, then forward that stream to your players.  This works but I found it awkward to setup each time.