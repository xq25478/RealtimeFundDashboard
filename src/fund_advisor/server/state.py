"""线程安全的实时状态存储

dashboard 后端的所有数据都先落到这里, 前端通过 /api/snapshot 拉取或 /api/stream
订阅推送. scheduler 在后台周期性刷新各个分桶字段.
"""

from __future__ import annotations

import json
import queue
import threading
import time
from typing import Any, Dict, List, Optional


class StateStore:
    """全局快照 + SSE 订阅广播"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._data: Dict[str, Any] = {
            "updated_at": 0.0,
            "market": {},               # 大盘指数 + 北向 + 板块流向 + 宽度 + 海外 + 估值 + 两融
            "market_tech": None,
            "funds": [],                # 自选基金实时估值列表
            "fund_decisions": [],       # 量化决策结果
            "fund_histories": {},       # 30 日净值序列(供图表)
            "fund_holdings": {},        # 重仓股 + 行业 + 当日归因
            "news": [],
            "policy": None,
            "sentiment": None,
            "valuations": {},
            "etf_premium": {},
            "data_health": {},
            "advice": {                 # LLM 投资指南 (按需触发)
                "text": "",
                "model": "",
                "generated_at": 0.0,
                "running": False,
            },
            "errors": [],
        }
        self._subscribers: List[queue.Queue] = []
        self._sub_lock = threading.Lock()

    # ------------------------------------------------------------------ snapshot

    def snapshot(self) -> Dict[str, Any]:
        """返回当前完整快照 (深拷贝层级安全的子集)"""
        with self._lock:
            return json.loads(json.dumps(self._data, default=_json_default))

    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            return self._data.get(key, default)

    # ------------------------------------------------------------------ updates

    def update(self, patch: Dict[str, Any], *, broadcast: bool = True) -> None:
        with self._lock:
            for k, v in patch.items():
                self._data[k] = v
            self._data["updated_at"] = time.time()
        if broadcast:
            self._broadcast({"type": "update", "keys": list(patch.keys()), "ts": self._data["updated_at"]})

    def update_fund(self, code: str, fund_payload: Dict[str, Any]) -> None:
        """局部更新单只基金 (用于 chat 重拉)"""
        with self._lock:
            funds = self._data.get("funds") or []
            replaced = False
            for i, f in enumerate(funds):
                if f.get("code") == code:
                    funds[i] = fund_payload
                    replaced = True
                    break
            if not replaced:
                funds.append(fund_payload)
            self._data["funds"] = funds
            self._data["updated_at"] = time.time()
        self._broadcast({"type": "fund_update", "code": code, "ts": self._data["updated_at"]})

    def push_error(self, stage: str, message: str) -> None:
        with self._lock:
            errs = self._data.get("errors") or []
            errs.append({"stage": stage, "message": str(message)[:300], "ts": time.time()})
            self._data["errors"] = errs[-30:]

    # ------------------------------------------------------------------ pub/sub

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=64)
        with self._sub_lock:
            self._subscribers.append(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._sub_lock:
            if q in self._subscribers:
                self._subscribers.remove(q)

    def _broadcast(self, event: Dict[str, Any]) -> None:
        with self._sub_lock:
            subs = list(self._subscribers)
        for q in subs:
            try:
                q.put_nowait(event)
            except queue.Full:
                pass


def _json_default(obj: Any) -> Any:
    """让 pandas / 决策对象 / 其它非 JSON 值能被序列化"""
    try:
        import pandas as pd
        if isinstance(obj, pd.DataFrame):
            return obj.to_dict(orient="records")
        if isinstance(obj, pd.Timestamp):
            return obj.isoformat()
        import numpy as np
        if isinstance(obj, (np.integer,)):
            return int(obj)
        if isinstance(obj, (np.floating,)):
            v = float(obj)
            if v != v or v in (float("inf"), float("-inf")):
                return None
            return v
        if isinstance(obj, np.ndarray):
            return obj.tolist()
    except ImportError:
        pass
    if hasattr(obj, "__dict__"):
        return obj.__dict__
    return str(obj)


_STORE: Optional[StateStore] = None


def get_store() -> StateStore:
    global _STORE
    if _STORE is None:
        _STORE = StateStore()
    return _STORE
