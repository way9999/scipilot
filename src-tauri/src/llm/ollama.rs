use super::{emit_chunk, ChatRequest, ChatResponse, Usage};
use futures::StreamExt;
use serde::{Deserialize, Serialize};
use tauri::AppHandle;

/// Ollama uses the OpenAI-compatible API format at localhost.
#[derive(Serialize)]
struct OllamaRequest {
    model: String,
    messages: Vec<OllamaMessage>,
    #[serde(skip_serializing_if = "Option::is_none")]
    temperature: Option<f64>,
    #[serde(skip_serializing_if = "std::ops::Not::not")]
    stream: bool,
}

#[derive(Serialize)]
struct OllamaMessage {
    role: String,
    content: String,
}

#[derive(Deserialize)]
struct OllamaResponse {
    message: Option<OllamaRespMessage>,
    model: String,
}

#[derive(Deserialize)]
struct OllamaRespMessage {
    content: Option<String>,
}

#[derive(Deserialize)]
struct OllamaStreamChunk {
    message: Option<OllamaRespMessage>,
    done: Option<bool>,
}

fn build_messages(request: &ChatRequest) -> Vec<OllamaMessage> {
    request
        .messages
        .iter()
        .map(|m| OllamaMessage {
            role: m.role.clone(),
            content: m.content.clone(),
        })
        .collect()
}

/// Non-streaming chat.
pub async fn chat(request: ChatRequest, base_url: Option<&String>) -> Result<ChatResponse, String> {
    let body = OllamaRequest {
        model: request.model.clone(),
        messages: build_messages(&request),
        temperature: request.temperature,
        stream: false,
    };

    let url = base_url
        .map(|s| s.as_str())
        .unwrap_or("http://localhost:11434/api/chat");

    let client = reqwest::Client::new();
    let resp = client
        .post(url)
        .json(&body)
        .send()
        .await
        .map_err(|e| format!("Ollama request failed: {}", e))?;

    if !resp.status().is_success() {
        let status = resp.status();
        let text = resp.text().await.unwrap_or_default();
        return Err(format!("Ollama error {}: {}", status, text));
    }

    let api_resp: OllamaResponse = resp
        .json()
        .await
        .map_err(|e| format!("Failed to parse Ollama response: {}", e))?;

    let content = api_resp
        .message
        .and_then(|m| m.content)
        .unwrap_or_default();

    Ok(ChatResponse {
        content,
        model: api_resp.model,
        usage: Some(Usage {
            input_tokens: None,
            output_tokens: None,
        }),
    })
}

/// Streaming chat — emits `llm-chunk` events via Tauri.
pub async fn stream_chat(request: ChatRequest, app: &AppHandle, base_url: Option<&String>) -> Result<(), String> {
    let body = OllamaRequest {
        model: request.model.clone(),
        messages: build_messages(&request),
        temperature: request.temperature,
        stream: true,
    };

    let url = base_url
        .map(|s| s.as_str())
        .unwrap_or("http://localhost:11434/api/chat");

    let client = reqwest::Client::new();
    let resp = client
        .post(url)
        .json(&body)
        .send()
        .await
        .map_err(|e| format!("Ollama stream request failed: {}", e))?;

    if !resp.status().is_success() {
        let status = resp.status();
        let text = resp.text().await.unwrap_or_default();
        return Err(format!("Ollama error {}: {}", status, text));
    }

    let mut stream = resp.bytes_stream();
    let mut byte_buf: Vec<u8> = Vec::new();

    while let Some(chunk) = stream.next().await {
        let chunk = chunk.map_err(|e| format!("Stream read error: {}", e))?;
        byte_buf.extend_from_slice(&chunk);

        // Ollama sends newline-delimited JSON — split on \n
        loop {
            let sep = match byte_buf.iter().position(|&b| b == b'\n') {
                Some(p) => p,
                None => break,
            };
            let line_bytes = byte_buf[..sep].to_vec();
            byte_buf = byte_buf[sep + 1..].to_vec();
            let line = match String::from_utf8(line_bytes) {
                Ok(s) => s,
                Err(_) => continue,
            };
            if line.trim().is_empty() {
                continue;
            }
            if let Ok(chunk_data) = serde_json::from_str::<OllamaStreamChunk>(&line) {
                if let Some(msg) = chunk_data.message {
                    if let Some(text) = msg.content {
                        emit_chunk(app, &text, false);
                    }
                }
                if chunk_data.done == Some(true) {
                    emit_chunk(app, "", true);
                    return Ok(());
                }
            }
        }
    }

    emit_chunk(app, "", true);
    Ok(())
}

/// Streaming chat that emits tagged `group-chat-chunk` events (for multi-model group chat).
pub async fn stream_chat_tagged(
    request: ChatRequest,
    app: &AppHandle,
    base_url: Option<&String>,
    model_id: &str,
) -> Result<(), String> {
    let body = OllamaRequest {
        model: request.model.clone(),
        messages: build_messages(&request),
        temperature: request.temperature,
        stream: true,
    };

    let url = base_url
        .map(|s| s.as_str())
        .unwrap_or("http://localhost:11434/api/chat");

    let client = reqwest::Client::new();
    let resp = client
        .post(url)
        .json(&body)
        .send()
        .await
        .map_err(|e| format!("Ollama stream request failed: {}", e))?;

    if !resp.status().is_success() {
        let status = resp.status();
        let text = resp.text().await.unwrap_or_default();
        return Err(format!("Ollama error {}: {}", status, text));
    }

    let mut stream = resp.bytes_stream();
    let mut byte_buf: Vec<u8> = Vec::new();

    while let Some(chunk) = stream.next().await {
        let chunk = chunk.map_err(|e| format!("Stream read error: {}", e))?;
        byte_buf.extend_from_slice(&chunk);

        loop {
            let sep = match byte_buf.iter().position(|&b| b == b'\n') {
                Some(p) => p,
                None => break,
            };
            let line_bytes = byte_buf[..sep].to_vec();
            byte_buf = byte_buf[sep + 1..].to_vec();
            let line = match String::from_utf8(line_bytes) {
                Ok(s) => s,
                Err(_) => continue,
            };
            if line.trim().is_empty() {
                continue;
            }
            if let Ok(chunk_data) = serde_json::from_str::<OllamaStreamChunk>(&line) {
                if let Some(msg) = chunk_data.message {
                    if let Some(text) = msg.content {
                        if !text.is_empty() {
                            super::emit_group_chunk(app, model_id, &text, false);
                        }
                    }
                }
                if chunk_data.done == Some(true) {
                    super::emit_group_chunk(app, model_id, "", true);
                    return Ok(());
                }
            }
        }
    }

    super::emit_group_chunk(app, model_id, "", true);
    Ok(())
}
