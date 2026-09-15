//! Application service sidecar: a managed child process speaking JSON lines on stdin/stdout.
//! The host validates and forwards commands; it contains no domain logic.

use serde_json::{json, Value};
use std::io::{BufRead, BufReader, Write};
use std::path::{Path, PathBuf};
use std::process::{Child, ChildStdin, ChildStdout, Command, Stdio};
use std::sync::Mutex;

use crate::credentials;

pub struct Sidecar {
    child: Child,
    stdin: ChildStdin,
    stdout: BufReader<ChildStdout>,
    next_id: u64,
}

impl Sidecar {
    pub fn kill(&mut self) {
        let _ = self.child.kill();
    }
}

#[derive(Default)]
pub struct ServiceBridge {
    pub process: Mutex<Option<Sidecar>>,
    /// Bundled resource directory in packaged builds; `None` in development.
    pub resources: Mutex<Option<PathBuf>>,
}

/// Commands that may carry a `credential_ref` for the host to resolve from the OS credential store.
const CREDENTIAL_COMMANDS: &[&str] = &["scrape.create_job", "job.retry"];

fn dev_root() -> PathBuf {
    std::env::var_os("DATAFORGE_ROOT")
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from(concat!(env!("CARGO_MANIFEST_DIR"), "/../../..")))
}

fn service_executable(resources: &Path) -> PathBuf {
    resources.join("service").join(if cfg!(windows) { "dataforge-service.exe" } else { "dataforge-service" })
}

pub fn spawn(resources: Option<&Path>) -> Result<Sidecar, String> {
    let mut command = match resources.filter(|r| service_executable(r).is_file()) {
        Some(resources) => {
            // Release: the packaged service executable; no Python installation required.
            let mut command = Command::new(service_executable(resources));
            command.arg("--resources").arg(resources).current_dir(resources);
            command
        }
        None => {
            let root = dev_root().canonicalize().map_err(|e| format!("DataForge resources not found: {e}"))?;
            let python_path = std::env::join_paths([
                root.join("services/application/src"),
                root.join("workers/matching/src"),
                root.join("workers/scraping/src"),
            ])
            .map_err(|e| e.to_string())?;
            let python = std::env::var("DATAFORGE_PYTHON").unwrap_or_else(|_| "python".into());
            let mut command = Command::new(python);
            command
                .args(["-m", "dataforge_application.server", "--resources"])
                .arg(&root)
                .env("PYTHONPATH", python_path)
                .current_dir(&root);
            command
        }
    };
    command.env("PYTHONUTF8", "1").stdin(Stdio::piped()).stdout(Stdio::piped()).stderr(Stdio::inherit());
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        command.creation_flags(CREATE_NO_WINDOW);
    }
    let mut child = command
        .spawn()
        .map_err(|e| format!("Could not start the DataForge service: {e}. In development install Python 3.11+ or set DATAFORGE_PYTHON."))?;
    let stdin = child.stdin.take().ok_or("service stdin unavailable")?;
    let stdout = BufReader::new(child.stdout.take().ok_or("service stdout unavailable")?);
    Ok(Sidecar { child, stdin, stdout, next_id: 0 })
}

pub fn is_valid_command(command: &str) -> bool {
    let mut parts = command.split('.');
    let valid = |p: Option<&str>| p.is_some_and(|s| !s.is_empty() && s.chars().all(|c| c.is_ascii_lowercase() || c == '_'));
    valid(parts.next()) && valid(parts.next()) && parts.next().is_none()
}

pub fn roundtrip(sidecar: &mut Sidecar, command: &str, payload: &Value) -> Result<Value, String> {
    sidecar.next_id += 1;
    let request = json!({ "id": sidecar.next_id, "schema_version": 1, "command": command, "payload": payload });
    writeln!(sidecar.stdin, "{request}").map_err(|e| e.to_string())?;
    sidecar.stdin.flush().map_err(|e| e.to_string())?;
    let mut line = String::new();
    if sidecar.stdout.read_line(&mut line).map_err(|e| e.to_string())? == 0 {
        return Err("The DataForge service stopped unexpectedly".into());
    }
    let response: Value = serde_json::from_str(&line).map_err(|e| format!("Invalid service response: {e}"))?;
    if response.get("id").and_then(Value::as_u64) != Some(sidecar.next_id) {
        return Err("Service response did not match the request".into());
    }
    Ok(response)
}

/// Sends one command, starting (or restarting) the sidecar as needed.
pub fn call(bridge: &ServiceBridge, command: &str, mut payload: Value) -> Result<Value, String> {
    if !is_valid_command(command) {
        return Err("Invalid command name".into());
    }
    if !payload.is_object() {
        return Err("Command payload must be an object".into());
    }
    if payload.get("credential_secret").is_some() {
        return Err("Secrets cannot be sent from the UI; save a credential in Settings and reference it by name".into());
    }
    if let Some(name) = payload.get("credential_ref").and_then(Value::as_str).map(str::to_owned) {
        if CREDENTIAL_COMMANDS.contains(&command) {
            payload["credential_secret"] = Value::String(credentials::read_secret(&name)?);
        }
    }
    let resources = bridge.resources.lock().map_err(|_| "service bridge poisoned")?.clone();
    let mut guard = bridge.process.lock().map_err(|_| "service bridge poisoned")?;
    if guard.is_none() {
        *guard = Some(spawn(resources.as_deref())?);
    }
    let result = roundtrip(guard.as_mut().expect("sidecar present"), command, &payload);
    if result.is_err() {
        // Drop a broken process; the next call restarts it and durable job state is recovered from SQLite.
        if let Some(mut dead) = guard.take() {
            dead.kill();
        }
    }
    result
}

#[tauri::command(async)]
pub fn service_call(bridge: tauri::State<'_, ServiceBridge>, command: String, payload: Option<Value>) -> Result<Value, String> {
    call(&bridge, &command, payload.unwrap_or_else(|| json!({})))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn command_names_are_validated() {
        assert!(is_valid_command("dataset.list"));
        assert!(is_valid_command("match.submit_review"));
        assert!(!is_valid_command("dataset"));
        assert!(!is_valid_command("dataset.list.extra"));
        assert!(!is_valid_command("Dataset.list"));
        assert!(!is_valid_command("../etc.passwd"));
    }

    #[test]
    fn sidecar_round_trips_a_health_check() {
        let mut sidecar = spawn(None).expect("python service starts");
        let response = roundtrip(&mut sidecar, "health.check", &json!({})).expect("round trip");
        assert_eq!(response["ok"], true);
        assert_eq!(response["result"]["service"], "application");
        sidecar.kill();
    }

    #[test]
    fn ui_supplied_secrets_are_refused() {
        let bridge = ServiceBridge::default();
        let error = call(&bridge, "scrape.create_job", json!({ "credential_secret": "x" })).unwrap_err();
        assert!(error.contains("cannot be sent from the UI"));
    }
}
