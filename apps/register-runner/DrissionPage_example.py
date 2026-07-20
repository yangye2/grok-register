from DrissionPage import Chromium, ChromiumOptions
from DrissionPage.errors import PageDisconnectedError
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import shutil
import tempfile
import datetime
import logging
import time
import os
import random
import re
import secrets
import string
import sys
import sqlite3

from email_register import get_email_and_token, get_oai_code, cleanup_mailbox_if_needed


_ORIG_PRINT = print


def _stamp_message(message: object) -> str:
    """Prefix plain console lines with local time so console.log always shows when events happen."""
    text = str(message)
    if not text:
        return text
    # Already stamped: [YYYY-MM-DD HH:MM:SS] or logging-style "YYYY-MM-DD HH:MM:SS |"
    head = text.lstrip()
    if re.match(r"^\[\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}", head):
        return text
    if re.match(r"^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}", head):
        return text
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return f"[{ts}] {text}"


def print(*args, **kwargs):  # type: ignore[no-redef]
    """Drop-in print with timestamp prefix for task console readability."""
    if not args:
        return _ORIG_PRINT(*args, **kwargs)
    # Preserve multi-arg print semantics for non-first parts; stamp first rendered message.
    sep = kwargs.get("sep", " ")
    try:
        body = sep.join(str(a) for a in args)
    except Exception:
        body = " ".join(map(str, args))
    stamped = _stamp_message(body)
    # Re-print as single string so timestamp only appears once.
    return _ORIG_PRINT(stamped, **{k: v for k, v in kwargs.items() if k != "sep"})




def _console_db_path() -> str:
    """Resolve console.db path when launched by grok-register console."""
    explicit = str(os.environ.get("GROK_CONSOLE_DB") or "").strip()
    if explicit:
        return explicit
    runtime = str(os.environ.get("GROK_REGISTER_CONSOLE_RUNTIME") or "").strip()
    if runtime:
        return os.path.join(runtime, "console.db")
    return ""


def _console_task_context() -> tuple[int | None, str]:
    raw = str(os.environ.get("GROK_TASK_ID") or "").strip()
    if not raw:
        return None, ""
    try:
        task_id = int(raw)
    except ValueError:
        return None, ""
    task_name = str(os.environ.get("GROK_TASK_NAME") or "").strip() or f"task_{task_id}"
    return task_id, task_name


def _now_iso_local() -> str:
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _append_cpa_log_lines_conn(conn: sqlite3.Connection, account_id: int, messages: list[str]) -> None:
    """Append register-time log lines into accounts.cpa_log (dedupe by message tail)."""
    if not messages:
        return
    row = conn.execute("SELECT cpa_log FROM accounts WHERE id = ?", (account_id,)).fetchone()
    existing = str((row[0] if row else "") or "")
    lines = [x for x in existing.splitlines() if x.strip()]
    existing_blob = "\n".join(lines)
    for msg in messages:
        msg = str(msg or "").strip()
        if not msg:
            continue
        # strip leading timestamp for de-dupe comparison
        bare = msg
        if bare.startswith("[") and "]" in bare[:36]:
            head = bare[1 : bare.find("]")]
            if len(head) >= 19 and head[4:5] == "-" and head[7:8] == "-":
                bare = bare[bare.find("]") + 1 :].strip()
        if bare and bare in existing_blob:
            continue
        if not re.match(r"^\[(\*|Debug|Error|Warn(?:ing)?|Info|OK|Success|Fail(?:ed)?)\]", bare, re.I):
            low = bare.lower()
            if any(k in low for k in ("fail", "error", "exception", "traceback", "失败", "异常", "错误")):
                bare = f"[Error] {bare}"
            elif any(k in low for k in ("warn", "警告")):
                bare = f"[Warn] {bare}"
            else:
                bare = f"[Info] {bare}"
        stamped = f"[{_now_iso_local()}] {bare}"
        lines.append(stamped)
        existing_blob += "\n" + bare
    kept = lines[-400:]
    conn.execute(
        "UPDATE accounts SET cpa_log = ?, cpa_updated_at = ? WHERE id = ?",
        ("\n".join(kept), _now_iso_local(), account_id),
    )


def persist_account_to_console_db(account_record: dict, cpa_record: dict | None = None) -> int | None:
    """Write/update registered account into console SQLite immediately (source of truth).

    When GROK_TASK_ID / console db env are missing (standalone runner), this is a no-op.
    Returns account id when written, else None.
    """
    db_path = _console_db_path()
    task_id, task_name = _console_task_context()
    if not db_path or task_id is None:
        return None
    if not isinstance(account_record, dict):
        return None

    email = str(account_record.get("email") or "").strip()
    sso = str(account_record.get("sso") or "").strip()
    if not email or not sso:
        return None

    given_name = str(account_record.get("given_name") or "")
    family_name = str(account_record.get("family_name") or "")
    password = str(account_record.get("password") or "")
    created_at = str(account_record.get("created_at") or _now_iso_local())
    source_file = str(account_record.get("source_file") or os.environ.get("GROK_ACCOUNT_OUTPUT") or "")
    now = _now_iso_local()
    cpa_record = cpa_record if isinstance(cpa_record, dict) else {}

    try:
        conn = sqlite3.connect(db_path, timeout=60)
        conn.execute("PRAGMA busy_timeout=60000")
        try:
            conn.execute(
                """
                INSERT OR IGNORE INTO accounts (
                    task_id, task_name, email, sso, given_name, family_name, password,
                    source_file, created_at, imported_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task_id,
                    task_name,
                    email,
                    sso,
                    given_name,
                    family_name,
                    password,
                    source_file,
                    created_at,
                    now,
                ),
            )
            # Keep latest password/name if row already existed
            conn.execute(
                """
                UPDATE accounts
                SET task_name = ?,
                    given_name = CASE WHEN ? != '' THEN ? ELSE given_name END,
                    family_name = CASE WHEN ? != '' THEN ? ELSE family_name END,
                    password = CASE WHEN ? != '' THEN ? ELSE password END,
                    source_file = CASE WHEN ? != '' THEN ? ELSE source_file END
                WHERE task_id = ? AND email = ? AND sso = ?
                """,
                (
                    task_name,
                    given_name, given_name,
                    family_name, family_name,
                    password, password,
                    source_file, source_file,
                    task_id, email, sso,
                ),
            )

            row = conn.execute(
                "SELECT id FROM accounts WHERE task_id = ? AND email = ? AND sso = ?",
                (task_id, email, sso),
            ).fetchone()
            if not row:
                conn.commit()
                return None
            account_id = int(row[0])

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

                existing = conn.execute(
                    "SELECT cpa_status, cpa_uploaded_at FROM accounts WHERE id = ?",
                    (account_id,),
                ).fetchone()
                existing_status = str((existing[0] if existing else "not_started") or "not_started")
                can_update = (
                    existing_status in {"not_started", "queued", "running", "failed"}
                    or (existing_status == "generated" and cpa_status == "uploaded")
                    or (existing_status == "uploaded" and cpa_status == "uploaded")
                )
                if can_update:
                    uploaded_at = now if cpa_status == "uploaded" else str((existing[1] if existing else "") or "")
                    cpa_path = str(cpa_record.get("path") or cpa_record.get("cpa_path") or "")
                    conn.execute(
                        """
                        UPDATE accounts
                        SET cpa_status = ?, cpa_path = ?, cpa_uploaded_at = ?, cpa_error = ?, cpa_updated_at = ?
                        WHERE id = ?
                        """,
                        (cpa_status, cpa_path, uploaded_at, cpa_error, now, account_id),
                    )

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
                    if sso_alive_raw is True or sso_alive_raw == 1 or sso_alive_raw == "1":
                        sso_alive_i = 1
                    elif sso_alive_raw is False or sso_alive_raw == 0 or sso_alive_raw == "0":
                        sso_alive_i = 0
                    else:
                        sso_alive_i = None
                    renew_src = str(cpa_record.get("mode") or "").strip()
                    if token_status or sso_alive_i is not None or renew_src:
                        conn.execute(
                            """
                            UPDATE accounts
                            SET token_status = CASE WHEN ? != '' THEN ? ELSE token_status END,
                                token_checked_at = CASE WHEN ? != '' THEN ? ELSE token_checked_at END,
                                token_error = CASE WHEN ? != '' THEN ? ELSE token_error END,
                                sso_alive = COALESCE(?, sso_alive),
                                last_renew_source = CASE WHEN ? != '' THEN ? ELSE last_renew_source END,
                                last_renew_at = CASE WHEN ? != '' THEN ? ELSE last_renew_at END
                            WHERE id = ?
                            """,
                            (
                                token_status, token_status,
                                token_status, now,
                                cpa_error, cpa_error,
                                sso_alive_i,
                                renew_src, renew_src,
                                renew_src, now,
                                account_id,
                            ),
                        )

                log_lines: list[str] = []
                raw_lines = cpa_record.get("log_lines")
                if isinstance(raw_lines, list):
                    log_lines.extend(str(x).strip() for x in raw_lines if str(x).strip())
                summary = (
                    f"[register-cpa] ok={bool(cpa_record.get('ok'))} "
                    f"mode={cpa_record.get('mode') or '-'} "
                    f"token={cpa_record.get('token_status') or '-'} "
                    f"sso_alive={cpa_record.get('sso_alive')} "
                    f"path={cpa_record.get('path') or cpa_record.get('cpa_path') or '-'}"
                )
                log_lines.append(summary)
                _append_cpa_log_lines_conn(conn, account_id, log_lines)

            conn.commit()
            return account_id
        finally:
            conn.close()
    except Exception as exc:
        print(f"[Warn] 写入控制台数据库失败(账号仍会写文件备份): {exc}", flush=True)
        return None


def load_cpa_config() -> dict:
    """Read optional CPA export settings from the current task config."""
    try:
        with open(os.path.join(os.path.dirname(__file__), "config.json"), "r", encoding="utf-8") as file:
            data = json.load(file)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def export_cpa_auth(email: str, password: str, sso_value: str) -> dict:
    """注册完成后的授权 + 测活。

    与账号管理对齐说明：
      - OAuth 成功 = 与「单独 OAuth」一致（export_cpa_xai_via_sso）
      - 测活 = 与「单独测活」同一 check_account_liveness
      - 测活失败时：先尝试一次 Token 续期（同「续期」refresh_cpa_auth_file），再复测
      - 授权文件已写出后，测活失败不再把整单 ok 打成 false（避免「测活失败但续期能成」被当成注册失败）
    可选：测活通过后再 cloud / sub2api 推送。
    """
    import time

    config = load_cpa_config()
    log_lines: list[str] = []

    def log(message: str) -> None:
        msg = str(message or "").strip()
        if msg:
            log_lines.append(msg)
        print(message, flush=True)

    # 任务结束后再统一续期/OAuth：注册阶段跳过即时授权
    if bool(config.get("cpa_post_task_oauth_enabled", False)) or bool(
        config.get("cpa_post_task_refresh_enabled", False)
    ):
        log("[cpa] post-task maintain mode: skip per-account OAuth during register")
        return {
            "ok": False,
            "skipped": True,
            "reason": "post_task_maintain",
            "log_lines": list(log_lines),
        }
    if not bool(config.get("cpa_export_enabled", False)):
        return {"ok": False, "skipped": True, "reason": "disabled", "log_lines": list(log_lines)}

    sso_val = (sso_value or "").strip()
    email = (email or "").strip()
    password = password or ""  # kept for signature compatibility; OAuth path does not use it
    _ = password

    cfg = dict(config)
    cfg.setdefault("cpa_prefer_sso_oauth", True)
    try:
        probe_delay = float(cfg.get("cpa_probe_delay_sec", 5) or 5)
    except (TypeError, ValueError):
        probe_delay = 5.0
    probe_delay = max(0.0, min(120.0, probe_delay))
    do_probe = bool(cfg.get("cpa_probe_after_write", True))

    result: dict = {"ok": False, "email": email, "mode": ""}

    try:
        import cpa_export
    except Exception as exc:
        log(f"[cpa] import cpa_export failed: {exc}")
        return {"ok": False, "error": f"import cpa_export: {exc}", "log_lines": list(log_lines)}

    # ---- 1) 单独 OAuth（同 run_account_sso_oauth）----
    if not sso_val:
        log("[cpa-oauth] missing SSO cookie, skip OAuth (same as account OAuth)")
        return {"ok": False, "email": email, "error": "missing sso", "mode": "sso_oauth", "log_lines": list(log_lines)}
    if not email:
        log("[cpa-oauth] missing email, skip OAuth")
        return {"ok": False, "error": "missing email", "mode": "sso_oauth", "log_lines": list(log_lines)}

    log("[cpa-oauth] start SSO OAuth (same as account single OAuth)")
    # 单独 OAuth 不做内置 models probe；测活由下一步统一 check_account_liveness 完成
    oauth_cfg = {**cfg, "cpa_probe_after_write": False, "cpa_prefer_sso_oauth": True}
    try:
        if hasattr(cpa_export, "export_cpa_xai_via_sso"):
            oauth_result = cpa_export.export_cpa_xai_via_sso(
                email,
                sso_val,
                config=oauth_cfg,
                log_callback=lambda m: log(f"[cpa-oauth] {m}"),
            )
        else:
            oauth_result = cpa_export.export_cpa_xai_for_account(
                email,
                "unused",
                page=None,
                sso=sso_val,
                config=oauth_cfg,
                log_callback=lambda m: log(f"[cpa-oauth] {m}"),
            )
    except Exception as exc:
        log(f"[cpa-oauth] exception: {exc}")
        return {"ok": False, "email": email, "error": str(exc), "mode": "sso_oauth", "log_lines": list(log_lines)}

    if not oauth_result.get("ok") or not (oauth_result.get("path") or oauth_result.get("cpa_path")):
        err = oauth_result.get("error") or "sso_oauth_failed"
        log(f"[cpa-oauth] failed: {err}")
        return {
            "ok": False,
            "email": email,
            "error": err,
            "mode": oauth_result.get("mode") or "sso_oauth",
            "oauth": oauth_result,
            "log_lines": list(log_lines),
        }

    result = dict(oauth_result)
    result["mode"] = result.get("mode") or "sso_oauth"
    auth_path = str(result.get("path") or result.get("cpa_path") or "").strip()
    result["path"] = auth_path
    result["cpa_path"] = auth_path
    result["ok"] = True
    log(f"[cpa-oauth] success path={auth_path}")

    # ---- 2) 延时 + 测活（同账号测活；失败时补一轮续期再测，对齐「续期能成功」场景）----
    if do_probe and auth_path:
        if probe_delay > 0:
            log(f"[cpa-probe] wait {probe_delay:.1f}s before liveness (cpa_probe_delay_sec)")
            time.sleep(probe_delay)
        try:
            from cpa_xai.token_maintain import (  # type: ignore
                check_account_liveness,
                refresh_cpa_auth_file,
            )

            proxy = (
                str(
                    cfg.get("cpa_proxy")
                    or cfg.get("browser_proxy")
                    or cfg.get("proxy")
                    or ""
                ).strip()
                or None
            )
            skew = float(cfg.get("cpa_token_refresh_skew_sec", 300) or 300)
            model = str(cfg.get("cpa_health_check_model") or "grok-4.5")
            timeout = float(cfg.get("cpa_health_check_timeout", 15) or 15)

            def _run_probe(*, auto_refresh: bool, force_refresh: bool, tag: str) -> dict:
                return check_account_liveness(
                    auth_path=auth_path,
                    sso=sso_val or None,
                    proxy=proxy,
                    probe_api=True,
                    probe_sso=bool(sso_val),
                    auto_refresh=auto_refresh,
                    force_refresh=force_refresh,
                    skew_seconds=skew,
                    model=model,
                    timeout=timeout,
                    log=lambda m: log(f"[cpa-probe{tag}] {m}"),
                )

            # 与账号单独测活一致：默认不强制续期；失败后再走「续期」逻辑
            live = _run_probe(auto_refresh=False, force_refresh=False, tag="")
            if not live.get("alive"):
                log("[cpa-probe] first liveness failed; try one refresh (same as account 续期) then re-probe")
                try:
                    ref = refresh_cpa_auth_file(
                        auth_path,
                        proxy=proxy,
                        skew_seconds=skew,
                        force=True,
                        sso=sso_val or None,
                        allow_sso_fallback=bool(sso_val),
                        persist=True,
                        log=lambda m: log(f"[cpa-refresh] {m}"),
                    )
                    result["refresh_retry"] = {
                        k: v for k, v in (ref or {}).items() if k != "auth"
                    }
                    if ref.get("path"):
                        auth_path = str(ref.get("path") or auth_path)
                        result["path"] = auth_path
                        result["cpa_path"] = auth_path
                    log(
                        f"[cpa-refresh] ok={ref.get('ok')} renewed={ref.get('renewed')} "
                        f"source={ref.get('source')} err={ref.get('error') or ''}"
                    )
                except Exception as ref_exc:
                    log(f"[cpa-refresh] exception: {ref_exc}")
                    result["refresh_retry_error"] = str(ref_exc)
                # 稍等再测，避免刚 mint/refresh 立刻 chat 失败
                extra_wait = min(3.0, max(0.0, probe_delay))
                if extra_wait > 0:
                    log(f"[cpa-probe] wait {extra_wait:.1f}s after refresh before re-probe")
                    time.sleep(extra_wait)
                live = _run_probe(auto_refresh=False, force_refresh=False, tag="-retry")

            sso_res = live.get("sso") if isinstance(live.get("sso"), dict) else {}
            health = live.get("health") if isinstance(live.get("health"), dict) else {}

            if live.get("alive"):
                token_status = "alive"
            elif sso_res.get("alive") is False and not auth_path:
                token_status = "sso_dead"
            elif health and health.get("ok") is False:
                token_status = "api_dead"
            elif sso_res.get("alive") is False:
                token_status = "sso_dead"
            else:
                token_status = "dead" if live.get("ok") is False else "unknown"

            result["liveness"] = {k: v for k, v in (live or {}).items() if k != "auth"}
            result["token_status"] = token_status
            result["sso_alive"] = sso_res.get("alive")
            result["alive"] = bool(live.get("alive"))

            if not live.get("alive"):
                # 授权文件已存在：不把整单 ok 打 false（与「账号里续期还能成功」一致）
                result["probe_ok"] = False
                result["error"] = (
                    live.get("error")
                    or health.get("message")
                    or "liveness_failed"
                )
                # 仅当配置强制要求测活才整单失败
                if bool(cfg.get("cpa_probe_required", False)):
                    result["ok"] = False
                log(
                    f"[cpa-probe] FAIL status={token_status} "
                    f"alive={live.get('alive')} sso={sso_res.get('alive')} "
                    f"health={health.get('ok')} err={result.get('error')} "
                    f"(auth file kept, ok={result.get('ok')})"
                )
            else:
                result["probe_ok"] = True
                log(
                    f"[cpa-probe] PASS status=alive sso={sso_res.get('alive')} "
                    f"health={health.get('ok')}"
                )
        except Exception as exc:
            log(f"[cpa-probe] liveness exception: {exc}")
            result["liveness_error"] = str(exc)
            result["token_status"] = "error"
            result["probe_ok"] = False
            if bool(cfg.get("cpa_probe_required", False)):
                result["ok"] = False
                result["error"] = f"liveness: {exc}"

    # ---- 3) 可选推送：注册后自动 CPA / Sub2（与账号管理批量推送开关分离）----
    if result.get("ok") and auth_path:
        push_path = str(result.get("cpa_path") or result.get("hotload_path") or auth_path)
        # 注册后自动推 CPA：cpa_register_push_enabled（兼容旧 cpa_cloud_upload_enabled）
        reg_cpa_push = bool(cfg.get("cpa_register_push_enabled", cfg.get("cpa_cloud_upload_enabled", False)))
        reg_sub2_push = bool(cfg.get("sub2api_register_push_enabled", cfg.get("sub2api_upload_enabled", False)))
        push_cfg = dict(cfg)
        push_cfg["cpa_cloud_upload_enabled"] = reg_cpa_push and bool(cfg.get("cpa_cloud_upload_enabled", True))
        push_cfg["sub2api_upload_enabled"] = reg_sub2_push and bool(cfg.get("sub2api_upload_enabled", True))
        try:
            cloud = cpa_export.upload_cpa_auth_to_cloud(push_path, push_cfg, log)
            result["cloud_cpa_upload"] = cloud
            if isinstance(cloud, dict) and cloud.get("ok"):
                result["cloud_uploaded"] = True
                log("[cpa] cloud upload ok")
            elif isinstance(cloud, dict) and cloud.get("health_failed"):
                result["ok"] = False
                result["error"] = (
                    cloud.get("error") or cloud.get("message") or "health_failed_before_upload"
                )
                log(f"[cpa] cloud upload blocked by health: {result['error']}")
        except Exception as exc:
            log(f"[cpa] cloud upload exception: {exc}")
            result["cloud_cpa_upload"] = {"ok": False, "error": str(exc)}

        if bool(push_cfg.get("sub2api_upload_enabled", False)) or bool(
            push_cfg.get("sub2api_export_enabled", False)
        ):
            try:
                sub_mod = cpa_export._import_cpa_to_sub2api()
                sub_res = sub_mod.export_after_cpa_result(
                    result, config=push_cfg, log_callback=log
                )
                result["sub2api"] = sub_res
            except Exception as exc:
                log(f"[sub2api] export failed: {exc}")
                result["sub2api_error"] = str(exc)

    result["log_lines"] = list(log_lines)
    return result


def setup_run_logger() -> logging.Logger:
    log_dir = os.path.join(os.path.dirname(__file__), "logs")
    os.makedirs(log_dir, exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = os.path.join(log_dir, f"run_{ts}.log")

    logger = logging.getLogger("grok_register")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()

    fmt = logging.Formatter("%(asctime)s | %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    fh = logging.FileHandler(log_path, encoding="utf-8")
    fh.setFormatter(fmt)
    logger.addHandler(fh)
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    logger.addHandler(sh)

    logger.info("日志文件: %s", log_path)
    return logger


run_logger: logging.Logger = None



def ensure_stable_python_runtime():
    # 优先自动切到更稳定的 3.12 / 3.13，避免 3.14 下 Mail.tm 偶发 TLS/兼容问题。
    if sys.version_info < (3, 14) or os.environ.get("DPE_REEXEC_DONE") == "1":
        return

    local_app_data = os.environ.get("LOCALAPPDATA", "")
    candidates = [
        os.path.join(local_app_data, "Programs", "Python", "Python312", "python.exe"),
        os.path.join(local_app_data, "Programs", "Python", "Python313", "python.exe"),
    ]

    current_python = os.path.normcase(os.path.abspath(sys.executable))
    for candidate in candidates:
        if not os.path.isfile(candidate):
            continue
        if os.path.normcase(os.path.abspath(candidate)) == current_python:
            return

        print(f"[*] 检测到 Python {sys.version.split()[0]}，自动切换到更稳定的解释器: {candidate}")
        env = os.environ.copy()
        env["DPE_REEXEC_DONE"] = "1"
        os.execve(candidate, [candidate, os.path.abspath(__file__), *sys.argv[1:]], env)


def warn_runtime_compatibility():
    # 中文提示：避免把底层 TLS 兼容问题误判成脚本逻辑错误。
    if sys.version_info >= (3, 14):
        print("[提示] 当前 Python 为 3.14+；若出现 Mail.tm TLS 异常，建议改用 Python 3.12 或 3.13。")


ensure_stable_python_runtime()
warn_runtime_compatibility()

_headless_browser = os.environ.get("GROK_REGISTER_HEADLESS", "0").lower() in ("1", "true", "yes", "on")

# 无头模式直接使用 Chrome/Chromium headless；非无头模式才自动启用 Xvfb 虚拟显示器。
_virtual_display = None
if not _headless_browser and (not os.environ.get("DISPLAY") or os.environ.get("USE_XVFB") == "1"):
    try:
        from pyvirtualdisplay import Display
        _virtual_display = Display(visible=0, size=(1920, 1080))
        _virtual_display.start()
        print(f"[*] Xvfb 虚拟显示器已启动: {os.environ.get('DISPLAY')}")
    except Exception as e:
        print(f"[Warn] Xvfb 启动失败: {e}，将尝试直接运行")

co = ChromiumOptions()
co.auto_port()
co.set_argument("--no-sandbox")
co.set_argument("--disable-gpu")
co.set_argument("--disable-dev-shm-usage")
co.set_argument("--disable-software-rasterizer")
if _headless_browser or not os.environ.get("DISPLAY"):
    co.set_argument("--headless=new")

# 从 config.json 读取代理配置给浏览器
_browser_proxy = ""
try:
    import json as _json_mod
    _cfg_path = os.path.join(os.path.dirname(__file__), "config.json")
    if os.path.isfile(_cfg_path):
        with open(_cfg_path, "r") as _f:
            _cfg = _json_mod.load(_f)
        _browser_proxy = str(_cfg.get("browser_proxy", "") or _cfg.get("proxy", "") or "")
except Exception:
    pass
if _browser_proxy:
    co.set_proxy(_browser_proxy)
    print(f"[*] 浏览器代理: {_browser_proxy}")

# Linux 服务器自动检测 chromium 路径
import platform
import shutil
import glob as _glob_mod
_linux_browser_path = ""
if platform.system() == "Linux":
    # 优先用 playwright 装的 chromium（无 AppArmor 限制）
    _pw_chromes = _glob_mod.glob(os.path.expanduser("~/.cache/ms-playwright/chromium-*/chrome-linux*/chrome"))
    if _pw_chromes:
        _linux_browser_path = _pw_chromes[0]
        co.set_browser_path(_linux_browser_path)
    else:
        for _candidate in ["/usr/bin/chromium-browser", "/usr/bin/chromium", "/usr/bin/google-chrome"]:
            if os.path.isfile(_candidate):
                _linux_browser_path = _candidate
                co.set_browser_path(_linux_browser_path)
                break
    # user_data_path 在 start_browser() 每轮动态设置，此处不固定

co.set_timeouts(base=1)

# 加载修复 MouseEvent.screenX / screenY 的扩展。
EXTENSION_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "turnstilePatch"))
co.add_extension(EXTENSION_PATH)

_chrome_temp_dir: str = ""
browser = None
page = None

SIGNUP_URL = "https://accounts.x.ai/sign-up?redirect=grok-com"

_sso_dir = os.path.join(os.path.dirname(__file__), "sso")
_sso_ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
DEFAULT_SSO_FILE = os.path.join(_sso_dir, f"sso_{_sso_ts}.txt")
_account_dir = os.path.join(os.path.dirname(__file__), "accounts")
DEFAULT_ACCOUNT_FILE = os.path.join(_account_dir, f"accounts_{_sso_ts}.jsonl")


def start_browser():
    # 每轮从全新浏览器开始，使用独立临时 profile 目录避免 Cookie/Session 复用。
    global browser, page, _chrome_temp_dir
    if platform.system() == "Linux" and not _linux_browser_path:
        raise RuntimeError(
            "未找到 Chrome/Chromium。请先安装浏览器后再运行。"
            "宿主机至少需要安装以下依赖："
            "`pip install -r requirements.txt`、`apt install xvfb`、"
            "`apt install chromium-browser` 或 `apt install google-chrome-stable`。"
        )
    _chrome_temp_dir = tempfile.mkdtemp(prefix="chrome_run_")
    co.set_user_data_path(_chrome_temp_dir)
    browser = Chromium(co)
    tabs = browser.get_tabs()
    page = tabs[-1] if tabs else browser.new_tab()
    return browser, page


def stop_browser():
    # 完整关闭整个浏览器实例，并清理本轮临时 profile，供下一轮重新拉起。
    global browser, page, _chrome_temp_dir
    if browser is not None:
        try:
            browser.quit()
        except Exception:
            pass
    browser = None
    page = None
    if _chrome_temp_dir and os.path.isdir(_chrome_temp_dir):
        shutil.rmtree(_chrome_temp_dir, ignore_errors=True)
    _chrome_temp_dir = ""


def restart_browser():
    # 清除 cookie/storage 代替完整重启，节省 Chrome 冷启动时间。
    global browser, page
    if browser is None:
        start_browser()
        return
    try:
        tabs = browser.get_tabs()
        page = tabs[-1] if tabs else browser.new_tab()
        page.run_js("window.localStorage.clear(); window.sessionStorage.clear();")
        page.clear_cache(session_storage=True, cookies=True)
    except Exception:
        stop_browser()
        start_browser()


def refresh_active_page():
    # 验证码确认后页面会跳转，旧 page 句柄可能断开，这里统一重新获取当前活动标签页。
    global browser, page
    if browser is None:
        start_browser()
    try:
        tabs = browser.get_tabs()
        if tabs:
            page = tabs[-1]
        else:
            page = browser.new_tab()
    except Exception:
        restart_browser()
    return page


def open_signup_page():
    # 每轮开始时打开注册页，并切到“使用邮箱注册”流程。
    global page
    refresh_active_page()
    try:
        page.get(SIGNUP_URL)
    except Exception:
        refresh_active_page()
        page = browser.new_tab(SIGNUP_URL)
    click_email_signup_button()


def close_current_page():
    # 兼容旧调用名，实际行为改为整轮重启浏览器。
    restart_browser()


def has_profile_form():
    """Detect xAI final signup profile page (name + password)."""
    refresh_active_page()
    try:
        return bool(page.run_js(
            r"""
function isVisible(node) {
    if (!node) return false;
    const style = window.getComputedStyle(node);
    if (style.display === 'none' || style.visibility === 'hidden' || Number(style.opacity) === 0) {
        return false;
    }
    const rect = node.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
}

const givenInput = document.querySelector(
    'input[data-testid="givenName"], input[name="givenName"], input[autocomplete="given-name"]'
);
const familyInput = document.querySelector(
    'input[data-testid="familyName"], input[name="familyName"], input[autocomplete="family-name"]'
);
const passwordInput = document.querySelector(
    'input[data-testid="password"], input[name="password"], input[type="password"]'
);

if (givenInput && familyInput && passwordInput) return true;
if ((givenInput || familyInput) && passwordInput) return true;

const buttons = Array.from(document.querySelectorAll('button')).filter(isVisible);
const complete = buttons.some((btn) => {
    const t = String(btn.innerText || btn.textContent || '').replace(/\s+/g, ' ').trim().toLowerCase();
    return (
        t.includes('complete sign up')
        || t.includes('complete signup')
        || t === 'complete'
        || t.includes('create account')
    );
});
if (complete && (givenInput || passwordInput)) return true;
return false;
            """
        ))
    except Exception:
        return False


def on_signup_profile_step():
    """True when signup has moved past OTP into profile form."""
    if has_profile_form():
        return True
    try:
        refresh_active_page()
        if page is None:
            return False
        return bool(page.run_js(
            r"""
return !!(
  document.querySelector('input[name="givenName"], input[data-testid="givenName"]')
  && document.querySelector('input[type="password"], input[name="password"]')
);
            """
        ))
    except Exception:
        return False

def click_email_signup_button(timeout=10):
    # 页面打开后，自动点击“使用邮箱注册”按钮。
    deadline = time.time() + timeout
    while time.time() < deadline:
        clicked = page.run_js(r"""
const candidates = Array.from(document.querySelectorAll('button, a, [role="button"]'));
const target = candidates.find((node) => {
    const text = (node.innerText || node.textContent || '').replace(/\s+/g, '').toLowerCase();
    return text.includes('使用邮箱注册') || text.includes('signupwithemail') || text.includes('signupemail') || text.includes('continuewith email') || text.includes('email');
});

if (!target) {
    return false;
}

target.click();
return true;
        """)

        if clicked:
            return True

        time.sleep(random.uniform(0.350, 0.675))

    raise Exception('未找到“使用邮箱注册”按钮')


def fill_email_and_submit(timeout=15):
    # 复用 `email_register.py` 里的邮箱获取逻辑，保留邮箱与 token 供后续验证码步骤继续使用。
    email, dev_token = get_email_and_token()
    if not email or not dev_token:
        raise Exception("获取邮箱失败")

    deadline = time.time() + timeout
    while time.time() < deadline:
        filled = page.run_js(
            """
const email = arguments[0];

function isVisible(node) {
    if (!node) {
        return false;
    }
    const style = window.getComputedStyle(node);
    if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') {
        return false;
    }
    const rect = node.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
}

const input = Array.from(document.querySelectorAll('input[data-testid="email"], input[name="email"], input[type="email"], input[autocomplete="email"]')).find((node) => {
    return isVisible(node) && !node.disabled && !node.readOnly;
}) || null;

if (!input) {
    return 'not-ready';
}

input.focus();
input.click();

// 不能只写 `input.value = xxx`，否则 React / 受控表单可能没有同步内部状态。
const valueSetter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set;
const tracker = input._valueTracker;
if (tracker) {
    tracker.setValue('');
}
if (valueSetter) {
    valueSetter.call(input, email);
} else {
    input.value = email;
}

input.dispatchEvent(new InputEvent('beforeinput', {
    bubbles: true,
    data: email,
    inputType: 'insertText',
}));
input.dispatchEvent(new InputEvent('input', {
    bubbles: true,
    data: email,
    inputType: 'insertText',
}));
input.dispatchEvent(new Event('change', { bubbles: true }));

if ((input.value || '').trim() !== email || !input.checkValidity()) {
    return false;
}

input.blur();
return 'filled';
            """,
            email,
        )

        if filled == 'not-ready':
            time.sleep(random.uniform(0.350, 0.675))
            continue

        if filled != 'filled':
            print(f"[Debug] 邮箱输入框已出现，但写入失败: {filled}")
            time.sleep(random.uniform(0.350, 0.675))
            continue

        if filled == 'filled':
            time.sleep(random.uniform(0.560, 1.080))
            clicked = page.run_js(
                r"""
function isVisible(node) {
    if (!node) {
        return false;
    }
    const style = window.getComputedStyle(node);
    if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') {
        return false;
    }
    const rect = node.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
}

const input = Array.from(document.querySelectorAll('input[data-testid="email"], input[name="email"], input[type="email"], input[autocomplete="email"]')).find((node) => {
    return isVisible(node) && !node.disabled && !node.readOnly;
}) || null;

if (!input || !input.checkValidity() || !(input.value || '').trim()) {
    return false;
}

const buttons = Array.from(document.querySelectorAll('button[type="submit"], button')).filter((node) => {
    return isVisible(node) && !node.disabled && node.getAttribute('aria-disabled') !== 'true';
});
const submitButton = buttons.find((node) => {
    const text = (node.innerText || node.textContent || '').replace(/\s+/g, '');
    const t = text.toLowerCase(); return text === '注册' || text.includes('注册') || t === 'signup' || t === 'sign up' || t.includes('sign up');
});

if (!submitButton || submitButton.disabled) {
    return false;
}

submitButton.click();
return true;
                """
            )

            if clicked:
                print(f"[*] 已填写邮箱并点击注册: {email}")
                return email, dev_token

        time.sleep(random.uniform(0.350, 0.675))

    raise Exception("未找到邮箱输入框或注册按钮")



def fill_code_and_submit(email, dev_token, timeout=60):
    # 复用 `email_register.py` 里的验证码轮询逻辑，等待邮件到达后自动填写 OTP。
    code = get_oai_code(dev_token, email)
    if not code:
        raise Exception("获取验证码失败")
    code_raw = str(code).strip()
    code = "".join(ch for ch in code_raw if ch.isalnum())
    if not code:
        code = code_raw
    print(f"[*] 获取到验证码: {code_raw} -> fill={code}")

    if on_signup_profile_step():
        print("[*] 已处于资料填写页，跳过验证码写入")
        return (code if code else "SKIPPED")

    deadline = time.time() + timeout
    while time.time() < deadline:
        if on_signup_profile_step():
            print("[*] 轮询中检测到资料页，跳过验证码写入")
            return code
        try:
            filled = page.run_js(
                r"""
const rawCode = String(arguments[0] || '').trim();
// xAI codes are often ABC-DEF; inputs usually want plain alnum without hyphen.
const codePlain = rawCode.replace(/[^A-Za-z0-9]/g, '').toUpperCase();
const codeHyphen = rawCode.toUpperCase();
const code = codePlain || codeHyphen;

function isVisible(node) {
    if (!node) return false;
    const style = window.getComputedStyle(node);
    if (style.display === 'none' || style.visibility === 'hidden' || Number(style.opacity) === 0) {
        return false;
    }
    const rect = node.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
}

function setNativeValue(input, value) {
    try {
        const proto = window.HTMLInputElement.prototype;
        const desc = Object.getOwnPropertyDescriptor(proto, 'value');
        const tracker = input._valueTracker;
        if (tracker) {
            try { tracker.setValue(input.value == null ? '' : String(input.value)); } catch (e) {}
            try { tracker.setValue(''); } catch (e) {}
        }
        if (desc && desc.set) {
            desc.set.call(input, value);
        } else {
            input.value = value;
        }
    } catch (e) {
        try { input.value = value; } catch (e2) {}
    }
}

function dispatchInputEvents(input, value) {
    try {
        input.dispatchEvent(new InputEvent('beforeinput', {
            bubbles: true, cancelable: true, data: value, inputType: 'insertText',
        }));
    } catch (e) {}
    try {
        input.dispatchEvent(new InputEvent('input', {
            bubbles: true, cancelable: true, data: value, inputType: 'insertText',
        }));
    } catch (e) {
        input.dispatchEvent(new Event('input', { bubbles: true }));
    }
    input.dispatchEvent(new Event('change', { bubbles: true }));
}

function fillOne(input, value) {
    try { input.focus(); } catch (e) {}
    try { input.click(); } catch (e) {}
    try { input.select && input.select(); } catch (e) {}
    // Strategy A: native value setter (React-friendly)
    setNativeValue(input, '');
    setNativeValue(input, value);
    dispatchInputEvents(input, value);
    // Strategy B: clipboard paste simulation
    if (String(input.value || '') !== value) {
        try {
            input.focus();
            const dt = new DataTransfer();
            dt.setData('text/plain', value);
            input.dispatchEvent(new ClipboardEvent('paste', {
                bubbles: true, cancelable: true, clipboardData: dt,
            }));
            if (String(input.value || '') !== value) {
                setNativeValue(input, value);
                dispatchInputEvents(input, value);
            }
        } catch (e) {}
    }
    // Strategy C: execCommand insertText
    if (String(input.value || '') !== value) {
        try {
            input.focus();
            input.select && input.select();
            document.execCommand('selectAll', false, null);
            document.execCommand('insertText', false, value);
        } catch (e) {}
    }
    // Strategy D: char-by-char keyboard for short OTP
    if (String(input.value || '') !== value && value.length <= 12) {
        try {
            setNativeValue(input, '');
            dispatchInputEvents(input, '');
            for (const ch of value) {
                input.dispatchEvent(new KeyboardEvent('keydown', { bubbles: true, key: ch, code: 'Key' + ch }));
                input.dispatchEvent(new KeyboardEvent('keypress', { bubbles: true, key: ch }));
                setNativeValue(input, String(input.value || '') + ch);
                dispatchInputEvents(input, ch);
                input.dispatchEvent(new KeyboardEvent('keyup', { bubbles: true, key: ch }));
            }
        } catch (e) {}
    }
    return String(input.value || '').trim();
}

function readOtpBoxes(boxes) {
    return boxes.map((n) => String(n.value || '').trim()).join('');
}

function readSlots() {
    const slots = Array.from(document.querySelectorAll(
        '[data-input-otp-slot="true"], [data-slot], [role="presentation"] span'
    ));
    // Prefer explicit otp slots
    const otpSlots = Array.from(document.querySelectorAll('[data-input-otp-slot="true"]'));
    if (otpSlots.length) {
        return otpSlots.map((s) => (s.textContent || '').trim()).join('');
    }
    return '';
}

// Prefer single-char OTP boxes first (common on accounts.x.ai)
let otpBoxes = Array.from(document.querySelectorAll('input')).filter((node) => {
    if (!isVisible(node) || node.disabled || node.readOnly) return false;
    const maxLength = Number(node.maxLength || 0);
    const autocomplete = String(node.autocomplete || '').toLowerCase();
    const name = String(node.name || '').toLowerCase();
    const id = String(node.id || '').toLowerCase();
    const testid = String(node.getAttribute('data-testid') || '').toLowerCase();
    const otpAttr = node.getAttribute('data-input-otp');
    if (maxLength === 1) return true;
    if (otpAttr === 'true' && maxLength === 1) return true;
    if (autocomplete === 'one-time-code' && maxLength === 1) return true;
    if ((name.includes('otp') || id.includes('otp') || testid.includes('otp')) && maxLength === 1) return true;
    return false;
});

// Also collect multi-char aggregate inputs
const aggregates = Array.from(document.querySelectorAll(
    'input[data-input-otp="true"], input[name="code"], input[name="otp"], input[autocomplete="one-time-code"], input[inputmode="numeric"], input[inputmode="text"], input[type="tel"], input[type="text"]'
)).filter((node) => {
    if (!isVisible(node) || node.disabled || node.readOnly) return false;
    const maxLength = Number(node.maxLength || 0);
    // multi-char: maxLength missing/-1/0 or > 1
    return maxLength !== 1;
});

if (!otpBoxes.length && !aggregates.length) {
    return 'not-ready';
}

// ---- Path 1: per-box OTP (most reliable for xAI) ----
if (otpBoxes.length >= Math.min(code.length, 4)) {
    const need = Math.min(otpBoxes.length, code.length);
    const ordered = otpBoxes.slice(0, need);
    for (let i = 0; i < ordered.length; i += 1) {
        fillOne(ordered[i], code[i] || '');
    }
    let merged = readOtpBoxes(ordered).replace(/[^A-Za-z0-9]/g, '').toUpperCase();
    if (merged === code.slice(0, need) || merged === code) {
        try { ordered[ordered.length - 1].blur(); } catch (e) {}
        return 'filled';
    }
    // retry boxes once
    for (let i = 0; i < ordered.length; i += 1) {
        fillOne(ordered[i], code[i] || '');
    }
    merged = readOtpBoxes(ordered).replace(/[^A-Za-z0-9]/g, '').toUpperCase();
    if (merged === code.slice(0, need) || merged === code) {
        return 'filled';
    }
    // fall through to aggregate; keep trying
}

// ---- Path 2: aggregate / hidden OTP input ----
const candidates = [code, codePlain, codeHyphen].filter((v, i, a) => v && a.indexOf(v) === i);
for (const input of aggregates) {
    for (const tryCode of candidates) {
        const got = fillOne(input, tryCode).replace(/[^A-Za-z0-9]/g, '').toUpperCase();
        const expect = tryCode.replace(/[^A-Za-z0-9]/g, '').toUpperCase();
        const slotText = readSlots().replace(/[^A-Za-z0-9]/g, '').toUpperCase();
        if (got === expect || slotText === expect || got.endsWith(expect) || expect.startsWith(got) && got.length >= 6) {
            try { input.blur(); } catch (e) {}
            return 'filled';
        }
        // maxLength truncated?
        const ml = Number(input.maxLength || 0);
        if (ml > 0 && got.length === ml && expect.startsWith(got)) {
            try { input.blur(); } catch (e) {}
            return 'filled';
        }
    }
}

// ---- Path 3: any visible single inputs equal to code length ----
if (!otpBoxes.length) {
    const singles = Array.from(document.querySelectorAll('input')).filter((node) => {
        if (!isVisible(node) || node.disabled || node.readOnly) return false;
        return Number(node.maxLength || 0) === 1;
    });
    if (singles.length >= code.length) {
        for (let i = 0; i < code.length; i += 1) {
            fillOne(singles[i], code[i]);
        }
        const merged = singles.slice(0, code.length).map((n) => String(n.value || '')).join('').toUpperCase();
        if (merged === code) return 'filled';
    }
}

// Soft success: if any aggregate already holds enough of the code after attempts
for (const input of aggregates) {
    const v = String(input.value || '').replace(/[^A-Za-z0-9]/g, '').toUpperCase();
    if (v && (v === code || code.startsWith(v) && v.length >= 6 || v.includes(code))) {
        return 'filled';
    }
}

if (aggregates.length || otpBoxes.length) {
    // Inputs exist but value not readable (controlled/shadow) ? still try confirm later.
    // Return soft-filled so click path can proceed once; verification re-check handles empty.
    const soft = aggregates[0] || otpBoxes[0];
    if (soft) {
        try {
            soft.focus();
            // last-ditch assign
            soft.value = code;
            soft.dispatchEvent(new Event('input', { bubbles: true }));
            soft.dispatchEvent(new Event('change', { bubbles: true }));
        } catch (e) {}
    }
    return 'soft-filled';
}

return 'not-ready';
                """,
                code,
            )

        except PageDisconnectedError:
            # 点击确认邮箱后如果刚好发生跳转，旧页面句柄会断开；此时切到新页继续判断即可。
            refresh_active_page()
            if has_profile_form():
                print("[*] 验证码提交后已跳转到最终注册页。")
                return code
            time.sleep(random.uniform(0.700, 1.350))
            continue

        if filled == 'not-ready':
            if has_profile_form():
                print("[*] 已直接进入最终注册页，跳过验证码按钮确认。")
                return code
            time.sleep(random.uniform(0.350, 0.675))
            continue

        if filled not in ('filled', 'soft-filled'):
            print(f"[Debug] 验证码输入框已出现，但写入失败: {filled}")
            time.sleep(random.uniform(0.350, 0.675))
            continue
        if filled == 'soft-filled':
            print(f"[Debug] 验证码 value 未回读，按 soft-filled 继续提交: {code}")

        if filled in ('filled', 'soft-filled'):
            time.sleep(random.uniform(0.840, 1.620))
            try:
                clicked = page.run_js(
                    r"""
function isVisible(node) {
    if (!node) {
        return false;
    }
    const style = window.getComputedStyle(node);
    if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') {
        return false;
    }
    const rect = node.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
}

const aggregateInput = Array.from(document.querySelectorAll('input[data-input-otp="true"], input[name="code"], input[autocomplete="one-time-code"], input[inputmode="numeric"], input[inputmode="text"]')).find((node) => {
    return isVisible(node) && !node.disabled && !node.readOnly && Number(node.maxLength || 0) > 1;
}) || null;

let value = '';
if (aggregateInput) {
    value = String(aggregateInput.value || '').trim();
    const expectedLength = Number(aggregateInput.maxLength || value.length || 6);
    if (!value || (expectedLength > 0 && value.length !== expectedLength)) {
        return false;
    }

    const slots = Array.from(document.querySelectorAll('[data-input-otp-slot="true"]'));
    if (slots.length) {
        const filledSlots = slots.filter((slot) => (slot.textContent || '').trim()).length;
        if (filledSlots && filledSlots !== value.length) {
            return false;
        }
    }
} else {
    const otpBoxes = Array.from(document.querySelectorAll('input')).filter((node) => {
        if (!isVisible(node) || node.disabled || node.readOnly) {
            return false;
        }
        const maxLength = Number(node.maxLength || 0);
        const autocomplete = String(node.autocomplete || '').toLowerCase();
        return maxLength === 1 || autocomplete === 'one-time-code';
    });
    value = otpBoxes.map((node) => String(node.value || '').trim()).join('');
    if (!value || value.length < 6) {
        return false;
    }
}

const buttons = Array.from(document.querySelectorAll('button[type="submit"], button')).filter((node) => {
    return isVisible(node) && !node.disabled && node.getAttribute('aria-disabled') !== 'true';
});
const confirmButton = buttons.find((node) => {
    const text = (node.innerText || node.textContent || '').replace(/\s+/g, '');
    const t = text.toLowerCase(); return text === '确认邮箱' || text.includes('确认邮箱') || text === '继续' || text.includes('继续') || text === '下一步' || text.includes('下一步') || t.includes('confirm') || t.includes('continue') || t.includes('next') || t.includes('verify');
});

if (!confirmButton) {
    return 'no-button';
}

confirmButton.focus();
confirmButton.click();
return 'clicked';
                    """
                )
            except PageDisconnectedError:
                refresh_active_page()
                if has_profile_form():
                    print("[*] 确认邮箱后页面跳转成功，已进入最终注册页。")
                    return code
                clicked = 'disconnected'

            if clicked == 'clicked':
                print(f"[*] 已填写验证码并点击确认邮箱: {code}")
                time.sleep(random.uniform(1.400, 2.700))
                refresh_active_page()
                if has_profile_form():
                    print("[*] 验证码确认完成，最终注册页已就绪。")
                return code

            if clicked == 'no-button':
                current_url = page.url
                if 'sign-up' in current_url or 'signup' in current_url:
                    print(f"[*] 已填写验证码，页面已自动跳转到下一步: {current_url}")
                    return code

            if clicked == 'disconnected':
                time.sleep(random.uniform(0.700, 1.350))
                continue

        time.sleep(random.uniform(0.350, 0.675))

    debug_snapshot = page.run_js(
        r"""
function isVisible(node) {
    if (!node) {
        return false;
    }
    const style = window.getComputedStyle(node);
    if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') {
        return false;
    }
    const rect = node.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
}

const inputs = Array.from(document.querySelectorAll('input')).filter(isVisible).map((node) => ({
    type: node.type || '',
    name: node.name || '',
    testid: node.getAttribute('data-testid') || '',
    autocomplete: node.autocomplete || '',
    maxLength: Number(node.maxLength || 0),
    value: String(node.value || ''),
}));

const buttons = Array.from(document.querySelectorAll('button')).filter(isVisible).map((node) => ({
    text: String(node.innerText || node.textContent || '').replace(/\s+/g, ' ').trim(),
    disabled: !!node.disabled,
    ariaDisabled: node.getAttribute('aria-disabled') || '',
}));

return { url: location.href, inputs, buttons };
        """
    )
    print(f"[Debug] 验证码页 DOM 摘要: {debug_snapshot}")
    if on_signup_profile_step():
        print("[*] DOM 已进入姓名/密码注册页，视为验证码步骤完成。")
        return code
    try:
        snap = debug_snapshot if isinstance(debug_snapshot, dict) else {}
        names = {str(x.get("name") or "") for x in (snap.get("inputs") or []) if isinstance(x, dict)}
        btn_text = " ".join(
            str(b.get("text") or "").lower()
            for b in (snap.get("buttons") or [])
            if isinstance(b, dict)
        )
        if {"givenName", "familyName", "password"} <= names or (
            "givenName" in names and "password" in names
        ) or ("complete sign up" in btn_text):
            print("[*] 根据 DOM 摘要判定已进入资料页，跳过验证码错误。")
            return code
    except Exception:
        pass
    raise Exception("未找到验证码输入框或确认邮箱按钮")


def getTurnstileToken():
    # 复用现有 turnstile 处理逻辑，在最终注册页需要时再触发。
    page.run_js("try { turnstile.reset() } catch(e) { }")

    turnstileResponse = None

    for i in range(0, 15):
        try:
            turnstileResponse = page.run_js("try { return turnstile.getResponse() } catch(e) { return null }")
            if turnstileResponse:
                return turnstileResponse

            challengeSolution = page.ele("@name=cf-turnstile-response")
            challengeWrapper = challengeSolution.parent()
            challengeIframe = challengeWrapper.shadow_root.ele("tag:iframe")

            challengeIframe.run_js("""
window.dtp = 1
function getRandomInt(min, max) {
    return Math.floor(Math.random() * (max - min + 1)) + min;
}

// 旧方案在 4K 屏下不稳定，这里给出更自然的屏幕坐标。
let screenX = getRandomInt(800, 1200);
let screenY = getRandomInt(400, 600);

Object.defineProperty(MouseEvent.prototype, 'screenX', { value: screenX });
Object.defineProperty(MouseEvent.prototype, 'screenY', { value: screenY });
                        """)

            challengeIframeBody = challengeIframe.ele("tag:body").shadow_root
            challengeButton = challengeIframeBody.ele("tag:input")
            challengeButton.click()
        except:
            pass
        time.sleep(random.uniform(0.700, 1.350))
    raise Exception("failed to solve turnstile")


def _build_register_password(min_len: int = 14, max_len: int = 22) -> str:
    """Build a strong password with variable length/structure (not a fixed template).

    Always includes upper / lower / digit / special; length random in [min_len, max_len].
    Avoids a fixed prefix/middle marker so batch accounts look less patterned.
    """
    min_len = max(12, int(min_len or 14))
    max_len = max(min_len, int(max_len or 22))
    length = random.randint(min_len, max_len)

    upper = string.ascii_uppercase
    lower = string.ascii_lowercase
    digits = string.digits
    # Avoid ambiguous / form-hostile chars: space, quotes, backslash
    specials = "!@#$%^&*_-+=?"
    # Prefer alnum-heavy pool so passwords stay easy to type/export
    pool = upper + lower + digits + specials

    # Guarantee one of each class, then fill the rest randomly
    required = [
        secrets.choice(upper),
        secrets.choice(lower),
        secrets.choice(digits),
        secrets.choice(specials),
    ]
    rest = [secrets.choice(pool) for _ in range(length - len(required))]
    chars = required + rest
    # Shuffle with secrets-backed RNG
    for i in range(len(chars) - 1, 0, -1):
        j = secrets.randbelow(i + 1)
        chars[i], chars[j] = chars[j], chars[i]
    password = "".join(chars)

    # Extra safety: if shuffle somehow missed a class (should not), repair
    if not re.search(r"[A-Z]", password):
        password = password[:-1] + secrets.choice(upper)
    if not re.search(r"[a-z]", password):
        password = password[:-2] + secrets.choice(lower) + password[-1:]
    if not re.search(r"[0-9]", password):
        password = password[:-3] + secrets.choice(digits) + password[-2:]
    if not re.search(r"[!@#$%^&*_\-+=?]", password):
        password = password[:-4] + secrets.choice(specials) + password[-3:]
    return password


def build_profile():
    """Generate registration profile: diverse names + strong variable password."""
    # Mix Latin first names; surnames mix East-Asia pinyin + common Western.
    # Avoid always "Chinese surname + short English given" which looks templated.
    given_name_pool = [
        "Neo", "Ethan", "Liam", "Noah", "Lucas", "Mason", "Ryan", "Leo",
        "Owen", "Aiden", "Elio", "Aron", "Ivan", "Nolan", "Evan", "Kai",
        "Caleb", "Adam", "Ezra", "Miles", "Logan", "Carter", "Hunter", "Jason",
        "Brian", "Dylan", "Alex", "Colin", "Blake", "Gavin", "Henry", "Julian",
        "Kevin", "Louis", "Marcus", "Nathan", "Oscar", "Peter", "Quinn", "Robin",
        "Simon", "Tristan", "Victor", "Wesley", "Xavier", "Yuri", "Zane", "Felix",
        "Aaron", "Damian", "Sofia", "Emma", "Olivia", "Ava", "Mia", "Luna",
        "Chloe", "Nora", "Ivy", "Zoe", "Ella", "Aria", "Maya", "Ruby",
        "Grace", "Hazel", "Iris", "Jade", "Leah", "Nina", "Tara", "Vera",
        "Daniel", "Michael", "James", "Robert", "David", "Thomas", "Andrew",
        "Matthew", "Joseph", "Samuel", "Benjamin", "Christopher", "Anthony",
        "William", "Alexander", "Sebastian", "Theodore", "Gabriel", "Mateo",
    ]
    family_name_pool = [
        # East Asia (pinyin)
        "Lin", "Wang", "Zhao", "Liu", "Chen", "Zhang", "Xu", "Sun",
        "Guo", "He", "Yang", "Wu", "Zhou", "Tang", "Qin", "Shi",
        "Fang", "Peng", "Cao", "Deng", "Fan", "Fu", "Gao", "Han",
        "Hu", "Jiang", "Kong", "Lu", "Ma", "Nie", "Pan", "Qiao",
        "Ren", "Shao", "Tian", "Xie", "Yan", "Yao", "Yu", "Zeng",
        "Bai", "Duan", "Hou", "Jin", "Kang", "Luo", "Mao", "Song",
        "Wei", "Xiong",
        # Western / mixed
        "Smith", "Johnson", "Brown", "Jones", "Miller", "Davis", "Wilson",
        "Moore", "Taylor", "Anderson", "Thomas", "Jackson", "White", "Harris",
        "Martin", "Thompson", "Garcia", "Martinez", "Robinson", "Clark",
        "Rodriguez", "Lewis", "Lee", "Walker", "Hall", "Allen", "Young",
        "King", "Wright", "Scott", "Green", "Baker", "Adams", "Nelson",
        "Hill", "Ramirez", "Campbell", "Mitchell", "Roberts", "Carter",
        "Phillips", "Evans", "Turner", "Torres", "Parker", "Collins",
        "Edwards", "Stewart", "Morris", "Murphy", "Cook", "Rogers", "Morgan",
        "Peterson", "Cooper", "Reed", "Bailey", "Bell", "Gomez", "Kelly",
        "Howard", "Ward", "Cox", "Diaz", "Richardson", "Wood", "Watson",
        "Brooks", "Bennett", "Gray", "James", "Reyes", "Cruz", "Hughes",
        "Price", "Myers", "Long", "Foster", "Sanders", "Ross", "Morales",
        "Powell", "Sullivan", "Russell", "Ortiz", "Jenkins", "Gutierrez",
        "Perry", "Butler", "Barnes", "Fisher", "Henderson", "Coleman",
        "Simmons", "Patterson", "Jordan", "Reynolds", "Hamilton", "Graham",
        "Kim", "Park", "Choi", "Nguyen", "Tran", "Patel", "Singh", "Khan",
        "Ali", "Hassan", "Ahmed", "Silva", "Santos", "Costa", "Oliveira",
    ]
    given_name = random.choice(given_name_pool)
    family_name = random.choice(family_name_pool)
    password = _build_register_password(14, 22)
    return given_name, family_name, password


def fill_profile_and_submit(timeout=30):
    # 在验证码通过后，直接锁定“可见且可写”的真实输入框，避免命中隐藏节点或 React 受控副本。
    given_name, family_name, password = build_profile()
    deadline = time.time() + timeout
    turnstile_token = ""

    while time.time() < deadline:
        filled = page.run_js(
            """
const givenName = arguments[0];
const familyName = arguments[1];
const password = arguments[2];

function isVisible(node) {
    if (!node) {
        return false;
    }
    const style = window.getComputedStyle(node);
    if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') {
        return false;
    }
    const rect = node.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
}

function pickInput(selector) {
    return Array.from(document.querySelectorAll(selector)).find((node) => {
        return isVisible(node) && !node.disabled && !node.readOnly;
    }) || null;
}

function setInputValue(input, value) {
    if (!input) {
        return false;
    }
    input.focus();
    input.click();

    const nativeSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')?.set;
    const tracker = input._valueTracker;
    if (tracker) {
        tracker.setValue('');
    }

    if (nativeSetter) {
        nativeSetter.call(input, '');
        nativeSetter.call(input, value);
    } else {
        input.value = '';
        input.value = value;
    }

    input.dispatchEvent(new InputEvent('beforeinput', {
        bubbles: true,
        cancelable: true,
        data: value,
        inputType: 'insertText',
    }));
    input.dispatchEvent(new InputEvent('input', {
        bubbles: true,
        cancelable: true,
        data: value,
        inputType: 'insertText',
    }));
    input.dispatchEvent(new Event('change', { bubbles: true }));
    input.dispatchEvent(new Event('blur', { bubbles: true }));

    return String(input.value || '') === String(value || '');
}

const givenInput = pickInput('input[data-testid="givenName"], input[name="givenName"], input[autocomplete="given-name"]');
const familyInput = pickInput('input[data-testid="familyName"], input[name="familyName"], input[autocomplete="family-name"]');
const passwordInput = pickInput('input[data-testid="password"], input[name="password"], input[type="password"]');

if (!givenInput || !familyInput || !passwordInput) {
    return 'not-ready';
}

const givenOk = setInputValue(givenInput, givenName);
const familyOk = setInputValue(familyInput, familyName);
const passwordOk = setInputValue(passwordInput, password);

if (!givenOk || !familyOk || !passwordOk) {
    return 'filled-failed';
}

return [
    String(givenInput.value || '').trim() === String(givenName || '').trim(),
    String(familyInput.value || '').trim() === String(familyName || '').trim(),
    String(passwordInput.value || '') === String(password || ''),
].every(Boolean) ? 'filled' : 'verify-failed';
            """,
            given_name,
            family_name,
            password,
        )

        if filled == 'not-ready':
            time.sleep(random.uniform(0.350, 0.675))
            continue

        if filled != 'filled':
            print(f"[Debug] 最终注册页输入框已出现，但姓名/密码写入失败: {filled}")
            time.sleep(random.uniform(0.350, 0.675))
            continue

        values_ok = page.run_js(
            """
const expectedGiven = arguments[0];
const expectedFamily = arguments[1];
const expectedPassword = arguments[2];

function isVisible(node) {
    if (!node) {
        return false;
    }
    const style = window.getComputedStyle(node);
    if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') {
        return false;
    }
    const rect = node.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
}

function pickInput(selector) {
    return Array.from(document.querySelectorAll(selector)).find((node) => {
        return isVisible(node) && !node.disabled && !node.readOnly;
    }) || null;
}

const givenInput = pickInput('input[data-testid="givenName"], input[name="givenName"], input[autocomplete="given-name"]');
const familyInput = pickInput('input[data-testid="familyName"], input[name="familyName"], input[autocomplete="family-name"]');
const passwordInput = pickInput('input[data-testid="password"], input[name="password"], input[type="password"]');

if (!givenInput || !familyInput || !passwordInput) {
    return false;
}

return String(givenInput.value || '').trim() === String(expectedGiven || '').trim()
    && String(familyInput.value || '').trim() === String(expectedFamily || '').trim()
    && String(passwordInput.value || '') === String(expectedPassword || '');
            """,
            given_name,
            family_name,
            password,
        )
        if not values_ok:
            print("[Debug] 最终注册页字段值校验失败，继续重试填写。")
            time.sleep(random.uniform(0.350, 0.675))
            continue

        turnstile_state = page.run_js(
            """
const challengeInput = document.querySelector('input[name="cf-turnstile-response"]');
if (!challengeInput) {
    return 'not-found';
}
const value = String(challengeInput.value || '').trim();
return value ? 'ready' : 'pending';
            """
        )

        if turnstile_state == "pending" and not turnstile_token:
            print("[*] 检测到最终注册页存在 Turnstile，开始使用现有真人化点击逻辑。")
            turnstile_token = getTurnstileToken()
            if turnstile_token:
                synced = page.run_js(
                    """
const token = arguments[0];
const challengeInput = document.querySelector('input[name="cf-turnstile-response"]');
if (!challengeInput) {
    return false;
}
const nativeSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')?.set;
if (nativeSetter) {
    nativeSetter.call(challengeInput, token);
} else {
    challengeInput.value = token;
}
challengeInput.dispatchEvent(new Event('input', { bubbles: true }));
challengeInput.dispatchEvent(new Event('change', { bubbles: true }));
return String(challengeInput.value || '').trim() === String(token || '').trim();
                    """,
                    turnstile_token,
                )
                if synced:
                    print("[*] Turnstile 响应已同步到最终注册表单。")

        time.sleep(random.uniform(0.840, 1.620))

        try:
            submit_button = page.ele('tag:button@@text()=完成注册') or page.ele('tag:button@@text():Create Account') or page.ele('tag:button@@text():Sign up')
        except Exception:
            submit_button = None

        if not submit_button:
            clicked = page.run_js(
                r"""
const challengeInput = document.querySelector('input[name="cf-turnstile-response"]');
if (challengeInput && !String(challengeInput.value || '').trim()) {
    return false;
}
const buttons = Array.from(document.querySelectorAll('button[type="submit"], button'));
const submitButton = buttons.find((node) => {
    const text = (node.innerText || node.textContent || '').replace(/\s+/g, '');
    const t = text.toLowerCase(); return text === '完成注册' || text.includes('完成注册') || t.includes('create account') || t.includes('sign up') || t.includes('complete');
});
if (!submitButton || submitButton.disabled || submitButton.getAttribute('aria-disabled') === 'true') {
    return false;
}
submitButton.focus();
submitButton.click();
return true;
                """
            )
        else:
            challenge_value = page.run_js(
                """
const challengeInput = document.querySelector('input[name="cf-turnstile-response"]');
return challengeInput ? String(challengeInput.value || '').trim() : 'not-found';
                """
            )
            if challenge_value not in ('not-found', ''):
                submit_button.click()
                clicked = True
            else:
                clicked = False

        if clicked:
            print(f"[*] 已填写注册资料并点击完成注册: {given_name} {family_name} / {password}")
            return {
                "given_name": given_name,
                "family_name": family_name,
                "password": password,
            }

        time.sleep(random.uniform(0.350, 0.675))

    raise Exception("未找到最终注册表单或完成注册按钮")


def extract_visible_numbers(timeout=60):
    # 登录/注册完成后，提取页面上可见的普通数字文本，不处理任何敏感 Cookie。
    deadline = time.time() + timeout
    while time.time() < deadline:
        result = page.run_js(
            r"""
function isVisible(el) {
    if (!el) {
        return false;
    }
    const style = window.getComputedStyle(el);
    if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') {
        return false;
    }
    const rect = el.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
}

const selector = [
    'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
    'div', 'span', 'p', 'strong', 'b', 'small',
    '[data-testid]', '[class]', '[role="heading"]'
].join(',');

const seen = new Set();
const matches = [];
for (const node of document.querySelectorAll(selector)) {
    if (!isVisible(node)) {
        continue;
    }
    const text = String(node.innerText || node.textContent || '').replace(/\s+/g, ' ').trim();
    if (!text) {
        continue;
    }
    const found = text.match(/\d+(?:\.\d+)?/g);
    if (!found) {
        continue;
    }
    for (const value of found) {
        const key = `${value}@@${text}`;
        if (seen.has(key)) {
            continue;
        }
        seen.add(key);
        matches.push({ value, text });
    }
}

return matches.slice(0, 30);
            """
        )

        if result:
            print("[*] 页面可见数字文本提取结果:")
            for item in result:
                try:
                    print(f"    - 数字: {item['value']} | 上下文: {item['text']}")
                except Exception:
                    pass
            return result

        time.sleep(random.uniform(0.700, 1.350))

    raise Exception("登录后未提取到可见数字文本")


def wait_for_sso_cookie(timeout=30):
    # 必须在注册完成后再取 sso，优先抓取精确的 sso cookie。
    deadline = time.time() + timeout
    last_seen_names = set()

    while time.time() < deadline:
        try:
            refresh_active_page()
            if page is None:
                time.sleep(random.uniform(0.700, 1.350))
                continue

            cookies = page.cookies(all_domains=True, all_info=True) or []
            for item in cookies:
                if isinstance(item, dict):
                    name = str(item.get("name", "")).strip()
                    value = str(item.get("value", "")).strip()
                else:
                    name = str(getattr(item, "name", "")).strip()
                    value = str(getattr(item, "value", "")).strip()

                if name:
                    last_seen_names.add(name)

                if name == "sso" and value:
                    print("[*] 注册完成后已获取到 sso cookie。")
                    return value

        except PageDisconnectedError:
            refresh_active_page()
        except Exception:
            pass

        time.sleep(random.uniform(0.700, 1.350))

    raise Exception(f"注册完成后未获取到 sso cookie，当前已见 cookie: {sorted(last_seen_names)}")


def append_sso_to_txt(sso_value, output_path=DEFAULT_SSO_FILE):
    # 可选备份：Console 下默认跳过 sso txt（账号以 DB 为准）。GROK_WRITE_SSO_TXT=1 可强制写。
    if _console_task_context()[0] is not None and str(os.environ.get("GROK_WRITE_SSO_TXT") or "").strip().lower() not in {"1", "true", "yes"}:
        return
    normalized = str(sso_value or "").strip()
    if not normalized:
        raise Exception("待写入的 sso 为空")

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "a", encoding="utf-8") as file:
        file.write(normalized + "\n")



def append_account_to_jsonl(account_record: dict, output_path=DEFAULT_ACCOUNT_FILE):
    if not output_path:
        return

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "a", encoding="utf-8") as file:
        file.write(json.dumps(account_record, ensure_ascii=False, separators=(",", ":")) + "\n")



def update_account_cpa_in_jsonl(email: str, sso_value: str, cpa_record: dict, output_path=DEFAULT_ACCOUNT_FILE):
    if not output_path or not os.path.isfile(output_path):
        return

    updated_lines = []
    changed = False
    with open(output_path, "r", encoding="utf-8") as file:
        for line in file:
            raw = line.rstrip("\n")
            if not raw.strip():
                continue
            try:
                record = json.loads(raw)
            except json.JSONDecodeError:
                updated_lines.append(raw)
                continue
            if (
                isinstance(record, dict)
                and str(record.get("email") or "").strip() == email
                and str(record.get("sso") or "").strip() == sso_value
            ):
                record["cpa"] = cpa_record
                raw = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
                changed = True
            updated_lines.append(raw)

    if changed:
        with open(output_path, "w", encoding="utf-8") as file:
            file.write("\n".join(updated_lines) + "\n")
        print(f"[*] 已更新账号 CPA 状态: {output_path}")


def build_account_record(email: str, sso_value: str, profile: dict) -> dict:
    return {
        "email": email,
        "sso": sso_value,
        "given_name": profile.get("given_name", ""),
        "family_name": profile.get("family_name", ""),
        "password": profile.get("password", ""),
        "created_at": datetime.datetime.now().isoformat(timespec="seconds"),
    }


def run_single_registration(output_path=DEFAULT_SSO_FILE, account_output_path=DEFAULT_ACCOUNT_FILE, extract_numbers=False):
    # 单轮流程：打开注册页 -> 完成注册 -> 获取 sso -> 先写本地结果 -> 再做 CPA 授权。
    open_signup_page()
    email, dev_token = fill_email_and_submit()
    fill_code_and_submit(email, dev_token)
    profile = fill_profile_and_submit()
    sso_value = wait_for_sso_cookie()
    append_sso_to_txt(sso_value, output_path)
    account_record = build_account_record(email, sso_value, profile)
    account_record["cpa"] = {
        "ok": False,
        "queued": True,
        "skipped": False,
        "reason": "",
        "path": "",
        "error": "",
        "cloud_uploaded": False,
    }
    account_record["source_file"] = str(account_output_path or "")
    append_account_to_jsonl(account_record, account_output_path)
    # Primary: write DB immediately so deleting task files cannot drop the account.
    persist_account_to_console_db(account_record, cpa_record=None)

    # Outmail: mark used + optional anonymous mailbox cleanup
    try:
        cleanup_mailbox_if_needed(email, dev_token=dev_token, log=lambda m: print(m, flush=True))
        if str(dev_token or "").startswith("outmail|"):
            try:
                from outmail_client import outmail_mark_mailbox_used, outmail_decode_token
                mailbox, _s, _r, mode = outmail_decode_token(dev_token, fallback_email=email)
                if mode != "anon":
                    outmail_mark_mailbox_used(mailbox or email, register_email=email, reason="success")
            except Exception as _om_exc:
                print(f"[outmail] mark used skipped: {_om_exc}", flush=True)
    except Exception as _om_exc:
        print(f"[outmail] cleanup skipped: {_om_exc}", flush=True)

    if run_logger:
        run_logger.info(
            "注册成功 | email=%s | password=%s | given=%s | family=%s",
            email,
            profile.get("password", ""),
            profile.get("given_name", ""),
            profile.get("family_name", ""),
        )

    cpa_result = export_cpa_auth(email, profile.get("password", ""), sso_value)
    cpa_record = {
        "ok": bool(cpa_result.get("ok")),
        "queued": False,
        "skipped": bool(cpa_result.get("skipped")),
        "reason": cpa_result.get("reason") or "",
        "path": cpa_result.get("cpa_path") or cpa_result.get("path") or "",
        "error": cpa_result.get("error") or "",
        "cloud_uploaded": bool(cpa_result.get("cloud_uploaded") or (cpa_result.get("cloud_cpa_upload") or {}).get("ok")),
        "mode": cpa_result.get("mode") or "",
        "token_status": cpa_result.get("token_status") or "",
        "sso_alive": cpa_result.get("sso_alive"),
        "liveness": cpa_result.get("liveness") or {},
        # 写入账号 cpa_log，与账号管理「查看日志」共用
        "log_lines": list(cpa_result.get("log_lines") or []),
    }
    account_record["cpa"] = cpa_record
    update_account_cpa_in_jsonl(email, sso_value, cpa_record, account_output_path)
    persist_account_to_console_db(account_record, cpa_record=cpa_record)

    if extract_numbers:
        extract_visible_numbers()

    result = {
        "email": email,
        "sso": sso_value,
        "account": account_record,
        "cpa": cpa_result,
        **profile,
    }

    print(f"[*] 本轮注册完成，邮箱: {email}")
    return result


def load_run_count() -> int:
    # 从 config.json 读取默认执行轮数，配置不存在时返回 10。
    config_path = os.path.join(os.path.dirname(__file__), "config.json")
    try:
        import json
        with open(config_path, "r", encoding="utf-8") as f:
            conf = json.load(f)
        v = conf.get("run", {}).get("count")
        if isinstance(v, int) and v >= 0:
            return v
    except Exception:
        pass
    return 10


def main():
    # 默认循环执行；每轮完成后关闭当前页，再自动进入下一轮。
    global run_logger
    run_logger = setup_run_logger()

    config_count = load_run_count()

    parser = argparse.ArgumentParser(description="xAI 自动注册并采集本地账号数据")
    parser.add_argument("--count", type=int, default=config_count, help=f"执行轮数，0 表示无限循环（默认读取 config.json run.count，当前 {config_count}）")
    parser.add_argument("--output", default=DEFAULT_SSO_FILE, help="sso 输出 txt 路径")
    parser.add_argument("--account-output", default=DEFAULT_ACCOUNT_FILE, help="账号数据 JSONL 输出路径")
    parser.add_argument("--extract-numbers", action="store_true", help="注册完成后额外提取页面数字文本")
    args = parser.parse_args()

    current_round = 0
    try:
        start_browser()
        while True:
            if args.count > 0 and current_round >= args.count:
                break

            current_round += 1
            print(f"\n[*] 开始第 {current_round} 轮注册")
            round_succeeded = False

            try:
                run_single_registration(args.output, account_output_path=args.account_output, extract_numbers=args.extract_numbers)
                round_succeeded = True
            except KeyboardInterrupt:
                print("\n[Info] 收到中断信号，停止后续轮次。")
                break
            except Exception as error:
                print(f"[Error] 第 {current_round} 轮失败: {error}")
            finally:
                restart_browser()

            if args.count == 0 or current_round < args.count:
                time.sleep(random.uniform(1.400, 2.700))

    finally:
        stop_browser()


if __name__ == "__main__":
    main()
