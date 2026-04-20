use crate::state::AppState;
use serde::Serialize;
use tauri::State;

const LICENSE_SECRET: &str = "SciPilot2024LicenseKey";

#[derive(Debug, Clone, Serialize)]
pub enum LicenseTier {
    Free,
    Student,
    Pro,
}

#[derive(Debug, Clone, Serialize)]
pub struct LicenseStatus {
    pub valid: bool,
    pub tier: LicenseTier,
}

/// DJB2 hash — fast, no external deps.
fn djb2(input: &str) -> u64 {
    let mut hash: u64 = 5381;
    for b in input.bytes() {
        hash = hash.wrapping_mul(33).wrapping_add(b as u64);
    }
    hash
}

/// Validate a license key.
///
/// Format: `SP-S-XXXXX-XXXX` or `SP-P-XXXXX-XXXX`
///   - `SP`   : fixed prefix
///   - `S`/`P`: Student / Pro
///   - `XXXXX`: serial number (00001–99999)
///   - `XXXX` : checksum (hex of djb2(payload + secret) mod 65536)
fn validate_key(raw: &str) -> Option<LicenseTier> {
    let clean: String = raw
        .to_uppercase()
        .chars()
        .filter(|c| c.is_ascii_alphanumeric())
        .collect();

    // SP + tier(1) + serial(5) + checksum(4) = 12
    if clean.len() != 12 || !clean.starts_with("SP") {
        return None;
    }

    let tier = match clean.as_bytes().get(2)? {
        b'S' => LicenseTier::Student,
        b'P' => LicenseTier::Pro,
        _ => return None,
    };

    let payload = &clean[..8]; // SP + tier + 5-digit serial
    let checksum = &clean[8..12];

    let expected_hash = djb2(&format!("{}{}", payload, LICENSE_SECRET)) % 65536;
    let expected = format!("{:04X}", expected_hash);

    if checksum == expected {
        Some(tier)
    } else {
        None
    }
}

#[tauri::command]
pub async fn activate_license(
    state: State<'_, AppState>,
    key: String,
) -> Result<LicenseStatus, String> {
    let tier = validate_key(&key).ok_or("Invalid license key".to_string())?;

    // Persist to settings
    {
        let mut settings = state.settings.lock().map_err(|e| e.to_string())?;
        settings.license_key = key;
        settings
            .save_to(&state.config_path)
            .map_err(|e| e.to_string())?;
    }

    Ok(LicenseStatus {
        valid: true,
        tier,
    })
}

#[tauri::command]
pub async fn get_license_status(state: State<'_, AppState>) -> Result<LicenseStatus, String> {
    let settings = state.settings.lock().map_err(|e| e.to_string())?;

    if settings.license_key.is_empty() {
        return Ok(LicenseStatus {
            valid: false,
            tier: LicenseTier::Free,
        });
    }

    match validate_key(&settings.license_key) {
        Some(tier) => Ok(LicenseStatus {
            valid: true,
            tier,
        }),
        None => Ok(LicenseStatus {
            valid: false,
            tier: LicenseTier::Free,
        }),
    }
}

#[tauri::command]
pub async fn deactivate_license(state: State<'_, AppState>) -> Result<(), String> {
    let mut settings = state.settings.lock().map_err(|e| e.to_string())?;
    settings.license_key.clear();
    settings
        .save_to(&state.config_path)
        .map_err(|e| e.to_string())?;
    Ok(())
}
