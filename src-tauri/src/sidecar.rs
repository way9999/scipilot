use std::collections::HashMap;
use std::net::TcpListener;
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::time::{Duration, Instant};

#[cfg(target_os = "windows")]
use std::os::windows::process::CommandExt;

#[cfg(target_os = "windows")]
const CREATE_NO_WINDOW: u32 = 0x08000000;

/// Manages the Python FastAPI sidecar process lifecycle.
#[allow(dead_code)]
pub struct SidecarManager {
    process: Mutex<Option<Child>>,
    port: u16,
}

#[allow(dead_code)]
impl SidecarManager {
    pub fn new() -> Self {
        let port = find_free_port().unwrap_or(9960);
        Self {
            process: Mutex::new(None),
            port,
        }
    }

    pub fn port(&self) -> u16 {
        self.port
    }

    pub fn base_url(&self) -> String {
        format!("http://127.0.0.1:{}", self.port)
    }

    /// Start the Python sidecar process.
    pub fn start(
        &self,
        resource_root: &str,
        user_root: &str,
        config_path: &str,
        api_keys: &HashMap<String, String>,
        agent_enabled: bool,
        agent_type: &str,
        agent_path: &str,
        agent_max_turns: u32,
        agent_timeout_secs: u32,
        agent_auto_fix: bool,
        agent_auto_supplement: bool,
    ) -> Result<(), String> {
        let mut guard = self.process.lock().map_err(|e| e.to_string())?;
        if guard.is_some() {
            return Ok(());
        }

        let launch = SidecarLaunch::resolve(resource_root)?;
        let mut cmd = launch.command(self.port, config_path, user_root, api_keys);

        // Inject agent config as environment variables
        if agent_enabled {
            cmd.env("SCIPILOT_AGENT_ENABLED", "true");
            cmd.env("SCIPILOT_AGENT_TYPE", agent_type);
            if !agent_path.is_empty() {
                cmd.env("SCIPILOT_AGENT_PATH", agent_path);
            }
            cmd.env("SCIPILOT_AGENT_MAX_TURNS", agent_max_turns.to_string());
            cmd.env("SCIPILOT_AGENT_TIMEOUT", agent_timeout_secs.to_string());
            cmd.env("SCIPILOT_AGENT_AUTO_FIX", if agent_auto_fix { "true" } else { "false" });
            cmd.env("SCIPILOT_AGENT_AUTO_SUPPLEMENT", if agent_auto_supplement { "true" } else { "false" });
        }
        eprintln!("Starting sidecar: {:?}", cmd);

        let child = cmd
            .spawn()
            .map_err(|e| format!("Failed to start sidecar: {}", e))?;

        eprintln!("Sidecar process started (PID: {})", child.id());
        *guard = Some(child);

        drop(guard);
        let start = Instant::now();
        let timeout = Duration::from_secs(30);
        loop {
            if self.health_check() {
                eprintln!("Sidecar ready on port {}", self.port);
                return Ok(());
            }

            if let Ok(mut g) = self.process.lock() {
                if let Some(ref mut child) = *g {
                    if let Ok(Some(status)) = child.try_wait() {
                        return Err(format!("Sidecar exited with status: {}", status));
                    }
                }
            }

            if start.elapsed() > timeout {
                eprintln!("Warning: Sidecar did not respond within {:?}, continuing anyway", timeout);
                return Ok(());
            }

            std::thread::sleep(Duration::from_millis(500));
        }
    }

    /// Stop the sidecar process.
    pub fn stop(&self) -> Result<(), String> {
        let mut guard = self.process.lock().map_err(|e| e.to_string())?;
        if let Some(mut child) = guard.take() {
            let _ = child.kill();
            let _ = child.wait();
        }
        Ok(())
    }

    /// Check if the sidecar is responding.
    pub fn health_check(&self) -> bool {
        let url = format!("{}/api/health", self.base_url());
        reqwest::blocking::Client::new()
            .get(&url)
            .timeout(Duration::from_secs(2))
            .send()
            .map(|r| r.status().is_success())
            .unwrap_or(false)
    }
}

impl Drop for SidecarManager {
    fn drop(&mut self) {
        let _ = self.stop();
    }
}

enum SidecarLaunch {
    BundledExe { exe_path: PathBuf, resource_root: PathBuf },
    PythonScript { script_path: PathBuf, repo_root: PathBuf },
}

impl SidecarLaunch {
    fn resolve(resource_root: &str) -> Result<Self, String> {
        let root = PathBuf::from(resource_root);

        // Try bundled exe first
        for bundled_exe in [
            root.join("sidecar").join("scipilot-sidecar.exe"),
            root.join("sidecar")
                .join("dist")
                .join("scipilot-sidecar")
                .join("scipilot-sidecar.exe"),
        ] {
            if bundled_exe.exists() {
                return Ok(Self::BundledExe {
                    exe_path: bundled_exe,
                    resource_root: root.clone(),
                });
            }
        }

        // Try Python script in bundled sidecar directory
        let bundled_script = root.join("sidecar").join("server.py");
        if bundled_script.exists() {
            return Ok(Self::PythonScript {
                script_path: bundled_script,
                repo_root: root,
            });
        }

        // Try Python script in development layout
        let dev_script = root.join("scipilot").join("sidecar").join("server.py");
        if dev_script.exists() {
            return Ok(Self::PythonScript {
                script_path: dev_script,
                repo_root: root,
            });
        }

        Err(format!(
            "Sidecar entrypoint not found under {}",
            root.display()
        ))
    }

    fn command(&self, port: u16, config_path: &str, user_root: &str, api_keys: &HashMap<String, String>) -> Command {
        match self {
            Self::BundledExe {
                exe_path,
                resource_root,
            } => {
                let mut cmd = Command::new(exe_path);
                cmd.args(["--port", &port.to_string()]);
                apply_common_env(&mut cmd, config_path, user_root, api_keys);
                cmd.env("SCIPILOT_RESOURCE_ROOT", resource_root)
                    .env("SCIPILOT_SIDECAR_MODE", "bundled")
                    .stdout(Stdio::inherit())
                    .stderr(Stdio::inherit());
                #[cfg(target_os = "windows")]
                cmd.creation_flags(CREATE_NO_WINDOW);
                cmd
            }
            Self::PythonScript {
                script_path,
                repo_root,
            } => {
                let mut cmd = Command::new("python");
                cmd.args([script_path.to_string_lossy().as_ref(), "--port", &port.to_string()]);
                apply_common_env(&mut cmd, config_path, user_root, api_keys);
                cmd.env("SCIPILOT_RESOURCE_ROOT", repo_root)
                    .stdout(Stdio::inherit())
                    .stderr(Stdio::inherit());
                #[cfg(target_os = "windows")]
                cmd.creation_flags(CREATE_NO_WINDOW);
                cmd
            }
        }
    }
}

fn apply_common_env(cmd: &mut Command, config_path: &str, user_root: &str, api_keys: &HashMap<String, String>) {
    cmd.env("SCIPILOT_SETTINGS_PATH", config_path)
        .env("SCIPILOT_USER_ROOT", user_root);

    for (key_name, env_var) in [
        ("s2", "S2_API_KEY"),
        ("semantic_scholar", "S2_API_KEY"),
        ("scholarly_proxy", "SCHOLARLY_PROXY"),
    ] {
        if let Some(val) = api_keys.get(key_name) {
            if !val.is_empty() {
                cmd.env(env_var, val);
            }
        }
    }
}

fn find_free_port() -> Option<u16> {
    TcpListener::bind("127.0.0.1:0")
        .ok()
        .map(|l| l.local_addr().unwrap().port())
}
