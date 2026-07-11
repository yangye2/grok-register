from __future__ import annotations

import json
import importlib.util
import math
import os
import re
import shutil
import signal
import sqlite3
import subprocess
import sys
import threading
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi import Request
from pydantic import BaseModel, Field


APP_DIR = Path(__file__).resolve().parent
REPO_ROOT = APP_DIR.parents[1]
RUNTIME_DIR = APP_DIR / "runtime"
TASKS_DIR = RUNTIME_DIR / "tasks"
DB_PATH = RUNTIME_DIR / "console.db"
TEMPLATES = Jinja2Templates(directory=str(APP_DIR / "templates"))


def default_source_python(project_dir: Path) -> Path:
    windows_path = project_dir / ".venv" / "Scripts" / "python.exe"
    posix_path = project_dir / ".venv" / "bin" / "python"
    if windows_path.exists():
        return windows_path
    if posix_path.exists():
        return posix_path
    return windows_path if os.name == "nt" else posix_path


SOURCE_PROJECT = Path(os.getenv("GROK_REGISTER_SOURCE_DIR", str(REPO_ROOT))).resolve()
SOURCE_VENV_PYTHON = Path(
    os.getenv("GROK_REGISTER_PYTHON", str(default_source_python(SOURCE_PROJECT)))
).expanduser()
MAX_CONCURRENT_TASKS = max(1, int(os.getenv("GROK_REGISTER_CONSOLE_MAX_CONCURRENT_TASKS", "1")))
SUPERVISOR_INTERVAL = max(1.0, float(os.getenv("GROK_REGISTER_CONSOLE_POLL_INTERVAL", "2")))

REGISTER_RUNNER_DIR = SOURCE_PROJECT / "apps" / "register-runner"
CPA_WORKER_DIR = SOURCE_PROJECT / "apps" / "cpa-worker"
PROJECT_FILES = ("DrissionPage_example.py", "email_register.py", "cpa_export.py")
PROJECT_DIRS = ("turnstilePatch", "cpa_xai")


def resolve_source_python() -> str:
    """Resolve the Python executable used by isolated task runners."""
    configured_python = str(SOURCE_VENV_PYTHON)
    if SOURCE_VENV_PYTHON.is_file():
        return configured_python
    found = shutil.which(configured_python)
    if found:
        return found
    return sys.executable


def missing_source_items() -> list[str]:
    required_paths = [
        REGISTER_RUNNER_DIR / "DrissionPage_example.py",
        REGISTER_RUNNER_DIR / "email_register.py",
        CPA_WORKER_DIR / "cpa_export.py",
        CPA_WORKER_DIR / "cpa_xai" / "__init__.py",
        SOURCE_PROJECT / "turnstilePatch" / "manifest.json",
    ]
    return [str(path) for path in required_paths if not path.exists()]

STATUS_QUEUED = "queued"
STATUS_RUNNING = "running"
STATUS_STOPPING = "stopping"
STATUS_COMPLETED = "completed"
STATUS_PARTIAL = "partial"
STATUS_FAILED = "failed"
STATUS_STOPPED = "stopped"

LINE_RE_ROUND = re.compile(r"开始第\s*(\d+)\s*轮注册")
LINE_RE_SUCCESS = re.compile(r"注册成功\s*\|\s*email=([^|\s]+)")
LINE_RE_ERROR = re.compile(r"\[Error\]\s*第\s*(\d+)\s*轮失败:\s*(.+)")
LINE_RE_TEMP_EMAIL = re.compile(r"临时邮箱创建成功:\s*([^\s]+)")
LINE_RE_FILLED_EMAIL = re.compile(r"已填写邮箱并点击注册:\s*([^\s]+)")

db_lock = threading.RLock()
cpa_jobs_lock = threading.Lock()
cpa_jobs: dict[int, threading.Thread] = {}


def now_iso() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def ensure_dirs() -> None:
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    TASKS_DIR.mkdir(parents=True, exist_ok=True)


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def fetch_all(query: str, params: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
    with db_lock, get_conn() as conn:
        return conn.execute(query, params).fetchall()


def fetch_one(query: str, params: tuple[Any, ...] = ()) -> sqlite3.Row | None:
    with db_lock, get_conn() as conn:
        return conn.execute(query, params).fetchone()


def execute(query: str, params: tuple[Any, ...] = ()) -> int:
    with db_lock, get_conn() as conn:
        cur = conn.execute(query, params)
        conn.commit()
        return int(cur.lastrowid)


def execute_no_return(query: str, params: tuple[Any, ...] = ()) -> None:
    with db_lock, get_conn() as conn:
        conn.execute(query, params)
        conn.commit()


def row_get(row: sqlite3.Row | None, key: str, default: Any = "") -> Any:
    if row is None:
        return default
    try:
        return row[key]
    except (IndexError, KeyError):
        return default


def init_db() -> None:
    ensure_dirs()
    with db_lock, get_conn() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                status TEXT NOT NULL,
                target_count INTEGER NOT NULL,
                completed_count INTEGER NOT NULL DEFAULT 0,
                failed_count INTEGER NOT NULL DEFAULT 0,
                current_round INTEGER NOT NULL DEFAULT 0,
                current_phase TEXT,
                last_email TEXT,
                last_error TEXT,
                last_log_at TEXT,
                notes TEXT,
                config_json TEXT NOT NULL,
                task_dir TEXT NOT NULL,
                console_path TEXT NOT NULL,
                pid INTEGER,
                created_at TEXT NOT NULL,
                started_at TEXT,
                finished_at TEXT,
                exit_code INTEGER
            );

            CREATE TABLE IF NOT EXISTS accounts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id INTEGER NOT NULL,
                task_name TEXT NOT NULL,
                email TEXT NOT NULL,
                sso TEXT NOT NULL,
                given_name TEXT,
                family_name TEXT,
                password TEXT,
                source_file TEXT,
                created_at TEXT NOT NULL,
                imported_at TEXT NOT NULL,
                UNIQUE(task_id, email, sso)
            );

            CREATE INDEX IF NOT EXISTS idx_accounts_task_id ON accounts(task_id);
            CREATE INDEX IF NOT EXISTS idx_accounts_created_at ON accounts(created_at DESC, id DESC);
            """
        )
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(accounts)").fetchall()}
        for name, definition in (
            ("cpa_status", "TEXT NOT NULL DEFAULT 'not_started'"),
            ("cpa_path", "TEXT"),
            ("cpa_uploaded_at", "TEXT"),
            ("cpa_error", "TEXT"),
            ("cpa_updated_at", "TEXT"),
        ):
            if name not in columns:
                conn.execute(f"ALTER TABLE accounts ADD COLUMN {name} {definition}")


def load_source_defaults() -> dict[str, Any]:
    config_path = SOURCE_PROJECT / "config.json"
    if config_path.exists():
        base = json.loads(config_path.read_text(encoding="utf-8"))
    else:
        example_path = SOURCE_PROJECT / "config.example.json"
        if example_path.exists():
            base = json.loads(example_path.read_text(encoding="utf-8"))
        else:
            base = {
                "run": {"count": 50},
                "proxy": "",
                "browser_proxy": "",
                "temp_mail_api_base": "",
                "temp_mail_admin_password": "",
                "temp_mail_domain": "",
                "temp_mail_site_password": "",
            }
    base.pop("api", None)

    env_count = os.getenv("GROK_REGISTER_DEFAULT_RUN_COUNT", "").strip()
    if env_count:
        try:
            base.setdefault("run", {})["count"] = max(1, int(env_count))
        except ValueError:
            pass

    env_map = {
        "proxy": "GROK_REGISTER_DEFAULT_PROXY",
        "browser_proxy": "GROK_REGISTER_DEFAULT_BROWSER_PROXY",
        "temp_mail_api_base": "GROK_REGISTER_DEFAULT_TEMP_MAIL_API_BASE",
        "temp_mail_admin_password": "GROK_REGISTER_DEFAULT_TEMP_MAIL_ADMIN_PASSWORD",
        "temp_mail_domain": "GROK_REGISTER_DEFAULT_TEMP_MAIL_DOMAIN",
        "temp_mail_site_password": "GROK_REGISTER_DEFAULT_TEMP_MAIL_SITE_PASSWORD",
    }
    for key, env_name in env_map.items():
        value = os.getenv(env_name)
        if value is not None:
            base[key] = value

    string_env_map = {
        "cpa_auth_dir": "GROK_REGISTER_DEFAULT_CPA_AUTH_DIR",
        "cpa_hotload_dir": "GROK_REGISTER_DEFAULT_CPA_HOTLOAD_DIR",
        "cpa_proxy": "GROK_REGISTER_DEFAULT_CPA_PROXY",
        "cpa_cloud_api_base": "CPA_CLOUD_API_BASE",
    }
    for key, env_name in string_env_map.items():
        value = os.getenv(env_name)
        if value is not None:
            base[key] = value

    bool_env_map = {
        "cpa_export_enabled": "GROK_REGISTER_DEFAULT_CPA_EXPORT_ENABLED",
        "cpa_copy_to_hotload": "GROK_REGISTER_DEFAULT_CPA_COPY_TO_HOTLOAD",
        "cpa_headless": "GROK_REGISTER_DEFAULT_CPA_HEADLESS",
        "cpa_cloud_upload_enabled": "CPA_CLOUD_UPLOAD_ENABLED",
    }
    for key, env_name in bool_env_map.items():
        value = os.getenv(env_name)
        if value is not None:
            base[key] = value.strip().lower() in {"1", "true", "yes", "on"}

    int_env_map = {
        "cpa_mint_timeout_sec": "GROK_REGISTER_DEFAULT_CPA_MINT_TIMEOUT_SEC",
        "cpa_cloud_upload_timeout": "CPA_CLOUD_UPLOAD_TIMEOUT",
        "cpa_cloud_upload_retries": "CPA_CLOUD_UPLOAD_RETRIES",
    }
    for key, env_name in int_env_map.items():
        value = os.getenv(env_name)
        if value is None:
            continue
        try:
            base[key] = int(value)
        except ValueError:
            pass

    return base


def _mask_proxy(proxy_url: str) -> str:
    parsed = urlparse(proxy_url)
    if not parsed.scheme or not parsed.netloc:
        return proxy_url
    host = parsed.hostname or ""
    port = f":{parsed.port}" if parsed.port else ""
    return f"{parsed.scheme}://{host}{port}"


def _request_with_optional_proxy(
    url: str,
    proxy_url: str = "",
    method: str = "GET",
    timeout: int = 15,
    headers: dict[str, str] | None = None,
) -> requests.Response:
    proxies = None
    if proxy_url:
        proxies = {"http": proxy_url, "https": proxy_url}
    return requests.request(
        method,
        url,
        timeout=timeout,
        headers=headers,
        proxies=proxies,
        allow_redirects=True,
    )


def _build_health_item(
    key: str,
    label: str,
    ok: bool,
    summary: str,
    detail: str,
    target: str,
) -> dict[str, Any]:
    return {
        "key": key,
        "label": label,
        "ok": ok,
        "summary": summary,
        "detail": detail,
        "target": target,
        "checked_at": now_iso(),
    }


def run_health_checks() -> dict[str, Any]:
    defaults = merged_defaults()
    items: list[dict[str, Any]] = []

    browser_proxy = str(defaults.get("browser_proxy", "") or "").strip()
    request_proxy = str(defaults.get("proxy", "") or "").strip()
    temp_mail_api_base = str(defaults.get("temp_mail_api_base", "") or "").strip()

    warp_target = browser_proxy or request_proxy
    if not warp_target:
        items.append(
            _build_health_item(
                "warp",
                "WARP / Proxy",
                False,
                "未配置代理出口",
                "当前系统默认配置里没有 `browser_proxy` 或 `proxy`，无法检查前置网络出口。",
                "-",
            )
        )
    else:
        try:
            response = _request_with_optional_proxy(
                "https://www.cloudflare.com/cdn-cgi/trace",
                proxy_url=warp_target,
                timeout=20,
            )
            body = response.text
            ip_match = re.search(r"(?m)^ip=(.+)$", body)
            loc_match = re.search(r"(?m)^loc=(.+)$", body)
            warp_match = re.search(r"(?m)^warp=(.+)$", body)
            ip = ip_match.group(1).strip() if ip_match else "unknown"
            loc = loc_match.group(1).strip() if loc_match else "unknown"
            warp_state = warp_match.group(1).strip() if warp_match else "unknown"
            ok = response.status_code == 200
            items.append(
                _build_health_item(
                    "warp",
                    "WARP / Proxy",
                    ok,
                    f"HTTP {response.status_code} | IP {ip} | LOC {loc}",
                    f"通过代理 `{_mask_proxy(warp_target)}` 访问 Cloudflare trace 成功，warp={warp_state}。",
                    _mask_proxy(warp_target),
                )
            )
        except Exception as exc:
            items.append(
                _build_health_item(
                    "warp",
                    "WARP / Proxy",
                    False,
                    "代理出口不可达",
                    f"通过 `{_mask_proxy(warp_target)}` 访问 Cloudflare trace 失败：{exc}",
                    _mask_proxy(warp_target),
                )
            )

    if not temp_mail_api_base:
        items.append(
            _build_health_item(
                "temp_mail",
                "Temp Mail API",
                False,
                "未配置临时邮箱 API",
                "当前系统默认配置里没有 `temp_mail_api_base`，注册流程会在创建邮箱阶段直接失败。",
                "-",
            )
        )
    else:
        try:
            response = _request_with_optional_proxy(
                temp_mail_api_base,
                proxy_url=request_proxy,
                timeout=15,
            )
            ok = response.status_code < 500
            items.append(
                _build_health_item(
                    "temp_mail",
                    "Temp Mail API",
                    ok,
                    f"HTTP {response.status_code}",
                    "接口地址可达。这里只做基础连通性检查，不会真的创建邮箱地址。",
                    temp_mail_api_base,
                )
            )
        except Exception as exc:
            items.append(
                _build_health_item(
                    "temp_mail",
                    "Temp Mail API",
                    False,
                    "接口不可达",
                    f"访问 `{temp_mail_api_base}` 失败：{exc}",
                    temp_mail_api_base,
                )
            )

    xai_proxy = browser_proxy or request_proxy
    try:
        response = _request_with_optional_proxy(
            "https://accounts.x.ai/sign-up?redirect=grok-com",
            proxy_url=xai_proxy,
            timeout=20,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        ok = response.status_code in {200, 301, 302, 303, 307, 308}
        detail = f"使用 `{_mask_proxy(xai_proxy)}` 访问注册页返回 HTTP {response.status_code}。" if xai_proxy else f"直连访问注册页返回 HTTP {response.status_code}。"
        if not ok and response.status_code in {401, 403, 429}:
            detail += " 这通常说明当前出口被目标站点拦截、限流，或还没完成可用的人机验证链路。"
        items.append(
            _build_health_item(
                "xai",
                "x.ai Sign-up",
                ok,
                f"HTTP {response.status_code}",
                detail,
                "https://accounts.x.ai/sign-up?redirect=grok-com",
            )
        )
    except Exception as exc:
        items.append(
            _build_health_item(
                "xai",
                "x.ai Sign-up",
                False,
                "注册页不可达",
                f"访问 `x.ai` 注册页失败：{exc}",
                "https://accounts.x.ai/sign-up?redirect=grok-com",
            )
        )

    return {
        "items": items,
        "checked_at": now_iso(),
    }


class TaskCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    count: int = Field(50, ge=1, le=5000)
    proxy: str | None = None
    browser_proxy: str | None = None
    temp_mail_api_base: str | None = None
    temp_mail_admin_password: str | None = None
    temp_mail_domain: str | None = None
    temp_mail_site_password: str | None = None
    cpa_export_enabled: bool | None = None
    cpa_auth_dir: str | None = None
    cpa_copy_to_hotload: bool | None = None
    cpa_hotload_dir: str | None = None
    cpa_proxy: str | None = None
    cpa_headless: bool | None = None
    cpa_mint_timeout_sec: int | None = Field(default=None, ge=60, le=900)
    notes: str = ""


class SystemSettings(BaseModel):
    proxy: str = ""
    browser_proxy: str = ""
    temp_mail_api_base: str = ""
    temp_mail_admin_password: str = ""
    temp_mail_domain: str = ""
    temp_mail_site_password: str = ""
    cpa_export_enabled: bool = True
    cpa_auth_dir: str = "./cpa_auths"
    cpa_copy_to_hotload: bool = False
    cpa_hotload_dir: str = ""
    cpa_proxy: str = ""
    cpa_headless: bool = False
    cpa_mint_timeout_sec: int = Field(default=300, ge=60, le=900)
    cpa_cloud_upload_enabled: bool = False
    cpa_cloud_api_base: str = ""
    cpa_cloud_management_key: str | None = None
    cpa_cloud_upload_timeout: int = Field(default=30, ge=5, le=180)
    cpa_cloud_upload_retries: int = Field(default=3, ge=1, le=10)


@dataclass
class ManagedProcess:
    task_id: int
    process: subprocess.Popen[Any]
    log_handle: Any


def read_settings() -> dict[str, Any]:
    row = fetch_one("SELECT value FROM settings WHERE key = ?", ("system",))
    if not row:
        return {}
    try:
        data = json.loads(row["value"])
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def write_settings(settings: SystemSettings) -> dict[str, Any]:
    data = settings.model_dump()
    if data["cpa_cloud_management_key"] is None:
        data["cpa_cloud_management_key"] = str(read_settings().get("cpa_cloud_management_key") or "")
    execute(
        """
        INSERT INTO settings (key, value, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
        """,
        ("system", json.dumps(data, ensure_ascii=False), now_iso()),
    )
    return data


def public_defaults() -> dict[str, Any]:
    defaults = merged_defaults()
    defaults.pop("cpa_cloud_management_key", None)
    return defaults


def merged_defaults() -> dict[str, Any]:
    base = load_source_defaults()
    saved = read_settings()
    if saved.get("proxy") is not None:
        base["proxy"] = str(saved.get("proxy", ""))
    if saved.get("browser_proxy") is not None:
        base["browser_proxy"] = str(saved.get("browser_proxy", ""))
    for key in ("temp_mail_api_base", "temp_mail_admin_password", "temp_mail_domain", "temp_mail_site_password"):
        if key in saved:
            base[key] = str(saved.get(key, ""))
    for key in ("cpa_export_enabled", "cpa_auth_dir", "cpa_copy_to_hotload", "cpa_hotload_dir",
                "cpa_proxy", "cpa_headless", "cpa_mint_timeout_sec", "cpa_cloud_upload_enabled",
                "cpa_cloud_api_base", "cpa_cloud_management_key", "cpa_cloud_upload_timeout",
                "cpa_cloud_upload_retries"):
        if key in saved:
            base[key] = saved[key]
    base.pop("api", None)
    return base


def build_task_config(payload: TaskCreate) -> dict[str, Any]:
    defaults = merged_defaults()
    return {
        "run": {"count": int(payload.count)},
        "proxy": defaults.get("proxy", "") if payload.proxy is None else payload.proxy.strip(),
        "browser_proxy": defaults.get("browser_proxy", "") if payload.browser_proxy is None else payload.browser_proxy.strip(),
        "temp_mail_api_base": defaults.get("temp_mail_api_base", "") if payload.temp_mail_api_base is None else payload.temp_mail_api_base.strip(),
        "temp_mail_admin_password": defaults.get("temp_mail_admin_password", "") if payload.temp_mail_admin_password is None else payload.temp_mail_admin_password.strip(),
        "temp_mail_domain": defaults.get("temp_mail_domain", "") if payload.temp_mail_domain is None else payload.temp_mail_domain.strip(),
        "temp_mail_site_password": defaults.get("temp_mail_site_password", "") if payload.temp_mail_site_password is None else payload.temp_mail_site_password.strip(),
        "cpa_export_enabled": defaults.get("cpa_export_enabled", True) if payload.cpa_export_enabled is None else payload.cpa_export_enabled,
        "cpa_auth_dir": defaults.get("cpa_auth_dir", "./cpa_auths") if payload.cpa_auth_dir is None else payload.cpa_auth_dir.strip(),
        "cpa_copy_to_hotload": defaults.get("cpa_copy_to_hotload", False) if payload.cpa_copy_to_hotload is None else payload.cpa_copy_to_hotload,
        "cpa_hotload_dir": defaults.get("cpa_hotload_dir", "") if payload.cpa_hotload_dir is None else payload.cpa_hotload_dir.strip(),
        "cpa_proxy": defaults.get("cpa_proxy", "") if payload.cpa_proxy is None else payload.cpa_proxy.strip(),
        "cpa_headless": defaults.get("cpa_headless", False) if payload.cpa_headless is None else payload.cpa_headless,
        "cpa_mint_timeout_sec": defaults.get("cpa_mint_timeout_sec", 300) if payload.cpa_mint_timeout_sec is None else payload.cpa_mint_timeout_sec,
        "cpa_base_url": "https://cli-chat-proxy.grok.com/v1",
        "cpa_force_standalone": False,
        "cpa_probe_after_write": True,
        "cpa_probe_chat": False,
        "cpa_mint_cookie_inject": True,
        "cpa_mint_browser_reuse": False,
        "cpa_cloud_upload_enabled": defaults.get("cpa_cloud_upload_enabled", False),
        "cpa_cloud_api_base": defaults.get("cpa_cloud_api_base", ""),
        "cpa_cloud_upload_timeout": defaults.get("cpa_cloud_upload_timeout", 30),
        "cpa_cloud_upload_retries": defaults.get("cpa_cloud_upload_retries", 3),
        "sub2api_export_enabled": False,
    }


def account_output_path(row: sqlite3.Row) -> Path:
    return Path(row["task_dir"]) / "accounts" / f"task_{int(row['id'])}.jsonl"


def sync_account_records_for_task(row: sqlite3.Row) -> None:
    path = account_output_path(row)
    if not path.exists():
        return

    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(record, dict):
            continue

        email = str(record.get("email") or "").strip()
        sso = str(record.get("sso") or "").strip()
        if not email or not sso:
            continue

        execute_no_return(
            """
            INSERT OR IGNORE INTO accounts (
                task_id, task_name, email, sso, given_name, family_name, password,
                source_file, created_at, imported_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(row["id"]),
                str(row["name"]),
                email,
                sso,
                str(record.get("given_name") or ""),
                str(record.get("family_name") or ""),
                str(record.get("password") or ""),
                str(path),
                str(record.get("created_at") or row["created_at"] or now_iso()),
                now_iso(),
            ),
        )

        cpa_record = record.get("cpa") if isinstance(record.get("cpa"), dict) else {}
        if cpa_record:
            if cpa_record.get("ok"):
                cpa_status = "uploaded" if cpa_record.get("cloud_uploaded") else "generated"
                cpa_error = str(cpa_record.get("error") or "")
            elif cpa_record.get("skipped"):
                cpa_status = "not_started"
                cpa_error = str(cpa_record.get("reason") or "skipped")
            elif cpa_record.get("queued"):
                cpa_status = "queued"
                cpa_error = str(cpa_record.get("error") or "")
            else:
                cpa_status = "failed"
                cpa_error = str(cpa_record.get("error") or "")

            existing = fetch_one(
                "SELECT cpa_status, cpa_uploaded_at FROM accounts WHERE task_id = ? AND email = ? AND sso = ?",
                (int(row["id"]), email, sso),
            )
            existing_status = str(row_get(existing, "cpa_status", "not_started") or "not_started")
            can_update = existing_status in {"not_started", "queued", "running", "failed"} or cpa_status in {
                "generated",
                "uploaded",
            }
            if can_update:
                uploaded_at = now_iso() if cpa_status == "uploaded" else str(row_get(existing, "cpa_uploaded_at", "") or "")
                execute_no_return(
                    """
                    UPDATE accounts
                    SET cpa_status = ?, cpa_path = ?, cpa_uploaded_at = ?, cpa_error = ?, cpa_updated_at = ?
                    WHERE task_id = ? AND email = ? AND sso = ?
                    """,
                    (
                        cpa_status,
                        str(cpa_record.get("path") or ""),
                        uploaded_at,
                        cpa_error,
                        now_iso(),
                        int(row["id"]),
                        email,
                        sso,
                    ),
                )


def sync_all_account_records() -> None:
    rows = fetch_all("SELECT * FROM tasks ORDER BY id ASC")
    for row in rows:
        sync_account_records_for_task(row)


def sync_active_account_records() -> None:
    rows = fetch_all(
        "SELECT * FROM tasks WHERE status IN (?, ?, ?) ORDER BY id ASC",
        (STATUS_QUEUED, STATUS_RUNNING, STATUS_STOPPING),
    )
    for row in rows:
        sync_account_records_for_task(row)


def account_count_for_task(task_id: int) -> int:
    row = fetch_one("SELECT COUNT(*) AS c FROM accounts WHERE task_id = ?", (task_id,))
    return int(row["c"]) if row else 0


def serialize_task(row: sqlite3.Row) -> dict[str, Any]:
    task_id = int(row["id"])
    return {
        "id": task_id,
        "name": row["name"],
        "status": row["status"],
        "target_count": int(row["target_count"]),
        "completed_count": int(row["completed_count"]),
        "failed_count": int(row["failed_count"]),
        "account_count": account_count_for_task(task_id),
        "current_round": int(row["current_round"]),
        "current_phase": row["current_phase"] or "",
        "last_email": row["last_email"] or "",
        "last_error": row["last_error"] or "",
        "last_log_at": row["last_log_at"] or "",
        "notes": row["notes"] or "",
        "config": json.loads(row["config_json"]),
        "created_at": row["created_at"],
        "started_at": row["started_at"],
        "finished_at": row["finished_at"],
        "exit_code": row["exit_code"],
        "pid": row["pid"],
    }


def serialize_account(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": int(row["id"]),
        "task_id": int(row["task_id"]),
        "task_name": row["task_name"],
        "email": row["email"],
        "sso": row["sso"],
        "given_name": row["given_name"] or "",
        "family_name": row["family_name"] or "",
        "password": row["password"] or "",
        "source_file": row["source_file"] or "",
        "created_at": row["created_at"],
        "imported_at": row["imported_at"],
        "cpa_status": row_get(row, "cpa_status", "not_started") or "not_started",
        "cpa_path": row_get(row, "cpa_path", "") or "",
        "cpa_uploaded_at": row_get(row, "cpa_uploaded_at", "") or "",
        "cpa_error": row_get(row, "cpa_error", "") or "",
        "cpa_updated_at": row_get(row, "cpa_updated_at", "") or "",
    }


def normalize_console_cpa_paths(config: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(config)
    for key in ("cpa_auth_dir", "cpa_hotload_dir"):
        raw = str(normalized.get(key) or "").strip()
        if not raw:
            continue
        path = Path(raw).expanduser()
        if not path.is_absolute():
            normalized[key] = str((SOURCE_PROJECT / path).resolve())
    return normalized


def run_account_cpa_export(account_id: int) -> None:
    """Mint and optionally push CPA credentials for one stored account."""
    try:
        row = account_row(account_id)
        email = str(row["email"] or "").strip()
        password = str(row["password"] or "")
        sso = str(row["sso"] or "").strip()
        if not email or not password or not sso:
            raise ValueError("账号缺少邮箱、密码或 SSO，无法执行 CPA 授权")

        if str(SOURCE_PROJECT) not in sys.path:
            sys.path.insert(0, str(SOURCE_PROJECT))
        module_path = CPA_WORKER_DIR / "cpa_export.py"
        if not module_path.is_file():
            raise FileNotFoundError(f"CPA module not found: {module_path}")
        spec = importlib.util.spec_from_file_location("console_cpa_export", module_path)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"Unable to load CPA module: {module_path}")
        cpa_export = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cpa_export)

        account_cpa_config = normalize_console_cpa_paths(merged_defaults())
        if not str(account_cpa_config.get("cpa_auth_dir") or "").strip():
            account_cpa_config["cpa_auth_dir"] = str(SOURCE_PROJECT / "cpa_auths")

        result = cpa_export.export_cpa_xai_for_account(
            email,
            password,
            sso=sso,
            config=account_cpa_config,
            log_callback=lambda message: print(f"[account-cpa:{account_id}] {message}", flush=True),
        )
        if result.get("skipped"):
            execute_no_return(
                """
                UPDATE accounts
                SET cpa_status = ?, cpa_path = ?, cpa_uploaded_at = ?, cpa_error = ?, cpa_updated_at = ?
                WHERE id = ?
                """,
                (
                    "not_started",
                    str(result.get("cpa_path") or result.get("path") or ""),
                    "",
                    str(result.get("reason") or "skipped"),
                    now_iso(),
                    account_id,
                ),
            )
            return
        if not result.get("ok"):
            raise RuntimeError(str(result.get("error") or result.get("reason") or "CPA 授权失败"))

        cloud = result.get("cloud_cpa_upload") or {}
        status = "uploaded" if cloud.get("ok") else "generated"
        cloud_error = "" if cloud.get("ok") or cloud.get("skipped") else f"远程推送失败: {cloud.get('error') or cloud}"
        execute_no_return(
            """
            UPDATE accounts
            SET cpa_status = ?, cpa_path = ?, cpa_uploaded_at = ?, cpa_error = ?, cpa_updated_at = ?
            WHERE id = ?
            """,
            (
                status,
                str(result.get("cpa_path") or result.get("path") or ""),
                now_iso() if cloud.get("ok") else "",
                cloud_error,
                now_iso(),
                account_id,
            ),
        )
    except Exception as exc:
        execute_no_return(
            "UPDATE accounts SET cpa_status = ?, cpa_error = ?, cpa_updated_at = ? WHERE id = ?",
            ("failed", str(exc), now_iso(), account_id),
        )
    finally:
        with cpa_jobs_lock:
            cpa_jobs.pop(account_id, None)


def build_accounts_where_clause(task_id: int | None, search: str) -> tuple[str, list[Any]]:
    clauses: list[str] = []
    params: list[Any] = []

    if task_id is not None:
        clauses.append("task_id = ?")
        params.append(task_id)

    keyword = search.strip()
    if keyword:
        like = f"%{keyword.lower()}%"
        clauses.append(
            "("
            "LOWER(email) LIKE ? OR LOWER(sso) LIKE ? OR LOWER(task_name) LIKE ? OR LOWER(password) LIKE ?"
            ")"
        )
        params.extend([like, like, like, like])

    if not clauses:
        return "", params
    return " WHERE " + " AND ".join(clauses), params


def build_tasks_where_clause(status: str) -> tuple[str, list[Any]]:
    normalized = status.strip().lower()
    if not normalized or normalized == "all":
      return "", []
    allowed = {
        STATUS_QUEUED,
        STATUS_RUNNING,
        STATUS_STOPPING,
        STATUS_COMPLETED,
        STATUS_PARTIAL,
        STATUS_FAILED,
        STATUS_STOPPED,
    }
    if normalized not in allowed:
        return "", []
    return " WHERE status = ?", [normalized]


def remove_account_from_source_file(row: sqlite3.Row) -> None:
    source_file = str(row["source_file"] or "").strip()
    if not source_file:
        return

    path = Path(source_file)
    if not path.exists() or not path.is_file():
        return

    email = str(row["email"] or "").strip()
    sso = str(row["sso"] or "").strip()
    kept: list[str] = []
    changed = False

    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            record = json.loads(stripped)
        except json.JSONDecodeError:
            kept.append(line)
            continue
        if (
            isinstance(record, dict)
            and str(record.get("email") or "").strip() == email
            and str(record.get("sso") or "").strip() == sso
        ):
            changed = True
            continue
        kept.append(line)

    if changed:
        path.write_text("\n".join(kept) + ("\n" if kept else ""), encoding="utf-8")


def read_log_lines(path: Path, limit: int = 200) -> list[str]:
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    return lines[-limit:]


def parse_console_state(console_path: Path) -> dict[str, Any]:
    state = {
        "completed_count": 0,
        "failed_count": 0,
        "current_round": 0,
        "current_phase": "",
        "last_email": "",
        "last_error": "",
        "last_log_at": now_iso(),
    }
    if not console_path.exists():
        return state

    lines = console_path.read_text(encoding="utf-8", errors="replace").splitlines()
    if not lines:
        return state

    interesting = (
        "开始第",
        "临时邮箱创建成功",
        "已填写邮箱并点击注册",
        "提取到验证码",
        "已填写验证码",
        "最终注册页",
        "Turnstile",
        "已填写注册资料并点击完成注册",
        "注册成功",
        "[Error]",
    )

    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            continue
        if m := LINE_RE_ROUND.search(line):
            state["current_round"] = int(m.group(1))
            state["current_phase"] = "starting_round"
        if m := LINE_RE_SUCCESS.search(line):
            state["completed_count"] += 1
            state["last_email"] = m.group(1)
            state["current_phase"] = "success"
        if m := LINE_RE_ERROR.search(line):
            state["failed_count"] += 1
            state["last_error"] = m.group(2).strip()
            state["current_phase"] = "error"
        if m := LINE_RE_TEMP_EMAIL.search(line):
            state["last_email"] = m.group(1)
            state["current_phase"] = "mailbox_created"
        if m := LINE_RE_FILLED_EMAIL.search(line):
            state["last_email"] = m.group(1)
            state["current_phase"] = "email_submitted"
        if "提取到验证码" in line:
            state["current_phase"] = "otp_received"
        if "最终注册页" in line:
            state["current_phase"] = "profile_page"
        if "Turnstile 响应已同步" in line:
            state["current_phase"] = "turnstile_solved"
        if "已填写注册资料并点击完成注册" in line:
            state["current_phase"] = "submitting_profile"
        if any(token in line for token in interesting):
            state["last_log_at"] = now_iso()
    return state


def task_row(task_id: int) -> sqlite3.Row:
    row = fetch_one("SELECT * FROM tasks WHERE id = ?", (task_id,))
    if row is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return row


def account_row(account_id: int) -> sqlite3.Row:
    row = fetch_one("SELECT * FROM accounts WHERE id = ?", (account_id,))
    if row is None:
        raise HTTPException(status_code=404, detail="Account not found")
    return row


def delete_task_files(row: sqlite3.Row) -> None:
    task_dir = Path(row["task_dir"])
    if task_dir.exists() and task_dir.is_dir():
        shutil.rmtree(task_dir, ignore_errors=True)


def copy_source_to_task_dir(task_dir: Path, task_config: dict[str, Any]) -> None:
    missing = missing_source_items()
    if missing:
        raise FileNotFoundError(f"Source files missing: {', '.join(missing)}")

    task_dir.mkdir(parents=True, exist_ok=True)
    for file_name in PROJECT_FILES:
        source_dir = CPA_WORKER_DIR if file_name == "cpa_export.py" else REGISTER_RUNNER_DIR
        shutil.copy2(source_dir / file_name, task_dir / file_name)
    for dir_name in PROJECT_DIRS:
        src = CPA_WORKER_DIR / dir_name if dir_name == "cpa_xai" else SOURCE_PROJECT / dir_name
        dst = task_dir / dir_name
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(src, dst)
    (task_dir / "logs").mkdir(exist_ok=True)
    (task_dir / "sso").mkdir(exist_ok=True)
    (task_dir / "accounts").mkdir(exist_ok=True)
    (task_dir / "config.json").write_text(
        json.dumps(task_config, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


class TaskSupervisor:
    def __init__(self) -> None:
        self._processes: dict[int, ManagedProcess] = {}
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._stop = threading.Event()

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def stop_task(self, task_id: int) -> None:
        managed = self._processes.get(task_id)
        if not managed:
            row = task_row(task_id)
            if row["status"] == STATUS_QUEUED:
                execute_no_return(
                    """
                    UPDATE tasks
                    SET status = ?, finished_at = ?, last_error = ?
                    WHERE id = ?
                    """,
                    (STATUS_STOPPED, now_iso(), "Task stopped before launch.", task_id),
                )
                return
            raise HTTPException(status_code=409, detail="Task is not running")
        execute_no_return(
            "UPDATE tasks SET status = ?, last_error = ?, current_phase = ? WHERE id = ?",
            (STATUS_STOPPING, "Stopping task...", STATUS_STOPPING, task_id),
        )
        self._terminate_process(managed.process)

    def _terminate_process(self, process: subprocess.Popen[Any]) -> None:
        if os.name == "nt":
            try:
                process.terminate()
            except ProcessLookupError:
                return
            return
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass

    def _running_count(self) -> int:
        return len(self._processes)

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._refresh_running()
                self._launch_queued()
            except Exception:
                pass
            time.sleep(SUPERVISOR_INTERVAL)

    def _launch_queued(self) -> None:
        slots = MAX_CONCURRENT_TASKS - self._running_count()
        if slots <= 0:
            return
        queued = fetch_all(
            "SELECT * FROM tasks WHERE status = ? ORDER BY id ASC LIMIT ?",
            (STATUS_QUEUED, slots),
        )
        for row in queued:
            try:
                self._start_task(row)
            except Exception as exc:
                task_id = int(row["id"])
                message = f"Task startup failed: {exc}"
                Path(row["console_path"]).parent.mkdir(parents=True, exist_ok=True)
                with Path(row["console_path"]).open("a", encoding="utf-8") as log_file:
                    log_file.write(f"[{now_iso()}] {message}\n")
                execute_no_return(
                    "UPDATE tasks SET status = ?, finished_at = ?, last_error = ?, current_phase = ? WHERE id = ?",
                    (STATUS_FAILED, now_iso(), message, "startup_failed", task_id),
                )

    def _start_task(self, row: sqlite3.Row) -> None:
        task_id = int(row["id"])
        task_dir = Path(row["task_dir"])
        console_path = Path(row["console_path"])
        task_config = json.loads(row["config_json"])
        copy_source_to_task_dir(task_dir, task_config)
        child_env = os.environ.copy()
        management_key = str(read_settings().get("cpa_cloud_management_key") or "").strip()
        if management_key:
            child_env["CPA_CLOUD_MANAGEMENT_KEY"] = management_key

        output_path = task_dir / "sso" / f"task_{task_id}.txt"
        account_output_path = task_dir / "accounts" / f"task_{task_id}.jsonl"
        source_python = resolve_source_python()
        command = [
            source_python,
            str(task_dir / "DrissionPage_example.py"),
            "--count",
            str(int(row["target_count"])),
            "--output",
            str(output_path),
            "--account-output",
            str(account_output_path),
        ]
        log_handle = console_path.open("a", encoding="utf-8")
        process = subprocess.Popen(
            command,
            cwd=task_dir,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            text=True,
            env=child_env,
        )
        self._processes[task_id] = ManagedProcess(task_id=task_id, process=process, log_handle=log_handle)
        execute_no_return(
            """
            UPDATE tasks
            SET status = ?, pid = ?, started_at = ?, current_phase = ?, last_log_at = ?
            WHERE id = ?
            """,
            (STATUS_RUNNING, process.pid, now_iso(), "process_started", now_iso(), task_id),
        )

    def _refresh_running(self) -> None:
        finished: list[int] = []
        for task_id, managed in list(self._processes.items()):
            row = task_row(task_id)
            console_path = Path(row["console_path"])
            parsed = parse_console_state(console_path)
            sync_account_records_for_task(row)
            execute_no_return(
                """
                UPDATE tasks
                SET completed_count = ?, failed_count = ?, current_round = ?, current_phase = ?,
                    last_email = ?, last_error = ?, last_log_at = ?
                WHERE id = ?
                """,
                (
                    parsed["completed_count"],
                    parsed["failed_count"],
                    parsed["current_round"],
                    parsed["current_phase"],
                    parsed["last_email"],
                    parsed["last_error"],
                    parsed["last_log_at"],
                    task_id,
                ),
            )
            exit_code = managed.process.poll()
            if exit_code is None:
                continue
            final_status = STATUS_FAILED
            if row["status"] == STATUS_STOPPING or exit_code in (-15, -9):
                final_status = STATUS_STOPPED
            elif parsed["completed_count"] >= int(row["target_count"]) and exit_code == 0:
                final_status = STATUS_COMPLETED
            elif parsed["completed_count"] > 0:
                final_status = STATUS_PARTIAL
            execute_no_return(
                """
                UPDATE tasks
                SET status = ?, finished_at = ?, exit_code = ?,
                    completed_count = ?, failed_count = ?, current_round = ?, current_phase = ?,
                    last_email = ?, last_error = ?, last_log_at = ?
                WHERE id = ?
                """,
                (
                    final_status,
                    now_iso(),
                    exit_code,
                    parsed["completed_count"],
                    parsed["failed_count"],
                    parsed["current_round"],
                    parsed["current_phase"] or final_status,
                    parsed["last_email"],
                    parsed["last_error"],
                    parsed["last_log_at"],
                    task_id,
                ),
            )
            finished.append(task_id)
        for task_id in finished:
            managed = self._processes.pop(task_id, None)
            if managed and managed.log_handle:
                managed.log_handle.close()


supervisor = TaskSupervisor()


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    execute_no_return(
        "UPDATE accounts SET cpa_status = ?, cpa_error = ?, cpa_updated_at = ? WHERE cpa_status = ?",
        ("failed", "Console restarted while CPA authorization was running; retry the operation.", now_iso(), "running"),
    )
    sync_all_account_records()
    supervisor.start()
    try:
        yield
    finally:
        supervisor.stop()


app = FastAPI(title="Grok Register Console", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(APP_DIR / "static")), name="static")


@app.get("/", response_class=HTMLResponse)
def index(request: Request) -> HTMLResponse:
    return TEMPLATES.TemplateResponse(
        request,
        "index.html",
        {
            "request": request,
            "defaults": json.dumps(public_defaults(), ensure_ascii=False),
            "max_concurrent_tasks": MAX_CONCURRENT_TASKS,
            "source_project": str(SOURCE_PROJECT),
        },
    )


@app.get("/api/meta")
def api_meta() -> dict[str, Any]:
    return {
        "defaults": public_defaults(),
        "settings": {key: value for key, value in read_settings().items() if key != "cpa_cloud_management_key"},
        "source_project": str(SOURCE_PROJECT),
        "python_path": resolve_source_python(),
        "configured_python_path": str(SOURCE_VENV_PYTHON),
        "max_concurrent_tasks": MAX_CONCURRENT_TASKS,
    }


@app.get("/api/health")
def api_health() -> dict[str, Any]:
    return run_health_checks()


@app.get("/api/settings")
def get_settings() -> dict[str, Any]:
    return {"settings": {key: value for key, value in read_settings().items() if key != "cpa_cloud_management_key"}, "defaults": public_defaults()}


@app.post("/api/settings")
def save_settings(payload: SystemSettings) -> dict[str, Any]:
    saved = write_settings(payload)
    return {"settings": {key: value for key, value in saved.items() if key != "cpa_cloud_management_key"}, "defaults": public_defaults()}


@app.get("/api/accounts")
def list_accounts(
    task_id: int | None = Query(None, ge=1),
    search: str = Query(""),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
) -> dict[str, Any]:
    sync_all_account_records()
    where_clause, where_params = build_accounts_where_clause(task_id, search)
    count_row = fetch_one(
        f"SELECT COUNT(*) AS c FROM accounts{where_clause}",
        tuple(where_params),
    )
    total = int(count_row["c"]) if count_row else 0
    total_pages = max(1, math.ceil(total / page_size))
    current_page = min(page, total_pages)
    offset = (current_page - 1) * page_size
    rows = fetch_all(
        f"SELECT * FROM accounts{where_clause} ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?",
        tuple(where_params + [page_size, offset]),
    )
    return {
        "accounts": [serialize_account(row) for row in rows],
        "pagination": {
            "page": current_page,
            "page_size": page_size,
            "total": total,
            "total_pages": total_pages,
        },
    }


@app.get("/api/accounts/{account_id}")
def get_account(account_id: int) -> dict[str, Any]:
    sync_all_account_records()
    return {"account": serialize_account(account_row(account_id))}


@app.delete("/api/accounts/{account_id}")
def delete_account(account_id: int) -> dict[str, Any]:
    row = account_row(account_id)
    remove_account_from_source_file(row)
    execute_no_return("DELETE FROM accounts WHERE id = ?", (account_id,))
    return {"ok": True}


@app.post("/api/accounts/{account_id}/cpa")
def authorize_account_cpa(account_id: int) -> dict[str, Any]:
    account_row(account_id)
    with cpa_jobs_lock:
        active = cpa_jobs.get(account_id)
        if active and active.is_alive():
            raise HTTPException(status_code=409, detail="CPA 授权任务正在执行")
        execute_no_return(
            "UPDATE accounts SET cpa_status = ?, cpa_error = '', cpa_updated_at = ? WHERE id = ?",
            ("running", now_iso(), account_id),
        )
        worker = threading.Thread(target=run_account_cpa_export, args=(account_id,), daemon=True)
        cpa_jobs[account_id] = worker
        worker.start()
    return {"ok": True, "status": "running", "account_id": account_id}


@app.get("/api/tasks")
def list_tasks(
    status: str = Query("all"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
) -> dict[str, Any]:
    where_clause, where_params = build_tasks_where_clause(status)
    count_row = fetch_one(
        f"SELECT COUNT(*) AS c FROM tasks{where_clause}",
        tuple(where_params),
    )
    total = int(count_row["c"]) if count_row else 0
    total_pages = max(1, math.ceil(total / page_size))
    current_page = min(page, total_pages)
    offset = (current_page - 1) * page_size
    rows = fetch_all(
        f"SELECT * FROM tasks{where_clause} ORDER BY id DESC LIMIT ? OFFSET ?",
        tuple(where_params + [page_size, offset]),
    )
    return {
        "tasks": [serialize_task(row) for row in rows],
        "pagination": {
            "page": current_page,
            "page_size": page_size,
            "total": total,
            "total_pages": total_pages,
        },
    }


@app.post("/api/tasks")
def create_task(payload: TaskCreate) -> dict[str, Any]:
    if not SOURCE_PROJECT.exists():
        raise HTTPException(status_code=500, detail=f"Source project not found: {SOURCE_PROJECT}")
    missing = missing_source_items()
    if missing:
        raise HTTPException(status_code=500, detail=f"Source files missing: {', '.join(missing)}")
    task_config = build_task_config(payload)
    created_at = now_iso()
    task_id = execute(
        """
        INSERT INTO tasks (
            name, status, target_count, notes, config_json, task_dir, console_path, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            payload.name.strip(),
            STATUS_QUEUED,
            payload.count,
            payload.notes.strip(),
            json.dumps(task_config, ensure_ascii=False),
            str(TASKS_DIR / "pending"),
            str(TASKS_DIR / "pending.log"),
            created_at,
        ),
    )
    task_dir = TASKS_DIR / f"task_{task_id}"
    console_path = task_dir / "console.log"
    task_dir.mkdir(parents=True, exist_ok=True)
    execute_no_return(
        "UPDATE tasks SET task_dir = ?, console_path = ? WHERE id = ?",
        (str(task_dir), str(console_path), task_id),
    )
    return {"task": serialize_task(task_row(task_id))}


@app.get("/api/tasks/{task_id}")
def get_task(task_id: int) -> dict[str, Any]:
    return {"task": serialize_task(task_row(task_id))}


@app.get("/api/tasks/{task_id}/logs")
def get_task_logs(task_id: int, limit: int = Query(200, ge=20, le=1000)) -> dict[str, Any]:
    row = task_row(task_id)
    console_path = Path(row["console_path"])
    return {"lines": read_log_lines(console_path, limit=limit)}


@app.post("/api/tasks/{task_id}/stop")
def stop_task(task_id: int) -> dict[str, Any]:
    supervisor.stop_task(task_id)
    return {"ok": True}


@app.delete("/api/tasks/{task_id}")
def delete_task(task_id: int) -> dict[str, Any]:
    row = task_row(task_id)
    managed = supervisor._processes.get(task_id)
    if managed and managed.process.poll() is None:
        raise HTTPException(status_code=409, detail="Task is still running")
    delete_task_files(row)
    execute_no_return("DELETE FROM tasks WHERE id = ?", (task_id,))
    return {"ok": True}


if __name__ == "__main__":
    import uvicorn

    host = os.getenv("GROK_REGISTER_CONSOLE_HOST", "127.0.0.1")
    port = int(os.getenv("GROK_REGISTER_CONSOLE_PORT", "18600"))
    uvicorn.run("app:app", host=host, port=port, reload=False)
