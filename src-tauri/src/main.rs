#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

//! Desktop shell: start the bundled Python engine on a free loopback port, show
//! a lightweight splash page, then hand the window to the local web app once
//! the engine answers. The child process is killed when the window closes.

use std::fs::File;
use std::net::{TcpListener, TcpStream};
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::time::{Duration, Instant};

use tauri::{Manager, RunEvent};

struct Backend(Mutex<Option<Child>>);

fn free_port() -> Option<u16> {
    TcpListener::bind("127.0.0.1:0")
        .ok()
        .and_then(|listener| listener.local_addr().ok())
        .map(|addr| addr.port())
}

fn port_open(port: u16) -> bool {
    let addr = match format!("127.0.0.1:{port}").parse() {
        Ok(addr) => addr,
        Err(_) => return false,
    };
    TcpStream::connect_timeout(&addr, Duration::from_millis(250)).is_ok()
}

/// The engine lives beside the app when installed. During development it is not
/// bundled, so fall back to the repository build output.
fn find_server(resource_dir: &PathBuf) -> Option<PathBuf> {
    let bundled = resource_dir.join("sidecar").join("localvoice-server.exe");
    if bundled.exists() {
        return Some(bundled);
    }
    let dev = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("sidecar")
        .join("localvoice-server.exe");
    dev.exists().then_some(dev)
}

fn find_models(resource_dir: &PathBuf) -> Option<PathBuf> {
    let bundled = resource_dir.join("models");
    if bundled.join("kokoro-v1.0.onnx").exists() {
        return Some(bundled);
    }
    let dev = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("models")
        .join("kokoro-v1.0.onnx");
    dev.exists()
        .then(|| PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("models"))
}

fn splash_failure(window: &tauri::WebviewWindow, message: &str) {
    let script = format!(
        "(() => {{ const m = document.getElementById('msg'); if (m) m.textContent = {}; }})()",
        serde_json::to_string(message).unwrap_or_else(|_| "\"Engine failed to start\"".into())
    );
    let _ = window.eval(&script);
}

fn main() {
    tauri::Builder::default()
        .manage(Backend(Mutex::new(None)))
        .setup(|app| {
            let window = app
                .get_webview_window("main")
                .expect("the main window is declared in tauri.conf.json");

            let resource_dir = app
                .path()
                .resource_dir()
                .unwrap_or_else(|_| PathBuf::from("."));

            let server = match find_server(&resource_dir) {
                Some(path) => path,
                None => {
                    splash_failure(
                        &window,
                        "The speech engine is missing from this install. Reinstall Local Voice.",
                    );
                    return Ok(());
                }
            };

            let models = find_models(&resource_dir);
            let port = match free_port() {
                Some(port) => port,
                None => {
                    splash_failure(&window, "No free local port. Close other copies of Local Voice.");
                    return Ok(());
                }
            };

            let data_dir = app
                .path()
                .app_data_dir()
                .unwrap_or_else(|_| std::env::temp_dir())
                .join("exports");
            let _ = std::fs::create_dir_all(&data_dir);

            let log_path = app
                .path()
                .app_log_dir()
                .unwrap_or_else(|_| std::env::temp_dir());
            let _ = std::fs::create_dir_all(&log_path);
            let log = File::create(log_path.join("engine.log")).ok();

            let port_arg = port.to_string();
            let mut command = Command::new(&server);
            command.args(["--no-browser", "--host", "127.0.0.1", "--port", &port_arg]);
            command.env("TTS_EXPORTS_DIR", &data_dir);
            if let Some(models) = models {
                command.env("TTS_MODELS_DIR", models);
            }
            if let Some(log) = &log {
                if let Ok(copy) = log.try_clone() {
                    command.stdout(Stdio::from(copy));
                }
                command.stderr(Stdio::from(log.try_clone().unwrap_or_else(|_| {
                    File::create(std::env::temp_dir().join("local-voice-engine.log"))
                        .expect("a log file to write to")
                })));
            } else {
                command.stdout(Stdio::null()).stderr(Stdio::null());
            }

            match command.spawn() {
                Ok(child) => {
                    *app.state::<Backend>().0.lock().unwrap() = Some(child);
                }
                Err(error) => {
                    splash_failure(&window, &format!("The speech engine did not start: {error}"));
                    return Ok(());
                }
            }

            // The engine loads the model before it accepts connections, so poll
            // the port and only then navigate to the real UI.
            let url = format!("http://127.0.0.1:{port}/");
            std::thread::spawn(move || {
                let deadline = Instant::now() + Duration::from_secs(120);
                while Instant::now() < deadline {
                    if port_open(port) {
                        if let Ok(parsed) = url.parse() {
                            let _ = window.navigate(parsed);
                        }
                        return;
                    }
                    std::thread::sleep(Duration::from_millis(150));
                }
                splash_failure(
                    &window,
                    "The speech engine took too long to start. Close Local Voice and open it again.",
                );
            });

            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("failed to build the Local Voice window")
        .run(|app, event| {
            if let RunEvent::Exit = event {
                if let Some(state) = app.try_state::<Backend>() {
                    if let Some(mut child) = state.0.lock().unwrap().take() {
                        let _ = child.kill();
                        let _ = child.wait();
                    }
                }
            }
        });
}
