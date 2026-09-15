use serde::Serialize;
use std::time::{SystemTime, UNIX_EPOCH};

#[derive(Serialize)]
struct HealthResult<'a> {
    schema_version: u8,
    service: &'a str,
    status: &'a str,
    checked_at: String,
}

fn main() {
    let checked_at = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .expect("system clock must be after Unix epoch")
        .as_secs();
    let result = HealthResult {
        schema_version: 1,
        service: "desktop-host",
        status: "ok",
        checked_at: checked_at.to_string(),
    };
    println!("{}", serde_json::to_string(&result).expect("health result serializes"));
}
