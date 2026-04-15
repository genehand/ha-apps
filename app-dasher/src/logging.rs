use tracing_subscriber::{
    fmt::{self, format::FmtSpan},
    layer::SubscriberExt,
    util::SubscriberInitExt,
    EnvFilter,
};

pub fn init() {
    let env_filter = EnvFilter::try_from_default_env().unwrap_or_else(|_| EnvFilter::new("info"));

    // Use local time for timestamps in format: "2026-04-15 07:58:09"
    let time_format =
        time::macros::format_description!("[year]-[month]-[day] [hour]:[minute]:[second]");
    let fmt_layer = fmt::layer()
        .with_target(false)
        .with_level(true)
        .with_ansi(true)
        .with_span_events(FmtSpan::CLOSE)
        .with_timer(fmt::time::LocalTime::new(time_format))
        .compact();

    tracing_subscriber::registry()
        .with(env_filter)
        .with(fmt_layer)
        .init();
}
