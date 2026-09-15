//! Update client: GitHub Releases discovery, signature-verified download, install on explicit approval.
//! The updater plugin verifies each artifact against the public key embedded in tauri.conf.json.

use serde_json::{json, Value};
use std::sync::Mutex;
use tauri::{AppHandle, Url};
use tauri_plugin_updater::{Update, UpdaterExt};

use crate::sidecar::{self, ServiceBridge};

#[derive(Default)]
pub struct PendingUpdate(Mutex<Option<Update>>);

const ALLOWED_DOWNLOAD_HOSTS: &[&str] = &["github.com", "objects.githubusercontent.com"];

pub fn is_valid_repository(repository: &str) -> bool {
    let mut parts = repository.split('/');
    let valid = |p: Option<&str>| p.is_some_and(|s| !s.is_empty() && s.len() <= 100 && s.chars().all(|c| c.is_ascii_alphanumeric() || "-_.".contains(c)) && s != "." && s != "..");
    valid(parts.next()) && valid(parts.next()) && parts.next().is_none()
}

pub fn endpoint(repository: &str, channel: &str) -> Result<Url, String> {
    if !is_valid_repository(repository) {
        return Err("Repository must look like owner/name".into());
    }
    let path = match channel {
        "stable" => format!("https://github.com/{repository}/releases/latest/download/latest.json"),
        "beta" => format!("https://github.com/{repository}/releases/download/beta/latest.json"),
        _ => return Err("Channel must be stable or beta".into()),
    };
    Url::parse(&path).map_err(|e| e.to_string())
}

#[tauri::command]
pub async fn update_check(app: AppHandle, pending: tauri::State<'_, PendingUpdate>, repository: String, channel: String) -> Result<Value, String> {
    let updater = app
        .updater_builder()
        .endpoints(vec![endpoint(&repository, &channel)?])
        .map_err(|e| e.to_string())?
        .build()
        .map_err(|e| e.to_string())?;
    let update = updater.check().await.map_err(|e| format!("Update check failed: {e}"))?;
    let result = match &update {
        Some(update) => {
            let host = update.download_url.host_str().unwrap_or_default();
            if update.download_url.scheme() != "https" || !ALLOWED_DOWNLOAD_HOSTS.contains(&host) {
                return Err(format!("Refusing update hosted outside GitHub ({host})"));
            }
            json!({ "available": true, "version": update.version, "current_version": update.current_version,
                    "notes": update.body, "published_at": update.date.map(|d| d.to_string()) })
        }
        None => json!({ "available": false }),
    };
    *pending.0.lock().map_err(|_| "update state poisoned")? = update;
    Ok(result)
}

#[tauri::command]
pub async fn update_install(app: AppHandle, pending: tauri::State<'_, PendingUpdate>, bridge: tauri::State<'_, ServiceBridge>) -> Result<(), String> {
    // Never install while jobs are running; review decisions are saved as they are made.
    let summary = sidecar::call(&bridge, "project.summary", json!({}));
    if let Ok(response) = &summary {
        if response["ok"] == true && response["result"]["active_jobs"].as_array().is_some_and(|jobs| !jobs.is_empty()) {
            return Err("Finish or cancel active jobs before installing an update".into());
        }
    }
    let update = pending.0.lock().map_err(|_| "update state poisoned")?.take().ok_or("Check for updates first")?;
    // download_and_install verifies the signature before installing; a tampered artifact is rejected.
    update
        .download_and_install(|_, _| {}, || {})
        .await
        .map_err(|e| format!("Update was not installed: {e}"))?;
    if let Ok(mut guard) = bridge.process.lock() {
        if let Some(mut process) = guard.take() {
            process.kill();
        }
    }
    app.restart();
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn update_endpoints_are_pinned_to_github_releases() {
        assert_eq!(endpoint("acme/dataforge", "stable").unwrap().as_str(), "https://github.com/acme/dataforge/releases/latest/download/latest.json");
        assert!(endpoint("acme/../x", "stable").is_err());
        assert!(endpoint("acme/dataforge", "nightly").is_err());
        assert!(endpoint("https://evil.example/x", "stable").is_err());
    }
}
