
from __future__ import annotations

import json
import random
import re
import string
import threading
import time
from email import policy
from email.parser import BytesParser
from html import unescape
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

try:
    from curl_cffi import requests as curl_requests
except ImportError:
    curl_requests = None

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# ============================================================
# 临时邮箱配置（从 config.json 加载）
# ============================================================

_config_path = Path(__file__).parent / "config.json"
_conf: Dict[str, Any] = {}
if _config_path.exists():
    with _config_path.open("r", encoding="utf-8") as _f:
        _conf = json.load(_f)

TEMP_MAIL_API_BASE = str(
    _conf.get("temp_mail_api_base")
    or _conf.get("duckmail_api_base")
    or ""
)
TEMP_MAIL_ADMIN_PASSWORD = str(
    _conf.get("temp_mail_admin_password")
    or _conf.get("duckmail_api_key")
    or _conf.get("duckmail_bearer")
    or ""
)
TEMP_MAIL_DOMAIN = str(_conf.get("temp_mail_domain") or _conf.get("duckmail_domain") or "")
# Domain pool: list or comma-separated string; also accepts nested defaultDomains
TEMP_MAIL_DOMAINS_RAW = _conf.get("temp_mail_domains")
if TEMP_MAIL_DOMAINS_RAW in (None, ""):
    TEMP_MAIL_DOMAINS_RAW = _conf.get("defaultDomains") or ""
TEMP_MAIL_DOMAIN_PICK = str(_conf.get("temp_mail_domain_pick") or "round_robin").strip().lower()
TEMP_MAIL_DOMAINS_REMOVED_RAW = (
    _conf.get("temp_mail_domains_removed")
    or _conf.get("temp_mail_domains_disabled")
    or ""
)
TEMP_MAIL_SITE_PASSWORD = str(_conf.get("temp_mail_site_password", ""))
PROXY = str(_conf.get("proxy", ""))
TEMP_MAIL_PROVIDER = str(_conf.get("temp_mail_provider") or "").strip().lower()

_domain_rr_lock = threading.Lock()
_domain_rr_index = 0

EMAIL_PROVIDER = str(
    _conf.get("email_provider")
    or _conf.get("temp_mail_provider")
    or _conf.get("mail_provider")
    or ""
).strip().lower()

# Outmail client (Outlook pool + anonymous temp mailbox)
try:
    from outmail_client import (  # type: ignore
        configure as _outmail_configure,
        get_email_provider as _outmail_get_provider,
        outmail_get_email_and_token,
        outmail_get_oai_code,
        _outmail_is_provider,
        outmail_cleanup_mailbox,
        outmail_decode_token,
    )
    _outmail_configure(_conf, merge=False)
    _HAS_OUTMAIL = True
    _outmail_import_exc = None
except Exception as _exc:  # pragma: no cover
    _HAS_OUTMAIL = False
    _outmail_import_exc = _exc

    def _outmail_is_provider(provider=None):  # type: ignore
        p = str(provider or EMAIL_PROVIDER or TEMP_MAIL_PROVIDER or "").strip().lower()
        return p in {"outmail", "outlook", "outlookemail"}


# ============================================================
# 适配层：为 DrissionPage_example.py 提供简单接口
# ============================================================

_temp_email_cache: Dict[str, str] = {}


def get_email_provider() -> str:
    """Active mail provider: outmail | duckmail | generic (cloudflare_temp_email)."""
    if _HAS_OUTMAIL:
        try:
            p = str(_outmail_get_provider() or "").strip().lower()
            if p:
                return p
        except Exception:
            pass
    return (EMAIL_PROVIDER or TEMP_MAIL_PROVIDER or "").strip().lower()


def reload_mail_config(config=None):
    """Reload config for long-running workers (console task config.json)."""
    global _conf, TEMP_MAIL_API_BASE, TEMP_MAIL_ADMIN_PASSWORD, TEMP_MAIL_DOMAIN
    global TEMP_MAIL_DOMAINS_RAW, TEMP_MAIL_DOMAIN_PICK, TEMP_MAIL_DOMAINS_REMOVED_RAW
    global TEMP_MAIL_SITE_PASSWORD, PROXY, TEMP_MAIL_PROVIDER, EMAIL_PROVIDER
    if config is None:
        if _config_path.exists():
            with _config_path.open("r", encoding="utf-8") as f:
                config = json.load(f)
        else:
            config = {}
    _conf = dict(config or {})
    TEMP_MAIL_API_BASE = str(
        _conf.get("temp_mail_api_base") or _conf.get("duckmail_api_base") or ""
    )
    TEMP_MAIL_ADMIN_PASSWORD = str(
        _conf.get("temp_mail_admin_password")
        or _conf.get("duckmail_api_key")
        or _conf.get("duckmail_bearer")
        or ""
    )
    TEMP_MAIL_DOMAIN = str(_conf.get("temp_mail_domain") or _conf.get("duckmail_domain") or "")
    TEMP_MAIL_DOMAINS_RAW = _conf.get("temp_mail_domains")
    if TEMP_MAIL_DOMAINS_RAW in (None, ""):
        TEMP_MAIL_DOMAINS_RAW = _conf.get("defaultDomains") or ""
    TEMP_MAIL_DOMAIN_PICK = str(_conf.get("temp_mail_domain_pick") or "round_robin").strip().lower()
    TEMP_MAIL_DOMAINS_REMOVED_RAW = (
        _conf.get("temp_mail_domains_removed")
        or _conf.get("temp_mail_domains_disabled")
        or ""
    )
    TEMP_MAIL_SITE_PASSWORD = str(_conf.get("temp_mail_site_password", ""))
    PROXY = str(_conf.get("proxy", ""))
    TEMP_MAIL_PROVIDER = str(_conf.get("temp_mail_provider") or "").strip().lower()
    EMAIL_PROVIDER = str(
        _conf.get("email_provider")
        or _conf.get("temp_mail_provider")
        or _conf.get("mail_provider")
        or ""
    ).strip().lower()
    if _HAS_OUTMAIL:
        _outmail_configure(_conf, merge=False)


def get_email_and_token():
    """获取邮箱与 token (email, mail_token)；outmail 走账号池/匿名邮箱。"""
    provider = get_email_provider()
    if _outmail_is_provider(provider):
        if not _HAS_OUTMAIL:
            raise Exception(f"outmail 模块不可用: {_outmail_import_exc}")
        try:
            if _config_path.exists():
                with _config_path.open("r", encoding="utf-8") as f:
                    _outmail_configure(json.load(f), merge=True)
        except Exception:
            pass
        email, token = outmail_get_email_and_token()
        if email and token:
            _temp_email_cache[email] = token
            return email, token
        return None, None

    email, _password, mail_token = create_temp_email()
    if email and mail_token:
        _temp_email_cache[email] = mail_token
        return email, mail_token
    return None, None


def get_oai_code(dev_token, email, timeout=30):
    """拉取注册 OTP 验证码；outmail token 走 Outmail 拉信。"""
    token_s = str(dev_token or "")
    provider = get_email_provider()
    if _outmail_is_provider(provider) or token_s.startswith("outmail|"):
        if not _HAS_OUTMAIL:
            raise Exception(f"outmail 模块不可用: {_outmail_import_exc}")
        try:
            cfg_timeout = int((_conf.get("outmail_poll_timeout_sec") or 180))
        except (TypeError, ValueError):
            cfg_timeout = 180
        use_timeout = max(int(timeout or 0), cfg_timeout) if int(timeout or 0) <= 30 else int(timeout)
        try:
            poll_interval = int(_conf.get("outmail_poll_interval_sec") or 5)
        except (TypeError, ValueError):
            poll_interval = 5
        code = outmail_get_oai_code(
            dev_token,
            email,
            timeout=use_timeout,
            poll_interval=poll_interval,
            log_callback=lambda m: print(m, flush=True),
        )
        if code:
            return str(code).strip()
        return None

    code = wait_for_verification_code(mail_token=dev_token, timeout=timeout)
    if code:
        code = code.replace("-", "")
    return code


def cleanup_mailbox_if_needed(email, dev_token="", log=None):
    """Optional Outmail cleanup after successful registration."""
    if not _HAS_OUTMAIL:
        return
    token_s = str(dev_token or "")
    if not (token_s.startswith("outmail|") or _outmail_is_provider(get_email_provider())):
        return
    try:
        mailbox, _since, _reg, mode = outmail_decode_token(token_s, fallback_email=email)
        outmail_cleanup_mailbox(mailbox or email, mode=mode, log_callback=log)
    except Exception as exc:
        if log:
            log(f"[outmail] cleanup skipped: {exc}")


def _parse_domain_list(value: Any) -> List[str]:
    """Parse domain pool from list / comma-separated / multi-line string."""
    if value is None:
        return []
    items: List[str] = []
    if isinstance(value, (list, tuple, set)):
        for item in value:
            items.extend(_parse_domain_list(item))
    else:
        text_v = str(value).strip()
        if not text_v:
            return []
        parts = re.split(r"[,;，、\s]+", text_v)
        items = [p.strip() for p in parts if p and p.strip()]

    out: List[str] = []
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


def configured_domain_pool() -> List[str]:
    """Return configured domain pool (explicit list preferred)."""
    pool = _parse_domain_list(TEMP_MAIL_DOMAINS_RAW)
    if not pool:
        pool = _parse_domain_list(TEMP_MAIL_DOMAIN)
    removed_set = set(_parse_domain_list(TEMP_MAIL_DOMAINS_REMOVED_RAW))
    if removed_set:
        pool = [d for d in pool if d not in removed_set]
    return pool


def _pick_domain(pool: List[str]) -> str:
    """Pick one domain from pool by temp_mail_domain_pick strategy."""
    if not pool:
        return ""
    if len(pool) == 1:
        return pool[0]
    mode = (TEMP_MAIL_DOMAIN_PICK or "round_robin").lower()
    if mode in {"random", "rand", "shuffle"}:
        return random.choice(pool)
    global _domain_rr_index
    with _domain_rr_lock:
        domain = pool[_domain_rr_index % len(pool)]
        _domain_rr_index += 1
        return domain


def _detect_mail_provider(api_base: str) -> str:
    if TEMP_MAIL_PROVIDER in {"duckmail", "temp-mail", "temp_mail", "generic"}:
        return "duckmail" if TEMP_MAIL_PROVIDER == "duckmail" else "generic"
    hostname = (urlparse(api_base).hostname or "").lower()
    if "duckmail" in hostname:
        return "duckmail"
    return "generic"


def _provider_label() -> str:
    return "DuckMail" if _detect_mail_provider(TEMP_MAIL_API_BASE) == "duckmail" else "Temp Mail"

def _create_session():
    """创建请求会话（优先 curl_cffi）。"""
    if curl_requests:
        session = curl_requests.Session()
        session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "application/json",
            "Content-Type": "application/json",
        })
        if PROXY:
            session.proxies = {"http": PROXY, "https": PROXY}
        return session, True

    s = requests.Session()
    retry = Retry(total=3, backoff_factor=1, status_forcelist=[429, 500, 502, 503, 504])
    adapter = HTTPAdapter(max_retries=retry)
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/json",
        "Content-Type": "application/json",
    })
    if PROXY:
        s.proxies = {"http": PROXY, "https": PROXY}
    return s, False


def _do_request(session, use_cffi, method, url, **kwargs):
    """统一请求，curl_cffi 自动附带 impersonate。"""
    if use_cffi:
        kwargs.setdefault("impersonate", "chrome131")
    return getattr(session, method)(url, **kwargs)


def _build_headers(extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    headers: Dict[str, str] = {}
    if TEMP_MAIL_SITE_PASSWORD:
        headers["x-custom-auth"] = TEMP_MAIL_SITE_PASSWORD
    if extra:
        headers.update(extra)
    return headers


def _generate_local_part(length: int | None = None) -> str:
    """Diverse mailbox local-parts (not only [a-z0-9]{fixed})."""
    if length is None:
        length = random.randint(8, 14)
    length = max(6, min(20, int(length)))

    style = random.choices(
        ["alnum", "name_digits", "words_join", "dotted", "mixed"],
        weights=[35, 25, 15, 10, 15],
        k=1,
    )[0]
    letters = string.ascii_lowercase
    digits = string.digits
    alnum = letters + digits

    def _finish(s: str) -> str:
        s = re.sub(r"[^a-z0-9._]", "", str(s or "").lower())
        s = s.strip("._")
        if not s:
            s = "u" + "".join(random.choice(alnum) for _ in range(8))
        if not s[0].isalpha():
            s = random.choice(letters) + s[1:]
        # collapse repeated separators
        while ".." in s:
            s = s.replace("..", ".")
        while "__" in s:
            s = s.replace("__", "_")
        return s[:20]

    if style == "alnum":
        n = random.randint(8, 14)
        body = [random.choice(letters)] + [random.choice(alnum) for _ in range(n - 1)]
        return _finish("".join(body))

    if style == "name_digits":
        seeds = [
            "alex", "sam", "jay", "max", "leo", "kai", "aria", "mia", "noah", "liam",
            "owen", "ryan", "cole", "eric", "tony", "amy", "ivy", "zoe", "luke", "mark",
            "anna", "bella", "chris", "dave", "ella", "finn", "gina", "hugo", "iris",
        ]
        base = random.choice(seeds)
        sep = random.choice(["", "", ".", "_"])
        num = "".join(random.choice(digits) for _ in range(random.randint(2, 5)))
        return _finish(f"{base}{sep}{num}")

    if style == "words_join":
        a = random.choice(["blue", "fast", "cool", "soft", "dark", "bright", "quiet", "lucky", "north", "sunny"])
        b = random.choice(["fox", "wave", "leaf", "stone", "bird", "lake", "road", "moon", "star", "wind"])
        sep = random.choice(["", ".", "_"])
        tail = "".join(random.choice(digits) for _ in range(random.randint(0, 3)))
        return _finish(f"{a}{sep}{b}{tail}")

    if style == "dotted":
        left = "".join(random.choice(letters) for _ in range(random.randint(3, 6)))
        right = "".join(random.choice(alnum) for _ in range(random.randint(3, 6)))
        return _finish(f"{left}.{right}")

    # mixed
    n = random.randint(9, 15)
    body = [random.choice(letters)]
    for _ in range(n - 1):
        if random.random() < 0.08 and body[-1] not in "._":
            body.append(random.choice(["_", "."]))
        else:
            body.append(random.choice(alnum))
    while body and body[-1] in "._":
        body.pop()
    return _finish("".join(body))



def _generate_mail_password(length: int | None = None) -> str:
    # mailbox provider password (not xAI account password); vary length
    if length is None:
        length = random.randint(14, 22)
    length = max(12, min(32, int(length)))
    chars = string.ascii_letters + string.digits
    # ensure mixed case + digit
    body = [
        random.choice(string.ascii_uppercase),
        random.choice(string.ascii_lowercase),
        random.choice(string.digits),
    ]
    body += [random.choice(chars) for _ in range(length - 3)]
    random.shuffle(body)
    return "".join(body)


def _build_duckmail_headers(token: str = "") -> Dict[str, str]:
    headers: Dict[str, str] = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _extract_duckmail_token(payload: Dict[str, Any]) -> str:
    for key in ("token", "jwt", "access_token", "id_token"):
        value = payload.get(key)
        if value:
            return str(value)
    return ""


def _extract_duckmail_domain_name(item: Dict[str, Any]) -> str:
    for key in ("domain", "name", "address"):
        value = item.get(key)
        if value:
            return str(value)
    return ""


def _resolve_duckmail_domain(session, use_cffi, api_base: str) -> str:
    pool = configured_domain_pool()
    if pool:
        domain = _pick_domain(pool)
        print(
            f"[*] DuckMail domain pool pick: {domain} "
            f"(pool={len(pool)}, pick={TEMP_MAIL_DOMAIN_PICK or 'round_robin'})"
        )
        return domain

    headers = _build_duckmail_headers(TEMP_MAIL_ADMIN_PASSWORD)
    res = _do_request(
        session,
        use_cffi,
        "get",
        f"{api_base}/domains",
        params={"page": 1},
        headers=headers,
        timeout=20,
    )
    if res.status_code != 200:
        raise Exception(f"获取 DuckMail 域名失败: {res.status_code} - {res.text[:200]}")

    data = res.json()
    if not isinstance(data, dict):
        raise Exception("DuckMail 域名接口返回格式异常")

    domains = data.get("hydra:member") or data.get("data") or data.get("results") or []
    if not isinstance(domains, list) or not domains:
        raise Exception("DuckMail 域名列表为空，请在配置里显式填写 temp_mail_domain 或 temp_mail_domains")

    public_verified: List[str] = []
    verified: List[str] = []
    fallback: List[str] = []
    for item in domains:
        if not isinstance(item, dict):
            continue
        domain = _extract_duckmail_domain_name(item)
        if not domain:
            continue
        domain = str(domain).strip().lstrip("@").lower()
        fallback.append(domain)
        if item.get("isVerified") is True:
            verified.append(domain)
            if item.get("isPublic") is True or item.get("ownerId") in (None, "", 0):
                public_verified.append(domain)

    for candidates in (public_verified, verified, fallback):
        uniq: List[str] = []
        seen: set[str] = set()
        for d in candidates:
            if d and d not in seen:
                seen.add(d)
                uniq.append(d)
        if uniq:
            domain = _pick_domain(uniq)
            print(f"[*] DuckMail auto domain pick: {domain} (candidates={len(uniq)})")
            return domain
    raise Exception("DuckMail 域名列表里没有可用域名，请在配置里显式填写 temp_mail_domain 或 temp_mail_domains")


def _create_duckmail_email() -> Tuple[str, str, str]:
    api_base = TEMP_MAIL_API_BASE.rstrip("/")
    session, use_cffi = _create_session()
    create_headers = _build_duckmail_headers(TEMP_MAIL_ADMIN_PASSWORD)
    last_error = ""
    pool = configured_domain_pool()
    max_attempts = max(5, len(pool) * 2) if pool else 5

    for attempt in range(max_attempts):
        domain = _resolve_duckmail_domain(session, use_cffi, api_base)
        email_local = _generate_local_part()
        email = f"{email_local}@{domain}"
        password = _generate_mail_password()

        res = _do_request(
            session,
            use_cffi,
            "post",
            f"{api_base}/accounts",
            json={
                "address": email,
                "password": password,
                "expiresIn": 86400,
            },
            headers=create_headers,
            timeout=20,
        )
        if res.status_code in {200, 201}:
            auth_res = _do_request(
                session,
                use_cffi,
                "post",
                f"{api_base}/token",
                json={"address": email, "password": password},
                timeout=20,
            )
            if auth_res.status_code != 200:
                raise Exception(f"登录 DuckMail 邮箱失败: {auth_res.status_code} - {auth_res.text[:200]}")

            token_data = auth_res.json()
            if not isinstance(token_data, dict):
                raise Exception("DuckMail token 接口返回格式异常")

            mail_token = _extract_duckmail_token(token_data)
            if not mail_token:
                raise Exception(f"DuckMail token 接口未返回 token: {token_data}")

            print(f"[*] DuckMail 临时邮箱创建成功: {email}")
            return email, password, mail_token

        if res.status_code in {409, 422}:
            last_error = f"{res.status_code} - {res.text[:200]}"
            print(f"[!] DuckMail create conflict, retry domain/local ({attempt + 1}/{max_attempts}): {last_error}")
            continue

        last_error = f"{res.status_code} - {res.text[:200]}"
        if pool and attempt + 1 < max_attempts:
            print(f"[!] DuckMail create failed, try next domain ({attempt + 1}/{max_attempts}): {last_error}")
            continue
        raise Exception(f"创建 DuckMail 邮箱失败: {last_error}")

    raise Exception(f"创建 DuckMail 邮箱失败，重试后仍冲突: {last_error}")


def create_temp_email() -> Tuple[str, str, str]:
    """创建临时邮箱地址，返回 (email, password, mail_token)。"""
    if not TEMP_MAIL_API_BASE:
        raise Exception("temp_mail_api_base 未设置，无法创建临时邮箱")

    provider = _detect_mail_provider(TEMP_MAIL_API_BASE)
    if provider == "duckmail":
        try:
            return _create_duckmail_email()
        except Exception as e:
            raise Exception(f"DuckMail 临时邮箱创建失败: {e}")

    if not TEMP_MAIL_ADMIN_PASSWORD:
        raise Exception("temp_mail_admin_password 未设置，无法创建临时邮箱")

    pool = configured_domain_pool()
    if not pool:
        raise Exception("temp_mail_domain / temp_mail_domains 未设置，无法创建临时邮箱")

    api_base = TEMP_MAIL_API_BASE.rstrip("/")
    session, use_cffi = _create_session()
    headers = _build_headers({"x-admin-auth": TEMP_MAIL_ADMIN_PASSWORD})
    last_error = ""
    max_attempts = max(3, len(pool) * 2)

    try:
        for attempt in range(max_attempts):
            domain = _pick_domain(pool)
            email_local = _generate_local_part()
            print(
                f"[*] Temp Mail domain pool pick: {domain} "
                f"(pool={len(pool)}, pick={TEMP_MAIL_DOMAIN_PICK or 'round_robin'}, "
                f"attempt={attempt + 1}/{max_attempts})"
            )
            res = _do_request(
                session,
                use_cffi,
                "post",
                f"{api_base}/admin/new_address",
                json={
                    "name": email_local,
                    "domain": domain,
                    "enablePrefix": False,
                },
                headers=headers,
                timeout=20,
            )
            if res.status_code != 200:
                last_error = f"{res.status_code} - {res.text[:200]}"
                if attempt + 1 < max_attempts:
                    print(f"[!] Temp Mail create failed, try next domain: {last_error}")
                    continue
                raise Exception(f"创建邮箱失败: {last_error}")

            data = res.json()
            email = data.get("address") or ""
            mail_token = data.get("jwt") or ""
            password = data.get("password") or ""
            if not email or not mail_token:
                last_error = f"接口返回缺少 address/jwt: {data}"
                if attempt + 1 < max_attempts:
                    print(f"[!] Temp Mail bad response, try next domain: {last_error}")
                    continue
                raise Exception(last_error)

            print(f"[*] Temp Mail 临时邮箱创建成功: {email}")
            return email, password, mail_token

        raise Exception(f"创建邮箱失败，重试耗尽: {last_error}")
    except Exception as e:
        raise Exception(f"Temp Mail 临时邮箱创建失败: {e}")


def _fetch_duckmail_emails(mail_token: str) -> List[Dict[str, Any]]:
    api_base = TEMP_MAIL_API_BASE.rstrip("/")
    headers = _build_duckmail_headers(mail_token)
    session, use_cffi = _create_session()
    res = _do_request(
        session,
        use_cffi,
        "get",
        f"{api_base}/messages",
        params={"page": 1},
        headers=headers,
        timeout=20,
    )
    if res.status_code != 200:
        return []
    data = res.json()
    if not isinstance(data, dict):
        return []
    return data.get("hydra:member") or data.get("data") or data.get("results") or data.get("messages") or []


def fetch_emails(mail_token: str) -> List[Dict[str, Any]]:
    """获取邮件列表。"""
    if _detect_mail_provider(TEMP_MAIL_API_BASE) == "duckmail":
        try:
            return _fetch_duckmail_emails(mail_token)
        except Exception:
            return []

    try:
        api_base = TEMP_MAIL_API_BASE.rstrip("/")
        headers = _build_headers({"Authorization": f"Bearer {mail_token}"})
        session, use_cffi = _create_session()
        res = _do_request(
            session,
            use_cffi,
            "get",
            f"{api_base}/api/mails",
            params={"limit": 20, "offset": 0},
            headers=headers,
            timeout=20,
        )
        if res.status_code == 200:
            data = res.json()
            if isinstance(data, dict):
                return data.get("results") or data.get("data") or []
    except Exception:
        pass
    return []


def _normalize_message_id(msg_id: Any) -> str:
    raw = str(msg_id or "").strip()
    if raw.startswith("/"):
        return raw.rsplit("/", 1)[-1]
    return raw


def _fetch_duckmail_email_detail(mail_token: str, msg_id: str) -> Optional[Dict[str, Any]]:
    api_base = TEMP_MAIL_API_BASE.rstrip("/")
    normalized_id = _normalize_message_id(msg_id)
    headers = _build_duckmail_headers(mail_token)
    session, use_cffi = _create_session()

    res = _do_request(
        session,
        use_cffi,
        "get",
        f"{api_base}/messages/{normalized_id}",
        headers=headers,
        timeout=20,
    )
    if res.status_code != 200:
        return None

    data = res.json()
    if not isinstance(data, dict):
        return None

    if not any(data.get(key) for key in ("text", "html", "raw", "source")):
        src_res = _do_request(
            session,
            use_cffi,
            "get",
            f"{api_base}/sources/{normalized_id}",
            headers=headers,
            timeout=20,
        )
        if src_res.status_code == 200:
            src_data = src_res.json()
            if isinstance(src_data, dict):
                raw_source = src_data.get("data") or src_data.get("source") or src_data.get("raw") or ""
                if raw_source:
                    data["raw"] = raw_source
    return data


def fetch_email_detail(mail_token: str, msg_id: str) -> Optional[Dict[str, Any]]:
    """获取单封邮件详情。"""
    if _detect_mail_provider(TEMP_MAIL_API_BASE) == "duckmail":
        try:
            return _fetch_duckmail_email_detail(mail_token, msg_id)
        except Exception:
            return None

    try:
        api_base = TEMP_MAIL_API_BASE.rstrip("/")
        headers = _build_headers({"Authorization": f"Bearer {mail_token}"})
        session, use_cffi = _create_session()
        res = _do_request(
            session,
            use_cffi,
            "get",
            f"{api_base}/api/mail/{msg_id}",
            headers=headers,
            timeout=20,
        )
        if res.status_code == 200:
            data = res.json()
            if isinstance(data, dict):
                return data
    except Exception:
        pass
    return None


def wait_for_verification_code(mail_token: str, timeout: int = 120) -> Optional[str]:
    """轮询临时邮箱，等待验证码邮件。"""
    start = time.time()
    seen_ids = set()

    while time.time() - start < timeout:
        messages = fetch_emails(mail_token)
        for msg in messages:
            if not isinstance(msg, dict):
                continue
            msg_id = msg.get("id")
            if not msg_id or msg_id in seen_ids:
                continue
            seen_ids.add(msg_id)

            detail = fetch_email_detail(mail_token, str(msg_id))
            if not detail:
                continue

            content = _extract_mail_content(detail)
            code = extract_verification_code(content)
            if code:
                print(f"[*] 从 {_provider_label()} 提取到验证码: {code}")
                return code
        time.sleep(3)
    return None


def _stringify_mail_part(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        parts = [_stringify_mail_part(item) for item in value]
        return "\n".join(part for part in parts if part)
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def _extract_mail_content(detail: Dict[str, Any]) -> str:
    """兼容 text/html/raw MIME 三种内容来源。"""
    direct_parts = [
        detail.get("subject"),
        detail.get("text"),
        detail.get("html"),
        detail.get("raw"),
        detail.get("source"),
    ]
    direct_content = "\n".join(_stringify_mail_part(part) for part in direct_parts if part)
    if detail.get("text") or detail.get("html"):
        return direct_content

    raw = detail.get("raw") or detail.get("source")
    if not raw or not isinstance(raw, str):
        return direct_content
    return f"{direct_content}\n{_parse_raw_email(raw)}"


def _parse_raw_email(raw: str) -> str:
    try:
        message = BytesParser(policy=policy.default).parsebytes(raw.encode("utf-8", errors="ignore"))
    except Exception:
        return raw

    parts: List[str] = []
    subject = message.get("subject")
    if subject:
        parts.append(f"Subject: {subject}")

    if message.is_multipart():
        for part in message.walk():
            if part.get_content_maintype() == "multipart":
                continue
            disposition = (part.get_content_disposition() or "").lower()
            if disposition == "attachment":
                continue
            content = _decode_email_part(part)
            if content:
                parts.append(content)
    else:
        content = _decode_email_part(message)
        if content:
            parts.append(content)
    return "\n".join(parts)


def _decode_email_part(part) -> str:
    try:
        content = part.get_content()
        if isinstance(content, bytes):
            charset = part.get_content_charset() or "utf-8"
            content = content.decode(charset, errors="ignore")
        if not isinstance(content, str):
            content = str(content)
        if "html" in (part.get_content_type() or "").lower():
            content = _html_to_text(content)
        return content.strip()
    except Exception:
        payload = part.get_payload(decode=True)
        if isinstance(payload, bytes):
            charset = part.get_content_charset() or "utf-8"
            return payload.decode(charset, errors="ignore").strip()
    return ""


def _html_to_text(html: str) -> str:
    text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", html)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</p>", "\n", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    return unescape(re.sub(r"[ \t\r\f\v]+", " ", text)).strip()


def extract_verification_code(content: str) -> Optional[str]:
    """
    从邮件内容提取验证码。
    Grok/x.ai 格式：MM0-SF3（3位-3位字母数字混合）或 6 位纯数字。
    """
    if not content:
        return None

    # 模式 1: Grok 格式 XXX-XXX
    m = re.search(r"(?<![A-Z0-9-])([A-Z0-9]{3}-[A-Z0-9]{3})(?![A-Z0-9-])", content)
    if m:
        return m.group(1)

    # 模式 2: 带标签的验证码
    m = re.search(r"(?:verification code|验证码|your code)[:\s]*[<>\s]*([A-Z0-9]{3}-[A-Z0-9]{3})\b", content, re.IGNORECASE)
    if m:
        return m.group(1)

    # 模式 3: HTML 样式包裹
    m = re.search(r"background-color:\s*#F3F3F3[^>]*>[\s\S]*?([A-Z0-9]{3}-[A-Z0-9]{3})[\s\S]*?</p>", content)
    if m:
        return m.group(1)

    # 模式 4: Subject 行 6 位数字
    m = re.search(r"Subject:.*?(\d{6})", content)
    if m and m.group(1) != "177010":
        return m.group(1)

    # 模式 5: HTML 标签内 6 位数字
    for code in re.findall(r">\s*(\d{6})\s*<", content):
        if code != "177010":
            return code

    # 模式 6: 独立 6 位数字
    for code in re.findall(r"(?<![&#\d])(\d{6})(?![&#\d])", content):
        if code != "177010":
            return code

    return None
