"""Register-machine hook: mint CPA xai auth after successful registration.

OIDC package lives at ./cpa_xai (bundled with this project).
Optional override: config `api_reverse_tools` / env `API_REVERSE_TOOLS`
points at a directory that *contains* the `cpa_xai` package.
"""

from __future__ import annotations

import os
import re
import shutil
import sys
import time
from pathlib import Path
from typing import Any, Callable

import requests

_REG_DIR = Path(__file__).resolve().parent
_DEFAULT_OUT = _REG_DIR / "cpa_auths"
_DEFAULT_CPA = Path("")  # empty = do not assume a machine-local CPA path


def _cloud_management_base(value: str) -> str:
    base = (value or "").strip().rstrip("/")
    if not base:
        return ""
    if not re.match(r"^https?://", base, re.IGNORECASE):
        base = f"http://{base}"
    base = re.sub(r"/v0/management/?$", "", base, flags=re.IGNORECASE).rstrip("/")
    return f"{base}/v0/management"


def _cloud_management_key(config: dict) -> str:
    return (
        os.environ.get("CPA_CLOUD_MANAGEMENT_KEY")
        or os.environ.get("CLI_PROXY_MANAGEMENT_KEY")
        or str(config.get("cpa_cloud_management_key") or "")
    ).strip()



def _import_cpa_health_check():
    """Load health_check.test_cpa_auth_file from sibling package."""
    try:
        from health_check.health_check import test_cpa_auth_file  # type: ignore
        return test_cpa_auth_file
    except Exception:
        pass
    import importlib.util

    candidates = [
        _REG_DIR / "health_check" / "health_check.py",
        Path(__file__).resolve().parent / "health_check" / "health_check.py",
    ]
    env_src = str(os.environ.get("GROK_REGISTER_SOURCE_DIR") or "").strip()
    if env_src:
        root = Path(env_src).expanduser().resolve()
        candidates.append(root / "apps" / "cpa-worker" / "health_check" / "health_check.py")

    seen: set[str] = set()
    for path in candidates:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        if not path.is_file():
            continue
        parent = path.parent.parent  # package parent (contains health_check/)
        if str(parent) not in sys.path:
            sys.path.insert(0, str(parent))
        try:
            from health_check.health_check import test_cpa_auth_file  # type: ignore
            return test_cpa_auth_file
        except Exception:
            # file load fallback
            spec = importlib.util.spec_from_file_location("cpa_health_check_mod", path)
            if spec is None or spec.loader is None:
                continue
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            fn = getattr(module, "test_cpa_auth_file", None)
            if callable(fn):
                return fn
    raise ImportError("cannot import health_check.test_cpa_auth_file")





def _import_cpa_to_sub2api():
    """Load sibling cpa_to_sub2api.py (apps/cpa-worker or task_dir copy)."""
    try:
        import cpa_to_sub2api  # type: ignore
        return cpa_to_sub2api
    except Exception:
        pass
    import importlib.util

    candidates = [
        _REG_DIR / "cpa_to_sub2api.py",
        Path(__file__).resolve().parent / "cpa_to_sub2api.py",
    ]
    env_src = str(os.environ.get("GROK_REGISTER_SOURCE_DIR") or "").strip()
    if env_src:
        root = Path(env_src).expanduser().resolve()
        candidates.append(root / "apps" / "cpa-worker" / "cpa_to_sub2api.py")

    seen: set[str] = set()
    for path in candidates:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        if not path.is_file():
            continue
        if str(path.parent) not in sys.path:
            sys.path.insert(0, str(path.parent))
        spec = importlib.util.spec_from_file_location("cpa_to_sub2api_mod", path)
        if spec is None or spec.loader is None:
            continue
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    raise ImportError("cannot import cpa_to_sub2api")


def _probe_delay_sec(config: dict | None = None) -> float:
    cfg = config or {}
    try:
        delay = float(cfg.get("cpa_probe_delay_sec", 5) or 5)
    except (TypeError, ValueError):
        delay = 5.0
    return max(0.0, min(120.0, delay))


def _sleep_before_probe(config: dict | None, log) -> None:
    delay = _probe_delay_sec(config)
    if delay <= 0:
        return
    log(f"[cpa-health] wait {delay:.1f}s before probe (cpa_probe_delay_sec)")
    time.sleep(delay)


def health_check_cpa_auth_before_upload(
    path_value: str | Path | None,
    config: dict,
    log: Callable[[str], None],
) -> dict:
    """Run chat/completions health check before remote CPA push.

    Returns:
      {ok: True} when alive or check disabled/skipped
      {ok: False, health_failed: True, error, message, path} when dead
    """
    enabled = bool(config.get("cpa_health_check_before_upload", True))
    if not enabled:
        return {"ok": True, "skipped": True, "reason": "disabled"}

    path = Path(path_value or "").expanduser().resolve()
    if not path.is_file():
        return {
            "ok": False,
            "health_failed": True,
            "error": "file_not_found",
            "message": "授权文件不存在，无法测活",
            "path": str(path),
        }

    base_url = str(
        config.get("cpa_base_url")
        or config.get("cpa_health_check_base_url")
        or "https://cli-chat-proxy.grok.com/v1"
    ).strip()
    model = str(config.get("cpa_health_check_model") or "grok-4.5").strip() or "grok-4.5"
    try:
        timeout = float(config.get("cpa_health_check_timeout", 15) or 15)
    except (TypeError, ValueError):
        timeout = 15.0
    proxy = str(
        config.get("cpa_proxy")
        or config.get("browser_proxy")
        or config.get("proxy")
        or ""
    ).strip() or None
    extra_headers = config.get("cpa_health_check_headers")
    use_file_headers = bool(config.get("cpa_health_check_use_file_headers", True))

    log(f"[cpa-health] 推送前测活: {path.name} model={model}")
    try:
        test_fn = _import_cpa_health_check()
        ok, message = test_fn(
            path,
            test_url=base_url,
            model=model,
            timeout=timeout,
            proxy=proxy,
            extra_headers=extra_headers,
            use_file_headers=use_file_headers,
        )
    except Exception as exc:  # noqa: BLE001
        log(f"[cpa-health] 测活模块异常: {exc}")
        return {
            "ok": False,
            "health_failed": True,
            "error": f"测活异常: {exc}",
            "message": str(exc),
            "path": str(path),
        }

    if ok:
        log(f"[cpa-health] 测活通过: {path.name}")
        return {"ok": True, "path": str(path), "message": message}

    log(f"[cpa-health] 测活失败，放弃推送: {path.name} -> {message}")
    # optional isolate
    if bool(config.get("cpa_health_check_isolate_invalid", False)):
        try:
            invalid_dir = path.parent / "invalid"
            invalid_dir.mkdir(parents=True, exist_ok=True)
            dest = invalid_dir / path.name
            if path.resolve() != dest.resolve():
                shutil.move(str(path), str(dest))
                path = dest
                log(f"[cpa-health] 已隔离无效文件 -> {dest}")
        except Exception as exc:  # noqa: BLE001
            log(f"[cpa-health] 隔离无效文件失败: {exc}")

    return {
        "ok": False,
        "health_failed": True,
        "error": f"测活失败: {message}",
        "message": message,
        "path": str(path),
    }



def _response_preview_text(text: str, *, limit: int = 240) -> str:
    """Short error preview; omit HTML/XML bodies so logs stay readable."""
    raw = str(text or "").strip()
    if not raw:
        return ""
    lower = raw[:240].lower()
    if "<!doctype" in lower or "<html" in lower or raw.lstrip().startswith("<"):
        tag = "html/xml"
        if "<!doctype" in lower:
            tag = "DOCTYPE html/xml"
        return f"[{tag} body omitted, len={len(raw)}]"
    return raw[:limit]


def upload_cpa_auth_to_cloud(
    path_value: str | Path | None,
    config: dict,
    log: Callable[[str], None],
) -> dict:
    """Upload one generated CPA auth JSON file to a remote CPA management API."""
    if not bool(config.get("cpa_cloud_upload_enabled", False)):
        return {"ok": False, "skipped": True, "reason": "disabled"}

    path = Path(path_value or "").expanduser().resolve()
    if not path.is_file():
        return {"ok": False, "error": "file_not_found", "path": str(path)}

    # 先测活，失败则不推送
    health = health_check_cpa_auth_before_upload(path, config, log)
    if not health.get("ok"):
        return {
            "ok": False,
            "health_failed": True,
            "error": health.get("error") or health.get("message") or "health_check_failed",
            "message": health.get("message") or "",
            "path": str(health.get("path") or path),
            "health": health,
        }
    if health.get("path"):
        path = Path(str(health["path"]))

    api_base = _cloud_management_base(
        str(config.get("cpa_cloud_api_base") or os.environ.get("CPA_CLOUD_API_BASE") or "")
    )
    key = _cloud_management_key(config)
    if not api_base:
        return {"ok": False, "error": "missing_api_base", "path": str(path)}
    if not key:
        return {"ok": False, "error": "missing_management_key", "path": str(path)}

    try:
        timeout = min(180, max(5, int(config.get("cpa_cloud_upload_timeout", 30))))
        retries = min(10, max(1, int(config.get("cpa_cloud_upload_retries", 3))))
    except (TypeError, ValueError):
        timeout, retries = 30, 3

    url = f"{api_base}/auth-files"
    for attempt in range(1, retries + 1):
        try:
            with path.open("rb") as file:
                response = requests.post(
                    url,
                    headers={"Authorization": f"Bearer {key}"},
                    files={"file": (path.name, file, "application/json")},
                    timeout=timeout,
                )
            preview = _response_preview_text(response.text, limit=300)
            if 200 <= response.status_code < 300:
                try:
                    payload: Any = response.json()
                except ValueError:
                    payload = {"raw": preview}
                log(f"[cloud-cpa] uploaded -> {path.name} status={response.status_code}")
                return {"ok": True, "path": str(path), "status_code": response.status_code, "response": payload}
            error = f"HTTP {response.status_code}: {preview}"
            if response.status_code not in {408, 429, 500, 502, 503, 504} or attempt == retries:
                return {"ok": False, "path": str(path), "status_code": response.status_code, "error": error}
        except requests.RequestException as exc:
            error = str(exc)
            if attempt == retries:
                return {"ok": False, "path": str(path), "error": error}
        log(f"[cloud-cpa] upload retry {attempt}/{retries}: {error}")
        time.sleep(min(2 * attempt, 8))
    return {"ok": False, "path": str(path), "error": "upload_failed"}


def _ensure_cpa_xai_on_path(tools_dir: str | Path | None = None) -> Path:
    """Put the parent of `cpa_xai` on sys.path. Default: this project root."""
    if tools_dir:
        tools = Path(tools_dir).expanduser().resolve()
    else:
        env = (os.environ.get("API_REVERSE_TOOLS") or "").strip()
        tools = Path(env).expanduser().resolve() if env else _REG_DIR
    # If user pointed at .../cpa_xai itself, use its parent
    if tools.name == "cpa_xai" and (tools / "__init__.py").is_file():
        tools = tools.parent
    if str(tools) not in sys.path:
        sys.path.insert(0, str(tools))
    return tools


def export_cookies_from_page(page: Any) -> list[dict]:
    """Best-effort export of cookies from a DrissionPage tab/browser."""
    if page is None:
        return []
    cookies = None
    for getter in (
        lambda: page.cookies(all_domains=True, all_info=True),
        lambda: page.cookies(all_domains=True),
        lambda: page.cookies(),
    ):
        try:
            cookies = getter()
            if cookies:
                break
        except TypeError:
            continue
        except Exception:
            continue
    if not cookies:
        try:
            browser = getattr(page, "browser", None)
            if browser is not None:
                cookies = browser.cookies()
        except Exception:
            cookies = None
    if isinstance(cookies, list):
        return [c for c in cookies if isinstance(c, dict)]
    return []




def export_cpa_xai_via_sso(
    email: str,
    sso: str,
    *,
    config: dict | None = None,
    log_callback: Callable[[str], None] | None = None,
) -> dict:
    """Mint CPA auth via pure HTTP SSO device-flow (no browser password login)."""
    cfg = config or {}
    log = log_callback or (lambda m: print(m, flush=True))
    if not cfg.get("cpa_export_enabled", True):
        log("[cpa] export disabled")
        return {"ok": False, "skipped": True, "reason": "disabled"}
    sso_val = (sso or "").strip()
    email = (email or "").strip()
    if not sso_val:
        return {"ok": False, "error": "missing sso"}
    if not email:
        return {"ok": False, "error": "missing email"}

    tools_dir = cfg.get("api_reverse_tools") or cfg.get("cpa_xai_parent") or None
    _ensure_cpa_xai_on_path(tools_dir)
    try:
        from cpa_xai import sso_oauth_to_cpa_auth, write_cpa_xai_auth  # type: ignore
        from cpa_xai.probe import probe_models  # type: ignore
    except Exception as e:  # noqa: BLE001
        log(f"[cpa] import cpa_xai sso oauth failed: {e}")
        return {"ok": False, "error": f"import: {e}"}

    out_dir = Path(cfg.get("cpa_auth_dir") or _DEFAULT_OUT).expanduser()
    if not out_dir.is_absolute():
        out_dir = (_REG_DIR / out_dir).resolve()
    hotload_raw = (cfg.get("cpa_hotload_dir") or "").strip()
    cpa_dir = Path(hotload_raw).expanduser() if hotload_raw else None
    if cpa_dir and not cpa_dir.is_absolute():
        cpa_dir = (_REG_DIR / cpa_dir).resolve()

    proxy = (cfg.get("cpa_proxy") or cfg.get("browser_proxy") or cfg.get("proxy") or "").strip()
    if not proxy:
        proxy = (
            os.environ.get("https_proxy")
            or os.environ.get("HTTPS_PROXY")
            or os.environ.get("http_proxy")
            or ""
        ).strip()
    base_url = cfg.get("cpa_base_url") or "https://cli-chat-proxy.grok.com/v1"
    probe = bool(cfg.get("cpa_probe_after_write", True))

    out_dir.mkdir(parents=True, exist_ok=True)
    log(f"[cpa-sso] oauth device-flow for {email} proxy={proxy or '(none)'}")
    try:
        payload = sso_oauth_to_cpa_auth(
            sso_val,
            email=email,
            proxy=proxy or None,
            base_url=base_url,
            log=lambda m: log(f"[cpa-sso] {m}"),
        )
    except Exception as e:  # noqa: BLE001
        log(f"[cpa-sso] oauth failed: {e}")
        return {"ok": False, "error": str(e), "mode": "sso_oauth"}

    path_written = write_cpa_xai_auth(out_dir, payload)
    log(f"[cpa-sso] wrote {path_written}")
    result: dict = {
        "ok": True,
        "email": email,
        "path": str(path_written),
        "mode": "sso_oauth",
        "base_url": base_url,
        "proxy": proxy,
    }

    if probe:
        _sleep_before_probe(cfg, log)
        try:
            pr = probe_models(payload["access_token"], base_url=base_url, proxy=proxy or None)
            result["probe_models"] = pr
            log(f"[cpa-sso] probe models ok={pr.get('ok')} has_grok_45={pr.get('has_grok_45')}")
            if not pr.get("has_grok_45"):
                result["ok"] = False
                result["error"] = "token ok but grok-4.5 not listed"
        except Exception as e:  # noqa: BLE001
            result["probe_error"] = str(e)
            log(f"[cpa-sso] probe failed: {e}")

    # optional hotload copy
    if bool(cfg.get("cpa_copy_to_hotload")) and cpa_dir is not None and result.get("ok"):
        try:
            cpa_dir.mkdir(parents=True, exist_ok=True)
            dest = cpa_dir / path_written.name
            dest.write_text(path_written.read_text(encoding="utf-8"), encoding="utf-8")
            result["hotload_path"] = str(dest)
            log(f"[cpa-sso] copied to hotload: {dest}")
        except Exception as e:  # noqa: BLE001
            log(f"[cpa-sso] hotload copy failed: {e}")
            result["hotload_error"] = str(e)

    return result


def export_cpa_xai_for_account(
    email: str,
    password: str,
    *,
    page: Any | None = None,
    cookies: Any | None = None,
    sso: str | None = None,
    config: dict | None = None,
    log_callback: Callable[[str], None] | None = None,
) -> dict:
    """Mint OIDC + write xai-<email>.json under register cpa_auths (and optional CPA auth-dir)."""
    cfg = config or {}
    log = log_callback or (lambda m: print(m, flush=True))

    if not cfg.get("cpa_export_enabled", True):
        log("[cpa] export disabled")
        return {"ok": False, "skipped": True, "reason": "disabled"}

    tools_dir = cfg.get("api_reverse_tools") or cfg.get("cpa_xai_parent") or None
    _ensure_cpa_xai_on_path(tools_dir)

    try:
        from cpa_xai import mint_and_export  # type: ignore
    except Exception as e:  # noqa: BLE001
        log(f"[cpa] import cpa_xai failed: {e}")
        return {"ok": False, "error": f"import: {e}"}

    out_dir = Path(cfg.get("cpa_auth_dir") or _DEFAULT_OUT).expanduser()
    if not out_dir.is_absolute():
        out_dir = (_REG_DIR / out_dir).resolve()

    hotload_raw = (cfg.get("cpa_hotload_dir") or "").strip()
    cpa_dir = Path(hotload_raw).expanduser() if hotload_raw else None
    if cpa_dir and not cpa_dir.is_absolute():
        cpa_dir = (_REG_DIR / cpa_dir).resolve()

    # Priority: cpa_proxy > browser_proxy > proxy > env. Match register browser routing.
    proxy = (cfg.get("cpa_proxy") or cfg.get("browser_proxy") or cfg.get("proxy") or "").strip()
    if not proxy:
        proxy = (
            os.environ.get("https_proxy")
            or os.environ.get("HTTPS_PROXY")
            or os.environ.get("http_proxy")
            or ""
        ).strip()
    # Default headed: headless is frequently Cloudflare-blocked on accounts.x.ai.
    # Allow overriding only for environments that explicitly accept this risk.
    requested_headless = bool(cfg.get("cpa_headless", False))
    allow_headless = str(
        cfg.get("cpa_allow_headless")
        or os.environ.get("CPA_ALLOW_HEADLESS")
        or ""
    ).strip().lower() in {"1", "true", "yes", "on"}
    headless = requested_headless and allow_headless
    if requested_headless and not allow_headless:
        log("[cpa] headless requested but disabled; use CPA_ALLOW_HEADLESS=1 to force it")
    probe = bool(cfg.get("cpa_probe_after_write", True))
    probe_chat = bool(cfg.get("cpa_probe_chat", False))
    timeout = float(cfg.get("cpa_mint_timeout_sec", 240))
    base_url = cfg.get("cpa_base_url") or "https://cli-chat-proxy.grok.com/v1"
    force_standalone_raw = cfg.get("cpa_force_standalone")
    force_standalone = bool(force_standalone_raw) if force_standalone_raw is not None else page is None
    cookie_inject = bool(cfg.get("cpa_mint_cookie_inject", True))
    reuse_browser = bool(cfg.get("cpa_mint_browser_reuse", True))
    recycle_every = int(cfg.get("cpa_mint_browser_recycle_every", 15) or 0)

    # cookies: explicit arg > page export > none
    use_cookies = cookies
    if use_cookies is None and cookie_inject and page is not None:
        use_cookies = export_cookies_from_page(page)
    if not cookie_inject:
        use_cookies = None
    else:
        # Always attach SSO cookie clones — register cookies alone often miss accounts.x.ai host
        sso_val = (sso or "").strip()
        if not sso_val and isinstance(use_cookies, list):
            for c in use_cookies:
                if isinstance(c, dict) and c.get("name") in ("sso", "sso-rw") and c.get("value"):
                    sso_val = str(c.get("value"))
                    break
        if sso_val:
            base = list(use_cookies) if isinstance(use_cookies, list) else []
            for name in ("sso", "sso-rw"):
                for dom in (".x.ai", "accounts.x.ai", ".accounts.x.ai", "auth.x.ai", "grok.com", ".grok.com"):
                    base.append({
                        "name": name,
                        "value": sso_val,
                        "domain": dom,
                        "path": "/",
                        "secure": True,
                        "httpOnly": True,
                    })
            use_cookies = base

    out_dir.mkdir(parents=True, exist_ok=True)
    log(
        f"[cpa] mint OIDC for {email} -> {out_dir} proxy={proxy or '(none)'} "
        f"cookies={len(use_cookies) if isinstance(use_cookies, list) else (1 if use_cookies else 0)} "
        f"reuse={reuse_browser}"
    )

    def _log(msg: str) -> None:
        log(f"[cpa] {msg}")

    # Prefer pure HTTP SSO OAuth when enabled (faster; no browser password login).
    prefer_sso = bool(cfg.get("cpa_prefer_sso_oauth", True))
    sso_for_oauth = (sso or "").strip()
    if not sso_for_oauth and isinstance(use_cookies, list):
        for c in use_cookies:
            if isinstance(c, dict) and c.get("name") in ("sso", "sso-rw") and c.get("value"):
                sso_for_oauth = str(c.get("value") or "").strip()
                break
    if prefer_sso and sso_for_oauth:
        log("[cpa] prefer SSO pure HTTP OAuth device-flow")
        sso_result = export_cpa_xai_via_sso(
            email,
            sso_for_oauth,
            config=cfg,
            log_callback=log,
        )
        if sso_result.get("ok") and sso_result.get("path"):
            result = dict(sso_result)
            # Align keys with browser mint path for shared post-processing below.
            if result.get("hotload_path") and not result.get("cpa_path"):
                result["cpa_path"] = result.get("hotload_path")
            # Jump to shared hotload/cloud/sub2api handling by skipping browser mint.
        else:
            log(f"[cpa] SSO OAuth failed, fallback browser mint: {sso_result.get('error') or sso_result}")
            result = None
    else:
        result = None

    if result is None:
        result = mint_and_export(
            email=email,
            password=password,
            auth_dir=out_dir,
            page=None if force_standalone else page,
            proxy=proxy or None,
            headless=headless,
            base_url=base_url,
            probe=probe,
            probe_chat=probe_chat,
            browser_timeout_sec=timeout,
            force_standalone=force_standalone,
            cookies=use_cookies,
            reuse_browser=reuse_browser,
            recycle_every=recycle_every,
            log=_log,
        )

    if result.get("ok") and result.get("path") and cfg.get("cpa_copy_to_hotload", False) and cpa_dir:
        try:
            cpa_dir.mkdir(parents=True, exist_ok=True)
            src = Path(result["path"])
            dst = cpa_dir / src.name
            shutil.copy2(src, dst)
            os.chmod(dst, 0o600)
            result["cpa_path"] = str(dst)
            log(f"[cpa] hotload copy -> {dst}")
        except Exception as e:  # noqa: BLE001
            log(f"[cpa] hotload copy failed: {e}")
            result["cpa_copy_error"] = str(e)

    # failure log under register dir
    if not result.get("ok"):
        fail_path = out_dir / "cpa_auth_failed.txt"
        with open(fail_path, "a", encoding="utf-8") as f:
            f.write(f"{email}----{result.get('error') or 'unknown'}----{int(time.time())}\n")
        if cfg.get("cpa_mint_required", False):
            raise RuntimeError(f"CPA mint required but failed: {result.get('error')}")
    elif result.get("path"):
        result["cloud_cpa_upload"] = upload_cpa_auth_to_cloud(
            result.get("cpa_path") or result["path"], cfg, log
        )
    if result.get("ok") and result.get("path") and (
        bool(cfg.get("sub2api_upload_enabled", False))
        or bool(cfg.get("sub2api_export_enabled", False))
    ):
        try:
            sub_mod = _import_cpa_to_sub2api()
            sub_res = sub_mod.export_after_cpa_result(
                result,
                config=cfg,
                log_callback=log,
            )
            result["sub2api"] = sub_res
        except Exception as e:  # noqa: BLE001
            log(f"[sub2api] export failed: {e}")
            result["sub2api_error"] = str(e)

    return result
