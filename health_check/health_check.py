#!/usr/bin/env python3
"""CPA 授权文件测活：chat/completions 探测 access_token 是否可用。

既可作为 CLI 批量检查脚本，也可被 cpa_export / Console 在推送前调用。
无效判定：HTTP 非 200、permission-denied、超时、缺 token、文件损坏。

请求 Header 合并顺序（后者覆盖前者）：
1. 默认客户端 Header
2. 授权文件内 headers（可选）
3. 配置/调用方 extra_headers
4. 强制：Authorization / Content-Type
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Any

import requests

# ===== CLI 默认配置 =====
CPA_AUTH_DIR = os.path.expanduser("~/.cli-proxy-api")
TEST_URL = "https://cli-chat-proxy.grok.com/v1/chat/completions"
MODEL = "grok-4.5"
TIMEOUT = 15
# =======================

# 与 cpa_xai.schema.DEFAULT_CLIENT_HEADERS 对齐
DEFAULT_CLIENT_HEADERS: dict[str, str] = {
    "x-grok-client-version": "0.2.93",
    "x-xai-token-auth": "xai-grok-cli",
    "x-authenticateresponse": "authenticate-response",
    "x-grok-client-identifier": "grok-shell",
    "User-Agent": "grok-shell/0.2.93 (linux; x86_64)",
}


def parse_headers_config(value: Any) -> dict[str, str]:
    """Parse headers from dict / JSON object / multi-line ``Key: Value`` text."""
    if value is None:
        return {}
    if isinstance(value, dict):
        out: dict[str, str] = {}
        for key, raw in value.items():
            k = str(key).strip()
            if not k or raw is None:
                continue
            out[k] = str(raw).strip()
        return out
    text = str(value).strip()
    if not text:
        return {}
    # JSON object
    if text.startswith("{"):
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            data = None
        if isinstance(data, dict):
            return parse_headers_config(data)
    # multi-line Key: Value / Key=Value
    out = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("//"):
            continue
        if ":" in line:
            key, raw = line.split(":", 1)
        elif "=" in line:
            key, raw = line.split("=", 1)
        else:
            continue
        key = key.strip()
        raw = raw.strip()
        if key:
            out[key] = raw
    return out


def build_health_check_headers(
    data: dict[str, Any] | None = None,
    *,
    extra_headers: Any = None,
    use_file_headers: bool = True,
    default_headers: dict[str, str] | None = None,
) -> dict[str, str]:
    """Build request headers for health check."""
    headers: dict[str, str] = dict(default_headers or DEFAULT_CLIENT_HEADERS)

    if use_file_headers and isinstance(data, dict):
        raw_headers = data.get("headers")
        if isinstance(raw_headers, dict):
            for key, value in raw_headers.items():
                if value is None:
                    continue
                headers[str(key)] = str(value)

    for key, value in parse_headers_config(extra_headers).items():
        headers[key] = value

    token = ""
    if isinstance(data, dict):
        token = str(data.get("access_token") or "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    headers["Content-Type"] = "application/json"
    return headers


def _build_test_url(base_url: str | None = None) -> str:
    base = (base_url or "").strip().rstrip("/")
    if not base:
        return TEST_URL
    if base.endswith("/chat/completions"):
        return base
    if base.endswith("/v1"):
        return f"{base}/chat/completions"
    return f"{base}/v1/chat/completions"


def _proxies(proxy: str | None) -> dict[str, str] | None:
    proxy = (proxy or "").strip()
    if not proxy:
        return None
    return {"http": proxy, "https": proxy}


def test_cpa_auth_data(
    data: dict[str, Any],
    *,
    test_url: str | None = None,
    model: str | None = None,
    timeout: float | int | None = None,
    proxy: str | None = None,
    extra_headers: Any = None,
    use_file_headers: bool = True,
    default_headers: dict[str, str] | None = None,
) -> tuple[bool, str]:
    """测试内存中的 CPA auth JSON，返回 (is_valid, message)。"""
    if not isinstance(data, dict):
        return False, "无效的授权数据"

    token = str(data.get("access_token") or "").strip()
    if not token:
        return False, "缺少 access_token"

    headers = build_health_check_headers(
        data,
        extra_headers=extra_headers,
        use_file_headers=use_file_headers,
        default_headers=default_headers,
    )

    url = _build_test_url(test_url)
    use_model = (model or MODEL).strip() or MODEL
    try:
        use_timeout = float(TIMEOUT if timeout is None else timeout)
    except (TypeError, ValueError):
        use_timeout = float(TIMEOUT)
    use_timeout = max(3.0, min(120.0, use_timeout))

    payload = {
        "messages": [{"role": "user", "content": "Hi"}],
        "max_tokens": 1,
        "model": use_model,
    }

    try:
        resp = requests.post(
            url,
            json=payload,
            headers=headers,
            timeout=use_timeout,
            proxies=_proxies(proxy),
        )
        if resp.status_code == 200:
            return True, "OK"
        error_text = (resp.text or "")[:300]
        if (
            "permission-denied" in error_text
            or "Access to the chat endpoint is denied" in error_text
            or "PERMISSION_DENIED" in error_text
        ):
            return False, "PERMISSION_DENIED"
        return False, f"HTTP {resp.status_code}: {error_text}"
    except requests.exceptions.Timeout:
        return False, "TIMEOUT"
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


def test_cpa_auth_file(
    filepath: str | Path,
    *,
    test_url: str | None = None,
    model: str | None = None,
    timeout: float | int | None = None,
    proxy: str | None = None,
    extra_headers: Any = None,
    use_file_headers: bool = True,
    default_headers: dict[str, str] | None = None,
) -> tuple[bool, str]:
    """测试单个认证文件，返回 (is_valid, message)。"""
    path = Path(filepath).expanduser()
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as exc:
        return False, f"文件损坏: {exc}"
    if not isinstance(data, dict):
        return False, "文件内容不是 JSON 对象"
    return test_cpa_auth_data(
        data,
        test_url=test_url,
        model=model,
        timeout=timeout,
        proxy=proxy,
        extra_headers=extra_headers,
        use_file_headers=use_file_headers,
        default_headers=default_headers,
    )


def test_account(filepath: str | Path, **kwargs: Any) -> tuple[bool, str]:
    """兼容旧脚本名。"""
    return test_cpa_auth_file(filepath, **kwargs)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="批量检查 CPA 认证文件有效性")
    parser.add_argument(
        "--dir",
        default=CPA_AUTH_DIR,
        help=f"CPA auth 目录（默认: {CPA_AUTH_DIR}）",
    )
    parser.add_argument("--model", default=MODEL, help=f"测活模型（默认: {MODEL}）")
    parser.add_argument("--timeout", type=float, default=TIMEOUT, help="超时秒数")
    parser.add_argument("--url", default=TEST_URL, help="chat/completions 完整 URL 或 base")
    parser.add_argument("--proxy", default="", help="可选 HTTP 代理")
    parser.add_argument(
        "--header",
        action="append",
        default=[],
        help='额外 Header，可重复传入: "Key: Value" 或 "Key=Value"',
    )
    parser.add_argument(
        "--headers-file",
        default="",
        help="Header 文件路径（JSON 对象或多行 Key: Value）",
    )
    parser.add_argument(
        "--no-file-headers",
        action="store_true",
        help="不使用授权文件内 headers，仅默认 + 额外 Header",
    )
    parser.add_argument(
        "--no-move",
        action="store_true",
        help="失败时不移动到 invalid/ 子目录",
    )
    args = parser.parse_args(argv)

    auth_dir = os.path.expanduser(args.dir)
    if not os.path.isdir(auth_dir):
        print(f"错误：目录 {auth_dir} 不存在")
        return 1

    extra_headers: dict[str, str] = {}
    if args.headers_file:
        headers_path = Path(args.headers_file).expanduser()
        if headers_path.is_file():
            extra_headers.update(parse_headers_config(headers_path.read_text(encoding="utf-8")))
    for item in args.header or []:
        extra_headers.update(parse_headers_config(item))

    pattern1 = os.path.join(auth_dir, "grok-*.json")
    pattern2 = os.path.join(auth_dir, "xai-*.json")
    files = sorted(set(glob.glob(pattern1) + glob.glob(pattern2)))

    if not files:
        print("没有找到任何 CPA 认证文件。")
        return 0

    print(f"找到 {len(files)} 个文件，开始健康检查...")
    if extra_headers:
        print(f"额外 Header: {extra_headers}")
    valid: list[str] = []
    invalid: list[str] = []

    for f in files:
        basename = os.path.basename(f)
        print(f"测试 {basename}...", end=" ", flush=True)
        ok, msg = test_cpa_auth_file(
            f,
            test_url=args.url,
            model=args.model,
            timeout=args.timeout,
            proxy=args.proxy or None,
            extra_headers=extra_headers,
            use_file_headers=not args.no_file_headers,
        )
        if ok:
            valid.append(f)
            print("PASS")
        else:
            invalid.append(f)
            print(f"FAIL: {msg}")
        time.sleep(0.2)

    print(f"\n检查完成：有效 {len(valid)} 个，无效 {len(invalid)} 个。")

    if invalid and not args.no_move:
        invalid_dir = os.path.join(auth_dir, "invalid")
        os.makedirs(invalid_dir, exist_ok=True)
        for f in invalid:
            dest = os.path.join(invalid_dir, os.path.basename(f))
            shutil.move(f, dest)
            print(f"已移动 {os.path.basename(f)} 到 invalid/")
        print("\n无效文件已隔离到 invalid/ 子目录，CPA 将忽略它们。")
        print("你也可以直接删除 invalid/ 目录下的文件。")

    return 0 if not invalid else 2


if __name__ == "__main__":
    sys.exit(main())
