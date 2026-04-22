mod commands;
mod llm;
mod sidecar;
mod state;

use state::{AppSettings, AppState};
use std::path::{Path, PathBuf};
use std::sync::Mutex;
use tauri::{Manager, WebviewUrl, WebviewWindowBuilder};

/// Find the sci project root by walking up from a starting directory
/// looking for the `tools/` directory that contains unified_search.py.
/// Returns `None` when running from an installed bundle where no repo exists.
fn find_project_root_opt() -> Option<PathBuf> {
    if let Ok(root) = std::env::var("SCIPILOT_PROJECT_ROOT") {
        let p = PathBuf::from(&root);
        if p.join("tools").join("unified_search.py").exists() {
            return Some(p);
        }
    }
    if let Ok(cwd) = std::env::current_dir() {
        if let Some(root) = walk_up_for_tools(&cwd) {
            return Some(root);
        }
    }
    if let Ok(exe) = std::env::current_exe() {
        if let Some(dir) = exe.parent() {
            if let Some(root) = walk_up_for_tools(&dir.to_path_buf()) {
                return Some(root);
            }
        }
    }
    None
}

fn walk_up_for_tools(start: &PathBuf) -> Option<PathBuf> {
    let mut dir = start.clone();
    for _ in 0..10 {
        if dir.join("tools").join("unified_search.py").exists() {
            return Some(dir);
        }
        if !dir.pop() {
            break;
        }
    }
    None
}

/// Seed the per-user settings.json from the bundled default template on first
/// run.  Never overwrites an existing user file.
fn seed_settings_file(config_path: &Path, bundled_default: Option<&Path>) {
    if config_path.exists() {
        return;
    }
    if let Some(parent) = config_path.parent() {
        let _ = std::fs::create_dir_all(parent);
    }
    if let Some(src) = bundled_default {
        if src.exists() {
            if std::fs::copy(src, config_path).is_ok() {
                return;
            }
        }
    }
    // Fallback: write library defaults.
    let _ = AppSettings::default().save_to(config_path);
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let builder = tauri::Builder::default().plugin(tauri_plugin_opener::init());
    #[cfg(not(debug_assertions))]
    let builder = builder.plugin(tauri_plugin_updater::Builder::new().build());

    builder
        .setup(|app| {
            let handle = app.handle();

            // --- Resolve paths ----------------------------------------------
            // Config file (settings.json) goes in the OS config dir so we can
            // ship a signed installer without writing into Program Files.
            let config_dir = handle
                .path()
                .app_config_dir()
                .unwrap_or_else(|_| PathBuf::from("."));
            let config_path = config_dir.join("settings.json");

            // User data dir holds drafts/, output/, papers/, knowledge-base/.
            let user_data_root = handle
                .path()
                .app_data_dir()
                .unwrap_or_else(|_| config_dir.clone());

            // Resource dir: in dev this resolves under src-tauri/; when
            // installed it's the platform's resource folder.
            let resource_root = handle
                .path()
                .resource_dir()
                .unwrap_or_else(|_| PathBuf::from("."));

            // Development fallback: if we can still find the repo, prefer it
            // so scripts keep working during `pnpm tauri dev`.
            let dev_project_root = find_project_root_opt();
            let effective_resource_root = dev_project_root
                .clone()
                .unwrap_or_else(|| resource_root.clone());
            let effective_user_root = dev_project_root
                .clone()
                .unwrap_or_else(|| user_data_root.clone());

            // Seed the user settings file from the bundled default on first run.
            let bundled_default = resource_root.join("settings.default.json");
            seed_settings_file(&config_path, Some(&bundled_default));

            // Ensure writable user subdirs exist (installed builds only).
            if dev_project_root.is_none() {
                for sub in ["drafts", "output", "papers", "knowledge-base"] {
                    let _ = std::fs::create_dir_all(user_data_root.join(sub));
                }
            }

            eprintln!("SciPilot config: {}", config_path.display());
            eprintln!("SciPilot user data: {}", effective_user_root.display());
            eprintln!("SciPilot resources: {}", effective_resource_root.display());

            // --- Load settings + start sidecar ------------------------------
            let settings = AppSettings::load_from(&config_path);
            let loaded_keys = settings.api_keys.clone();

            let sidecar_mgr = Box::new(sidecar::SidecarManager::new());
            let sidecar_port = sidecar_mgr.port();
            eprintln!("Sidecar assigned port: {}", sidecar_port);

            let sidecar_resource_root = effective_resource_root.to_string_lossy().to_string();
            let sidecar_user_root = effective_user_root.to_string_lossy().to_string();
            let leaked_sidecar_mgr: &'static sidecar::SidecarManager = Box::leak(sidecar_mgr);
            if settings.sidecar_auto_start {
                let config_path_str = config_path.to_string_lossy().to_string();
                let loaded_keys_clone = loaded_keys.clone();
                let agent_enabled = settings.agent_enabled;
                let agent_type = settings.agent_type.clone();
                let agent_path = settings.agent_path.clone();
                let agent_max_turns = settings.agent_max_turns;
                let agent_timeout_secs = settings.agent_timeout_secs;
                let agent_auto_fix = settings.agent_auto_fix;
                let agent_auto_supplement = settings.agent_auto_supplement;
                std::thread::spawn(move || {
                    if let Err(e) = leaked_sidecar_mgr.start(
                        &sidecar_resource_root,
                        &sidecar_user_root,
                        &config_path_str,
                        &loaded_keys_clone,
                        agent_enabled,
                        &agent_type,
                        &agent_path,
                        agent_max_turns,
                        agent_timeout_secs,
                        agent_auto_fix,
                        agent_auto_supplement,
                    ) {
                        eprintln!("Warning: Failed to start sidecar: {}", e);
                    }
                });
            } else {
                eprintln!("Sidecar auto-start disabled in settings");
            }
            // Intentionally leak — Drop would kill the sidecar before Tauri's
            // event loop ends.

            let app_state = AppState {
                sidecar_port,
                config_path,
                user_data_root: effective_user_root.to_string_lossy().to_string(),
                resource_root: effective_resource_root.to_string_lossy().to_string(),
                project_root: effective_user_root.to_string_lossy().to_string(),
                settings: Mutex::new(settings),
                api_keys: Mutex::new(loaded_keys),
            };
            app.manage(app_state);

            // --- Window -----------------------------------------------------
            let window = if let Some(existing) = app.get_webview_window("main") {
                existing
            } else {
                WebviewWindowBuilder::new(app, "main", WebviewUrl::App("index.html".into()))
                    .title("SciPilot")
                    .inner_size(1280.0, 860.0)
                    .min_inner_size(900.0, 600.0)
                    .center()
                    .build()
                    .map_err(|e| -> Box<dyn std::error::Error> { Box::new(e) })?
            };
            let _ = window.show();
            let _ = window.set_focus();
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            // Research commands
            commands::research::search_papers,
            commands::research::download_paper,
            commands::research::refresh_workbench,
            commands::research::get_project_state,
            commands::research::get_papers,
            commands::research::get_dashboard,
            commands::research::verify_paper,
            commands::research::sidecar_health,
            commands::research::get_recommendations,
            commands::research::get_sidecar_url,
            commands::research::batch_download,
            commands::research::batch_verify,
            commands::research::crawl_paper,
            commands::research::batch_crawl,
            commands::research::landscape_analyze,
            commands::research::landscape_report,
            commands::research::generate_paper_draft,
            commands::research::generate_paper_from_project,
            commands::research::start_generate_paper_draft,
            commands::research::start_generate_paper_from_project,
            commands::research::start_generate_proposal,
            commands::research::start_generate_presentation,
            commands::research::start_generate_literature_review,
            commands::research::start_refine_draft,
            commands::research::start_answer_research_question,
            commands::research::start_export_docx,
            commands::research::start_export_pptx,
            commands::research::get_writing_task_status,
            commands::research::cancel_writing_task,
            commands::research::read_project_file,
            commands::research::read_project_file_binary,
            commands::research::open_file_in_system,
            commands::research::show_in_file_manager,
            commands::research::delete_project_file,
            // LLM commands
            commands::llm::set_api_key,
            commands::llm::get_providers,
            commands::llm::get_llm_config,
            commands::llm::test_llm_connection,
            commands::llm::group_chat_stream,
            // Settings commands
            commands::settings::get_settings,
            commands::settings::update_settings,
            commands::settings::get_project_root,
            commands::settings::pick_directory,
            commands::settings::pick_files,
            commands::settings::get_host_platform,
            commands::settings::detect_agent_cli,
            // License commands
            commands::license::activate_license,
            commands::license::get_license_status,
            commands::license::deactivate_license,
        ])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
