#![allow(clippy::expect_used)]

use std::{path::PathBuf, process::Command};

fn binary() -> PathBuf {
    std::env::var_os("NEXTEST_BIN_EXE_openapi-mcp").map_or_else(
        || PathBuf::from(env!("CARGO_BIN_EXE_openapi-mcp")),
        PathBuf::from,
    )
}

#[test]
fn missing_spec_is_a_startup_error_on_stderr() {
    let output = Command::new(binary())
        .env_remove("OPENAPI_MCP_BEARER_TOKEN")
        .output()
        .expect("run binary");
    assert!(!output.status.success());
    assert!(output.stdout.is_empty());
    assert!(String::from_utf8_lossy(&output.stderr).contains("provide --spec"));
}

#[test]
fn unknown_config_fields_are_rejected() {
    let directory = tempfile::tempdir().expect("tempdir");
    let config = directory.path().join("config.json");
    std::fs::write(&config, r#"{"specc":"example.json"}"#).expect("write config");
    let output = Command::new(binary())
        .arg("--config")
        .arg(config)
        .output()
        .expect("run binary");
    assert!(!output.status.success());
    assert!(output.stdout.is_empty());
    assert!(String::from_utf8_lossy(&output.stderr).contains("unknown field"));
}

#[test]
fn help_identifies_the_generic_server_and_configuration() {
    let output = Command::new(binary())
        .arg("--help")
        .output()
        .expect("run binary");
    assert!(output.status.success());
    let text = String::from_utf8_lossy(&output.stdout);
    for option in [
        "--spec",
        "--base-url",
        "--config",
        "--header",
        "--allow-file-reads",
    ] {
        assert!(text.contains(option), "missing {option}");
    }
    assert!(!text.to_lowercase().contains("onshape"));
}
