import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict

import requests
import websocket


REGISTER_PATH = "/api/testers/v1/register"
WS_PATH = "/api/testers/v1/ws"


class WSAuthError(RuntimeError):
    pass


@dataclass
class Config:
    server_url: str
    ws_url: str
    bootstrap_token: str
    token_file: Path
    request_timeout: float
    heartbeat_interval: float
    evaluator_path: Path
    origin: str


def env(name: str, default: str = "") -> str:
    return str(os.environ.get(name, default)).strip()


def to_float(name: str, default: float) -> float:
    raw = env(name, "")
    if not raw:
        return default
    try:
        value = float(raw)
    except Exception:
        return default
    if value <= 0:
        return default
    return value


def build_ws_url(server_url: str) -> str:
    http_url = server_url.rstrip("/") + WS_PATH
    parsed = requests.utils.urlparse(http_url)
    scheme = "wss" if parsed.scheme == "https" else "ws"
    return requests.utils.urlunparse((scheme, parsed.netloc, parsed.path, parsed.params, parsed.query, parsed.fragment))


def build_config() -> Config:
    script_dir = Path(__file__).resolve().parent
    server_url = env("TESTER_SERVER_URL")
    if not server_url:
        raise RuntimeError("必须设置 TESTER_SERVER_URL")
    parsed_origin = requests.utils.urlparse(server_url)
    if not parsed_origin.scheme or not parsed_origin.netloc:
        raise RuntimeError("TESTER_SERVER_URL 必须包含协议和主机，例如 http://127.0.0.1:8080")
    origin = f"{parsed_origin.scheme}://{parsed_origin.netloc}"
    bootstrap_token = env("TESTER_BOOTSTRAP_TOKEN")
    token_file_raw = env("TESTER_TOKEN_FILE") or str(script_dir / ".tester-token")
    return Config(
        server_url=server_url.rstrip("/"),
        ws_url=build_ws_url(server_url),
        bootstrap_token=bootstrap_token,
        token_file=Path(token_file_raw).expanduser(),
        request_timeout=to_float("TESTER_HTTP_TIMEOUT", 30.0),
        heartbeat_interval=to_float("TESTER_HEARTBEAT_INTERVAL", 30.0),
        evaluator_path=script_dir / "tester_eval.py",
        origin=origin,
    )


def timestamp() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def log(message: str) -> None:
    print(f"[探测节点] {timestamp()} {message}", flush=True)


def parse_error(response: requests.Response) -> str:
    try:
        payload = response.json()
    except Exception:
        payload = None
    if isinstance(payload, dict):
        message = payload.get("error") or payload.get("message")
        if message:
            return str(message)
    text = response.text.strip()
    if text:
        return text
    return f"http {response.status_code}"


def load_cached_token(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return ""


def save_cached_token(path: Path, token: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(token.strip() + "\n", encoding="utf-8")


def clear_cached_token(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        return


def register_tester(session: requests.Session, cfg: Config) -> str:
    if not cfg.bootstrap_token:
        raise RuntimeError("首次注册必须设置 TESTER_BOOTSTRAP_TOKEN")
    url = cfg.server_url + REGISTER_PATH
    response = session.post(
        url,
        json={},
        headers={
            "X-Tester-Bootstrap-Token": cfg.bootstrap_token,
            "Origin": cfg.origin,
            "Referer": cfg.origin + "/",
        },
        timeout=cfg.request_timeout,
    )
    if response.status_code >= 400:
        raise RuntimeError(f"注册探测节点失败：{parse_error(response)}")
    body = response.json() if response.content else {}
    data = body.get("data") if isinstance(body, dict) else None
    if not isinstance(data, dict):
        raise RuntimeError("注册探测节点失败：控制台返回的数据格式无效")
    token = str(data.get("token") or "").strip()
    if not token:
        raise RuntimeError("注册探测节点失败：控制台未返回唯一 token")
    save_cached_token(cfg.token_file, token)
    log(
        "探测节点注册成功 "
        f"id={str(data.get('id') or '').strip() or '-'} "
        f"name={str(data.get('name') or '').strip() or '-'}"
    )
    return token


def run_evaluator(cfg: Config, payload: Dict[str, Any]) -> Dict[str, Any]:
    process = subprocess.run(
        [sys.executable, str(cfg.evaluator_path)],
        input=json.dumps(payload, ensure_ascii=False),
        text=True,
        capture_output=True,
        check=False,
    )
    if process.returncode != 0:
        stderr = process.stderr.strip()
        stdout = process.stdout.strip()
        detail = stderr or stdout or f"exit code {process.returncode}"
        raise RuntimeError(f"测试执行器运行失败：{detail}")
    output = process.stdout.strip()
    if not output:
        raise RuntimeError("测试执行器运行失败：输出为空")
    try:
        data = json.loads(output)
    except Exception as exc:
        raise RuntimeError(f"测试执行器运行失败：JSON 解析失败：{exc}") from exc
    if not isinstance(data, dict):
        raise RuntimeError("测试执行器运行失败：返回数据格式无效")
    return data


def normalize_failure(message: str) -> Dict[str, Any]:
    return {
        "success": False,
        "message": message.strip() or "探测节点执行失败",
        "groups": [],
    }


def connect_ws(cfg: Config, token: str) -> websocket.WebSocket:
    headers = [
        f"X-Tester-Token: {token}",
        f"Origin: {cfg.origin}",
        f"Referer: {cfg.origin}/",
    ]
    try:
        return websocket.create_connection(cfg.ws_url, timeout=cfg.request_timeout, header=headers)
    except websocket.WebSocketBadStatusException as exc:
        if exc.status_code == 401:
            raise WSAuthError("tester token required or invalid") from exc
        raise RuntimeError(f"建立 WebSocket 连接失败：http {exc.status_code}") from exc


def parse_ws_message(raw_message: Any) -> Dict[str, Any]:
    if isinstance(raw_message, bytes):
        raw_message = raw_message.decode("utf-8", errors="replace")
    if not isinstance(raw_message, str):
        raise RuntimeError("控制台返回了无效的 WebSocket 消息")
    try:
        data = json.loads(raw_message)
    except Exception as exc:
        raise RuntimeError(f"解析 WebSocket 消息失败：{exc}") from exc
    if not isinstance(data, dict):
        raise RuntimeError("控制台返回了无效的 WebSocket 数据结构")
    return data


def send_ws_message(ws: websocket.WebSocket, payload: Dict[str, Any]) -> None:
    ws.send(json.dumps(payload, ensure_ascii=False))


def execute_job_over_ws(cfg: Config, ws: websocket.WebSocket, message: Dict[str, Any]) -> None:
    job = message.get("job")
    if not isinstance(job, dict):
        raise RuntimeError("任务消息无效：缺少 job")
    job_id = str(job.get("id") or "").strip()
    payload = job.get("payload")
    if not job_id:
        raise RuntimeError("任务消息无效：缺少任务 ID")
    if not isinstance(payload, dict):
        raise RuntimeError(f"任务消息无效：{job_id} 缺少 payload")
    log(f"已收到任务 {job_id}")
    try:
        result = run_evaluator(cfg, payload)
        if not isinstance(result, dict):
            result = normalize_failure("测试执行器返回结果格式无效")
    except Exception as exc:
        result = normalize_failure(str(exc))
    send_ws_message(
        ws,
        {
            "type": "result",
            "jobId": job_id,
            "result": result,
        },
    )
    log(
        f"已提交任务结果 {job_id} "
        f"success={bool(result.get('success'))} "
        f"message={str(result.get('message') or '').strip() or '-'}"
    )


def run_ws_session(cfg: Config, token: str) -> None:
    ws = connect_ws(cfg, token)
    log(f"已连接到 WebSocket 通道：{cfg.ws_url}")
    next_heartbeat_at = time.monotonic()
    try:
        while True:
            now = time.monotonic()
            if now >= next_heartbeat_at:
                send_ws_message(ws, {"type": "heartbeat"})
                next_heartbeat_at = now + cfg.heartbeat_interval
            timeout = max(1.0, min(next_heartbeat_at - time.monotonic(), cfg.heartbeat_interval))
            ws.settimeout(timeout)
            try:
                raw_message = ws.recv()
            except websocket.WebSocketTimeoutException:
                continue
            if raw_message in (None, ""):
                raise RuntimeError("WebSocket 连接已关闭")
            message = parse_ws_message(raw_message)
            message_type = str(message.get("type") or "").strip()
            if message_type == "ready":
                tester_id = str(message.get("testerId") or "").strip() or "-"
                log(f"WebSocket 通道已就绪 testerId={tester_id}")
                continue
            if message_type == "job":
                execute_job_over_ws(cfg, ws, message)
                continue
            if message_type == "error":
                detail = str(message.get("message") or "").strip() or "控制台返回了错误"
                log(f"控制台消息：{detail}")
                continue
            log(f"收到未识别消息类型：{message_type or '-'}")
    finally:
        try:
            ws.close()
        except Exception:
            pass


def main() -> int:
    try:
        cfg = build_config()
    except Exception as exc:
        log(str(exc))
        return 1
    if not cfg.evaluator_path.exists():
        log(f"未找到测试执行器：{cfg.evaluator_path}")
        return 1
    session = requests.Session()
    token = load_cached_token(cfg.token_file)
    if token:
        log(f"已加载本地缓存 token：{cfg.token_file}")
    else:
        log("未找到本地缓存 token，开始注册探测节点")
    backoff = 1.0
    while True:
        try:
            if not token:
                token = register_tester(session, cfg)
            run_ws_session(cfg, token)
            backoff = 1.0
        except WSAuthError as exc:
            log(f"探测节点 token 已失效，准备重新注册：{exc}")
            clear_cached_token(cfg.token_file)
            token = ""
            time.sleep(min(backoff, 15.0))
            backoff = min(backoff * 2, 30.0)
        except requests.RequestException as exc:
            log(f"HTTP 请求异常：{exc}")
            time.sleep(min(backoff, 30.0))
            backoff = min(backoff * 2, 60.0)
        except websocket.WebSocketException as exc:
            log(f"WebSocket 连接异常：{exc}")
            time.sleep(min(backoff, 15.0))
            backoff = min(backoff * 2, 30.0)
        except KeyboardInterrupt:
            log("探测节点已停止")
            return 0
        except Exception as exc:
            log(str(exc))
            time.sleep(min(backoff, 30.0))
            backoff = min(backoff * 2, 60.0)


if __name__ == "__main__":
    sys.exit(main())