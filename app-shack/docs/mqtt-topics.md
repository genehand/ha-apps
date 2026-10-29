# MQTT Topic Naming

MQTT topics used for Home Assistant discovery must follow specific naming conventions:

## Topic Structure

Topics follow the pattern: `homeassistant/<platform>/<entity_id>/<suffix>`

Examples:
- Discovery config: `homeassistant/sensor/living-room-temperature/config`
- State topic: `homeassistant/sensor/living-room-temperature/state`
- Command topic: `homeassistant/switch/living-room/set`

## Naming Rules

1. **Use dashes (`-`) not underscores (`_`) or colons (`:`)** in MQTT topic names
   - Entity ID: `sensor.living_room` (Home Assistant format)
   - MQTT topic: `living-room` (MQTT format)
   - Colons from some integrations (e.g., `device:001:section`) also convert to dashes: `device-001-section`
   - *Why?* Home Assistant doesn't handle colons well in MQTT discovery. Dashes are safe while colons can cause issues. The Entity class has an `mqtt_object_id` property that handles this automatically.

2. **Conversion function**: Use `get_mqtt_entity_id()` from `shim/entity.py`:

   ```python
   from shim.entity import get_mqtt_entity_id

   entity_id = "fan.living_room"
   mqtt_id = get_mqtt_entity_id(entity_id)  # Returns: "living-room"
   topic = f"homeassistant/fan/{mqtt_id}/state"

   # Also handles colons from integrations like Dreo
   entity_id = "fan.device:001:section"
   mqtt_id = get_mqtt_entity_id(entity_id)  # Returns: "device-001-section"
   ```

3. **Reverse conversion**: When receiving commands from HA, convert dashes back to underscores:

   ```python
   # MQTT topic: homeassistant/fan/living-room/set
   parts = topic.split("/")
   object_id = parts[2].replace("-", "_")  # "living-room" -> "living_room"
   entity_id = f"{parts[1]}.{object_id}"   # "fan.living_room"
   ```

**Why dashes?** MQTT topic naming conventions recommend avoiding underscores in topic
names. Dashes are more readable and standard in MQTT ecosystems. Home Assistant's
MQTT discovery format expects this convention.
