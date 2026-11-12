"use strict";

const REFRESH_MS = 5000;
const SVG_NS = "http://www.w3.org/2000/svg";
const THEME_STORAGE_KEY = "superagent-theme";

const state = {
  data: null,
  filter: "all",
  query: "",
  selectedId: null,
  auto: true,
  inFlight: false,
  nextRefreshAt: Date.now() + REFRESH_MS,
  lastLoadedAt: null,
  lastFocused: null,
  timer: null,
};

const els = {
  connection: document.getElementById("connection"),
  connectionLabel: document.getElementById("connection-label"),
  refresh: document.getElementById("refresh"),
  auto: document.getElementById("auto-refresh"),
  lastUpdated: document.getElementById("last-updated"),
  refreshNote: document.getElementById("refresh-note"),
  errorBanner: document.getElementById("error-banner"),
  errorMessage: document.getElementById("error-message"),
  metricOpen: document.getElementById("metric-open"),
  metricRunning: document.getElementById("metric-running"),
  metricReview: document.getElementById("metric-review"),
  metricMessages: document.getElementById("metric-messages"),
  metricTriggers: document.getElementById("metric-triggers"),
  workers: document.getElementById("workers"),
  workersCount: document.getElementById("workers-count"),
  activityFeed: document.getElementById("activity-feed"),
  activityCount: document.getElementById("activity-count"),
  systemHealth: document.getElementById("system-health"),
  sysStorage: document.getElementById("sys-storage"),
  sysUpdated: document.getElementById("sys-updated"),
  sysOutbox: document.getElementById("sys-outbox"),
  sysEnded: document.getElementById("sys-ended"),
  sysLastTrigger: document.getElementById("sys-last-trigger"),
  sourceBars: document.getElementById("source-bars"),
  sourceTotal: document.getElementById("source-total"),
  triggerList: document.getElementById("trigger-list"),
  triggerCount: document.getElementById("trigger-count"),
  sysPath: document.getElementById("sys-path"),
  ticketRows: document.getElementById("ticket-rows"),
  ticketEmpty: document.getElementById("ticket-empty"),
  tableWrap: document.getElementById("table-wrap"),
  ticketsCount: document.getElementById("tickets-count"),
  search: document.getElementById("search"),
  filters: Array.from(document.querySelectorAll("[data-filter]")),
  filterAll: document.getElementById("filter-all"),
  filterActive: document.getElementById("filter-active"),
  filterReview: document.getElementById("filter-review"),
  filterEnded: document.getElementById("filter-ended"),
  drawer: document.getElementById("drawer"),
  drawerSource: document.getElementById("drawer-source"),
  drawerTitle: document.getElementById("drawer-title"),
  drawerBody: document.getElementById("drawer-body"),
  drawerClose: document.getElementById("drawer-close"),
  themeToggle: document.getElementById("theme-toggle"),
  themeLabel: document.getElementById("theme-label"),
  footerClock: document.getElementById("footer-clock"),
};

const STATE_LABELS = {
  working: "运行中",
  queued: "排队中",
  waiting: "等待中",
  idle: "空闲",
  review: "待评审",
  ready: "已交付",
  progress: "进度更新",
  blocked: "受阻",
  failed: "失败",
  dropped: "已放弃",
  ended: "已结束",
};

const SOURCE_LABELS = {
  github: "GitHub",
  dingtalk: "钉钉",
  feishu: "飞书",
  web: "Web",
  unknown: "未知来源",
};

const SOURCE_GLYPHS = {
  github: "GH",
  dingtalk: "钉",
  feishu: "飞",
  web: "W",
  unknown: "?",
};

const ACTIVITY_LABELS = {
  "ticket.created": "收到请求",
  "assignment.started": "Worker 开始处理",
  "assignment.progress": "进度更新",
  "assignment.report": "提交报告",
  "assignment.ended": "Worker 结束",
  "ticket.ended": "工单结束",
};

const OUTCOME_LABELS = {
  merged: "已合并",
  closed: "已关闭",
  dropped: "已放弃",
};

const numberFmt = new Intl.NumberFormat("zh-CN");
const timeFmt = new Intl.DateTimeFormat("zh-CN", {
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hour12: false,
});
const dateTimeFmt = new Intl.DateTimeFormat("zh-CN", {
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
});
const fullDateTimeFmt = new Intl.DateTimeFormat("zh-CN", {
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hour12: false,
});

// -- tiny DOM helpers -----------------------------------------------------

function h(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === null || value === undefined || value === false) continue;
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else if (key.startsWith("on") && typeof value === "function") {
      node.addEventListener(key.slice(2), value);
    } else if (value === true) {
      node.setAttribute(key, "");
    } else {
      node.setAttribute(key, String(value));
    }
  }
  for (const child of children) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

function icon(name) {
  const svg = document.createElementNS(SVG_NS, "svg");
  svg.setAttribute("class", "icon");
  svg.setAttribute("aria-hidden", "true");
  const use = document.createElementNS(SVG_NS, "use");
  use.setAttribute("href", `#i-${name}`);
  svg.append(use);
  return svg;
}

function clear(node) {
  while (node.firstChild) node.removeChild(node.firstChild);
}

// -- formatting -----------------------------------------------------------

function parseDate(value) {
  if (!value || typeof value !== "string") return null;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? null : date;
}

function relativeText(value) {
  const date = parseDate(value);
  if (!date) return "—";
  const seconds = Math.round((Date.now() - date.getTime()) / 1000);
  if (seconds < 10) return "刚刚";
  if (seconds < 60) return `${seconds} 秒前`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)} 分钟前`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)} 小时前`;
  if (seconds < 86400 * 7) return `${Math.floor(seconds / 86400)} 天前`;
  return dateTimeFmt.format(date);
}

function absoluteText(value) {
  const date = parseDate(value);
  return date ? fullDateTimeFmt.format(date) : "—";
}

function durationText(seconds) {
  if (seconds === null || seconds === undefined || !Number.isFinite(Number(seconds))) return "—";
  const total = Math.max(0, Math.floor(Number(seconds)));
  if (total < 60) return `${total} 秒`;
  if (total < 3600) return `${Math.floor(total / 60)} 分 ${total % 60} 秒`;
  if (total < 86400) return `${Math.floor(total / 3600)} 小时 ${Math.floor((total % 3600) / 60)} 分`;
  return `${Math.floor(total / 86400)} 天 ${Math.floor((total % 86400) / 3600)} 小时`;
}

function elapsedFrom(startIso) {
  const start = parseDate(startIso);
  if (!start) return "—";
  return durationText((Date.now() - start.getTime()) / 1000);
}

function formatBytes(bytes) {
  const value = Number(bytes) || 0;
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  if (value < 1024 * 1024 * 1024) return `${(value / 1024 / 1024).toFixed(1)} MB`;
  return `${(value / 1024 / 1024 / 1024).toFixed(1)} GB`;
}

function shortId(id) {
  return typeof id === "string" ? id.slice(0, 8) : "—";
}

function stateLabel(value) {
  return STATE_LABELS[value] || value || "未知";
}

function sourceLabel(value) {
  return SOURCE_LABELS[value] || value || "未知来源";
}

function outcomeLabel(value) {
  return OUTCOME_LABELS[value] || value || "";
}

function safeHttpUrl(value) {
  if (typeof value !== "string") return null;
  try {
    const url = new URL(value);
    return url.protocol === "http:" || url.protocol === "https:" ? url.href : null;
  } catch {
    return null;
  }
}

function setRelative(node, value) {
  const date = parseDate(value);
  if (!date) {
    node.textContent = "—";
    node.removeAttribute("title");
    return;
  }
  node.dataset.at = date.toISOString();
  node.textContent = relativeText(value);
  node.title = absoluteText(value);
}

function relativeNode(value) {
  const node = document.createElement("span");
  setRelative(node, value);
  return node;
}

// -- shared components ----------------------------------------------------

function badge(value) {
  return h("span", { class: "badge", "data-state": value || "unknown" }, stateLabel(value));
}

function sourceTag(source) {
  return h(
    "span",
    { class: "source-tag", "data-source": source || "unknown" },
    h("span", { class: "source-glyph", "aria-hidden": "true" }, SOURCE_GLYPHS[source] || "?"),
    sourceLabel(source),
  );
}

function prLink(pr, compact = false) {
  const url = safeHttpUrl(pr?.url);
  if (!url) return h("span", { class: "muted-dash" }, "—");
  const node = h(
    "a",
    {
      class: "pr-link",
      href: url,
      target: "_blank",
      rel: "noreferrer noopener",
      title: `打开 PR #${pr.number} 的 GitHub 页面`,
      onclick: (event) => event.stopPropagation(),
    },
    compact ? `#${pr.number}` : `PR #${pr.number}`,
  );
  return node;
}

function emptyPanel(title, text, iconName) {
  return h(
    "div",
    { class: "panel-empty" },
    h("span", { class: "empty-mark" }, icon(iconName || "ticket")),
    h("h4", {}, title),
    h("p", {}, text),
  );
}

function activityText(activity) {
  const label = ACTIVITY_LABELS[activity.kind] || activity.kind || "活动";
  if (activity.kind === "ticket.ended") {
    const suffix = outcomeLabel(activity.outcome);
    return suffix ? `${label}（${suffix}）` : label;
  }
  return label;
}

// -- header / metrics -----------------------------------------------------

function setConnection(kind) {
  els.connection.dataset.state = kind;
  els.connectionLabel.textContent =
    kind === "live" ? "实时连接" : kind === "degraded" ? "数据降级" : kind === "offline" ? "连接中断" : "连接中";
}

function showError(message) {
  if (!message) {
    els.errorBanner.hidden = true;
    els.errorMessage.textContent = "";
    return;
  }
  els.errorMessage.textContent = message;
  els.errorBanner.hidden = false;
}

function renderSummary() {
  const summary = state.data?.summary || {};
  const set = (node, value, fallback = 0) => {
    node.textContent = numberFmt.format(Number(value ?? fallback));
  };
  set(els.metricOpen, summary.openTickets);
  set(els.metricRunning, summary.runningAssignments);
  set(els.metricReview, summary.reviewTickets);
  set(els.metricMessages, summary.pendingMessages);
  set(els.metricTriggers, summary.triggersLastHour);

  if (state.data && !state.data.ok) {
    setConnection("degraded");
    showError(state.data.error || "状态数据库暂不可用。");
    els.systemHealth.dataset.state = "error";
    els.systemHealth.textContent = "不可用";
  } else {
    setConnection("live");
    showError(null);
    els.systemHealth.dataset.state = "ok";
    els.systemHealth.textContent = "正常";
  }
}

function renderWorkers() {
  clear(els.workers);
  const tickets = state.data?.tickets || [];
  const running = tickets.filter((ticket) => ticket.state === "working");
  els.workersCount.textContent = numberFmt.format(running.length);

  if (!running.length) {
    els.workers.append(
      emptyPanel("当前没有运行中的 Worker", "新工单被接单后，会在这里实时出现。", "activity"),
    );
    return;
  }

  for (const ticket of running) {
    const progress = ticket.report?.summary?.trim();
    const card = h(
      "article",
      {
        class: "worker",
        tabindex: "0",
        role: "button",
        "aria-label": `查看工单：${ticket.title}`,
        "data-ticket-id": ticket.id,
        onclick: () => openDrawer(ticket.id),
        onkeydown: (event) => {
          if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            openDrawer(ticket.id);
          }
        },
      },
      h(
        "div",
        { class: "worker-top" },
        h("span", { class: "worker-avatar" }, (ticket.worker || "?").slice(0, 2).toUpperCase()),
        h(
          "div",
          { class: "worker-heading" },
          h("p", { class: "worker-name" }, ticket.worker || "未命名 Worker"),
          h("p", { class: "worker-title", title: ticket.title }, ticket.title),
        ),
        h(
          "span",
          { class: "live-tag", "data-live-start": ticket.startedAt || ticket.createdAt || "" },
          elapsedFrom(ticket.startedAt || ticket.createdAt),
        ),
      ),
      h(
        "p",
        { class: "worker-progress" },
        progress ? progress.slice(0, 280) : "等待 Worker 提交第一条进度…",
      ),
      h(
        "div",
        { class: "worker-foot" },
        h(
          "span",
          {},
          `${sourceLabel(ticket.source)} · 开始于 `,
          relativeNode(ticket.startedAt || ticket.createdAt),
        ),
        h("span", { class: "arrow" }, "查看详情", icon("arrow")),
      ),
    );
    els.workers.append(card);
  }
}

function renderActivity() {
  clear(els.activityFeed);
  const events = (state.data?.events || []).slice(0, 5);
  els.activityCount.textContent = numberFmt.format(events.length);

  if (!events.length) {
    els.activityFeed.append(
      emptyPanel("暂无动态", "新的请求、Worker 进度和报告会出现在这里。", "bolt"),
    );
    return;
  }

  for (const event of events) {
    const button = h(
      "button",
      {
        class: "feed-item",
        type: "button",
        "data-state": event.state || "",
        "data-ticket-id": event.ticketId || "",
        onclick: () => event.ticketId && openDrawer(event.ticketId),
      },
      h("span", { class: "feed-dot", "aria-hidden": "true" }),
      h(
        "span",
        { class: "feed-body" },
        h(
          "span",
          { class: "feed-line" },
          h("span", { class: "feed-kind" }, activityText(event)),
          h("span", { class: "feed-title", title: event.title || "" }, event.title || "未命名工单"),
        ),
        h(
          "span",
          { class: "feed-meta" },
          sourceLabel(event.source),
          h("span", { class: "sep" }, "·"),
          relativeNode(event.at),
        ),
      ),
    );
    els.activityFeed.append(h("li", {}, button));
  }
}

function renderSystem() {  const data = state.data;
  const system = data?.system || {};
  const sourceTotal = (system.sources || []).reduce((sum, item) => sum + item.count, 0);

  els.sysStorage.textContent = data?.ok
    ? `${formatBytes((system.dbSizeBytes || 0) + (system.walSizeBytes || 0))} · 只读`
    : "不可用";
  els.sysStorage.classList.toggle("is-muted", !data?.ok);

  setRelative(els.sysUpdated, system.dbUpdatedAt);
  els.sysOutbox.textContent = `${numberFmt.format(system.pendingDeliveries || 0)} 条`;
  els.sysEnded.textContent = `${numberFmt.format(system.endedToday || 0)} 个`;
  setRelative(els.sysLastTrigger, system.lastTriggerAt);
  els.sysPath.textContent = system.dbPath || "未配置状态目录";
  els.sysPath.title = system.dbPath || "";
  els.sourceTotal.textContent = `${numberFmt.format(sourceTotal)} 个工单`;

  clear(els.sourceBars);
  if (!system.sources?.length) {
    els.sourceBars.append(h("p", { class: "muted-dash" }, "暂无工单来源数据"));
    return;
  }
  for (const item of system.sources) {
    const percent = sourceTotal ? Math.max(2, Math.round((item.count / sourceTotal) * 100)) : 0;
    const fill = h("span", { class: "source-fill" });
    fill.style.width = `${percent}%`;
    els.sourceBars.append(
      h(
        "div",
        { class: "source-row" },
        h("span", { class: "source-name" }, sourceLabel(item.source)),
        h("span", { class: "source-track" }, fill),
        h("span", { class: "source-count" }, numberFmt.format(item.count)),
      ),
    );
  }
}

function renderTriggers() {
  clear(els.triggerList);
  const triggers = state.data?.recentTriggers || [];
  els.triggerCount.textContent = `${numberFmt.format(triggers.length)} 条`;
  if (!triggers.length) {
    els.triggerList.append(h("p", { class: "trigger-empty" }, "还没有收到触发。"));
    return;
  }
  for (const trigger of triggers) {
    const time = relativeNode(trigger.at);
    time.classList.add("trigger-time");
    const text = h(
      "span",
      { class: "trigger-text", title: trigger.text || "" },
      trigger.text || "（无文本）",
    );
    els.triggerList.append(
      h(
        "div",
        { class: "trigger-item", "data-source": trigger.source || "unknown" },
        h(
          "span",
          { class: "source-glyph", "aria-hidden": "true" },
          SOURCE_GLYPHS[trigger.source] || "?",
        ),
        text,
        time,
      ),
    );
  }
}

// -- ticket table ---------------------------------------------------------

function filterMatches(ticket, filter) {
  if (filter === "all") return true;
  if (filter === "review") return ticket.state === "review";
  if (filter === "ended") return ticket.state === "ended" || ticket.state === "dropped";
  if (filter === "active") {
    return ["working", "queued", "waiting", "blocked", "failed"].includes(ticket.state);
  }
  return true;
}

function filterCounts(tickets) {
  return {
    all: tickets.length,
    active: tickets.filter((ticket) => filterMatches(ticket, "active")).length,
    review: tickets.filter((ticket) => filterMatches(ticket, "review")).length,
    ended: tickets.filter((ticket) => filterMatches(ticket, "ended")).length,
  };
}

function searchMatches(ticket) {
  const query = state.query.trim().toLowerCase();
  if (!query) return true;
  const haystack = [
    ticket.title,
    ticket.scope,
    ticket.id,
    ticket.worker,
    sourceLabel(ticket.source),
  ]
    .filter(Boolean)
    .join(" ")
    .toLowerCase();
  return haystack.includes(query);
}

function ticketRow(ticket) {
  const scope = ticket.scope && ticket.scope !== ticket.title ? ticket.scope : "";
  const sub = `#${shortId(ticket.id)}${scope ? ` · ${scope}` : ""}`;
  const active = ["working", "queued", "waiting"].includes(ticket.state);
  const updatedNode = h("span", {});
  setRelative(updatedNode, ticket.updatedAt);

  const row = h(
    "tr",
    {
      tabindex: "0",
      "data-ticket-id": ticket.id,
      "aria-label": `查看工单：${ticket.title}`,
      onclick: () => openDrawer(ticket.id),
      onkeydown: (event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          openDrawer(ticket.id);
        }
      },
    },
    h(
      "td",
      {},
      h(
        "div",
        { class: "ticket-main" },
        h("span", { class: "ticket-title", title: ticket.title }, ticket.title),
        h("span", { class: "ticket-scope", title: sub }, sub),
      ),
    ),
    h("td", {}, badge(ticket.state)),
    h("td", { class: "col-source" }, sourceTag(ticket.source)),
    h("td", { class: "worker-cell", title: ticket.worker || "" }, ticket.worker || "—"),
    h(
      "td",
      { class: "time-cell" },
      h(
        "span",
        {
          class: "elapsed",
          ...(active ? { "data-live-start": ticket.startedAt || ticket.createdAt || "" } : {}),
        },
        active ? elapsedFrom(ticket.startedAt || ticket.createdAt) : ticket.elapsedSeconds != null
          ? durationText(ticket.elapsedSeconds)
          : "—",
      ),
      updatedNode,
    ),
    h("td", {}, ticket.pr ? prLink(ticket.pr, true) : h("span", { class: "muted-dash" }, "—")),
  );
  return row;
}

function renderTickets() {
  const tickets = state.data?.tickets || [];
  const counts = filterCounts(tickets);
  els.filterAll.textContent = numberFmt.format(counts.all);
  els.filterActive.textContent = numberFmt.format(counts.active);
  els.filterReview.textContent = numberFmt.format(counts.review);
  els.filterEnded.textContent = numberFmt.format(counts.ended);
  els.ticketsCount.textContent = numberFmt.format(counts.all);

  for (const button of els.filters) {
    button.classList.toggle("is-active", button.dataset.filter === state.filter);
  }

  const visible = tickets.filter(
    (ticket) => filterMatches(ticket, state.filter) && searchMatches(ticket),
  );
  clear(els.ticketRows);
  for (const ticket of visible) {
    els.ticketRows.append(ticketRow(ticket));
  }
  els.ticketEmpty.hidden = visible.length > 0;
  els.tableWrap.querySelector("table").hidden = visible.length === 0;
  if (visible.length === 0 && tickets.length > 0) {
    els.ticketEmpty.querySelector("h4").textContent = "没有匹配的工单";
    els.ticketEmpty.querySelector("p").textContent = "调整筛选或搜索词再试一次。";
  } else if (tickets.length === 0) {
    els.ticketEmpty.querySelector("h4").textContent = "还没有工单";
    els.ticketEmpty.querySelector("p").textContent = "superagent 接到新请求后会出现在这里。";
  }
}

// -- drawer ---------------------------------------------------------------

function detailCell(label, value, options = {}) {
  const dd = h("dd", { class: options.mono ? "mono" : "" }, value);
  if (options.title) dd.title = options.title;
  return h("div", { class: `detail-cell${options.wide ? " wide" : ""}` }, h("dt", {}, label), dd);
}

function renderDrawer() {
  const ticket = (state.data?.tickets || []).find((item) => item.id === state.selectedId);
  if (!ticket) {
    closeDrawer({ restoreFocus: false });
    return;
  }
  const scrollTop = els.drawerBody.scrollTop;
  els.drawerTitle.textContent = ticket.title;
  els.drawerSource.textContent = `${sourceLabel(ticket.source)} · 工单 #${shortId(ticket.id)}`;
  clear(els.drawerBody);

  const sourceUrl = safeHttpUrl(ticket.sourceUrl);
  const links = h("div", { class: "detail-links" });
  if (ticket.pr) links.append(prLink(ticket.pr));
  if (sourceUrl) {
    links.append(
      h(
        "a",
        {
          class: "detail-link",
          href: sourceUrl,
          target: "_blank",
          rel: "noreferrer noopener",
        },
        "来源会话",
        icon("external"),
      ),
    );
  }

  els.drawerBody.append(
    h("div", { class: "detail-hero" }, badge(ticket.state), sourceTag(ticket.source), links),
    h(
      "dl",
      { class: "detail-grid" },
      detailCell("状态", stateLabel(ticket.state)),
      detailCell("Worker", ticket.worker || "—", { mono: true }),
      detailCell("来源", sourceLabel(ticket.source)),
      detailCell("创建时间", absoluteText(ticket.createdAt)),
      detailCell("本阶段用时", durationText(ticket.elapsedSeconds)),
      detailCell("最近更新", relativeText(ticket.updatedAt), { title: absoluteText(ticket.updatedAt) }),
      detailCell("工单 ID", ticket.id, { mono: true, wide: true, title: ticket.id }),
      detailCell("工作目录", ticket.directory || "—", { mono: true, wide: true, title: ticket.directory || "" }),
    ),
  );

  const report = ticket.report;
  const reportSection = h(
    "section",
    { class: "detail-section" },
    h("h3", {}, icon("ticket"), "最新报告"),
  );
  if (report) {
    reportSection.append(
      h(
        "div",
        { class: "report-card" },
        h(
          "div",
          { class: "report-head" },
          badge(report.state),
          h("span", { class: "report-time" }, absoluteText(report.at)),
        ),
        h("pre", { class: "report-text" }, report.summary || ""),
      ),
    );
  } else {
    reportSection.append(h("div", { class: "report-card" }, h("p", { class: "report-empty" }, "Worker 尚未提交报告。")));
  }
  els.drawerBody.append(reportSection);

  const timeline = h("ul", { class: "timeline" });
  const activities = (ticket.activities || []).slice().reverse();
  for (const activity of activities) {
    const meta = [absoluteText(activity.at), activity.state ? stateLabel(activity.state) : ""]
      .filter(Boolean)
      .join(" · ");
    timeline.append(
      h(
        "li",
        { "data-state": activity.state || "" },
        h("div", { class: "event-label" }, activityText(activity)),
        h("div", { class: "event-meta" }, meta),
      ),
    );
  }
  els.drawerBody.append(
    h("section", { class: "detail-section" }, h("h3", {}, icon("clock"), "时间线"), timeline),
  );

  els.drawerBody.scrollTop = scrollTop;
}

function openDrawer(ticketId) {
  if (!(state.data?.tickets || []).some((ticket) => ticket.id === ticketId)) return;
  const alreadyOpen = els.drawer.classList.contains("is-open");
  if (!alreadyOpen) state.lastFocused = document.activeElement;
  state.selectedId = ticketId;
  renderDrawer();
  els.drawer.classList.add("is-open");
  els.drawer.setAttribute("aria-hidden", "false");
  document.body.style.overflow = "hidden";
  syncTicketParam(ticketId);
  if (!alreadyOpen) {
    requestAnimationFrame(() => els.drawerClose.focus());
  }
}

function closeDrawer({ restoreFocus = true } = {}) {
  if (!els.drawer.classList.contains("is-open")) return;
  els.drawer.classList.remove("is-open");
  els.drawer.setAttribute("aria-hidden", "true");
  document.body.style.overflow = "";
  state.selectedId = null;
  syncTicketParam(null);
  if (restoreFocus && state.lastFocused?.isConnected) {
    state.lastFocused.focus();
  }
  state.lastFocused = null;
}

function readTicketParam() {
  try {
    return new URL(window.location.href).searchParams.get("ticket");
  } catch {
    return null;
  }
}

function syncTicketParam(ticketId) {
  try {
    const url = new URL(window.location.href);
    if (ticketId) url.searchParams.set("ticket", ticketId);
    else url.searchParams.delete("ticket");
    window.history.replaceState(null, "", url);
  } catch {
    // Deep linking is a convenience; never break the dashboard over it.
  }
}

// -- polling --------------------------------------------------------------

function render() {
  renderSummary();
  renderWorkers();
  renderActivity();
  renderSystem();
  renderTriggers();
  renderTickets();
  if (state.selectedId) renderDrawer();
}

async function load({ manual = false } = {}) {
  if (state.inFlight) return;
  state.inFlight = true;
  state.nextRefreshAt = Date.now() + REFRESH_MS;
  els.refresh.classList.add("is-busy");
  els.refresh.disabled = true;
  if (manual) els.refreshNote.textContent = "正在刷新…";

  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 8000);
  try {
    const response = await fetch("/api/status", {
      cache: "no-store",
      signal: controller.signal,
      headers: { Accept: "application/json" },
    });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const payload = await response.json();
    state.data = payload;
    state.lastLoadedAt = new Date();
    els.lastUpdated.textContent = timeFmt.format(state.lastLoadedAt);
    render();
  } catch (error) {
    if (!state.data || !state.data.ok) {
      setConnection("offline");
      showError("无法读取 /api/status，请确认状态页面仍在运行。");
    } else {
      setConnection("offline");
      showError("本次刷新失败，正在展示上一次成功获取的数据。");
    }
    if (manual) els.lastUpdated.textContent = "刷新失败";
  } finally {
    clearTimeout(timeout);
    state.inFlight = false;
    els.refresh.classList.remove("is-busy");
    els.refresh.disabled = false;
    state.nextRefreshAt = Date.now() + REFRESH_MS;
    updateCountdown();
  }
}

function scheduleRefresh() {
  clearTimeout(state.timer);
  if (!state.auto) return;
  state.timer = setTimeout(async () => {
    if (!state.auto) return;
    if (!document.hidden) await load();
    scheduleRefresh();
  }, REFRESH_MS);
}

function updateCountdown() {
  if (state.inFlight) return;
  if (!state.auto) {
    els.refreshNote.textContent = "自动刷新已暂停";
    return;
  }
  const remain = Math.max(0, Math.ceil((state.nextRefreshAt - Date.now()) / 1000));
  els.refreshNote.textContent = remain > 0 ? `${remain} 秒后自动刷新` : "即将刷新…";
}

function tick() {
  if (document.hidden) return;
  const now = new Date();
  els.footerClock.textContent = fullDateTimeFmt.format(now);
  updateCountdown();
  for (const node of document.querySelectorAll("[data-live-start]")) {
    node.textContent = elapsedFrom(node.dataset.liveStart);
  }
}

function refreshRelativeLabels() {
  for (const node of document.querySelectorAll("[data-at]")) {
    node.textContent = relativeText(node.dataset.at);
  }
}

function currentTheme() {
  return document.documentElement.dataset.theme === "light" ? "light" : "dark";
}

function syncThemeToggle() {
  const dark = currentTheme() === "dark";
  const action = dark ? "切换到浅色主题" : "切换到深色主题";
  els.themeLabel.textContent = dark ? "深色" : "浅色";
  els.themeToggle.setAttribute("aria-label", action);
  els.themeToggle.title = action;
}

function setTheme(theme) {
  const next = theme === "light" ? "light" : "dark";
  document.documentElement.dataset.theme = next;
  try {
    localStorage.setItem(THEME_STORAGE_KEY, next);
  } catch (error) {
    // Storage can be unavailable; the theme still applies for this page view.
  }
  syncThemeToggle();
}

// -- events ---------------------------------------------------------------

els.themeToggle.addEventListener("click", () => {
  setTheme(currentTheme() === "dark" ? "light" : "dark");
});

els.refresh.addEventListener("click", () => load({ manual: true }));

els.auto.addEventListener("change", () => {
  state.auto = els.auto.checked;
  state.nextRefreshAt = Date.now() + REFRESH_MS;
  updateCountdown();
  if (state.auto) {
    load();
    scheduleRefresh();
  } else {
    clearTimeout(state.timer);
  }
});

for (const button of els.filters) {
  button.addEventListener("click", () => {
    state.filter = button.dataset.filter || "all";
    renderTickets();
  });
}

els.search.addEventListener("input", () => {
  state.query = els.search.value;
  renderTickets();
});

els.drawerClose.addEventListener("click", () => closeDrawer());

els.drawer.addEventListener("click", (event) => {
  if (event.target.closest("[data-close='true']")) closeDrawer();
});

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") {
    if (els.drawer.classList.contains("is-open")) closeDrawer();
  }
  if (event.key === "Tab" && els.drawer.classList.contains("is-open")) {
    const focusables = els.drawer.querySelectorAll(
      "a[href], button:not([disabled]), input, [tabindex]:not([tabindex='-1'])",
    );
    if (!focusables.length) return;
    const first = focusables[0];
    const last = focusables[focusables.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  }
});

document.addEventListener("visibilitychange", () => {
  if (!document.hidden && (!state.lastLoadedAt || Date.now() - state.lastLoadedAt.getTime() > REFRESH_MS)) {
    load();
  }
});

window.addEventListener("beforeunload", () => {
  document.body.style.overflow = "";
});

// -- boot -----------------------------------------------------------------

syncThemeToggle();
updateCountdown();
tick();
load().then(() => {
  const requested = readTicketParam();
  if (requested && (state.data?.tickets || []).some((ticket) => ticket.id === requested)) {
    openDrawer(requested);
  }
  scheduleRefresh();
});
setInterval(tick, 1000);
setInterval(refreshRelativeLabels, 30000);
