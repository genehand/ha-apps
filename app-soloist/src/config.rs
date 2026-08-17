use clap::parser::ValueSource;
use clap::{ArgMatches, CommandFactory, FromArgMatches, Parser};
use serde::Deserialize;
use std::path::{Path, PathBuf};

/// Configuration for the Soloist bridge, from CLI arguments, environment
/// variables, or an options.json file (add-on style).
#[derive(Parser, Debug, Clone)]
#[command(name = "soloist-bridge")]
#[command(about = "Bridges Spotify Soloist's WebSocket API to Home Assistant via MQTT discovery")]
#[command(version)]
pub struct Cli {
    /// Device name shown in Spotify and Home Assistant
    #[arg(
        long,
        env = "DEVICE_NAME",
        default_value = "Soloist",
        help = "Name shown in Spotify and Home Assistant"
    )]
    pub device_name: String,

    /// Spotify Soloist API key (required when spawning the soloist daemon)
    #[arg(
        long,
        env = "SOLOIST_API_KEY",
        help = "Spotify Soloist API key from developer.spotify.com/dashboard/soloist"
    )]
    pub soloist_api_key: Option<String>,

    /// Connect to a running soloist WebSocket endpoint instead of spawning the daemon
    #[arg(
        long,
        env = "SOLOIST_WS_URL",
        help = "Override: connect to this WebSocket URL (e.g. ws://127.0.0.1:9090) without spawning soloist"
    )]
    pub soloist_ws_url: Option<String>,

    /// Soloist persistent data directory (contains stored session + ws.port)
    #[arg(
        long,
        env = "SOLOIST_DATA_DIR",
        help = "Soloist data directory (persistent; holds session and ws.port runtime file)"
    )]
    pub soloist_data_dir: Option<PathBuf>,

    /// Soloist volatile cache directory
    #[arg(
        long,
        env = "SOLOIST_CACHE_DIR",
        default_value = "/tmp/soloist-cache",
        help = "Soloist cache directory"
    )]
    pub soloist_cache_dir: PathBuf,

    /// Initial volume (0-100) applied when soloist starts
    #[arg(
        long,
        env = "INITIAL_VOLUME",
        help = "Initial volume 0-100 applied at soloist startup"
    )]
    pub initial_volume: Option<u8>,

    /// MQTT broker host
    #[arg(
        long,
        env = "MQTT_HOST",
        default_value = "localhost",
        help = "MQTT broker hostname or IP"
    )]
    pub mqtt_host: String,

    /// MQTT broker port
    #[arg(
        long,
        env = "MQTT_PORT",
        default_value = "1883",
        help = "MQTT broker port"
    )]
    pub mqtt_port: u16,

    /// MQTT username (optional)
    #[arg(long, env = "MQTT_USERNAME", help = "MQTT broker username")]
    pub mqtt_username: Option<String>,

    /// MQTT password (optional)
    #[arg(long, env = "MQTT_PASSWORD", help = "MQTT broker password")]
    pub mqtt_password: Option<String>,

    /// MQTT device ID for unique topics (defaults to slugified device name)
    #[arg(
        long,
        env = "MQTT_DEVICE_ID",
        help = "MQTT device ID (used in topic names and unique_id)"
    )]
    pub mqtt_device_id: Option<String>,

    /// Log level (trace, debug, info, warn, error)
    #[arg(
        short,
        long,
        env = "RUST_LOG",
        default_value = "info",
        help = "Log level: trace, debug, info, warn, error"
    )]
    pub log_level: String,
}

impl Cli {
    /// Parse args like `Cli::parse()` but also return the ArgMatches so we can
    /// tell whether each value came from the command line / environment.
    pub fn parse_with_matches() -> (Cli, ArgMatches) {
        let cmd = Cli::command();
        let matches = cmd.get_matches();
        let cli = Cli::from_arg_matches(&matches).unwrap_or_else(|e| e.exit());
        (cli, matches)
    }
}

/// Contents of an add-on style options.json file (all fields optional).
#[derive(Debug, Default, Deserialize)]
pub struct OptionsFile {
    #[serde(default)]
    pub device_name: Option<String>,
    #[serde(default)]
    pub soloist_api_key: Option<String>,
    #[serde(default)]
    pub initial_volume: Option<u8>,
    #[serde(default)]
    pub mqtt_host: Option<String>,
    #[serde(default)]
    pub mqtt_port: Option<u16>,
    #[serde(default)]
    pub mqtt_username: Option<String>,
    #[serde(default)]
    pub mqtt_password: Option<String>,
    #[serde(default)]
    pub mqtt_device_id: Option<String>,
    #[serde(default)]
    pub log_level: Option<String>,
}

/// Locate the options file: /data/options.json when running as an add-on,
/// otherwise ./options.json in the current directory (local testing).
pub fn find_options_file() -> Option<PathBuf> {
    for candidate in [Path::new("/data/options.json"), Path::new("options.json")] {
        if candidate.is_file() {
            return Some(candidate.to_path_buf());
        }
    }
    None
}

pub fn load_options_file(path: &Path) -> anyhow::Result<OptionsFile> {
    let content = std::fs::read_to_string(path)?;
    let options: OptionsFile = serde_json::from_str(&content)?;
    Ok(options)
}

/// Runtime configuration derived from CLI args + env + options file.
#[derive(Clone)]
pub struct Config {
    pub device_name: String,
    pub soloist_api_key: Option<String>,
    /// Managed path to the soloist daemon binary (<data-dir>/bin/soloist);
    /// downloaded and refreshed by the bridge at startup.
    pub soloist_bin: PathBuf,
    pub soloist_ws_url: Option<String>,
    pub soloist_data_dir: PathBuf,
    pub soloist_cache_dir: PathBuf,
    pub initial_volume: Option<u8>,
    pub mqtt_host: String,
    pub mqtt_port: u16,
    pub mqtt_username: Option<String>,
    pub mqtt_password: Option<String>,
    pub mqtt_device_id: String,
    pub log_level: String,
    /// Path to the options file that was loaded (if any), for logging.
    pub options_file: Option<PathBuf>,
}

impl Config {
    /// Merge CLI/env values (which win) with options.json values (fallback).
    pub fn from_cli_and_options(cli: Cli, matches: &ArgMatches, options: OptionsFile) -> Self {
        // An option was explicitly provided if it came from the command line
        // or an environment variable; otherwise fall back to options.json.
        let explicit = |id: &str| {
            matches!(
                matches.value_source(id),
                Some(ValueSource::CommandLine) | Some(ValueSource::EnvVariable)
            )
        };

        let device_name = if explicit("device_name") {
            cli.device_name
        } else {
            options
                .device_name
                .filter(|s| !s.is_empty())
                .unwrap_or(cli.device_name)
        };

        let soloist_api_key = if explicit("soloist_api_key") {
            cli.soloist_api_key
        } else {
            options.soloist_api_key.filter(|s| !s.is_empty())
        };

        let initial_volume = if explicit("initial_volume") {
            cli.initial_volume
        } else {
            options.initial_volume
        };

        let mqtt_host = if explicit("mqtt_host") {
            cli.mqtt_host
        } else {
            options
                .mqtt_host
                .filter(|s| !s.is_empty())
                .unwrap_or(cli.mqtt_host)
        };

        let mqtt_port = if explicit("mqtt_port") {
            cli.mqtt_port
        } else {
            options.mqtt_port.unwrap_or(cli.mqtt_port)
        };

        let mqtt_username = if explicit("mqtt_username") {
            cli.mqtt_username
        } else {
            options.mqtt_username.filter(|s| !s.is_empty())
        };

        let mqtt_password = if explicit("mqtt_password") {
            cli.mqtt_password
        } else {
            options.mqtt_password.filter(|s| !s.is_empty())
        };

        let mqtt_device_id = if explicit("mqtt_device_id") {
            cli.mqtt_device_id
        } else {
            options.mqtt_device_id.filter(|s| !s.is_empty())
        };

        // Data dir: explicit, or /data/soloist in the add-on environment, else local
        let soloist_data_dir = cli.soloist_data_dir.unwrap_or_else(|| {
            if Path::new("/data/options.json").exists() {
                PathBuf::from("/data/soloist")
            } else {
                PathBuf::from("soloist-data")
            }
        });

        // The soloist binary is always managed by the bridge: downloaded and
        // refreshed at startup into <data-dir>/bin/soloist (the persistent
        // /data/soloist dir in the add-on, "soloist-data" locally) — there is
        // no SOLOIST_BIN option anymore.
        let soloist_bin = soloist_data_dir.join("bin").join("soloist");

        let mqtt_device_id = mqtt_device_id.unwrap_or_else(|| slugify(&device_name));
        let options_file = find_options_file();

        let log_level = if explicit("log_level") {
            cli.log_level
        } else {
            options
                .log_level
                .filter(|s| !s.is_empty())
                .unwrap_or(cli.log_level)
        };

        Self {
            device_name,
            soloist_api_key,
            soloist_bin,
            soloist_ws_url: cli.soloist_ws_url,
            soloist_data_dir,
            soloist_cache_dir: cli.soloist_cache_dir,
            initial_volume,
            mqtt_host,
            mqtt_port,
            mqtt_username,
            mqtt_password,
            mqtt_device_id,
            log_level,
            options_file,
        }
    }
}

/// Slugify a string for use as a safe MQTT device ID:
/// lowercase, spaces to underscores, strip non-alphanumeric characters.
pub fn slugify(s: &str) -> String {
    s.to_lowercase()
        .chars()
        .map(|c| if c == ' ' { '_' } else { c })
        .filter(|c| c.is_ascii_alphanumeric() || *c == '_')
        .collect()
}
#[cfg(test)]
mod tests {
    use super::*;

    /// Serializes tests that manipulate process env vars (clap reads them).
    static ENV_LOCK: std::sync::Mutex<()> = std::sync::Mutex::new(());

    fn config_with(args: &[&str], options: OptionsFile) -> Config {
        let full_args = std::iter::once("soloist-bridge").chain(args.iter().copied());
        let cmd = Cli::command();
        let matches = cmd.try_get_matches_from(full_args).unwrap();
        let cli = Cli::from_arg_matches(&matches).unwrap();
        Config::from_cli_and_options(cli, &matches, options)
    }

    #[test]
    fn options_file_is_used_as_fallback() {
        let _g = ENV_LOCK.lock().unwrap();
        std::env::remove_var("MQTT_HOST");
        let options = OptionsFile {
            device_name: Some("TestSoloist".into()),
            mqtt_host: Some("broker.local".into()),
            mqtt_port: Some(1884),
            ..Default::default()
        };
        let cfg = config_with(&[], options);
        assert_eq!(cfg.device_name, "TestSoloist");
        assert_eq!(cfg.mqtt_host, "broker.local");
        assert_eq!(cfg.mqtt_port, 1884);
        assert_eq!(cfg.mqtt_device_id, "testsoloist");
    }

    #[test]
    fn env_vars_override_options_file() {
        let _g = ENV_LOCK.lock().unwrap();
        std::env::set_var("MQTT_HOST", "env-broker");
        let options = OptionsFile {
            device_name: Some("TestSoloist".into()),
            mqtt_host: Some("broker.local".into()),
            ..Default::default()
        };
        let cfg = config_with(&[], options);
        std::env::remove_var("MQTT_HOST");
        assert_eq!(cfg.device_name, "TestSoloist");
        assert_eq!(cfg.mqtt_host, "env-broker");
    }

    #[test]
    fn cli_args_override_options_file() {
        let _g = ENV_LOCK.lock().unwrap();
        std::env::remove_var("MQTT_PORT");
        let options = OptionsFile {
            mqtt_port: Some(1884),
            ..Default::default()
        };
        let cfg = config_with(&["--mqtt-port", "9999"], options);
        assert_eq!(cfg.mqtt_port, 9999);
    }

    #[test]
    fn slugify_handles_spaces_and_caps() {
        assert_eq!(slugify("My Soloist Device"), "my_soloist_device");
        assert_eq!(slugify("Soloist!"), "soloist");
    }
}
