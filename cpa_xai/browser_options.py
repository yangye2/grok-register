from __future__ import annotations

import glob
import json
import os
import platform
from pathlib import Path
from typing import Any, Callable

LogFn = Callable[[str], None]
_VIRTUAL_DISPLAY = None


def _noop(_: str) -> None:
    return None


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _load_proxy_from_config(base_dir: Path) -> str:
    config_path = base_dir / "config.json"
    if not config_path.is_file():
        return ""
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except Exception:
        return ""
    if not isinstance(data, dict):
        return ""
    return str(data.get("browser_proxy") or data.get("proxy") or "").strip()


def _extension_candidates(base_dir: Path) -> list[Path]:
    candidates = [base_dir / "turnstilePatch", Path.cwd() / "turnstilePatch"]
    parents = list(base_dir.parents)
    if len(parents) > 1:
        candidates.append(parents[1] / "turnstilePatch")
    if parents:
        candidates.append(parents[0] / "turnstilePatch")
    return candidates


def _ensure_display(headless: bool, log: LogFn) -> None:
    global _VIRTUAL_DISPLAY
    if headless or os.name == "nt":
        return
    if os.environ.get("DISPLAY") and not _truthy(os.environ.get("USE_XVFB")):
        return
    try:
        from pyvirtualdisplay import Display

        _VIRTUAL_DISPLAY = Display(visible=0, size=(1920, 1080))
        _VIRTUAL_DISPLAY.start()
        log(f"register-style Xvfb display started: {os.environ.get('DISPLAY', '')!r}")
    except Exception as exc:  # noqa: BLE001
        log(f"register-style Xvfb start failed: {exc}")


def create_browser_options(
    *,
    base_dir: str | Path | None = None,
    browser_proxy: str | None = None,
    headless: bool | None = None,
    log: LogFn | None = None,
) -> Any:
    """Create ChromiumOptions using the same browser setup as the register runner."""
    from DrissionPage import ChromiumOptions

    log = log or _noop
    root = Path(base_dir or Path.cwd()).resolve()
    use_headless = _truthy(os.environ.get("GROK_REGISTER_HEADLESS")) if headless is None else bool(headless)
    _ensure_display(use_headless, log)

    opts = ChromiumOptions()
    opts.auto_port()
    for flag in (
        "--no-sandbox",
        "--disable-gpu",
        "--disable-dev-shm-usage",
        "--disable-software-rasterizer",
    ):
        opts.set_argument(flag)
    if use_headless or not os.environ.get("DISPLAY"):
        opts.set_argument("--headless=new")

    proxy = str(browser_proxy or "").strip() or _load_proxy_from_config(root)
    if proxy:
        opts.set_proxy(proxy)
        log(f"register-style browser proxy: {proxy}")

    if platform.system() == "Linux":
        playwright_chromes = glob.glob(
            os.path.expanduser("~/.cache/ms-playwright/chromium-*/chrome-linux*/chrome")
        )
        candidates = [
            *playwright_chromes,
            "/usr/bin/chromium-browser",
            "/usr/bin/chromium",
            "/usr/bin/google-chrome",
            "/usr/bin/google-chrome-stable",
        ]
        for candidate in candidates:
            if os.path.isfile(candidate):
                try:
                    opts.set_browser_path(candidate)
                    log(f"register-style browser path: {candidate}")
                except Exception:
                    pass
                break

    opts.set_timeouts(base=1)
    for extension_dir in _extension_candidates(root):
        if not extension_dir.is_dir():
            continue
        try:
            opts.add_extension(str(extension_dir))
            log(f"register-style added extension: {extension_dir}")
        except Exception as exc:  # noqa: BLE001
            log(f"register-style extension add failed: {exc}")
        break
    return opts
