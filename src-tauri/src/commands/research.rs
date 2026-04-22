use crate::state::AppState;
use serde::{Deserialize, Serialize};
use std::path::{Path, PathBuf};
use tauri::State;

#[derive(Debug, Serialize, Deserialize)]
pub struct SidecarResponse {
    pub success: bool,
    pub data: Option<serde_json::Value>,
    pub error: Option<String>,
}

fn resolve_user_path(project_root: &str, path_value: &str) -> Result<PathBuf, String> {
    let raw = PathBuf::from(path_value);
    let candidate = if raw.is_absolute() {
        raw
    } else {
        Path::new(project_root).join(&raw)
    };

    if candidate.exists() {
        return Ok(strip_windows_verbatim_prefix(candidate));
    }

    Err(format!("File not found: {}", path_value))
}

#[cfg(target_os = "windows")]
fn strip_windows_verbatim_prefix(path: PathBuf) -> PathBuf {
    let rendered = path.to_string_lossy();
    if let Some(stripped) = rendered.strip_prefix(r"\\?\") {
        return PathBuf::from(stripped);
    }
    path
}

#[cfg(not(target_os = "windows"))]
fn strip_windows_verbatim_prefix(path: PathBuf) -> PathBuf {
    path
}

/// Proxy a request to the Python sidecar.
async fn sidecar_request(
    base_url: &str,
    method: &str,
    path: &str,
    body: Option<serde_json::Value>,
) -> Result<SidecarResponse, String> {
    let client = reqwest::Client::new();
    let url = format!("{}{}", base_url, path);

    let resp = match method {
        "GET" => client.get(&url).send().await,
        "POST" => {
            let mut req = client.post(&url);
            if let Some(b) = body {
                req = req.json(&b);
            }
            req.send().await
        }
        _ => return Err(format!("Unsupported method: {}", method)),
    };

    let resp = resp.map_err(|e| format!("Sidecar request failed: {}", e))?;

    if !resp.status().is_success() {
        let status = resp.status();
        let text = resp.text().await.unwrap_or_default();
        return Ok(SidecarResponse {
            success: false,
            data: None,
            error: Some(format!("Sidecar error {}: {}", status, text)),
        });
    }

    let json: serde_json::Value = resp
        .json()
        .await
        .map_err(|e| format!("Failed to parse sidecar response: {}", e))?;

    Ok(SidecarResponse {
        success: json.get("success").and_then(|v| v.as_bool()).unwrap_or(true),
        data: json.get("data").cloned().or(Some(json.clone())),
        error: json.get("error").and_then(|v| v.as_str()).map(String::from),
    })
}

#[tauri::command]
pub async fn search_papers(
    state: State<'_, AppState>,
    query: String,
    discipline: String,
    limit: u32,
    download: bool,
) -> Result<SidecarResponse, String> {
    let base_url = format!("http://127.0.0.1:{}", state.sidecar_port);
    let body = serde_json::json!({
        "query": query,
        "discipline": discipline,
        "limit": limit,
        "download": download,
    });
    sidecar_request(&base_url, "POST", "/api/search", Some(body)).await
}

#[tauri::command]
pub async fn download_paper(
    state: State<'_, AppState>,
    record_id: String,
) -> Result<SidecarResponse, String> {
    let base_url = format!("http://127.0.0.1:{}", state.sidecar_port);
    let body = serde_json::json!({ "record_id": record_id });
    sidecar_request(&base_url, "POST", "/api/download", Some(body)).await
}

#[tauri::command]
pub async fn refresh_workbench(
    state: State<'_, AppState>,
) -> Result<SidecarResponse, String> {
    let base_url = format!("http://127.0.0.1:{}", state.sidecar_port);
    sidecar_request(&base_url, "POST", "/api/refresh", None).await
}

#[tauri::command]
pub async fn get_project_state(
    state: State<'_, AppState>,
) -> Result<SidecarResponse, String> {
    let base_url = format!("http://127.0.0.1:{}", state.sidecar_port);
    sidecar_request(&base_url, "GET", "/api/state", None).await
}

#[tauri::command]
pub async fn get_papers(
    state: State<'_, AppState>,
    discipline: Option<String>,
    source: Option<String>,
) -> Result<SidecarResponse, String> {
    let base_url = format!("http://127.0.0.1:{}", state.sidecar_port);
    let mut params = vec![];
    if let Some(d) = discipline {
        params.push(format!("discipline={}", d));
    }
    if let Some(s) = source {
        params.push(format!("source={}", s));
    }
    let query_string = if params.is_empty() {
        String::new()
    } else {
        format!("?{}", params.join("&"))
    };
    sidecar_request(&base_url, "GET", &format!("/api/papers{}", query_string), None).await
}

#[tauri::command]
pub async fn get_dashboard(
    state: State<'_, AppState>,
) -> Result<SidecarResponse, String> {
    let base_url = format!("http://127.0.0.1:{}", state.sidecar_port);
    sidecar_request(&base_url, "GET", "/api/dashboard", None).await
}

#[tauri::command]
pub async fn verify_paper(
    state: State<'_, AppState>,
    title: String,
    authors: Vec<String>,
) -> Result<SidecarResponse, String> {
    let base_url = format!("http://127.0.0.1:{}", state.sidecar_port);
    let body = serde_json::json!({ "title": title, "authors": authors });
    sidecar_request(&base_url, "POST", "/api/verify", Some(body)).await
}

#[tauri::command]
pub async fn sidecar_health(
    state: State<'_, AppState>,
) -> Result<SidecarResponse, String> {
    let base_url = format!("http://127.0.0.1:{}", state.sidecar_port);
    sidecar_request(&base_url, "GET", "/api/health", None).await
}

#[tauri::command]
pub async fn get_sidecar_url(state: State<'_, AppState>) -> Result<String, String> {
    Ok(format!("http://127.0.0.1:{}", state.sidecar_port))
}

#[tauri::command]
pub async fn get_recommendations(
    state: State<'_, AppState>,
) -> Result<SidecarResponse, String> {
    let base_url = format!("http://127.0.0.1:{}", state.sidecar_port);
    sidecar_request(&base_url, "GET", "/api/recommendations", None).await
}

#[tauri::command]
pub async fn batch_download(
    state: State<'_, AppState>,
    record_ids: Vec<String>,
) -> Result<SidecarResponse, String> {
    let base_url = format!("http://127.0.0.1:{}", state.sidecar_port);
    let body = serde_json::json!({ "record_ids": record_ids });
    sidecar_request(&base_url, "POST", "/api/batch-download", Some(body)).await
}

#[tauri::command]
pub async fn batch_verify(
    state: State<'_, AppState>,
    record_ids: Vec<String>,
) -> Result<SidecarResponse, String> {
    let base_url = format!("http://127.0.0.1:{}", state.sidecar_port);
    let body = serde_json::json!({ "record_ids": record_ids });
    sidecar_request(&base_url, "POST", "/api/batch-verify", Some(body)).await
}

#[tauri::command]
pub async fn crawl_paper(
    state: State<'_, AppState>,
    record_id: String,
) -> Result<SidecarResponse, String> {
    let base_url = format!("http://127.0.0.1:{}", state.sidecar_port);
    let body = serde_json::json!({ "record_id": record_id });
    sidecar_request(&base_url, "POST", "/api/crawl", Some(body)).await
}

#[tauri::command]
pub async fn batch_crawl(
    state: State<'_, AppState>,
    record_ids: Vec<String>,
) -> Result<SidecarResponse, String> {
    let base_url = format!("http://127.0.0.1:{}", state.sidecar_port);
    let body = serde_json::json!({ "record_ids": record_ids });
    sidecar_request(&base_url, "POST", "/api/batch-crawl", Some(body)).await
}

#[tauri::command]
pub async fn landscape_analyze(
    state: State<'_, AppState>,
    topic: String,
    discipline: String,
    limit: u32,
) -> Result<SidecarResponse, String> {
    let base_url = format!("http://127.0.0.1:{}", state.sidecar_port);
    let body = serde_json::json!({
        "topic": topic,
        "discipline": discipline,
        "limit": limit,
        "save": true,
    });
    sidecar_request(&base_url, "POST", "/api/landscape/analyze", Some(body)).await
}

/// Read a file relative to the project root (for outline/draft display).
#[tauri::command]
pub async fn generate_paper_draft(
    state: State<'_, AppState>,
    topic: String,
    language: String,
    paper_type: String,
) -> Result<SidecarResponse, String> {
    let base_url = format!("http://127.0.0.1:{}", state.sidecar_port);
    let body = serde_json::json!({
        "topic": topic,
        "language": language,
        "paper_type": paper_type,
    });
    sidecar_request(&base_url, "POST", "/api/writing/paper", Some(body)).await
}

#[tauri::command]
pub async fn generate_paper_from_project(
    state: State<'_, AppState>,
    source_project: String,
    topic: String,
    language: String,
    paper_type: String,
) -> Result<SidecarResponse, String> {
    let base_url = format!("http://127.0.0.1:{}", state.sidecar_port);
    let body = serde_json::json!({
        "source_project": source_project,
        "topic": topic,
        "language": language,
        "paper_type": paper_type,
    });
    sidecar_request(&base_url, "POST", "/api/writing/project-paper", Some(body)).await
}

#[tauri::command]
pub async fn start_generate_paper_draft(
    state: State<'_, AppState>,
    topic: String,
    language: String,
    paper_type: String,
    target_words: Option<i32>,
    reference_files: Option<Vec<String>>,
) -> Result<SidecarResponse, String> {
    let base_url = format!("http://127.0.0.1:{}", state.sidecar_port);
    let mut body = serde_json::json!({
        "topic": topic,
        "language": language,
        "paper_type": paper_type,
        "target_words": target_words,
    });
    if let Some(files) = reference_files {
        if !files.is_empty() {
            body["reference_files"] = serde_json::json!(files);
        }
    }
    sidecar_request(&base_url, "POST", "/api/writing/paper/start", Some(body)).await
}

#[tauri::command]
pub async fn start_generate_paper_from_project(
    state: State<'_, AppState>,
    source_project: String,
    topic: String,
    language: String,
    paper_type: String,
    target_words: Option<i32>,
    reference_files: Option<Vec<String>>,
) -> Result<SidecarResponse, String> {
    let base_url = format!("http://127.0.0.1:{}", state.sidecar_port);
    let mut body = serde_json::json!({
        "source_project": source_project,
        "topic": topic,
        "language": language,
        "paper_type": paper_type,
        "target_words": target_words,
    });
    if let Some(files) = reference_files {
        if !files.is_empty() {
            body["reference_files"] = serde_json::json!(files);
        }
    }
    sidecar_request(&base_url, "POST", "/api/writing/project-paper/start", Some(body)).await
}

#[tauri::command]
pub async fn start_generate_proposal(
    state: State<'_, AppState>,
    topic: String,
    language: String,
) -> Result<SidecarResponse, String> {
    let base_url = format!("http://127.0.0.1:{}", state.sidecar_port);
    let body = serde_json::json!({
        "topic": topic,
        "language": language,
    });
    sidecar_request(&base_url, "POST", "/api/writing/proposal/start", Some(body)).await
}

#[tauri::command]
pub async fn start_generate_presentation(
    state: State<'_, AppState>,
    topic: String,
    language: String,
    deck_type: String,
) -> Result<SidecarResponse, String> {
    let base_url = format!("http://127.0.0.1:{}", state.sidecar_port);
    let body = serde_json::json!({
        "topic": topic,
        "language": language,
        "deck_type": deck_type,
    });
    sidecar_request(&base_url, "POST", "/api/writing/presentation/start", Some(body)).await
}

#[tauri::command]
pub async fn start_generate_literature_review(
    state: State<'_, AppState>,
    topic: String,
    language: String,
) -> Result<SidecarResponse, String> {
    let base_url = format!("http://127.0.0.1:{}", state.sidecar_port);
    let body = serde_json::json!({
        "topic": topic,
        "language": language,
    });
    sidecar_request(&base_url, "POST", "/api/writing/literature-review/start", Some(body)).await
}

#[tauri::command]
pub async fn start_refine_draft(
    state: State<'_, AppState>,
    source: String,
    language: String,
) -> Result<SidecarResponse, String> {
    let base_url = format!("http://127.0.0.1:{}", state.sidecar_port);
    let body = serde_json::json!({
        "source": source,
        "language": language,
    });
    sidecar_request(&base_url, "POST", "/api/writing/refine/start", Some(body)).await
}

#[tauri::command]
pub async fn start_answer_research_question(
    state: State<'_, AppState>,
    question: String,
    language: String,
) -> Result<SidecarResponse, String> {
    let base_url = format!("http://127.0.0.1:{}", state.sidecar_port);
    let body = serde_json::json!({
        "question": question,
        "language": language,
    });
    sidecar_request(&base_url, "POST", "/api/writing/research-qa/start", Some(body)).await
}

#[tauri::command]
pub async fn start_export_docx(
    state: State<'_, AppState>,
    artifact: String,
    source: Option<String>,
    output: Option<String>,
    topic: Option<String>,
    question: Option<String>,
    language: String,
    paper_type: String,
    target_words: Option<i32>,
    docx_style: String,
    deck_type: String,
) -> Result<SidecarResponse, String> {
    let base_url = format!("http://127.0.0.1:{}", state.sidecar_port);
    let body = serde_json::json!({
        "artifact": artifact,
        "source": source,
        "output": output,
        "topic": topic,
        "question": question,
        "language": language,
        "paper_type": paper_type,
        "target_words": target_words,
        "docx_style": docx_style,
        "deck_type": deck_type,
    });
    sidecar_request(&base_url, "POST", "/api/writing/export-docx/start", Some(body)).await
}

#[tauri::command]
pub async fn start_export_pptx(
    state: State<'_, AppState>,
    source: Option<String>,
    output: Option<String>,
    topic: Option<String>,
    language: String,
    deck_type: String,
) -> Result<SidecarResponse, String> {
    let base_url = format!("http://127.0.0.1:{}", state.sidecar_port);
    let body = serde_json::json!({
        "source": source,
        "output": output,
        "topic": topic,
        "language": language,
        "deck_type": deck_type,
    });
    sidecar_request(&base_url, "POST", "/api/writing/export-pptx/start", Some(body)).await
}

#[tauri::command]
pub async fn get_writing_task_status(
    state: State<'_, AppState>,
    task_id: String,
) -> Result<SidecarResponse, String> {
    let base_url = format!("http://127.0.0.1:{}", state.sidecar_port);
    sidecar_request(
        &base_url,
        "GET",
        &format!("/api/writing/tasks/{}", task_id),
        None,
    )
    .await
}

#[tauri::command]
pub async fn cancel_writing_task(
    state: State<'_, AppState>,
    task_id: String,
) -> Result<SidecarResponse, String> {
    let base_url = format!("http://127.0.0.1:{}", state.sidecar_port);
    sidecar_request(
        &base_url,
        "POST",
        &format!("/api/writing/tasks/{}/cancel", task_id),
        None,
    )
    .await
}

#[tauri::command]
pub async fn read_project_file(
    state: State<'_, AppState>,
    rel_path: String,
) -> Result<String, String> {
    let full = std::path::Path::new(&state.project_root).join(&rel_path);
    std::fs::read_to_string(&full)
        .map_err(|e| format!("Cannot read {}: {}", rel_path, e))
}

#[tauri::command]
pub async fn delete_project_file(
    state: State<'_, AppState>,
    rel_path: String,
) -> Result<(), String> {
    let full = std::path::Path::new(&state.project_root).join(&rel_path);
    if !full.exists() {
        return Err(format!("File not found: {}", rel_path));
    }
    std::fs::remove_file(&full)
        .map_err(|e| format!("Cannot delete {}: {}", rel_path, e))
}

#[tauri::command]
pub async fn read_project_file_binary(
    state: State<'_, AppState>,
    rel_path: String,
) -> Result<String, String> {
    let full = std::path::Path::new(&state.project_root).join(&rel_path);
    if !full.exists() {
        return Err(format!("File not found: {}", rel_path));
    }
    let bytes = std::fs::read(&full)
        .map_err(|e| format!("Cannot read {}: {}", rel_path, e))?;
    use base64::Engine;
    Ok(base64::engine::general_purpose::STANDARD.encode(&bytes))
}

#[tauri::command]
pub async fn open_file_in_system(
    state: State<'_, AppState>,
    rel_path: String,
) -> Result<(), String> {
    let full = resolve_user_path(&state.project_root, &rel_path)?;
    opener::open(&full)
        .map_err(|e| format!("Cannot open {}: {}", rel_path, e))
}

#[tauri::command]
pub async fn show_in_file_manager(
    state: State<'_, AppState>,
    rel_path: String,
) -> Result<(), String> {
    let full = resolve_user_path(&state.project_root, &rel_path)?;
    #[cfg(target_os = "windows")]
    {
        let mut command = std::process::Command::new("explorer");
        if full.is_dir() {
            command.arg(&full);
        } else {
            command.arg("/select,").arg(&full);
        }
        command
            .spawn()
            .map_err(|e| format!("Cannot open folder: {}", e))?;
    }
    #[cfg(target_os = "macos")]
    {
        if full.is_dir() {
            std::process::Command::new("open")
                .arg(&full)
                .spawn()
                .map_err(|e| format!("Cannot open folder: {}", e))?;
        } else {
            std::process::Command::new("open")
                .args(["-R", &full.to_string_lossy()])
                .spawn()
                .map_err(|e| format!("Cannot open folder: {}", e))?;
        }
    }
    #[cfg(target_os = "linux")]
    {
        let target = if full.is_dir() {
            full.as_path()
        } else {
            full.parent().unwrap_or(&full)
        };
        opener::open(target).map_err(|e| format!("Cannot open folder: {}", e))?;
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::resolve_user_path;
    use std::path::PathBuf;

    #[test]
    fn resolve_user_path_supports_relative_paths() {
        let root = std::env::temp_dir().join(format!("scipilot-rel-{}", std::process::id()));
        let nested = root.join("output").join("exports");
        std::fs::create_dir_all(&nested).unwrap();
        let file = nested.join("paper.docx");
        std::fs::write(&file, b"ok").unwrap();

        let resolved = resolve_user_path(root.to_string_lossy().as_ref(), "output/exports/paper.docx").unwrap();
        assert_eq!(resolved, file);

        let _ = std::fs::remove_file(&file);
        let _ = std::fs::remove_dir_all(&root);
    }

    #[test]
    fn resolve_user_path_supports_absolute_paths() {
        let root = std::env::temp_dir().join(format!("scipilot-abs-root-{}", std::process::id()));
        let other_root = std::env::temp_dir().join(format!("scipilot-abs-file-{}", std::process::id()));
        std::fs::create_dir_all(&root).unwrap();
        std::fs::create_dir_all(&other_root).unwrap();
        let file = other_root.join("paper.docx");
        std::fs::write(&file, b"ok").unwrap();

        let resolved = resolve_user_path(root.to_string_lossy().as_ref(), file.to_string_lossy().as_ref()).unwrap();
        assert_eq!(resolved, PathBuf::from(&file));

        let _ = std::fs::remove_file(&file);
        let _ = std::fs::remove_dir_all(&root);
        let _ = std::fs::remove_dir_all(&other_root);
    }
}

#[tauri::command]
pub async fn landscape_report(
    state: State<'_, AppState>,
    topic: String,
    discipline: String,
    limit: u32,
) -> Result<SidecarResponse, String> {
    let base_url = format!("http://127.0.0.1:{}", state.sidecar_port);
    let body = serde_json::json!({
        "topic": topic,
        "discipline": discipline,
        "limit": limit,
        "save": true,
    });
    sidecar_request(&base_url, "POST", "/api/landscape/report", Some(body)).await
}
