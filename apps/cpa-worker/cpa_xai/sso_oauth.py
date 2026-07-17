"""SSO cookie ? OIDC tokens via pure HTTP device flow (no browser).

Ported from grokcli-2api/scripts/sso_to_auth_json.py for grok-register account
management: OAuth authorize, token refresh recovery, and SSO liveness.

Flow:
  1) Validate SSO by GET https://accounts.x.ai/ (must not redirect to sign-in)
  2) POST /oauth2/device/code
  3) Open verification_uri_complete with SSO session
  4) POST /oauth2/device/verify + /oauth2/device/approve (action=allow)
  5) Poll /oauth2/token with device_code ? access_token + refresh_token
"""

from __future__ import annotations

import base64
import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any, Callable

from .oauth_device import CLIENT_ID, ISSUER, SCOPE, TOKEN_URL
from .proxyutil import resolve_proxy
from .schema import DEFAULT_BASE_URL, build_cpa_xai_auth

LogFn = Callable[[str], None]

_DEVICE_FLOW_LOCK = threading.RLock()
_DEVICE_FLOW_LAST_TS = 0.0

ACCOUNTS_HOME = "https://accounts.x.ai/"
DEVICE_CODE_URL = f"{ISSUER}/oauth2/device/code"
DEVICE_VERIFY_URL = f"{ISSUER}/oauth2/device/verify"
DEVICE_APPROVE_URL = f"{ISSUER}/oauth2/device/approve"


def _noop_log(_: str) -> None:
    return None


def _device_flow_gap_sec() -> float:
    try:
        return max(0.0, float(os.getenv("GROK_REGISTER_SSO_DEVICE_GAP_SEC", "1.2") or 1.2))
    except (TypeError, ValueError):
        return 1.2


def _device_flow_retries() -> int:
    # Bulk OAuth can burst device/code; give several retries on 429/slow_down.
    try:
        return max(1, min(12, int(os.getenv("GROK_REGISTER_SSO_DEVICE_RETRIES", "6") or 6)))
    except (TypeError, ValueError):
        return 6


def _device_flow_backoff_sec(attempt: int) -> float:
    base = 1.5
    try:
        base = float(os.getenv("GROK_REGISTER_SSO_DEVICE_BACKOFF_SEC", str(base)) or base)
    except (TypeError, ValueError):
        base = 1.5
    return min(30.0, max(0.5, base * max(1, attempt)))


def _wait_device_flow_slot() -> None:
    global _DEVICE_FLOW_LAST_TS
    gap = _device_flow_gap_sec()
    with _DEVICE_FLOW_LOCK:
        now = time.time()
        wait = (_DEVICE_FLOW_LAST_TS + gap) - now
        if wait > 0:
            time.sleep(wait)
        _DEVICE_FLOW_LAST_TS = time.time()


def _http_timeout() -> float:
    try:
        return max(5.0, float(os.getenv("GROK_REGISTER_SSO_HTTP_TIMEOUT", "12") or 12))
    except (TypeError, ValueError):
        return 12.0


def _poll_interval_sec(raw: Any = None) -> float:
    env = (os.getenv("GROK_REGISTER_SSO_POLL_INTERVAL") or "").strip()
    if env:
        try:
            return max(0.2, min(10.0, float(env)))
        except ValueError:
            pass
    try:
        hinted = float(raw if raw is not None else 1)
    except (TypeError, ValueError):
        hinted = 1.0
    return max(0.4, min(hinted, 1.5))


def _is_rate_limited_payload(
    text: str | None = None,
    url: str | None = None,
    status: int | None = None,
) -> bool:
    blob = f"{status or ''} {url or ''} {text or ''}".lower()
    return any(
        k in blob
        for k in (
            "slow_down",
            "rate_limited",
            "rate limit",
            "too many",
            "429",
        )
    )


def _proxy_kwargs(proxy: str | None = None) -> dict[str, Any]:
    resolved = resolve_proxy(proxy)
    if not resolved:
        resolved = (
            os.getenv("GROK_REGISTER_XAI_PROXY")
            or os.getenv("https_proxy")
            or os.getenv("HTTPS_PROXY")
            or os.getenv("http_proxy")
            or os.getenv("HTTP_PROXY")
            or ""
        ).strip()
        if "\n" in resolved or "\r" in resolved:
            resolved = next(
                (
                    ln.strip()
                    for ln in resolved.replace("\r", "\n").split("\n")
                    if ln.strip() and not ln.strip().startswith("#")
                ),
                "",
            )
    if not resolved:
        return {}
    return {"proxies": {"http": resolved, "https": resolved}}


def b64url_decode(seg: str) -> bytes:
    seg += "=" * (-len(seg) % 4)
    return base64.urlsafe_b64decode(seg)


def decode_jwt_payload(token: str) -> dict[str, Any]:
    try:
        return json.loads(b64url_decode(token.split(".")[1]).decode("utf-8"))
    except Exception:
        return {}


def _use_curl_cffi() -> bool:
    raw = (os.getenv("GROK_REGISTER_SSO_USE_CURL_CFFI") or "1").strip().lower()
    if raw in {"0", "false", "no", "off"}:
        return False
    try:
        import curl_cffi  # noqa: F401

        return True
    except Exception:
        return False


def _new_session(proxy: str | None = None):
    """Create an HTTP session. Prefer curl_cffi chrome impersonation."""
    proxy_kw = _proxy_kwargs(proxy)
    if _use_curl_cffi():
        from curl_cffi import requests as curl_requests

        s = curl_requests.Session()
        # curl_cffi Session accepts proxies on each request; keep on object if possible.
        if proxy_kw.get("proxies"):
            try:
                s.proxies = proxy_kw["proxies"]  # type: ignore[attr-defined]
            except Exception:
                pass
        return s, True, proxy_kw

    # stdlib fallback: cookie-aware opener
    handlers: list[Any] = []
    proxies = proxy_kw.get("proxies") or {}
    if proxies:
        handlers.append(urllib.request.ProxyHandler(proxies))
    handlers.append(urllib.request.HTTPCookieProcessor())
    opener = urllib.request.build_opener(*handlers)
    return opener, False, proxy_kw


def _session_get(session: Any, use_cffi: bool, url: str, *, timeout: float, proxy_kw: dict[str, Any]):
    if use_cffi:
        return session.get(url, impersonate="chrome", timeout=timeout, **proxy_kw)
    req = urllib.request.Request(
        url,
        method="GET",
        headers={
            "Accept": "text/html,application/json",
            "User-Agent": "Mozilla/5.0 (compatible; grok-register-sso/1.0)",
        },
    )
    with session.open(req, timeout=timeout) as resp:
        body = resp.read()
        final_url = getattr(resp, "geturl", lambda: url)()
        status = getattr(resp, "status", 200) or 200

        class _Resp:
            text = body.decode("utf-8", errors="replace")
            url = final_url
            status_code = int(status)

            def json(self_inner):
                return json.loads(self_inner.text)

        return _Resp()


def _session_post_form(
    session: Any,
    use_cffi: bool,
    url: str,
    form: dict[str, str],
    *,
    timeout: float,
    proxy_kw: dict[str, Any],
    allow_redirects: bool = True,
):
    if use_cffi:
        return session.post(
            url,
            data=form,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            impersonate="chrome",
            timeout=timeout,
            allow_redirects=allow_redirects,
            **proxy_kw,
        )
    data = urllib.parse.urlencode(form).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json,text/html",
            "User-Agent": "Mozilla/5.0 (compatible; grok-register-sso/1.0)",
        },
    )
    try:
        with session.open(req, timeout=timeout) as resp:
            body = resp.read()
            final_url = getattr(resp, "geturl", lambda: url)()
            status = getattr(resp, "status", 200) or 200

            class _Resp:
                text = body.decode("utf-8", errors="replace")
                url = final_url
                status_code = int(status)

                def json(self_inner):
                    return json.loads(self_inner.text)

            return _Resp()
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")

        class _Resp:
            text = body
            url = url
            status_code = int(e.code)

            def json(self_inner):
                return json.loads(self_inner.text)

        return _Resp()


def request_device_code(
    session: Any | None = None,
    *,
    use_cffi: bool = True,
    proxy: str | None = None,
    client_id: str = CLIENT_ID,
    scope: str = SCOPE,
) -> dict[str, Any] | None:
    form = {"client_id": client_id, "scope": scope}
    timeout = _http_timeout()
    retries = _device_flow_retries()
    proxy_kw = _proxy_kwargs(proxy)
    last_err = ""
    for attempt in range(1, retries + 1):
        _wait_device_flow_slot()
        try:
            if session is not None:
                r = _session_post_form(
                    session,
                    use_cffi,
                    DEVICE_CODE_URL,
                    form,
                    timeout=timeout,
                    proxy_kw=proxy_kw,
                )
                code = int(getattr(r, "status_code", 0) or 0)
                body_text = (getattr(r, "text", None) or "")[:300]
                if code >= 400:
                    last_err = f"HTTP {code}: {body_text[:200]}"
                    if _is_rate_limited_payload(body_text, status=code) and attempt < retries:
                        time.sleep(_device_flow_backoff_sec(attempt))
                        continue
                    return None
                data = r.json()
                return data if isinstance(data, dict) else None

            # bare urllib fallback without shared session
            data_bytes = urllib.parse.urlencode(form).encode("utf-8")
            req = urllib.request.Request(
                DEVICE_CODE_URL,
                data=data_bytes,
                method="POST",
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            opener = urllib.request.build_opener()
            if proxy_kw.get("proxies"):
                opener = urllib.request.build_opener(urllib.request.ProxyHandler(proxy_kw["proxies"]))
            with opener.open(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as e:  # noqa: BLE001
            last_err = str(e)
            if attempt < retries and _is_rate_limited_payload(str(e)):
                time.sleep(_device_flow_backoff_sec(attempt))
                continue
            if attempt < retries:
                time.sleep(_device_flow_backoff_sec(attempt))
                continue
            return None
    if last_err:
        return None
    return None


def poll_token(
    device_code: str,
    interval: int | float = 1,
    expires_in: int = 1800,
    timeout: int | float = 45,
    *,
    session: Any | None = None,
    use_cffi: bool = True,
    proxy: str | None = None,
    client_id: str = CLIENT_ID,
    immediate: bool = True,
) -> dict[str, Any] | None:
    interval_f = _poll_interval_sec(interval)
    deadline = time.time() + min(float(expires_in or 1800), float(timeout or 45))
    form = {
        "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
        "client_id": client_id,
        "device_code": device_code,
    }
    http_timeout = _http_timeout()
    proxy_kw = _proxy_kwargs(proxy)
    first = True
    while time.time() < deadline:
        if not (first and immediate):
            time.sleep(interval_f)
        first = False
        try:
            if session is not None:
                r = _session_post_form(
                    session,
                    use_cffi,
                    TOKEN_URL,
                    form,
                    timeout=http_timeout,
                    proxy_kw=proxy_kw,
                )
                code = int(getattr(r, "status_code", 0) or 0)
                text = getattr(r, "text", "") or ""
                if code == 200:
                    data = r.json()
                    if isinstance(data, dict) and (data.get("access_token") or data.get("key")):
                        return data
                    continue
                # authorization_pending / slow_down keep polling
                low = text.lower()
                if "authorization_pending" in low or "slow_down" in low or code in (400, 428):
                    if "slow_down" in low:
                        interval_f = min(interval_f + 1.0, 8.0)
                    continue
                if _is_rate_limited_payload(text, status=code):
                    interval_f = min(interval_f + 1.5, 10.0)
                    continue
                continue

            data_bytes = urllib.parse.urlencode(form).encode("utf-8")
            req = urllib.request.Request(
                TOKEN_URL,
                data=data_bytes,
                method="POST",
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            opener = urllib.request.build_opener()
            if proxy_kw.get("proxies"):
                opener = urllib.request.build_opener(urllib.request.ProxyHandler(proxy_kw["proxies"]))
            with opener.open(req, timeout=http_timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                if isinstance(data, dict) and (data.get("access_token") or data.get("key")):
                    return data
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            low = body.lower()
            if "authorization_pending" in low or "slow_down" in low:
                if "slow_down" in low:
                    interval_f = min(interval_f + 1.0, 8.0)
                continue
            if e.code in (400, 428):
                continue
            continue
        except Exception:
            continue
    return None


def probe_sso_cookie(
    sso_cookie: str,
    *,
    proxy: str | None = None,
    timeout: float | None = None,
) -> dict[str, Any]:
    """Lightweight SSO liveness: accounts.x.ai must not redirect to sign-in."""
    sso_cookie = (sso_cookie or "").strip()
    if not sso_cookie:
        return {"ok": False, "alive": False, "error": "empty_sso"}
    timeout = float(timeout if timeout is not None else _http_timeout())
    session, use_cffi, proxy_kw = _new_session(proxy)
    try:
        if use_cffi:
            session.cookies.set("sso", sso_cookie, domain=".x.ai")
        else:
            # urllib cookie jar: set via Cookie header on first request
            class _CookieOpener:
                def __init__(self, opener, cookie: str):
                    self._opener = opener
                    self._cookie = cookie

                def open(self, req, timeout=None):  # noqa: A003
                    req.add_header("Cookie", f"sso={self._cookie}")
                    return self._opener.open(req, timeout=timeout)

            session = _CookieOpener(session, sso_cookie)

        r = _session_get(session, use_cffi, ACCOUNTS_HOME, timeout=timeout, proxy_kw=proxy_kw)
        final_url = str(getattr(r, "url", "") or "")
        if "sign-in" in final_url or "sign-up" in final_url:
            return {
                "ok": True,
                "alive": False,
                "url": final_url,
                "error": "sso_invalid_redirect_sign_in",
            }
        return {"ok": True, "alive": True, "url": final_url}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "alive": False, "error": str(e)[:300]}


def sso_to_token(
    sso_cookie: str,
    *,
    proxy: str | None = None,
    quiet: bool = False,
    log: LogFn | None = None,
    client_id: str = CLIENT_ID,
    scope: str = SCOPE,
) -> dict[str, Any] | None:
    """SSO cookie ? token dict (access/refresh/expires_in)."""
    log_fn = log or (_noop_log if quiet else print)
    sso_cookie = (sso_cookie or "").strip()
    if not sso_cookie:
        log_fn("sso empty")
        return None

    session, use_cffi, proxy_kw = _new_session(proxy)
    timeout = _http_timeout()

    try:
        if use_cffi:
            session.cookies.set("sso", sso_cookie, domain=".x.ai")
        else:
            class _CookieOpener:
                def __init__(self, opener, cookie: str):
                    self._opener = opener
                    self._cookie = cookie

                def open(self, req, timeout=None):  # noqa: A003
                    if not req.has_header("Cookie"):
                        req.add_header("Cookie", f"sso={self._cookie}")
                    return self._opener.open(req, timeout=timeout)

            session = _CookieOpener(session, sso_cookie)

        r = _session_get(session, use_cffi, ACCOUNTS_HOME, timeout=timeout, proxy_kw=proxy_kw)
        final_url = str(getattr(r, "url", "") or "")
        if "sign-in" in final_url or "sign-up" in final_url:
            log_fn("sso invalid (redirected to sign-in)")
            return None
        log_fn("sso valid")
    except Exception as e:  # noqa: BLE001
        log_fn(f"sso network error: {e}")
        return None

    retries = _device_flow_retries()
    for attempt in range(1, retries + 1):
        log_fn(f"device flow try {attempt}/{retries}")
        dc = request_device_code(
            session,
            use_cffi=use_cffi,
            proxy=proxy,
            client_id=client_id,
            scope=scope,
        )
        if not dc:
            if attempt < retries:
                time.sleep(_device_flow_backoff_sec(attempt))
                continue
            return None
        user_code = str(dc.get("user_code") or "").strip()
        device_code = str(dc.get("device_code") or "").strip()
        verification = str(
            dc.get("verification_uri_complete")
            or f"{dc.get('verification_uri') or 'https://accounts.x.ai/oauth2/device'}?user_code={user_code}"
        ).strip()
        log_fn(f"user_code={user_code}")

        try:
            _session_get(session, use_cffi, verification, timeout=timeout, proxy_kw=proxy_kw)
            r = _session_post_form(
                session,
                use_cffi,
                DEVICE_VERIFY_URL,
                {"user_code": user_code},
                timeout=timeout,
                proxy_kw=proxy_kw,
            )
            if int(getattr(r, "status_code", 0) or 0) >= 400:
                body = (getattr(r, "text", None) or "")[:200]
                log_fn(f"verify failed: {body}")
                if _is_rate_limited_payload(body, status=getattr(r, "status_code", None)) and attempt < retries:
                    time.sleep(_device_flow_backoff_sec(attempt))
                    continue
                return None

            r = _session_post_form(
                session,
                use_cffi,
                DEVICE_APPROVE_URL,
                {
                    "user_code": user_code,
                    "action": "allow",
                    "principal_type": "User",
                    "principal_id": "",
                },
                timeout=timeout,
                proxy_kw=proxy_kw,
                allow_redirects=True,
            )
            final_url = str(getattr(r, "url", "") or "")
            if "done" not in final_url:
                log_fn(f"approve failed: {final_url}")
                if _is_rate_limited_payload(
                    getattr(r, "text", None), final_url, getattr(r, "status_code", None)
                ) and attempt < retries:
                    time.sleep(_device_flow_backoff_sec(attempt))
                    continue
                # some deployments return 200 JSON without done URL; continue if 2xx
                if int(getattr(r, "status_code", 0) or 0) >= 400:
                    return None
            else:
                log_fn("approve ok")
        except Exception as e:  # noqa: BLE001
            log_fn(f"approve exception: {e}")
            if _is_rate_limited_payload(str(e)) and attempt < retries:
                time.sleep(_device_flow_backoff_sec(attempt))
                continue
            return None

        token = poll_token(
            device_code,
            dc.get("interval", 1),
            dc.get("expires_in", 1800),
            timeout=float(os.getenv("GROK_REGISTER_SSO_POLL_TIMEOUT", "45") or 45),
            session=session,
            use_cffi=use_cffi,
            proxy=proxy,
            client_id=client_id,
            immediate=True,
        )
        if not token:
            if attempt < retries:
                log_fn("token poll empty ? retry device flow")
                time.sleep(_device_flow_backoff_sec(attempt))
                continue
            return None
        log_fn(
            f"access_token ok expires_in={token.get('expires_in')}s"
            + (" + refresh_token" if token.get("refresh_token") else "")
        )
        return token
    return None


def token_to_cpa_auth(
    token: dict[str, Any],
    *,
    email: str = "",
    base_url: str = DEFAULT_BASE_URL,
    sso: str | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Convert OIDC token response into CPA xai auth payload."""
    access = str(token.get("access_token") or token.get("key") or "").strip()
    refresh = str(token.get("refresh_token") or "").strip()
    if not access or not refresh:
        raise ValueError("token response missing access_token/refresh_token")
    payload = decode_jwt_payload(access)
    sub = str(payload.get("sub") or payload.get("principal_id") or "").strip()
    email_val = (email or str(payload.get("email") or "")).strip()
    expires_in = token.get("expires_in")
    try:
        expires_in_i = int(expires_in) if expires_in is not None else None
    except (TypeError, ValueError):
        expires_in_i = None
    merged_extra: dict[str, Any] = {
        "auth_mode": "oidc",
        "oidc_issuer": ISSUER,
        "oidc_client_id": CLIENT_ID,
        "principal_id": str(payload.get("principal_id") or sub),
        "principal_type": str(payload.get("principal_type") or "User"),
        "oauth_source": "sso_device_flow",
    }
    if sso:
        merged_extra["sso"] = sso
    if extra:
        merged_extra.update(extra)
    return build_cpa_xai_auth(
        email=email_val,
        access_token=access,
        refresh_token=refresh,
        sub=sub or None,
        id_token=str(token.get("id_token") or "") or None,
        expires_in=expires_in_i,
        base_url=base_url,
        extra=merged_extra,
    )


def sso_oauth_to_cpa_auth(
    sso_cookie: str,
    *,
    email: str = "",
    proxy: str | None = None,
    base_url: str = DEFAULT_BASE_URL,
    log: LogFn | None = None,
) -> dict[str, Any]:
    """High-level: SSO ? CPA auth dict. Raises on failure."""
    token = sso_to_token(sso_cookie, proxy=proxy, quiet=True, log=log)
    if not isinstance(token, dict):
        raise RuntimeError("SSO OAuth ???????? token?SSO ?????????")
    return token_to_cpa_auth(token, email=email, base_url=base_url, sso=sso_cookie)
