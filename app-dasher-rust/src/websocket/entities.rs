use regex::Regex;
use serde_json::Value;
use std::collections::HashSet;
use tracing::debug;

lazy_static::lazy_static! {
    static ref ENTITY_PATTERN: Regex = Regex::new(r"^[\w]+\.[\w]+$").unwrap();
    static ref TEMPLATE_PATTERN: Regex = Regex::new(r#"states\[(?:'|")([\w]+\.[\w]+)(?:'|")\]"#).unwrap();
    static ref BUBBLE_CARD_PATTERN: Regex = Regex::new(r"^\d+_entity$").unwrap();
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
                // Check for custom:bubble-card background color pattern
                else if BUBBLE_CARD_PATTERN.is_match(key) {
                    if let Some(s) = value.as_str() {
                        validate_and_add(s, entities);
                    }
                }
                // Check for template patterns in string values
                else if let Some(s) = value.as_str() {
                    for cap in TEMPLATE_PATTERN.captures_iter(s) {
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
        } else {
            if actual_value != value_pattern {
                return false;
            }
        }
    }

    true
}
