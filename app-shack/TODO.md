# MQTT Command Coverage TODO

This file tracks missing MQTT command implementations across Home Assistant domains.

## Pending Enhancements

### 🔧 Climate Platform - Missing Command Topics

The climate entity has methods for these features but they aren't exposed via MQTT:

| Feature | Method | Missing Command Topic |
|---------|--------|----------------------|
| Fan mode | `async_set_fan_mode()` | `fan_mode_command_topic` |
| Preset mode | `async_set_preset_mode()` | `preset_mode_command_topic` |
| Humidity | `async_set_humidity()` | `humidity_command_topic` |
| Swing mode | `async_set_swing_mode()` | `swing_mode_command_topic` |

**Files to modify:**

- `shim/platforms/climate.py` - Add command topics to discovery config
- `shim/manager.py` - Add handlers in `_route_command` and `_setup_mqtt_subscriptions`

---

### 🔧 Platforms Without MQTT Discovery

These platforms have entity methods but no MQTT discovery implemented (state only, no commands):

#### Humidifier
**Methods available:**

- `async_turn_on()` / `async_turn_off()`
- `async_set_humidity()`
- `async_set_mode()`

**Required command topics:**

- `command_topic` (for ON/OFF)
- `target_humidity_command_topic`
- `mode_command_topic`

**Files to create/modify:**

- `shim/platforms/humidifier.py` - Add discovery and command handling

#### Vacuum
**Methods available:**

- `async_start()`, `async_stop()`, `async_pause()`
- `async_return_to_base()`, `async_locate()`
- `async_set_fan_speed()`

**Required command topics:**

- `command_topic` (for start/stop/pause/return_to_base/locate)
- `fan_speed_command_topic` (if supported)
- `set_fan_speed_topic` (if supported)

**Files to create/modify:**

- `shim/platforms/vacuum.py` - Add discovery and command handling

#### Remote
**Methods available:**

- `async_turn_on()` / `async_turn_off()`
- `async_send_command()`

**Required command topics:**

- `command_topic` (for ON/OFF)
- Additional handling for `send_command` service calls

**Files to create/modify:**

- `shim/platforms/remote.py` - Add discovery and command handling

#### Siren
**Methods available:**

- `async_turn_on()` / `async_turn_off()`

**Required command topics:**

- `command_topic`

**Files to create/modify:**

- `shim/platforms/siren.py` - Add discovery and command handling

---

## Implementation Notes

### Adding New Command Topics

When adding a new command topic, you need to:

1. **In the platform file** (`shim/platforms/{platform}.py`):
   - Add the command topic to `_publish_mqtt_discovery()` method
   - Example: `config["fan_mode_command_topic"] = f"{base_topic}/fan_mode_set"`

2. **In manager.py** (`shim/manager.py`):

   - Add subscription in `_setup_mqtt_subscriptions()`:
     ```python
     ("homeassistant/+/+/fan_mode_set", self._on_entity_command),
     ```
   - Add handler in `_route_command()`:
     ```python
     elif command_type == "fan_mode_set":
         if hasattr(entity, "async_set_fan_mode"):
             await entity.async_set_fan_mode(payload)
         elif hasattr(entity, "set_fan_mode"):
             entity.set_fan_mode(payload)
     ```

3. **In tests** (`tests/test_manager.py`):

   - Add test cases in `TestManagerCommandRouting` class
   - Follow existing patterns for button press, turn_on/off tests

### Topic Naming Convention

Always use **underscore format** for command suffixes:

- ✅ `homeassistant/climate/living_room/mode_set`
- ✅ `homeassistant/fan/bedroom/percentage_set`
- ❌ `homeassistant/water_heater/tank/mode/set` (causes subscription mismatch)

---

## Future Considerations

### Service Calls vs MQTT Commands

Some platforms (like `remote`) use service calls (`remote.send_command`) rather than entity methods. These may require:

- Mapping MQTT commands to service calls in the shim layer
- Or implementing entity methods that call the services

### Dynamic Feature Detection

For platforms like `vacuum` and `fan`, features vary by device. Consider implementing:

- `supported_features` check before publishing command topics
- Dynamic command topic registration based on what's actually supported

---

*Last updated: 2024-04-15*
