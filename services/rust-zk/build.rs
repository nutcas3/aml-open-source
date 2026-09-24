// Build script — compiles the shared Trinity protobuf contract into Rust gRPC
// stubs. The proto file lives in the shared contracts directory so Go, Python
// and Rust all agree on the service definitions.
fn main() -> Result<(), Box<dyn std::error::Error>> {
    // Paths are relative to this crate's directory (services/rust-zk/).
    let proto_dir = "../../contracts/proto";
    let proto_file = format!("{proto_dir}/trinity.proto");

    // Re-run the build if the proto contract changes.
    println!("cargo:rerun-if-changed={proto_file}");

    // Ensure the output directory exists for the generated stubs.
    std::fs::create_dir_all("src/proto").ok();

    tonic_build::configure()
        // Preserve the `trinity` package so generated types live under
        // `proto::*` (the file is included via `mod proto { include!(...) }`).
        .out_dir("src/proto")
        .compile_protos(&[&proto_file], &[proto_dir])?;

    Ok(())
}
