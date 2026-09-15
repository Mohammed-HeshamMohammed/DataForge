//! OS credential store adapter (Windows Credential Manager, macOS Keychain, Secret Service).
//! Secrets are written and read only by the host. The UI can save, delete, and list names, never read values.

use serde_json::{json, Value};
use std::path::PathBuf;
use tauri::{AppHandle, Manager};

const SERVICE: &str = "DataForge";

pub fn is_valid_name(name: &str) -> bool {
    !name.is_empty() && name.len() <= 64 && name.chars().all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || "_.-".contains(c))
}

fn entry(name: &str) -> Result<keyring::Entry, String> {
    if !is_valid_name(name) {
        return Err("Credential names use lowercase letters, digits, '.', '_' or '-' (max 64)".into());
    }
    keyring::Entry::new(SERVICE, name).map_err(|e| format!("Credential store unavailable: {e}"))
}

pub fn read_secret(name: &str) -> Result<String, String> {
    entry(name)?.get_password().map_err(|e| match e {
        keyring::Error::NoEntry => format!("No saved credential named '{name}'"),
        other => format!("Could not read credential '{name}': {other}"),
    })
}

fn index_path(app: &AppHandle) -> Result<PathBuf, String> {
    let dir = app.path().app_data_dir().map_err(|e| e.to_string())?;
    std::fs::create_dir_all(&dir).map_err(|e| e.to_string())?;
    Ok(dir.join("credential-names.json"))
}

fn load_index(app: &AppHandle) -> Result<Vec<String>, String> {
    match std::fs::read_to_string(index_path(app)?) {
        Ok(text) => serde_json::from_str(&text).map_err(|e| e.to_string()),
        Err(_) => Ok(Vec::new()),
    }
}

fn save_index(app: &AppHandle, names: &[String]) -> Result<(), String> {
    std::fs::write(index_path(app)?, serde_json::to_string_pretty(names).map_err(|e| e.to_string())?).map_err(|e| e.to_string())
}

#[tauri::command]
pub fn credential_save(app: AppHandle, name: String, secret: String) -> Result<Value, String> {
    if secret.is_empty() || secret.len() > 8192 {
        return Err("Secret must be between 1 and 8192 characters".into());
    }
    entry(&name)?.set_password(&secret).map_err(|e| format!("Could not save credential: {e}"))?;
    let mut names = load_index(&app)?;
    if !names.contains(&name) {
        names.push(name.clone());
        names.sort();
        save_index(&app, &names)?;
    }
    Ok(json!({ "name": name }))
}

#[tauri::command]
pub fn credential_delete(app: AppHandle, name: String) -> Result<Value, String> {
    match entry(&name)?.delete_credential() {
        Ok(()) | Err(keyring::Error::NoEntry) => {}
        Err(e) => return Err(format!("Could not delete credential: {e}")),
    }
    let names: Vec<String> = load_index(&app)?.into_iter().filter(|n| n != &name).collect();
    save_index(&app, &names)?;
    Ok(json!({ "deleted": name }))
}

#[tauri::command]
pub fn credential_list(app: AppHandle) -> Result<Value, String> {
    let names = load_index(&app)?;
    let entries: Vec<Value> = names
        .iter()
        .map(|name| json!({ "name": name, "stored": entry(name).and_then(|e| e.get_password().map_err(|e| e.to_string())).is_ok() }))
        .collect();
    Ok(Value::Array(entries))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn credential_names_are_restricted() {
        assert!(is_valid_name("vendor-api.prod"));
        assert!(!is_valid_name("Vendor"));
        assert!(!is_valid_name("../x"));
        assert!(!is_valid_name(""));
    }

    #[test]
    fn secrets_round_trip_through_the_os_store() {
        let name = format!("dataforge-test-{}", std::process::id());
        let entry = entry(&name).unwrap();
        entry.set_password("fixture-secret").unwrap();
        assert_eq!(read_secret(&name).unwrap(), "fixture-secret");
        entry.delete_credential().unwrap();
        assert!(read_secret(&name).unwrap_err().contains("No saved credential"));
    }
}
