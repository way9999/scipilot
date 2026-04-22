use crate::state::{AppSettings, AppState};
use serde::Serialize;
use tauri::State;
#[cfg(target_os = "windows")]
use std::os::windows::process::CommandExt;
use std::process::Command;
#[cfg(target_os = "windows")]
const CREATE_NO_WINDOW: u32 = 0x08000000;

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct HostPlatform {
    pub os: String,
    pub arch: String,
    pub updater_target: String,
}

fn normalize_arch(raw: &str) -> &str {
    match raw {
        "x86_64" | "amd64" => "x86_64",
        "x86" | "i386" | "i586" | "i686" => "i686",
        "aarch64" | "arm64" => "aarch64",
        "arm" | "armv7" | "armv7l" => "armv7",
        other => other,
    }
}

fn host_platform() -> HostPlatform {
    let os = match std::env::consts::OS {
        "macos" => "darwin",
        other => other,
    };
    let arch = normalize_arch(std::env::consts::ARCH);
    HostPlatform {
        os: os.to_string(),
        arch: arch.to_string(),
        updater_target: format!("{}-{}", os, arch),
    }
}

#[tauri::command]
pub async fn get_host_platform() -> Result<HostPlatform, String> {
    Ok(host_platform())
}

#[cfg(not(target_os = "windows"))]
fn run_command_capture(command: &mut Command) -> Result<std::process::Output, String> {
    #[cfg(target_os = "windows")]
    {
        command.creation_flags(CREATE_NO_WINDOW);
    }
    command
        .output()
        .map_err(|e| format!("Failed to run system dialog command: {}", e))
}

#[cfg(not(target_os = "windows"))]
fn trim_command_output(output: &std::process::Output) -> String {
    String::from_utf8_lossy(&output.stdout).trim().to_string()
}

#[tauri::command]
pub async fn get_settings(
    state: State<'_, AppState>,
) -> Result<AppSettings, String> {
    let mut settings = state.settings.lock().map_err(|e| e.to_string())?.clone();
    settings.api_keys.clear();
    Ok(settings)
}

#[tauri::command]
pub async fn update_settings(
    state: State<'_, AppState>,
    settings: AppSettings,
) -> Result<(), String> {
    let existing_api_keys = state.settings.lock().map_err(|e| e.to_string())?.api_keys.clone();
    let mut normalized = settings.normalize();

    if normalized.api_keys.is_empty() {
        normalized.api_keys = existing_api_keys.clone();
    } else {
        for (provider, key) in existing_api_keys {
            normalized.api_keys.entry(provider).or_insert(key);
        }
    }

    normalized.save_to(&state.config_path)?;

    {
        let mut current = state.settings.lock().map_err(|e| e.to_string())?;
        *current = normalized.clone();
    }

    {
        let mut api_keys = state.api_keys.lock().map_err(|e| e.to_string())?;
        *api_keys = normalized.api_keys.clone();
    }

    Ok(())
}

#[tauri::command]
pub async fn get_project_root(
    state: State<'_, AppState>,
) -> Result<String, String> {
    Ok(state.project_root.clone())
}

#[tauri::command]
pub async fn pick_directory(
    initial_path: Option<String>,
) -> Result<Option<String>, String> {
    #[cfg(target_os = "windows")]
    {
        let escaped = initial_path
            .unwrap_or_default()
            .replace('\'', "''");

        let initial_script = if escaped.is_empty() {
            String::new()
        } else {
            format!("$dialog.SelectedPath = '{}'\n", escaped)
        };

        let script = format!(
            "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8\n\
             Add-Type -AssemblyName System.Windows.Forms\n\
             $dialog = New-Object System.Windows.Forms.FolderBrowserDialog\n\
             $dialog.Description = '选择项目文件夹'\n\
             $dialog.ShowNewFolderButton = $false\n\
             {}\
             $result = $dialog.ShowDialog()\n\
             if ($result -eq [System.Windows.Forms.DialogResult]::OK) {{\n\
               Write-Output $dialog.SelectedPath\n\
             }}\n",
            initial_script
        );

        let output = Command::new("powershell.exe")
            .args(["-NoProfile", "-STA", "-Command", &script])
            .creation_flags(CREATE_NO_WINDOW)
            .output()
            .map_err(|e| format!("Failed to open directory picker: {}", e))?;

        if !output.status.success() {
            let error = String::from_utf8_lossy(&output.stderr).trim().to_string();
            return Err(if error.is_empty() {
                "Directory picker exited unexpectedly.".to_string()
            } else {
                error
            });
        }

        let selected = String::from_utf8_lossy(&output.stdout).trim().to_string();
        if selected.is_empty() {
            Ok(None)
        } else {
            Ok(Some(selected))
        }
    }

    #[cfg(target_os = "macos")]
    {
        let initial_clause = initial_path
            .as_deref()
            .filter(|value| !value.trim().is_empty())
            .map(|value| format!(" default location POSIX file \"{}\"", value.replace('\\', "\\\\").replace('"', "\\\"")))
            .unwrap_or_default();
        let script = format!(
            "POSIX path of (choose folder with prompt \"Select project directory\"{})",
            initial_clause
        );
        let output = run_command_capture(
            Command::new("osascript")
                .args(["-e", &script])
        )?;
        if !output.status.success() {
            return Ok(None);
        }
        let selected = trim_command_output(&output);
        if selected.is_empty() {
            Ok(None)
        } else {
            Ok(Some(selected))
        }
    }

    #[cfg(target_os = "linux")]
    {
        let initial = initial_path.unwrap_or_default();
        let zenity_filename = if initial.trim().is_empty() {
            None
        } else {
            Some(format!("--filename={}/", initial.trim_end_matches('/')))
        };

        let mut zenity = Command::new("zenity");
        zenity.args(["--file-selection", "--directory", "--title=Select project directory"]);
        if let Some(filename_arg) = zenity_filename.as_deref() {
            zenity.arg(filename_arg);
        }
        if let Ok(output) = run_command_capture(&mut zenity) {
            if output.status.success() {
                let selected = trim_command_output(&output);
                return if selected.is_empty() { Ok(None) } else { Ok(Some(selected)) };
            }
        }

        let mut kdialog = Command::new("kdialog");
        kdialog.arg("--getexistingdirectory");
        if !initial.trim().is_empty() {
            kdialog.arg(initial.trim());
        }
        let output = run_command_capture(&mut kdialog)?;
        if !output.status.success() {
            return Ok(None);
        }
        let selected = trim_command_output(&output);
        if selected.is_empty() {
            Ok(None)
        } else {
            Ok(Some(selected))
        }
    }

    #[cfg(not(any(target_os = "windows", target_os = "macos", target_os = "linux")))]
    {
        let _ = initial_path;
        Err("Directory picker is not implemented on this platform.".to_string())
    }
}

#[tauri::command]
pub async fn pick_files(
    initial_path: Option<String>,
) -> Result<Option<Vec<String>>, String> {
    #[cfg(target_os = "windows")]
    {
        let escaped = initial_path.unwrap_or_default().replace('\'', "''");
        let initial_script = if escaped.is_empty() {
            String::new()
        } else {
            format!("$dialog.InitialDirectory = '{}'\n", escaped)
        };

        let script = format!(
            "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8\n\
             Add-Type -AssemblyName System.Windows.Forms\n\
             $dialog = New-Object System.Windows.Forms.OpenFileDialog\n\
             $dialog.Title = 'Select reference files'\n\
             $dialog.Multiselect = $true\n\
             $dialog.Filter = 'Reference Files (*.pdf;*.txt;*.md;*.docx;*.png;*.jpg;*.jpeg;*.bmp;*.webp;*.svg)|*.pdf;*.txt;*.md;*.docx;*.png;*.jpg;*.jpeg;*.bmp;*.webp;*.svg|All Files (*.*)|*.*'\n\
             {}\
             $result = $dialog.ShowDialog()\n\
             if ($result -eq [System.Windows.Forms.DialogResult]::OK) {{\n\
               $dialog.FileNames | ForEach-Object {{ Write-Output $_ }}\n\
             }}\n",
            initial_script
        );

        let output = Command::new("powershell.exe")
            .args(["-NoProfile", "-STA", "-Command", &script])
            .creation_flags(CREATE_NO_WINDOW)
            .output()
            .map_err(|e| format!("Failed to open file picker: {}", e))?;

        if !output.status.success() {
            let error = String::from_utf8_lossy(&output.stderr).trim().to_string();
            return Err(if error.is_empty() {
                "File picker exited unexpectedly.".to_string()
            } else {
                error
            });
        }

        let selected: Vec<String> = String::from_utf8_lossy(&output.stdout)
            .lines()
            .map(|line| line.trim().to_string())
            .filter(|line| !line.is_empty())
            .collect();

        if selected.is_empty() {
            Ok(None)
        } else {
            Ok(Some(selected))
        }
    }

    #[cfg(target_os = "macos")]
    {
        let initial_clause = initial_path
            .as_deref()
            .filter(|value| !value.trim().is_empty())
            .map(|value| format!(" default location POSIX file \"{}\"", value.replace('\\', "\\\\").replace('"', "\\\"")))
            .unwrap_or_default();
        let script = format!(
            "set chosenFiles to choose file with prompt \"Select reference files\" with multiple selections allowed true{}\
\nset AppleScript's text item delimiters to linefeed\
\nset outText to \"\"\
\nrepeat with f in chosenFiles\
\n  set outText to outText & POSIX path of f & linefeed\
\nend repeat\
\nreturn outText",
            initial_clause
        );
        let output = run_command_capture(
            Command::new("osascript")
                .args(["-e", &script])
        )?;
        if !output.status.success() {
            return Ok(None);
        }
        let selected: Vec<String> = String::from_utf8_lossy(&output.stdout)
            .lines()
            .map(|line| line.trim().to_string())
            .filter(|line| !line.is_empty())
            .collect();
        if selected.is_empty() {
            Ok(None)
        } else {
            Ok(Some(selected))
        }
    }

    #[cfg(target_os = "linux")]
    {
        let initial = initial_path.unwrap_or_default();
        let filter = "Reference Files | *.pdf *.txt *.md *.docx *.png *.jpg *.jpeg *.bmp *.webp *.svg";
        let zenity_filename = if initial.trim().is_empty() {
            None
        } else {
            Some(format!("--filename={}/", initial.trim_end_matches('/')))
        };

        let mut zenity = Command::new("zenity");
        zenity.args([
            "--file-selection",
            "--multiple",
            "--separator=\n",
            "--title=Select reference files",
            "--file-filter",
            filter,
        ]);
        if let Some(filename_arg) = zenity_filename.as_deref() {
            zenity.arg(filename_arg);
        }
        if let Ok(output) = run_command_capture(&mut zenity) {
            if output.status.success() {
                let selected: Vec<String> = String::from_utf8_lossy(&output.stdout)
                    .lines()
                    .map(|line| line.trim().to_string())
                    .filter(|line| !line.is_empty())
                    .collect();
                if !selected.is_empty() {
                    return Ok(Some(selected));
                }
            }
        }

        let mut kdialog = Command::new("kdialog");
        kdialog.args(["--getopenfilename", "--multiple", "--separate-output"]);
        if !initial.trim().is_empty() {
            kdialog.arg(initial.trim());
        }
        let output = run_command_capture(&mut kdialog)?;
        if !output.status.success() {
            return Ok(None);
        }
        let selected: Vec<String> = String::from_utf8_lossy(&output.stdout)
            .lines()
            .map(|line| line.trim().to_string())
            .filter(|line| !line.is_empty())
            .collect();
        if selected.is_empty() {
            Ok(None)
        } else {
            Ok(Some(selected))
        }
    }

    #[cfg(not(any(target_os = "windows", target_os = "macos", target_os = "linux")))]
    {
        let _ = initial_path;
        Err("File picker is not implemented on this platform.".to_string())
    }
}

#[tauri::command]
pub async fn detect_agent_cli(
    agent_type: String,
) -> Result<Option<String>, String> {
    #[cfg(target_os = "windows")]
    {
        let candidates = match agent_type.as_str() {
            "claude_code" => vec!["claude.exe", "claude.cmd", "claude"],
            "codex" => vec!["codex.exe", "codex.cmd", "codex"],
            _ => return Ok(None),
        };
        for exe_name in &candidates {
            let output = Command::new("where")
                .arg(exe_name)
                .creation_flags(CREATE_NO_WINDOW)
                .output()
                .map_err(|e| format!("Failed to detect CLI: {}", e))?;
            if output.status.success() {
                let path = String::from_utf8_lossy(&output.stdout).lines().next().unwrap_or("").trim().to_string();
                if !path.is_empty() {
                    return Ok(Some(path));
                }
            }
        }
        Ok(None)
    }

    #[cfg(not(target_os = "windows"))]
    {
        let exe_name = match agent_type.as_str() {
            "claude_code" => "claude",
            "codex" => "codex",
            _ => return Ok(None),
        };
        let output = std::process::Command::new("which")
            .arg(exe_name)
            .output()
            .map_err(|e| format!("Failed to detect CLI: {}", e))?;
        if output.status.success() {
            let path = String::from_utf8_lossy(&output.stdout).trim().to_string();
            if !path.is_empty() {
                return Ok(Some(path));
            }
        }
        Ok(None)
    }
}
