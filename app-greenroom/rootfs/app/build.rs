use std::io::Result;

fn main() -> Result<()> {
    // Generate Rust code from the ESPHome API protobuf definition
    prost_build::compile_protos(&["proto/api.proto"], &["proto/"])?;

    Ok(())
}
