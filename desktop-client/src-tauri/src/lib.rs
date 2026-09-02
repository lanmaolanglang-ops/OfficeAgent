// OfficeAgent Desktop - Tauri Backend
// 实现：系统托盘、文件拖拽、文件操作、通知、开机启动、窗口状态

use std::fs;
use std::io::{Read, Write};
use std::net::TcpStream;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};
use tauri::{
    menu::{Menu, MenuItem, PredefinedMenuItem},
    tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent},
    Emitter, Manager, WindowEvent,
};

const BACKEND_HOST: &str = "127.0.0.1";
const BACKEND_PORT: u16 = 8765;
const BACKEND_STARTUP_TIMEOUT: Duration = Duration::from_secs(30);

#[derive(Clone, Default)]
struct BackendManager {
    // Only contains a process started by this Tauri instance. An already-running
    // healthy backend is reused and is never terminated by the desktop client.
    child: Arc<Mutex<Option<Child>>>,
}

impl BackendManager {
    fn start(&self, app: &tauri::AppHandle) -> Result<(), String> {
        if backend_is_healthy() {
            return Ok(());
        }

        if TcpStream::connect_timeout(
            &format!("{BACKEND_HOST}:{BACKEND_PORT}")
                .parse()
                .map_err(|e| format!("Backend地址无效: {e}"))?,
            Duration::from_millis(500),
        )
        .is_ok()
        {
            // 端口被占但不健康：优先按 PID 文件接管上次异常残留的
            // OfficeAgent 后端（进程名核验后才杀，防 PID 复用误伤）
            if takeover_stale_backend() {
                let deadline = Instant::now() + Duration::from_secs(10);
                while Instant::now() < deadline {
                    if !TcpStream::connect_timeout(
                        &format!("{BACKEND_HOST}:{BACKEND_PORT}")
                            .parse()
                            .map_err(|e| format!("Backend地址无效: {e}"))?,
                        Duration::from_millis(300),
                    )
                    .is_ok()
                    {
                        break;
                    }
                    std::thread::sleep(Duration::from_millis(300));
                }
                if backend_is_healthy() {
                    return Ok(());
                }
            } else {
                return Err(format!(
                    "端口 {BACKEND_PORT} 已被其他程序占用，且该服务未通过 OfficeAgent 健康检查"
                ));
            }
        }

        let child = spawn_backend(app)?;
        *self.child.lock().map_err(|_| "Backend进程锁不可用")? = Some(child);

        let started_at = Instant::now();
        while started_at.elapsed() < BACKEND_STARTUP_TIMEOUT {
            {
                let mut guard = self.child.lock().map_err(|_| "Backend进程锁不可用")?;
                if let Some(child) = guard.as_mut() {
                    if let Some(status) = child.try_wait().map_err(|e| e.to_string())? {
                        *guard = None;
                        return Err(format!("Backend启动后立即退出，退出码: {status}"));
                    }
                }
            }

            if backend_is_healthy() {
                return Ok(());
            }
            std::thread::sleep(Duration::from_millis(500));
        }

        self.stop();
        Err(format!(
            "Backend在{}秒内未通过健康检查: http://{BACKEND_HOST}:{BACKEND_PORT}/health",
            BACKEND_STARTUP_TIMEOUT.as_secs()
        ))
    }

    fn stop(&self) {
        let Ok(mut guard) = self.child.lock() else {
            return;
        };
        if let Some(mut child) = guard.take() {
            let child_pid = child.id().to_string();
            let _ = child.kill();
            let _ = child.wait();
            // 强杀的后端不会走到自己的 shutdown 清理；残留 PID 文件会让下次
            // 启动的接管逻辑面对一个已死/可能被复用的 PID。仅删除当前子进程
            // 的记录，避免后端崩溃后由其他实例重启时误删新实例 PID 文件。
            if read_backend_pid().as_deref() == Some(child_pid.as_str()) {
                let _ = fs::remove_file(pid_file_path());
            }
        }
    }
}

/// 读取 backend.pid 并核验进程身份后强制结束残留后端。
/// 三重核验：PID 必须监听后端端口、映像名必须匹配、命令行必须指向
/// OfficeAgent 后端。仅凭 PID 文件或映像名不可靠——PID 复用后可能误杀
/// 用户自己的进程。
#[cfg(target_os = "windows")]
fn takeover_stale_backend() -> bool {
    use std::os::windows::process::CommandExt;

    let Some(pid) = read_backend_pid() else {
        return false;
    };

    // PID 文件可能陈旧；只允许接管当前实际监听后端端口的进程。
    let port_owner = Command::new("powershell")
        .args([
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            &format!(
                "Get-NetTCPConnection -State Listen -LocalPort {BACKEND_PORT} -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty OwningProcess"
            ),
        ])
        .creation_flags(0x08000000)
        .output();
    let Ok(port_owner) = port_owner else {
        return false;
    };
    if String::from_utf8_lossy(&port_owner.stdout).trim() != pid {
        return false;
    }

    // tasklist 查询该 PID 的进程映像名
    let output = Command::new("tasklist")
        .args(["/FI", &format!("PID eq {pid}"), "/FO", "CSV", "/NH"])
        .creation_flags(0x08000000)
        .output();
    let Ok(output) = output else { return false };
    let stdout = String::from_utf8_lossy(&output.stdout);
    let first_line = stdout.lines().next().unwrap_or("");
    let image_name = first_line.split('"').nth(1).unwrap_or("").to_lowercase();

    // 没有该 PID，或不是我们的后端进程形态
    if image_name != "officeagent.exe" && image_name != "python.exe" {
        return false;
    }

    // 第二重核验：命令行必须指向 OfficeAgent 后端
    let cmdline = Command::new("powershell")
        .args([
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            &format!("(Get-CimInstance Win32_Process -Filter 'ProcessId={pid}').CommandLine"),
        ])
        .creation_flags(0x08000000)
        .output();
    let Ok(cmdline) = cmdline else { return false };
    let cmdline_text = String::from_utf8_lossy(&cmdline.stdout).to_lowercase();
    if !cmdline_text.contains("officeagent") && !cmdline_text.contains("office_agent") {
        return false;
    }

    let kill = Command::new("taskkill")
        .args(["/PID", &pid, "/F"])
        .creation_flags(0x08000000)
        .output();
    match kill {
        Ok(k) => {
            if k.status.success() {
                let _ = fs::remove_file(pid_file_path());
                true
            } else {
                false
            }
        }
        Err(_) => false,
    }
}

#[cfg(not(target_os = "windows"))]
fn takeover_stale_backend() -> bool {
    false
}

fn pid_file_path() -> PathBuf {
    // 与 office_agent 侧一致：OFFICE_AGENT_DATA_DIR / %APPDATA%\OfficeAgent
    let base = std::env::var("OFFICE_AGENT_DATA_DIR")
        .map(PathBuf::from)
        .unwrap_or_else(|_| {
            #[cfg(target_os = "windows")]
            {
                let appdata = std::env::var("APPDATA").unwrap_or_default();
                PathBuf::from(appdata).join("OfficeAgent")
            }
            #[cfg(not(target_os = "windows"))]
            {
                let home = std::env::var("HOME").unwrap_or_default();
                PathBuf::from(home).join(".office_agent")
            }
        });
    base.join("backend.pid")
}

fn read_backend_pid() -> Option<String> {
    let path = pid_file_path();
    let content = fs::read_to_string(path).ok()?;
    parse_backend_pid(&content)
}

fn parse_backend_pid(content: &str) -> Option<String> {
    let pid = content.trim();
    if !pid.is_empty() && pid.chars().all(|c| c.is_ascii_digit()) {
        Some(pid.to_string())
    } else {
        None
    }
}

fn health_request() -> String {
    format!(
        "GET /health HTTP/1.1\r\nHost: {BACKEND_HOST}:{BACKEND_PORT}\r\nConnection: close\r\n\r\n"
    )
}

fn health_response_is_ready(response: &str) -> bool {
    response.starts_with("HTTP/1.1 200")
        && (response.contains("\"status\":\"healthy\"")
            || response.contains("\"status\":\"degraded\""))
}

fn backend_is_healthy() -> bool {
    let address = format!("{BACKEND_HOST}:{BACKEND_PORT}");
    let Ok(socket_address) = address.parse() else {
        return false;
    };
    let Ok(mut stream) = TcpStream::connect_timeout(&socket_address, Duration::from_secs(1)) else {
        return false;
    };

    let _ = stream.set_read_timeout(Some(Duration::from_secs(2)));
    let _ = stream.set_write_timeout(Some(Duration::from_secs(2)));
    let request = health_request();
    if stream.write_all(request.as_bytes()).is_err() {
        return false;
    }

    // 后端契约：/health 永远返回 200，status 为 healthy 或 degraded。
    // degraded 只表示某个子系统（如模型未配置）不可用，进程本身在正常运行——
    // 把 degraded 当成启动失败会误杀后端，陷入"启动失败"死循环。
    let mut response = String::new();
    stream.read_to_string(&mut response).is_ok() && health_response_is_ready(&response)
}

#[cfg(test)]
mod tests {
    use super::{health_request, health_response_is_ready, parse_backend_pid};

    #[test]
    fn pid_parser_accepts_only_decimal_process_ids() {
        assert_eq!(parse_backend_pid(" 12345\r\n"), Some("12345".to_string()));
        assert_eq!(parse_backend_pid(""), None);
        assert_eq!(parse_backend_pid("12x45"), None);
        assert_eq!(parse_backend_pid("-1"), None);
    }

    #[test]
    fn health_contract_accepts_healthy_and_degraded_backend_states() {
        assert!(health_response_is_ready(
            "HTTP/1.1 200 OK\r\n\r\n{\"status\":\"healthy\"}"
        ));
        assert!(health_response_is_ready(
            "HTTP/1.1 200 OK\r\n\r\n{\"status\":\"degraded\"}"
        ));
    }

    #[test]
    fn health_contract_rejects_wrong_status_or_http_code() {
        assert!(!health_response_is_ready(
            "HTTP/1.1 503 Service Unavailable\r\n\r\n{\"status\":\"healthy\"}"
        ));
        assert!(!health_response_is_ready(
            "HTTP/1.1 200 OK\r\n\r\n{\"status\":\"failed\"}"
        ));
    }

    #[test]
    fn health_request_is_local_and_connection_closing() {
        let request = health_request();
        assert!(request.starts_with("GET /health HTTP/1.1\r\n"));
        assert!(request.contains("Host: 127.0.0.1:8765\r\n"));
        assert!(request.ends_with("Connection: close\r\n\r\n"));
    }
}

fn spawn_backend(app: &tauri::AppHandle) -> Result<Child, String> {
    let mut command;

    if cfg!(debug_assertions) {
        let project_root = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("..")
            .join("..");
        command = Command::new("python");
        command.current_dir(&project_root).args([
            "-m",
            "uvicorn",
            "office_agent.api.main:app",
            "--host",
            BACKEND_HOST,
            "--port",
            &BACKEND_PORT.to_string(),
        ]);
    } else {
        let resource_dir = app
            .path()
            .resource_dir()
            .map_err(|e| format!("无法定位应用资源目录: {e}"))?;
        let executable = if cfg!(target_os = "windows") {
            resource_dir.join("backend").join("OfficeAgent.exe")
        } else {
            resource_dir.join("backend").join("OfficeAgent")
        };
        if !executable.exists() {
            return Err(format!("未找到Backend程序: {}", executable.display()));
        }
        command = Command::new(executable);
        command.args([
            "--host",
            BACKEND_HOST,
            "--port",
            &BACKEND_PORT.to_string(),
            "--background",
        ]);
    }

    command
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null());

    // 开发模式直接运行 uvicorn，不会经过 desktop/app_launcher.py；这里显式
    // 注入与正式包一致的数据目录，确保数据库、配置和 backend.pid 都落在
    // 同一位置。正式包重复设置同一变量也不会改变行为。
    if let Some(data_dir) = pid_file_path().parent() {
        fs::create_dir_all(data_dir)
            .map_err(|e| format!("无法创建Backend数据目录 {}: {e}", data_dir.display()))?;
        command.env("OFFICE_AGENT_DATA_DIR", data_dir);
    }

    #[cfg(target_os = "windows")]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(0x08000000);
    }

    command.spawn().map_err(|e| format!("无法启动Backend: {e}"))
}

// 自定义命令：获取应用数据目录
#[tauri::command]
fn get_app_data_dir(app: tauri::AppHandle) -> Result<String, String> {
    app.path()
        .app_data_dir()
        .map(|p| p.to_string_lossy().to_string())
        .map_err(|e| format!("无法获取应用数据目录: {}", e))
}

// 自定义命令：获取下载目录
#[tauri::command]
fn get_download_dir() -> Result<String, String> {
    #[cfg(target_os = "windows")]
    {
        if let Ok(userprofile) = std::env::var("USERPROFILE") {
            let downloads = PathBuf::from(userprofile).join("Downloads");
            if downloads.exists() {
                return Ok(downloads.to_string_lossy().to_string());
            }
        }
        // 回退到用户目录
        if let Ok(userprofile) = std::env::var("USERPROFILE") {
            return Ok(userprofile);
        }
        Ok("C:\\".to_string())
    }
    #[cfg(target_os = "macos")]
    {
        if let Ok(home) = std::env::var("HOME") {
            let downloads = PathBuf::from(home).join("Downloads");
            if downloads.exists() {
                return Ok(downloads.to_string_lossy().to_string());
            }
            return Ok(home);
        }
        Ok("/tmp".to_string())
    }
    #[cfg(target_os = "linux")]
    {
        if let Ok(home) = std::env::var("HOME") {
            let downloads = PathBuf::from(home).join("Downloads");
            if downloads.exists() {
                return Ok(downloads.to_string_lossy().to_string());
            }
            return Ok(home);
        }
        Ok("/tmp".to_string())
    }
}

// 自定义命令：退出应用
#[tauri::command]
fn quit_app(app: tauri::AppHandle) {
    app.exit(0);
}

// 自定义命令：最小化到托盘
#[tauri::command]
fn minimize_to_tray(window: tauri::WebviewWindow) {
    window.hide().unwrap_or(());
}

// "关闭窗口时最小化到托盘"设置：前端在启动和应用设置变更时同步过来。
// 默认 true，与前端 settingsStore 默认值一致。
struct MinimizeToTrayState(Mutex<bool>);

impl Default for MinimizeToTrayState {
    fn default() -> Self {
        Self(Mutex::new(true))
    }
}

// 自定义命令：更新最小化到托盘设置
#[tauri::command]
fn set_minimize_to_tray(
    state: tauri::State<'_, MinimizeToTrayState>,
    enabled: bool,
) -> Result<(), String> {
    let mut guard = state.0.lock().map_err(|_| "设置锁不可用")?;
    *guard = enabled;
    Ok(())
}

// 自定义命令：显示主窗口
#[tauri::command]
fn show_main_window(app: tauri::AppHandle) {
    if let Some(window) = app.get_webview_window("main") {
        window.show().unwrap_or(());
        window.unminimize().unwrap_or(());
        window.set_focus().unwrap_or(());
    }
}

// 自定义命令：发送系统通知
#[tauri::command]
fn send_notification(app: tauri::AppHandle, title: String, body: String) -> Result<(), String> {
    use tauri_plugin_notification::NotificationExt;
    app.notification()
        .builder()
        .title(&title)
        .body(&body)
        .show()
        .map_err(|e| format!("通知发送失败: {}", e))?;
    Ok(())
}

// 自定义命令：设置开机启动
#[tauri::command]
fn set_autostart(app: tauri::AppHandle, enabled: bool) -> Result<bool, String> {
    use tauri_plugin_autostart::ManagerExt;
    let autostart_manager = app.autolaunch();
    if enabled {
        autostart_manager
            .enable()
            .map_err(|e| format!("启用开机启动失败: {}", e))?;
    } else {
        autostart_manager
            .disable()
            .map_err(|e| format!("禁用开机启动失败: {}", e))?;
    }
    Ok(enabled)
}

// 自定义命令：获取开机启动状态
#[tauri::command]
fn get_autostart(app: tauri::AppHandle) -> Result<bool, String> {
    use tauri_plugin_autostart::ManagerExt;
    let autostart_manager = app.autolaunch();
    autostart_manager
        .is_enabled()
        .map_err(|e| format!("获取开机启动状态失败: {}", e))
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let backend_manager = BackendManager::default();
    let setup_backend_manager = backend_manager.clone();
    let exit_backend_manager = backend_manager.clone();

    tauri::Builder::default()
        // 插件
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_fs::init())
        .plugin(tauri_plugin_notification::init())
        .plugin(tauri_plugin_autostart::init(
            tauri_plugin_autostart::MacosLauncher::LaunchAgent,
            Some(vec!["--autostart"]),
        ))
        .plugin(tauri_plugin_window_state::Builder::default().build())
        .manage(MinimizeToTrayState::default())
        // 自定义命令
        .invoke_handler(tauri::generate_handler![
            get_app_data_dir,
            get_download_dir,
            quit_app,
            minimize_to_tray,
            set_minimize_to_tray,
            show_main_window,
            send_notification,
            set_autostart,
            get_autostart,
        ])
        // 设置
        .setup(move |app| {
            // 创建系统托盘菜单
            let show_item = MenuItem::with_id(app, "show", "显示 OfficeAgent", true, None::<&str>)?;
            let status_item =
                MenuItem::with_id(app, "status", "Backend: 检查中...", false, None::<&str>)?;
            let separator = PredefinedMenuItem::separator(app)?;
            let quit_item = MenuItem::with_id(app, "quit", "退出", true, None::<&str>)?;

            let menu = Menu::with_items(app, &[&show_item, &status_item, &separator, &quit_item])?;

            // 创建托盘图标
            let mut tray_builder = TrayIconBuilder::with_id("main-tray");
            if let Some(icon) = app.default_window_icon() {
                tray_builder = tray_builder.icon(icon.clone());
            }
            let _tray = tray_builder
                .tooltip("OfficeAgent - 智能办公助手")
                .menu(&menu)
                .show_menu_on_left_click(false)
                .on_menu_event(|app, event| match event.id.as_ref() {
                    "show" => {
                        if let Some(window) = app.get_webview_window("main") {
                            window.show().unwrap_or(());
                            window.unminimize().unwrap_or(());
                            window.set_focus().unwrap_or(());
                        }
                    }
                    "quit" => {
                        app.exit(0);
                    }
                    _ => {}
                })
                .on_tray_icon_event(|tray, event| {
                    if let TrayIconEvent::Click {
                        button: MouseButton::Left,
                        button_state: MouseButtonState::Up,
                        ..
                    } = event
                    {
                        let app = tray.app_handle();
                        if let Some(window) = app.get_webview_window("main") {
                            window.show().unwrap_or(());
                            window.unminimize().unwrap_or(());
                            window.set_focus().unwrap_or(());
                        }
                    }
                })
                .build(app)?;

            // 自启动时只在首次启动阶段隐藏窗口，不改变之后关闭按钮的语义。
            if std::env::args().any(|arg| arg == "--autostart") {
                if let Some(window) = app.get_webview_window("main") {
                    window.hide().unwrap_or(());
                }
            }

            // Backend startup is intentionally off the UI thread. The window can
            // render immediately while the existing frontend health poll waits.
            let app_handle = app.handle().clone();
            let status_item_for_thread = status_item.clone();
            let manager = setup_backend_manager.clone();
            std::thread::spawn(move || match manager.start(&app_handle) {
                Ok(()) => {
                    let _ = status_item_for_thread.set_text("Backend: 已连接");
                }
                Err(error) => {
                    use tauri_plugin_dialog::{DialogExt, MessageDialogKind};
                    let _ = status_item_for_thread.set_text("Backend: 启动失败");
                    app_handle
                        .dialog()
                        .message(format!("OfficeAgent Backend 启动失败。\n\n{error}"))
                        .title("OfficeAgent 启动错误")
                        .kind(MessageDialogKind::Error)
                        .blocking_show();
                }
            });

            // 开发模式打开开发者工具
            #[cfg(debug_assertions)]
            {
                if let Some(window) = app.get_webview_window("main") {
                    window.open_devtools();
                }
            }

            Ok(())
        })
        // 窗口事件处理
        .on_window_event(|window, event| {
            match event {
                // 关闭按钮：按设置最小化到托盘，或真正退出
                WindowEvent::CloseRequested { api, .. } => {
                    let minimize_enabled = window
                        .app_handle()
                        .state::<MinimizeToTrayState>()
                        .0
                        .lock()
                        .map(|v| *v)
                        .unwrap_or(true);

                    if minimize_enabled {
                        window.hide().unwrap_or(());
                        api.prevent_close();
                    }
                    // 设置关闭：放行关闭，走 RunEvent::Exit 正常退出并回收后端
                }
                // 文件拖拽事件
                WindowEvent::DragDrop(event) => {
                    use tauri::DragDropEvent;
                    if let DragDropEvent::Drop { paths, .. } = event {
                        // 将文件路径发送到前端
                        let file_paths: Vec<String> = paths
                            .iter()
                            .map(|p| p.to_string_lossy().to_string())
                            .collect();

                        // 只接受支持的文件类型
                        let supported_extensions = [
                            "docx", "doc", "pptx", "ppt", "xlsx", "xls", "pdf", "txt", "md", "csv",
                            "json", "png", "jpg", "jpeg", "gif", "bmp", "webp", "svg", "xml",
                            "html",
                        ];

                        let filtered: Vec<String> = file_paths
                            .into_iter()
                            .filter(|path| {
                                let ext = Path::new(path)
                                    .extension()
                                    .and_then(|value| value.to_str())
                                    .unwrap_or("")
                                    .to_lowercase();
                                supported_extensions.contains(&ext.as_str())
                            })
                            .collect();

                        if !filtered.is_empty() {
                            // 注意：不能复用核心事件名 tauri://drag-drop —— Tauri 核心
                            // 自己会以 {paths, position} 结构向 JS 发送同名事件，重名
                            // 会导致每次拖拽触发两次回调（文件被上传两遍）。
                            window
                                .emit("office-agent://drag-drop", &filtered)
                                .unwrap_or(());
                        }
                    }
                }
                _ => {}
            }
        })
        .build(tauri::generate_context!())
        .expect("error while building tauri application")
        .run(move |_app, event| {
            match event {
                // Exit 在正常退出时触发；ExitRequested 兜底覆盖异常终止路径，
                // 双重 stop() 是幂等的（child 已 take 则为空操作）
                tauri::RunEvent::ExitRequested { .. } => {
                    exit_backend_manager.stop();
                }
                tauri::RunEvent::Exit => {
                    exit_backend_manager.stop();
                }
                _ => {}
            }
        });
}
