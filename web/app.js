/* mad web board — data load + render + runtime/session controls */

(function () {
  const state = {
    report: null,
    source: "",
    tagFilter: new Set(),
    showSystem: true,
    onlyClaims: false,
    // runtime selection (mirrors Open Design)
    mode: "local_cli",
    agent: "kimi",
    model: "",
    baseUrl: "",
    apiKey: "",
    cwd: "",
    timeoutSec: 180,
    catalog: null,
    sessionPoll: null,
    // multi-session: null = latest session
    sessionId: null,
    lastConfig: null,
  };

  const $ = (id) => document.getElementById(id);

  function toast(msg, ms = 2600) {
    const el = $("toast");
    el.textContent = msg;
    el.hidden = false;
    clearTimeout(el._t);
    el._t = setTimeout(() => (el.hidden = true), ms);
  }

  function esc(s) {
    return String(s ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  // ---------------------------------------------- 中文显示层（协议名保留在 title）
  const TAG_ZH = {
    CLAIM: "主张", EVIDENCE: "证据", COUNTEREXAMPLE: "反例", DEAD_END: "死路",
    FIX: "修复", QUESTION: "疑问", CONFIRMED: "已确认", UNCONTESTED: "无人挑战",
    PROPOSAL: "提案", PLAN: "计划", BASELINE_RESULT: "基线结果",
    EXPERIMENT_RESULT: "实验结果", LEAKAGE_SUSPECTED: "泄漏嫌疑", LITERATURE: "文献",
    LEMMA_PROVED: "引理已证", VERDICT: "判决", NOTE: "备注", SYSTEM: "系统",
  };
  const STATUS_ZH = {
    open: "待定", rejected: "已拒绝", uncontested: "无人挑战",
    confirmed: "已确认", challenged: "被攻击",
  };
  const ROLE_ZH = {
    Explorer: "探索者", Skeptic: "怀疑者", Modeler: "建模者", Experimenter: "实验者",
    Reviewer: "评审", Proposer: "提案者", Scout: "查证者",
    verifier: "验证器", orchestrator: "调度器",
  };
  const SESSION_STATUS_ZH = {
    running: "进行中", done: "已完成", error: "出错", stopped: "已停止", idle: "空闲",
  };
  const STOP_ZH = [
    [/^verifier_gate_passed/, "闸门通过"],
    [/^stopped_by_user/, "用户停止"],
    [/^stall_no_new_claim/, "停滞：连续多轮无新主张"],
    [/^stagnation_no_improvement_(\d+)_rounds/, (m) => `停滞：连续 ${m[1]} 轮无改进`],
    [/^target_score_reached/, "达到目标分数"],
    [/^max_messages/, "消息数达上限"],
    [/^max_rounds/, "达到最大轮次"],
  ];
  const tagLabel = (t) => TAG_ZH[t] || t;
  const statusLabel = (s) => STATUS_ZH[s] || s;
  const authorLabel = (a) => ROLE_ZH[a] || a;
  const sessionStatusLabel = (s) => SESSION_STATUS_ZH[s] || s;
  function stopLabel(s) {
    if (!s) return s;
    for (const [re, zh] of STOP_ZH) {
      const m = s.match(re);
      if (m) return typeof zh === "function" ? zh(m) : zh;
    }
    return s;
  }
  function provenanceNote(m) {
    const p = m.metadata && m.metadata.provenance;
    if (!p) return "";
    const bits = [
      p.model && p.model !== "default" ? p.model : null,
      p.latency_ms != null ? `${Math.round(p.latency_ms)}ms` : null,
    ].filter(Boolean);
    return bits.length ? ` · ${bits.join(" · ")}` : "";
  }

  function allMessages(report) {
    if (report.board && Array.isArray(report.board)) return report.board;
    const out = [];
    for (const r of report.rounds || []) for (const m of r.posts || []) out.push(m);
    return out;
  }

  // ---------------------------------------------------------------- runtime

  async function loadCatalog() {
    try {
      const res = await fetch("/api/runtimes", { cache: "no-store" });
      if (res.ok) {
        state.catalog = await res.json();
      }
    } catch (_) { /* offline demo */ }
    if (!state.catalog) {
      state.catalog = {
        modes: [
          {
            id: "local_cli",
            label: "本地 CLI",
            agents: [
              { id: "claude", label: "Claude Code", available: false, mode: "local_cli" },
              { id: "kimi", label: "Kimi CLI", available: false, mode: "local_cli" },
            ],
          },
          {
            id: "api_key",
            label: "自带 Key",
            agents: [
              { id: "openai_compatible", label: "OpenAI 兼容", available: true, mode: "api_key" },
              { id: "mock", label: "Mock（管道自测）", available: true, mode: "api_key" },
            ],
          },
        ],
        default_mode: "api_key",
        default_agent: "mock",
      };
    }
    state.mode = state.catalog.default_mode || state.mode;
    state.agent = state.catalog.default_agent || state.agent;
    renderRuntimeUI();
    renderRuntimeSummary();
  }

  function agentsForMode(mode) {
    const m = (state.catalog.modes || []).find((x) => x.id === mode);
    return (m && m.agents) || [];
  }

  function renderRuntimeUI() {
    // segmented mode
    document.querySelectorAll("#mode-seg .seg-btn").forEach((btn) => {
      btn.classList.toggle("active", btn.dataset.mode === state.mode);
    });

    // agent buttons
    const row = $("agent-row");
    row.innerHTML = "";
    const title = $("rp-agent-title");
    title.textContent = state.mode === "local_cli" ? "代理" : "提供方";
    for (const a of agentsForMode(state.mode)) {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "agent-btn" + (a.id === state.agent ? " active" : "");
      btn.disabled = !a.available;
      btn.innerHTML = `<span class="dot-av ${a.available ? "" : "off"}"></span>${esc(a.label)}`;
      btn.onclick = () => {
        state.agent = a.id;
        state.model = "";
        renderRuntimeUI();
        renderRuntimeSummary();
      };
      row.appendChild(btn);
    }

    // model select
    const sel = $("model-select");
    const models =
      state.mode === "local_cli"
        ? ["", "Default (CLI config)", "sonnet", "opus", "kimi-k2", "kimi-k2.5"]
        : ["", "gpt-4o-mini", "gpt-4o", "deepseek-chat", "kimi-k2"];
    sel.innerHTML = models
      .map((m) => {
        const label = m === "" ? "Default (CLI config)" : m === "Default (CLI config)" ? m : m;
        const val = m === "Default (CLI config)" ? "" : m;
        return `<option value="${esc(val)}" ${val === (state.model || "") ? "selected" : ""}>${esc(label)}</option>`;
      })
      .join("");
    sel.value = state.model || "";

    // api key fields
    $("api-key-fields").hidden = state.mode !== "api_key";
    $("inp-base-url").value = state.baseUrl;
    $("inp-api-key").value = state.apiKey;

    // chip label
    $("rc-mode").textContent = state.mode === "local_cli" ? "本地 CLI" : "自带 Key";
    const agent = agentsForMode(state.mode).find((a) => a.id === state.agent);
    $("rc-agent").textContent = agent
      ? `${agent.label} · ${state.model || "默认"}`
      : state.agent || "—";
    $("rc-logo").textContent =
      state.agent === "claude" ? "C" : state.agent === "kimi" ? "K" : state.mode === "api_key" ? "A" : "M";
  }

  function renderRuntimeSummary() {
    const el = $("runtime-summary");
    if (!el) return;
    const agent = agentsForMode(state.mode).find((a) => a.id === state.agent);
    el.textContent =
      `runtime = ${state.mode} / ${agent ? agent.label : state.agent}` +
      (state.model ? ` / ${state.model}` : " / 默认模型") +
      (state.mode === "api_key" && state.baseUrl ? ` / ${state.baseUrl}` : "");
  }

  function currentRuntimePayload() {
    return {
      mode: state.mode,
      agent: state.agent,
      model: state.model || null,
      base_url: state.baseUrl || undefined,
      api_key: state.apiKey || undefined,
      cwd: state.cwd || undefined,
      timeout: state.timeoutSec || 180,
    };
  }

  // ---------------------------------------------------------------- session

  function openModal(id) {
    $(id).hidden = false;
  }
  function closeModal(id) {
    $(id).hidden = true;
  }

  async function startSession() {
    const task = $("session-task").value.trim();
    if (!task) {
      toast("请填写研究任务");
      return;
    }
    const roles = Array.from(document.querySelectorAll("#role-checks input:checked")).map(
      (i) => i.value
    );
    if (!roles.length) {
      toast("至少选一个角色");
      return;
    }
    let verifier;
    try {
      verifier = JSON.parse($("session-verifier").value || '{"kind":"null"}');
    } catch {
      toast("验证器 JSON 不合法");
      return;
    }
    const payload = {
      task,
      roles,
      max_rounds: Number($("session-rounds").value || 6),
      challenge_grace_rounds: Number($("session-grace").value || 2),
      require_verifier_gate: $("session-require-gate").checked,
      verifier,
      runtime: currentRuntimePayload(),
    };
    try {
      const res = await fetch("/api/session/start", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const data = await res.json();
      if (!data.ok) {
        toast(data.error || "启动失败");
        return;
      }
      closeModal("session-modal");
      toast("会话已启动，黑板刷新中…");
      refreshSessions().then(startPolling);
    } catch (e) {
      toast("无法连接后端：" + e.message);
    }
  }

  async function stopSession() {
    try {
      await fetch("/api/session/stop", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(state.sessionId ? { id: state.sessionId } : {}),
      });
      toast("已请求停止");
    } catch (e) {
      toast("停止失败：" + e.message);
    }
  }

  function applySession(data) {
    state.lastConfig = data.session.config || null;
    const rb = $("btn-resume");
    if (rb) rb.hidden = data.status === "running";
    state.report = {
      board: data.board,
      task: data.session.task,
      stop_reason: data.session.stop_reason || data.status,
      verdict: data.session.report && data.session.report.verdict,
      best_messages:
        (data.session.report && data.session.report.best_messages) ||
        data.board.filter((m) =>
          ["EXPERIMENT_RESULT", "BASELINE_RESULT", "LEMMA_PROVED", "CONFIRMED"].includes(m.tag)
        ),
      open_questions: data.board.filter((m) => m.tag === "QUESTION"),
      dead_ends: data.board.filter((m) => ["DEAD_END", "COUNTEREXAMPLE"].includes(m.tag)),
      rounds: data.session.report ? data.session.report.rounds : [],
      session: data.session,
      live: true,
    };
    state.source = `实况会话 · ${sessionStatusLabel(data.status)}` + (data.session.progress ? ` · ${data.session.progress}` : "");
    render();
  }

  function sessionUrl() {
    return "/api/session" + (state.sessionId ? `?id=${encodeURIComponent(state.sessionId)}` : "");
  }

  function startPolling() {
    if (state.sessionPoll) return;
    state.sessionPoll = setInterval(async () => {
      try {
        const res = await fetch(sessionUrl(), { cache: "no-store" });
        if (!res.ok) return;
        const data = await res.json();
        if (data.board && data.session) {
          applySession(data);
        }
        if (data.status !== "running") {
          clearInterval(state.sessionPoll);
          state.sessionPoll = null;
          toast(data.status === "error" ? "会话出错" : "会话结束：" + (data.session?.stop_reason || data.status));
          refreshSessions();
          loadData();
        }
      } catch (_) { /* keep polling */ }
    }, 2000);
  }

  // ---------------------------------------------------------------- render

  async function loadData() {
    if (state.sessionId) {
      // viewing a specific session: load it directly, not the latest report
      try {
        const res = await fetch(sessionUrl(), { cache: "no-store" });
        if (res.ok) {
          const data = await res.json();
          if (data.board && data.session) {
            applySession(data);
            if (data.status === "running") startPolling();
            return;
          }
        }
      } catch (_) { /* fall through to demo */ }
    }
    try {
      const res = await fetch("/api/report", { cache: "no-store" });
      if (res.ok) {
        state.report = await res.json();
        state.source = state.report.live
          ? `实况 · ${state.report.session?.progress || sessionStatusLabel(state.report.session?.status) || ""}`
          : "GET /api/report";
        render();
        if (state.report.live && state.report.session?.status === "running") startPolling();
        return;
      }
    } catch (_) { /* fall through */ }
    if (window.MAD_DEMO_DATA) {
      state.report = window.MAD_DEMO_DATA;
      state.source = "内置演示数据 (web/demo-data.js)";
      render();
      toast("未能连接后端，已载入内置演示数据");
      return;
    }
    $("task-text").textContent = "无法加载数据。请运行: python -m mad.web --port 8765";
  }

  // ------------------------------------------------------------- sessions ui

  async function refreshSessions() {
    const picker = $("session-picker");
    if (!picker) return;
    let sessions = [];
    try {
      const res = await fetch("/api/sessions", { cache: "no-store" });
      if (res.ok) sessions = (await res.json()).sessions || [];
    } catch (_) { /* offline demo */ }
    renderDebates(sessions);
    picker.hidden = true; // superseded by the debates sidebar
  }

  function relTime(iso) {
    if (!iso) return "";
    const t = new Date(iso).getTime();
    if (isNaN(t)) return "";
    const m = Math.round((Date.now() - t) / 60000);
    if (m < 1) return "刚刚";
    if (m < 60) return m + " 分钟前";
    const h = Math.round(m / 60);
    if (h < 24) return h + " 小时前";
    return Math.round(h / 24) + " 天前";
  }

  function renderDebates(sessions) {
    const list = $("debate-list");
    if (!list) return;
    if (!sessions.length) {
      list.innerHTML = '<div class="debate-empty">还没有辩论<br/>点「＋ 新辩论」开一场</div>';
      return;
    }
    const rank = { running: 0, stopping: 1, stopped: 2, done: 3, error: 4 };
    const sorted = [...sessions].sort(
      (a, b) =>
        ((rank[a.status] ?? 9) - (rank[b.status] ?? 9)) ||
        (b.created_at || "").localeCompare(a.created_at || "")
    );
    const active = state.sessionId || (sorted[0] && sorted[0].id) || null;
    let html = "";
    for (const s of sorted) {
      const dot = s.status === "running" ? "run" : s.status === "error" ? "err" : "off";
      html +=
        '<div class="debate-item ' + (s.id === active ? "active" : "") + '" data-id="' + esc(s.id) + '">' +
        '<div class="di-top"><span class="di-dot ' + dot + '"></span>' +
        '<span class="di-time">' + esc(relTime(s.created_at)) + "</span></div>" +
        '<div class="di-task">' + esc((s.task || "（无任务）").slice(0, 60)) + "</div>" +
        '<div class="di-meta">' + esc(sessionStatusLabel(s.status)) +
        (s.progress ? " · " + esc(s.progress) : "") + "</div></div>";
    }
    list.innerHTML = html;
    list.querySelectorAll(".debate-item").forEach((el) => {
      el.addEventListener("click", () => selectDebate(el.dataset.id));
    });
  }

  function selectDebate(id) {
    state.sessionId = id || null;
    if (state.sessionPoll) {
      clearInterval(state.sessionPoll);
      state.sessionPoll = null;
    }
    loadData();
    refreshSessions();
  }

  async function injectThought() {
    const body = $("inject-body").value.trim();
    if (!body) {
      toast("写点内容再插入");
      return;
    }
    const tag = $("inject-tag") ? $("inject-tag").value : "NOTE";
    try {
      const res = await fetch("/api/inject", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ id: state.sessionId, body, tag }),
      });
      const data = await res.json();
      if (!data.ok) {
        toast(data.error || "插入失败");
        return;
      }
      $("inject-body").value = "";
      toast("已插入黑板，下一个 agent 立即可见");
      loadData();
    } catch (e) {
      toast("插入失败：" + e.message);
    }
  }

  async function resumeSession() {
    const cfg = state.lastConfig || {};
    try {
      const res = await fetch("/api/session/start", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(Object.assign({}, cfg, { resume: true })),
      });
      const data = await res.json();
      if (!data.ok) {
        toast(data.error || "恢复失败");
        return;
      }
      toast("辩论已恢复");
      refreshSessions().then(startPolling);
    } catch (e) {
      toast("恢复失败：" + e.message);
    }
  }

  function render() {
    const report = state.report || {};
    const msgs = allMessages(report);
    const task =
      report.task ||
      (report.board || [])
        .map((m) => m.body)
        .join("\n")
        .match(/(?:Task|任务):\s*([\s\S]*?)(?:\n(?:Roles|角色):|$)/)?.[1]
        ?.trim() ||
      extractTask(msgs) ||
      "（未指定）";

    $("task-text").textContent = task;
    $("stop-reason").textContent =
      stopLabel(report.stop_reason) ||
      (report.session ? `${sessionStatusLabel(report.session.status)} ${report.session.progress || ""}` : "—");
    $("data-source").textContent = state.source || "—";

    const dead =
      report.dead_ends ||
      msgs.filter((m) => m.tag === "DEAD_END" || m.tag === "COUNTEREXAMPLE");
    const questions = report.open_questions || msgs.filter((m) => m.tag === "QUESTION");
    const uncontested = msgs.filter((m) => m.tag === "UNCONTESTED" || m.status === "uncontested");
    const rounds =
      report.rounds?.length || new Set(msgs.map((m) => m.round_no)).size;

    $("st-posts").textContent = String(msgs.length);
    $("st-rounds").textContent = String(rounds);
    $("st-dead").textContent = String(dead.length);
    $("st-uncontested").textContent = String(uncontested.length);
    $("st-questions").textContent = String(questions.length);

    const v = report.verdict;
    const wrap = $("st-verdict-wrap");
    wrap.classList.remove("pass", "fail");
    if (v) {
      $("st-verdict").textContent = v.passed ? `通过 · ${v.score ?? "—"}` : `未通过 · ${v.score ?? "—"}`;
      wrap.classList.add(v.passed ? "pass" : "fail");
    } else {
      $("st-verdict").textContent = report.session?.status === "running" ? "运行中" : "未验证";
    }

    const banner = $("verdict-banner");
    if (v) {
      banner.hidden = false;
      banner.className = "verdict-banner " + (v.passed ? "pass" : "fail");
      banner.innerHTML = `<strong>Verifier 闸门 · ${v.passed ? "通过" : "未通过"}</strong> — ${esc(v.summary || "")}`;
    } else if (report.session?.status === "running") {
      banner.hidden = false;
      banner.className = "verdict-banner";
      banner.innerHTML = `<span class="session-live"><span class="pulse"></span>讨论进行中</span> — ${esc(report.session.progress || "等待角色发言…")}`;
    } else {
      banner.hidden = true;
    }

    renderTagFilters(msgs);
    renderTimeline(msgs);
    renderSide(report, msgs);
  }

  function extractTask(msgs) {
    const sys = msgs.find((m) => m.tag === "SYSTEM");
    if (!sys) return null;
    const m = sys.body.match(/(?:Task|任务):\s*([\s\S]*?)(?:\n(?:Roles|角色):|$)/);
    return m ? m[1].trim() : null;
  }

  function renderTagFilters(msgs) {
    const counts = {};
    for (const m of msgs) counts[m.tag] = (counts[m.tag] || 0) + 1;
    const box = $("tag-filters");
    const tags = Object.keys(counts).sort((a, b) => counts[b] - counts[a]);
    box.innerHTML = "";
    for (const tag of tags) {
      const btn = document.createElement("button");
      btn.className = "chip" + (state.tagFilter.has(tag) ? " active" : "");
      btn.title = tag;
      btn.innerHTML = `${esc(tagLabel(tag))}<span class="n">${counts[tag]}</span>`;
      btn.onclick = () => {
        if (state.tagFilter.has(tag)) state.tagFilter.delete(tag);
        else state.tagFilter.add(tag);
        render();
      };
      box.appendChild(btn);
    }
    if (state.tagFilter.size) {
      const clear = document.createElement("button");
      clear.className = "chip";
      clear.textContent = "清除筛选";
      clear.onclick = () => {
        state.tagFilter.clear();
        render();
      };
      box.appendChild(clear);
    }
  }

  function filtered(msgs) {
    return msgs.filter((m) => {
      if (!state.showSystem && m.tag === "SYSTEM") return false;
      if (state.tagFilter.size && !state.tagFilter.has(m.tag)) return false;
      if (state.onlyClaims) {
        const claimLike = ["CLAIM", "PROPOSAL", "LEMMA_PROVED", "PLAN"];
        const related = claimLike.includes(m.tag) || m.parent_id;
        if (!related) return false;
      }
      return true;
    });
  }

  function renderTimeline(msgs) {
    const list = filtered(msgs);
    const root = $("timeline");
    if (!list.length) {
      root.innerHTML = `<div class="empty">没有匹配的消息</div>`;
      return;
    }
    const byId = Object.fromEntries(msgs.map((m) => [m.id, m]));
    root.innerHTML = list
      .map((m) => {
        const parent = m.parent_id ? byId[m.parent_id] : null;
        const isChild = !!m.parent_id;
        const status =
          m.status && m.status !== "open"
            ? `<span class="status-pill ${esc(m.status)}" title="${esc(m.status)}">${esc(statusLabel(m.status))}</span>`
            : "";
        const metrics =
          m.metadata && m.metadata.metrics
            ? `<div class="meta">metrics: ${esc(JSON.stringify(m.metadata.metrics))}</div>`
            : "";
        return `
          <article class="msg ${isChild ? "child" : ""}" data-tag="${esc(m.tag)}" data-id="${esc(m.id)}">
            <div class="msg-head">
              <span class="tag-pill tag-${esc(m.tag)}" title="${esc(m.tag)}">${esc(tagLabel(m.tag))}</span>
              <span class="author">${esc(authorLabel(m.author))}</span>
              ${status}
              <span class="meta">r${m.round_no} · ${esc(m.id)}${provenanceNote(m)}</span>
              ${parent ? `<span class="meta">→ ${esc(authorLabel(parent.author))} / ${esc(tagLabel(parent.tag))}</span>` : ""}
            </div>
            <div class="msg-body clamped">${esc(m.body)}</div>
            ${m.body.length > 180 ? `<button class="msg-more">展开全文</button>` : ""}
            ${metrics}
          </article>`;
      })
      .join("");
    root.querySelectorAll(".msg-more").forEach((btn) => {
      btn.addEventListener("click", () => {
        const body = btn.parentElement.querySelector(".msg-body");
        const open = !body.classList.contains("clamped");
        body.classList.toggle("clamped", open);
        btn.textContent = open ? "展开全文" : "收起";
      });
    });
  }

  function renderSide(report, msgs) {
    const dead =
      report.dead_ends ||
      msgs.filter((m) => m.tag === "DEAD_END" || m.tag === "COUNTEREXAMPLE");
    const best = report.best_messages || [];
    const qs = report.open_questions || msgs.filter((m) => m.tag === "QUESTION");
    fillStack("dead-ends", dead, "还没有失败记录——多 agent 系统若无共享负结果，会反复走同一条死路。");
    fillStack("best-results", best, "尚无证据背书的结果。UNCONTESTED 的 claim 不会出现在这里。");
    fillStack("open-questions", qs, "暂无 open question。");
  }

  function fillStack(id, items, emptyText) {
    const box = $(id);
    if (!items.length) {
      box.innerHTML = `<div class="empty">${esc(emptyText)}</div>`;
      return;
    }
    box.innerHTML = items
      .slice(0, 30)
      .map(
        (m) => `
        <div class="card">
          <span class="tag-pill tag-${esc(m.tag)}" title="${esc(m.tag)}">${esc(tagLabel(m.tag))}</span>
          <p>${esc(m.body.length > 220 ? m.body.slice(0, 220) + "…" : m.body)}</p>
          <div class="meta">${esc(authorLabel(m.author))} · r${m.round_no} · ${esc(m.id)}</div>
        </div>`
      )
      .join("");
  }

  // ---------------------------------------------------------------- events

  $("btn-reload").addEventListener("click", () => loadData().then(() => toast("已刷新")));
  $("btn-demo").addEventListener("click", () => {
    if (window.MAD_DEMO_DATA) {
      state.report = window.MAD_DEMO_DATA;
      state.source = "内置演示数据 (web/demo-data.js)";
      render();
      toast("已载入演示数据");
    } else {
      toast("未找到演示数据");
    }
  });
  $("show-system").addEventListener("change", (e) => {
    state.showSystem = e.target.checked;
    render();
  });
  $("only-claims").addEventListener("change", (e) => {
    state.onlyClaims = e.target.checked;
    render();
  });

  // runtime chip
  $("runtime-chip").addEventListener("click", (e) => {
    e.stopPropagation();
    const panel = $("runtime-panel");
    panel.hidden = !panel.hidden;
  });
  document.addEventListener("click", (e) => {
    const panel = $("runtime-panel");
    if (!panel.hidden && !panel.contains(e.target) && e.target.id !== "runtime-chip") {
      panel.hidden = true;
    }
  });
  document.querySelectorAll("#mode-seg .seg-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      state.mode = btn.dataset.mode;
      const avail = agentsForMode(state.mode).filter((a) => a.available);
      if (!avail.find((a) => a.id === state.agent)) {
        state.agent = avail.length ? avail[0].id : "";
      }
      renderRuntimeUI();
      renderRuntimeSummary();
    });
  });
  $("model-select").addEventListener("change", (e) => {
    state.model = e.target.value;
    renderRuntimeUI();
    renderRuntimeSummary();
  });
  $("inp-base-url").addEventListener("change", (e) => {
    state.baseUrl = e.target.value.trim();
    renderRuntimeSummary();
  });
  $("inp-api-key").addEventListener("change", (e) => {
    state.apiKey = e.target.value.trim();
  });

  // modals
  $("btn-new-debate").addEventListener("click", () => {
    openModal("session-modal");
  });
  $("btn-inject").addEventListener("click", injectThought);
  $("btn-resume").addEventListener("click", resumeSession);
  $("btn-new-session").addEventListener("click", () => {
    renderRuntimeSummary();
    openModal("session-modal");
  });
  $("session-close").addEventListener("click", () => closeModal("session-modal"));
  $("session-cancel").addEventListener("click", () => closeModal("session-modal"));
  $("session-start").addEventListener("click", startSession);

  $("btn-open-settings").addEventListener("click", () => {
    $("runtime-panel").hidden = true;
    $("set-mode").value = state.mode;
    const sel = $("set-agent");
    sel.innerHTML = agentsForMode(state.mode)
      .map((a) => `<option value="${esc(a.id)}" ${a.id === state.agent ? "selected" : ""}>${esc(a.label)}${a.available ? "" : "（未安装）"}</option>`)
      .join("");
    $("set-cwd").value = state.cwd;
    $("set-timeout").value = state.timeoutSec;
    openModal("settings-modal");
  });
  $("settings-close").addEventListener("click", () => closeModal("settings-modal"));
  $("set-mode").addEventListener("change", (e) => {
    const sel = $("set-agent");
    sel.innerHTML = agentsForMode(e.target.value)
      .map((a) => `<option value="${esc(a.id)}">${esc(a.label)}${a.available ? "" : "（未安装）"}</option>`)
      .join("");
  });
  $("settings-save").addEventListener("click", () => {
    state.mode = $("set-mode").value;
    state.agent = $("set-agent").value;
    state.cwd = $("set-cwd").value.trim();
    state.timeoutSec = Number($("set-timeout").value || 180);
    renderRuntimeUI();
    renderRuntimeSummary();
    closeModal("settings-modal");
    toast("执行设置已保存（本页）");
  });

  // stop button injected near stop reason when live
  const stopBtn = document.createElement("button");
  stopBtn.className = "btn ghost";
  stopBtn.textContent = "停止会话";
  stopBtn.style.marginTop = "8px";
  stopBtn.addEventListener("click", stopSession);
  document.querySelector(".sidebar .panel:last-child")?.appendChild(stopBtn);

  // session picker: switch which session the board displays (multi-session)
  const picker = document.createElement("select");
  picker.id = "session-picker";
  picker.className = "session-picker";
  picker.hidden = true;
  picker.innerHTML = `<option value="">最新会话</option>`;
  picker.addEventListener("change", () => {
    state.sessionId = picker.value || null;
    if (state.sessionPoll) {
      clearInterval(state.sessionPoll);
      state.sessionPoll = null;
    }
    loadData();
  });
  document.querySelector(".sidebar .panel:last-child")?.insertBefore(picker, stopBtn);

  // boot
  loadCatalog();
  loadData();
  refreshSessions();
})();
