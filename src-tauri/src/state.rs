use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::sync::Mutex;

/// Application state managed by Tauri.
pub struct AppState {
    pub sidecar_port: u16,
    /// Path where settings.json lives (OS config dir when installed, fallback
    /// to project root in dev).  Set once at startup.
    pub config_path: PathBuf,
    /// Writable directory for user artefacts (drafts/, output/, papers/, ...).
    pub user_data_root: String,
    /// Read-only resource directory containing bundled tools/ and sidecar/.
    /// In dev this equals the repo root; when installed it's Tauri's
    /// `resource_dir()`.
    pub resource_root: String,
    /// Kept for backwards-compat with older commands that take `project_root`.
    /// Mirrors `user_data_root` so file operations land in a writable place.
    pub project_root: String,
    pub api_keys: Mutex<HashMap<String, String>>,
    pub settings: Mutex<AppSettings>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AppSettings {
    #[serde(default = "default_provider")]
    pub default_provider: String,
    #[serde(default = "default_active_model")]
    pub default_model: String,
    #[serde(default = "default_llm_model")]
    pub llm_model: String,
    #[serde(default = "default_ollama_model")]
    pub ollama_model: String,
    #[serde(default)]
    pub image_gen_model: String,
    pub default_discipline: String,
    pub sidecar_auto_start: bool,
    #[serde(default = "default_language")]
    pub language: String,
    #[serde(default)]
    pub api_base_urls: HashMap<String, String>,
    #[serde(default)]
    pub api_keys: HashMap<String, String>,
    #[serde(default)]
    pub agent_enabled: bool,
    #[serde(default = "default_agent_type")]
    pub agent_type: String,
    #[serde(default)]
    pub agent_path: String,
    #[serde(default = "default_agent_max_turns")]
    pub agent_max_turns: u32,
    #[serde(default = "default_agent_timeout")]
    pub agent_timeout_secs: u32,
    #[serde(default = "default_true")]
    pub agent_auto_fix: bool,
    #[serde(default = "default_true")]
    pub agent_auto_supplement: bool,
    #[serde(default)]
    pub license_key: String,
}

fn default_provider() -> String {
    "llm".to_string()
}

fn default_llm_model() -> String {
    "gpt-4o".to_string()
}

fn default_ollama_model() -> String {
    "qwen2.5".to_string()
}

fn default_active_model() -> String {
    default_llm_model()
}

fn default_language() -> String {
    "zh".to_string()
}

fn default_agent_type() -> String {
    "claude_code".to_string()
}

fn default_agent_max_turns() -> u32 {
    10
}

fn default_agent_timeout() -> u32 {
    300
}

fn default_true() -> bool {
    true
}

impl Default for AppSettings {
    fn default() -> Self {
        Self {
            default_provider: default_provider(),
            default_model: default_active_model(),
            llm_model: default_llm_model(),
            ollama_model: default_ollama_model(),
            image_gen_model: String::new(),
            default_discipline: "generic".to_string(),
            sidecar_auto_start: true,
            language: default_language(),
            api_base_urls: HashMap::new(),
            api_keys: HashMap::new(),
            agent_enabled: false,
            agent_type: default_agent_type(),
            agent_path: String::new(),
            agent_max_turns: default_agent_max_turns(),
            agent_timeout_secs: default_agent_timeout(),
            agent_auto_fix: true,
            agent_auto_supplement: true,
            license_key: String::new(),
        }
    }
}

fn first_non_empty(map: &HashMap<String, String>, keys: &[&str]) -> Option<String> {
    keys.iter()
        .filter_map(|key| map.get(*key))
        .map(|value| value.trim())
        .find(|value| !value.is_empty())
        .map(ToOwned::to_owned)
}

impl AppSettings {
    pub fn normalize(mut self) -> Self {
        let legacy_provider = self.default_provider.trim().to_string();

        let llm_candidates: Vec<&str> = match legacy_provider.as_str() {
            "claude" | "anthropic" => vec!["llm", "claude", "anthropic", "openai"],
            "openai" => vec!["llm", "openai", "claude", "anthropic"],
            _ => vec!["llm", "openai", "claude", "anthropic"],
        };

        if let Some(value) = first_non_empty(&self.api_base_urls, &llm_candidates) {
            self.api_base_urls.insert("llm".to_string(), value);
        }
        if let Some(value) = first_non_empty(&self.api_keys, &llm_candidates) {
            self.api_keys.insert("llm".to_string(), value);
        }
        if let Some(value) = first_non_empty(&self.api_base_urls, &["ollama"]) {
            self.api_base_urls.insert("ollama".to_string(), value);
        }
        if let Some(value) = first_non_empty(&self.api_base_urls, &["image_gen"]) {
            self.api_base_urls.insert("image_gen".to_string(), value);
        }
        if let Some(value) = first_non_empty(&self.api_keys, &["image_gen"]) {
            self.api_keys.insert("image_gen".to_string(), value);
        }

        if self.llm_model.trim().is_empty() {
            if legacy_provider != "ollama" && !self.default_model.trim().is_empty() {
                self.llm_model = self.default_model.trim().to_string();
            } else {
                self.llm_model = default_llm_model();
            }
        }

        if self.ollama_model.trim().is_empty() {
            if legacy_provider == "ollama" && !self.default_model.trim().is_empty() {
                self.ollama_model = self.default_model.trim().to_string();
            } else {
                self.ollama_model = default_ollama_model();
            }
        }

        self.default_provider = match legacy_provider.as_str() {
            "ollama" => "ollama".to_string(),
            "llm" => "llm".to_string(),
            _ => "llm".to_string(),
        };

        self.default_model = if self.default_provider == "ollama" {
            self.ollama_model.clone()
        } else {
            self.llm_model.clone()
        };

        if self.language.trim().is_empty() {
            self.language = default_language();
        }
        if self.default_discipline.trim().is_empty() {
            self.default_discipline = "generic".to_string();
        }

        self
    }

    /// Load settings from an explicit path, falling back to defaults.
    pub fn load_from(path: &Path) -> Self {
        if path.exists() {
            if let Ok(data) = std::fs::read_to_string(path) {
                if let Ok(settings) = serde_json::from_str::<Self>(&data) {
                    return settings.normalize();
                }
            }
        }
        Self::default().normalize()
    }

    /// Persist current settings to an explicit path.
    pub fn save_to(&self, path: &Path) -> Result<(), String> {
        if let Some(parent) = path.parent() {
            std::fs::create_dir_all(parent).map_err(|e| e.to_string())?;
        }
        let json = serde_json::to_string_pretty(&self.clone().normalize()).map_err(|e| e.to_string())?;
        std::fs::write(path, json).map_err(|e| e.to_string())
    }
}
