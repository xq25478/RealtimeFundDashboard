"""轻量磁盘缓存：避免重复爬取 akshare

使用模式:
    @disk_cache(ttl=4 * 3600)
    def get_xxx(arg1, arg2): ...

或直接调用:
    data = cached(key="north_money", ttl=600, fetch=lambda: get_north_money())

缓存目录: reports/cache/
缓存文件: <key>.pkl (pickle, 跨类型通用)
"""

from __future__ import annotations

import hashlib
import os
import pickle
import time
from functools import wraps
from pathlib import Path
from typing import Any, Callable, Optional

from fund_advisor.utils.logger import get_logger

log = get_logger(__name__)

CACHE_DIR = Path("reports/cache")


def _key_to_path(key: str) -> Path:
    """安全文件名: 长 key 走 hash"""
    safe = "".join(c if c.isalnum() or c in "._-" else "_" for c in key)
    if len(safe) > 100:
        h = hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]
        safe = safe[:80] + "_" + h
    return CACHE_DIR / f"{safe}.pkl"


def cached(
    *,
    key: str,
    ttl: int,
    fetch: Callable[[], Any],
    skip: bool = False,
) -> Any:
    """读取缓存,过期则刷新.

    Args:
        key: 缓存唯一标识
        ttl: 秒, 0 表示永不命中(强制刷新)
        fetch: 缓存 miss 时调用的函数
        skip: True 时跳过缓存直接调用 fetch（不写入）
    """
    if skip or ttl <= 0 or os.environ.get("FUND_NO_CACHE") == "1":
        return fetch()

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = _key_to_path(key)

    if path.exists():
        try:
            age = time.time() - path.stat().st_mtime
            if age < ttl:
                with path.open("rb") as f:
                    return pickle.load(f)
        except Exception as e:
            log.debug(f"读取缓存 {key} 失败: {e}")

    value = fetch()

    try:
        with path.open("wb") as f:
            pickle.dump(value, f)
    except Exception as e:
        log.debug(f"写入缓存 {key} 失败: {e}")

    return value


def disk_cache(ttl: int, key_prefix: Optional[str] = None) -> Callable:
    """装饰器: 自动以 函数名+参数 作为 key 缓存"""
    def deco(fn: Callable) -> Callable:
        prefix = key_prefix or fn.__name__

        @wraps(fn)
        def wrapper(*args, **kwargs):
            arg_repr = "_".join(str(a) for a in args)
            kw_repr = "_".join(f"{k}={v}" for k, v in sorted(kwargs.items()))
            key = "__".join(p for p in (prefix, arg_repr, kw_repr) if p)
            return cached(key=key, ttl=ttl, fetch=lambda: fn(*args, **kwargs))

        return wrapper
    return deco


def clear_cache(prefix: Optional[str] = None) -> int:
    """清除缓存目录下匹配 prefix 的文件,返回删除数."""
    if not CACHE_DIR.exists():
        return 0
    n = 0
    for p in CACHE_DIR.iterdir():
        if not p.is_file():
            continue
        if prefix and not p.name.startswith(prefix):
            continue
        try:
            p.unlink()
            n += 1
        except Exception:
            pass
    return n
