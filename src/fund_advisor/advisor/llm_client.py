"""Anthropic Claude API 客户端封装

凭证优先级:
  1. 显式参数
  2. config/config.json (项目本地, 加入 .gitignore)
  3. 环境变量 (ANTHROPIC_API_KEY / ANTHROPIC_BASE_URL / ANTHROPIC_MODEL)
"""

import json
import os
from pathlib import Path
from typing import Optional, Dict, Any

try:
    import anthropic
except ImportError:
    anthropic = None

try:
    import httpx
except ImportError:
    httpx = None


DEFAULT_MODEL = "Claude-Opus-4.7"
_CONFIG_CACHE: Optional[Dict[str, Any]] = None


def _config_path() -> Path:
    return Path(__file__).resolve().parents[3] / "config" / "config.json"


def _load_config() -> Dict[str, Any]:
    global _CONFIG_CACHE
    if _CONFIG_CACHE is not None:
        return _CONFIG_CACHE
    p = _config_path()
    if p.exists():
        try:
            _CONFIG_CACHE = json.loads(p.read_text(encoding="utf-8")) or {}
        except Exception:
            _CONFIG_CACHE = {}
    else:
        _CONFIG_CACHE = {}
    return _CONFIG_CACHE


def _anthropic_cfg() -> Dict[str, Any]:
    return (_load_config().get("anthropic") or {})


def _is_truthy(s: Optional[str]) -> bool:
    return bool(s) and s.strip().lower() in ("1", "true", "yes", "on")


def _is_falsy(s: Optional[str]) -> bool:
    return bool(s) and s.strip().lower() in ("0", "false", "no", "off")


def get_client(
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    verify_ssl: Optional[bool] = None,
    timeout: float = 600.0,
) -> "anthropic.Anthropic":
    """构造 Anthropic 客户端 (config.json 优先, 其次环境变量)"""
    if anthropic is None:
        raise ImportError("未安装 anthropic SDK，请先运行: pip install anthropic")

    cfg = _anthropic_cfg()

    api_key = api_key or cfg.get("api_key") or os.getenv("ANTHROPIC_API_KEY")
    base_url = base_url or cfg.get("base_url") or os.getenv("ANTHROPIC_BASE_URL")

    if not api_key or api_key == "YOUR_ANTHROPIC_API_KEY":
        raise ValueError(
            "缺少 API Key, 请在 config/config.json 中填写 anthropic.api_key, "
            "或设置环境变量 ANTHROPIC_API_KEY"
        )

    kwargs = {"api_key": api_key, "timeout": timeout}
    if base_url:
        kwargs["base_url"] = base_url

    if verify_ssl is None:
        if isinstance(cfg.get("verify_ssl"), bool):
            verify_ssl = cfg["verify_ssl"]
        else:
            env_val = os.getenv("ANTHROPIC_VERIFY_SSL")
            if _is_falsy(env_val):
                verify_ssl = False
            elif _is_truthy(env_val):
                verify_ssl = True
            elif base_url and ("127.0.0.1" in base_url or "localhost" in base_url):
                verify_ssl = False
            else:
                verify_ssl = True

    if not verify_ssl:
        if httpx is None:
            raise ImportError("禁用 SSL 校验需要 httpx, 请先安装: pip install httpx")
        kwargs["http_client"] = httpx.Client(verify=False, timeout=timeout)

    return anthropic.Anthropic(**kwargs)


def get_model() -> str:
    return (
        _anthropic_cfg().get("model")
        or os.getenv("ANTHROPIC_MODEL")
        or DEFAULT_MODEL
    )


def get_server_config() -> Dict[str, Any]:
    """返回 dashboard server 配置 (host/port)"""
    cfg = _load_config().get("server") or {}
    return {
        "host": cfg.get("host", "127.0.0.1"),
        "port": int(cfg.get("port", 31009)),
    }
