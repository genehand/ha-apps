use regex::Regex;
use serde_json::Value;
use std::collections::HashSet;
use tracing::debug;

lazy_static::lazy_static! {
    static ref ENTITY_PATTERN: Regex = Regex::new(r"^[\w]+\.[\w]+$").unwrap();
    static ref TEMPLATE_PATTERN: Regex = Regex::new(r#"states\[(?:'|")([\w]+\.[\w]+)(?:'|")\]"#).unwrap();
    static ref SUFFIX_ENTITY_PATTERN: Regex = Regex::new(r"^[\w]+_entity$").unwrap();
    static ref WALLPANEL_PLACEHOLDER_PATTERN: Regex = Regex::new(
        r#"\$\{entity:([\w]+\.[\w]+)\}"#
    ).unwrap();
}

pub fn parse_lovelace_entities(
    data: &Value,
    entities: &mut HashSet<String>,
    filter_rules: &mut Vec<Value>,
) {
    match data {
        Value::Object(map) => {
            // Handle custom:auto-entities card filters
            if map.get("type").and_then(|v| v.as_str()) == Some("custom:auto-entities") {
                if let Some(filter) = map.get("filter") {
                    if let Some(include) = filter.get("include").and_then(|v| v.as_array()) {
                        for rule in include {
                            match rule {
                                // Case 1: Rule is a string (e.g., "light.kitchen_*")
                                Value::String(s) => {
                                    entities.insert(s.clone());
                                    continue;
                                }
                                // Case 2 & 3: Rule is a dict
                                Value::Object(rule_map) => {
                                    // Case 2: entity_id filter
                                    if let Some(entity_id) =
                                        rule_map.get("entity_id").and_then(|v| v.as_str())
                                    {
                                        entities.insert(entity_id.to_string());
                                    }

                                    // Case 3: Complex filters
                                    let complex_keys = [
                                        "attributes",
                                        "domain",
                                        "group",
                                        "integration",
                                        "device",
                                        "area",
                                    ];
                                    let mut supported_filters = serde_json::Map::new();

                                    for key in &complex_keys {
                                        if let Some(val) = rule_map.get(*key) {
                                            supported_filters.insert(key.to_string(), val.clone());
                                        }
                                    }

                                    if !supported_filters.is_empty() {
                                        filter_rules.push(Value::Object(supported_filters));
                                    }

                                    // Handle "or" conditions
                                    if let Some(or_conditions) =
                                        rule_map.get("or").and_then(|v| v.as_array())
                                    {
                                        for condition in or_conditions {
                                            filter_rules.push(condition.clone());
                                        }
                                    }
                                }
                                _ => {}
                            }
                        }
                    }
                }
            }

            for (key, value) in map {
                // Check for entity/entity_id keys
                if key == "entity" || key == "entity_id" {
                    if let Some(s) = value.as_str() {
                        validate_and_add(s, entities);
                    }
                }
                // Check for entities array
                else if key == "entities" && value.is_array() {
                    if let Some(arr) = value.as_array() {
                        for item in arr {
                            if let Some(s) = item.as_str() {
                                validate_and_add(s, entities);
                            } else if let Some(obj) = item.as_object() {
                                if let Some(entity) = obj.get("entity").and_then(|v| v.as_str()) {
                                    validate_and_add(entity, entities);
                                }
                            }
                        }
                    }
                }
                // Check for any key ending in _entity (e.g. bubble-card colors,
                // wallpanel configs, and other card types that use this pattern)
                else if SUFFIX_ENTITY_PATTERN.is_match(key) {
                    if let Some(s) = value.as_str() {
                        validate_and_add(s, entities);
                    }
                }
                // Check for template patterns and wallpanel placeholders in string values
                else if let Some(s) = value.as_str() {
                    for cap in TEMPLATE_PATTERN.captures_iter(s) {
                        if let Some(entity_id) = cap.get(1) {
                            validate_and_add(entity_id.as_str(), entities);
                        }
                    }
                    for cap in WALLPANEL_PLACEHOLDER_PATTERN.captures_iter(s) {
                        if let Some(entity_id) = cap.get(1) {
                            validate_and_add(entity_id.as_str(), entities);
                        }
                    }
                }

                // Recurse into nested objects and arrays
                parse_lovelace_entities(value, entities, filter_rules);
            }
        }
        Value::Array(arr) => {
            for item in arr {
                parse_lovelace_entities(item, entities, filter_rules);
            }
        }
        _ => {}
    }
}

fn validate_and_add(entity_id: &str, entities: &mut HashSet<String>) {
    if ENTITY_PATTERN.is_match(entity_id) {
        entities.insert(entity_id.to_string());
    }
}

pub fn resolve_rules_and_update_entities(
    all_states: &Value,
    rules: &[Value],
    entities: &mut HashSet<String>,
) {
    let count_before = entities.len();

    if let Some(states_map) = all_states.as_object() {
        for (entity_id, state_obj) in states_map {
            for rule in rules {
                if let Some(rule_map) = rule.as_object() {
                    let mut matches = true;

                    // Check domain filter
                    if let Some(domain) = rule_map.get("domain").and_then(|v| v.as_str()) {
                        if domain.ends_with("/") {
                            if !matches_filter(entity_id, domain) {
                                matches = false;
                            }
                        } else if !entity_id.starts_with(&format!("{}.", domain)) {
                            matches = false;
                        }
                    }

                    // Check entity_id filter
                    if matches {
                        if let Some(entity_filter) =
                            rule_map.get("entity_id").and_then(|v| v.as_str())
                        {
                            if !matches_filter(entity_id, entity_filter) {
                                matches = false;
                            }
                        }
                    }

                    // Check attributes filter
                    if matches {
                        if let Some(rule_attrs) = rule_map.get("attributes") {
                            let state_attrs = state_obj.get("a").and_then(|v| v.as_object());
                            if !check_attribute_match(state_attrs, rule_attrs) {
                                matches = false;
                            }
                        }
                    }

                    if matches {
                        entities.insert(entity_id.clone());
                        break;
                    }
                }
            }
        }
    }

    let added_count = entities.len() - count_before;
    if added_count > 0 {
        debug!(
            "Resolved {} new entities from auto-entities attribute/domain rules.",
            added_count
        );
    }
}

fn matches_filter(entity_id: &str, filter: &str) -> bool {
    // Check for regex pattern: /^/pattern/$/
    if filter.starts_with("/") && filter.ends_with("/") && filter.len() > 2 {
        let pattern = &filter[1..filter.len() - 1];
        if let Ok(regex) = Regex::new(pattern) {
            return regex.is_match(entity_id);
        }
    }

    // Convert glob pattern to regex
    let regex_pattern =
        regex::escape(&filter.replace("*", "__STAR__").replace("?", "__QUESTION__"))
            .replace("__STAR__", ".*")
            .replace("__QUESTION__", ".");

    if let Ok(regex) = Regex::new(&format!("^{}$", regex_pattern)) {
        return regex.is_match(entity_id);
    }

    entity_id == filter
}

fn check_attribute_match(
    state_attrs: Option<&serde_json::Map<String, Value>>,
    rule_attrs: &Value,
) -> bool {
    let rule_map = match rule_attrs.as_object() {
        Some(m) => m,
        None => return false,
    };

    let state_map = match state_attrs {
        Some(m) => m,
        None => return false,
    };

    for (key, value_pattern) in rule_map {
        let actual_value = match state_map.get(key) {
            Some(v) => v,
            None => return false,
        };

        // Check for wildcard pattern in value
        if let Some(pattern_str) = value_pattern.as_str() {
            if pattern_str.contains("*") {
                let regex_pattern =
                    regex::escape(&pattern_str.replace("*", "__STAR__")).replace("__STAR__", ".*");
                if let Ok(regex) = Regex::new(&format!("^{}$", regex_pattern)) {
                    if !regex.is_match(&actual_value.to_string()) {
                        return false;
                    }
                }
            } else if pattern_str.starts_with("/")
                && pattern_str.ends_with("/")
                && pattern_str.len() > 2
            {
                // Regex pattern
                let regex_str = &pattern_str[1..pattern_str.len() - 1];
                if let Ok(regex) = Regex::new(regex_str) {
                    if !regex.is_match(&actual_value.to_string()) {
                        return false;
                    }
                }
            } else {
                // Exact match
                if actual_value != value_pattern {
                    return false;
                }
            }
        } else if actual_value != value_pattern {
            return false;
        }
    }

    true
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn test_parse_lovelace_entities_simple() {
        let data = json!({
            "type": "entities",
            "entities": ["light.living_room", "switch.kitchen"]
        });
        let mut entities = HashSet::new();
        let mut filter_rules = Vec::new();

        parse_lovelace_entities(&data, &mut entities, &mut filter_rules);

        assert!(entities.contains("light.living_room"));
        assert!(entities.contains("switch.kitchen"));
        assert_eq!(entities.len(), 2);
        assert!(filter_rules.is_empty());
    }

    #[test]
    fn test_parse_lovelace_entities_with_entity_key() {
        let data = json!({
            "type": "custom:bubble-card",
            "entity": "climate.living_room"
        });
        let mut entities = HashSet::new();
        let mut filter_rules = Vec::new();

        parse_lovelace_entities(&data, &mut entities, &mut filter_rules);

        assert!(entities.contains("climate.living_room"));
        assert_eq!(entities.len(), 1);
    }

    #[test]
    fn test_parse_lovelace_entities_bubble_card_pattern() {
        let data = json!({
            "1_entity": "light.bedroom",
            "2_entity": "switch.office"
        });
        let mut entities = HashSet::new();
        let mut filter_rules = Vec::new();

        parse_lovelace_entities(&data, &mut entities, &mut filter_rules);

        assert!(entities.contains("light.bedroom"));
        assert!(entities.contains("switch.office"));
        assert_eq!(entities.len(), 2);
    }

    #[test]
    fn test_parse_lovelace_entities_from_template() {
        let data = json!({
            "template": "{{ states['light.kitchen'].state }} and {{ states['sensor.temperature'].state }}"
        });
        let mut entities = HashSet::new();
        let mut filter_rules = Vec::new();

        parse_lovelace_entities(&data, &mut entities, &mut filter_rules);

        assert!(entities.contains("light.kitchen"));
        assert!(entities.contains("sensor.temperature"));
        assert_eq!(entities.len(), 2);
    }

    #[test]
    fn test_parse_lovelace_entities_with_auto_entities() {
        let data = json!({
            "type": "custom:auto-entities",
            "filter": {
                "include": [
                    {
                        "domain": "light",
                        "attributes": {"brightness": ">0"}
                    },
                    "light.living_room_*"
                ]
            }
        });
        let mut entities = HashSet::new();
        let mut filter_rules = Vec::new();

        parse_lovelace_entities(&data, &mut entities, &mut filter_rules);

        // The entity_id pattern should be extracted
        assert!(entities.contains("light.living_room_*"));
        // Complex filters should be added to filter_rules
        assert_eq!(filter_rules.len(), 1);
        let rule = filter_rules[0].as_object().unwrap();
        assert!(rule.contains_key("domain"));
        assert!(rule.contains_key("attributes"));
    }

    #[test]
    fn test_parse_lovelace_entities_nested() {
        let data = json!({
            "cards": [
                {
                    "entity": "sensor.nested"
                }
            ]
        });
        let mut entities = HashSet::new();
        let mut filter_rules = Vec::new();

        parse_lovelace_entities(&data, &mut entities, &mut filter_rules);

        assert!(entities.contains("sensor.nested"));
        assert_eq!(entities.len(), 1);
    }

    #[test]
    fn test_parse_lovelace_entities_invalid_entity_id() {
        let data = json!({
            "entities": ["invalid_entity", "also.invalid.still"]
        });
        let mut entities = HashSet::new();
        let mut filter_rules = Vec::new();

        parse_lovelace_entities(&data, &mut entities, &mut filter_rules);

        // Invalid entity IDs should not be added
        assert!(!entities.contains("invalid_entity"));
        assert!(!entities.contains("also.invalid.still"));
        assert!(entities.is_empty());
    }

    #[test]
    fn test_matches_filter_exact_match() {
        assert!(matches_filter("light.kitchen", "light.kitchen"));
        assert!(!matches_filter("light.kitchen", "light.living_room"));
    }

    #[test]
    fn test_matches_filter_wildcard() {
        assert!(matches_filter("light.kitchen_main", "light.kitchen_*"));
        assert!(matches_filter("light.kitchen_ceiling", "light.kitchen_*"));
        assert!(!matches_filter("light.living_room", "light.kitchen_*"));

        // Multiple wildcards
        assert!(matches_filter("light.kitchen_main_top", "light.*_*"));

        // Single character wildcard
        assert!(matches_filter("light.kitchen1", "light.kitchen?"));
        assert!(!matches_filter("light.kitchen12", "light.kitchen?"));
    }

    #[test]
    fn test_matches_filter_regex() {
        assert!(matches_filter("light.kitchen", "/light\\..*/"));
        assert!(matches_filter("sensor.temperature", "/sensor\\..*/"));
        assert!(!matches_filter("light.kitchen", "/switch\\..*/"));
    }

    #[test]
    fn test_check_attribute_match_exact() {
        let state_attrs = serde_json::Map::from_iter([("brightness".to_string(), json!(255))]);
        let rule_attrs = json!({"brightness": 255});

        assert!(check_attribute_match(Some(&state_attrs), &rule_attrs));
    }

    #[test]
    fn test_check_attribute_match_wildcard() {
        // Note: to_string() on JSON strings includes quotes, so we need to account for that
        let state_attrs =
            serde_json::Map::from_iter([("friendly_name".to_string(), json!("Living Room Light"))]);
        // The pattern needs to match "Living Room Light" (with quotes)
        let rule_attrs = json!({"friendly_name": "\"Living Room*\""});

        assert!(check_attribute_match(Some(&state_attrs), &rule_attrs));
    }

    #[test]
    fn test_check_attribute_match_missing() {
        let state_attrs = serde_json::Map::from_iter([("brightness".to_string(), json!(255))]);
        let rule_attrs = json!({"color_temp": 300});

        assert!(!check_attribute_match(Some(&state_attrs), &rule_attrs));
    }

    #[test]
    fn test_check_attribute_match_none() {
        let rule_attrs = json!({"brightness": 255});

        assert!(!check_attribute_match(None, &rule_attrs));
    }

    #[test]
    fn test_resolve_rules_and_update_entities() {
        let all_states = json!({
            "light.kitchen": {
                "s": "on",
                "a": {"brightness": 255}
            },
            "light.bedroom": {
                "s": "off",
                "a": {"brightness": 0}
            },
            "switch.lamp": {
                "s": "on",
                "a": {}
            }
        });

        let rules = vec![json!({"domain": "light"})];

        let mut entities = HashSet::new();

        resolve_rules_and_update_entities(&all_states, &rules, &mut entities);

        // Should add light entities but not switch
        assert!(entities.contains("light.kitchen"));
        assert!(entities.contains("light.bedroom"));
        assert!(!entities.contains("switch.lamp"));
    }

    #[test]
    fn test_resolve_rules_with_entity_filter() {
        let all_states = json!({
            "light.living_room_main": {
                "s": "on",
                "a": {}
            },
            "light.living_room_side": {
                "s": "off",
                "a": {}
            },
            "light.kitchen": {
                "s": "on",
                "a": {}
            }
        });

        let rules = vec![json!({"entity_id": "light.living_room_*"})];

        let mut entities = HashSet::new();

        resolve_rules_and_update_entities(&all_states, &rules, &mut entities);

        assert!(entities.contains("light.living_room_main"));
        assert!(entities.contains("light.living_room_side"));
        assert!(!entities.contains("light.kitchen"));
    }

    #[test]
    fn test_parse_lovelace_entities_wallpanel_entity_keys() {
        let data = json!({
            "wallpanel": {
                "enabled": true,
                "screensaver_entity": "input_boolean.wallpanel_screensaver",
                "image_url_entity": "input_text.wallpanel_image_url",
                "profile_entity": "input_text.wallpanel_profile",
                "camera_motion_detection_set_entity": "input_boolean.motion_detected"
            }
        });
        let mut entities = HashSet::new();
        let mut filter_rules = Vec::new();

        parse_lovelace_entities(&data, &mut entities, &mut filter_rules);

        assert!(entities.contains("input_boolean.wallpanel_screensaver"));
        assert!(entities.contains("input_text.wallpanel_image_url"));
        assert!(entities.contains("input_text.wallpanel_profile"));
        assert!(entities.contains("input_boolean.motion_detected"));
        assert_eq!(entities.len(), 4);
        assert!(filter_rules.is_empty());
    }

    #[test]
    fn test_parse_lovelace_entities_wallpanel_placeholders() {
        let data = json!({
            "wallpanel": {
                "image_url": "${entity:input_select.wallpanel_image_url}",
                "screensaver_entity": "input_boolean.${browser_id}_wallpanel_screensaver"
            }
        });
        let mut entities = HashSet::new();
        let mut filter_rules = Vec::new();

        parse_lovelace_entities(&data, &mut entities, &mut filter_rules);

        assert!(entities.contains("input_select.wallpanel_image_url"));
        assert_eq!(entities.len(), 1);
        assert!(filter_rules.is_empty());
    }

    #[test]
    fn test_parse_lovelace_entities_wallpanel_in_profile() {
        let data = json!({
            "wallpanel": {
                "profiles": {
                    "night": {
                        "screensaver_entity": "input_boolean.night_screensaver"
                    }
                }
            }
        });
        let mut entities = HashSet::new();
        let mut filter_rules = Vec::new();

        parse_lovelace_entities(&data, &mut entities, &mut filter_rules);

        assert!(entities.contains("input_boolean.night_screensaver"));
        assert_eq!(entities.len(), 1);
    }
}
