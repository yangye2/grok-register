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
import queue
import threading
import time
from contextlib import asynccontextmanager
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests
import hmac
import secrets
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi import Request
from pydantic import BaseModel, Field
from starlette.middleware.sessions import SessionMiddleware


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

# Console login (enabled when GROK_REGISTER_AUTH_PASSWORD is non-empty)
AUTH_USER = (os.getenv("GROK_REGISTER_AUTH_USER", "admin") or "admin").strip()
AUTH_PASSWORD = (os.getenv("GROK_REGISTER_AUTH_PASSWORD", "") or "").strip()
AUTH_ENABLED = bool(AUTH_PASSWORD)
AUTH_SESSION_SECRET = (os.getenv("GROK_REGISTER_AUTH_SECRET", "") or "").strip() or secrets.token_hex(32)
AUTH_SESSION_MAX_AGE = max(3600, int(os.getenv("GROK_REGISTER_AUTH_SESSION_HOURS", "168")) * 3600)

REGISTER_RUNNER_DIR = SOURCE_PROJECT / "apps" / "register-runner"
CPA_WORKER_DIR = SOURCE_PROJECT / "apps" / "cpa-worker"
# Isolated task_dir copies (sources under apps/register-runner + apps/cpa-worker; turnstilePatch at repo root)
PROJECT_FILES = ("DrissionPage_example.py", "email_register.py", "outmail_client.py", "cpa_export.py", "cpa_to_sub2api.py")
PROJECT_DIRS = ("turnstilePatch", "cpa_xai", "health_check")


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
        REGISTER_RUNNER_DIR / "outmail_client.py",
        CPA_WORKER_DIR / "cpa_export.py",
        CPA_WORKER_DIR / "cpa_to_sub2api.py",
        CPA_WORKER_DIR / "cpa_xai" / "__init__.py",
        CPA_WORKER_DIR / "health_check" / "health_check.py",
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
cpa_jobs_lock = threading.RLock()
cpa_jobs: dict[int, threading.Thread] = {}  # busy marker; shared worker thread
cpa_work_queue: queue.Queue[dict[str, Any]] = queue.Queue()
cpa_worker_thread: threading.Thread | None = None
cpa_cancel_event = threading.Event()
cpa_queue_state: dict[str, Any] = {
    "active": False,
    "cancel_requested": False,
    "mode": "",
    "total": 0,
    "done": 0,
    "success": 0,
    "failed": 0,
    "cancelled": 0,
    "current_id": None,
    "current_email": "",
    "started_at": "",
    "finished_at": "",
    "message": "",
    "results": [],
}


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
            ("cpa_log", "TEXT"),
            ("cpa_updated_at", "TEXT"),
            ("token_status", "TEXT NOT NULL DEFAULT 'unknown'"),
            ("token_expires_at", "TEXT"),
            ("token_checked_at", "TEXT"),
            ("token_error", "TEXT"),
            ("sso_alive", "INTEGER"),
            ("last_renew_source", "TEXT"),
            ("last_renew_at", "TEXT"),
        ):
            if name not in columns:
                conn.execute(f"ALTER TABLE accounts ADD COLUMN {name} {definition}")

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS email_domain_stats (
                domain TEXT PRIMARY KEY,
                fail_count INTEGER NOT NULL DEFAULT 0,
                success_count INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'active',
                last_error TEXT,
                last_failed_at TEXT,
                last_success_at TEXT,
                disabled_at TEXT,
                updated_at TEXT NOT NULL
            )
            """
        )


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
        "cpa_cloud_management_key": "CPA_CLOUD_MANAGEMENT_KEY",
        "sub2api_api_base": "SUB2API_API_BASE",
        "sub2api_url": "SUB2API_URL",
        "sub2api_api_key": "SUB2API_API_KEY",
        "sub2api_platform": "SUB2API_PLATFORM",
        "sub2api_account_type": "SUB2API_ACCOUNT_TYPE",
        "sub2api_account_group_ids": "SUB2API_ACCOUNT_GROUP_IDS",
        "sub2api_default_proxy": "SUB2API_DEFAULT_PROXY",
        "sub2api_local_export_dir": "SUB2API_LOCAL_EXPORT_DIR",
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
        "sub2api_upload_enabled": "SUB2API_UPLOAD_ENABLED",
        "sub2api_export_enabled": "SUB2API_EXPORT_ENABLED",
        "sub2api_local_export": "SUB2API_LOCAL_EXPORT",
    }
    for key, env_name in bool_env_map.items():
        value = os.getenv(env_name)
        if value is not None:
            base[key] = value.strip().lower() in {"1", "true", "yes", "on"}

    int_env_map = {
        "cpa_mint_timeout_sec": "GROK_REGISTER_DEFAULT_CPA_MINT_TIMEOUT_SEC",
        "cpa_cloud_upload_timeout": "CPA_CLOUD_UPLOAD_TIMEOUT",
        "cpa_cloud_upload_retries": "CPA_CLOUD_UPLOAD_RETRIES",
        "sub2api_upload_timeout": "SUB2API_UPLOAD_TIMEOUT",
        "sub2api_upload_retries": "SUB2API_UPLOAD_RETRIES",
        "sub2api_account_concurrency": "SUB2API_ACCOUNT_CONCURRENCY",
        "sub2api_account_priority": "SUB2API_ACCOUNT_PRIORITY",
        "sub2api_account_load_factor": "SUB2API_ACCOUNT_LOAD_FACTOR",
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



def _as_nonneg_int(value: Any, default: int = 0, *, maximum: int | None = None) -> int:
    try:
        if value is None or value == "":
            n = int(default)
        else:
            n = int(value)
    except (TypeError, ValueError):
        n = int(default)
    if n < 0:
        n = 0
    if maximum is not None and n > maximum:
        n = maximum
    return n



def _normalize_health_headers(value: Any) -> str:
    """Normalize health-check headers config to multi-line ``Key: Value`` text."""
    if value is None:
        return ""
    if isinstance(value, dict):
        lines = []
        for key, raw in value.items():
            k = str(key).strip()
            if not k or raw is None:
                continue
            lines.append(f"{k}: {str(raw).strip()}")
        return "\n".join(lines)
    text_value = str(value).replace("\r\n", "\n").strip()
    return text_value


def _default_health_headers_text() -> str:
    return (
        "x-grok-client-version: 0.2.93\n"
        "x-xai-token-auth: xai-grok-cli\n"
        "x-authenticateresponse: authenticate-response\n"
        "x-grok-client-identifier: grok-shell\n"
        "User-Agent: grok-shell/0.2.93 (linux; x86_64)"
    )


def _parse_domain_list_value(value: Any) -> list[str]:
    """Parse domain list from list / comma / multi-line text."""
    if value is None:
        return []
    items: list[str] = []
    if isinstance(value, (list, tuple, set)):
        for item in value:
            items.extend(_parse_domain_list_value(item))
    else:
        text_v = str(value).strip()
        if not text_v:
            return []
        parts = re.split(r"[,;，、\s]+", text_v)
        items = [p.strip() for p in parts if p and p.strip()]
    out: list[str] = []
    seen: set[str] = set()
    for item in items:
        domain = str(item or "").strip().lstrip("@").lower()
        if not domain or domain in seen:
            continue
        if "." not in domain or " " in domain:
            continue
        seen.add(domain)
        out.append(domain)
    return out


def _join_domain_list(domains: list[str]) -> str:
    return ",".join(domains)


def _extract_email_domain(email: str) -> str:
    text = str(email or "").strip().lower()
    if "@" not in text:
        return ""
    domain = text.rsplit("@", 1)[-1].strip().lstrip("@")
    if not domain or "." not in domain:
        return ""
    return domain


def _normalize_domain_pool_settings(data: dict[str, Any]) -> dict[str, Any]:
    """Keep active/removed domain lists consistent (active wins if re-added)."""
    active = _parse_domain_list_value(data.get("temp_mail_domain"))
    removed = _parse_domain_list_value(data.get("temp_mail_domains_removed"))
    active_set = set(active)
    removed = [d for d in removed if d not in active_set]
    data["temp_mail_domain"] = _join_domain_list(active)
    data["temp_mail_domains_removed"] = _join_domain_list(removed)
    data["domain_auth_fail_threshold"] = max(
        1, _as_nonneg_int(data.get("domain_auth_fail_threshold"), 3, maximum=100)
    )
    if "domain_auth_fail_auto_remove" not in data:
        data["domain_auth_fail_auto_remove"] = True
    else:
        data["domain_auth_fail_auto_remove"] = bool(data.get("domain_auth_fail_auto_remove"))
    return data


def _save_settings_dict(data: dict[str, Any]) -> dict[str, Any]:
    payload = _normalize_domain_pool_settings(dict(data or {}))
    execute(
        """
        INSERT INTO settings (key, value, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
        """,
        ("system", json.dumps(payload, ensure_ascii=False), now_iso()),
    )
    return payload


def _remove_domain_from_settings_pool(domain: str, *, reason: str = "") -> dict[str, Any]:
    """Mark domain removed and strip it from active temp_mail_domain pool."""
    domain = str(domain or "").strip().lstrip("@").lower()
    if not domain:
        return {"removed": False, "reason": "empty_domain"}

    current = dict(read_settings() or {})
    merged = merged_defaults()
    for key in (
        "temp_mail_domain",
        "temp_mail_domains_removed",
        "domain_auth_fail_threshold",
        "domain_auth_fail_auto_remove",
        "temp_mail_api_base",
        "temp_mail_admin_password",
        "temp_mail_site_password",
        "proxy",
        "browser_proxy",
        "cpa_export_enabled",
        "cpa_auth_dir",
        "cpa_copy_to_hotload",
        "cpa_hotload_dir",
        "cpa_proxy",
        "cpa_headless",
        "cpa_mint_timeout_sec",
        "cpa_cloud_upload_enabled",
        "cpa_cloud_api_base",
        "cpa_cloud_management_key",
        "cpa_cloud_upload_timeout",
        "cpa_cloud_upload_retries",
        "cpa_batch_retry_count",
        "cpa_mint_browser_recycle_every",
        "cpa_health_check_before_upload",
        "cpa_health_check_timeout",
        "cpa_health_check_model",
        "cpa_health_check_headers",
        "cpa_health_check_use_file_headers",
    ):
        if key not in current and key in merged:
            current[key] = merged.get(key)

    active = _parse_domain_list_value(current.get("temp_mail_domain"))
    removed = _parse_domain_list_value(current.get("temp_mail_domains_removed"))
    changed = False
    if domain in active:
        active = [d for d in active if d != domain]
        changed = True
    if domain not in removed:
        removed.append(domain)
        changed = True
    if not changed:
        return {
            "removed": False,
            "domain": domain,
            "reason": "already_removed",
            "active": active,
            "removed_list": removed,
        }

    current["temp_mail_domain"] = _join_domain_list(active)
    current["temp_mail_domains_removed"] = _join_domain_list(removed)
    if reason:
        current["temp_mail_domain_last_remove_reason"] = f"{domain}: {reason}"[:500]
    _save_settings_dict(current)
    return {
        "removed": True,
        "domain": domain,
        "reason": reason,
        "active": active,
        "removed_list": removed,
    }


def record_domain_auth_failure(
    email: str,
    error: str = "",
    log: Any = None,
) -> dict[str, Any]:
    """Increment domain auth-fail counter; auto-remove domain when threshold reached."""
    domain = _extract_email_domain(email)
    if not domain:
        return {"ok": False, "reason": "no_domain"}

    defaults = merged_defaults()
    threshold = max(1, _as_nonneg_int(defaults.get("domain_auth_fail_threshold"), 3, maximum=100))
    auto_remove = bool(defaults.get("domain_auth_fail_auto_remove", True))
    now = now_iso()
    err = str(error or "")[:500]

    with db_lock, get_conn() as conn:
        row = conn.execute(
            "SELECT domain, fail_count, success_count, status FROM email_domain_stats WHERE domain = ?",
            (domain,),
        ).fetchone()
        if row is None:
            fail_count = 1
            success_count = 0
            status = "active"
            conn.execute(
                """
                INSERT INTO email_domain_stats
                (domain, fail_count, success_count, status, last_error, last_failed_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (domain, fail_count, success_count, status, err, now, now),
            )
        else:
            fail_count = int(row["fail_count"] or 0) + 1
            success_count = int(row["success_count"] or 0)
            status = str(row["status"] or "active")
            conn.execute(
                """
                UPDATE email_domain_stats
                SET fail_count = ?, last_error = ?, last_failed_at = ?, updated_at = ?
                WHERE domain = ?
                """,
                (fail_count, err, now, now, domain),
            )
        conn.commit()

    result: dict[str, Any] = {
        "ok": True,
        "domain": domain,
        "fail_count": fail_count,
        "success_count": success_count,
        "threshold": threshold,
        "status": status,
        "removed": False,
    }
    msg = f"[domain] 授权验证失败累计 {fail_count}/{threshold}: {domain}"
    if callable(log):
        log(msg)
    else:
        print(msg, flush=True)

    if fail_count >= threshold and auto_remove and status != "disabled":
        remove_info = _remove_domain_from_settings_pool(
            domain,
            reason=f"auth_fail x{fail_count}: {err}"[:300],
        )
        with db_lock, get_conn() as conn:
            conn.execute(
                """
                UPDATE email_domain_stats
                SET status = ?, disabled_at = ?, updated_at = ?
                WHERE domain = ?
                """,
                ("disabled", now, now, domain),
            )
            conn.commit()
        result["status"] = "disabled"
        result["removed"] = bool(remove_info.get("removed")) or remove_info.get("reason") == "already_removed"
        result["remove_info"] = remove_info
        active_left = ",".join(remove_info.get("active") or []) or "(empty)"
        done = f"[domain] 域名 {domain} 授权验证失败达 {fail_count} 次，已标记并移出邮箱域名池；剩余: {active_left}"
        if callable(log):
            log(done)
        else:
            print(done, flush=True)
    return result


def record_domain_auth_success(email: str, log: Any = None) -> dict[str, Any]:
    """Reset fail count on successful auth push for active domain."""
    domain = _extract_email_domain(email)
    if not domain:
        return {"ok": False, "reason": "no_domain"}
    now = now_iso()
    with db_lock, get_conn() as conn:
        row = conn.execute(
            "SELECT domain, fail_count, success_count, status FROM email_domain_stats WHERE domain = ?",
            (domain,),
        ).fetchone()
        if row is None:
            conn.execute(
                """
                INSERT INTO email_domain_stats
                (domain, fail_count, success_count, status, last_success_at, updated_at)
                VALUES (?, 0, 1, 'active', ?, ?)
                """,
                (domain, now, now),
            )
            fail_count = 0
            success_count = 1
            status = "active"
        else:
            status = str(row["status"] or "active")
            success_count = int(row["success_count"] or 0) + 1
            fail_count = 0 if status == "active" else int(row["fail_count"] or 0)
            conn.execute(
                """
                UPDATE email_domain_stats
                SET fail_count = ?, success_count = ?, last_success_at = ?, updated_at = ?
                WHERE domain = ?
                """,
                (fail_count, success_count, now, now, domain),
            )
        conn.commit()
    return {
        "ok": True,
        "domain": domain,
        "fail_count": fail_count,
        "success_count": success_count,
        "status": status,
    }


def list_email_domain_stats() -> list[dict[str, Any]]:
    rows = fetch_all(
        """
        SELECT domain, fail_count, success_count, status, last_error,
               last_failed_at, last_success_at, disabled_at, updated_at
        FROM email_domain_stats
        ORDER BY
          CASE WHEN status = 'disabled' THEN 0 ELSE 1 END,
          fail_count DESC,
          domain ASC
        """
    )
    out = []
    for row in rows:
        out.append(
            {
                "domain": row["domain"],
                "fail_count": int(row["fail_count"] or 0),
                "success_count": int(row["success_count"] or 0),
                "status": row["status"] or "active",
                "last_error": row["last_error"] or "",
                "last_failed_at": row["last_failed_at"] or "",
                "last_success_at": row["last_success_at"] or "",
                "disabled_at": row["disabled_at"] or "",
                "updated_at": row["updated_at"] or "",
            }
        )
    return out

class SystemSettings(BaseModel):
    proxy: str = ""
    browser_proxy: str = ""
    temp_mail_api_base: str = ""
    temp_mail_admin_password: str = ""
    temp_mail_domain: str = ""
    temp_mail_domains_removed: str = ""
    temp_mail_site_password: str = ""
    email_provider: str = "duckmail"
    outmail_api_base: str = ""
    outmail_api_key: str = ""
    outmail_session_cookie: str = ""
    outmail_proxy: str = ""
    outmail_plus_alias: bool = True
    outmail_plus_alias_count: int = Field(default=1, ge=1, le=1000)
    outmail_alias_suffix_len: int = Field(default=6, ge=2, le=32)
    outmail_fetch_top: int = Field(default=10, ge=1, le=50)
    outmail_poll_interval_sec: int = Field(default=5, ge=1, le=60)
    outmail_poll_timeout_sec: int = Field(default=180, ge=30, le=600)
    outmail_since_padding_sec: int = Field(default=30, ge=0, le=300)
    outmail_from_filter: str = "x.ai"
    outmail_subject_filter: str = "xAI"
    outmail_group_id: str = ""
    outmail_anonymous_enabled: bool = False
    outmail_anonymous_provider: str = "cloudflare"
    outmail_anonymous_domain: str = ""
    outmail_anonymous_username_prefix: str = ""
    outmail_anonymous_password: str = ""
    outmail_anonymous_delete_after: bool = False
    outmail_exclude_used: bool = True
    outmail_used_file: str = "outmail_used_mailboxes.txt"
    domain_auth_fail_threshold: int = Field(default=3, ge=1, le=100)
    domain_auth_fail_auto_remove: bool = True
    cpa_export_enabled: bool = True
    cpa_auth_dir: str = "./cpa_auths"
    cpa_copy_to_hotload: bool = False
    cpa_hotload_dir: str = ""
    cpa_proxy: str = ""
    cpa_headless: bool = False
    cpa_mint_timeout_sec: int = Field(default=300, ge=60, le=900)
    cpa_prefer_sso_oauth: bool = True
    cpa_probe_after_write: bool = True
    cpa_probe_delay_sec: float = Field(default=5.0, ge=0.0, le=120.0)
    cpa_probe_required: bool = False
    cpa_cloud_upload_enabled: bool = False
    cpa_cloud_api_base: str = ""
    cpa_cloud_management_key: str | None = None
    cpa_cloud_upload_timeout: int = Field(default=30, ge=5, le=180)
    cpa_cloud_upload_retries: int = Field(default=3, ge=1, le=10)
    cpa_batch_retry_count: int = Field(default=1, ge=0, le=5)
    cpa_mint_browser_recycle_every: int = Field(default=15, ge=0, le=200)
    cpa_health_check_before_upload: bool = True
    cpa_health_check_timeout: int = Field(default=15, ge=3, le=120)
    cpa_health_check_model: str = "grok-4.5"
    cpa_health_check_headers: str = (
        "x-grok-client-version: 0.2.93\n"
        "x-xai-token-auth: xai-grok-cli\n"
        "x-authenticateresponse: authenticate-response\n"
        "x-grok-client-identifier: grok-shell\n"
        "User-Agent: grok-shell/0.2.93 (linux; x86_64)"
    )
    cpa_health_check_use_file_headers: bool = True
    sub2api_upload_enabled: bool = False
    sub2api_export_enabled: bool = False
    sub2api_api_base: str = ""
    sub2api_api_key: str | None = None
    sub2api_upload_timeout: int = Field(default=30, ge=5, le=180)
    sub2api_upload_retries: int = Field(default=3, ge=1, le=10)
    sub2api_platform: str = "grok"
    sub2api_account_type: str = "oauth"
    sub2api_account_concurrency: int = Field(default=1, ge=1, le=200)
    sub2api_account_priority: int = Field(default=1, ge=0, le=1000)
    sub2api_account_load_factor: int = Field(default=10, ge=1, le=10000)
    sub2api_account_rate_multiplier: float = Field(default=1.0, ge=0.0)
    sub2api_account_group_ids: str = ""
    sub2api_default_proxy: str = ""
    sub2api_local_export: bool = True
    sub2api_local_export_dir: str = "./sub2api_exports"


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
    if data.get("sub2api_api_key") is None:
        data["sub2api_api_key"] = str(read_settings().get("sub2api_api_key") or "")
    data = _normalize_domain_pool_settings(data)
    # Re-activate domains that user put back into the active pool
    active_set = set(_parse_domain_list_value(data.get("temp_mail_domain")))
    for domain in active_set:
        try:
            execute_no_return(
                """
                UPDATE email_domain_stats
                SET status = 'active', fail_count = 0, disabled_at = NULL, updated_at = ?
                WHERE domain = ? AND status = 'disabled'
                """,
                (now_iso(), domain),
            )
        except Exception:
            pass
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
    defaults.pop("sub2api_api_key", None)
    return defaults


def merged_defaults() -> dict[str, Any]:
    base = load_source_defaults()
    saved = read_settings()
    if saved.get("proxy") is not None:
        base["proxy"] = str(saved.get("proxy", ""))
    if saved.get("browser_proxy") is not None:
        base["browser_proxy"] = str(saved.get("browser_proxy", ""))
    for key in (
        "temp_mail_api_base",
        "temp_mail_admin_password",
        "temp_mail_domain",
        "temp_mail_domains_removed",
        "temp_mail_site_password",
        "email_provider",
        "outmail_api_base",
        "outmail_api_key",
        "outmail_session_cookie",
        "outmail_proxy",
        "outmail_from_filter",
        "outmail_subject_filter",
        "outmail_group_id",
        "outmail_anonymous_provider",
        "outmail_anonymous_domain",
        "outmail_anonymous_username_prefix",
        "outmail_anonymous_password",
        "outmail_used_file",
    ):
        if key in saved:
            base[key] = str(saved.get(key, "") or "")
    for key in (
        "outmail_plus_alias",
        "outmail_anonymous_enabled",
        "outmail_anonymous_delete_after",
        "outmail_exclude_used",
    ):
        if key in saved:
            base[key] = bool(saved.get(key))
    for key in (
        "outmail_fetch_top",
        "outmail_poll_interval_sec",
        "outmail_poll_timeout_sec",
        "outmail_since_padding_sec",
        "outmail_plus_alias_count",
        "outmail_alias_suffix_len",
    ):
        if key in saved and saved.get(key) is not None:
            try:
                base[key] = int(saved.get(key))
            except (TypeError, ValueError):
                pass
    for key in (
        "cpa_prefer_sso_oauth",
        "cpa_probe_after_write",
        "cpa_probe_required",
    ):
        if key in saved:
            base[key] = bool(saved.get(key))
    if "cpa_probe_delay_sec" in saved and saved.get("cpa_probe_delay_sec") is not None:
        try:
            base["cpa_probe_delay_sec"] = float(saved.get("cpa_probe_delay_sec"))
        except (TypeError, ValueError):
            base["cpa_probe_delay_sec"] = 5.0
    for key in ("domain_auth_fail_threshold", "domain_auth_fail_auto_remove"):
        if key in saved:
            base[key] = saved[key]
    for key in ("cpa_export_enabled", "cpa_auth_dir", "cpa_copy_to_hotload", "cpa_hotload_dir",
                "cpa_proxy", "cpa_headless", "cpa_mint_timeout_sec", "cpa_cloud_upload_enabled",
                "cpa_cloud_api_base", "cpa_cloud_management_key", "cpa_cloud_upload_timeout",
                "cpa_cloud_upload_retries", "cpa_batch_retry_count", "cpa_mint_browser_recycle_every",
                "cpa_health_check_before_upload", "cpa_health_check_timeout", "cpa_health_check_model",
                "cpa_health_check_headers", "cpa_health_check_use_file_headers",
                "sub2api_upload_enabled", "sub2api_export_enabled", "sub2api_api_base",
                "sub2api_api_key", "sub2api_upload_timeout", "sub2api_upload_retries",
                "sub2api_platform", "sub2api_account_type", "sub2api_account_concurrency",
                "sub2api_account_priority", "sub2api_account_load_factor",
                "sub2api_account_rate_multiplier", "sub2api_account_group_ids",
                "sub2api_default_proxy", "sub2api_local_export", "sub2api_local_export_dir"):
        if key in saved:
            base[key] = saved[key]
    base.pop("api", None)
    # Defaults for CPA queue settings even when absent from saved config
    base["cpa_batch_retry_count"] = _as_nonneg_int(base.get("cpa_batch_retry_count"), 1, maximum=5)
    base["cpa_mint_browser_recycle_every"] = _as_nonneg_int(
        base.get("cpa_mint_browser_recycle_every"), 15, maximum=200
    )
    if "cpa_health_check_before_upload" not in base:
        base["cpa_health_check_before_upload"] = True
    else:
        base["cpa_health_check_before_upload"] = bool(base.get("cpa_health_check_before_upload"))
    base["cpa_health_check_timeout"] = _as_nonneg_int(base.get("cpa_health_check_timeout"), 15, maximum=120)
    if base["cpa_health_check_timeout"] < 3:
        base["cpa_health_check_timeout"] = 3
    model = str(base.get("cpa_health_check_model") or "grok-4.5").strip() or "grok-4.5"
    base["cpa_health_check_model"] = model
    headers_text = _normalize_health_headers(base.get("cpa_health_check_headers"))
    if not headers_text:
        headers_text = _default_health_headers_text()
    base["cpa_health_check_headers"] = headers_text
    if "cpa_health_check_use_file_headers" not in base:
        base["cpa_health_check_use_file_headers"] = True
    else:
        base["cpa_health_check_use_file_headers"] = bool(base.get("cpa_health_check_use_file_headers"))
    if "sub2api_upload_enabled" not in base:
        base["sub2api_upload_enabled"] = False
    else:
        base["sub2api_upload_enabled"] = bool(base.get("sub2api_upload_enabled"))
    if "sub2api_export_enabled" not in base:
        base["sub2api_export_enabled"] = False
    else:
        base["sub2api_export_enabled"] = bool(base.get("sub2api_export_enabled"))
    base["sub2api_api_base"] = str(base.get("sub2api_api_base") or base.get("sub2api_url") or "").strip()
    base["sub2api_upload_timeout"] = _as_nonneg_int(base.get("sub2api_upload_timeout"), 30, maximum=180)
    if base["sub2api_upload_timeout"] < 5:
        base["sub2api_upload_timeout"] = 5
    base["sub2api_upload_retries"] = _as_nonneg_int(base.get("sub2api_upload_retries"), 3, maximum=10)
    if base["sub2api_upload_retries"] < 1:
        base["sub2api_upload_retries"] = 1
    base["sub2api_platform"] = str(base.get("sub2api_platform") or "grok").strip().lower() or "grok"
    # 历史默认 openai 对 xAI 账号不正确，自动纠正为 grok
    if base["sub2api_platform"] in {"openai", "chatgpt", "codex"}:
        base["sub2api_platform"] = "grok"
    acct_type = str(base.get("sub2api_account_type") or "oauth").strip().lower() or "oauth"
    if acct_type not in {"oauth", "apikey", "upstream"}:
        acct_type = "oauth"
    base["sub2api_account_type"] = acct_type
    base["sub2api_account_concurrency"] = _as_nonneg_int(base.get("sub2api_account_concurrency"), 1, maximum=200) or 1
    base["sub2api_account_priority"] = _as_nonneg_int(base.get("sub2api_account_priority"), 1, maximum=1000)
    base["sub2api_account_load_factor"] = _as_nonneg_int(base.get("sub2api_account_load_factor"), 10, maximum=10000) or 10
    try:
        rate = float(base.get("sub2api_account_rate_multiplier", 1.0) or 1.0)
    except (TypeError, ValueError):
        rate = 1.0
    if rate < 0:
        rate = 0.0
    base["sub2api_account_rate_multiplier"] = rate
    base["sub2api_account_group_ids"] = str(base.get("sub2api_account_group_ids") or "").strip()
    base["sub2api_default_proxy"] = str(base.get("sub2api_default_proxy") or "").strip()
    if "sub2api_local_export" not in base:
        base["sub2api_local_export"] = True
    else:
        base["sub2api_local_export"] = bool(base.get("sub2api_local_export"))
    base["sub2api_local_export_dir"] = str(base.get("sub2api_local_export_dir") or "./sub2api_exports").strip() or "./sub2api_exports"
    base = _normalize_domain_pool_settings(base)
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
        "temp_mail_domains_removed": str(defaults.get("temp_mail_domains_removed") or ""),
        "temp_mail_site_password": defaults.get("temp_mail_site_password", "") if payload.temp_mail_site_password is None else payload.temp_mail_site_password.strip(),
        "email_provider": str(defaults.get("email_provider") or "duckmail"),
        "outmail_api_base": str(defaults.get("outmail_api_base") or ""),
        "outmail_api_key": str(defaults.get("outmail_api_key") or ""),
        "outmail_session_cookie": str(defaults.get("outmail_session_cookie") or ""),
        "outmail_proxy": str(defaults.get("outmail_proxy") or ""),
        "outmail_plus_alias": bool(defaults.get("outmail_plus_alias", True)),
        "outmail_plus_alias_count": int(defaults.get("outmail_plus_alias_count") or 1),
        "outmail_alias_suffix_len": int(defaults.get("outmail_alias_suffix_len") or 6),
        "outmail_fetch_top": int(defaults.get("outmail_fetch_top") or 10),
        "outmail_poll_interval_sec": int(defaults.get("outmail_poll_interval_sec") or 5),
        "outmail_poll_timeout_sec": int(defaults.get("outmail_poll_timeout_sec") or 180),
        "outmail_since_padding_sec": int(defaults.get("outmail_since_padding_sec") or 30),
        "outmail_from_filter": str(defaults.get("outmail_from_filter") or "x.ai"),
        "outmail_subject_filter": str(defaults.get("outmail_subject_filter") or "xAI"),
        "outmail_group_id": (lambda v: None if v in (None, "") else v)(defaults.get("outmail_group_id")),
        "outmail_anonymous_enabled": bool(defaults.get("outmail_anonymous_enabled", False)),
        "outmail_anonymous_provider": str(defaults.get("outmail_anonymous_provider") or "cloudflare"),
        "outmail_anonymous_domain": str(defaults.get("outmail_anonymous_domain") or ""),
        "outmail_anonymous_username_prefix": str(defaults.get("outmail_anonymous_username_prefix") or ""),
        "outmail_anonymous_password": str(defaults.get("outmail_anonymous_password") or ""),
        "outmail_anonymous_delete_after": bool(defaults.get("outmail_anonymous_delete_after", False)),
        "outmail_exclude_used": bool(defaults.get("outmail_exclude_used", True)),
        "outmail_used_file": str(defaults.get("outmail_used_file") or "outmail_used_mailboxes.txt"),
        "cpa_export_enabled": defaults.get("cpa_export_enabled", True) if payload.cpa_export_enabled is None else payload.cpa_export_enabled,
        "cpa_prefer_sso_oauth": bool(defaults.get("cpa_prefer_sso_oauth", True)),
        "cpa_probe_after_write": bool(defaults.get("cpa_probe_after_write", True)),
        "cpa_probe_delay_sec": float(defaults.get("cpa_probe_delay_sec", 5) or 5),
        "cpa_probe_required": bool(defaults.get("cpa_probe_required", False)),
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
        "cpa_mint_browser_reuse": True,
        "cpa_mint_browser_recycle_every": _as_nonneg_int(defaults.get("cpa_mint_browser_recycle_every"), 15, maximum=200),
        "cpa_health_check_before_upload": bool(defaults.get("cpa_health_check_before_upload", True)),
        "cpa_health_check_timeout": _as_nonneg_int(defaults.get("cpa_health_check_timeout"), 15, maximum=120),
        "cpa_health_check_model": str(defaults.get("cpa_health_check_model") or "grok-4.5"),
        "cpa_health_check_headers": _normalize_health_headers(
            defaults.get("cpa_health_check_headers") or _default_health_headers_text()
        ),
        "cpa_health_check_use_file_headers": bool(defaults.get("cpa_health_check_use_file_headers", True)),
        "cpa_cloud_upload_enabled": defaults.get("cpa_cloud_upload_enabled", False),
        "cpa_cloud_api_base": defaults.get("cpa_cloud_api_base", ""),
        "cpa_cloud_upload_timeout": defaults.get("cpa_cloud_upload_timeout", 30),
        "cpa_cloud_upload_retries": defaults.get("cpa_cloud_upload_retries", 3),
        "sub2api_upload_enabled": bool(defaults.get("sub2api_upload_enabled", False)),
        "sub2api_export_enabled": bool(defaults.get("sub2api_export_enabled", False)),
        "sub2api_api_base": str(defaults.get("sub2api_api_base") or ""),
        "sub2api_upload_timeout": defaults.get("sub2api_upload_timeout", 30),
        "sub2api_upload_retries": defaults.get("sub2api_upload_retries", 3),
        "sub2api_platform": str(defaults.get("sub2api_platform") or "grok"),
        "sub2api_account_type": str(defaults.get("sub2api_account_type") or "oauth"),
        "sub2api_account_concurrency": defaults.get("sub2api_account_concurrency", 1),
        "sub2api_account_priority": defaults.get("sub2api_account_priority", 1),
        "sub2api_account_load_factor": defaults.get("sub2api_account_load_factor", 10),
        "sub2api_account_rate_multiplier": defaults.get("sub2api_account_rate_multiplier", 1.0),
        "sub2api_account_group_ids": str(defaults.get("sub2api_account_group_ids") or ""),
        "sub2api_default_proxy": str(defaults.get("sub2api_default_proxy") or ""),
        "sub2api_local_export": bool(defaults.get("sub2api_local_export", True)),
        "sub2api_local_export_dir": str(defaults.get("sub2api_local_export_dir") or "./sub2api_exports"),
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
            can_update = (
                existing_status in {"not_started", "queued", "running", "failed"}
                or (existing_status == "generated" and cpa_status == "uploaded")
                or (existing_status == "uploaded" and cpa_status == "uploaded")
            )
            if can_update:
                uploaded_at = now_iso() if cpa_status == "uploaded" else str(row_get(existing, "cpa_uploaded_at", "") or "")
                token_status = str(cpa_record.get("token_status") or "").strip()
                live = cpa_record.get("liveness") if isinstance(cpa_record.get("liveness"), dict) else {}
                if not token_status:
                    if live.get("alive") is True:
                        token_status = "alive"
                    elif live.get("alive") is False:
                        token_status = "dead"
                sso_alive_raw = cpa_record.get("sso_alive")
                if sso_alive_raw is None and isinstance(live.get("sso"), dict):
                    sso_alive_raw = live.get("sso", {}).get("alive")
                sso_alive_i: int | None
                if sso_alive_raw is True or sso_alive_raw == 1 or sso_alive_raw == "1":
                    sso_alive_i = 1
                elif sso_alive_raw is False or sso_alive_raw == 0 or sso_alive_raw == "0":
                    sso_alive_i = 0
                else:
                    sso_alive_i = None
                renew_src = str(cpa_record.get("mode") or "").strip()
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
                if token_status or sso_alive_i is not None or renew_src:
                    execute_no_return(
                        """
                        UPDATE accounts
                        SET token_status = CASE WHEN ? != '' THEN ? ELSE token_status END,
                            token_checked_at = CASE WHEN ? != '' THEN ? ELSE token_checked_at END,
                            token_error = CASE WHEN ? != '' THEN ? ELSE token_error END,
                            sso_alive = COALESCE(?, sso_alive),
                            last_renew_source = CASE WHEN ? != '' THEN ? ELSE last_renew_source END,
                            last_renew_at = CASE WHEN ? != '' THEN ? ELSE last_renew_at END
                        WHERE task_id = ? AND email = ? AND sso = ?
                        """,
                        (
                            token_status,
                            token_status,
                            token_status,
                            now_iso(),
                            cpa_error,
                            cpa_error,
                            sso_alive_i,
                            renew_src,
                            renew_src,
                            renew_src,
                            now_iso(),
                            int(row["id"]),
                            email,
                            sso,
                        ),
                    )

            # 注册 OAuth/测活日志并入账号 cpa_log（与账号管理「查看日志」同一份，去重）
            acc_row = fetch_one(
                "SELECT id FROM accounts WHERE task_id = ? AND email = ? AND sso = ?",
                (int(row["id"]), email, sso),
            )
            if acc_row is not None:
                merge_register_cpa_logs(int(acc_row["id"]), cpa_record)





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
        "cpa_log": row_get(row, "cpa_log", "") or "",
        "cpa_updated_at": row_get(row, "cpa_updated_at", "") or "",
        "token_status": row_get(row, "token_status", "unknown") or "unknown",
        "token_expires_at": row_get(row, "token_expires_at", "") or "",
        "token_checked_at": row_get(row, "token_checked_at", "") or "",
        "token_error": row_get(row, "token_error", "") or "",
        "sso_alive": row_get(row, "sso_alive", None),
        "last_renew_source": row_get(row, "last_renew_source", "") or "",
        "last_renew_at": row_get(row, "last_renew_at", "") or "",
    }


def append_account_cpa_log(account_id: int, message: str) -> None:
    line = str(message or "").strip()
    if not line:
        return
    stamped = f"[{now_iso()}] {line}"
    with db_lock, get_conn() as conn:
        row = conn.execute("SELECT cpa_log FROM accounts WHERE id = ?", (account_id,)).fetchone()
        existing = str(row_get(row, "cpa_log", "") or "")
        lines = [item for item in existing.splitlines() if item.strip()]
        lines.append(stamped)
        kept = lines[-400:]
        conn.execute(
            "UPDATE accounts SET cpa_log = ?, cpa_updated_at = ? WHERE id = ?",
            ("\n".join(kept), now_iso(), account_id),
        )
        conn.commit()


def append_account_cpa_log_unique(account_id: int, message: str) -> None:
    """Append log line only if the same text is not already present (avoid sync spam)."""
    line = str(message or "").strip()
    if not line:
        return
    with db_lock, get_conn() as conn:
        row = conn.execute("SELECT cpa_log FROM accounts WHERE id = ?", (account_id,)).fetchone()
        existing = str(row_get(row, "cpa_log", "") or "")
    # strip timestamps for de-dupe: compare raw message tails
    for existing_line in existing.splitlines():
        raw = existing_line.strip()
        if raw.endswith(line) or line in raw:
            return
    append_account_cpa_log(account_id, line)


def merge_register_cpa_logs(account_id: int, cpa_record: dict[str, Any]) -> None:
    """Import register-time OAuth/probe logs into the same accounts.cpa_log used by maintain ops."""
    if not isinstance(cpa_record, dict):
        return
    lines: list[str] = []
    raw_lines = cpa_record.get("log_lines")
    if isinstance(raw_lines, list):
        lines.extend(str(x).strip() for x in raw_lines if str(x).strip())
    # summary line always useful even when log_lines empty
    summary = (
        f"[register-cpa] ok={bool(cpa_record.get('ok'))} "
        f"mode={cpa_record.get('mode') or '-'} "
        f"token={cpa_record.get('token_status') or '-'} "
        f"path={cpa_record.get('path') or '-'} "
        f"err={cpa_record.get('error') or cpa_record.get('reason') or '-'}"
    )
    lines.append(summary)
    for line in lines:
        append_account_cpa_log_unique(account_id, line)



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


def _sanitize_file_segment(value: str) -> str:
    out: list[str] = []
    for ch in str(value or "").strip():
        if ("a" <= ch <= "z") or ("A" <= ch <= "Z") or ("0" <= ch <= "9") or ch in {"@", ".", "_", "-"}:
            out.append(ch)
        else:
            out.append("-")
    return "".join(out).strip("-")



def build_account_cpa_config(*, force_cloud_upload: bool = False) -> dict[str, Any]:
    """Console-side CPA config for account authorize/push (includes secrets)."""
    account_cpa_config = normalize_console_cpa_paths(merged_defaults())
    if not str(account_cpa_config.get("cpa_auth_dir") or "").strip():
        account_cpa_config["cpa_auth_dir"] = str(SOURCE_PROJECT / "cpa_auths")

    saved = read_settings()
    management_key = str(
        saved.get("cpa_cloud_management_key")
        or account_cpa_config.get("cpa_cloud_management_key")
        or os.environ.get("CPA_CLOUD_MANAGEMENT_KEY")
        or os.environ.get("CLI_PROXY_MANAGEMENT_KEY")
        or ""
    ).strip()
    if management_key:
        account_cpa_config["cpa_cloud_management_key"] = management_key

    if force_cloud_upload:
        account_cpa_config["cpa_cloud_upload_enabled"] = True

    account_cpa_config["cpa_mint_browser_reuse"] = True
    account_cpa_config["cpa_mint_browser_recycle_every"] = _as_nonneg_int(
        account_cpa_config.get("cpa_mint_browser_recycle_every"), 15, maximum=200
    )
    account_cpa_config["cpa_health_check_before_upload"] = bool(
        account_cpa_config.get("cpa_health_check_before_upload", True)
    )
    account_cpa_config["cpa_health_check_timeout"] = _as_nonneg_int(
        account_cpa_config.get("cpa_health_check_timeout"), 15, maximum=120
    )
    if account_cpa_config["cpa_health_check_timeout"] < 3:
        account_cpa_config["cpa_health_check_timeout"] = 3
    account_cpa_config["cpa_health_check_model"] = str(
        account_cpa_config.get("cpa_health_check_model") or "grok-4.5"
    ).strip() or "grok-4.5"
    account_cpa_config["cpa_health_check_headers"] = _normalize_health_headers(
        account_cpa_config.get("cpa_health_check_headers") or _default_health_headers_text()
    )
    account_cpa_config["cpa_health_check_use_file_headers"] = bool(
        account_cpa_config.get("cpa_health_check_use_file_headers", True)
    )
    # Align health-check network with browser mint path
    if not str(account_cpa_config.get("cpa_proxy") or "").strip():
        fallback_proxy = str(
            account_cpa_config.get("browser_proxy")
            or account_cpa_config.get("proxy")
            or ""
        ).strip()
        if fallback_proxy:
            account_cpa_config["cpa_proxy"] = fallback_proxy

    sub2api_key = str(
        saved.get("sub2api_api_key")
        or account_cpa_config.get("sub2api_api_key")
        or os.environ.get("SUB2API_API_KEY")
        or os.environ.get("SUB2API_KEY")
        or ""
    ).strip()
    if sub2api_key:
        account_cpa_config["sub2api_api_key"] = sub2api_key
    account_cpa_config["sub2api_api_base"] = str(
        account_cpa_config.get("sub2api_api_base")
        or account_cpa_config.get("sub2api_url")
        or os.environ.get("SUB2API_API_BASE")
        or os.environ.get("SUB2API_URL")
        or ""
    ).strip()
    account_cpa_config["sub2api_upload_enabled"] = bool(account_cpa_config.get("sub2api_upload_enabled", False))
    account_cpa_config["sub2api_export_enabled"] = bool(account_cpa_config.get("sub2api_export_enabled", False))
    # 推送 xAI 授权固定 platform=grok
    plat = str(account_cpa_config.get("sub2api_platform") or "grok").strip().lower() or "grok"
    if plat in {"openai", "chatgpt", "codex", ""}:
        plat = "grok"
    account_cpa_config["sub2api_platform"] = plat
    return account_cpa_config


def load_cpa_export_module():
    """Load apps/cpa-worker/cpa_export.py (canonical path; no root legacy copy)."""
    module_path = CPA_WORKER_DIR / "cpa_export.py"
    if not module_path.is_file():
        raise FileNotFoundError(f"CPA module not found: {module_path}")
    worker_s = str(CPA_WORKER_DIR)
    if worker_s not in sys.path:
        sys.path.insert(0, worker_s)
    spec = importlib.util.spec_from_file_location("console_cpa_export", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load CPA module: {module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def resolve_account_cpa_path(row: sqlite3.Row) -> Path | None:
    raw_path = str(row_get(row, "cpa_path", "") or "").strip()
    if raw_path:
        path = Path(raw_path).expanduser()
        if not path.is_absolute():
            path = (SOURCE_PROJECT / path).resolve()
        if path.is_file():
            return path

    email = str(row_get(row, "email", "") or "").strip()
    if not email:
        return None

    defaults = normalize_console_cpa_paths(merged_defaults())
    filename = f"xai-{_sanitize_file_segment(email)}.json"
    for key in ("cpa_auth_dir", "cpa_hotload_dir"):
        dir_raw = str(defaults.get(key) or "").strip()
        if not dir_raw:
            continue
        directory = Path(dir_raw).expanduser()
        if not directory.is_absolute():
            directory = (SOURCE_PROJECT / directory).resolve()
        candidate = directory / filename
        if candidate.is_file():
            return candidate
    return None


def run_account_cpa_export(account_id: int, *, manage_job: bool = True) -> bool:
    """Mint and optionally push CPA credentials for one stored account."""
    def account_log(message: str) -> None:
        print(f"[account-cpa:{account_id}] {message}", flush=True)
        append_account_cpa_log(account_id, message)

    ok = False
    try:
        row = account_row(account_id)
        email = str(row["email"] or "").strip()
        password = str(row["password"] or "")
        sso = str(row["sso"] or "").strip()
        if not email or not password or not sso:
            raise ValueError("账号缺少邮箱、密码或 SSO，无法执行 CPA 授权")
        append_account_cpa_log(account_id, f"开始 CPA 授权并推送: {email}")

        cpa_export = load_cpa_export_module()
        account_cpa_config = build_account_cpa_config(force_cloud_upload=False)

        result = cpa_export.export_cpa_xai_for_account(
            email,
            password,
            sso=sso,
            config=account_cpa_config,
            log_callback=account_log,
        )
        if result.get("skipped"):
            account_log(f"CPA 授权跳过: {result.get('reason') or 'skipped'}")
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
            return False
        if not result.get("ok"):
            raise RuntimeError(str(result.get("error") or result.get("reason") or "CPA 授权失败"))

        cloud = result.get("cloud_cpa_upload") or {}
        cpa_path_value = str(result.get("cpa_path") or result.get("path") or "")
        if cloud.get("health_failed"):
            status = "invalid"
            cloud_error = str(cloud.get("error") or cloud.get("message") or "测活失败")
            account_log(f"测活失败，已放弃推送: {cloud_error}")
            if cloud.get("path"):
                cpa_path_value = str(cloud.get("path") or cpa_path_value)
            execute_no_return(
                """
                UPDATE accounts
                SET cpa_status = ?, cpa_path = ?, cpa_uploaded_at = ?, cpa_error = ?, cpa_updated_at = ?
                WHERE id = ?
                """,
                (
                    status,
                    cpa_path_value,
                    "",
                    cloud_error,
                    now_iso(),
                    account_id,
                ),
            )
            try:
                record_domain_auth_failure(email, cloud_error, log=account_log)
            except Exception as domain_exc:  # noqa: BLE001
                account_log(f"域名失败统计异常: {domain_exc}")
            ok = False
            return ok

        cloud_enabled = bool(account_cpa_config.get("cpa_cloud_upload_enabled", False))
        sub2 = result.get("sub2api") or {}
        if result.get("sub2api_error") and not sub2:
            sub2 = {"ok": False, "error": result.get("sub2api_error")}
        sub2_enabled = bool(
            account_cpa_config.get("sub2api_upload_enabled", False)
            or account_cpa_config.get("sub2api_export_enabled", False)
        )
        sub2_upload_enabled = bool(account_cpa_config.get("sub2api_upload_enabled", False))

        errors: list[str] = []
        any_remote_ok = False

        if cloud.get("ok"):
            any_remote_ok = True
            account_log(f"CPA 授权文件已推送远程: {cpa_path_value}")
        elif cloud_enabled and cloud and not cloud.get("skipped"):
            err = f"CPA 远程推送失败: {cloud.get('error') or cloud}"
            errors.append(err)
            account_log(err)
        else:
            account_log(f"CPA 授权文件已生成: {cpa_path_value}")
            if not cloud_enabled:
                account_log("CPA 远程推送未开启")

        if sub2.get("ok") and not sub2.get("skipped"):
            any_remote_ok = True
            account_log(f"Sub2API 推送成功: {sub2.get('message') or cpa_path_value}")
        elif sub2.get("skipped"):
            if sub2_enabled:
                account_log(f"Sub2API 跳过: {sub2.get('reason') or 'skipped'}")
        elif sub2_enabled:
            err = f"Sub2API 推送失败: {sub2.get('error') or result.get('sub2api_error') or sub2 or 'unknown'}"
            errors.append(err)
            account_log(err)

        remote_uploaded = bool(
            cloud.get("ok")
            or (sub2.get("ok") and sub2_upload_enabled and not sub2.get("skipped_upload"))
        )
        if remote_uploaded:
            status = "uploaded"
            ok = not errors
        elif not cloud_enabled and not sub2_upload_enabled:
            status = "generated"
            ok = True
        else:
            status = "generated"
            ok = not errors

        cloud_error = "; ".join(errors)
        uploaded_at = now_iso() if status == "uploaded" else ""

        execute_no_return(
            """
            UPDATE accounts
            SET cpa_status = ?, cpa_path = ?, cpa_uploaded_at = ?, cpa_error = ?, cpa_updated_at = ?
            WHERE id = ?
            """,
            (
                status,
                cpa_path_value,
                uploaded_at,
                cloud_error,
                now_iso(),
                account_id,
            ),
        )
        if status == "uploaded" and not errors:
            try:
                record_domain_auth_success(email, log=account_log)
            except Exception as domain_exc:  # noqa: BLE001
                account_log(f"域名成功统计异常: {domain_exc}")
    except Exception as exc:
        append_account_cpa_log(account_id, f"CPA 授权失败: {exc}")
        execute_no_return(
            "UPDATE accounts SET cpa_status = ?, cpa_error = ?, cpa_updated_at = ? WHERE id = ?",
            ("failed", str(exc), now_iso(), account_id),
        )
        ok = False
    finally:
        if manage_job:
            with cpa_jobs_lock:
                cpa_jobs.pop(account_id, None)
    return ok


def run_account_cpa_upload(account_id: int, *, manage_job: bool = True) -> bool:
    """Upload an existing CPA auth file to remote CPA management."""
    def account_log(message: str) -> None:
        print(f"[account-cpa-upload:{account_id}] {message}", flush=True)
        append_account_cpa_log(account_id, message)

    cpa_path: Path | None = None
    ok = False
    try:
        row = account_row(account_id)
        email = str(row["email"] or "").strip()
        cpa_path = resolve_account_cpa_path(row)
        if not email:
            raise ValueError("账号缺少邮箱，无法执行 CPA 推送")
        if cpa_path is None:
            raise ValueError("未找到已生成的 CPA 授权文件，请先生成授权")

        account_log(f"开始推送 CPA 授权文件: {cpa_path.name}")
        cpa_export = load_cpa_export_module()
        account_cpa_config = build_account_cpa_config(force_cloud_upload=True)

        result = cpa_export.upload_cpa_auth_to_cloud(cpa_path, account_cpa_config, account_log)
        if result.get("health_failed"):
            error = str(result.get("error") or result.get("message") or "测活失败")
            account_log(f"测活失败，已放弃推送: {error}")
            path_value = str(result.get("path") or cpa_path)
            execute_no_return(
                """
                UPDATE accounts
                SET cpa_status = ?, cpa_path = ?, cpa_uploaded_at = ?, cpa_error = ?, cpa_updated_at = ?
                WHERE id = ?
                """,
                (
                    "invalid",
                    path_value,
                    "",
                    error,
                    now_iso(),
                    account_id,
                ),
            )
            try:
                record_domain_auth_failure(email, error, log=account_log)
            except Exception as domain_exc:  # noqa: BLE001
                account_log(f"域名失败统计异常: {domain_exc}")
            ok = False
            return ok

        if result.get("ok"):
            account_log(f"CPA 授权文件已推送远程: {cpa_path.name}")
            execute_no_return(
                """
                UPDATE accounts
                SET cpa_status = ?, cpa_path = ?, cpa_uploaded_at = ?, cpa_error = ?, cpa_updated_at = ?
                WHERE id = ?
                """,
                (
                    "uploaded",
                    str(cpa_path),
                    now_iso(),
                    "",
                    now_iso(),
                    account_id,
                ),
            )
            try:
                record_domain_auth_success(email, log=account_log)
            except Exception as domain_exc:  # noqa: BLE001
                account_log(f"域名成功统计异常: {domain_exc}")
            ok = True
            return ok

        if result.get("skipped"):
            raise RuntimeError(str(result.get("reason") or "push skipped"))

        error = str(result.get("error") or "upload failed")
        account_log(f"CPA 推送失败: {error}")
        execute_no_return(
            """
            UPDATE accounts
            SET cpa_status = ?, cpa_path = ?, cpa_uploaded_at = ?, cpa_error = ?, cpa_updated_at = ?
            WHERE id = ?
            """,
            (
                "generated",
                str(cpa_path),
                str(row_get(row, "cpa_uploaded_at", "") or ""),
                error,
                now_iso(),
                account_id,
            ),
        )
    except Exception as exc:
        append_account_cpa_log(account_id, f"CPA 推送失败: {exc}")
        status = "generated" if cpa_path is not None else "failed"
        # cpa_path may already exist even if upload failed; keep the generated state.
        execute_no_return(
            "UPDATE accounts SET cpa_status = ?, cpa_error = ?, cpa_updated_at = ? WHERE id = ?",
            (status, str(exc), now_iso(), account_id),
        )
        ok = False
    finally:
        if manage_job:
            with cpa_jobs_lock:
                cpa_jobs.pop(account_id, None)
    return ok


def run_account_sub2api_upload(account_id: int, *, manage_job: bool = True) -> bool:
    """Upload an existing CPA auth file to Sub2API only."""
    def account_log(message: str) -> None:
        print(f"[account-sub2api:{account_id}] {message}", flush=True)
        append_account_cpa_log(account_id, message)

    cpa_path: Path | None = None
    ok = False
    try:
        row = account_row(account_id)
        email = str(row["email"] or "").strip()
        cpa_path = resolve_account_cpa_path(row)
        if not email:
            raise ValueError("账号缺少邮箱，无法执行 Sub2API 推送")
        if cpa_path is None:
            raise ValueError("未找到已生成的 CPA 授权文件，请先生成授权")

        account_log(f"开始推送 Sub2API: {cpa_path.name}")
        cpa_export = load_cpa_export_module()
        account_cpa_config = build_account_cpa_config(force_cloud_upload=False)
        account_cpa_config["sub2api_upload_enabled"] = True

        if bool(account_cpa_config.get("cpa_health_check_before_upload", True)):
            health = cpa_export.health_check_cpa_auth_before_upload(
                cpa_path, account_cpa_config, account_log
            )
            if not health.get("ok"):
                error = str(health.get("error") or health.get("message") or "测活失败")
                account_log(f"测活失败，已放弃 Sub2API 推送: {error}")
                execute_no_return(
                    """
                    UPDATE accounts
                    SET cpa_status = ?, cpa_path = ?, cpa_uploaded_at = ?, cpa_error = ?, cpa_updated_at = ?
                    WHERE id = ?
                    """,
                    ("invalid", str(cpa_path), "", error, now_iso(), account_id),
                )
                try:
                    record_domain_auth_failure(email, error, log=account_log)
                except Exception as domain_exc:  # noqa: BLE001
                    account_log(f"域名失败统计异常: {domain_exc}")
                return False

        if str(CPA_WORKER_DIR) not in sys.path:
            sys.path.insert(0, str(CPA_WORKER_DIR))
        try:
            import cpa_to_sub2api as sub_mod  # type: ignore
        except Exception:
            sub_mod = cpa_export._import_cpa_to_sub2api()  # type: ignore[attr-defined]

        result = sub_mod.upload_cpa_auth_to_sub2api(cpa_path, account_cpa_config, account_log)
        if result.get("ok"):
            account_log(f"Sub2API 推送成功: {result.get('message') or cpa_path.name}")
            execute_no_return(
                """
                UPDATE accounts
                SET cpa_status = ?, cpa_path = ?, cpa_uploaded_at = ?, cpa_error = ?, cpa_updated_at = ?
                WHERE id = ?
                """,
                ("uploaded", str(cpa_path), now_iso(), "", now_iso(), account_id),
            )
            try:
                record_domain_auth_success(email, log=account_log)
            except Exception as domain_exc:  # noqa: BLE001
                account_log(f"域名成功统计异常: {domain_exc}")
            ok = True
            return ok

        if result.get("skipped"):
            raise RuntimeError(str(result.get("reason") or "sub2api push skipped"))

        error = str(result.get("error") or "sub2api upload failed")
        account_log(f"Sub2API 推送失败: {error}")
        execute_no_return(
            """
            UPDATE accounts
            SET cpa_status = ?, cpa_path = ?, cpa_uploaded_at = ?, cpa_error = ?, cpa_updated_at = ?
            WHERE id = ?
            """,
            (
                "generated",
                str(cpa_path),
                str(row_get(row, "cpa_uploaded_at", "") or ""),
                error,
                now_iso(),
                account_id,
            ),
        )
    except Exception as exc:
        append_account_cpa_log(account_id, f"Sub2API 推送失败: {exc}")
        status = "generated" if cpa_path is not None else "failed"
        execute_no_return(
            "UPDATE accounts SET cpa_status = ?, cpa_error = ?, cpa_updated_at = ? WHERE id = ?",
            (status, str(exc), now_iso(), account_id),
        )
        ok = False
    finally:
        if manage_job:
            with cpa_jobs_lock:
                cpa_jobs.pop(account_id, None)
    return ok


def _cpa_queue_snapshot() -> dict[str, Any]:
    with cpa_jobs_lock:
        snap = deepcopy(cpa_queue_state)
        snap["queue_size"] = cpa_work_queue.qsize()
        snap["busy_ids"] = sorted(cpa_jobs.keys())
        return snap


def _validate_cpa_cloud_config(mode: str) -> str | None:
    """Return error message when cloud push config is incomplete for the given mode."""
    settings = merged_defaults()
    saved = read_settings()
    enabled = bool(settings.get("cpa_cloud_upload_enabled"))
    api_base = str(settings.get("cpa_cloud_api_base") or "").strip()
    management_key = str(saved.get("cpa_cloud_management_key") or settings.get("cpa_cloud_management_key") or "").strip()

    if mode == "push_only":
        if not enabled:
            return "未开启「推送 CPA 授权到远程」，无法批量推送"
        if not api_base:
            return "未配置远程 CPA 管理地址"
        if not management_key:
            return "未配置远程 CPA 管理密钥"
        return None

    if mode == "push_sub2api":
        return None

    # authorize_and_push: only require full cloud config when upload is enabled
    if enabled:
        if not api_base:
            return "已开启远程推送，但未配置远程 CPA 管理地址"
        if not management_key:
            return "已开启远程推送，但未配置远程 CPA 管理密钥"
    return None


def _validate_sub2api_config(mode: str) -> str | None:
    """Return error when Sub2API config is incomplete for the given mode."""
    settings = merged_defaults()
    saved = read_settings()
    upload_enabled = bool(settings.get("sub2api_upload_enabled"))
    api_base = str(settings.get("sub2api_api_base") or settings.get("sub2api_url") or "").strip()
    api_key = str(
        saved.get("sub2api_api_key")
        or settings.get("sub2api_api_key")
        or os.environ.get("SUB2API_API_KEY")
        or os.environ.get("SUB2API_KEY")
        or ""
    ).strip()

    if mode == "push_sub2api":
        if not api_base:
            return "未配置 Sub2API 地址（sub2api_api_base）"
        if not api_key:
            return "未配置 Sub2API API Key（sub2api_api_key）"
        return None

    if upload_enabled:
        if not api_base:
            return "已开启 Sub2API 推送，但未配置 Sub2API 地址"
        if not api_key:
            return "已开启 Sub2API 推送，但未配置 Sub2API API Key"
    return None


def _mark_account_cpa_cancelled(account_id: int, reason: str = "批量任务已取消") -> None:
    execute_no_return(
        "UPDATE accounts SET cpa_status = ?, cpa_error = ?, cpa_updated_at = ? WHERE id = ?",
        ("cancelled", reason, now_iso(), account_id),
    )
    append_account_cpa_log(account_id, reason)


def _record_cpa_result(account_id: int, email: str, status: str, error: str = "") -> None:
    item = {
        "id": account_id,
        "email": email,
        "status": status,
        "error": error,
        "at": now_iso(),
    }
    results = cpa_queue_state.setdefault("results", [])
    results.append(item)
    if len(results) > 500:
        del results[:-500]


def _drain_cancelled_cpa_jobs() -> int:
    drained = 0
    while True:
        try:
            item = cpa_work_queue.get_nowait()
        except queue.Empty:
            break
        account_id = int(item.get("account_id") or 0)
        email = ""
        try:
            row = fetch_one("SELECT email FROM accounts WHERE id = ?", (account_id,))
            email = str(row_get(row, "email", "") or "") if row else ""
        except Exception:
            pass
        _mark_account_cpa_cancelled(account_id, "排队中取消：批量任务已停止")
        _record_cpa_result(account_id, email, "cancelled", "cancelled while queued")
        cpa_queue_state["cancelled"] = int(cpa_queue_state.get("cancelled") or 0) + 1
        cpa_queue_state["done"] = int(cpa_queue_state.get("done") or 0) + 1
        cpa_jobs.pop(account_id, None)
        cpa_work_queue.task_done()
        drained += 1
    return drained


def _finish_cpa_session_if_idle() -> None:
    if cpa_work_queue.qsize() > 0 or cpa_queue_state.get("current_id") is not None:
        return
    if not cpa_queue_state.get("active"):
        return
    cpa_queue_state["active"] = False
    cpa_queue_state["finished_at"] = now_iso()
    cpa_queue_state["current_id"] = None
    cpa_queue_state["current_email"] = ""
    if cpa_cancel_event.is_set():
        cpa_queue_state["message"] = (
            f"已停止：成功 {cpa_queue_state.get('success', 0)}，"
            f"失败 {cpa_queue_state.get('failed', 0)}，"
            f"取消 {cpa_queue_state.get('cancelled', 0)}"
        )
    else:
        cpa_queue_state["message"] = (
            f"全部完成：成功 {cpa_queue_state.get('success', 0)}，"
            f"失败 {cpa_queue_state.get('failed', 0)}"
        )
    cpa_cancel_event.clear()


def _shutdown_shared_mint_browser() -> None:
    try:
        if str(CPA_WORKER_DIR) not in sys.path:
            sys.path.insert(0, str(CPA_WORKER_DIR))
        from cpa_xai.browser_confirm import shutdown_mint_browsers  # type: ignore

        shutdown_mint_browsers()
        print("[account-cpa-queue] shared mint browser closed", flush=True)
    except Exception as exc:  # noqa: BLE001
        print(f"[account-cpa-queue] close mint browser failed: {exc}", flush=True)


def _process_one_cpa_job(account_id: int, mode: str) -> tuple[bool, str, str]:
    row = account_row(account_id)
    email = str(row_get(row, "email", "") or "")
    if mode == "push_only":
        ok = run_account_cpa_upload(account_id, manage_job=False)
        status = "uploaded" if ok else "failed"
    elif mode == "push_sub2api":
        ok = run_account_sub2api_upload(account_id, manage_job=False)
        status = "uploaded" if ok else "failed"
    elif mode == "oauth_only":
        result = run_account_sso_oauth(account_id, manage_job=False)
        ok = bool(result.get("ok"))
        latest = fetch_one("SELECT cpa_status FROM accounts WHERE id = ?", (account_id,))
        status = str(row_get(latest, "cpa_status", "failed") or ("generated" if ok else "failed"))
    elif mode == "probe_only":
        result = run_account_token_probe(account_id, probe_api=True, probe_sso=True, auto_refresh=False)
        ok = bool(result.get("ok") or result.get("alive"))
        latest = fetch_one(
            "SELECT cpa_status, token_status, cpa_error FROM accounts WHERE id = ?",
            (account_id,),
        )
        # Prefer persisted account status after probe (invalid on failure)
        status = str(
            row_get(latest, "cpa_status", "")
            or result.get("token_status")
            or ("alive" if ok else "invalid")
        )
        if not ok and status not in {"invalid", "failed"}:
            status = "invalid"
    elif mode == "refresh_only":
        result = run_account_token_refresh(account_id, force=True, allow_sso_fallback=True)
        ok = bool(result.get("ok"))
        status = "refreshed" if ok else "failed"
    else:
        ok = run_account_cpa_export(account_id, manage_job=False)
        # re-read status written by export
        latest = fetch_one("SELECT cpa_status FROM accounts WHERE id = ?", (account_id,))
        status = str(row_get(latest, "cpa_status", "failed") or ("generated" if ok else "failed"))
    return ok, email, status


def cpa_worker_loop() -> None:
    """Single shared worker: sequential jobs, one browser via thread-local reuse."""
    print("[account-cpa-queue] worker started", flush=True)
    while True:
        try:
            item = cpa_work_queue.get(timeout=1.0)
        except queue.Empty:
            with cpa_jobs_lock:
                if cpa_queue_state.get("active") and cpa_queue_state.get("current_id") is None:
                    if cpa_work_queue.qsize() == 0:
                        _shutdown_shared_mint_browser()
                        _finish_cpa_session_if_idle()
            continue

        account_id = int(item.get("account_id") or 0)
        mode = str(item.get("mode") or "authorize_and_push")

        with cpa_jobs_lock:
            if cpa_cancel_event.is_set():
                _mark_account_cpa_cancelled(account_id, "排队中取消：批量任务已停止")
                _record_cpa_result(account_id, "", "cancelled", "cancelled while queued")
                cpa_queue_state["cancelled"] = int(cpa_queue_state.get("cancelled") or 0) + 1
                cpa_queue_state["done"] = int(cpa_queue_state.get("done") or 0) + 1
                cpa_jobs.pop(account_id, None)
                cpa_work_queue.task_done()
                drained = _drain_cancelled_cpa_jobs()
                if drained:
                    print(f"[account-cpa-queue] drained {drained} cancelled jobs", flush=True)
                if cpa_work_queue.qsize() == 0:
                    _shutdown_shared_mint_browser()
                    _finish_cpa_session_if_idle()
                continue

            row = fetch_one("SELECT email FROM accounts WHERE id = ?", (account_id,))
            email = str(row_get(row, "email", "") or "") if row else ""
            cpa_queue_state["current_id"] = account_id
            cpa_queue_state["current_email"] = email
            cpa_queue_state["mode"] = mode
            cpa_queue_state["message"] = f"正在处理 {email or ('#' + str(account_id))}"

        try:
            ok, email, error = _process_one_cpa_job(account_id, mode)
            with cpa_jobs_lock:
                if cpa_cancel_event.is_set() and not ok:
                    _mark_account_cpa_cancelled(account_id, "任务取消")
                    _record_cpa_result(account_id, email, "cancelled", error or "cancelled")
                    cpa_queue_state["cancelled"] = int(cpa_queue_state.get("cancelled") or 0) + 1
                elif ok:
                    row3 = fetch_one("SELECT cpa_status, cpa_error FROM accounts WHERE id = ?", (account_id,))
                    status = str(row_get(row3, "cpa_status", "generated") or "generated") if row3 else "generated"
                    err = str(row_get(row3, "cpa_error", "") or "") if row3 else ""
                    if status in {"queued", "running", "uploading"}:
                        status = "generated" if mode in {"probe_only", "refresh_only", "oauth_only"} else status
                        if status in {"queued", "running", "uploading"}:
                            status = "generated"
                        try:
                            execute_no_return(
                                "UPDATE accounts SET cpa_status = ?, cpa_updated_at = ? WHERE id = ?",
                                (status, now_iso(), account_id),
                            )
                        except Exception:
                            pass
                    # Defensive: generated+error should not be counted as queue success
                    if status == "generated" and err:
                        _record_cpa_result(account_id, email, status, err)
                        cpa_queue_state["failed"] = int(cpa_queue_state.get("failed") or 0) + 1
                    else:
                        _record_cpa_result(account_id, email, status, "")
                        cpa_queue_state["success"] = int(cpa_queue_state.get("success") or 0) + 1
                else:
                    row3 = fetch_one("SELECT cpa_status, cpa_error FROM accounts WHERE id = ?", (account_id,))
                    status = str(row_get(row3, "cpa_status", "failed") or "failed") if row3 else "failed"
                    err = str(row_get(row3, "cpa_error", "") or error or "") if row3 else (error or "")
                    # Never leave accounts stuck in busy states after a finished job
                    if status in {"queued", "running", "uploading"}:
                        status = "invalid" if mode in {"probe_only", "refresh_only", "oauth_only"} else "failed"
                        try:
                            execute_no_return(
                                "UPDATE accounts SET cpa_status = ?, cpa_error = ?, cpa_updated_at = ? WHERE id = ?",
                                (status, (err or "queue job finished with busy status")[:500], now_iso(), account_id),
                            )
                        except Exception:
                            pass
                    if status not in {"invalid", "failed", "generated", "not_started", "uploaded", "cancelled"}:
                        status = "failed"
                    _record_cpa_result(account_id, email, status, err)
                    cpa_queue_state["failed"] = int(cpa_queue_state.get("failed") or 0) + 1
                cpa_queue_state["done"] = int(cpa_queue_state.get("done") or 0) + 1
                cpa_jobs.pop(account_id, None)
                cpa_queue_state["current_id"] = None
                cpa_queue_state["current_email"] = ""
        except Exception as exc:  # noqa: BLE001
            print(f"[account-cpa-queue] job error id={account_id}: {exc}", flush=True)
            with cpa_jobs_lock:
                _record_cpa_result(account_id, "", "failed", str(exc))
                cpa_queue_state["failed"] = int(cpa_queue_state.get("failed") or 0) + 1
                cpa_queue_state["done"] = int(cpa_queue_state.get("done") or 0) + 1
                cpa_jobs.pop(account_id, None)
                cpa_queue_state["current_id"] = None
                cpa_queue_state["current_email"] = ""
                try:
                    execute_no_return(
                        "UPDATE accounts SET cpa_status = ?, cpa_error = ?, cpa_updated_at = ? WHERE id = ?",
                        ("failed", str(exc), now_iso(), account_id),
                    )
                except Exception:
                    pass
        finally:
            cpa_work_queue.task_done()

        with cpa_jobs_lock:
            if cpa_cancel_event.is_set():
                drained = _drain_cancelled_cpa_jobs()
                if drained:
                    print(f"[account-cpa-queue] drained {drained} cancelled jobs", flush=True)
            if cpa_work_queue.qsize() == 0 and cpa_queue_state.get("current_id") is None:
                _shutdown_shared_mint_browser()
                _finish_cpa_session_if_idle()


def _ensure_cpa_worker_locked() -> None:
    """Start the global CPA worker if needed. Caller must hold cpa_jobs_lock."""
    global cpa_worker_thread
    if cpa_worker_thread is not None and cpa_worker_thread.is_alive():
        return
    cpa_worker_thread = threading.Thread(target=cpa_worker_loop, daemon=True, name="cpa-global-queue")
    cpa_worker_thread.start()


def ensure_cpa_worker() -> None:
    with cpa_jobs_lock:
        _ensure_cpa_worker_locked()




def _ensure_cpa_xai_importable() -> None:
    """Make apps/cpa-worker/cpa_xai importable for console token/sso helpers."""
    root = CPA_WORKER_DIR
    if (root / "cpa_xai" / "__init__.py").is_file():
        root_s = str(root)
        if root_s not in sys.path:
            sys.path.insert(0, root_s)
        return
    raise ModuleNotFoundError(f"cpa_xai not found under {root}")


def _account_proxy_for_token_ops(config: dict[str, Any] | None = None) -> str:
    cfg = config or build_account_cpa_config()
    proxy = str(
        cfg.get("cpa_proxy")
        or cfg.get("browser_proxy")
        or cfg.get("proxy")
        or ""
    ).strip()
    return proxy


def _update_account_token_fields(
    account_id: int,
    *,
    token_status: str | None = None,
    token_expires_at: str | None = None,
    token_error: str | None = None,
    sso_alive: bool | int | None = None,
    last_renew_source: str | None = None,
    last_renew_at: str | None = None,
    cpa_path: str | None = None,
    cpa_status: str | None = None,
) -> None:
    fields: list[str] = ["token_checked_at = ?"]
    values: list[Any] = [now_iso()]
    if token_status is not None:
        fields.append("token_status = ?")
        values.append(token_status)
    if token_expires_at is not None:
        fields.append("token_expires_at = ?")
        values.append(token_expires_at)
    if token_error is not None:
        fields.append("token_error = ?")
        values.append(token_error)
    if sso_alive is not None:
        fields.append("sso_alive = ?")
        values.append(1 if bool(sso_alive) else 0)
    if last_renew_source is not None:
        fields.append("last_renew_source = ?")
        values.append(last_renew_source)
    if last_renew_at is not None:
        fields.append("last_renew_at = ?")
        values.append(last_renew_at)
    if cpa_path is not None:
        fields.append("cpa_path = ?")
        values.append(cpa_path)
    if cpa_status is not None:
        fields.append("cpa_status = ?")
        values.append(cpa_status)
    fields.append("cpa_updated_at = ?")
    values.append(now_iso())
    values.append(account_id)
    execute_no_return(
        f"UPDATE accounts SET {', '.join(fields)} WHERE id = ?",
        tuple(values),
    )


def run_account_token_probe(
    account_id: int,
    *,
    probe_api: bool = True,
    probe_sso: bool = True,
    auto_refresh: bool = False,
    force_refresh: bool = False,
) -> dict[str, Any]:
    """账号测活：SSO cookie + 可选 API chat/completions（与注册后测活同一套 check_account_liveness）。"""
    row = account_row(account_id)
    email = str(row_get(row, "email", "") or "")
    sso = str(row_get(row, "sso", "") or "").strip()
    cfg = build_account_cpa_config()
    proxy = _account_proxy_for_token_ops(cfg)
    auth_path = resolve_account_cpa_path(row)

    def account_log(message: str) -> None:
        print(f"[account-probe:{account_id}] {message}", flush=True)
        append_account_cpa_log(account_id, message)

    account_log(
        f"开始测活 email={email} probe_api={probe_api} probe_sso={probe_sso} "
        f"auto_refresh={auto_refresh} force_refresh={force_refresh}"
    )
    try:
        _ensure_cpa_xai_importable()
        from cpa_xai.token_maintain import check_account_liveness  # type: ignore

        result = check_account_liveness(
            auth_path=auth_path,
            sso=sso or None,
            proxy=proxy or None,
            probe_api=probe_api and auth_path is not None,
            probe_sso=probe_sso and bool(sso),
            auto_refresh=auto_refresh and auth_path is not None,
            force_refresh=force_refresh,
            skew_seconds=float(cfg.get("cpa_token_refresh_skew_sec", 300) or 300),
            model=str(cfg.get("cpa_health_check_model") or "grok-4.5"),
            timeout=float(cfg.get("cpa_health_check_timeout", 15) or 15),
            log=account_log,
        )
    except Exception as exc:  # noqa: BLE001
        account_log(f"测活异常: {exc}")
        _update_account_token_fields(
            account_id,
            token_status="error",
            token_error=str(exc)[:500],
            sso_alive=None,
            cpa_status="invalid",
        )
        execute_no_return(
            "UPDATE accounts SET cpa_error = ?, cpa_updated_at = ? WHERE id = ?",
            (str(exc)[:500], now_iso(), account_id),
        )
        return {"ok": False, "account_id": account_id, "error": str(exc), "token_status": "error", "cpa_status": "invalid"}

    sso_res = result.get("sso") if isinstance(result.get("sso"), dict) else {}
    health = result.get("health") if isinstance(result.get("health"), dict) else {}
    refresh = result.get("refresh") if isinstance(result.get("refresh"), dict) else {}
    auth = result.get("auth") if isinstance(result.get("auth"), dict) else None
    expires_at = ""
    if auth:
        expires_at = str(auth.get("expired") or "")
    elif auth_path:
        try:
            import json as _json
            data = _json.loads(Path(auth_path).read_text(encoding="utf-8"))
            expires_at = str(data.get("expired") or "")
        except Exception:
            expires_at = ""

    if result.get("alive"):
        status = "alive"
    elif sso_res.get("alive") is False and not auth_path:
        status = "sso_dead"
    elif health and health.get("ok") is False:
        status = "api_dead"
    elif sso_res.get("alive") is False:
        status = "sso_dead"
    else:
        status = "dead" if result.get("ok") is False else "unknown"

    renew_source = refresh.get("source") if refresh.get("renewed") else None
    err_text = str(result.get("error") or health.get("message") or "")[:500]
    # Map liveness into account CPA status so UI "CPA" column reflects probe outcome.
    # CRITICAL: batch maintain may set cpa_status=queued; must always leave busy states
    # (queued/running/uploading), otherwise action buttons stay disabled forever.
    busy_cpa = {"queued", "running", "uploading"}
    cpa_status_update: str | None = None
    row_now = fetch_one("SELECT cpa_status, cpa_path FROM accounts WHERE id = ?", (account_id,))
    prev_cpa = str(row_get(row_now, "cpa_status", "not_started") or "not_started")
    path_now = str(auth_path or row_get(row_now, "cpa_path", "") or "").strip()

    if result.get("alive"):
        if prev_cpa in busy_cpa:
            # finished queue job: restore a non-busy status
            cpa_status_update = "generated" if path_now else "not_started"
        elif prev_cpa in {"not_started", "failed", "invalid"} and path_now:
            cpa_status_update = "generated"
        elif prev_cpa == "invalid" and path_now:
            cpa_status_update = "generated"
        # uploaded/generated stay as-is on success
    else:
        # probe failed: always leave busy states and mark invalid
        cpa_status_update = "invalid"

    _update_account_token_fields(
        account_id,
        token_status=status,
        token_expires_at=expires_at,
        token_error=err_text,
        sso_alive=sso_res.get("alive") if "alive" in sso_res else None,
        last_renew_source=str(renew_source or "") or None,
        last_renew_at=now_iso() if renew_source else None,
        cpa_path=path_now or None,
        cpa_status=cpa_status_update,
    )
    if cpa_status_update == "invalid" and err_text:
        execute_no_return(
            "UPDATE accounts SET cpa_error = ?, cpa_updated_at = ? WHERE id = ?",
            (err_text, now_iso(), account_id),
        )
    elif result.get("alive") and cpa_status_update in {"generated", "uploaded"}:
        execute_no_return(
            "UPDATE accounts SET cpa_error = '', cpa_updated_at = ? WHERE id = ?",
            (now_iso(), account_id),
        )
    elif result.get("alive") and prev_cpa in {"generated", "uploaded"}:
        # clear previous probe error on success without status change
        execute_no_return(
            "UPDATE accounts SET cpa_error = '', cpa_updated_at = ? WHERE id = ?",
            (now_iso(), account_id),
        )

    account_log(
        f"测活完成 status={status} cpa={cpa_status_update or prev_cpa} "
        f"alive={result.get('alive')} sso={sso_res.get('alive')} health={health.get('ok')}"
    )
    return {
        "ok": bool(result.get("ok")),
        "alive": bool(result.get("alive")),
        "account_id": account_id,
        "email": email,
        "token_status": status,
        "cpa_status": cpa_status_update or prev_cpa,
        "token_expires_at": expires_at,
        "result": result,
    }


def run_account_token_refresh(
    account_id: int,
    *,
    force: bool = True,
    allow_sso_fallback: bool = True,
) -> dict[str, Any]:
    """Token 续期：优先 refresh_token，失败时可回退 SSO OAuth 重建。"""
    row = account_row(account_id)
    email = str(row_get(row, "email", "") or "")
    sso = str(row_get(row, "sso", "") or "").strip()
    cfg = build_account_cpa_config()
    proxy = _account_proxy_for_token_ops(cfg)
    auth_path = resolve_account_cpa_path(row)

    def account_log(message: str) -> None:
        print(f"[account-refresh:{account_id}] {message}", flush=True)
        append_account_cpa_log(account_id, message)

    if auth_path is None and not (allow_sso_fallback and sso):
        msg = "缺少 auth 文件且无 SSO，无法续期"
        account_log(msg)
        _update_account_token_fields(
            account_id,
            token_status="error",
            token_error=msg,
            cpa_status="invalid",
        )
        return {"ok": False, "account_id": account_id, "error": msg}

    account_log(f"开始 Token 续期 force={force} sso_fallback={allow_sso_fallback}")
    try:
        _ensure_cpa_xai_importable()
        from cpa_xai.token_maintain import (  # type: ignore
            refresh_cpa_auth,
            refresh_cpa_auth_file,
            load_cpa_auth_file,
        )
        from cpa_xai.sso_oauth import sso_oauth_to_cpa_auth  # type: ignore
        from cpa_xai.writer import write_cpa_xai_auth  # type: ignore

        if auth_path is not None:
            result = refresh_cpa_auth_file(
                auth_path,
                proxy=proxy or None,
                skew_seconds=float(cfg.get("cpa_token_refresh_skew_sec", 300) or 300),
                force=force,
                sso=sso or None,
                allow_sso_fallback=allow_sso_fallback and bool(sso),
                persist=True,
                log=account_log,
            )
            path_value = str(result.get("path") or auth_path)
        else:
            # No existing file: mint via SSO only
            account_log("无 auth 文件，改用 SSO OAuth 重建")
            payload = sso_oauth_to_cpa_auth(
                sso,
                email=email,
                proxy=proxy or None,
                base_url=str(cfg.get("cpa_base_url") or "https://cli-chat-proxy.grok.com/v1"),
                log=account_log,
            )
            out_dir = Path(str(cfg.get("cpa_auth_dir") or (RUNTIME_DIR / "cpa_auths"))).expanduser()
            if not out_dir.is_absolute():
                out_dir = (SOURCE_PROJECT / out_dir).resolve()
            path_obj = write_cpa_xai_auth(out_dir, payload)
            path_value = str(path_obj)
            result = {
                "ok": True,
                "renewed": True,
                "source": "sso",
                "auth": payload,
                "path": path_value,
            }
    except Exception as exc:  # noqa: BLE001
        account_log(f"续期异常: {exc}")
        _update_account_token_fields(
            account_id,
            token_status="refresh_failed",
            token_error=str(exc)[:500],
            cpa_status="invalid",
        )
        return {"ok": False, "account_id": account_id, "error": str(exc)}

    auth = result.get("auth") if isinstance(result.get("auth"), dict) else {}
    expires_at = str(auth.get("expired") or "")
    if result.get("ok") and (result.get("renewed") or result.get("skipped")):
        status = "alive" if not result.get("permanent") else "refresh_invalid"
        if result.get("skipped"):
            status = "alive"
        source = str(result.get("source") or "")
        # leave queue busy states even when refresh skipped (token still valid)
        row_now = fetch_one("SELECT cpa_status FROM accounts WHERE id = ?", (account_id,))
        prev_cpa = str(row_get(row_now, "cpa_status", "not_started") or "not_started")
        if result.get("renewed") or prev_cpa in {"queued", "running", "uploading", "failed", "invalid", "not_started"}:
            refresh_cpa_status = "generated"
        else:
            refresh_cpa_status = None
        _update_account_token_fields(
            account_id,
            token_status=status if result.get("ok") else "refresh_failed",
            token_expires_at=expires_at,
            token_error=str(result.get("error") or "")[:500],
            last_renew_source=source or None,
            last_renew_at=now_iso() if result.get("renewed") else None,
            cpa_path=path_value,
            cpa_status=refresh_cpa_status,
        )
        account_log(
            f"续期完成 ok={result.get('ok')} renewed={result.get('renewed')} "
            f"source={result.get('source')} exp={expires_at}"
        )
        return {
            "ok": bool(result.get("ok")),
            "account_id": account_id,
            "email": email,
            "renewed": bool(result.get("renewed")),
            "skipped": bool(result.get("skipped")),
            "source": result.get("source"),
            "path": path_value,
            "token_expires_at": expires_at,
            "result": {k: v for k, v in result.items() if k != "auth"},
        }

    _update_account_token_fields(
        account_id,
        token_status="refresh_invalid" if result.get("permanent") else "refresh_failed",
        token_expires_at=expires_at,
        token_error=str(result.get("error") or "refresh_failed")[:500],
        cpa_path=path_value if "path_value" in locals() else None,
        cpa_status="invalid",
    )
    account_log(f"续期失败: {result.get('error')}")
    return {
        "ok": False,
        "account_id": account_id,
        "email": email,
        "error": result.get("error") or "refresh_failed",
        "result": {k: v for k, v in result.items() if k != "auth"},
    }


def run_account_sso_oauth(
    account_id: int,
    *,
    manage_job: bool = False,
) -> dict[str, Any]:
    """单独 OAuth：SSO cookie 经 device flow 写入 CPA auth（与注册后 OAuth 同一套）。"""
    row = account_row(account_id)
    email = str(row_get(row, "email", "") or "").strip()
    sso = str(row_get(row, "sso", "") or "").strip()
    cfg = build_account_cpa_config()

    def account_log(message: str) -> None:
        print(f"[account-oauth:{account_id}] {message}", flush=True)
        append_account_cpa_log(account_id, message)

    if not email or not sso:
        msg = "缺少邮箱或 SSO，无法执行 OAuth"
        account_log(msg)
        execute_no_return(
            "UPDATE accounts SET cpa_status = ?, cpa_error = ?, cpa_updated_at = ? WHERE id = ?",
            ("failed", msg, now_iso(), account_id),
        )
        return {"ok": False, "account_id": account_id, "error": msg}

    execute_no_return(
        "UPDATE accounts SET cpa_status = ?, cpa_error = '', cpa_updated_at = ? WHERE id = ?",
        ("running", now_iso(), account_id),
    )
    account_log("开始 SSO OAuth（纯 HTTP device-flow）")
    try:
        mod = load_cpa_export_module()
        # 与注册完成后 / 单独 OAuth 一致：纯 SSO OAuth，不做内置 models probe
        oauth_cfg = {**cfg, "cpa_prefer_sso_oauth": True, "cpa_probe_after_write": False}
        if hasattr(mod, "export_cpa_xai_via_sso"):
            result = mod.export_cpa_xai_via_sso(
                email,
                sso,
                config=oauth_cfg,
                log_callback=account_log,
            )
        else:
            # fallback: export_cpa_xai_for_account with prefer sso
            result = mod.export_cpa_xai_for_account(
                email,
                str(row_get(row, "password", "") or "unused"),
                sso=sso,
                config=oauth_cfg,
                log_callback=account_log,
            )
        if not result.get("ok") or not result.get("path"):
            raise RuntimeError(result.get("error") or "SSO OAuth failed")
        path_value = str(result["path"])
        expires_at = ""
        try:
            import json as _json
            data = _json.loads(Path(path_value).read_text(encoding="utf-8"))
            expires_at = str(data.get("expired") or "")
        except Exception:
            pass
        execute_no_return(
            """
            UPDATE accounts
            SET cpa_status = ?, cpa_path = ?, cpa_error = '', cpa_updated_at = ?,
                token_status = ?, token_expires_at = ?, token_checked_at = ?,
                last_renew_source = ?, last_renew_at = ?, sso_alive = 1, token_error = ''
            WHERE id = ?
            """,
            (
                "generated",
                path_value,
                now_iso(),
                "alive",
                expires_at,
                now_iso(),
                "sso_oauth",
                now_iso(),
                account_id,
            ),
        )
        account_log(f"SSO OAuth 成功: {path_value}")
        return {
            "ok": True,
            "account_id": account_id,
            "email": email,
            "path": path_value,
            "token_expires_at": expires_at,
            "mode": result.get("mode") or "sso_oauth",
        }
    except Exception as exc:  # noqa: BLE001
        account_log(f"SSO OAuth 失败: {exc}")
        execute_no_return(
            "UPDATE accounts SET cpa_status = ?, cpa_error = ?, cpa_updated_at = ?, token_status = ?, token_error = ?, token_checked_at = ? WHERE id = ?",
            ("failed", str(exc)[:500], now_iso(), "oauth_failed", str(exc)[:500], now_iso(), account_id),
        )
        return {"ok": False, "account_id": account_id, "error": str(exc)}
    finally:
        if manage_job:
            with cpa_jobs_lock:
                cpa_jobs.pop(account_id, None)



def enqueue_cpa_jobs(account_ids: list[int], mode: str) -> dict[str, Any]:
    """Validate and enqueue accounts into the global sequential CPA queue."""
    mode = str(mode or "authorize_and_push").strip()
    allowed_modes = {
        "authorize_and_push",
        "push_only",
        "push_sub2api",
        "probe_only",
        "refresh_only",
        "oauth_only",
    }
    if mode not in allowed_modes:
        raise HTTPException(
            status_code=400,
            detail="mode 仅支持 authorize_and_push / push_only / push_sub2api / probe_only / refresh_only / oauth_only",
        )

    cloud_error = _validate_cpa_cloud_config(mode)
    if cloud_error:
        raise HTTPException(status_code=400, detail=cloud_error)
    sub2_error = _validate_sub2api_config(mode)
    if sub2_error:
        raise HTTPException(status_code=400, detail=sub2_error)

    ordered_ids: list[int] = []
    seen: set[int] = set()
    for raw_id in account_ids:
        account_id = int(raw_id)
        if account_id in seen:
            continue
        seen.add(account_id)
        ordered_ids.append(account_id)

    accepted: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    candidates: list[tuple[int, str]] = []

    for account_id in ordered_ids:
        row = fetch_one("SELECT * FROM accounts WHERE id = ?", (account_id,))
        if row is None:
            rejected.append({"id": account_id, "reason": "账号不存在"})
            continue
        email = str(row_get(row, "email", "") or "").strip()
        if mode in {"push_only", "push_sub2api"}:
            if resolve_account_cpa_path(row) is None:
                rejected.append({
                    "id": account_id,
                    "email": email,
                    "reason": "缺少已生成的 CPA 授权文件，无法仅推送",
                })
                continue
        elif mode in {"probe_only", "refresh_only"}:
            sso = str(row_get(row, "sso", "") or "").strip()
            has_file = resolve_account_cpa_path(row) is not None
            if mode == "probe_only" and not sso and not has_file:
                rejected.append({
                    "id": account_id,
                    "email": email,
                    "reason": "缺少 SSO 且无 CPA 文件，无法测活",
                })
                continue
            if mode == "refresh_only" and not has_file and not sso:
                rejected.append({
                    "id": account_id,
                    "email": email,
                    "reason": "缺少 CPA 文件且无 SSO，无法续期",
                })
                continue
        elif mode == "oauth_only":
            sso = str(row_get(row, "sso", "") or "").strip()
            if not email or not sso:
                rejected.append({
                    "id": account_id,
                    "email": email,
                    "reason": "缺少邮箱或 SSO，无法执行 OAuth",
                })
                continue
        else:
            password = str(row_get(row, "password", "") or "")
            sso = str(row_get(row, "sso", "") or "").strip()
            if not email or not password or not sso:
                rejected.append({
                    "id": account_id,
                    "email": email,
                    "reason": "缺少邮箱/密码/SSO，无法执行 CPA 授权",
                })
                continue
        candidates.append((account_id, email))

    to_start: list[tuple[int, str]] = []
    with cpa_jobs_lock:
        for account_id, email in candidates:
            if account_id in cpa_jobs:
                skipped.append({"id": account_id, "email": email, "reason": "CPA 任务已在队列或执行中"})
                continue
            to_start.append((account_id, email))

        if to_start:
            _ensure_cpa_worker_locked()
            if not cpa_queue_state.get("active"):
                cpa_cancel_event.clear()
                cpa_queue_state.update({
                    "active": True,
                    "cancel_requested": False,
                    "mode": mode,
                    "total": 0,
                    "done": 0,
                    "success": 0,
                    "failed": 0,
                    "cancelled": 0,
                    "current_id": None,
                    "current_email": "",
                    "started_at": now_iso(),
                    "finished_at": "",
                    "message": "队列已启动",
                    "results": [],
                })
            else:
                # User explicitly queued more work: clear prior cancel so new items run
                if cpa_cancel_event.is_set() or cpa_queue_state.get("cancel_requested"):
                    cpa_cancel_event.clear()
                    cpa_queue_state["cancel_requested"] = False
                    cpa_queue_state["message"] = (
                        f"已取消停止并追加 {len(to_start)} 个账号（当前账号完成后继续）"
                    )
                else:
                    cpa_queue_state["message"] = f"已向运行中队列追加 {len(to_start)} 个账号"

            cpa_queue_state["total"] = int(cpa_queue_state.get("total") or 0) + len(to_start)
            cpa_queue_state["mode"] = mode
            worker = cpa_worker_thread
            for account_id, email in to_start:
                execute_no_return(
                    "UPDATE accounts SET cpa_status = ?, cpa_error = '', cpa_updated_at = ? WHERE id = ?",
                    ("queued", now_iso(), account_id),
                )
                append_account_cpa_log(
                    account_id,
                    f"已加入全局 CPA 队列（mode={mode}，单线程单浏览器）",
                )
                if worker is not None:
                    cpa_jobs[account_id] = worker
                cpa_work_queue.put({"account_id": account_id, "mode": mode})
                accepted.append({"id": account_id, "email": email, "status": "queued"})

    return {
        "ok": True,
        "mode": mode,
        "worker": "global_single_thread_shared_browser",
        "accepted": accepted,
        "skipped": skipped,
        "rejected": rejected,
        "accepted_count": len(accepted),
        "skipped_count": len(skipped),
        "rejected_count": len(rejected),
        "queue": _cpa_queue_snapshot(),
    }



def run_account_cpa_batch(account_ids: list[int], mode: str) -> None:
    """Backward-compatible entry: enqueue then rely on global worker."""
    enqueue_cpa_jobs(account_ids, mode)




def build_accounts_where_clause(
    task_id: int | None,
    search: str,
    cpa_status: str | None = None,
    token_status: str | None = None,
    sso_alive: str | None = None,
) -> tuple[str, list[Any]]:
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

    cpa = (cpa_status or "").strip().lower()
    if cpa and cpa != "all":
        clauses.append("LOWER(COALESCE(cpa_status, '')) = ?")
        params.append(cpa)

    tok = (token_status or "").strip().lower()
    if tok and tok != "all":
        clauses.append("LOWER(COALESCE(token_status, '')) = ?")
        params.append(tok)

    sso = (sso_alive or "").strip().lower()
    if sso and sso != "all":
        if sso in {"1", "true", "alive", "yes"}:
            clauses.append("sso_alive = 1")
        elif sso in {"0", "false", "dead", "no"}:
            clauses.append("sso_alive = 0")
        elif sso in {"unknown", "null", "none", "unset"}:
            clauses.append("sso_alive IS NULL")

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
        source_dir = (
            CPA_WORKER_DIR
            if file_name in {"cpa_export.py", "cpa_to_sub2api.py"}
            else REGISTER_RUNNER_DIR
        )
        shutil.copy2(source_dir / file_name, task_dir / file_name)
    for dir_name in PROJECT_DIRS:
        if dir_name in {"cpa_xai", "health_check"}:
            src = CPA_WORKER_DIR / dir_name
        else:
            src = SOURCE_PROJECT / dir_name
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
        sub2_key = str(read_settings().get("sub2api_api_key") or "").strip()
        if sub2_key:
            child_env["SUB2API_API_KEY"] = sub2_key

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
        "UPDATE accounts SET cpa_status = ?, cpa_error = ?, cpa_updated_at = ? WHERE cpa_status IN (?, ?, ?)",
        ("failed", "Console restarted while CPA task was running; retry the operation.", now_iso(), "running", "uploading", "queued"),
    )
    sync_all_account_records()
    supervisor.start()
    ensure_cpa_worker()
    try:
        yield
    finally:
        cpa_cancel_event.set()
        try:
            _shutdown_shared_mint_browser()
        except Exception:
            pass
        supervisor.stop()


app = FastAPI(title="Grok Register Console", lifespan=lifespan)
class _NoCacheStaticFiles(StaticFiles):
    async def get_response(self, path: str, scope):  # type: ignore[override]
        response = await super().get_response(path, scope)
        # Always revalidate console assets so syntax fixes apply immediately.
        if path.endswith((".js", ".css")):
            response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
        ctype = str(response.headers.get("content-type") or "")
        if path.endswith(".js") and "charset" not in ctype.lower():
            response.headers["content-type"] = "application/javascript; charset=utf-8"
        elif path.endswith(".css") and "charset" not in ctype.lower():
            response.headers["content-type"] = "text/css; charset=utf-8"
        return response


app.mount("/static", _NoCacheStaticFiles(directory=str(APP_DIR / "static")), name="static")


class LoginPayload(BaseModel):
    username: str = ""
    password: str = ""


_AUTH_PUBLIC_PATHS = {
    "/login",
    "/api/auth/login",
    "/api/auth/status",
}


def _is_auth_public_path(path: str) -> bool:
    if path.startswith("/static"):
        return True
    return path in _AUTH_PUBLIC_PATHS


@app.middleware("http")
async def require_console_auth(request: Request, call_next):
    """Protect pages and APIs when AUTH_PASSWORD is configured."""
    if not AUTH_ENABLED:
        return await call_next(request)
    path = request.url.path or "/"
    if _is_auth_public_path(path):
        return await call_next(request)
    user = request.session.get("auth_user")
    if user:
        return await call_next(request)
    if path.startswith("/api/"):
        return JSONResponse({"detail": "未登录或登录已过期"}, status_code=401)
    return RedirectResponse(url="/login", status_code=302)


# SessionMiddleware must be outermost so request.session is available in auth middleware
app.add_middleware(
    SessionMiddleware,
    secret_key=AUTH_SESSION_SECRET,
    max_age=AUTH_SESSION_MAX_AGE,
    same_site="lax",
    https_only=False,
)


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request) -> HTMLResponse:
    if AUTH_ENABLED and request.session.get("auth_user"):
        return RedirectResponse(url="/", status_code=302)
    if not AUTH_ENABLED:
        return RedirectResponse(url="/", status_code=302)
    return TEMPLATES.TemplateResponse(
        request,
        "login.html",
        {"request": request, "auth_enabled": AUTH_ENABLED},
    )


@app.get("/api/auth/status")
def auth_status(request: Request) -> dict[str, Any]:
    user = str(request.session.get("auth_user") or "")
    return {
        "auth_enabled": AUTH_ENABLED,
        "authenticated": (not AUTH_ENABLED) or bool(user),
        "username": user if AUTH_ENABLED else "",
    }


@app.post("/api/auth/login")
def auth_login(payload: LoginPayload, request: Request) -> dict[str, Any]:
    if not AUTH_ENABLED:
        return {"ok": True, "auth_enabled": False, "username": ""}
    username = str(payload.username or "").strip()
    password = str(payload.password or "")
    user_ok = hmac.compare_digest(username, AUTH_USER)
    pass_ok = hmac.compare_digest(password, AUTH_PASSWORD)
    if not (user_ok and pass_ok):
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    request.session["auth_user"] = AUTH_USER
    return {"ok": True, "auth_enabled": True, "username": AUTH_USER}


@app.post("/api/auth/logout")
def auth_logout(request: Request) -> dict[str, Any]:
    request.session.clear()
    return {"ok": True}




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
        "settings": {key: value for key, value in read_settings().items() if key not in {"cpa_cloud_management_key", "sub2api_api_key"}},
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
    return {"settings": {key: value for key, value in read_settings().items() if key not in {"cpa_cloud_management_key", "sub2api_api_key"}}, "defaults": public_defaults()}


@app.post("/api/settings")
def save_settings(payload: SystemSettings) -> dict[str, Any]:
    saved = write_settings(payload)
    return {"settings": {key: value for key, value in saved.items() if key not in {"cpa_cloud_management_key", "sub2api_api_key"}}, "defaults": public_defaults()}



@app.get("/api/email-domains")
def get_email_domains() -> dict[str, Any]:
    defaults = merged_defaults()
    return {
        "active": _parse_domain_list_value(defaults.get("temp_mail_domain")),
        "removed": _parse_domain_list_value(defaults.get("temp_mail_domains_removed")),
        "threshold": int(defaults.get("domain_auth_fail_threshold") or 3),
        "auto_remove": bool(defaults.get("domain_auth_fail_auto_remove", True)),
        "stats": list_email_domain_stats(),
    }


@app.post("/api/email-domains/restore")
def restore_email_domain(payload: dict[str, Any]) -> dict[str, Any]:
    raw = str(payload.get("domain") or "").strip().lstrip("@").lower()
    domain = _extract_email_domain("user@" + raw) if raw and "@" not in raw else _extract_email_domain(raw)
    if not domain:
        domain = raw
    if not domain or "." not in domain:
        raise HTTPException(status_code=400, detail="domain required")

    current = dict(read_settings() or {})
    merged = merged_defaults()
    for key in (
        "temp_mail_domain",
        "temp_mail_domains_removed",
        "domain_auth_fail_threshold",
        "domain_auth_fail_auto_remove",
        "temp_mail_api_base",
        "temp_mail_admin_password",
        "temp_mail_site_password",
        "proxy",
        "browser_proxy",
    ):
        if key not in current and key in merged:
            current[key] = merged.get(key)

    active = _parse_domain_list_value(current.get("temp_mail_domain"))
    removed = _parse_domain_list_value(current.get("temp_mail_domains_removed"))
    if domain not in active:
        active.append(domain)
    removed = [d for d in removed if d != domain]
    current["temp_mail_domain"] = _join_domain_list(active)
    current["temp_mail_domains_removed"] = _join_domain_list(removed)
    _save_settings_dict(current)
    execute_no_return(
        """
        UPDATE email_domain_stats
        SET status = 'active', fail_count = 0, disabled_at = NULL, updated_at = ?
        WHERE domain = ?
        """,
        (now_iso(), domain),
    )
    return {
        "ok": True,
        "domain": domain,
        "active": active,
        "removed": removed,
    }



def _heal_stale_busy_accounts() -> int:
    """Clear cpa_status queued/running/uploading when account is no longer in global queue.

    Prevents UI action buttons from staying disabled after probe/refresh finished
    without writing a terminal cpa_status (historical bug).
    """
    try:
        with cpa_jobs_lock:
            active_ids = set(cpa_jobs.keys())
            current_id = cpa_queue_state.get("current_id")
            if current_id is not None:
                active_ids.add(int(current_id))
        rows = fetch_all(
            "SELECT id, cpa_status, cpa_path, token_status FROM accounts "
            "WHERE cpa_status IN ('queued', 'running', 'uploading')"
        )
        healed = 0
        for row in rows or []:
            account_id = int(row["id"])
            if account_id in active_ids:
                continue
            path = str(row_get(row, "cpa_path", "") or "").strip()
            token_status = str(row_get(row, "token_status", "") or "").strip().lower()
            if token_status in {
                "dead", "sso_dead", "api_dead", "error",
                "refresh_failed", "refresh_invalid", "oauth_failed",
            }:
                new_status = "invalid"
            elif path:
                new_status = "generated"
            else:
                new_status = "not_started"
            execute_no_return(
                "UPDATE accounts SET cpa_status = ?, cpa_error = CASE "
                "WHEN ? = 'invalid' AND (cpa_error IS NULL OR cpa_error = '') "
                "THEN ? ELSE cpa_error END, cpa_updated_at = ? WHERE id = ?",
                (
                    new_status,
                    new_status,
                    f"auto-heal: stale {row_get(row, 'cpa_status', '')} cleared (token={token_status or 'unknown'})",
                    now_iso(),
                    account_id,
                ),
            )
            healed += 1
        if healed:
            print(f"[accounts] healed {healed} stale busy cpa_status rows", flush=True)
        return healed
    except Exception as exc:  # noqa: BLE001
        print(f"[accounts] heal stale busy failed: {exc}", flush=True)
        return 0


@app.get("/api/accounts")
def list_accounts(
    task_id: int | None = Query(None, ge=1),
    search: str = Query(""),
    cpa_status: str = Query(""),
    token_status: str = Query(""),
    sso_alive: str = Query(""),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
) -> dict[str, Any]:
    sync_all_account_records()
    _heal_stale_busy_accounts()
    where_clause, where_params = build_accounts_where_clause(
        task_id, search, cpa_status=cpa_status, token_status=token_status, sso_alive=sso_alive
    )
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


class AccountCpaBatch(BaseModel):
    account_ids: list[int] = Field(default_factory=list)
    mode: str = "authorize_and_push"


class AccountIdsPayload(BaseModel):
    account_ids: list[int] = Field(default_factory=list)


@app.get("/api/accounts/ids")
def list_account_ids(
    task_id: int | None = Query(None, ge=1),
    search: str = Query(""),
    cpa_status: str = Query(""),
    token_status: str = Query(""),
    sso_alive: str = Query(""),
) -> dict[str, Any]:
    """Return all account ids matching current filters (for cross-page select-all)."""
    sync_all_account_records()
    where_clause, where_params = build_accounts_where_clause(
        task_id, search, cpa_status=cpa_status, token_status=token_status, sso_alive=sso_alive
    )
    rows = fetch_all(
        f"SELECT id FROM accounts{where_clause} ORDER BY created_at DESC, id DESC",
        tuple(where_params),
    )
    ids = [int(row["id"]) for row in rows]
    return {"ids": ids, "total": len(ids)}


@app.post("/api/accounts/by-ids")
def list_accounts_by_ids(payload: AccountIdsPayload) -> dict[str, Any]:
    """Fetch full account rows by ids (cross-page download/delete)."""
    sync_all_account_records()
    ordered_ids: list[int] = []
    seen: set[int] = set()
    for raw_id in payload.account_ids:
        account_id = int(raw_id)
        if account_id in seen:
            continue
        seen.add(account_id)
        ordered_ids.append(account_id)
    if not ordered_ids:
        return {"accounts": []}
    placeholders = ",".join("?" for _ in ordered_ids)
    rows = fetch_all(
        f"SELECT * FROM accounts WHERE id IN ({placeholders})",
        tuple(ordered_ids),
    )
    by_id = {int(row["id"]): row for row in rows}
    accounts = [
        serialize_account(by_id[account_id])
        for account_id in ordered_ids
        if account_id in by_id
    ]
    return {"accounts": accounts}


@app.post("/api/accounts/cpa/batch")
def batch_account_cpa(payload: AccountCpaBatch) -> dict[str, Any]:
    if not payload.account_ids:
        raise HTTPException(status_code=400, detail="请至少选择一个账号")
    return enqueue_cpa_jobs(payload.account_ids, payload.mode)


@app.get("/api/accounts/cpa/queue")
def get_cpa_queue_status() -> dict[str, Any]:
    return {"queue": _cpa_queue_snapshot()}


@app.post("/api/accounts/cpa/queue/cancel")
def cancel_cpa_queue() -> dict[str, Any]:
    with cpa_jobs_lock:
        if not cpa_queue_state.get("active") and cpa_work_queue.qsize() == 0:
            return {"ok": True, "message": "当前没有运行中的 CPA 队列", "queue": _cpa_queue_snapshot()}
        cpa_cancel_event.set()
        cpa_queue_state["cancel_requested"] = True
        cpa_queue_state["message"] = "正在停止：当前账号完成后取消剩余排队任务"
    return {"ok": True, "message": "已请求停止 CPA 队列", "queue": _cpa_queue_snapshot()}




class AccountExportPayload(BaseModel):
    account_ids: list[int] = Field(default_factory=list)


@app.post("/api/accounts/export")
def export_accounts_csv(payload: AccountExportPayload) -> Any:
    """Export selected accounts as plain text lines:
    email----password----sso
    """
    from fastapi.responses import Response

    ids = [int(x) for x in (payload.account_ids or []) if int(x) > 0]
    if not ids:
        raise HTTPException(status_code=400, detail="account_ids cannot be empty")
    rows: list[sqlite3.Row] = []
    for aid in ids:
        row = fetch_one("SELECT * FROM accounts WHERE id = ?", (aid,))
        if row is not None:
            rows.append(row)
    if not rows:
        raise HTTPException(status_code=404, detail="no accounts found")

    lines: list[str] = []
    for row in rows:
        email = str(row_get(row, "email", "") or "").strip()
        password = str(row_get(row, "password", "") or "").strip()
        sso = str(row_get(row, "sso", "") or "").strip()
        if not email and not sso:
            continue
        lines.append(f"{email}----{password}----{sso}")

    if not lines:
        raise HTTPException(status_code=404, detail="no valid account lines")

    content = "\n".join(lines) + "\n"
    filename = f"accounts_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
    return Response(
        content=content.encode("utf-8"),
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )



@app.get("/api/accounts/cpa/queue/export")
def export_cpa_queue_results() -> Any:
    from fastapi.responses import Response

    snap = _cpa_queue_snapshot()
    results = snap.get("results") or []
    lines = ["id,email,status,error,at"]
    for item in results:
        email = str(item.get("email") or "").replace('"', '""')
        error = str(item.get("error") or "").replace('"', '""').replace("\n", " ")
        lines.append(
            f'{item.get("id","")},"{email}",{item.get("status","")},"{error}",{item.get("at","")}'
        )
    content = "\n".join(lines) + "\n"
    filename = f"cpa_queue_results_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    return Response(
        content=content.encode("utf-8-sig"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )



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
    result = enqueue_cpa_jobs([account_id], "authorize_and_push")
    if result["accepted_count"] == 0:
        reason = ""
        if result["skipped"]:
            reason = result["skipped"][0].get("reason") or "CPA 任务已在队列或执行中"
        elif result["rejected"]:
            reason = result["rejected"][0].get("reason") or "无法加入队列"
        else:
            reason = "无法加入队列"
        raise HTTPException(status_code=409 if result["skipped"] else 400, detail=reason)
    return {
        "ok": True,
        "status": "queued",
        "account_id": account_id,
        "queue": result.get("queue"),
    }


@app.post("/api/accounts/{account_id}/cpa/upload")
def upload_existing_account_cpa(account_id: int) -> dict[str, Any]:
    row = account_row(account_id)
    if resolve_account_cpa_path(row) is None:
        raise HTTPException(status_code=400, detail="未找到已生成的 CPA 授权文件，请先生成授权")
    result = enqueue_cpa_jobs([account_id], "push_only")
    if result["accepted_count"] == 0:
        reason = ""
        if result["skipped"]:
            reason = result["skipped"][0].get("reason") or "CPA 任务已在队列或执行中"
        elif result["rejected"]:
            reason = result["rejected"][0].get("reason") or "无法加入队列"
        else:
            reason = "无法加入队列"
        raise HTTPException(status_code=409 if result["skipped"] else 400, detail=reason)
    return {
        "ok": True,
        "status": "queued",
        "account_id": account_id,
        "queue": result.get("queue"),
    }


@app.post("/api/accounts/{account_id}/cpa/sub2api")
def upload_existing_account_sub2api(account_id: int) -> dict[str, Any]:
    row = account_row(account_id)
    if resolve_account_cpa_path(row) is None:
        raise HTTPException(status_code=400, detail="未找到已生成的 CPA 授权文件，请先生成授权")
    result = enqueue_cpa_jobs([account_id], "push_sub2api")
    if result["accepted_count"] == 0:
        reason = ""
        if result["skipped"]:
            reason = result["skipped"][0].get("reason") or "Sub2API 任务已在队列或执行中"
        elif result["rejected"]:
            reason = result["rejected"][0].get("reason") or "无法加入队列"
        else:
            reason = "无法加入队列"
        raise HTTPException(status_code=409 if result["skipped"] else 400, detail=reason)
    return {
        "ok": True,
        "status": "queued",
        "account_id": account_id,
        "queue": result.get("queue"),
    }






class AccountMaintainBatch(BaseModel):
    account_ids: list[int] = Field(default_factory=list)
    mode: str = "probe_only"  # probe_only | refresh_only | oauth_only
    force: bool = True


@app.post("/api/accounts/{account_id}/probe")
def probe_account(account_id: int) -> dict[str, Any]:
    account_row(account_id)
    return run_account_token_probe(
        account_id,
        probe_api=True,
        probe_sso=True,
        auto_refresh=False,
        force_refresh=False,
    )


@app.post("/api/accounts/{account_id}/refresh")
def refresh_account_token(account_id: int, force: bool = Query(True)) -> dict[str, Any]:
    account_row(account_id)
    return run_account_token_refresh(account_id, force=force, allow_sso_fallback=True)


@app.post("/api/accounts/{account_id}/oauth")
def oauth_account(account_id: int) -> dict[str, Any]:
    account_row(account_id)
    # queue for serial processing when many; single call runs immediately if idle preferred:
    result = enqueue_cpa_jobs([account_id], "oauth_only")
    if result["accepted_count"] == 0:
        # if already busy with same account, still try direct? reject
        reason = ""
        if result["skipped"]:
            reason = result["skipped"][0].get("reason") or "任务已在队列或执行中"
        elif result["rejected"]:
            reason = result["rejected"][0].get("reason") or "请求被拒绝"
        else:
            reason = "未能入队"
        raise HTTPException(status_code=409 if result["skipped"] else 400, detail=reason)
    return {"ok": True, "status": "queued", "account_id": account_id, "queue": result.get("queue")}


@app.post("/api/accounts/maintain/batch")
def batch_account_maintain(payload: AccountMaintainBatch) -> dict[str, Any]:
    mode = (payload.mode or "probe_only").strip()
    if mode not in {"probe_only", "refresh_only", "oauth_only"}:
        raise HTTPException(status_code=400, detail="mode 仅支持 probe_only / refresh_only / oauth_only")
    ids = [int(x) for x in (payload.account_ids or []) if int(x) > 0]
    if not ids:
        raise HTTPException(status_code=400, detail="account_ids 不能为空")
    # probe/refresh can run concurrent-ish but reuse CPA queue for backpressure & UI progress
    return enqueue_cpa_jobs(ids, mode)


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
