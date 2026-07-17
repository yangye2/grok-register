"""Token refresh + account liveness for CPA xAI auth files.

Aligned with grokcli-2api:
  - refresh_token grant against auth.x.ai/oauth2/token
  - permanent invalid_grant / revoked handling
  - optional SSO re-auth fallback when RT is dead but SSO cookie is alive
  - chat/completions probe for end-to-end liveness
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .oauth_device import CLIENT_ID, TOKEN_URL
from .proxyutil import resolve_proxy
from .schema import (
    DEFAULT_BASE_URL,
    DEFAULT_TOKEN_ENDPOINT,
    build_cpa_xai_auth,
    expired_from_access_token,
    jwt_payload,
)
from .writer import write_cpa_xai_auth

LogFn = Callable[[str], None]


def _noop_log(_: str) -> None:
    return None


class RefreshRevokedError(ValueError):
    """Refresh token permanently rejected by the IdP (invalid_grant / revoked)."""


def _utc_now_iso() -> str:
    return datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_expires_unix(auth: dict[str, Any]) -> float | None:
    """Return access token expiry as unix seconds if known."""
    access = str(auth.get("access_token") or "").strip()
    if access:
        try:
            pl = jwt_payload(access)
            exp = pl.get("exp")
            if exp is not None:
                return float(exp)
        except Exception:
            pass
    expired = str(auth.get("expired") or "").strip()
    if expired:
        try:
            # support both ...Z and with fractional seconds
            text = expired.replace("Z", "+00:00")
            # strip nanoseconds if present (...000000000+00:00)
            if "." in text:
                head, rest = text.split(".", 1)
                tz = ""
                frac = rest
                for sep in ("+", "-"):
                    if sep in rest[1:] if rest[:1].isdigit() else rest:
                        # keep simple: fromisoformat handles most cases
                        break
                dt = datetime.fromisoformat(text)
            else:
                dt = datetime.fromisoformat(text)
            return dt.timestamp()
        except Exception:
            pass
    expires_in = auth.get("expires_in")
    last_refresh = str(auth.get("last_refresh") or "").strip()
    if expires_in is not None and last_refresh:
        try:
            base = datetime.fromisoformat(last_refresh.replace("Z", "+00:00")).timestamp()
            return base + float(expires_in)
        except Exception:
            return None
    return None


def remaining_seconds(auth: dict[str, Any]) -> float | None:
    exp = parse_expires_unix(auth)
    if exp is None:
        return None
    return exp - time.time()


def _summarize_refresh_error_body(status_code: int, body: str) -> str:
    text = (body or "").strip()
    low = text.lower()
    if low.startswith("<!doctype html") or low.startswith("<html") or "<html" in low[:200]:
        if "cloudflare" in low or "/cdn-cgi/" in low or "cf-error" in low:
            kind = "Cloudflare HTML challenge/error"
        else:
            kind = "HTML error page"
        return f"refresh failed {status_code}: upstream returned {kind}; check outbound proxy / xAI access"
    if len(text) > 400:
        text = text[:400]
    return f"refresh failed {status_code}: {text}"


def _is_permanent_refresh_failure(status_code: int, body: str) -> bool:
    """True only for clearly permanent refresh-token rejections."""
    text = (body or "").lower()
    if status_code not in (400, 401):
        return False
    markers = (
        "invalid_grant",
        "refresh token has been revoked",
        "refresh_token has been revoked",
        "refresh token is invalid",
        "refresh_token is invalid",
        "refresh token revoked",
        "refresh_token revoked",
        "refresh token expired",
        "refresh_token expired",
        "token has been revoked",
    )
    return any(m in text for m in markers)


def _post_form(
    url: str,
    form: dict[str, str],
    *,
    proxy: str | None = None,
    timeout: float = 30.0,
) -> tuple[int, dict[str, Any] | str]:
    import urllib.error
    import urllib.parse
    import urllib.request

    data = urllib.parse.urlencode(form).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
            "User-Agent": "grok-register-token-refresh/1.0",
        },
    )
    handlers: list[Any] = []
    resolved = resolve_proxy(proxy)
    if resolved:
        handlers.append(urllib.request.ProxyHandler({"http": resolved, "https": resolved}))
    opener = urllib.request.build_opener(*handlers) if handlers else urllib.request.build_opener()
    try:
        with opener.open(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            status = int(getattr(resp, "status", 200) or 200)
            try:
                return status, json.loads(body)
            except json.JSONDecodeError:
                return status, body
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        try:
            return int(e.code), json.loads(body)
        except json.JSONDecodeError:
            return int(e.code), body


def refresh_access_token(
    auth: dict[str, Any],
    *,
    proxy: str | None = None,
    timeout: float = 30.0,
    client_id: str | None = None,
) -> dict[str, Any]:
    """Exchange refresh_token for a new access_token (+ rotated refresh_token)."""
    if auth.get("refresh_invalid"):
        raise RefreshRevokedError(
            str(auth.get("refresh_invalid_reason") or "refresh_token marked invalid")
        )
    rt = str(auth.get("refresh_token") or "").strip()
    if not rt:
        raise ValueError("no refresh_token on account")
    cid = (
        client_id
        or str(auth.get("oidc_client_id") or "").strip()
        or CLIENT_ID
    )
    endpoint = str(auth.get("token_endpoint") or DEFAULT_TOKEN_ENDPOINT or TOKEN_URL).strip() or TOKEN_URL
    status, body = _post_form(
        endpoint,
        {
            "grant_type": "refresh_token",
            "refresh_token": rt,
            "client_id": str(cid),
        },
        proxy=proxy,
        timeout=timeout,
    )
    if status >= 400:
        text = body if isinstance(body, str) else json.dumps(body, ensure_ascii=False)
        summary = _summarize_refresh_error_body(status, text)
        if _is_permanent_refresh_failure(status, text):
            raise RefreshRevokedError(summary)
        raise ValueError(summary)
    if not isinstance(body, dict) or not (body.get("access_token") or body.get("key")):
        raise ValueError("invalid refresh response")
    return body


def apply_token_response(
    auth: dict[str, Any],
    token_data: dict[str, Any],
    *,
    source: str = "refresh_token",
) -> dict[str, Any]:
    """Merge OIDC token response into an existing CPA auth payload."""
    access = str(token_data.get("access_token") or token_data.get("key") or "").strip()
    refresh = str(token_data.get("refresh_token") or auth.get("refresh_token") or "").strip()
    if not access:
        raise ValueError("token response missing access_token")
    if not refresh:
        raise ValueError("token response missing refresh_token")

    email = str(auth.get("email") or "").strip()
    try:
        expired, expires_in, sub = expired_from_access_token(access)
    except Exception:
        expired, expires_in, sub = "", int(token_data.get("expires_in") or 21600), str(auth.get("sub") or "")

    extra = {
        k: v
        for k, v in auth.items()
        if k
        not in {
            "type",
            "auth_kind",
            "access_token",
            "refresh_token",
            "token_type",
            "expires_in",
            "expired",
            "last_refresh",
            "email",
            "sub",
            "base_url",
            "token_endpoint",
            "redirect_uri",
            "disabled",
            "headers",
            "id_token",
            "refresh_invalid",
            "refresh_invalid_at",
            "refresh_invalid_reason",
        }
    }
    extra["token_renew_source"] = source
    extra["token_renewed_at"] = _utc_now_iso()
    # clear invalid marks
    extra.pop("refresh_invalid", None)
    extra.pop("refresh_invalid_at", None)
    extra.pop("refresh_invalid_reason", None)

    id_token = token_data.get("id_token") or auth.get("id_token")
    payload = build_cpa_xai_auth(
        email=email,
        access_token=access,
        refresh_token=refresh,
        sub=sub or str(auth.get("sub") or "") or None,
        id_token=str(id_token).strip() if id_token else None,
        expires_in=int(token_data.get("expires_in") or expires_in or 21600),
        expired=expired or None,
        last_refresh=_utc_now_iso(),
        base_url=str(auth.get("base_url") or DEFAULT_BASE_URL),
        token_endpoint=str(auth.get("token_endpoint") or DEFAULT_TOKEN_ENDPOINT),
        redirect_uri=str(auth.get("redirect_uri") or "http://localhost:1455/auth/callback"),
        headers=auth.get("headers") if isinstance(auth.get("headers"), dict) else None,
        disabled=bool(auth.get("disabled", False)),
        extra=extra,
    )
    return payload


def refresh_cpa_auth(
    auth: dict[str, Any],
    *,
    proxy: str | None = None,
    skew_seconds: float = 300.0,
    force: bool = False,
    sso: str | None = None,
    allow_sso_fallback: bool = True,
    log: LogFn | None = None,
) -> dict[str, Any]:
    """Refresh CPA auth if near expiry (or force). Optionally fall back to SSO OAuth.

    Returns dict:
      ok, renewed, source, auth, error?, permanent?, skipped?
    """
    log = log or _noop_log
    auth = dict(auth or {})
    remaining = remaining_seconds(auth)
    if (
        not force
        and remaining is not None
        and remaining > float(skew_seconds)
        and not auth.get("refresh_invalid")
    ):
        return {
            "ok": True,
            "renewed": False,
            "skipped": True,
            "reason": "not_near_expiry",
            "remaining_seconds": remaining,
            "auth": auth,
            "source": None,
        }

    proxy_resolved = resolve_proxy(proxy)
    try:
        token_data = refresh_access_token(auth, proxy=proxy_resolved)
        new_auth = apply_token_response(auth, token_data, source="refresh_token")
        log(
            f"refresh_token ok remaining_was={remaining if remaining is not None else 'unknown'} "
            f"new_exp={new_auth.get('expired')}"
        )
        return {
            "ok": True,
            "renewed": True,
            "skipped": False,
            "auth": new_auth,
            "source": "refresh_token",
            "remaining_seconds_before": remaining,
        }
    except RefreshRevokedError as e:
        log(f"refresh permanent failure: {e}")
        permanent = True
        err = str(e)
    except Exception as e:  # noqa: BLE001
        log(f"refresh failed: {e}")
        permanent = False
        err = str(e)

    sso_val = (sso or str(auth.get("sso") or "")).strip()
    if allow_sso_fallback and sso_val and (permanent or force or (remaining is not None and remaining <= 0)):
        try:
            from .sso_oauth import sso_oauth_to_cpa_auth

            log("trying SSO re-auth fallback")
            rebuilt = sso_oauth_to_cpa_auth(
                sso_val,
                email=str(auth.get("email") or ""),
                proxy=proxy_resolved,
                base_url=str(auth.get("base_url") or DEFAULT_BASE_URL),
                log=log,
            )
            # preserve durable fields
            for key in ("headers", "base_url", "token_endpoint", "redirect_uri", "disabled"):
                if key in auth and auth.get(key) is not None:
                    rebuilt[key] = auth[key]
            rebuilt["sso"] = sso_val
            rebuilt["token_renew_source"] = "sso"
            rebuilt["token_renewed_at"] = _utc_now_iso()
            log(f"SSO re-auth ok new_exp={rebuilt.get('expired')}")
            return {
                "ok": True,
                "renewed": True,
                "skipped": False,
                "auth": rebuilt,
                "source": "sso",
                "previous_error": err,
                "permanent": permanent,
            }
        except Exception as sso_exc:  # noqa: BLE001
            log(f"SSO re-auth failed: {sso_exc}")
            return {
                "ok": False,
                "renewed": False,
                "skipped": False,
                "error": f"refresh failed: {err}; sso fallback failed: {sso_exc}",
                "permanent": permanent,
                "auth": auth,
                "source": None,
            }

    if permanent:
        marked = dict(auth)
        marked["refresh_invalid"] = True
        marked["refresh_invalid_at"] = time.time()
        marked["refresh_invalid_reason"] = err[:300]
        marked["disabled"] = True
        return {
            "ok": False,
            "renewed": False,
            "skipped": False,
            "error": err,
            "permanent": True,
            "auth": marked,
            "source": None,
        }
    return {
        "ok": False,
        "renewed": False,
        "skipped": False,
        "error": err,
        "permanent": False,
        "auth": auth,
        "source": None,
    }


def load_cpa_auth_file(path: str | Path) -> dict[str, Any]:
    p = Path(path).expanduser()
    data = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("auth file is not a JSON object")
    return data


def save_cpa_auth_file(path: str | Path, auth: dict[str, Any]) -> Path:
    p = Path(path).expanduser()
    # keep original filename when writing refresh result
    return write_cpa_xai_auth(p.parent, auth, filename=p.name)


def refresh_cpa_auth_file(
    path: str | Path,
    *,
    proxy: str | None = None,
    skew_seconds: float = 300.0,
    force: bool = False,
    sso: str | None = None,
    allow_sso_fallback: bool = True,
    persist: bool = True,
    log: LogFn | None = None,
) -> dict[str, Any]:
    path = Path(path).expanduser()
    auth = load_cpa_auth_file(path)
    result = refresh_cpa_auth(
        auth,
        proxy=proxy,
        skew_seconds=skew_seconds,
        force=force,
        sso=sso,
        allow_sso_fallback=allow_sso_fallback,
        log=log,
    )
    if persist and result.get("renewed") and isinstance(result.get("auth"), dict):
        saved = save_cpa_auth_file(path, result["auth"])
        result["path"] = str(saved)
    else:
        result["path"] = str(path)
    return result


def check_account_liveness(
    *,
    auth: dict[str, Any] | None = None,
    auth_path: str | Path | None = None,
    sso: str | None = None,
    proxy: str | None = None,
    probe_api: bool = True,
    probe_sso: bool = True,
    auto_refresh: bool = True,
    force_refresh: bool = False,
    skew_seconds: float = 300.0,
    model: str = "grok-4.5",
    timeout: float = 15.0,
    log: LogFn | None = None,
) -> dict[str, Any]:
    """Unified account liveness:
      1) optional SSO cookie probe
      2) optional token refresh when near expiry
      3) optional chat/completions health check
    """
    log = log or _noop_log
    out: dict[str, Any] = {
        "ok": False,
        "alive": False,
        "sso": None,
        "refresh": None,
        "health": None,
        "auth_path": str(auth_path) if auth_path else "",
    }

    current = dict(auth) if isinstance(auth, dict) else None
    if current is None and auth_path:
        try:
            current = load_cpa_auth_file(auth_path)
        except Exception as e:  # noqa: BLE001
            out["error"] = f"load auth failed: {e}"
            return out

    sso_val = (sso or (str(current.get("sso") or "") if current else "")).strip()
    if probe_sso and sso_val:
        from .sso_oauth import probe_sso_cookie

        sso_res = probe_sso_cookie(sso_val, proxy=proxy)
        out["sso"] = sso_res
        log(f"sso probe alive={sso_res.get('alive')} err={sso_res.get('error') or ''}")
    elif probe_sso and not sso_val:
        out["sso"] = {"ok": False, "alive": False, "error": "no_sso", "skipped": True}

    if current is not None and auto_refresh:
        refresh_res = refresh_cpa_auth(
            current,
            proxy=proxy,
            skew_seconds=skew_seconds,
            force=force_refresh,
            sso=sso_val or None,
            allow_sso_fallback=bool(sso_val),
            log=log,
        )
        out["refresh"] = {
            k: v
            for k, v in refresh_res.items()
            if k != "auth"
        }
        if isinstance(refresh_res.get("auth"), dict):
            current = refresh_res["auth"]
            out["auth"] = current
            if auth_path and refresh_res.get("renewed"):
                try:
                    save_cpa_auth_file(auth_path, current)
                    out["auth_path"] = str(auth_path)
                except Exception as e:  # noqa: BLE001
                    log(f"persist refreshed auth failed: {e}")

    if probe_api and current is not None:
        # Prefer project health_check when available; fall back to probe_models.
        health_ok = False
        health_msg = ""
        try:
            from health_check.health_check import test_cpa_auth_data  # type: ignore

            health_ok, health_msg = test_cpa_auth_data(
                current,
                model=model,
                timeout=timeout,
                proxy=proxy,
            )
            out["health"] = {"ok": health_ok, "message": health_msg, "via": "chat_completions"}
        except Exception:
            try:
                from .probe import probe_models

                pr = probe_models(
                    str(current.get("access_token") or ""),
                    base_url=str(current.get("base_url") or DEFAULT_BASE_URL),
                    proxy=proxy,
                    timeout=timeout,
                )
                health_ok = bool(pr.get("ok") and pr.get("has_grok_45"))
                health_msg = "models ok" if health_ok else str(pr.get("error") or pr)
                out["health"] = {
                    "ok": health_ok,
                    "message": health_msg,
                    "via": "models",
                    "detail": pr,
                }
            except Exception as e:  # noqa: BLE001
                out["health"] = {"ok": False, "message": str(e), "via": "error"}
                health_ok = False
                health_msg = str(e)
        log(f"api probe ok={health_ok} msg={health_msg}")
        out["alive"] = bool(health_ok)
        out["ok"] = bool(health_ok)
        if not health_ok:
            out["error"] = health_msg
        return out

    # No API probe: consider alive if SSO alive or token not expired.
    sso_alive = bool((out.get("sso") or {}).get("alive"))
    remaining = remaining_seconds(current) if current else None
    token_alive = remaining is not None and remaining > 0
    if current is None and sso_val:
        out["alive"] = sso_alive
        out["ok"] = sso_alive
        if not sso_alive:
            out["error"] = (out.get("sso") or {}).get("error") or "sso_dead"
        return out
    out["alive"] = bool(token_alive or sso_alive)
    out["ok"] = out["alive"]
    out["remaining_seconds"] = remaining
    if not out["alive"]:
        out["error"] = "token_expired_and_sso_dead"
    return out
