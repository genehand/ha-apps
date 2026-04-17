# Greenroom

Spotify's [2026 policy change](https://developer.spotify.com/blog/2026-02-06-update-on-developer-access-and-platform-security) no longer includes Web API access for non-premium accounts.
This means the standard integrations no longer work even with paid plans like [Basic Family](https://support.spotify.com/us/article/spotify-basic/).

## What It Does

- Monitors playback from any Spotify device on your account
- Publishes real-time track info (artist, track, album art, etc) to Home Assistant via MQTT
- **No Web API needed** - uses the Connect protocol with [librespot](https://github.com/librespot-org/librespot)
- Works with Free, Basic, and Premium Spotify accounts

### Limitations

- Unfortunately this method doesn't provide playback control (play/pause/next track)
- Because this isn't a player device, we send keepalives to keep Spotify from disconnecting
    - An Active switch is included to disconnect when not in use

## Quick Start

1. Add this repository to your Home Assistant app store
2. Install and start the **Greenroom** app (and the Mosquitto MQTT broker)
3. Open the Greenroom Web UI
4. Click "Connect Spotify" and authorize your account
5. New entities available in Home Assistant:
  - `sensor.greenroom`
  - `switch.greenroom_active`

## Media player entity

Media players aren't supported by the MQTT integration, but you can use [Template Media Player](https://github.com/EuleMitKeule/template-media-player) with this sensor:

```yaml
media_player:
  - platform: template_media_player
    media_players:
      greenroom:
        # required field
        global_template: "{# #}"

        name: Greenroom
        unique_id: greenroom

        device_class: speaker
        state: "{{ iif(has_value('sensor.greenroom'), states('sensor.greenroom'), 'idle') }}"
        attributes:
          source: "{{ state_attr('sensor.greenroom', 'source') }}"
          entity_picture: "{{ state_attr('sensor.greenroom', 'media_image_url') }}"

          media_content_type: music
          media_title: "{{ state_attr('sensor.greenroom', 'media_title') }}"
          media_album_name: "{{ state_attr('sensor.greenroom', 'media_album_name') }}"
          media_artist: "{{ state_attr('sensor.greenroom', 'media_artist') }}"
          media_content_id: "{{ state_attr('sensor.greenroom', 'media_content_id') }}"
          media_position: "{{ state_attr('sensor.greenroom', 'media_position') }}"
          media_position_updated_at: "{{ state_attr('sensor.greenroom', 'media_position_updated_at') }}"
          media_duration: "{{ state_attr('sensor.greenroom', 'media_duration') }}"

          volume_level: "{{ state_attr('sensor.greenroom', 'volume') }}"
          is_volume_muted: "{{ state_attr('sensor.greenroom', 'is_volume_muted') }}"
          shuffle: "{{ state_attr('sensor.greenroom', 'shuffle') }}"
          repeat: "{{ state_attr('sensor.greenroom', 'repeat') }}"
```

## Alternatives

You could use the Spotify Connect plugin for Music Assistant as the active playing device, then forward that stream to your players.  This works and supports playback control, but I found it awkward to setup each time.