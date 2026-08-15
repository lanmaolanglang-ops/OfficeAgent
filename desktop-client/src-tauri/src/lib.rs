// OfficeAgent Desktop - Tauri Backend
// 实现：系统托盘、文件拖拽、文件操作、通知、开机启动、窗口状态

use std::fs;
use std::io::{Read, Write};
use std::net::TcpStream;
use std::path::PathBuf;
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
            return Err(format!(
                "端口 {BACKEND_PORT} 已被其他程序占用，且该服务未通过 OfficeAgent 健康检查"
            ));
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
            let _ = child.kill();
            let _ = child.wait();
        }
    }
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
    let request = format!(
        "GET /health HTTP/1.1\r\nHost: {BACKEND_HOST}:{BACKEND_PORT}\r\nConnection: close\r\n\r\n"
    );
    if stream.write_all(request.as_bytes()).is_err() {
        return false;
    }

    let mut response = String::new();
    stream.read_to_string(&mut response).is_ok()
        && response.starts_with("HTTP/1.1 200")
        && response.contains("\"status\":\"healthy\"")
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

    #[cfg(target_os = "windows")]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(0x08000000);
    }

    command.spawn().map_err(|e| format!("无法启动Backend: {e}"))
}

// 自定义命令：打开文件所在目录
#[tauri::command]
fn show_in_folder(path: String) -> Result<(), String> {
    let path_buf = PathBuf::from(&path);
    if !path_buf.exists() {
        return Err(format!("文件不存在: {}", path));
    }

    #[cfg(target_os = "windows")]
    {
        // Windows: 使用explorer /select
        let path_str = path_buf.to_string_lossy().to_string();
        std::process::Command::new("explorer")
            .args(["/select,", &path_str])
            .spawn()
            .map_err(|e| format!("无法打开资源管理器: {}", e))?;
    }

    #[cfg(target_os = "macos")]
    {
        std::process::Command::new("open")
            .args(["-R", &path])
            .spawn()
            .map_err(|e| format!("无法打开Finder: {}", e))?;
    }

    #[cfg(target_os = "linux")]
    {
        if let Some(parent) = path_buf.parent() {
            std::process::Command::new("xdg-open")
                .arg(parent)
                .spawn()
                .map_err(|e| format!("无法打开文件管理器: {}", e))?;
        }
    }

    Ok(())
}

// 自定义命令：用系统默认程序打开文件
#[tauri::command]
fn open_file_path(path: String) -> Result<(), String> {
    let path_buf = PathBuf::from(&path);
    if !path_buf.exists() {
        return Err(format!("文件不存在: {}", path));
    }

    #[cfg(target_os = "windows")]
    {
        std::process::Command::new("cmd")
            .args(["/C", "start", "", &path])
            .spawn()
            .map_err(|e| format!("无法打开文件: {}", e))?;
    }

    #[cfg(target_os = "macos")]
    {
        std::process::Command::new("open")
            .arg(&path)
            .spawn()
            .map_err(|e| format!("无法打开文件: {}", e))?;
    }

    #[cfg(target_os = "linux")]
    {
        std::process::Command::new("xdg-open")
            .arg(&path)
            .spawn()
            .map_err(|e| format!("无法打开文件: {}", e))?;
    }

    Ok(())
}

// 自定义命令：检查文件是否存在
#[tauri::command]
fn file_exists(path: String) -> bool {
    PathBuf::from(&path).exists()
}

// 自定义命令：获取文件信息
#[tauri::command]
fn get_file_info(path: String) -> Result<serde_json::Value, String> {
    let path_buf = PathBuf::from(&path);
    if !path_buf.exists() {
        return Err(format!("文件不存在: {}", path));
    }

    let metadata = fs::metadata(&path_buf).map_err(|e| format!("无法读取文件信息: {}", e))?;

    Ok(serde_json::json!({
        "path": path,
        "size": metadata.len(),
        "is_dir": metadata.is_dir(),
        "is_file": metadata.is_file(),
        "modified": metadata.modified().ok().map(|t| {
            t.duration_since(std::time::UNIX_EPOCH).map(|d| d.as_millis()).unwrap_or(0)
        }),
        "extension": path_buf.extension().and_then(|e| e.to_str()).unwrap_or(""),
        "filename": path_buf.file_name().and_then(|f| f.to_str()).unwrap_or(""),
    }))
}

// 自定义命令：读取目录内容
#[tauri::command]
fn list_directory(path: String) -> Result<Vec<serde_json::Value>, String> {
    let path_buf = PathBuf::from(&path);
    if !path_buf.exists() || !path_buf.is_dir() {
        return Err(format!("目录不存在: {}", path));
    }

    let entries = fs::read_dir(&path_buf).map_err(|e| format!("无法读取目录: {}", e))?;

    let mut files = Vec::new();
    for entry in entries.flatten() {
        if let Ok(metadata) = entry.metadata() {
            let name = entry.file_name().to_string_lossy().to_string();
            files.push(serde_json::json!({
                "name": name,
                "path": entry.path().to_string_lossy().to_string(),
                "size": metadata.len(),
                "is_dir": metadata.is_dir(),
                "extension": entry.path().extension().and_then(|e| e.to_str()).unwrap_or(""),
            }));
        }
    }

    Ok(files)
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
        // 自定义命令
        .invoke_handler(tauri::generate_handler![
            show_in_folder,
            open_file_path,
            file_exists,
            get_file_info,
            list_directory,
            get_app_data_dir,
            get_download_dir,
            quit_app,
            minimize_to_tray,
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
            let _tray = TrayIconBuilder::with_id("main-tray")
                .icon(app.default_window_icon().unwrap().clone())
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
                // 关闭按钮：最小化到托盘而不是退出
                WindowEvent::CloseRequested { api, .. } => {
                    // 检查是否是启动参数导致的关闭
                    let args: Vec<String> = std::env::args().collect();
                    let is_autostart = args.iter().any(|a| a == "--autostart");

                    // 如果是开机自启动，直接隐藏窗口
                    if is_autostart {
                        window.hide().unwrap_or(());
                        api.prevent_close();
                        return;
                    }

                    // 询问用户是否最小化到托盘或退出
                    // 这里直接最小化到托盘，前端可以通过设置控制
                    window.hide().unwrap_or(());
                    api.prevent_close();
                }
                // 文件拖拽事件
                WindowEvent::DragDrop(event) => {
                    use tauri::DragDropEvent;
                    match event {
                        DragDropEvent::Drop { paths, .. } => {
                            // 将文件路径发送到前端
                            let file_paths: Vec<String> = paths
                                .iter()
                                .map(|p| p.to_string_lossy().to_string())
                                .collect();

                            // 只接受支持的文件类型
                            let supported_extensions = [
                                "docx", "doc", "pptx", "ppt", "xlsx", "xls", "pdf", "txt", "md",
                                "csv", "json", "png", "jpg", "jpeg", "gif", "bmp",
                            ];

                            let filtered: Vec<String> = file_paths
                                .into_iter()
                                .filter(|path| {
                                    let ext = path.split('.').last().unwrap_or("").to_lowercase();
                                    supported_extensions.contains(&ext.as_str())
                                })
                                .collect();

                            if !filtered.is_empty() {
                                window.emit("tauri://drag-drop", &filtered).unwrap_or(());
                            }
                        }
                        _ => {}
                    }
                }
                _ => {}
            }
        })
        .build(tauri::generate_context!())
        .expect("error while building tauri application")
        .run(move |_app, event| {
            if let tauri::RunEvent::Exit = event {
                exit_backend_manager.stop();
            }
        });
}
