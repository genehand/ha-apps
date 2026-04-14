# Greenroom

![Greenroom logo](/app-greenroom/logo.png)

Spotify's [2026 policy change](https://developer.spotify.com/blog/2026-02-06-update-on-developer-access-and-platform-security) no longer includes Web API access for non-premium accounts.
This means the standard integrations no longer work even with paid plans like [Basic Family](https://support.spotify.com/us/article/spotify-basic/).

## What It Does

- Monitors playback from any Spotify device on your account
- Publishes real-time track info (artist, track, album art, etc) to Home Assistant via MQTT
- **No Web API needed** - uses the Connect protocol
- Works with Free, Basic, and Premium Spotify accounts

**Note**: Unfortunately it can't provide any playback control (play/pause/next track).

## Quick Start

1. Add this repository to your Home Assistant app store
2. Install and start the **Greenroom** app (and the Mosquitto MQTT broker)
3. Open the Greenroom Web UI
4. Click "Connect Spotify" and authorize your account
5. A `sensor.greenroom` entity will appear in Home Assistant

## Media player entity

Media players aren't supported by the MQTT integration, but we can use [`media_player.template`](https://github.com/sennevds/media_player.template) with this sensor:

```yaml
media_player:
  - platform: media_player_template
    media_players:
      greenroom:
        friendly_name: Greenroom
        device_class: speaker
        media_content_type_template: music
        value_template: "{{ iif(has_value('sensor.greenroom'), states('sensor.greenroom'), 'idle') }}"
        current_source_template: "{{ state_attr('sensor.greenroom', 'source') }}"
        current_position_template: "{{ state_attr('sensor.greenroom', 'media_position') }}"
        media_duration_template: "{{ state_attr('sensor.greenroom', 'media_duration') }}"
        title_template: "{{ state_attr('sensor.greenroom', 'media_title') }}"
        album_template: "{{ state_attr('sensor.greenroom', 'media_album_name') }}"
        artist_template: "{{ state_attr('sensor.greenroom', 'media_artist') }}"
        media_image_url_template: "{{ state_attr('sensor.greenroom', 'media_image_url') }}"
        media_image_url_remotely_accessible: true
```

## Alternatives

You can use the Spotify Connect plugin for Music Assistant as the active playing device, then forward that stream to your players.  This works and also supports playback control but I found it awkward to setup each time.