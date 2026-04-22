pub mod claude;
pub mod ollama;
pub mod openai;

use serde::{Deserialize, Serialize};
use tauri::{AppHandle, Emitter};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ChatMessage {
    pub role: String,
    pub content: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ChatRequest {
    pub provider: String,
    pub model: String,
    pub messages: Vec<ChatMessage>,
    pub max_tokens: Option<u32>,
    pub temperature: Option<f64>,
    #[serde(default)]
    pub stream: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ChatResponse {
    pub content: String,
    pub model: String,
    pub usage: Option<Usage>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Usage {
    pub input_tokens: Option<u32>,
    pub output_tokens: Option<u32>,
}

/// Emitted to the frontend for each streaming chunk.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct StreamChunk {
    pub delta: String,
    pub done: bool,
}

/// Emitted for group chat — tagged with model_id.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct GroupChatChunk {
    pub model_id: String,
    pub delta: String,
    pub done: bool,
}

pub fn provider_key(provider: &str) -> &str {
    match provider {
        "claude" | "anthropic" | "openai" | "gpt" | "llm" | "google" | "gemini" => "llm",
        "ollama" => "ollama",
        other => other,
    }
}

fn uses_anthropic_api(base_url: Option<&String>) -> bool {
    base_url
        .map(|url| {
            let normalized = url.trim().to_ascii_lowercase();
            normalized.contains("/v1/messages") || normalized.ends_with("/messages") || normalized.contains("anthropic")
        })
        .unwrap_or(false)
}

/// Route a non-streaming chat request.
pub async fn route_chat(
    mut request: ChatRequest,
    api_keys: &std::collections::HashMap<String, String>,
    base_urls: &std::collections::HashMap<String, String>,
) -> Result<ChatResponse, String> {
    match provider_key(request.provider.as_str()) {
        "llm" => {
            let key = api_keys.get("llm").ok_or("LLM API key not configured")?;
            let base_url = base_urls.get("llm");
            if uses_anthropic_api(base_url) {
                request.provider = "claude".to_string();
                claude::chat(request, key, base_url).await
            } else {
                request.provider = "openai".to_string();
                openai::chat(request, key, base_url).await
            }
        }
        "ollama" => {
            request.provider = "ollama".to_string();
            let base_url = base_urls.get("ollama");
            ollama::chat(request, base_url).await
        }
        other => Err(format!("Unknown provider: {}", other)),
    }
}

/// Route a streaming chat request — emits `llm-chunk` events to the frontend.
pub async fn route_stream(
    mut request: ChatRequest,
    api_keys: &std::collections::HashMap<String, String>,
    base_urls: &std::collections::HashMap<String, String>,
    app: &AppHandle,
) -> Result<(), String> {
    match provider_key(request.provider.as_str()) {
        "llm" => {
            let key = api_keys.get("llm").ok_or("LLM API key not configured")?;
            let base_url = base_urls.get("llm");
            if uses_anthropic_api(base_url) {
                request.provider = "claude".to_string();
                claude::stream_chat(request, key, app, base_url).await
            } else {
                request.provider = "openai".to_string();
                openai::stream_chat(request, key, app, base_url).await
            }
        }
        "ollama" => {
            request.provider = "ollama".to_string();
            let base_url = base_urls.get("ollama");
            ollama::stream_chat(request, app, base_url).await
        }
        other => Err(format!("Unknown provider: {}", other)),
    }
}

/// Route a streaming chat request with model_id tagging — emits `group-chat-chunk` events.
/// Used by group_chat_stream to distinguish responses from multiple concurrent models.
pub async fn route_stream_tagged(
    mut request: ChatRequest,
    api_keys: &std::collections::HashMap<String, String>,
    base_urls: &std::collections::HashMap<String, String>,
    app: &AppHandle,
    model_id: &str,
) -> Result<(), String> {
    eprintln!("[stream_tagged] model_id={} provider={} model={}", model_id, request.provider, request.model);
    match provider_key(request.provider.as_str()) {
        "llm" => {
            let key = api_keys.get("llm").ok_or("LLM API key not configured")?;
            let base_url = base_urls.get("llm");
            if uses_anthropic_api(base_url) {
                request.provider = "claude".to_string();
                claude::stream_chat_tagged(request, key, app, base_url, model_id).await
            } else {
                request.provider = "openai".to_string();
                openai::stream_chat_tagged(request, key, app, base_url, model_id).await
            }
        }
        "ollama" => {
            request.provider = "ollama".to_string();
            let base_url = base_urls.get("ollama");
            ollama::stream_chat_tagged(request, app, base_url, model_id).await
        }
        other => Err(format!("Unknown provider: {}", other)),
    }
}

/// Helper to emit a chunk event.
pub fn emit_chunk(app: &AppHandle, delta: &str, done: bool) {
    let chunk = StreamChunk {
        delta: delta.to_string(),
        done,
    };
    let _ = app.emit("llm-chunk", &chunk);
}

/// Emit a tagged group-chat chunk.
pub fn emit_group_chunk(app: &AppHandle, model_id: &str, delta: &str, done: bool) {
    let chunk = GroupChatChunk {
        model_id: model_id.to_string(),
        delta: delta.to_string(),
        done,
    };
    let _ = app.emit("group-chat-chunk", &chunk);
}
