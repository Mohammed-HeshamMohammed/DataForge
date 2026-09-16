//! Scrape Studio: a native child WebView embedded in the main window (never an iframe, never an
//! external browser). The UI can only call a fixed set of bridge functions; navigation outside the
//! preset's allowed hosts is blocked by the host.

use serde::Deserialize;
use serde_json::{json, Value};
use std::sync::{mpsc, Mutex};
use std::time::Duration;
use tauri::webview::PageLoadEvent;
use tauri::{AppHandle, Emitter, LogicalPosition, LogicalSize, Manager, Rect, Url, WebviewBuilder, WebviewUrl};

const LABEL: &str = "scrape-studio";
const BRIDGE_SCRIPT: &str = include_str!("studio.js");
const BRIDGE_ACTIONS: &[&str] = &["setMode", "takePicks", "pageInfo", "count", "extract", "scrollStep", "links"];

#[derive(Default)]
pub struct StudioState(Mutex<Vec<String>>);

#[derive(Deserialize, Clone, Copy)]
pub struct Bounds {
    x: f64,
    y: f64,
    width: f64,
    height: f64,
}

impl Bounds {
    fn rect(self) -> Rect {
        Rect {
            position: LogicalPosition::new(self.x.max(0.0), self.y.max(0.0)).into(),
            size: LogicalSize::new(self.width.max(1.0), self.height.max(1.0)).into(),
        }
    }
}

pub fn url_allowed(url: &Url, allowed_hosts: &[String]) -> bool {
    let host = url.host_str().unwrap_or_default();
    let local = matches!(host, "127.0.0.1" | "localhost");
    let scheme_ok = url.scheme() == "https" || (url.scheme() == "http" && local);
    scheme_ok && url.username().is_empty() && url.password().is_none() && (local || allowed_hosts.iter().any(|h| h == host)) || url.as_str() == "about:blank"
}

/// Only the origin and path reach the UI; query strings may carry tokens.
fn redacted(url: &Url) -> String {
    format!("{}://{}{}", url.scheme(), url.host_str().unwrap_or_default(), url.path())
}

fn parse_allowed(state: &StudioState, raw: &str) -> Result<Url, String> {
    let url = Url::parse(raw).map_err(|_| "Invalid URL".to_string())?;
    let hosts = state.0.lock().map_err(|_| "studio state poisoned")?;
    if !url_allowed(&url, &hosts) {
        return Err("URL is outside the preset's allowed hosts".into());
    }
    Ok(url)
}

#[tauri::command(async)]
pub fn studio_open(app: AppHandle, state: tauri::State<'_, StudioState>, url: String, allowed_hosts: Vec<String>, bounds: Bounds) -> Result<(), String> {
    if allowed_hosts.is_empty() || allowed_hosts.iter().any(|h| h.is_empty() || h.contains('/')) {
        return Err("Allowed hosts must be plain host names".into());
    }
    *state.0.lock().map_err(|_| "studio state poisoned")? = allowed_hosts.clone();
    let url = parse_allowed(&state, &url)?;
    if let Some(existing) = app.get_webview(LABEL) {
        let _ = existing.close();
    }
    let window = app.get_window("main").ok_or("Main window not found")?;
    let nav_app = app.clone();
    let load_app = app.clone();
    let builder = WebviewBuilder::new(LABEL, WebviewUrl::External(url))
        .initialization_script(BRIDGE_SCRIPT)
        .incognito(true) // one isolated session per Studio visit: no stored cookies or logins
        .disable_drag_drop_handler()
        .on_navigation(move |target| {
            let allowed = url_allowed(target, &allowed_hosts);
            if !allowed {
                let _ = nav_app.emit_to("main", "studio-event", json!({ "type": "navigation_blocked", "url": redacted(target) }));
            }
            allowed
        })
        .on_page_load(move |_webview, payload| {
            let event = match payload.event() {
                PageLoadEvent::Started => "started",
                PageLoadEvent::Finished => "finished",
            };
            let _ = load_app.emit_to("main", "studio-event", json!({ "type": "page_load", "event": event, "url": redacted(payload.url()) }));
        });
    let rect = bounds.rect();
    window.add_child(builder, rect.position, rect.size).map_err(|e| format!("Could not open Scrape Studio: {e}"))?;
    Ok(())
}

fn studio(app: &AppHandle) -> Result<tauri::Webview, String> {
    app.get_webview(LABEL).ok_or_else(|| "Scrape Studio is not open".to_string())
}

#[tauri::command(async)]
pub fn studio_set_bounds(app: AppHandle, bounds: Bounds) -> Result<(), String> {
    studio(&app)?.set_bounds(bounds.rect()).map_err(|e| e.to_string())
}

#[tauri::command(async)]
pub fn studio_navigate(app: AppHandle, state: tauri::State<'_, StudioState>, url: String) -> Result<(), String> {
    let url = parse_allowed(&state, &url)?;
    studio(&app)?.navigate(url).map_err(|e| e.to_string())
}

#[tauri::command(async)]
pub fn studio_control(app: AppHandle, action: String) -> Result<(), String> {
    let script = match action.as_str() {
        "reload" => "location.reload()",
        "stop" => "window.stop()",
        "back" => "history.back()",
        _ => return Err("Unknown studio control".into()),
    };
    studio(&app)?.eval(script).map_err(|e| e.to_string())
}

#[tauri::command(async)]
pub fn studio_close(app: AppHandle, state: tauri::State<'_, StudioState>) -> Result<(), String> {
    state.0.lock().map_err(|_| "studio state poisoned")?.clear();
    if let Some(webview) = app.get_webview(LABEL) {
        webview.close().map_err(|e| e.to_string())?;
    }
    Ok(())
}

/// Builds `window.__dataforgeStudio.<action>(...args)` from an allow-listed action and JSON-encoded
/// arguments, so the UI cannot inject arbitrary script into the page.
pub fn bridge_script(action: &str, args: &Value) -> Result<String, String> {
    if !BRIDGE_ACTIONS.contains(&action) {
        return Err("Unknown studio bridge action".into());
    }
    let args = args.as_array().ok_or("Bridge arguments must be an array")?;
    let encoded: Vec<String> = args.iter().map(|a| serde_json::to_string(a).expect("json value serializes")).collect();
    Ok(format!(
        "(function(){{try{{return window.__dataforgeStudio ? window.__dataforgeStudio.{action}({}) : {{\"error\":\"bridge_unavailable\"}};}}catch(e){{return {{\"error\":String(e)}};}}}})()",
        encoded.join(",")
    ))
}

#[tauri::command(async)]
pub fn studio_call(app: AppHandle, action: String, args: Value) -> Result<Value, String> {
    let script = bridge_script(&action, &args)?;
    let (tx, rx) = mpsc::channel();
    studio(&app)?
        .eval_with_callback(script, move |result| {
            let _ = tx.send(result);
        })
        .map_err(|e| e.to_string())?;
    let raw = rx.recv_timeout(Duration::from_secs(20)).map_err(|_| "The page did not respond in time".to_string())?;
    serde_json::from_str(&raw).map_err(|e| format!("Invalid bridge response: {e}"))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn navigation_is_limited_to_allowed_hosts() {
        let hosts = vec!["example.org".to_string()];
        assert!(url_allowed(&Url::parse("https://example.org/list?page=2").unwrap(), &hosts));
        assert!(!url_allowed(&Url::parse("https://evil.example/").unwrap(), &hosts));
        assert!(!url_allowed(&Url::parse("http://example.org/").unwrap(), &hosts));
        assert!(!url_allowed(&Url::parse("https://user:pw@example.org/").unwrap(), &hosts));
        assert!(!url_allowed(&Url::parse("file:///C:/Windows/win.ini").unwrap(), &hosts));
        assert!(url_allowed(&Url::parse("http://127.0.0.1:8799/list").unwrap(), &hosts));
    }

    #[test]
    fn bridge_scripts_only_call_allow_listed_actions_with_encoded_arguments() {
        let script = bridge_script("count", &json!(["a'); alert(1); ('"])).unwrap();
        assert!(script.contains(r#"window.__dataforgeStudio.count("a'); alert(1); ('")"#));
        assert!(bridge_script("eval", &json!([])).is_err());
        assert!(bridge_script("count", &json!({"x": 1})).is_err());
    }

    #[test]
    fn redaction_drops_query_strings() {
        assert_eq!(redacted(&Url::parse("https://example.org/a?token=secret").unwrap()), "https://example.org/a");
    }
}
