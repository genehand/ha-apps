# Soloist Bridge

Home Assistant app that runs a [Soloist](https://developer.spotify.com/documentation/soloist) Spotify Connect device and bridges its WebSocket API with MQTT.

Publishes currently playing track info and provides **full playback control** (play, pause, skip, seek, volume, shuffle, repeat, queue).

## Why the bridge?

Spotify's [2026 policy change](https://developer.spotify.com/blog/2026-02-06-update-on-developer-access-and-platform-security) no longer includes Web API access for non-premium accounts.  
This means standard integrations don't work with other paid plans like [Basic Family](https://support.spotify.com/us/article/spotify-basic/).

Soloist is an official Spotify client that:

- Advertises itself as a Spotify Connect device on your local network
- Plays audio through PulseAudio/PipeWire (a silent null sink by default, or from your HA host)
- Exposes a local WebSocket API for observing playback and sending control commands

## Requirements

- A **paid Spotify plan (Basic or Premium)** to generate a [Soloist API key](https://developer.spotify.com/dashboard/soloist)
- MQTT broker

## Quick Start

1. Add this repository to your Home Assistant app store
2. Install the **Soloist** app
3. Configure the `soloist_api_key`
4. Start the app
4. Open the Spotify app on the same network and select the **Soloist** device from the device picker — this logs the device into Spotify Connect (one-time pairing)
5. New entities appear in Home Assistant:
   - `sensor.soloist` — playback state + media attributes
   - `switch.soloist_active` — make Soloist the active Connect device
   - `switch.soloist_power` — reporting gate (see [Power Switch](#power-switch))

## Media Player Entity

Home Assistant's MQTT integration doesn't support `media_player` entities.  This app publishes a sensor plus MQTT command topics. Install [Template Media Player](https://github.com/EuleMitKeule/template-media-player) via HACS and add this to your `configuration.yaml` to get a fully controllable media player.  

Note that volume levels are for the device where HA is running, so you may want to leave these out when used as a passive listener.

```yaml
media_player:
  - platform: template_media_player
    media_players:
      soloist:
        # required field
        global_template: "{# #}"

        name: Soloist
        unique_id: soloist
        icon: mdi:spotify

        device_class: speaker
        state: "{{ states('sensor.soloist') }}"
        attributes:
          source: "{{ state_attr('sensor.soloist', 'source') }}"
          entity_picture: "{{ state_attr('sensor.soloist', 'media_image_url') }}"

          media_content_type: music
          media_title: "{{ state_attr('sensor.soloist', 'media_title') }}"
          media_album_name: "{{ state_attr('sensor.soloist', 'media_album_name') }}"
          media_artist: "{{ state_attr('sensor.soloist', 'media_artist') }}"
          media_content_id: "{{ state_attr('sensor.soloist', 'media_content_id') }}"
          media_position: "{{ state_attr('sensor.soloist', 'media_position') }}"
          media_position_updated_at: "{{ state_attr('sensor.soloist', 'media_position_updated_at') }}"
          media_duration: "{{ state_attr('sensor.soloist', 'media_duration') }}"

          volume_level: "{{ state_attr('sensor.soloist', 'volume') }}"
          is_volume_muted: "{{ state_attr('sensor.soloist', 'is_volume_muted') }}"
          shuffle: "{{ state_attr('sensor.soloist', 'shuffle') }}"
          repeat: "{{ state_attr('sensor.soloist', 'repeat') }}"

        service_scripts:
          media_play:
            - service: mqtt.publish
              data:
                topic: soloist/soloist/cmd/play
                payload: ""
          media_pause:
            - service: mqtt.publish
              data:
                topic: soloist/soloist/cmd/pause
                payload: ""
          media_next_track:
            - service: mqtt.publish
              data:
                topic: soloist/soloist/cmd/next
                payload: ""
          media_previous_track:
            - service: mqtt.publish
              data:
                topic: soloist/soloist/cmd/previous
                payload: ""
          media_seek:
            - service: mqtt.publish
              data:
                topic: soloist/soloist/cmd/seek
                payload: "{{ position }}"
          media_stop:
            - service: mqtt.publish
              data:
                topic: soloist/soloist/cmd/pause
                payload: ""
          volume_set:
            - service: mqtt.publish
              data:
                topic: soloist/soloist/cmd/volume
                payload: "{{ (volume * 100) | round(0) | int }}"
          volume_up:
            - service: mqtt.publish
              data:
                topic: soloist/soloist/cmd/volume_up
                payload: ""
          volume_down:
            - service: mqtt.publish
              data:
                topic: soloist/soloist/cmd/volume_down
                payload: ""
          volume_mute:
            - service: mqtt.publish
              data:
                topic: soloist/soloist/cmd/volume_mute
                payload: "{{ 'ON' if mute else 'OFF' }}"
          shuffle_set:
            - service: mqtt.publish
              data:
                topic: soloist/soloist/cmd/shuffle
                payload: "{{ 'ON' if shuffle else 'OFF' }}"
          repeat_set:
            - service: mqtt.publish
              data:
                topic: soloist/soloist/cmd/repeat
                payload: "{{ repeat }}"
          play_media:
            - service: mqtt.publish
              data:
                topic: soloist/soloist/cmd/play
                payload: "{{ media_id }}"
          turn_on:
            - service: mqtt.publish
              data:
                topic: soloist/soloist/cmd/activate
                payload: ""
          turn_off:
            - service: mqtt.publish
              data:
                topic: soloist/soloist/cmd/deactivate
                payload: ""
```

Replace `soloist/soloist/...` topics with `soloist/<mqtt_device_id>/...` if you changed `mqtt_device_id` (defaults to the slugified device name).

## Power Switch

`switch.soloist_power` is a **reporting-only** switch — it never pauses
playback or stops the soloist daemon, it only gates the reported state:

- **Off**: the sensor always reports `idle`, whatever the device is doing, and
  the media attributes (title, artist, position, source, ...) are published as
  `null` so no stale track info lingers. Device-level attributes (volume,
  shuffle, repeat) stay populated.
- **On**: normal reporting resumes — except that if the device is paused at
  the moment the switch is turned on, the sensor stays `idle` until playback
  actually starts (`playing`), and only then reports `paused` states normally
  again (until the switch is turned off).

The power state resets to off when the bridge restarts. To control the audio,
use the play/pause commands (or the Spotify app) — the power switch just hides
playback from Home Assistant.

While **off**, the bridge publishes nothing further to MQTT (the state and
attributes are static), so soloist events — e.g. `position_sync` ticks during
background playback — generate no MQTT traffic. Turning the switch back on
republishes the full state immediately.

## Audio

To play audio through your HA host, set `audio: true` in `config.yaml` (TODO: this is a [config file](https://developers.home-assistant.io/docs/apps/configuration#optional-configuration-options) change, not an option). The supervisor injects the host audio connection and the app uses it instead of the null sink.

## Soloist Build Expiry

Soloist binaries are only valid for [90 days](https://developer.spotify.com/documentation/soloist/reference/downloads-and-updates) from their build date. The bridge downloads it at startup, refreshing it whenever:

- the binary is missing (first run),
- it's older than **7 days**, or
- the daemon exits with code 10 (expired build).

If a running build expires mid-session it automatically refreshes and restarts.