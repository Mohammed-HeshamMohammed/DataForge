mod credentials;
mod sidecar;
mod studio;
mod updates;

use chrono::{SecondsFormat, Utc};
use serde::Serialize;
use tauri::{Manager, RunEvent};

#[derive(Serialize)]
struct HealthResult {
    schema_version: u8,
    service: &'static str,
    status: &'static str,
    checked_at: String,
}

#[tauri::command]
fn health_check() -> HealthResult {
    HealthResult {
        schema_version: 1,
        service: "desktop-host",
        status: "ok",
        checked_at: Utc::now().to_rfc3339_opts(SecondsFormat::Millis, true),
    }
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_updater::Builder::new().build())
        .manage(sidecar::ServiceBridge::default())
        .manage(studio::StudioState::default())
        .manage(updates::PendingUpdate::default())
        .setup(|app| {
            // Packaged builds resolve the service and data files from the bundle, never the source checkout.
            if let Ok(resources) = app.path().resource_dir() {
                if resources.join("service").is_dir() {
                    *app.state::<sidecar::ServiceBridge>().resources.lock().expect("bridge lock") = Some(resources);
                }
            }
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            health_check,
            sidecar::service_call,
            credentials::credential_save,
            credentials::credential_delete,
            credentials::credential_list,
            studio::studio_open,
            studio::studio_set_bounds,
            studio::studio_navigate,
            studio::studio_control,
            studio::studio_close,
            studio::studio_call,
            updates::update_check,
            updates::update_install,
        ])
        .build(tauri::generate_context!())
        .expect("error while building DataForge")
        .run(|app, event| {
            if let RunEvent::Exit = event {
                if let Ok(mut guard) = app.state::<sidecar::ServiceBridge>().process.lock() {
                    if let Some(mut process) = guard.take() {
                        process.kill();
                    }
                }
            }
        });
}
