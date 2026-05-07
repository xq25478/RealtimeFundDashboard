"""Flask app 工厂

路由:
  GET  /                      → dashboard.html
  GET  /api/snapshot          → 当前完整 state JSON
  GET  /api/stream            → SSE, 推 update 事件
  POST /api/chat              → 流式聊天 (NDJSON)
  POST /api/refresh-fund/<code> → 强制重拉单只基金
  POST /api/advice            → 触发完整投资指南 (后台跑, 完成后写入 state.advice)
  GET  /api/advice            → 拿当前 advice 文本

启动 Flask 时同步启动 scheduler。
"""

from __future__ import annotations

import json
import queue
import threading
import time
from pathlib import Path
from typing import Any, Dict

from flask import Flask, Response, jsonify, render_template, request, stream_with_context

from fund_advisor.utils.config import load_config
from fund_advisor.utils.logger import get_logger
from fund_advisor.server.state import get_store
from fund_advisor.server.scheduler import start_scheduler
from fund_advisor.server.chat import stream_chat


log = get_logger("fund_advisor.app")


def create_app(config_path: str = "config/holdings.yaml") -> Flask:
    base_dir = Path(__file__).resolve().parent
    app = Flask(
        __name__,
        template_folder=str(base_dir / "templates"),
        static_folder=str(base_dir / "static"),
    )

    app.config["CONFIG_PATH"] = config_path
    app.jinja_env.auto_reload = True
    app.config["TEMPLATES_AUTO_RELOAD"] = True
    start_scheduler(config_path)

    # ---------------------------------------------------------------- routes
    @app.get("/")
    def index():
        return render_template("dashboard.html")

    @app.get("/api/snapshot")
    def api_snapshot():
        return jsonify(get_store().snapshot())

    @app.get("/api/stream")
    def api_stream():
        store = get_store()
        q = store.subscribe()

        @stream_with_context
        def gen():
            # 入场先发一次 hello, 让前端知道连接活着
            yield f"event: hello\ndata: {json.dumps({'ts': time.time()})}\n\n"
            try:
                while True:
                    try:
                        ev = q.get(timeout=15)
                        yield f"event: {ev.get('type','update')}\ndata: {json.dumps(ev, ensure_ascii=False)}\n\n"
                    except queue.Empty:
                        # 心跳, 防中间代理掐连接
                        yield f"event: ping\ndata: {json.dumps({'ts': time.time()})}\n\n"
            except GeneratorExit:
                pass
            finally:
                store.unsubscribe(q)

        return Response(
            gen(),
            mimetype="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
                "Connection": "keep-alive",
            },
        )

    @app.post("/api/chat")
    def api_chat():
        body = request.get_json(silent=True) or {}
        messages = body.get("messages") or []
        mention_codes = body.get("mention_codes") or []

        if not isinstance(messages, list) or not messages:
            return jsonify({"error": "messages 不能为空"}), 400

        @stream_with_context
        def gen():
            try:
                for chunk in stream_chat(
                    messages,
                    mention_codes=mention_codes,
                    config_path=app.config["CONFIG_PATH"],
                ):
                    yield chunk
            except Exception as e:  # noqa: BLE001
                yield json.dumps({"type": "error", "message": str(e)}, ensure_ascii=False) + "\n"

        return Response(
            gen(),
            mimetype="application/x-ndjson",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.post("/api/refresh-fund/<code>")
    def api_refresh_fund(code: str):
        from fund_advisor.cli import _summarize_fund
        cfg = load_config(app.config["CONFIG_PATH"])
        fund = next((f for f in cfg.funds if f.code == code), None)
        if fund is None:
            return jsonify({"error": f"未配置基金 {code}"}), 404
        try:
            summary, _tech, _hist = _summarize_fund(fund)
            get_store().update_fund(code, summary)
            return jsonify({"ok": True, "fund": summary})
        except Exception as e:  # noqa: BLE001
            return jsonify({"ok": False, "error": str(e)}), 500

    @app.get("/api/advice")
    def api_get_advice():
        return jsonify(get_store().get("advice") or {})

    @app.post("/api/advice")
    def api_run_advice():
        store = get_store()
        cur = store.get("advice") or {}
        if cur.get("running"):
            return jsonify({"ok": False, "error": "正在生成中"}), 409

        store.update({"advice": {**cur, "running": True, "text": "", "generated_at": 0.0}}, broadcast=True)

        def _run():
            try:
                from fund_advisor.advisor import generate_advice
                from fund_advisor.advisor.llm_client import get_model
                snap = store.snapshot()
                # 把 snapshot 里 dict 化的字段重新喂给 generate_advice
                # generate_advice 期望的对象类型 (TechnicalSignal 等) 比较严格,
                # 我们用 SimpleNamespace 包一下让 prompt.py 的 .score / .summary 访问能通过
                from types import SimpleNamespace as NS

                def _to_ns(d):
                    return NS(**(d or {})) if isinstance(d, dict) else d

                import pandas as pd

                def _records_to_df(rows):
                    return pd.DataFrame(rows) if rows else pd.DataFrame()

                market = snap.get("market") or {}
                index_histories = {
                    k: _records_to_df(v) for k, v in (market.get("index_histories") or {}).items()
                }
                fund_histories = {
                    k: _records_to_df(v) for k, v in (snap.get("fund_histories") or {}).items()
                }
                fund_holdings_in: Dict[str, Any] = {}
                for code, blob in (snap.get("fund_holdings") or {}).items():
                    fund_holdings_in[code] = {
                        "top_holdings": _records_to_df(blob.get("top_holdings")),
                        "industries": _records_to_df(blob.get("industries")),
                        "attribution": list(blob.get("attribution") or []),
                    }

                text = generate_advice(
                    market_data=market.get("indices") or {},
                    sector_data=market.get("sectors") or [],
                    fund_summaries=snap.get("funds") or [],
                    decisions=[NS(**d) for d in (snap.get("fund_decisions") or [])],
                    sentiment=_to_ns(snap.get("sentiment")),
                    policy=_to_ns(snap.get("policy")),
                    north_money=market.get("north_money_total") or 0.0,
                    market_tech=_to_ns(snap.get("market_tech")),
                    news_top=(snap.get("news") or [])[:60],
                    index_histories=index_histories,
                    fund_histories=fund_histories,
                    north_history=_records_to_df(market.get("north_history")),
                    policy_news_recent=snap.get("policy_news") or [],
                    margin_history=_records_to_df(market.get("margin_history")),
                    breadth=market.get("breadth") or {},
                    overseas=market.get("overseas") or {},
                    valuations=snap.get("valuations") or {},
                    fund_holdings=fund_holdings_in,
                    etf_premium=snap.get("etf_premium") or {},
                    data_health=snap.get("data_health") or {},
                )
                store.update({
                    "advice": {
                        "text": text,
                        "model": get_model(),
                        "generated_at": time.time(),
                        "running": False,
                    }
                })
            except Exception as e:  # noqa: BLE001
                log.error(f"advice 生成失败: {e}")
                store.update({
                    "advice": {
                        "text": "",
                        "model": "",
                        "generated_at": time.time(),
                        "running": False,
                        "error": str(e),
                    }
                })
                store.push_error("advice", str(e))

        threading.Thread(target=_run, name="advice-runner", daemon=True).start()
        return jsonify({"ok": True})

    return app
