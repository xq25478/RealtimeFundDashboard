// RealtimeFundDashboard 前端: SSE + localStorage 聊天
(() => {
  const $ = (id) => document.getElementById(id);
  const fmtPct = (v) => (typeof v === "number" ? `${v >= 0 ? "+" : ""}${v.toFixed(2)}%` : "-");
  const fmtNav = (v) => (typeof v === "number" ? v.toFixed(4) : "-");
  const fmtNum = (v, d = 2) => (typeof v === "number" ? v.toFixed(d) : "-");
  const fmtTime = (ts) => (typeof ts === "number" && ts ? new Date(ts * 1000).toLocaleTimeString() : "-");
  const cls = (v) =>
    typeof v !== "number" ? "flat" : v > 0 ? "up" : v < 0 ? "down" : "flat";
  const actionClass = (a) =>
    ({ 买入: "action-buy", 加仓: "action-add", 持有: "action-hold", 减仓: "action-cut", 卖出: "action-sell" }[a] || "");

  const CHAT_KEY = "fund_chat_history";
  let chatHistory = [];
  try {
    chatHistory = JSON.parse(localStorage.getItem(CHAT_KEY) || "[]");
  } catch (e) {
    chatHistory = [];
  }

  let _currentSnap = null;
  let _holdingsActive = null;

  // ----------------------------------------------------------- badges
  function freshnessBadge(fresh) {
    if (!fresh || !fresh.label || fresh.label === "unknown") return "";
    const labelMap = {
      fresh: ["新鲜", "badge-fresh"],
      ok: ["较新", "badge-ok"],
      stale: ["偏旧", "badge-stale"],
      very_stale: ["过期", "badge-vstale"],
    };
    const [txt, klass] = labelMap[fresh.label] || [fresh.label, "badge-muted"];
    const age = fresh.age_days != null ? `${fresh.age_days}天` : "-";
    const title = `报告期 ${fresh.as_of || "未知"} · 数据源 ${fresh.source || "-"}`;
    return `<span class="badge ${klass}" title="${escapeHtml(title)}">${txt}·${age}</span>`;
  }

  function biasBadge(b) {
    if (!b || !b.n) return `<span class="badge badge-muted">样本少</span>`;
    const confMap = { high: "badge-ok", medium: "badge-stale", low: "badge-muted" };
    const k = confMap[b.confidence] || "badge-muted";
    const mae = typeof b.mae === "number" ? b.mae.toFixed(2) : "-";
    const bias = typeof b.bias === "number" ? (b.bias >= 0 ? `+${b.bias.toFixed(2)}` : b.bias.toFixed(2)) : "-";
    const title = `近 ${b.n} 天 · 平均绝对误差 ${mae}% · 有向偏差 ${bias}% (正值=估算偏高)`;
    return `<span class="badge ${k}" title="${escapeHtml(title)}">±${mae}% n${b.n}</span>`;
  }

  // ----------------------------------------------------------- sparkline
  function drawSpark(canvas, series, options = {}) {
    if (!canvas || !series || series.length < 2) return;
    const ctx = canvas.getContext("2d");
    const w = canvas.width, h = canvas.height;
    ctx.clearRect(0, 0, w, h);
    const values = series.map(p => p.v).filter(v => typeof v === "number");
    if (values.length < 2) return;
    const min = Math.min(...values), max = Math.max(...values);
    const span = max - min || 1;
    const pad = 4;
    const toX = (i) => pad + ((w - 2 * pad) * i) / (values.length - 1);
    const toY = (v) => h - pad - ((h - 2 * pad) * (v - min)) / span;

    // 零轴参考 (若跨零)
    if (min < 0 && max > 0) {
      const y0 = toY(0);
      ctx.beginPath();
      ctx.strokeStyle = "#d1d5db";
      ctx.setLineDash([2, 3]);
      ctx.moveTo(pad, y0); ctx.lineTo(w - pad, y0);
      ctx.stroke();
      ctx.setLineDash([]);
    }

    // 填充面积
    const last = values[values.length - 1];
    const rising = last >= values[0];
    const color = options.color || (rising ? "#e11d48" : "#059669");
    const soft  = options.soft  || (rising ? "rgba(225,29,72,0.10)" : "rgba(5,150,105,0.10)");

    ctx.beginPath();
    ctx.moveTo(toX(0), h - pad);
    values.forEach((v, i) => ctx.lineTo(toX(i), toY(v)));
    ctx.lineTo(toX(values.length - 1), h - pad);
    ctx.closePath();
    ctx.fillStyle = soft;
    ctx.fill();

    ctx.beginPath();
    ctx.strokeStyle = color;
    ctx.lineWidth = 1.6;
    values.forEach((v, i) => {
      if (i === 0) ctx.moveTo(toX(i), toY(v));
      else ctx.lineTo(toX(i), toY(v));
    });
    ctx.stroke();

    // 最后一点
    ctx.beginPath();
    ctx.fillStyle = color;
    ctx.arc(toX(values.length - 1), toY(last), 2.5, 0, Math.PI * 2);
    ctx.fill();
  }

  // ----------------------------------------------------------- render
  function renderMarket(snap) {
    const m = snap.market || {};
    const tech = snap.market_tech || {};
    const indices = m.indices || {};
    const idxBox = $("indices");
    idxBox.innerHTML = "";
    Object.entries(indices).forEach(([name, info]) => {
      const pct = info.change_pct;
      const div = document.createElement("div");
      div.className = "idx";
      div.innerHTML = `
        <div class="name">${escapeHtml(name)}</div>
        <div class="price ${cls(pct)}">${fmtNum(info.price, 2)}</div>
        <div class="pct ${cls(pct)}">${fmtPct(pct)}</div>`;
      idxBox.appendChild(div);
    });

    const meta = $("marketMeta");
    const north = m.north_money_total;
    const breadth = m.breadth || {};
    const rows = [
      tech.trend ? `<div class="row"><span>技术面</span><span>${escapeHtml(tech.trend)}</span></div>` : "",
      typeof tech.score === "number" ? `<div class="row"><span>大盘评分</span><span>${tech.score.toFixed(0)}</span></div>` : "",
      typeof north === "number" ? `<div class="row"><span>北向资金</span><span class="${cls(north)}">${north.toFixed(1)} 亿</span></div>` : "",
      typeof breadth.up_ratio === "number" ? `<div class="row"><span>上涨比例</span><span>${(breadth.up_ratio * 100).toFixed(0)}%</span></div>` : "",
      typeof breadth.limit_up === "number" ? `<div class="row"><span>涨停</span><span class="up">${breadth.limit_up}</span></div>` : "",
      typeof breadth.limit_down === "number" ? `<div class="row"><span>跌停</span><span class="down">${breadth.limit_down}</span></div>` : "",
      typeof breadth.median_change === "number" ? `<div class="row"><span>中位涨幅</span><span class="${cls(breadth.median_change)}">${fmtPct(breadth.median_change)}</span></div>` : "",
    ].filter(Boolean).join("");
    meta.innerHTML = rows || `<div class="muted">暂无</div>`;

    const tbody = $("sectorTable").querySelector("tbody");
    const sectors = (m.sectors || []).slice().sort((a, b) => (b.main_net_flow || 0) - (a.main_net_flow || 0));
    tbody.innerHTML = sectors.map((s) => {
      const net = (s.main_net_flow || 0) / 1e8;
      return `<tr>
        <td>${escapeHtml(s.sector || "-")}</td>
        <td class="${cls(s.change_pct)}">${fmtPct(s.change_pct)}</td>
        <td class="${cls(net)}">${net.toFixed(1)} 亿</td>
      </tr>`;
    }).join("") || `<tr><td colspan="3" class="muted">暂无数据</td></tr>`;
  }

  function renderFunds(snap) {
    const decisions = {};
    (snap.fund_decisions || []).forEach((d) => { decisions[d.fund_code] = d; });
    const tbody = $("fundsTable").querySelector("tbody");
    const rows = (snap.funds || []).map((f) => {
      const d = decisions[f.code] || {};
      const reasons = (d.reasons || []).slice(0, 2).join("; ");
      return `<tr>
        <td>${escapeHtml(f.code)}</td>
        <td>${escapeHtml(f.name || "-")}</td>
        <td>${escapeHtml(f.theme || "-")}</td>
        <td>${fmtNav(f.last_nav)}</td>
        <td class="${cls(f.estimate_pct)}">${fmtPct(f.estimate_pct)}</td>
        <td>${biasBadge(f.bias)}</td>
        <td>${freshnessBadge(f.holdings_freshness)}</td>
        <td>${typeof d.score === "number" ? d.score.toFixed(0) : "-"}</td>
        <td class="${actionClass(d.action)}">${escapeHtml(d.action || "-")}</td>
        <td>${escapeHtml(d.confidence || "-")}</td>
        <td class="reasons">${escapeHtml(reasons || "")}</td>
      </tr>`;
    }).join("");
    tbody.innerHTML = rows || `<tr><td colspan="11" class="muted">基金数据加载中…</td></tr>`;
  }

  function renderSentiment(snap) {
    const s = snap.sentiment || {};
    const p = snap.policy || {};
    const box = $("sentBox");
    box.innerHTML = `
      <div class="item"><span class="label">消息面</span>${escapeHtml(s.summary || "-")} (${typeof s.score === "number" ? s.score.toFixed(0) : "-"})</div>
      <div class="item"><span class="label">政策面</span>${escapeHtml(p.summary || "-")} (${typeof p.score === "number" ? p.score.toFixed(0) : "-"})</div>
    `;

    const list = $("policyList");
    const items = snap.policy_news || [];
    list.innerHTML = items.slice(0, 15).map((n) => `
      <li><span class="t">${escapeHtml(n.time || "")}</span>${escapeHtml(n.title || "")}</li>
    `).join("") || `<li class="muted">暂无</li>`;
  }

  function renderNews(snap) {
    const list = $("newsList");
    const items = snap.news || [];
    list.innerHTML = items.slice(0, 40).map((n) => `
      <li><span class="t">${escapeHtml(n.time || "")}</span>${escapeHtml(n.title || "")}</li>
    `).join("") || `<li class="muted">暂无</li>`;
  }

  function renderValuations(snap) {
    const vals = snap.valuations || {};
    const tbody = $("valTable").querySelector("tbody");
    const pct = (v) => typeof v === "number" ? `${(v * 100).toFixed(0)}%` : "-";
    const pctCls = (v) => typeof v !== "number" ? "flat" : v < 0.3 ? "down" : v > 0.7 ? "up" : "flat";
    const entries = Object.entries(vals);
    tbody.innerHTML = entries.map(([name, v]) => `
      <tr>
        <td>${escapeHtml(name)}</td>
        <td>${fmtNum(v.pe, 1)}</td>
        <td class="${pctCls(v.pe_percentile)}">${pct(v.pe_percentile)}</td>
        <td>${fmtNum(v.pb, 2)}</td>
        <td class="${pctCls(v.pb_percentile)}">${pct(v.pb_percentile)}</td>
      </tr>`).join("") || `<tr><td colspan="5" class="muted">暂无</td></tr>`;
  }

  function renderOverseas(snap) {
    const m = snap.market || {};
    const ov = m.overseas || {};
    const box = $("overseasBox");
    const entries = Object.entries(ov);
    box.innerHTML = entries.map(([name, info]) => {
      const pct = info.change_pct;
      return `<div class="idx">
        <div class="name">${escapeHtml(name)}</div>
        <div class="price ${cls(pct)}">${fmtNum(info.price, 2)}</div>
        <div class="pct ${cls(pct)}">${fmtPct(pct)}</div>
      </div>`;
    }).join("") || `<div class="muted">暂无</div>`;

    // 两融趋势
    const margin = m.margin_history || [];
    const series = margin.map(r => ({ v: r.balance ?? r.financing_balance ?? r.total }));
    drawSpark($("marginSpark"), series);
    const latest = margin[margin.length - 1] || {};
    const first = margin[0] || {};
    const sum = $("marginSummary");
    if (margin.length >= 2) {
      const delta = (latest.balance ?? 0) - (first.balance ?? 0);
      const days = margin.length;
      sum.innerHTML = `
        <div class="item"><span class="label">最新</span>${fmtNum((latest.balance || 0) / 10000, 2)} 万亿</div>
        <div class="item"><span class="label">${days}日变化</span><span class="${cls(delta)}">${delta.toFixed(0)} 亿</span></div>
      `;
    } else {
      sum.innerHTML = `<div class="muted">两融数据加载中…</div>`;
    }
  }

  function renderNorth(snap) {
    const m = snap.market || {};
    const hist = m.north_history || [];
    const series = hist.map(r => ({ v: r.net ?? r.north_money ?? r.total }));
    drawSpark($("northSpark"), series);
    const sum = $("northSummary");
    const total = m.north_money_total;
    if (hist.length >= 2) {
      const cumul = hist.reduce((a, r) => a + (r.net || 0), 0);
      const positive = hist.filter(r => (r.net || 0) > 0).length;
      sum.innerHTML = `
        <div class="item"><span class="label">今日</span><span class="${cls(total)}">${typeof total === "number" ? total.toFixed(1) + " 亿" : "-"}</span></div>
        <div class="item"><span class="label">累计</span><span class="${cls(cumul)}">${cumul.toFixed(0)} 亿</span></div>
        <div class="item"><span class="label">净流入天数</span>${positive} / ${hist.length}</div>
      `;
    } else {
      sum.innerHTML = `<div class="muted">北向数据加载中…</div>`;
    }
  }

  function renderHoldings(snap) {
    const holdings = snap.fund_holdings || {};
    const codes = Object.keys(holdings);
    const tabs = $("holdingTabs");
    if (!_holdingsActive || !holdings[_holdingsActive]) _holdingsActive = codes[0] || null;

    tabs.innerHTML = codes.map(code => {
      const active = code === _holdingsActive ? "active" : "";
      const name = ((snap.funds || []).find(f => f.code === code) || {}).name || code;
      return `<button class="fund-tab ${active}" data-code="${code}">${escapeHtml(code)} · ${escapeHtml(name)}</button>`;
    }).join("");

    tabs.querySelectorAll(".fund-tab").forEach(btn => {
      btn.addEventListener("click", () => {
        _holdingsActive = btn.getAttribute("data-code");
        renderHoldings(_currentSnap);
      });
    });

    const blob = holdings[_holdingsActive] || {};
    const topBody = $("topHoldingsTable").querySelector("tbody");
    const tops = (blob.top_holdings || []).slice(0, 15);
    topBody.innerHTML = tops.map((r, i) => `
      <tr>
        <td>${r.rank || i + 1}</td>
        <td>${escapeHtml(r.stock_name || "-")}</td>
        <td>${escapeHtml(r.stock_code || "-")}</td>
        <td>${typeof r.weight === "number" ? r.weight.toFixed(2) + "%" : "-"}</td>
      </tr>`).join("") || `<tr><td colspan="4" class="muted">暂无</td></tr>`;

    const indBody = $("industryTable").querySelector("tbody");
    const inds = (blob.industries || []).slice(0, 12);
    indBody.innerHTML = inds.map(r => `
      <tr>
        <td>${escapeHtml(r.industry || "-")}</td>
        <td>${typeof r.weight === "number" ? r.weight.toFixed(2) + "%" : "-"}</td>
      </tr>`).join("") || `<tr><td colspan="2" class="muted">暂无</td></tr>`;

    const attrUl = $("attributionList");
    const attrs = blob.attribution || [];
    attrUl.innerHTML = attrs.slice(0, 6).map(a => {
      const contrib = typeof a.contribution === "number" ? fmtPct(a.contribution) : "";
      return `<li>${escapeHtml(a.label || a.name || "")} <span class="muted">${contrib}</span></li>`;
    }).join("") || `<li class="muted">暂无归因数据</li>`;
  }

  function renderEtf(snap) {
    const etf = snap.etf_premium || {};
    const tbody = $("etfTable").querySelector("tbody");
    const rows = Object.entries(etf).map(([code, blob]) => {
      const prem = blob.premium;
      return `<tr>
        <td>${escapeHtml(code)}</td>
        <td>${fmtNum(blob.iopv, 4)}</td>
        <td>${fmtNum(blob.price, 4)}</td>
        <td class="${cls(prem)}">${fmtPct(prem)}</td>
      </tr>`;
    }).join("");
    tbody.innerHTML = rows || `<tr><td colspan="4" class="muted">暂无 ETF 折溢价数据</td></tr>`;
  }

  function renderHealth(snap) {
    const health = snap.data_health || {};
    const errors = snap.errors || [];
    const box = $("healthBox");
    const entries = Object.entries(health);
    box.innerHTML = entries.map(([k, v]) => {
      const klass = v === "ok" ? "badge-fresh" : v === "empty" ? "badge-stale" : "badge-vstale";
      return `<div class="health-cell"><span class="label">${escapeHtml(k)}</span><span class="badge ${klass}">${escapeHtml(String(v))}</span></div>`;
    }).join("") || `<div class="muted">暂无健康数据</div>`;

    const pill = $("healthPill");
    const okCount = entries.filter(([, v]) => v === "ok").length;
    pill.textContent = `健康度 ${okCount}/${entries.length || 0}`;

    const list = $("errorList");
    list.innerHTML = errors.slice(-20).reverse().map(e => `
      <li><span class="t">${fmtTime(e.ts)}</span><strong>${escapeHtml(e.stage || e.source || "")}</strong> · ${escapeHtml(e.message || "")}</li>
    `).join("") || `<li class="muted">暂无错误</li>`;
  }

  function renderAll(snap) {
    if (!snap) return;
    _currentSnap = snap;
    $("updatedAt").textContent = `· ${fmtTime(snap.updated_at)}`;
    renderMarket(snap);
    renderFunds(snap);
    renderSentiment(snap);
    renderNews(snap);
    renderValuations(snap);
    renderOverseas(snap);
    renderNorth(snap);
    renderHoldings(snap);
    renderEtf(snap);
    renderHealth(snap);
  }

  // ----------------------------------------------------------- pull / sse
  async function pullSnapshot() {
    try {
      const r = await fetch("/api/snapshot");
      const snap = await r.json();
      renderAll(snap);
    } catch (e) {
      console.warn("snapshot 失败", e);
    }
  }

  let _pullTimer = null;
  function schedulePull(delay = 1000) {
    if (_pullTimer) return;
    _pullTimer = setTimeout(() => {
      _pullTimer = null;
      pullSnapshot();
    }, delay);
  }

  function startSSE() {
    const es = new EventSource("/api/stream");
    es.addEventListener("hello", () => $("liveDot").classList.add("live"));
    es.addEventListener("ping", () => {});
    es.addEventListener("update", () => schedulePull());
    es.addEventListener("fund_update", () => schedulePull());
    es.onerror = () => {
      $("liveDot").classList.remove("live");
    };
  }

  // ----------------------------------------------------------- chat
  function saveHistory() {
    try {
      localStorage.setItem(CHAT_KEY, JSON.stringify(chatHistory.slice(-100)));
    } catch (e) {}
  }

  function escapeHtml(s) {
    return (s == null ? "" : String(s))
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }

  function mdToHtml(s) {
    let h = escapeHtml(s);
    h = h.replace(/```([\s\S]*?)```/g, (_m, c) => `<pre>${c}</pre>`);
    h = h.replace(/`([^`]+)`/g, "<code>$1</code>");
    h = h.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
    h = h.replace(/^- (.+)$/gm, "• $1");
    h = h.replace(/\n/g, "<br>");
    return h;
  }

  function appendMessage(role, content, meta = "") {
    const box = $("chatBox");
    const div = document.createElement("div");
    div.className = `msg ${role}`;
    if (role === "refresh") {
      div.textContent = content;
    } else {
      div.innerHTML = mdToHtml(content) + (meta ? `<div class="muted" style="font-size:11px;margin-top:4px">${meta}</div>` : "");
    }
    box.appendChild(div);
    box.scrollTop = box.scrollHeight;
    return div;
  }

  function renderHistory() {
    $("chatBox").innerHTML = "";
    chatHistory.forEach((m) => appendMessage(m.role, m.content));
  }

  function extractCodes(text) {
    const out = [];
    const re = /\b(\d{6})\b/g;
    let m;
    while ((m = re.exec(text || "")) !== null) {
      if (!out.includes(m[1])) out.push(m[1]);
    }
    return out;
  }

  async function sendChat(text) {
    const codes = extractCodes(text);
    chatHistory.push({ role: "user", content: text });
    saveHistory();
    appendMessage("user", text);

    const assistantDiv = appendMessage("assistant", "…");
    let collected = "";

    try {
      const resp = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          messages: chatHistory.slice(-20),
          mention_codes: codes,
        }),
      });
      if (!resp.ok || !resp.body) {
        assistantDiv.innerHTML = `<span class="down">请求失败: ${resp.status}</span>`;
        return;
      }
      const reader = resp.body.getReader();
      const decoder = new TextDecoder();
      let buf = "";
      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        const lines = buf.split("\n");
        buf = lines.pop() || "";
        for (const line of lines) {
          if (!line.trim()) continue;
          let ev;
          try { ev = JSON.parse(line); } catch { continue; }
          if (ev.type === "refresh") {
            appendMessage("refresh", `已实时重拉: ${(ev.codes || []).join(", ")}`);
            pullSnapshot();
          } else if (ev.type === "delta") {
            collected += ev.text;
            assistantDiv.innerHTML = mdToHtml(collected);
            $("chatBox").scrollTop = $("chatBox").scrollHeight;
          } else if (ev.type === "error") {
            assistantDiv.innerHTML = `<span class="down">出错: ${escapeHtml(ev.message || "")}</span>`;
          }
        }
      }
      if (collected) {
        chatHistory.push({ role: "assistant", content: collected });
        saveHistory();
      } else {
        assistantDiv.innerHTML = `<span class="muted">(无回复内容)</span>`;
      }
    } catch (e) {
      assistantDiv.innerHTML = `<span class="down">网络错误: ${escapeHtml(String(e))}</span>`;
    }
  }

  // ----------------------------------------------------------- advice modal
  async function openAdvice() {
    $("adviceModal").showModal();
    const r = await fetch("/api/advice");
    const a = await r.json();
    $("adviceText").innerHTML = a.text ? mdToHtml(a.text) : "尚未生成。点击右上角按钮触发。";
  }
  async function triggerAdvice() {
    const r = await fetch("/api/advice", { method: "POST" });
    const j = await r.json();
    if (!j.ok) {
      alert(j.error || "触发失败");
      return;
    }
    $("adviceModal").showModal();
    $("adviceText").textContent = "正在调用 Claude 生成... 完成后会自动刷新。";
    const poll = setInterval(async () => {
      const rr = await fetch("/api/advice");
      const a = await rr.json();
      if (a && !a.running && (a.text || a.error)) {
        clearInterval(poll);
        $("adviceText").innerHTML = a.error
          ? `<span class="down">出错: ${escapeHtml(a.error)}</span>`
          : mdToHtml(a.text);
      }
    }, 3000);
  }

  // ----------------------------------------------------------- init
  function bind() {
    $("chatForm").addEventListener("submit", (e) => {
      e.preventDefault();
      const v = $("chatInput").value.trim();
      if (!v) return;
      $("chatInput").value = "";
      sendChat(v);
    });
    $("chatClear").addEventListener("click", () => {
      if (!confirm("清空本机聊天记录?")) return;
      chatHistory = [];
      saveHistory();
      renderHistory();
    });
    $("refreshBtn").addEventListener("click", async () => {
      $("refreshBtn").disabled = true;
      try {
        await pullSnapshot();
      } finally {
        $("refreshBtn").disabled = false;
      }
    });
    $("adviceBtn").addEventListener("click", triggerAdvice);
    $("adviceClose").addEventListener("click", () => $("adviceModal").close());
  }

  renderHistory();
  bind();
  pullSnapshot();
  startSSE();
  setInterval(pullSnapshot, 30000);
})();
