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
    8124
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
