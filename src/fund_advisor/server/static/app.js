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
        <div class="name">${name}</div>
        <div class="price ${cls(pct)}">${fmtNum(info.price, 2)}</div>
        <div class="pct ${cls(pct)}">${fmtPct(pct)}</div>`;
      idxBox.appendChild(div);
    });

    const meta = $("marketMeta");
    const north = m.north_money_total;
    const breadth = m.breadth || {};
    const rows = [
      tech.trend ? `<div class="row"><span>技术面</span><span>${tech.trend}</span></div>` : "",
      typeof north === "number" ? `<div class="row"><span>北向资金</span><span class="${cls(north)}">${(north / 1e8).toFixed(1)} 亿</span></div>` : "",
      typeof breadth.up_ratio === "number" ? `<div class="row"><span>上涨比例</span><span>${(breadth.up_ratio * 100).toFixed(0)}%</span></div>` : "",
      typeof breadth.limit_up === "number" ? `<div class="row"><span>涨停</span><span class="up">${breadth.limit_up}</span></div>` : "",
      typeof breadth.limit_down === "number" ? `<div class="row"><span>跌停</span><span class="down">${breadth.limit_down}</span></div>` : "",
    ].filter(Boolean).join("");
    meta.innerHTML = rows || `<div class="muted">暂无</div>`;

    const tbody = $("sectorTable").querySelector("tbody");
    const sectors = (m.sectors || []).slice().sort((a, b) => (b.main_net_flow || 0) - (a.main_net_flow || 0));
    tbody.innerHTML = sectors.map((s) => {
      const net = (s.main_net_flow || 0) / 1e8;
      return `<tr>
        <td>${s.sector || "-"}</td>
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
        <td>${f.code}</td>
        <td>${f.name || "-"}</td>
        <td>${f.theme || "-"}</td>
        <td>${fmtNav(f.last_nav)}</td>
        <td class="${cls(f.estimate_pct)}">${fmtPct(f.estimate_pct)}</td>
        <td>${typeof d.score === "number" ? d.score.toFixed(0) : "-"}</td>
        <td class="${actionClass(d.action)}">${d.action || "-"}</td>
        <td>${d.confidence || "-"}</td>
        <td class="reasons">${reasons || ""}</td>
      </tr>`;
    }).join("");
    tbody.innerHTML = rows || `<tr><td colspan="9" class="muted">基金数据加载中…</td></tr>`;
  }

  function renderSentiment(snap) {
    const s = snap.sentiment || {};
    const p = snap.policy || {};
    const box = $("sentBox");
    box.innerHTML = `
      <div class="item"><span class="label">消息面</span>${s.summary || "-"} (${typeof s.score === "number" ? s.score.toFixed(0) : "-"})</div>
      <div class="item"><span class="label">政策面</span>${p.summary || "-"} (${typeof p.score === "number" ? p.score.toFixed(0) : "-"})</div>
    `;
  }

  function renderNews(snap) {
    const list = $("newsList");
    const items = snap.news || [];
    list.innerHTML = items.slice(0, 30).map((n) => `
      <li><span class="t">${n.time || ""}</span>${n.title || ""}</li>
    `).join("") || `<li class="muted">暂无</li>`;
  }

  function renderAll(snap) {
    if (!snap) return;
    $("updatedAt").textContent = `· ${fmtTime(snap.updated_at)}`;
    renderMarket(snap);
    renderFunds(snap);
    renderSentiment(snap);
    renderNews(snap);
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

  function startSSE() {
    const es = new EventSource("/api/stream");
    es.addEventListener("hello", () => $("liveDot").classList.add("live"));
    es.addEventListener("ping", () => {});
    es.addEventListener("update", () => pullSnapshot());
    es.addEventListener("fund_update", () => pullSnapshot());
    es.onerror = () => {
      $("liveDot").classList.remove("live");
      // 浏览器会自动重连; 不必手动
    };
  }

  // ----------------------------------------------------------- chat
  function saveHistory() {
    try {
      localStorage.setItem(CHAT_KEY, JSON.stringify(chatHistory.slice(-100)));
    } catch (e) {}
  }

  function escapeHtml(s) {
    return (s || "")
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }

  // 极简 markdown: 加粗、行内代码、列表
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
            // 再拉一次 snapshot 让表格立刻反映
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
    // 轮询等结果
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
        await fetch("/api/snapshot");
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
  setInterval(pullSnapshot, 30000); // 兜底轮询
})();
