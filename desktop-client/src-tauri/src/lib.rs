// OfficeAgent Desktop - Tauri Backend
// 实现：系统托盘、文件拖拽、文件操作、通知、开机启动、窗口状态

use std::fs;
use std::io::{Read, Write};
use std::net::TcpStream;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex, OnceLock};
use std::time::{Duration, Instant};
use tauri::{
    menu::{Menu, MenuItem, PredefinedMenuItem},
    tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent},
    Emitter, Manager, WindowEvent,
};

const BACKEND_HOST: &str = "127.0.0.1";
const BACKEND_PORT: u16 = 8765;
const BACKEND_STARTUP_TIMEOUT: Duration = Duration::from_secs(30);
// 运行期看护：只判断"本进程拉起的后端子进程是否还活着"。刻意不轮询 /ready ——
// 依赖短暂 not-ready 就 kill 整个后端会把可自愈的抖动放大成硬重启。
const BACKEND_RESTART_TIMEOUT: Duration = Duration::from_secs(30);
const WATCHDOG_POLL_INTERVAL: Duration = Duration::from_secs(2);

/// 重启预算：时间窗内最多重启 max_restarts 次，超出即放弃，避免后端崩溃循环
/// 时无限高速重启。退避随失败次数指数增长并封顶。
#[derive(Clone, Copy, Debug)]
struct SupervisorPolicy {
    window_ms: u64,
    max_restarts: u32,
    base_backoff_ms: u64,
    max_backoff_ms: u64,
}

impl Default for SupervisorPolicy {
    fn default() -> Self {
        Self {
            window_ms: 300_000,
            max_restarts: 3,
            base_backoff_ms: 1_000,
            max_backoff_ms: 30_000,
        }
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum SupervisorDecision {
    /// 仍在预算内，可以立即重启
    RestartNow,
    /// 仍在预算内，但需先退避
    RestartAfter { delay_ms: u64 },
    /// 预算耗尽：停止重启，等人工介入
    GiveUp,
}

#[derive(Debug, Default)]
struct SupervisorState {
    /// 时间窗内的重启时刻（毫秒，单调时钟）
    recent_restarts: Vec<u64>,
    failures: u32,
}

impl SupervisorState {
    /// 子进程退出一次：给出本次应执行的动作。
    fn on_exit(&mut self, now_ms: u64, policy: &SupervisorPolicy) -> SupervisorDecision {
        self.recent_restarts
            .retain(|at| now_ms.saturating_sub(*at) < policy.window_ms);
        if self.recent_restarts.len() as u32 >= policy.max_restarts {
            return SupervisorDecision::GiveUp;
        }
        self.recent_restarts.push(now_ms);
        self.failures = self.failures.saturating_add(1);
        let shift = self.failures.saturating_sub(1).min(16);
        let delay = policy
            .base_backoff_ms
            .saturating_mul(1u64 << shift)
            .min(policy.max_backoff_ms);
        if delay == 0 {
            SupervisorDecision::RestartNow
        } else {
            SupervisorDecision::RestartAfter { delay_ms: delay }
        }
    }

    /// 后端恢复就绪：预算与退避全部归零，下次崩溃重新计数。
    fn on_ready(&mut self) {
        self.recent_restarts.clear();
        self.failures = 0;
    }
}

/// 进程级单调毫秒（SupervisorState 只吃纯数值，便于单测注入时间）。
fn monotonic_ms() -> u64 {
    static START: OnceLock<Instant> = OnceLock::new();
    START.get_or_init(Instant::now).elapsed().as_millis() as u64
}

/// 可被"停止看护"信号打断的睡眠；返回 true 表示已被要求停止。
fn interruptible_sleep(duration: Duration, stop: &AtomicBool) -> bool {
    let step = Duration::from_millis(100);
    let mut remaining = duration;
    while !stop.load(Ordering::SeqCst) {
        if remaining == Duration::ZERO {
            return false;
        }
        let slice = remaining.min(step);
        std::thread::sleep(slice);
        remaining = remaining.saturating_sub(slice);
    }
    true
}

#[derive(Clone, Default)]
struct BackendManager {
    // Only contains a process started by this Tauri instance. An already-running
    // healthy backend is reused and is never terminated by the desktop client.
    child: Arc<Mutex<Option<Child>>>,
    // 看护线程的停止信号：用户主动停止后端 / 应用退出时置位，看护不得把
    // 主动停止的后端再拉起来。
    watchdog_stop: Arc<AtomicBool>,
    // 保证任何时刻只有一个看护线程，避免双重看护互相抢重启。
    watchdog_running: Arc<AtomicBool>,
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
            "Backend在{}秒内未通过就绪检查: http://{BACKEND_HOST}:{BACKEND_PORT}/ready",
            BACKEND_STARTUP_TIMEOUT.as_secs()
        ))
    }

    fn stop(&self) {
        // 先看护停止、再杀进程：否则看护会把"用户主动停止"当成崩溃并拉起。
        self.watchdog_stop.store(true, Ordering::SeqCst);
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

    /// 重新拉起后端并等待就绪。只在看护线程里调用。
    ///
    /// 拉起失败时刻意保留原子进程记录：看护下一轮仍会看到"已退出"，从而
    /// 继续按退避策略重试，直到预算耗尽。
    fn restart(&self, app: &tauri::AppHandle) -> Result<(), String> {
        let child = match spawn_backend(app) {
            Ok(child) => child,
            Err(error) => return Err(error),
        };
        {
            let mut guard = self.child.lock().map_err(|_| "Backend进程锁不可用")?;
            if let Some(mut previous) = guard.take() {
                let _ = previous.kill();
                let _ = previous.wait();
            }
            *guard = Some(child);
        }

        let deadline = Instant::now() + BACKEND_RESTART_TIMEOUT;
        while Instant::now() < deadline {
            if self.watchdog_stop.load(Ordering::SeqCst) {
                return Err("看护已在重启过程中停止".to_string());
            }
            {
                let mut guard = self.child.lock().map_err(|_| "Backend进程锁不可用")?;
                if let Some(child) = guard.as_mut() {
                    if let Some(status) = child.try_wait().map_err(|e| e.to_string())? {
                        return Err(format!("Backend重启后立即退出，退出码: {status}"));
                    }
                }
            }
            if backend_is_healthy() {
                return Ok(());
            }
            std::thread::sleep(Duration::from_millis(500));
        }
        Err(format!(
            "Backend重启后在{}秒内未通过就绪检查",
            BACKEND_RESTART_TIMEOUT.as_secs()
        ))
    }

    /// 启动运行期看护线程（只启动一次）。
    ///
    /// 只监测**本进程拉起**的后端子进程是否还活着：Python 侧 runtime_manager
    /// 负责自己的健康重启，Tauri 不再深入内部 health，避免双重 supervisor
    /// 互相抢重启。on_status 用于把状态回写到托盘菜单。
    fn start_watchdog(
        &self,
        app: &tauri::AppHandle,
        on_status: Option<Box<dyn Fn(&str) + Send + 'static>>,
    ) {
        // 没有子进程说明复用了外部已运行的后端，不归本进程看护
        let owns_backend = self
            .child
            .lock()
            .map(|guard| guard.is_some())
            .unwrap_or(false);
        if !owns_backend {
            return;
        }
        if self.watchdog_running.swap(true, Ordering::SeqCst) {
            return; // 已有一个看护线程
        }
        self.watchdog_stop.store(false, Ordering::SeqCst);

        let manager = self.clone();
        let app_handle = app.clone();
        std::thread::spawn(move || {
            manager.watchdog_loop(&app_handle, on_status.as_deref());
            manager.watchdog_running.store(false, Ordering::SeqCst);
        });
    }

    fn watchdog_loop(&self, app: &tauri::AppHandle, on_status: Option<&(dyn Fn(&str) + Send)>) {
        let policy = SupervisorPolicy::default();
        let mut state = SupervisorState::default();

        loop {
            if interruptible_sleep(WATCHDOG_POLL_INTERVAL, &self.watchdog_stop) {
                return;
            }

            let exit_status = {
                let mut guard = match self.child.lock() {
                    Ok(guard) => guard,
                    Err(_) => return,
                };
                match guard.as_mut() {
                    // 后端已被主动停止（stop 会 take 走）→ 看护退出，不拉起
                    None => return,
                    Some(child) => match child.try_wait() {
                        Ok(Some(status)) => Some(status.to_string()),
                        _ => None,
                    },
                }
            };

            let Some(status) = exit_status else {
                continue; // 子进程仍存活：不做任何动作
            };

            if let Some(callback) = on_status {
                callback("Backend: 已断开，正在重启");
            }
            match state.on_exit(monotonic_ms(), &policy) {
                SupervisorDecision::RestartNow => {}
                SupervisorDecision::RestartAfter { delay_ms } => {
                    if interruptible_sleep(Duration::from_millis(delay_ms), &self.watchdog_stop) {
                        return;
                    }
                }
                SupervisorDecision::GiveUp => {
                    if let Some(callback) = on_status {
                        callback("Backend: 已停止（重启次数超限）");
                    }
                    return;
                }
            }

            match self.restart(app) {
                Ok(()) => {
                    state.on_ready();
                    if let Some(callback) = on_status {
                        callback("Backend: 已连接");
                    }
                }
                Err(error) => {
                    eprintln!("Backend 重启失败（退出码 {status}）: {error}");
                    if let Some(callback) = on_status {
                        callback("Backend: 重启失败");
                    }
                }
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
    if !backend_cmdline_matches(&cmdline_text) {
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

/// 命令行必须落到 OfficeAgent 后端的**明确入口标记**上。
///
/// 此前只做 `contains("officeagent")`：任何碰巧路径里带这个词、又恰好是
/// python.exe 且占着后端端口的进程都会被当成自家后端杀掉。这里收敛到真实
/// 入口形状——开发的 uvicorn 目标、正式包的后端可执行文件名、launcher 脚本。
/// 调用方传入的必须是已小写化的命令行。
fn backend_cmdline_matches(cmdline: &str) -> bool {
    cmdline.contains("office_agent.api.main")
        || cmdline.contains("officeagent.exe")
        || cmdline.contains("app_launcher.py")
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
        "GET /ready HTTP/1.1\r\nHost: {BACKEND_HOST}:{BACKEND_PORT}\r\nConnection: close\r\n\r\n"
    )
}

fn health_response_is_ready(response: &str) -> bool {
    if !response.starts_with("HTTP/1.1 200") {
        return false;
    }
    let Some((_, body)) = response.split_once("\r\n\r\n") else {
        return false;
    };
    serde_json::from_str::<serde_json::Value>(body)
        .ok()
        .and_then(|payload| payload.get("ready").and_then(|ready| ready.as_bool()))
        == Some(true)
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

    // 启动就绪要求数据库、schema 和 Worker 都可用。存活但 degraded 的
    // 后端仍可由 /live 诊断，但不能让桌面主界面误以为任务链已经就绪。
    let mut response = String::new();
    stream.read_to_string(&mut response).is_ok() && health_response_is_ready(&response)
}

#[cfg(test)]
mod tests {
    use super::{
        backend_cmdline_matches, health_request, health_response_is_ready, interruptible_sleep,
        monotonic_ms, parse_backend_pid, SupervisorDecision, SupervisorPolicy, SupervisorState,
    };
    use std::sync::atomic::AtomicBool;
    use std::time::Duration;

    fn policy() -> SupervisorPolicy {
        SupervisorPolicy {
            window_ms: 10_000,
            max_restarts: 3,
            base_backoff_ms: 1_000,
            max_backoff_ms: 30_000,
        }
    }

    #[test]
    fn pid_parser_accepts_only_decimal_process_ids() {
        assert_eq!(parse_backend_pid(" 12345\r\n"), Some("12345".to_string()));
        assert_eq!(parse_backend_pid(""), None);
        assert_eq!(parse_backend_pid("12x45"), None);
        assert_eq!(parse_backend_pid("-1"), None);
    }

    #[test]
    fn health_contract_accepts_only_ready_backend_state() {
        assert!(health_response_is_ready(
            "HTTP/1.1 200 OK\r\ncontent-type: application/json\r\n\r\n{\"ready\":true,\"checks\":{\"database\":{\"status\":\"healthy\"}}}"
        ));
        assert!(!health_response_is_ready(
            "HTTP/1.1 200 OK\r\n\r\n{\"ready\":false}"
        ));
        assert!(!health_response_is_ready(
            "HTTP/1.1 200 OK\r\n\r\n{\"ready\":false,\"checks\":{\"database\":{\"status\":\"healthy\"}}}"
        ));
        assert!(!health_response_is_ready(
            "HTTP/1.1 200 OK\r\n\r\n{\"status\":\"healthy\"}"
        ));
    }

    #[test]
    fn health_contract_rejects_wrong_status_or_http_code() {
        assert!(!health_response_is_ready(
            "HTTP/1.1 503 Service Unavailable\r\n\r\n{\"ready\":true}"
        ));
        assert!(!health_response_is_ready(
            "HTTP/1.1 200 OK\r\n\r\n{\"status\":\"failed\"}"
        ));
        assert!(!health_response_is_ready("HTTP/1.1 200 OK\r\n\r\nnot-json"));
    }

    #[test]
    fn health_request_is_local_and_connection_closing() {
        let request = health_request();
        assert!(request.starts_with("GET /ready HTTP/1.1\r\n"));
        assert!(request.contains("Host: 127.0.0.1:8765\r\n"));
        assert!(request.ends_with("Connection: close\r\n\r\n"));
    }

    // ===== P2-13 运行期看护：重启预算状态机 =====

    #[test]
    fn first_crash_restarts_with_backoff_not_immediately() {
        let mut state = SupervisorState::default();
        assert_eq!(
            state.on_exit(1_000, &policy()),
            SupervisorDecision::RestartAfter { delay_ms: 1_000 }
        );
        assert_eq!(state.failures, 1);
    }

    #[test]
    fn backoff_grows_exponentially_within_the_budget() {
        let mut state = SupervisorState::default();
        let mut delays = Vec::new();
        for tick in 0..8 {
            if let SupervisorDecision::RestartAfter { delay_ms } =
                state.on_exit(1_000 + tick * 10, &policy())
            {
                delays.push(delay_ms);
            }
        }
        // 预算只有 3 次：第 4 次起是 GiveUp，不会再产生退避值
        assert_eq!(delays, vec![1_000, 2_000, 4_000]);
    }

    #[test]
    fn backoff_is_capped_at_the_policy_maximum() {
        let mut state = SupervisorState::default();
        let policy = SupervisorPolicy {
            max_restarts: 8,
            ..policy()
        };
        let mut delays = Vec::new();
        for tick in 0..8 {
            if let SupervisorDecision::RestartAfter { delay_ms } =
                state.on_exit(1_000 + tick * 10, &policy)
            {
                delays.push(delay_ms);
            }
        }
        assert_eq!(
            delays,
            vec![1_000, 2_000, 4_000, 8_000, 16_000, 30_000, 30_000, 30_000]
        );
    }

    #[test]
    fn restart_budget_is_exhausted_after_max_attempts() {
        let mut state = SupervisorState::default();
        let policy = policy();
        for tick in 0..policy.max_restarts {
            assert!(matches!(
                state.on_exit(1_000 + tick as u64, &policy),
                SupervisorDecision::RestartAfter { .. }
            ));
        }
        // 第 4 次崩溃：预算耗尽，停止重启，绝不无限循环拉起
        assert_eq!(state.on_exit(1_100, &policy), SupervisorDecision::GiveUp);
        assert_eq!(state.on_exit(1_200, &policy), SupervisorDecision::GiveUp);
    }

    #[test]
    fn recovery_resets_the_budget() {
        let mut state = SupervisorState::default();
        let policy = policy();
        state.on_exit(1_000, &policy);
        state.on_exit(1_100, &policy);
        state.on_ready();
        assert_eq!(state.failures, 0);
        assert!(state.recent_restarts.is_empty());
        // 恢复后重新计数，而不是继承历史失败
        assert!(matches!(
            state.on_exit(2_000, &policy),
            SupervisorDecision::RestartAfter { delay_ms: 1_000 }
        ));
    }

    #[test]
    fn old_failures_fall_out_of_the_window() {
        let mut state = SupervisorState::default();
        let policy = policy();
        for tick in 0..policy.max_restarts {
            state.on_exit(1_000 + tick as u64, &policy);
        }
        assert_eq!(state.on_exit(1_900, &policy), SupervisorDecision::GiveUp);
        // 越过时间窗后旧失败不再计入，允许再次重启
        assert!(matches!(
            state.on_exit(1_000 + policy.window_ms + 1, &policy),
            SupervisorDecision::RestartAfter { .. }
        ));
    }

    #[test]
    fn zero_base_backoff_restarts_immediately() {
        let mut state = SupervisorState::default();
        let policy = SupervisorPolicy {
            base_backoff_ms: 0,
            ..policy()
        };
        assert_eq!(state.on_exit(0, &policy), SupervisorDecision::RestartNow);
    }

    #[test]
    fn interruptible_sleep_returns_immediately_when_stopped() {
        let stop = AtomicBool::new(true);
        assert!(interruptible_sleep(Duration::from_secs(30), &stop));
        let stop = AtomicBool::new(false);
        assert!(!interruptible_sleep(Duration::ZERO, &stop));
    }

    #[test]
    fn monotonic_clock_is_non_decreasing() {
        let first = monotonic_ms();
        let second = monotonic_ms();
        assert!(second >= first);
    }

    // ===== P2-13 进程身份核验：命令行必须落到明确入口 =====

    #[test]
    fn backend_cmdline_accepts_real_backend_entry_points() {
        assert!(backend_cmdline_matches(
            r#"python -m uvicorn office_agent.api.main:app --host 127.0.0.1 --port 8765"#
        ));
        assert!(backend_cmdline_matches(
            r#"c:\program files\officeagent\backend\officeagent.exe --host 127.0.0.1 --port 8765 --background"#
        ));
        assert!(backend_cmdline_matches(
            r#"python c:\app\desktop\app_launcher.py --host 127.0.0.1 --port 8765"#
        ));
    }

    #[test]
    fn backend_cmdline_rejects_unrelated_processes() {
        // 仅"路径里出现 officeagent"不足以证明它是自家后端
        assert!(!backend_cmdline_matches(
            r#"python c:\users\me\officeagent_notes\server.py --port 8765"#
        ));
        assert!(!backend_cmdline_matches(
            r#"python -m http.server 8765 --directory officeagent"#
        ));
        assert!(!backend_cmdline_matches(""));
        assert!(!backend_cmdline_matches("python -m uvicorn other.app:app"));
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
            let watchdog_status = status_item.clone();
            let manager = setup_backend_manager.clone();
            let watchdog_manager = setup_backend_manager.clone();
            std::thread::spawn(move || match manager.start(&app_handle) {
                Ok(()) => {
                    let _ = status_item_for_thread.set_text("Backend: 已连接");
                    // 启动成功后接管运行期看护：后端此后崩溃会被检测并有限重启
                    watchdog_manager.start_watchdog(
                        &app_handle,
                        Some(Box::new(move |text: &str| {
                            let _ = watchdog_status.set_text(text);
                        })),
                    );
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
