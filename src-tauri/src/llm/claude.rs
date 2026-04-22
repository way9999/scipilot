use super::{emit_chunk, ChatRequest, ChatResponse, Usage};
use futures::StreamExt;
use serde::{Deserialize, Serialize};
use tauri::AppHandle;

#[derive(Serialize)]
struct ClaudeApiRequest {
    model: String,
    max_tokens: u32,
    messages: Vec<ClaudeMessage>,
    #[serde(skip_serializing_if = "Option::is_none")]
    system: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    temperature: Option<f64>,
    #[serde(skip_serializing_if = "std::ops::Not::not")]
    stream: bool,
}

#[derive(Serialize)]
struct ClaudeMessage {
    role: String,
    content: String,
}

#[derive(Deserialize)]
struct ClaudeApiResponse {
    content: Vec<ClaudeContent>,
    model: String,
    usage: Option<ClaudeUsage>,
}

#[derive(Deserialize)]
struct ClaudeContent {
    #[serde(rename = "type")]
    content_type: String,
    text: Option<String>,
}

#[derive(Deserialize)]
struct ClaudeUsage {
    input_tokens: Option<u32>,
    output_tokens: Option<u32>,
}

fn build_messages(request: &ChatRequest, inject_system: bool) -> Vec<ClaudeMessage> {
    let system_content = if inject_system { extract_system(request) } else { None };

    let mut messages: Vec<ClaudeMessage> = request
        .messages
        .iter()
        .filter(|m| m.role != "system")
        .map(|m| ClaudeMessage {
            role: m.role.clone(),
            content: m.content.clone(),
        })
        .collect();

    // Relay (e.g. codex.makeup) ignores the top-level `system` field — inject it
    // into the first user message so the model still sees the instructions.
    if let Some(sys) = system_content {
        if let Some(first_user) = messages.iter_mut().find(|m| m.role == "user") {
            first_user.content = format!("<system>\n{}\n</system>\n\n{}", sys, first_user.content);
        }
    }

    messages
}

fn extract_system(request: &ChatRequest) -> Option<String> {
    request
        .messages
        .iter()
        .find(|m| m.role == "system")
        .map(|m| m.content.clone())
}

fn build_client_request(
    client: &reqwest::Client,
    request: &ChatRequest,
    api_key: &str,
    stream: bool,
    base_url: Option<&String>,
) -> reqwest::RequestBuilder {
    // When using a relay, inject system prompt into first user message instead
    // of the `system` field (relays like codex.makeup silently drop it).
    let using_relay = base_url.is_some();
    let body = ClaudeApiRequest {
        model: request.model.clone(),
        max_tokens: request.max_tokens.unwrap_or(4096),
        messages: build_messages(request, using_relay),
        system: if using_relay { None } else { extract_system(request) },
        temperature: request.temperature,
        stream,
    };

    let url = base_url
        .map(|s| s.as_str())
        .unwrap_or("https://api.anthropic.com/v1/messages");

    client
        .post(url)
        .header("x-api-key", api_key)
        .header("anthropic-version", "2023-06-01")
        .header("content-type", "application/json")
        .json(&body)
}

/// Non-streaming chat.
pub async fn chat(request: ChatRequest, api_key: &str, base_url: Option<&String>) -> Result<ChatResponse, String> {
    let client = reqwest::Client::new();
    let resp = build_client_request(&client, &request, api_key, false, base_url)
        .send()
        .await
        .map_err(|e| format!("Claude API request failed: {}", e))?;

    if !resp.status().is_success() {
        let status = resp.status();
        let text = resp.text().await.unwrap_or_default();
        return Err(format!("Claude API error {}: {}", status, text));
    }

    let api_resp: ClaudeApiResponse = resp
        .json()
        .await
        .map_err(|e| format!("Failed to parse Claude response: {}", e))?;

    // Log actual model used (relay may substitute a different model)
    eprintln!("[claude] requested={} actual={}", request.model, api_resp.model);

    let content = api_resp
        .content
        .into_iter()
        .filter_map(|c| if c.content_type == "text" { c.text } else { None })
        .collect::<Vec<_>>()
        .join("");

    Ok(ChatResponse {
        content,
        model: api_resp.model,
        usage: api_resp.usage.map(|u| Usage {
            input_tokens: u.input_tokens,
            output_tokens: u.output_tokens,
        }),
    })
}

/// Streaming chat — emits `llm-chunk` events via Tauri.
pub async fn stream_chat(
    request: ChatRequest,
    api_key: &str,
    app: &AppHandle,
    base_url: Option<&String>,
) -> Result<(), String> {
    let client = reqwest::Client::new();
    let resp = build_client_request(&client, &request, api_key, true, base_url)
        .send()
        .await
        .map_err(|e| format!("Claude stream request failed: {}", e))?;

    if !resp.status().is_success() {
        let status = resp.status();
        let text = resp.text().await.unwrap_or_default();
        return Err(format!("Claude API error {}: {}", status, text));
    }

    let mut stream = resp.bytes_stream();
    // Use byte buffer to avoid splitting multi-byte UTF-8 chars (e.g. Chinese) across chunks
    let mut byte_buf: Vec<u8> = Vec::new();

    while let Some(chunk) = stream.next().await {
        let chunk = chunk.map_err(|e| format!("Stream read error: {}", e))?;
        byte_buf.extend_from_slice(&chunk);

        // Process complete SSE events — split on \n\n or \r\n\r\n boundary
        loop {
            // Find separator: prefer \n\n, also handle \r\n\r\n
            let (sep, sep_len) = {
                let nn = byte_buf.windows(2).position(|w| w == b"\n\n");
                let rnrn = byte_buf.windows(4).position(|w| w == b"\r\n\r\n");
                match (nn, rnrn) {
                    (Some(a), Some(b)) if b < a => (b, 4),
                    (Some(a), _) => (a, 2),
                    (None, Some(b)) => (b, 4),
                    (None, None) => break,
                }
            };
            let event_bytes = byte_buf[..sep].to_vec();
            byte_buf = byte_buf[sep + sep_len..].to_vec();
            // Only convert to string once we have a complete event block (no split multi-byte chars)
            let event_block = match String::from_utf8(event_bytes) {
                Ok(s) => s,
                Err(_) => continue,
            };

            for line in event_block.lines() {
                if let Some(data) = line.strip_prefix("data: ") {
                    if data.trim() == "[DONE]" {
                        emit_chunk(app, "", true);
                        return Ok(());
                    }
                    if let Ok(parsed) = serde_json::from_str::<serde_json::Value>(data) {
                        let event_type = parsed.get("type").and_then(|t| t.as_str()).unwrap_or("");
                        // Log actual model from message_start (relay may substitute)
                        if event_type == "message_start" {
                            if let Some(model) = parsed.get("message").and_then(|m| m.get("model")).and_then(|m| m.as_str()) {
                                eprintln!("[claude stream] requested={} actual={}", request.model, model);
                            }
                        }
                        // content_block_delta: delta.text
                        if let Some(delta_text) = parsed
                            .get("delta")
                            .and_then(|d| d.get("text"))
                            .and_then(|t| t.as_str())
                        {
                            if !delta_text.is_empty() {
                                emit_chunk(app, delta_text, false);
                            }
                        }
                        // message_stop
                        if event_type == "message_stop" {
                            emit_chunk(app, "", true);
                            return Ok(());
                        }
                    }
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
    api_key: &str,
    app: &AppHandle,
    base_url: Option<&String>,
    model_id: &str,
) -> Result<(), String> {
    let client = reqwest::Client::new();
    let resp = build_client_request(&client, &request, api_key, true, base_url)
        .send()
        .await
        .map_err(|e| format!("Claude stream request failed: {}", e))?;

    if !resp.status().is_success() {
        let status = resp.status();
        let text = resp.text().await.unwrap_or_default();
        return Err(format!("Claude API error {}: {}", status, text));
    }

    let mut stream = resp.bytes_stream();
    let mut byte_buf: Vec<u8> = Vec::new();

    while let Some(chunk) = stream.next().await {
        let chunk = chunk.map_err(|e| format!("Stream read error: {}", e))?;
        byte_buf.extend_from_slice(&chunk);

        loop {
            let (sep, sep_len) = {
                let nn = byte_buf.windows(2).position(|w| w == b"\n\n");
                let rnrn = byte_buf.windows(4).position(|w| w == b"\r\n\r\n");
                match (nn, rnrn) {
                    (Some(a), Some(b)) if b < a => (b, 4),
                    (Some(a), _) => (a, 2),
                    (None, Some(b)) => (b, 4),
                    (None, None) => break,
                }
            };
            let event_bytes = byte_buf[..sep].to_vec();
            byte_buf = byte_buf[sep + sep_len..].to_vec();
            let event_block = match String::from_utf8(event_bytes) {
                Ok(s) => s,
                Err(_) => continue,
            };

            for line in event_block.lines() {
                if let Some(data) = line.strip_prefix("data: ") {
                    if let Ok(parsed) = serde_json::from_str::<serde_json::Value>(data) {
                        let event_type = parsed.get("type").and_then(|t| t.as_str()).unwrap_or("");
                        if let Some(delta_text) = parsed
                            .get("delta")
                            .and_then(|d| d.get("text"))
                            .and_then(|t| t.as_str())
                        {
                            if !delta_text.is_empty() {
                                super::emit_group_chunk(app, model_id, delta_text, false);
                            }
                        }
                        if event_type == "message_stop" {
                            super::emit_group_chunk(app, model_id, "", true);
                            return Ok(());
                        }
                    }
                }
            }
        }
    }

    super::emit_group_chunk(app, model_id, "", true);
    Ok(())
}
