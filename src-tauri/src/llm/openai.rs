use super::{emit_chunk, ChatRequest, ChatResponse, Usage};
use futures::StreamExt;
use serde::{Deserialize, Serialize};
use tauri::AppHandle;

#[derive(Serialize)]
struct OpenAIRequest {
    model: String,
    messages: Vec<OpenAIMessage>,
    #[serde(skip_serializing_if = "Option::is_none")]
    max_tokens: Option<u32>,
    #[serde(skip_serializing_if = "Option::is_none")]
    temperature: Option<f64>,
    #[serde(skip_serializing_if = "std::ops::Not::not")]
    stream: bool,
}

#[derive(Serialize)]
struct OpenAIMessage {
    role: String,
    content: String,
}

#[derive(Deserialize)]
struct OpenAIResponse {
    choices: Vec<OpenAIChoice>,
    model: String,
    usage: Option<OpenAIUsage>,
}

#[derive(Deserialize)]
struct OpenAIChoice {
    message: OpenAIRespMessage,
}

#[derive(Deserialize)]
struct OpenAIRespMessage {
    content: Option<String>,
}

#[derive(Deserialize)]
struct OpenAIUsage {
    prompt_tokens: Option<u32>,
    completion_tokens: Option<u32>,
}

fn build_messages(request: &ChatRequest) -> Vec<OpenAIMessage> {
    request
        .messages
        .iter()
        .map(|m| OpenAIMessage {
            role: m.role.clone(),
            content: m.content.clone(),
        })
        .collect()
}

fn build_client_request(
    client: &reqwest::Client,
    request: &ChatRequest,
    api_key: &str,
    stream: bool,
    base_url: Option<&String>,
) -> reqwest::RequestBuilder {
    let body = OpenAIRequest {
        model: request.model.clone(),
        messages: build_messages(request),
        max_tokens: request.max_tokens,
        temperature: request.temperature,
        stream,
    };

    let url = base_url
        .map(|s| s.as_str())
        .unwrap_or("https://api.openai.com/v1/chat/completions");

    client
        .post(url)
        .header("Authorization", format!("Bearer {}", api_key))
        .header("Content-Type", "application/json")
        .json(&body)
}

/// Non-streaming chat.
pub async fn chat(request: ChatRequest, api_key: &str, base_url: Option<&String>) -> Result<ChatResponse, String> {
    let client = reqwest::Client::new();
    let resp = build_client_request(&client, &request, api_key, false, base_url)
        .send()
        .await
        .map_err(|e| format!("OpenAI API request failed: {}", e))?;

    if !resp.status().is_success() {
        let status = resp.status();
        let text = resp.text().await.unwrap_or_default();
        return Err(format!("OpenAI API error {}: {}", status, text));
    }

    let api_resp: OpenAIResponse = resp
        .json()
        .await
        .map_err(|e| format!("Failed to parse OpenAI response: {}", e))?;

    let content = api_resp
        .choices
        .first()
        .and_then(|c| c.message.content.clone())
        .unwrap_or_default();

    Ok(ChatResponse {
        content,
        model: api_resp.model,
        usage: api_resp.usage.map(|u| Usage {
            input_tokens: u.prompt_tokens,
            output_tokens: u.completion_tokens,
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
        .map_err(|e| format!("OpenAI stream request failed: {}", e))?;

    if !resp.status().is_success() {
        let status = resp.status();
        let text = resp.text().await.unwrap_or_default();
        return Err(format!("OpenAI API error {}: {}", status, text));
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
                    if data.trim() == "[DONE]" {
                        emit_chunk(app, "", true);
                        return Ok(());
                    }
                    if let Ok(parsed) = serde_json::from_str::<serde_json::Value>(data) {
                        // OpenAI SSE: choices[0].delta.content
                        if let Some(delta_text) = parsed
                            .get("choices")
                            .and_then(|c| c.get(0))
                            .and_then(|c| c.get("delta"))
                            .and_then(|d| d.get("content"))
                            .and_then(|t| t.as_str())
                        {
                            emit_chunk(app, delta_text, false);
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
        .map_err(|e| {
            let _ = std::fs::write("G:\\sci\\scipilot\\openai_error.log", &format!("send error: {}\n", e));
            format!("OpenAI stream request failed: {}", e)
        })?;

    if !resp.status().is_success() {
        let status = resp.status();
        let body = resp.text().await.unwrap_or_default();
        eprintln!("[openai_stream_tagged] error {} model={}: {}", status, request.model, body);
        return Err(format!("OpenAI API error {}: {}", status, body));
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
                    if data.trim() == "[DONE]" {
                        super::emit_group_chunk(app, model_id, "", true);
                        return Ok(());
                    }
                    if let Ok(parsed) = serde_json::from_str::<serde_json::Value>(data) {
                        if let Some(delta_text) = parsed
                            .get("choices")
                            .and_then(|c| c.get(0))
                            .and_then(|c| c.get("delta"))
                            .and_then(|d| d.get("content"))
                            .and_then(|t| t.as_str())
                        {
                            if !delta_text.is_empty() {
                                super::emit_group_chunk(app, model_id, delta_text, false);
                            }
                        }
                    }
                }
            }
        }
    }

    super::emit_group_chunk(app, model_id, "", true);
    Ok(())
}
