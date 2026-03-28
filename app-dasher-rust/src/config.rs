use serde::{Deserialize, Serialize};
use std::fs;
use tracing::{info, warn};

const CONFIG_FILE_PATH: &str = "/data/options.json";
const DEV_CONFIG_PATH: &str = "proxy-config.yaml";

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Config {
    #[serde(default = "default_ha_host")]
    pub ha_host: String,
    #[serde(default = "default_transparent")]
    pub transparent: bool,
    #[serde(default = "default_proxy_port")]
    pub proxy_port: u16,
    #[serde(default = "default_log_level")]
    pub log_level: String,
}

fn default_ha_host() -> String {
    "homeassistant:8123".to_string()
}

fn default_transparent() -> bool {
    true
}

fn default_proxy_port() -> u16 {
    8125
}

fn default_log_level() -> String {
    "INFO".to_string()
}

impl Default for Config {
    fn default() -> Self {
        Self {
            ha_host: default_ha_host(),
            transparent: default_transparent(),
            proxy_port: default_proxy_port(),
            log_level: default_log_level(),
        }
    }
}

impl Config {
    pub fn load() -> anyhow::Result<Self> {
        if std::path::Path::new(CONFIG_FILE_PATH).exists() {
            info!("Loading configuration from {}", CONFIG_FILE_PATH);
            let content = fs::read_to_string(CONFIG_FILE_PATH)?;
            let config: Config = serde_json::from_str(&content)?;
            Ok(config)
        } else if std::path::Path::new(DEV_CONFIG_PATH).exists() {
            info!("Loading configuration from {} (dev mode)", DEV_CONFIG_PATH);
            let content = fs::read_to_string(DEV_CONFIG_PATH)?;
            let config: Config = serde_yaml::from_str(&content)?;
            Ok(config)
        } else {
            warn!("No configuration file found, using defaults");
            info!("Running in dev mode - creating default proxy-config.yaml");
            let config = Config::default();
            let yaml = serde_yaml::to_string(&config)?;
            fs::write(DEV_CONFIG_PATH, yaml)?;
            Ok(config)
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_default_config() {
        let config = Config::default();
        assert_eq!(config.ha_host, "homeassistant:8123");
        assert!(config.transparent);
        assert_eq!(config.proxy_port, 8125);
        assert_eq!(config.log_level, "INFO");
    }

    #[test]
    fn test_config_serialization() {
        let config = Config {
            ha_host: "test:8080".to_string(),
            transparent: false,
            proxy_port: 9000,
            log_level: "DEBUG".to_string(),
        };

        let json = serde_json::to_string(&config).unwrap();
        let deserialized: Config = serde_json::from_str(&json).unwrap();

        assert_eq!(config.ha_host, deserialized.ha_host);
        assert_eq!(config.transparent, deserialized.transparent);
        assert_eq!(config.proxy_port, deserialized.proxy_port);
        assert_eq!(config.log_level, deserialized.log_level);
    }

    #[test]
    fn test_config_deserialization_with_defaults() {
        let json = r#"{"ha_host": "custom:8123"}"#;
        let config: Config = serde_json::from_str(json).unwrap();

        assert_eq!(config.ha_host, "custom:8123");
        // Check defaults are used for missing fields
        assert!(config.transparent);
        assert_eq!(config.proxy_port, 8125);
        assert_eq!(config.log_level, "INFO");
    }

    #[test]
    fn test_config_yaml_serialization() {
        let config = Config {
            ha_host: "homeassistant.local:8123".to_string(),
            transparent: true,
            proxy_port: 8124,
            log_level: "WARN".to_string(),
        };

        let yaml = serde_yaml::to_string(&config).unwrap();
        assert!(yaml.contains("ha_host: homeassistant.local:8123"));
        assert!(yaml.contains("transparent: true"));
        assert!(yaml.contains("proxy_port: 8124"));
        assert!(yaml.contains("log_level: WARN"));
    }
}
