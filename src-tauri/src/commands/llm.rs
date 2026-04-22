use crate::llm::{self, ChatMessage, ChatRequest};
use crate::state::AppState;
use serde::{Deserialize, Serialize};
use tauri::{Emitter, State};

#[derive(Debug, Serialize, Deserialize)]
pub struct TestConnectionResponse {
    pub success: bool,
    pub message: String,
}

#[tauri::command]
pub async fn set_api_key(
    state: State<'_, AppState>,
    provider: String,
    key: String,
) -> Result<(), String> {
    state
        .api_keys
        .lock()
        .map_err(|e| e.to_string())?
        .insert(provider.clone(), key.clone());

    let config_path = state.config_path.clone();
    let mut settings = state.settings.lock().map_err(|e| e.to_string())?;
    settings.api_keys.insert(provider, key);
    settings.save_to(&config_path)
}

#[derive(Debug, Serialize, Deserialize)]
pub struct LlmConfig {
    pub api_key: String,
    pub base_url: String,
    pub model: String,
}

fn default_base_url_for(provider: &str) -> &'static str {
    match provider {
        "ollama" => "http://localhost:11434/api/chat",
        "image_gen" => "https://api.openai.com/v1",
        _ => "https://api.openai.com/v1/chat/completions",
    }
}

fn normalize_openai_base_url(base_url: &str) -> String {
    let raw = base_url.trim();
    if raw.is_empty() {
        return default_base_url_for("llm").to_string();
    }

    let trimmed = raw.trim_end_matches('/');
    let normalized = trimmed.to_ascii_lowercase();

    if normalized.ends_with("/chat/completions") {
        trimmed.to_string()
    } else if normalized.ends_with("/v1") {
        format!("{}/chat/completions", trimmed)
    } else {
        format!("{}/v1/chat/completions", trimmed)
    }
}

fn normalize_anthropic_base_url(base_url: &str) -> String {
    let raw = base_url.trim();
    if raw.is_empty() {
        return "https://api.anthropic.com/v1/messages".to_string();
    }

    let trimmed = raw.trim_end_matches('/');
    let normalized = trimmed.to_ascii_lowercase();

    if normalized.ends_with("/v1/messages") || normalized.ends_with("/messages") {
        trimmed.to_string()
    } else if normalized.ends_with("/v1") {
        format!("{}/messages", trimmed)
    } else {
        format!("{}/v1/messages", trimmed)
    }
}

fn normalize_base_url(provider: &str, base_url: &str) -> String {
    match provider {
        "ollama" => {
            let raw = base_url.trim();
            if raw.is_empty() {
                default_base_url_for("ollama").to_string()
            } else {
                raw.trim_end_matches('/').to_string()
            }
        }
        "image_gen" => {
            let raw = base_url.trim();
            if raw.is_empty() {
                default_base_url_for("image_gen").to_string()
            } else {
                raw.trim_end_matches('/').to_string()
            }
        }
        _ => {
            let normalized = base_url.trim().to_ascii_lowercase();
            if normalized.contains("anthropic") || normalized.ends_with("/messages") || normalized.contains("/v1/messages") {
                normalize_anthropic_base_url(base_url)
            } else {
                normalize_openai_base_url(base_url)
            }
        }
    }
}

fn model_for(settings: &crate::state::AppSettings, provider: &str) -> String {
    match provider {
        "ollama" => settings.ollama_model.clone(),
        "image_gen" => settings.image_gen_model.clone(),
        _ => settings.llm_model.clone(),
    }
}

fn image_generation_endpoint(base_url: &str) -> String {
    let trimmed = base_url.trim().trim_end_matches('/');
    let api_root = trimmed.strip_suffix("/v1").unwrap_or(trimmed);
    format!("{}/v1/images/generations", api_root)
}

fn image_generation_fallback_endpoint(base_url: &str) -> String {
    let trimmed = base_url.trim().trim_end_matches('/');
    let api_root = trimmed.strip_suffix("/v1").unwrap_or(trimmed);
    format!("{}/v1/chat/completations", api_root)
}

async fn test_image_generation_connection(
    api_key: &str,
    base_url: &str,
    model: &str,
) -> Result<TestConnectionResponse, String> {
    if api_key.trim().is_empty() {
        return Ok(TestConnectionResponse {
            success: false,
            message: "image_gen API key not configured".to_string(),
        });
    }

    if base_url.trim().is_empty() {
        return Ok(TestConnectionResponse {
            success: false,
            message: "image_gen base URL not configured".to_string(),
        });
    }

    if model.trim().is_empty() {
        return Ok(TestConnectionResponse {
            success: false,
            message: "image_gen model not configured".to_string(),
        });
    }

    let client = reqwest::Client::new();
    let primary_url = image_generation_endpoint(base_url);
    let primary_resp = client
        .post(&primary_url)
        .header("Authorization", format!("Bearer {}", api_key))
        .header("Content-Type", "application/json")
        .json(&serde_json::json!({
            "model": model,
            "prompt": "A plain white square.",
            "n": 1,
            "size": "1024x1024",
            "response_format": "b64_json",
        }))
        .send()
        .await;

    match primary_resp {
        Ok(resp) if resp.status().is_success() => {
            return Ok(TestConnectionResponse {
                success: true,
                message: "image_gen connection successful".to_string(),
            });
        }
        Ok(resp) => {
            let status = resp.status();
            let text = resp.text().await.unwrap_or_default();
            let fallback_url = image_generation_fallback_endpoint(base_url);
            let fallback_resp = client
                .post(&fallback_url)
                .header("Authorization", format!("Bearer {}", api_key))
                .header("Content-Type", "application/json")
                .json(&serde_json::json!({
                    "model": model,
                    "messages": [{
                        "role": "user",
                        "content": "Generate a tiny test image and describe it briefly.",
                    }],
                    "modalities": ["text", "image"],
                    "max_tokens": 32,
                }))
                .send()
                .await;

            match fallback_resp {
                Ok(fallback) if fallback.status().is_success() => Ok(TestConnectionResponse {
                    success: true,
                    message: format!(
                        "image_gen connection successful (chat fallback after images endpoint returned {})",
                        status
                    ),
                }),
                Ok(fallback) => {
                    let fallback_status = fallback.status();
                    let fallback_text = fallback.text().await.unwrap_or_default();
                    Ok(TestConnectionResponse {
                        success: false,
                        message: format!(
                            "images endpoint {}: {}; chat fallback {}: {}",
                            status,
                            text,
                            fallback_status,
                            fallback_text
                        ),
                    })
                }
                Err(error) => Ok(TestConnectionResponse {
                    success: false,
                    message: format!(
                        "images endpoint {}: {}; chat fallback failed: {}",
                        status,
                        text,
                        error
                    ),
                }),
            }
        }
        Err(error) => Ok(TestConnectionResponse {
            success: false,
            message: format!("image_gen request failed: {}", error),
        }),
    }
}

#[tauri::command]
pub async fn get_llm_config(
    state: State<'_, AppState>,
    provider: String,
) -> Result<LlmConfig, String> {
    let api_keys = state.api_keys.lock().map_err(|e| e.to_string())?.clone();
    let settings = state.settings.lock().map_err(|e| e.to_string())?.clone();
    let provider_key = provider.as_str();

    let api_key = if provider_key == "ollama" {
        String::new()
    } else {
        api_keys.get(provider_key).cloned().unwrap_or_default()
    };

    let raw_base_url = settings
        .api_base_urls
        .get(provider_key)
        .cloned()
        .unwrap_or_else(|| default_base_url_for(provider_key).to_string());
    let base_url = normalize_base_url(provider_key, &raw_base_url);

    let model = model_for(&settings, provider_key);

    Ok(LlmConfig {
        api_key,
        base_url,
        model,
    })
}

#[tauri::command]
pub async fn get_providers(state: State<'_, AppState>) -> Result<Vec<String>, String> {
    let keys = state.api_keys.lock().map_err(|e| e.to_string())?;
    Ok(keys.keys().cloned().collect())
}

#[tauri::command]
pub async fn test_llm_connection(
    state: State<'_, AppState>,
    provider: String,
    model: String,
) -> Result<TestConnectionResponse, String> {
    let api_keys = state.api_keys.lock().map_err(|e| e.to_string())?.clone();
    let settings = state.settings.lock().map_err(|e| e.to_string())?.clone();
    let mut base_urls = settings.api_base_urls.clone();

    let normalized_provider_url = normalize_base_url(
        &provider,
        base_urls
            .get(&provider)
            .map(|s| s.as_str())
            .unwrap_or(default_base_url_for(&provider)),
    );
    base_urls.insert(provider.clone(), normalized_provider_url.clone());

    if provider == "image_gen" {
        let api_key = api_keys.get("image_gen").cloned().unwrap_or_default();
        let effective_model = if model.trim().is_empty() {
            settings.image_gen_model.clone()
        } else {
            model.clone()
        };
        return test_image_generation_connection(&api_key, &normalized_provider_url, &effective_model).await;
    }

    let request = ChatRequest {
        provider: provider.clone(),
        model,
        messages: vec![ChatMessage {
            role: "user".to_string(),
            content: "Hi".to_string(),
        }],
        max_tokens: Some(10),
        temperature: Some(0.0),
        stream: false,
    };

    match llm::route_chat(request, &api_keys, &base_urls).await {
        Ok(_) => Ok(TestConnectionResponse {
            success: true,
            message: format!("{} connection successful", provider),
        }),
        Err(e) => Ok(TestConnectionResponse {
            success: false,
            message: e,
        }),
    }
}

// ── Multi-model group chat commands ──────────────────────────────────

/// A single model's config for group chat streaming.
#[derive(Debug, Serialize, Deserialize)]
pub struct GroupModelRequest {
    pub model_id: String,
    pub provider: String,
    pub model: String,
    pub system_prompt: Option<String>,
}

/// Stream multiple LLMs concurrently. Each model emits `group-chat-chunk`
/// events tagged with its `model_id`.
///
/// Uses inline SSE parsing per model to avoid the cross-contamination issue
/// with `app.listen()` (which receives ALL events globally).
#[tauri::command]
pub async fn group_chat_stream(
    state: State<'_, AppState>,
    requests: Vec<GroupModelRequest>,
    history: Vec<ChatMessage>,
    app: tauri::AppHandle,
) -> Result<(), String> {
    eprintln!("[group_chat] {} requests", requests.len());
    let api_keys = state.api_keys.lock().map_err(|e| e.to_string())?.clone();
    let settings = state.settings.lock().map_err(|e| e.to_string())?.clone();
    let mut base_urls = settings.api_base_urls.clone();

    // Normalize base URLs (same as get_llm_config does)
    for req in &requests {
        let provider = req.provider.as_str();
        if let Some(raw) = base_urls.get(provider).cloned() {
            let normalized = normalize_base_url(provider, &raw);
            base_urls.insert(provider.to_string(), normalized);
        } else if let Some(raw) = base_urls.get("llm").cloned() {
            let normalized = normalize_base_url("llm", &raw);
            base_urls.insert("llm".to_string(), normalized);
        }
    }

    let mut handles: Vec<tokio::task::JoinHandle<()>> = Vec::new();

    for req in requests {
        let api_keys = api_keys.clone();
        let base_urls = base_urls.clone();
        let history = history.clone();
        let app = app.clone();
        let model_id = req.model_id.clone();
        let system_prompt = req.system_prompt.clone();

        let handle = tokio::spawn(async move {
            let mut messages = Vec::new();

            if let Some(sys) = system_prompt {
                messages.push(ChatMessage {
                    role: "system".to_string(),
                    content: sys,
                });
            }
            messages.extend(history);

            let chat_request = ChatRequest {
                provider: req.provider.clone(),
                model: req.model.clone(),
                messages,
                max_tokens: Some(4096),
                temperature: Some(0.7),
                stream: true,
            };

            // Route to the correct stream handler and emit tagged group-chat-chunk events
            let result = llm::route_stream_tagged(
                chat_request,
                &api_keys,
                &base_urls,
                &app,
                &model_id,
            )
            .await;

            if let Err(e) = result {
                eprintln!("[group_chat] stream error for {}: {}", model_id, e);
                let _ = app.emit(
                    "group-chat-chunk",
                    &llm::GroupChatChunk {
                        model_id,
                        delta: format!("[Error: {}]", e),
                        done: true,
                    },
                );
            }
        });

        handles.push(handle);
    }

    for handle in handles {
        let _ = handle.await;
    }

    Ok(())
}
