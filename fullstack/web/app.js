"use strict";
/* =====================================================================
   Ontology Lab · 前端
   和离线演练场最大的区别：这里没有 world 对象。所有数据来自 API，
   所有写入经过服务端的事务。每次请求返回的 SQL 追踪显示在右下角面板。
   ===================================================================== */

const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

/* ---------------------------------------------------------------------
   §1  API 客户端
   --------------------------------------------------------------------- */
const state = {
  onto: null, actor: null, users: [], counts: {},
  actions: [], graph: { nodes: [], edges: [] },
  selected: null, detail: null,
  activeAction: null, form: {}, preview: null, banner: null,
  audit: [], schemaLog: [], tables: [],
  mode: "operate", editing: null, highlight: null,
  objCache: new Map(),          // "Type:pk" → 对象（含 rowVersion，用于乐观锁）
  listCache: new Map(),         // 类型 → 对象列表
};

async function api(path, opts = {}) {
  const res = await fetch("/api" + path, {
    ...opts,
    headers: {
      "Content-Type": "application/json",
      "X-Actor": state.actor ? state.actor.username : "",
      ...(opts.headers || {}),
    },
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  let data = {};
  try { data = await res.json(); } catch { /* 静态资源等非 JSON 响应 */ }
  if (data._sql) renderSql(data._sql);
  if (!res.ok) {
    const err = new Error(data.error || `HTTP ${res.status}`);
    err.kind = data.kind || "http";
    err.status = res.status;
    throw err;
  }
  return data;
}

/* SQL 面板 —— 这个项目的教学核心：界面上的每一次点击底下跑了什么 */
const SQL_KW = /\b(SELECT|FROM|WHERE|JOIN|ON|INSERT INTO|VALUES|UPDATE|SET|DELETE|ORDER BY|LIMIT|OFFSET|AND|OR|IS NULL|NOT NULL|COUNT)\b/g;
function sqlRow(e) {
  return `<div class="sqlq">
    <code>${esc(e.sql).replace(SQL_KW, m => `<span class="kw">${m}</span>`)}</code>
    <div class="meta">
      <span>参数 ${esc(JSON.stringify(e.params))}</span>
      <span>${e.rows === null ? "" : e.rows + " 行"}</span>
      <span>${e.ms} ms</span>
    </div>
  </div>`;
}

function renderSql(entries) {
  // 读本体元数据的查询单独折叠 —— 否则真正有意思的那条 JOIN 会被埋掉
  const biz = entries.filter(e => !e.meta);
  const meta = entries.filter(e => e.meta);
  document.getElementById("sql-count").textContent = biz.length;
  document.getElementById("sql-body").innerHTML =
    (biz.map(sqlRow).join("") || `<p class="note">这次请求没有业务查询。</p>`) +
    (meta.length ? `<details style="margin-top:8px">
        <summary class="note" style="cursor:pointer">另有 ${meta.length} 条读本体元数据的查询（每次请求都要先知道 Order 对应哪张表）</summary>
        <div style="opacity:.65">${meta.map(sqlRow).join("")}</div>
      </details>` : "");
}

/* ---------------------------------------------------------------------
   §2  取数
   --------------------------------------------------------------------- */
const OT = id => state.onto && state.onto.objectTypes.find(t => t.id === id);
const LT = id => state.onto && state.onto.linkTypes.find(l => l.id === id);
const AT = id => state.actions.find(a => a.id === id);
const typeColor = id => { const t = OT(id); return `var(--p${t ? t.color % 8 : 3})`; };
const isAdmin = () => state.actor && state.actor.role === "管理员";

async function loadOntology() {
  const data = await api("/ontology");
  state.onto = { objectTypes: data.objectTypes, linkTypes: data.linkTypes, actionTypes: data.actionTypes };
  state.counts = data.counts;
  state.users = data.users;
  if (!state.actor) state.actor = data.actor;
}

async function loadActions() {
  state.actions = (await api("/actions")).actions;
  if (!AT(state.activeAction)) state.activeAction = state.actions.length ? state.actions[0].id : null;
}

async function loadGraph() {
  state.graph = await api("/graph");
  syncGraph();
}

async function loadAudit() {
  state.audit = (await api("/audit?limit=30")).entries;
}

async function objectsOf(type) {
  if (!state.listCache.has(type)) {
    const data = await api(`/objects/${encodeURIComponent(type)}?limit=500`);
    data.objects.forEach(o => state.objCache.set(`${type}:${o.pk}`, o));
    state.listCache.set(type, data.objects);
  }
  return state.listCache.get(type);
}

function invalidate() {
  state.listCache.clear();
  state.objCache.clear();
}

async function selectObject(key) {
  const [type, ...rest] = key.split(":");
  const pk = rest.join(":");
  try {
    const data = await api(`/objects/${encodeURIComponent(type)}/${encodeURIComponent(pk)}`);
    state.selected = key;
    state.detail = data;
    state.objCache.set(key, data.object);
  } catch (e) {
    state.selected = null; state.detail = null;
  }
  renderDetail(); renderLeft(); kick();
}

/* ---------------------------------------------------------------------
   §3  图谱 —— 力导向布局，与离线演练场同一套实现
   --------------------------------------------------------------------- */
const canvas = document.getElementById("graph");
const ctx2d = canvas.getContext("2d");
const layouts = { operate: new Map(), model: new Map() };
let flashes = new Map(), alpha = 1, raf = null, dragging = null, hovered = null;
const REDUCED = matchMedia("(prefers-reduced-motion: reduce)").matches;
const nodes = () => layouts[state.mode];
const flash = k => flashes.set(k, performance.now());

function graphData() {
  if (state.mode === "model") {
    return {
      nodes: (state.onto ? state.onto.objectTypes : []).map(t => ({
        key: t.id, label: t.cn && t.cn !== t.id ? `${t.id} ${t.cn}` : t.id,
        color: typeColor(t.id),
        active: state.editing && state.editing.kind === "ot" && state.editing.id === t.id,
      })),
      edges: (state.onto ? state.onto.linkTypes : []).map(l => ({
        a: l.from, b: l.to, label: l.id, id: l.id,
        active: state.editing && state.editing.kind === "lt" && state.editing.id === l.id,
      })),
    };
  }
  return {
    nodes: state.graph.nodes.map(n => ({
      key: n.key, label: n.title, color: typeColor(n.type), active: n.key === state.selected,
    })),
    edges: state.graph.edges.map(e => ({
      a: e.a, b: e.b, label: e.link, id: e.link,
      active: state.selected && (e.a === state.selected || e.b === state.selected),
    })),
  };
}

let theme = {};
function refreshTheme() {
  const cs = getComputedStyle(document.documentElement);
  theme = {
    line: cs.getPropertyValue("--line").trim(), muted: cs.getPropertyValue("--muted").trim(),
    text: cs.getPropertyValue("--text").trim(), panel: cs.getPropertyValue("--panel").trim(),
    write: cs.getPropertyValue("--write").trim(),
  };
  for (let i = 0; i < 8; i++) theme["p" + i] = cs.getPropertyValue("--p" + i).trim();
  kick();
}
const solid = c => { const m = /var\(--(p\d)\)/.exec(c); return m ? theme[m[1]] : c; };

function syncGraph() {
  const map = nodes();
  const cx = canvas.clientWidth / 2 || 300, cy = canvas.clientHeight / 2 || 200;
  const data = graphData();
  data.nodes.forEach((n, i) => {
    if (!map.has(n.key)) {
      const a = (i / Math.max(data.nodes.length, 1)) * Math.PI * 2;
      map.set(n.key, { x: cx + Math.cos(a) * 140 + Math.random() * 8, y: cy + Math.sin(a) * 110 + Math.random() * 8, vx: 0, vy: 0, w: 80, h: 26, pinned: false });
    }
  });
  const live = new Set(data.nodes.map(n => n.key));
  for (const k of [...map.keys()]) if (!live.has(k)) map.delete(k);
  measureNodes(); kick();
}
function measureNodes() {
  ctx2d.font = '600 11.5px ' + getComputedStyle(document.body).getPropertyValue("--mono");
  for (const n of graphData().nodes) {
    const g = nodes().get(n.key); if (!g) continue;
    g.w = Math.min(170, ctx2d.measureText(n.label).width + 30); g.h = 26;
  }
}
function relayout() {
  const cx = canvas.clientWidth / 2, cy = canvas.clientHeight / 2;
  const keys = [...nodes().keys()];
  keys.forEach((k, i) => {
    const n = nodes().get(k), a = (i / keys.length) * Math.PI * 2;
    n.x = cx + Math.cos(a) * 150; n.y = cy + Math.sin(a) * 120; n.vx = n.vy = 0; n.pinned = false;
  });
  kick();
}
function step() {
  const W = canvas.clientWidth, H = canvas.clientHeight;
  const map = nodes(), arr = [...map.values()], data = graphData();
  for (const n of arr) { n.fx = 0; n.fy = 0; }
  const GAP = 14;
  for (let i = 0; i < arr.length; i++) for (let j = i + 1; j < arr.length; j++) {
    const a = arr[i], b = arr[j];
    let dx = b.x - a.x, dy = b.y - a.y, d2 = dx * dx + dy * dy;
    if (d2 < 1) { dx = Math.random() - 0.5; dy = Math.random() - 0.5; d2 = 1; }
    const d = Math.sqrt(d2), ux = dx / d, uy = dy / d, f = Math.min(13000 / d2, 45);
    a.fx -= ux * f; a.fy -= uy * f; b.fx += ux * f; b.fy += uy * f;
    const ox = (a.w + b.w) / 2 + GAP - Math.abs(dx), oy = (a.h + b.h) / 2 + GAP - Math.abs(dy);
    if (ox > 0 && oy > 0) {
      if (ox < oy) { const p = Math.sign(dx || 1) * ox * 0.5; a.fx -= p; b.fx += p; }
      else { const p = Math.sign(dy || 1) * oy * 0.55; a.fy -= p; b.fy += p; }
    }
  }
  for (const e of data.edges) {
    const a = map.get(e.a), b = map.get(e.b);
    if (!a || !b || a === b) continue;
    const dx = b.x - a.x, dy = b.y - a.y, d = Math.hypot(dx, dy) || 1;
    const rest = (a.w + b.w) / 2 + 74, f = (d - rest) * 0.04, ux = dx / d, uy = dy / d;
    a.fx += ux * f; a.fy += uy * f; b.fx -= ux * f; b.fy -= uy * f;
  }
  const cx = W / 2, cy = H / 2;
  for (const n of arr) {
    n.fx += (cx - n.x) * 0.009; n.fy += (cy - n.y) * 0.016;
    if (n.pinned) { n.vx = n.vy = 0; continue; }
    n.vx = (n.vx + n.fx * alpha) * 0.84; n.vy = (n.vy + n.fy * alpha) * 0.84;
    n.x += n.vx; n.y += n.vy;
    n.x = Math.max(n.w / 2 + 8, Math.min(W - n.w / 2 - 8, n.x));
    n.y = Math.max(n.h / 2 + 8, Math.min(H - n.h / 2 - 8, n.y));
  }
  alpha *= 0.985;
}
function rectEdge(n, dx, dy) {
  const hw = n.w / 2 + 3, hh = n.h / 2 + 3;
  const s = Math.min(hw / Math.abs(dx || 1e-6), hh / Math.abs(dy || 1e-6));
  return [n.x + dx * s, n.y + dy * s];
}
function roundRect(x, y, w, h, r) {
  ctx2d.beginPath(); ctx2d.moveTo(x + r, y);
  ctx2d.arcTo(x + w, y, x + w, y + h, r); ctx2d.arcTo(x + w, y + h, x, y + h, r);
  ctx2d.arcTo(x, y + h, x, y, r); ctx2d.arcTo(x, y, x + w, y, r); ctx2d.closePath();
}
function clip(s, max) {
  if (ctx2d.measureText(s).width <= max) return s;
  let t = s;
  while (t.length > 1 && ctx2d.measureText(t + "…").width > max) t = t.slice(0, -1);
  return t + "…";
}
function draw() {
  const dpr = Math.min(devicePixelRatio || 1, 2);
  const W = canvas.clientWidth, H = canvas.clientHeight;
  if (canvas.width !== W * dpr || canvas.height !== H * dpr) { canvas.width = W * dpr; canvas.height = H * dpr; }
  ctx2d.setTransform(dpr, 0, 0, dpr, 0, 0); ctx2d.clearRect(0, 0, W, H);

  const map = nodes(), data = graphData();
  const mono = getComputedStyle(document.body).getPropertyValue("--mono");
  const anyActive = data.nodes.some(n => n.active);
  const nbr = new Set();
  if (state.mode === "operate" && state.selected)
    for (const e of data.edges) { if (e.a === state.selected) nbr.add(e.b); if (e.b === state.selected) nbr.add(e.a); }

  const nodeType = key => {
    if (state.mode === "model") return key;
    const n = state.graph.nodes.find(x => x.key === key);
    return n ? n.type : null;
  };
  const dimNode = n => {
    if (state.highlight) {
      if (state.highlight.kind === "objectType") return nodeType(n.key) === state.highlight.id ? 1 : 0.16;
      const l = LT(state.highlight.id); const ty = nodeType(n.key);
      return l && (ty === l.from || ty === l.to) ? 1 : 0.16;
    }
    if (state.mode === "operate" && state.selected)
      return n.key === state.selected ? 1 : (nbr.has(n.key) ? 1 : 0.3);
    if (state.mode === "model" && anyActive) return n.active ? 1 : 0.42;
    return 1;
  };
  const dimKey = k => { const n = data.nodes.find(x => x.key === k); return n ? dimNode(n) : 1; };

  for (const e of data.edges) {
    const a = map.get(e.a), b = map.get(e.b);
    if (!a || !b || a === b) continue;
    let op = Math.min(dimKey(e.a), dimKey(e.b));
    if (state.highlight && state.highlight.kind === "linkType") op = e.id === state.highlight.id ? 1 : 0.1;
    const strong = e.active || (state.highlight && state.highlight.kind === "linkType" && e.id === state.highlight.id);
    const dx = b.x - a.x, dy = b.y - a.y;
    const [x1, y1] = rectEdge(a, dx, dy), [x2, y2] = rectEdge(b, -dx, -dy);
    ctx2d.globalAlpha = op * (strong ? 0.95 : 0.5);
    ctx2d.strokeStyle = strong ? theme.text : theme.line;
    ctx2d.lineWidth = strong ? 1.4 : 1;
    ctx2d.beginPath(); ctx2d.moveTo(x1, y1); ctx2d.lineTo(x2, y2); ctx2d.stroke();
    const ang = Math.atan2(y2 - y1, x2 - x1);
    ctx2d.fillStyle = strong ? theme.text : theme.line;
    ctx2d.beginPath(); ctx2d.moveTo(x2, y2);
    ctx2d.lineTo(x2 - 7 * Math.cos(ang - 0.4), y2 - 7 * Math.sin(ang - 0.4));
    ctx2d.lineTo(x2 - 7 * Math.cos(ang + 0.4), y2 - 7 * Math.sin(ang + 0.4));
    ctx2d.closePath(); ctx2d.fill();
    if (strong || state.mode === "model") {
      const mx = (x1 + x2) / 2, my = (y1 + y2) / 2;
      ctx2d.font = '500 10px ' + mono;
      const tw = ctx2d.measureText(e.label).width;
      ctx2d.globalAlpha = op * 0.95;
      ctx2d.fillStyle = theme.panel; ctx2d.fillRect(mx - tw / 2 - 3, my - 7, tw + 6, 13);
      ctx2d.fillStyle = strong ? theme.text : theme.muted;
      ctx2d.textAlign = "center"; ctx2d.textBaseline = "middle";
      ctx2d.fillText(e.label, mx, my);
    }
  }

  ctx2d.textAlign = "left"; ctx2d.textBaseline = "middle";
  for (const n of data.nodes) {
    const g = map.get(n.key); if (!g) continue;
    const col = solid(n.color), op = dimNode(n);
    const x = g.x - g.w / 2, y = g.y - g.h / 2;
    ctx2d.globalAlpha = op;
    roundRect(x, y, g.w, g.h, 4); ctx2d.fillStyle = theme.panel; ctx2d.fill();
    ctx2d.globalAlpha = op * 0.1; ctx2d.fillStyle = col; ctx2d.fill();
    ctx2d.globalAlpha = op; ctx2d.strokeStyle = col; ctx2d.lineWidth = n.active ? 2 : 1; ctx2d.stroke();
    if (n.active) {
      ctx2d.globalAlpha = op * 0.35;
      roundRect(x - 3.5, y - 3.5, g.w + 7, g.h + 7, 6);
      ctx2d.strokeStyle = col; ctx2d.lineWidth = 1; ctx2d.stroke();
    }
    const f = flashes.get(n.key);
    if (f !== undefined) {
      const t = (performance.now() - f) / 2200;
      if (t >= 1) flashes.delete(n.key);
      else {
        ctx2d.globalAlpha = (1 - t) * 0.9;
        roundRect(x - 3 - t * 7, y - 3 - t * 7, g.w + 6 + t * 14, g.h + 6 + t * 14, 6 + t * 6);
        ctx2d.strokeStyle = theme.write; ctx2d.lineWidth = 1.5; ctx2d.stroke();
      }
    }
    ctx2d.globalAlpha = op; ctx2d.fillStyle = col;
    ctx2d.beginPath(); ctx2d.arc(x + 11, g.y, 3.2, 0, Math.PI * 2); ctx2d.fill();
    ctx2d.fillStyle = theme.text; ctx2d.font = '500 11.5px ' + mono;
    ctx2d.fillText(clip(n.label, g.w - 26), x + 19, g.y + 0.5);
  }
  ctx2d.globalAlpha = 1;

  if (!data.nodes.length) {
    ctx2d.fillStyle = theme.muted;
    ctx2d.font = '13px ' + getComputedStyle(document.body).getPropertyValue("--sans");
    ctx2d.textAlign = "center";
    ctx2d.fillText("没有数据", W / 2, H / 2);
    ctx2d.textAlign = "left";
  }
}
function loop() {
  step(); draw();
  if (alpha > 0.008 || dragging || flashes.size) raf = requestAnimationFrame(loop);
  else { raf = null; draw(); }
}
function kick() {
  alpha = Math.max(alpha, 0.55);
  if (REDUCED) { for (let i = 0; i < 220; i++) step(); alpha = 0; draw(); return; }
  if (!raf) raf = requestAnimationFrame(loop);
}
function nodeAt(px, py) {
  for (const [k, n] of [...nodes()].reverse())
    if (Math.abs(px - n.x) <= n.w / 2 && Math.abs(py - n.y) <= n.h / 2) return k;
  return null;
}
canvas.addEventListener("pointerdown", ev => {
  const r = canvas.getBoundingClientRect();
  const k = nodeAt(ev.clientX - r.left, ev.clientY - r.top);
  if (!k) return;
  dragging = k; nodes().get(k).pinned = true;
  canvas.setPointerCapture(ev.pointerId);
  if (state.mode === "operate") selectObject(k); else openEditor("ot", k);
});
canvas.addEventListener("pointermove", ev => {
  const r = canvas.getBoundingClientRect();
  const px = ev.clientX - r.left, py = ev.clientY - r.top;
  if (dragging) { const n = nodes().get(dragging); if (n) { n.x = px; n.y = py; } kick(); return; }
  const k = nodeAt(px, py);
  if (k !== hovered) { hovered = k; canvas.style.cursor = k ? "pointer" : "default"; }
});
const endDrag = () => { if (dragging) { const n = nodes().get(dragging); if (n) n.pinned = false; dragging = null; kick(); } };
canvas.addEventListener("pointerup", endDrag);
canvas.addEventListener("pointercancel", endDrag);
new ResizeObserver(() => kick()).observe(canvas);

/* ---------------------------------------------------------------------
   §4  值的显示
   --------------------------------------------------------------------- */
const TONE = { "已送达": "ok", "已完成": "ok", "已取消": "bad", "运输中": "info", "已发货": "info", "已支付": "info" };
function fmt(v, p) {
  if (v === null || v === undefined || v === "") return `<span style="color:var(--muted)">—</span>`;
  if (p.type === "currency") return "¥" + Number(v).toLocaleString("zh-CN", { minimumFractionDigits: 2 });
  if (p.type === "integer") return Number(v).toLocaleString("zh-CN");
  if (p.type === "boolean") return v ? "是" : "否";
  if (p.type === "enum") {
    const tone = TONE[v] || "neutral";
    return `<span class="pill" style="color:var(--${tone});border-color:currentColor">${esc(v)}</span>`;
  }
  return esc(v);
}

/* ---------------------------------------------------------------------
   §5  概念带 / 统计 / 左栏
   --------------------------------------------------------------------- */
function renderConcepts() {
  const cards = state.mode === "model" ? [
    { g: "▣", c: "var(--p0)", cn: "Object Type 对象类型", p: "绑定一张真实的表，把列映射成属性。对象类型不能凭空造 —— 没有数据源就没有实例。" },
    { g: "⇄", c: "var(--p2)", cn: "Link Type 链接类型", p: "由一个真实外键实现。这里能看到每条链接背后是哪张表的哪一列。" },
    { g: "↯", c: "var(--write)", round: true, cn: "Action Type 操作类型", p: "声明式的参数、校验、编辑，存在数据库里，由服务端解释器求值。" },
  ] : [
    { g: "▣", c: "var(--p0)", cn: "Object 对象", p: "业务表里的一行，按映射翻译过的：码值变成人话，分变成元。" },
    { g: "⇄", c: "var(--p2)", cn: "Link 链接", p: "沿着链接走一步，底下就是一条 SQL。右下角面板里能看到它。" },
    { g: "↯", c: "var(--write)", round: true, cn: "Action 操作", p: "唯一写入口。一个事务、一次权限检查、一条审计记录。没有通用撤销。" },
  ];
  document.getElementById("concepts").innerHTML = cards.map(c => `
    <div class="concept">
      <span class="glyph ${c.round ? "round" : ""}" style="color:${c.c}">${c.g}</span>
      <div><h2>${esc(c.cn)}</h2><p>${esc(c.p)}</p></div>
    </div>`).join("");
}

function renderStats() {
  const s = state.mode === "model"
    ? [["对象类型", state.onto.objectTypes.length], ["链接类型", state.onto.linkTypes.length],
       ["操作类型", state.onto.actionTypes.length],
       ["实例", Object.values(state.counts).reduce((a, b) => a + b, 0)]]
    : [["Objects", Object.values(state.counts).reduce((a, b) => a + b, 0)],
       ["Links", state.graph.edges.length],
       ["Actions", state.audit.filter(e => e.status === "applied").length],
       ["Rejected", state.audit.filter(e => e.status === "rejected").length]];
  document.getElementById("stats").innerHTML = s.map(([k, v]) =>
    `<div class="stat"><b>${v}</b><span>${esc(k)}</span></div>`).join("");
  document.querySelectorAll(".mode").forEach(b => b.setAttribute("aria-pressed", b.dataset.mode === state.mode));
  document.getElementById("graph-tip").textContent =
    state.mode === "model" ? "类型图谱：节点是对象类型，连线是链接类型" : "点击节点查看对象 · 可拖拽";
  document.getElementById("role").textContent = state.actor ? state.actor.role : "";
  const sel = document.getElementById("actor");
  if (sel.options.length !== state.users.length) {
    sel.innerHTML = state.users.map(u =>
      `<option value="${esc(u.username)}">${esc(u.cn)}</option>`).join("");
  }
  if (state.actor) sel.value = state.actor.username;
}

function renderLeft() {
  const modelling = state.mode === "model";
  const ots = state.onto.objectTypes.map(t => `
    <button class="type-row" ${modelling
      ? `data-edit="ot" data-id="${esc(t.id)}" aria-pressed="${state.editing && state.editing.kind === "ot" && state.editing.id === t.id}"`
      : `data-hl="objectType" data-id="${esc(t.id)}" aria-pressed="${state.highlight && state.highlight.kind === "objectType" && state.highlight.id === t.id}"`}>
      <span class="swatch" style="background:${typeColor(t.id)}"></span>
      <span class="body">
        <span class="name">${esc(t.id)}</span> <span class="cn">${esc(t.cn)}</span>
        <span class="props">表 ${esc(t.sourceTable)} · ${t.props.length} 个属性</span>
      </span>
      <span class="count">${state.counts[t.id] ?? 0}</span>
    </button>`).join("");

  const lts = state.onto.linkTypes.map(l => `
    <button class="link-row" ${modelling
      ? `data-edit="lt" data-id="${esc(l.id)}" aria-pressed="${state.editing && state.editing.kind === "lt" && state.editing.id === l.id}"`
      : `data-hl="linkType" data-id="${esc(l.id)}" aria-pressed="${state.highlight && state.highlight.kind === "linkType" && state.highlight.id === l.id}"`}>
      <span class="link-sig">
        <span style="color:${typeColor(l.from)}">${esc(l.from)}</span>
        <span class="arrow">──▸</span>
        <span style="color:${typeColor(l.to)}">${esc(l.to)}</span>
      </span>
      <span class="link-meta">
        <span class="card-badge">${esc(l.card)}</span>
        <span class="map">← <b>${esc(l.fkTable)}.${esc(l.fkColumn)}</b></span>
      </span>
    </button>`).join("");

  const ats = state.actions.map(a => `
    <button class="act-row ${a.allowed ? "" : "locked"}" ${modelling
      ? `data-edit="at" data-id="${esc(a.id)}" aria-pressed="${state.editing && state.editing.kind === "at" && state.editing.id === a.id}"`
      : `data-action="${esc(a.id)}"`}>
      <span class="dot"></span>
      <span class="name">${esc(a.id)}</span>
      <span class="cn">${esc(a.cn)}</span>
      ${a.requiredRole ? `<span class="count">${esc(a.requiredRole)}</span>` : ""}
    </button>`).join("");

  document.getElementById("col-left").innerHTML = `
    <div class="pane-head">
      <span class="eyebrow">本体模型 / Schema</span>
      <span class="hint">${modelling ? "点类型看映射" : (state.highlight ? "点同一项取消筛选" : "")}</span>
    </div>
    <div class="sub-head"><span class="eyebrow">对象类型</span><span class="hair"></span>
      ${modelling && isAdmin() ? `<button class="plus" data-new="ot">+ 绑定表</button>` : ""}</div>
    <div class="type-list">${ots}</div>
    <div class="sub-head"><span class="eyebrow">链接类型</span><span class="hair"></span>
      ${modelling && isAdmin() ? `<button class="plus" data-new="lt">+ 新建</button>` : ""}</div>
    <div class="type-list">${lts}</div>
    <div class="sub-head"><span class="eyebrow">操作类型</span><span class="hair"></span>
      ${modelling && isAdmin() ? `<button class="plus" data-new="at">+ 新建</button>` : ""}</div>
    <div class="type-list">${ats}</div>`;

  document.getElementById("legend").innerHTML = state.onto.objectTypes.map(t =>
    `<span class="lg"><i style="background:${typeColor(t.id)}"></i>${esc(t.cn)}</span>`).join("");
}

/* ---------------------------------------------------------------------
   §6  运行模式：对象详情
   --------------------------------------------------------------------- */
function renderDetail() {
  const el = document.getElementById("mid-body");
  if (!state.detail) {
    el.innerHTML = `<div class="empty">
      <p class="lead">点图谱里的一个节点，看看一个 Object 长什么样。</p>
      属性表里每一行右边都标着它来自哪一列；「关联对象」里每一组都标着它由哪个外键实现，
      以及走了什么 SQL。右下角的 SQL 面板会显示这一次点击真正跑过的全部查询。</div>`;
    return;
  }
  const obj = state.detail.object;
  const t = OT(obj.type);
  const rows = t.props.map(p => `
    <tr>
      <th>${esc(p.cn)}<small>${esc(p.id)} : ${esc(p.type)}</small></th>
      <td>${fmt(obj.props[p.id], p)}</td>
      <td class="origin"><span class="map">← <b>${esc(p.sourceColumn)}</b>${p.scale ? ` ÷${p.scale}` : ""}${p.valueMap ? " 码表" : ""}</span></td>
    </tr>`).join("");

  const links = state.detail.links.map(g => `
    <div class="link-group-head">
      <span class="card-badge">${esc(g.card)}</span>
      <span class="lt">${g.dir === "fwd" ? "" : "◂── "}${esc(g.link)}${g.dir === "fwd" ? " ──▸" : ""}</span>
      <span class="dir">${esc(g.label)}</span>
      <span class="map">← <b>${esc(g.fk)}</b></span>
    </div>
    <div class="chips">${g.targets.map(o => `
      <button class="chip" data-goto="${esc(o.type)}:${esc(o.pk)}" style="color:${typeColor(o.type)}">
        <i style="background:${typeColor(o.type)}"></i>
        <span style="color:var(--text)">${esc(o.title)}</span>
        ${o.title === String(o.pk) ? "" : `<span class="rid">${esc(o.pk)}</span>`}
      </button>`).join("")}</div>
    <div class="sqlq"><code>${esc(g.sql).replace(SQL_KW, m => `<span class="kw">${m}</span>`)}</code></div>`).join("")
    || `<p class="note" style="margin-top:8px">这个对象目前没有任何链接。</p>`;

  const usable = state.actions.filter(a => a.params.some(p => p.kind === "object" && p.objectType === obj.type));
  el.innerHTML = `
    <div class="obj-head">
      <div class="obj-kicker">
        <span class="pill" style="color:${typeColor(obj.type)};border-color:currentColor">▣ ${esc(obj.type)}</span>
        <span class="rid">${esc(t.cn)} · 主键 ${esc(obj.pk)}</span>
        <span class="src-table">← 表 <b style="color:var(--text-2)">${esc(t.sourceTable)}</b></span>
        ${obj.rowVersion !== null ? `<span class="map">version <b>${obj.rowVersion}</b></span>` : ""}
      </div>
      <h3>${esc(obj.title)}</h3>
    </div>
    <table class="props">${rows}</table>
    <div class="link-group">
      <div class="sub-head" style="padding-left:0"><span class="eyebrow">关联对象 / Links</span><span class="hair"></span></div>
      ${links}
      <div class="sub-head" style="padding-left:0"><span class="eyebrow">可用操作 / Actions</span><span class="hair"></span></div>
      <div class="chips">${usable.map(a => `
        <button class="chip ${a.allowed ? "" : "locked"}" data-run="${esc(a.id)}" style="color:var(--write)">
          <span style="color:var(--write)">↯ ${esc(a.cn)}</span>
          <span class="rid">${a.allowed ? esc(a.id) : "需要 " + esc(a.requiredRole)}</span>
        </button>`).join("") || `<span class="note">没有以该类型为参数的操作。</span>`}</div>
    </div>`;
}

/* ---------------------------------------------------------------------
   §7  运行模式：Action 执行器
   --------------------------------------------------------------------- */
function defaultParams(action) {
  const p = {};
  for (const pr of action.params) {
    if (pr.kind === "object") {
      const list = state.listCache.get(pr.objectType) || [];
      const cur = state.detail && state.detail.object.type === pr.objectType ? state.detail.object.pk : null;
      p[pr.id] = cur && list.some(o => o.pk === cur) ? cur : (list[0] ? list[0].pk : "");
    } else if (pr.kind === "enum") p[pr.id] = pr.def ?? (pr.options || [])[0] ?? "";
    else if (pr.kind === "number") p[pr.id] = pr.def ?? 0;
    else p[pr.id] = pr.def ?? "";
  }
  return p;
}

/** 提交时带上这一页读到的版本号 —— 乐观锁靠它发现"你读的已经过期了" */
function versionsFor(action, params) {
  const v = {};
  for (const pr of action.params) {
    if (pr.kind !== "object") continue;
    const key = `${pr.objectType}:${params[pr.id]}`;
    const o = state.objCache.get(key);
    if (o && o.rowVersion !== null && o.rowVersion !== undefined) v[key] = o.rowVersion;
  }
  return v;
}

let previewSeq = 0;
async function refreshPreview() {
  const action = AT(state.activeAction);
  if (!action) return;
  const seq = ++previewSeq;
  try {
    const data = await api(`/actions/${encodeURIComponent(action.id)}/preview`,
      { method: "POST", body: { params: state.form } });
    if (seq !== previewSeq) return;         // 有更新的请求在路上，丢弃这次
    state.preview = data;
  } catch (e) {
    if (seq !== previewSeq) return;
    state.preview = null; state.banner = { kind: e.kind, text: e.message };
  }
  renderRunnerState();
}

async function prepareRunner() {
  const action = AT(state.activeAction);
  if (!action) return;
  for (const pr of action.params) if (pr.kind === "object") await objectsOf(pr.objectType);
  state.form = defaultParams(action);
  await refreshPreview();
  renderRunner();
}

function renderRunner() {
  const right = document.getElementById("col-right");
  if (!state.actions.length) {
    right.innerHTML = `<div class="pane-head"><span class="eyebrow">操作执行器 / Action</span></div>
      <div class="empty">本体里还没有操作类型。</div>`;
    return;
  }
  const action = AT(state.activeAction);
  const fields = action.params.map(pr => {
    let input, hint = pr.kind;
    if (pr.kind === "object") {
      const list = state.listCache.get(pr.objectType) || [];
      hint = pr.objectType;
      input = `<select data-p="${esc(pr.id)}">${list.length ? "" : '<option value="">（没有这类对象）</option>'}${
        list.map(o => `<option value="${esc(o.pk)}" ${o.pk === state.form[pr.id] ? "selected" : ""}>${
          esc(o.title === String(o.pk) ? o.title : `${o.title} · ${o.pk}`)}</option>`).join("")}</select>`;
    } else if (pr.kind === "enum") {
      input = `<select data-p="${esc(pr.id)}">${(pr.options || []).map(o =>
        `<option ${o === state.form[pr.id] ? "selected" : ""}>${esc(o)}</option>`).join("")}</select>`;
    } else if (pr.kind === "number") {
      input = `<input type="number" data-p="${esc(pr.id)}" value="${esc(state.form[pr.id])}">`;
    } else {
      input = `<input type="text" data-p="${esc(pr.id)}" value="${esc(state.form[pr.id])}">`;
    }
    return `<div class="field"><label>${esc(pr.cn)} <code>${esc(pr.id)} : ${esc(hint)}</code></label>${input}</div>`;
  }).join("") || `<p class="note">这个操作没有参数。</p>`;

  right.innerHTML = `
    <div class="pane-head"><span class="eyebrow">操作执行器 / Action</span></div>
    <div class="action-tabs">${state.actions.map(a =>
      `<button class="tab ${a.allowed ? "" : "locked"}" data-action="${esc(a.id)}"
        aria-pressed="${a.id === state.activeAction}">${esc(a.cn)}</button>`).join("")}</div>
    <div id="banner-slot"></div>
    <div class="runner">
      <div class="runner-head">
        <h3>${esc(action.cn)} <span class="mono" style="font-size:11.5px;color:var(--muted)">${esc(action.id)}</span>
          ${action.requiredRole ? `<span class="role">${esc(action.requiredRole)}</span>` : ""}</h3>
        <p>${esc(action.desc || "")}</p>
      </div>
      <div class="runner-sec"><span class="eyebrow">① 参数 Parameters</span>${fields}</div>
      <div class="runner-sec"><span class="eyebrow">② 校验 Validation</span><div id="runner-checks"></div></div>
      <div class="runner-sec">
        <span class="eyebrow">③ 编辑预览 Edits <span id="runner-count" style="color:var(--write)">0</span></span>
        <div id="runner-edits"></div>
      </div>
      <div class="runner-sec" id="runner-apply"></div>
    </div>
    <div class="sub-head"><span class="eyebrow">写入日志 / Audit</span><span class="hair"></span></div>
    <div class="log" id="audit-log"></div>`;
  renderRunnerState();
  renderAudit();
}

function renderRunnerState() {
  const action = AT(state.activeAction);
  const pv = state.preview;
  const set = (id, html) => { const el = document.getElementById(id); if (el) el.innerHTML = html; };

  set("banner-slot", state.banner
    ? `<div class="banner ${esc(state.banner.kind)}">${esc(state.banner.text)}</div>` : "");

  if (!pv) { set("runner-checks", `<p class="note">载入中…</p>`); return; }

  set("runner-checks", pv.rules.map(r => `
    <div class="check ${r.state}">
      <span class="mk">${r.state === "pass" ? "✓" : r.state === "fail" ? "✕" : "·"}</span>
      <span>${esc(r.cn)}${r.why ? ` <span style="opacity:.8">— ${esc(r.why)}</span>` : ""}</span>
    </div>`).join("") || `<p class="note">没有校验规则。</p>`);

  set("runner-edits", pv.valid && pv.edits.length
    ? pv.edits.map(e => `<div class="edit"><span class="op">${esc(e.op.toUpperCase())}</span>
        <span class="txt">${esc(e.desc)}</span></div>`).join("")
    : `<p class="note">${pv.complete ? "校验未通过，暂无编辑可提交。" : "填完参数后这里会列出将要写入的编辑。"}</p>`);

  const cnt = document.getElementById("runner-count");
  if (cnt) cnt.textContent = pv.valid ? pv.edits.length : 0;

  const can = pv.valid && pv.allowed;
  set("runner-apply", `
    <button class="apply" id="apply" ${can ? "" : "disabled"}>应用操作 · 写回数据库</button>
    <p class="apply-note">${
      !pv.allowed ? `你的角色不能执行这个操作，需要 ${esc(pv.requiredRole || "")}`
      : pv.valid ? "全部编辑在一个事务里提交，并带上你读到的版本号"
      : "校验未通过时无法提交"}</p>`);
}

function renderAudit() {
  const el = document.getElementById("audit-log");
  if (!el) return;
  if (!state.audit.length) { el.innerHTML = `<p class="note">还没有写入。</p>`; return; }
  el.innerHTML = state.audit.map((e, i) => `
    <details class="log-entry" ${i === 0 ? "open" : ""}>
      <summary>
        <span class="ts">${esc(e.ts.slice(11))}</span>
        <span class="who">${esc(e.action)}</span>
        <span style="color:var(--${e.status === "applied" ? "ok" : "bad"})">${e.status === "applied" ? "已提交" : "被拒绝"}</span>
        <span class="n">${esc(e.actor)}</span>
      </summary>
      <div class="log-body">
        <div class="log-params">${esc(JSON.stringify(e.params))}</div>
        ${e.error ? `<div class="check fail"><span class="mk">✕</span><span>${esc(e.error)}</span></div>` : ""}
        ${e.edits.map(x => `<div class="edit"><span class="op">${esc((x.op || "").toUpperCase())}</span>
          <span class="txt">${esc(x.desc || "")}</span></div>`).join("")}
      </div>
    </details>`).join("");
}

/* ---------------------------------------------------------------------
   §8  建模模式
   --------------------------------------------------------------------- */
function renderModelMid() {
  const el = document.getElementById("mid-body");
  if (!state.editing) {
    el.innerHTML = `<div class="empty">
      <p class="lead">这里是本体的类型层 —— 每个类型都绑着一张真实的表。</p>
      从左栏挑一个类型，看它的映射：哪张表、哪些列变成了属性、哪个外键变成了链接。<br><br>
      和离线演练场最大的区别：<b>属性不能凭空加</b>，只能映射一个已经存在的列。
      列不存在，就得先去改 <code>db/schema.sql</code> 建表 —— 那才是 schema 变更的真实成本。</div>`;
    return;
  }
  const { kind, id } = state.editing;
  if (kind === "new-ot") el.innerHTML = newObjectTypeForm();
  else if (kind === "new-lt") el.innerHTML = newLinkTypeForm();
  else if (kind === "new-at") el.innerHTML = newActionTypeForm();
  else if (kind === "ot") el.innerHTML = objectTypeEditor(OT(id));
  else if (kind === "lt") el.innerHTML = linkTypeEditor(LT(id));
  else el.innerHTML = actionTypeEditor(AT(id));
}

/* --- 新建：三种类型的创建成本是递增的 -------------------------------
   操作类型  纯元数据，随时能加
   链接类型  要有一个真实外键（没有就得先在管道里造出关联）
   对象类型  要有一张真实的表 —— 最贵，往往牵动数据工程
   ------------------------------------------------------------------ */

const camel = s => s.split("_").map((w, i) => i ? w.charAt(0).toUpperCase() + w.slice(1) : w).join("");
const pascal = s => s.split("_").map(w => w.charAt(0).toUpperCase() + w.slice(1)).join("");

function newObjectTypeForm() {
  const boundTables = new Set(state.onto.objectTypes.map(t => t.sourceTable));
  const free = state.tables.filter(t => !boundTables.has(t.name));
  if (!free.length) {
    return `<div class="ed"><div class="ed-head"><h3>绑定一张表</h3></div>
      <div class="ed-sec"><p class="warn">数据库里所有的表都已经绑定过了。
      想加一个新的对象类型，得先在 <code>db/schema.sql</code> 里建一张新表、灌上数据、
      重新 <code>python -m server.initdb</code>。<br><br>
      <b>这就是真实顺序：数据先到，本体后到。</b></p></div></div>`;
  }
  const t = free.find(x => x.name === state.draftTable) || free[0];
  const fks = new Set(t.foreignKeys.map(f => f.column));

  return `
    <div class="ed">
      <div class="ed-head">
        <div class="obj-kicker"><span class="pill" style="color:var(--p0);border-color:currentColor">▣ 新建对象类型</span></div>
        <h3>把一张已有的表变成对象类型</h3>
      </div>
      <div class="ed-sec">
        <span class="eyebrow">① 选一张还没绑定的表</span>
        <div class="f-grid">
          <div class="field"><label>数据源表 <code>source_table</code></label>
            <select id="nt-table">${free.map(x =>
              `<option value="${esc(x.name)}" ${x.name === t.name ? "selected" : ""}>${esc(x.name)}（${x.columns.length} 列）</option>`).join("")}</select></div>
          <div class="field"><label>主键列 <code>pk_column</code></label>
            <select id="nt-pk">${t.columns.map(c =>
              `<option value="${esc(c.name)}" ${c.pk ? "selected" : ""}>${esc(c.name)}${c.pk ? "（表主键）" : ""}</option>`).join("")}</select></div>
          <div class="field"><label>API 名</label>
            <input type="text" id="nt-api" value="${esc(pascal(t.name))}"></div>
          <div class="field"><label>中文名</label>
            <input type="text" id="nt-cn" value="${esc(pascal(t.name))}"></div>
          <div class="field"><label>主键前缀 <code>Action 建新对象时用</code></label>
            <input type="text" id="nt-prefix" value="${esc(pascal(t.name).slice(0, 1))}"></div>
        </div>
      </div>
      <div class="ed-sec">
        <span class="eyebrow">② 绑定后会自动映射这些列</span>
        ${t.columns.map(c => {
          const isFk = fks.has(c.name);
          const skip = isFk || c.name === "row_version";
          return `<div class="spec" style="${skip ? "opacity:.7" : ""}">
            <span class="map"><b>${esc(c.name)}</b></span>
            <span class="card-badge">${esc(c.type || "?")}</span>
            ${isFk
              ? `<span class="txt" style="color:var(--p2)">→ 跳过：这是指向 ${esc(t.foreignKeys.find(f => f.column === c.name).refTable)} 的外键，应该建成<b>链接类型</b></span>`
              : c.name === "row_version"
                ? `<span class="txt" style="color:var(--muted)">→ 跳过：乐观锁用</span>`
                : `<span class="txt">→ 属性 <b>${esc(camel(c.name))}</b></span>`}
          </div>`;
        }).join("")}
        <p class="note">
          外键列不会变成属性 —— 这是关系模型到本体最核心的一次转换。
          绑定完记得去建对应的链接类型。
        </p>
      </div>
      <div class="ed-sec">
        <button class="apply" id="nt-create">绑定并创建对象类型</button>
      </div>
    </div>`;
}

function newLinkTypeForm() {
  // 只列真实存在、且还没被映射成链接的外键
  const used = new Set(state.onto.linkTypes.map(l => `${l.fkTable}.${l.fkColumn}`));
  const byTable = new Map(state.onto.objectTypes.map(t => [t.sourceTable, t]));
  const cands = [];
  for (const t of state.tables) {
    for (const fk of t.foreignKeys) {
      const key = `${t.name}.${fk.column}`;
      if (used.has(key)) continue;
      const holder = byTable.get(t.name), target = byTable.get(fk.refTable);
      if (!holder || !target) continue;      // 两端都得先有对象类型
      cands.push({ key, fkTable: t.name, fkColumn: fk.column, holder, target });
    }
  }
  if (!cands.length) {
    return `<div class="ed"><div class="ed-head"><h3>新建链接类型</h3></div>
      <div class="ed-sec"><p class="warn">没有可用的外键了。<br><br>
      链接必须由**真实存在的外键**实现。要么这些外键都已经映射成链接了，
      要么外键两端的表还没有被绑定成对象类型。<br><br>
      真实系统里如果两张表之间本来就没有外键（比如来自两个不同的源系统），
      得先在数据管道里做实体匹配、把关联关系算出来落成一列，才谈得上建链接。</p></div></div>`;
  }
  const c = cands.find(x => x.key === state.draftFk) || cands[0];
  // 外键落在哪一端，决定默认方向：持有外键的那张表通常是"多"的一端
  const defFrom = c.target.id, defTo = c.holder.id;

  return `
    <div class="ed">
      <div class="ed-head">
        <div class="obj-kicker"><span class="pill" style="color:var(--p2);border-color:currentColor">⇄ 新建链接类型</span></div>
        <h3>把一个真实外键变成链接</h3>
      </div>
      <div class="ed-sec">
        <span class="eyebrow">① 挑一个还没映射的外键</span>
        <div class="field"><label>真实外键 <code>来自 PRAGMA foreign_key_list</code></label>
          <select id="nl-fk">${cands.map(x =>
            `<option value="${esc(x.key)}" ${x.key === c.key ? "selected" : ""}>${esc(x.key)} → ${esc(x.target.sourceTable)}</option>`).join("")}</select></div>
        <p class="note">下拉里只有数据库里**真实存在**的外键。凭空写一个列名会被服务端拒绝。</p>
      </div>
      <div class="ed-sec">
        <span class="eyebrow">② 命名与方向</span>
        <div class="f-grid">
          <div class="field"><label>API 名</label>
            <input type="text" id="nl-api" value="${esc(camel(c.fkColumn.replace(/_id$|_no$/, "")) || "newLink")}"></div>
          <div class="field"><label>起点 from</label>
            <select id="nl-from">${state.onto.objectTypes.map(t =>
              `<option value="${esc(t.id)}" ${t.id === defFrom ? "selected" : ""}>${esc(t.id)} ${esc(t.cn)}</option>`).join("")}</select></div>
          <div class="field"><label>终点 to</label>
            <select id="nl-to">${state.onto.objectTypes.map(t =>
              `<option value="${esc(t.id)}" ${t.id === defTo ? "selected" : ""}>${esc(t.id)} ${esc(t.cn)}</option>`).join("")}</select></div>
          <div class="field"><label>基数</label>
            <select id="nl-card">${["1 : 1", "1 : N", "N : 1", "N : N"].map(x =>
              `<option ${x === "1 : N" ? "selected" : ""}>${x}</option>`).join("")}</select></div>
          <div class="field"><label>正向名</label><input type="text" id="nl-fwd" value="关联对象"></div>
          <div class="field"><label>反向名</label><input type="text" id="nl-inv" value="来源对象"></div>
        </div>
        <p class="note">
          外键在 <b>${esc(c.fkTable)}</b> 上。把持有外键的那一端设成 <b>to</b>，
          从 from 出发就是一次单表过滤；反过来就得 JOIN。两种都对，只是 SQL 不同。
        </p>
      </div>
      <div class="ed-sec"><button class="apply" id="nl-create">创建链接类型</button></div>
    </div>`;
}

const ACTION_TEMPLATE = {
  params: [{ id: "target", cn: "目标对象", kind: "object", objectType: "Order" }],
  rules: [],
  edits: [{
    uid: "e1", op: "modify", target: { kind: "param", param: "target" },
    prop: "status", mode: "set", value: { src: "const", value: "已取消" },
  }],
  requiredRole: null,
};

function newActionTypeForm() {
  return `
    <div class="ed">
      <div class="ed-head">
        <div class="obj-kicker"><span class="pill" style="color:var(--write);border-color:currentColor">↯ 新建操作类型</span></div>
        <h3>加一个写入口</h3>
      </div>
      <div class="ed-sec">
        <div class="f-grid">
          <div class="field"><label>API 名</label><input type="text" id="na-api" value="newAction"></div>
          <div class="field"><label>中文名</label><input type="text" id="na-cn" value="新操作"></div>
          <div class="field"><label>需要的角色 <code>留空 = 谁都能跑</code></label>
            <select id="na-role"><option value="">（不限）</option>
              ${["客服", "仓管", "管理员"].map(r => `<option>${r}</option>`).join("")}</select></div>
        </div>
        <div class="field" style="margin-top:10px"><label>一句话说明</label>
          <input type="text" id="na-desc" value=""></div>
      </div>
      <div class="ed-sec">
        <span class="eyebrow">声明式定义</span>
        <div class="field">
          <textarea id="na-json" rows="16" style="font-family:var(--mono);font-size:11.5px">${
            esc(JSON.stringify(ACTION_TEMPLATE, null, 2))}</textarea>
        </div>
        <p class="note">
          这是操作类型最便宜的一点：它是**纯元数据**，不依赖任何表结构改动，
          存进 <code>action_type</code> 表就能立刻执行。
          可视化的规则/编辑构建器在离线演练场里。
        </p>
      </div>
      <div class="ed-sec"><button class="apply" id="na-create">创建操作类型</button></div>
    </div>`;
}

function objectTypeEditor(t) {
  if (!t) return "";
  const admin = isAdmin();
  const tbl = state.tables.find(x => x.name === t.sourceTable);
  const mapped = new Set(t.props.map(p => p.sourceColumn));
  const asLink = new Map();
  state.onto.linkTypes.forEach(l => { if (l.fkTable === t.sourceTable) asLink.set(l.fkColumn, l.id); });

  const rows = t.props.map(p => `
    <div class="spec">
      <span class="lead">属性</span>
      <input class="mini s" value="${esc(p.cn)}" ${admin ? `data-prop-cn="${esc(p.id)}"` : "readonly"}>
      <input class="mini s" value="${esc(p.id)}" readonly>
      <span class="txt">←</span>
      <span class="map"><b>${esc(t.sourceTable)}.${esc(p.sourceColumn)}</b></span>
      <span class="card-badge">${esc(p.type)}</span>
      ${p.scale ? `<span class="map">÷${p.scale}</span>` : ""}
      ${p.valueMap ? `<span class="map">码表 ${esc(Object.keys(p.valueMap).join("/"))}</span>` : ""}
      ${admin ? `<button class="kill" data-unmap="${esc(p.id)}" title="解除映射（不删数据）">✕</button>` : ""}
    </div>`).join("");

  const unmapped = (tbl ? tbl.columns : []).filter(c =>
    !mapped.has(c.name) && !asLink.has(c.name) && c.name !== "row_version");

  return `
    <div class="ed">
      <div class="ed-head">
        <div class="obj-kicker">
          <span class="pill" style="color:${typeColor(t.id)};border-color:currentColor">▣ Object Type</span>
          <span class="src-table">← 表 <b style="color:var(--text-2)">${esc(t.sourceTable)}</b>，主键列 <b style="color:var(--text-2)">${esc(t.pkColumn)}</b></span>
          <span class="rid mono" style="font-size:12px;color:var(--muted)">${state.counts[t.id] ?? 0} 个实例</span>
        </div>
        <h3>${esc(t.cn)} <span class="mono" style="font-size:14px;color:var(--muted)">${esc(t.id)}</span></h3>
      </div>

      <div class="ed-sec">
        <span class="eyebrow">属性映射 Properties</span>
        ${rows}
        ${admin ? `
        <div class="spec" style="background:none;border-style:dashed">
          <span class="lead">新增</span>
          <input class="mini s" id="np-cn" placeholder="中文名">
          <input class="mini s" id="np-id" placeholder="API 名">
          <select class="mini" id="np-type">
            ${["string", "integer", "currency", "enum", "timestamp", "date", "boolean"].map(x =>
              `<option>${x}</option>`).join("")}
          </select>
          <select class="mini" id="np-col">
            ${unmapped.length
              ? unmapped.map(c => `<option value="${esc(c.name)}">${esc(c.name)} (${esc(c.type)})</option>`).join("")
              : `<option value="">（这张表的列都映射完了）</option>`}
          </select>
          <button class="add tight" data-add-prop="${esc(t.id)}">映射</button>
        </div>` : ""}
        <p class="note">
          属性只能映射<b style="color:var(--text-2)">已经存在的列</b>。
          ${unmapped.length ? `这张表还有 ${unmapped.length} 列没映射。` : "这张表的列已经映射完了 ——"}
          想要一个全新的字段，得先改 <code>db/schema.sql</code> 加列再重建库。
          这就是演练场里「一键加属性」掩盖掉的成本。
        </p>
      </div>

      <div class="ed-sec">
        <span class="eyebrow">这张表上没变成属性的列</span>
        ${[...asLink].map(([col, link]) => `
          <div class="spec">
            <span class="map"><b>${esc(t.sourceTable)}.${esc(col)}</b></span>
            <span class="txt">→ 链接类型</span>
            <span class="act-row" style="padding:0"><span class="name">${esc(link)}</span></span>
            <span class="txt" style="color:var(--muted)">外键不是属性</span>
          </div>`).join("") || `<p class="note">这张表上没有外键列。</p>`}
        ${t.hasRowVersion ? `<div class="spec">
          <span class="map"><b>${esc(t.sourceTable)}.row_version</b></span>
          <span class="txt" style="color:var(--muted)">→ 乐观锁用，不暴露给本体</span></div>` : ""}
      </div>

      ${admin ? `<div class="ed-sec">
        <span class="eyebrow">解除绑定</span>
        <button class="ghost danger" data-del-ot="${esc(t.id)}">解除 ${esc(t.id)} 的绑定</button>
        <p class="note" style="margin-top:7px">
          只删本体里的映射，<b style="color:var(--text-2)">业务表 ${esc(t.sourceTable)} 和里面的数据一动不动</b>。
          被链接类型或操作类型引用时会拒绝 —— 真实系统也不会让你抽掉底下的砖。
        </p>
      </div>` : ""}
    </div>`;
}

function linkTypeEditor(l) {
  if (!l) return "";
  return `
    <div class="ed">
      <div class="ed-head">
        <div class="obj-kicker">
          <span class="pill" style="color:var(--p2);border-color:currentColor">⇄ Link Type</span>
          <span class="card-badge">${esc(l.card)}</span>
        </div>
        <h3 class="mono" style="font-size:17px">${esc(l.from)}
          <span style="color:var(--muted)">──${esc(l.id)}──▸</span> ${esc(l.to)}</h3>
      </div>
      <div class="ed-sec">
        <span class="eyebrow">由哪个外键实现</span>
        <div class="spec">
          <span class="map"><b>${esc(l.fkTable)}.${esc(l.fkColumn)}</b></span>
          <span class="txt">这一列不是属性，它就是这条链接本身</span>
        </div>
        <p class="note">
          外键落在 <b style="color:var(--text-2)">${esc(l.fkTable === (OT(l.to) || {}).sourceTable ? "to" : "from")}</b> 端。
          从这一端出发只要单表过滤，从另一端出发就得 JOIN 回去 ——
          <b style="color:var(--text-2)">链接方向和外键方向是两件独立的事</b>。
        </p>
      </div>
      <div class="ed-sec">
        <span class="eyebrow">双向命名</span>
        <div class="f-grid">
          <div class="field"><label>正向 <code>从 ${esc(l.from)} 看过去</code></label>
            <input type="text" value="${esc(l.fwd)}" readonly></div>
          <div class="field"><label>反向 <code>从 ${esc(l.to)} 看回来</code></label>
            <input type="text" value="${esc(l.inv)}" readonly></div>
        </div>
      </div>
      ${isAdmin() ? `<div class="ed-sec">
        <span class="eyebrow">危险操作</span>
        <button class="ghost danger" data-del-link="${esc(l.id)}">解除链接映射 ${esc(l.id)}</button>
        <p class="note" style="margin-top:7px">只删映射，外键列和数据都不动。</p>
      </div>` : ""}
    </div>`;
}

function actionTypeEditor(a) {
  if (!a) return "";
  const g = a.glosses || { rules: [], edits: [] };
  return `
    <div class="ed">
      <div class="ed-head">
        <div class="obj-kicker">
          <span class="pill" style="color:var(--write);border-color:currentColor">↯ Action Type</span>
          ${a.requiredRole ? `<span class="role">需要 ${esc(a.requiredRole)}</span>` : ""}
        </div>
        <h3>${esc(a.cn)} <span class="mono" style="font-size:14px;color:var(--muted)">${esc(a.id)}</span></h3>
      </div>
      <div class="ed-sec"><span class="eyebrow">① 参数</span>
        ${a.params.map(p => `<div class="spec">
          <span class="lead">参数</span>
          <span class="txt">${esc(p.cn)}</span>
          <span class="map"><b>${esc(p.id)}</b> : ${esc(p.kind === "object" ? p.objectType : p.kind)}</span>
          ${p.options ? `<span class="map">${esc(p.options.join(" / "))}</span>` : ""}
        </div>`).join("") || `<p class="note">没有参数。</p>`}
      </div>
      <div class="ed-sec"><span class="eyebrow">② 校验规则</span>
        ${g.rules.map(r => `<div class="spec"><span class="lead">规则</span><span class="txt">${esc(r)}</span></div>`).join("")
          || `<p class="note">没有规则 —— 这个操作永远可以提交。</p>`}
      </div>
      <div class="ed-sec"><span class="eyebrow">③ 写入编辑</span>
        ${g.edits.map(e => `<div class="spec write"><span class="op">${esc(e.op)}</span>
          <span class="txt">${esc(e.txt)}</span></div>`).join("") || `<p class="note">没有编辑。</p>`}
      </div>
      <div class="ed-sec">
        <span class="eyebrow">声明式定义 ${isAdmin() ? "（可改）" : "（只读）"}</span>
        <div class="field">
          <textarea id="at-json" rows="14" ${isAdmin() ? "" : "readonly"}
            style="font-family:var(--mono);font-size:11.5px">${esc(JSON.stringify(
              { params: a.params, rules: a.rules, edits: a.edits, requiredRole: a.requiredRole }, null, 2))}</textarea>
        </div>
        ${isAdmin() ? `<button class="add" data-save-action="${esc(a.id)}">保存到数据库</button>` : ""}
        <p class="note" style="margin-top:7px">
          操作类型就是 <code>action_type</code> 表里的三个 JSON 字段，由服务端解释器求值。
          可视化的规则/编辑构建器在离线演练场里（仓库根目录的 <code>index.html</code>）。
        </p>
      </div>
    </div>`;
}

function renderModelRight() {
  const right = document.getElementById("col-right");
  const log = state.schemaLog.length
    ? state.schemaLog.map(e => `<div class="row"><span class="ts">${esc(e.ts.slice(11))}</span>
        <span>${esc(e.summary)}</span></div>`).join("")
    : `<p class="note">对本体的每一次改动都会记在这里 —— schema 也是数据。</p>`;

  right.innerHTML = `
    <div class="pane-head"><span class="eyebrow">数据源 / Data source</span></div>
    <div class="impact">
      <div class="impact-sec">
        <span class="eyebrow">数据库里的表</span>
        <div class="impact-list">
          ${state.tables.map(t => {
            const bound = state.onto.objectTypes.find(o => o.sourceTable === t.name);
            return `<div class="row"><span class="n">${t.columns.length}</span>
              <span><b class="mono">${esc(t.name)}</b>
              ${bound ? `→ <span style="color:${typeColor(bound.id)}">${esc(bound.id)}</span>`
                      : `<span style="color:var(--muted)">未绑定对象类型</span>`}</span></div>`;
          }).join("")}
        </div>
        <p class="note" style="margin-top:9px">
          对象类型必须绑定一张真实的表。没有数据源就没有实例 ——
          这是全栈版和内存演练场最根本的区别。
        </p>
      </div>
      <div class="impact-sec">
        <span class="eyebrow">真实外键</span>
        <div class="impact-list">
          ${state.tables.flatMap(t => t.foreignKeys.map(fk => {
            const link = state.onto.linkTypes.find(l => l.fkTable === t.name && l.fkColumn === fk.column);
            return `<div class="row"><span class="n"></span><span>
              <span class="map"><b>${esc(t.name)}.${esc(fk.column)}</b></span> → ${esc(fk.refTable)}
              ${link ? `<span style="color:var(--p2)"> = 链接 ${esc(link.id)}</span>`
                     : `<span style="color:var(--muted)"> 未映射成链接</span>`}</span></div>`;
          })).join("") || `<p class="note">没有外键。</p>`}
        </div>
      </div>
      ${!isAdmin() ? `<div class="impact-sec">
        <p class="warn" style="margin:0">当前身份是${esc(state.actor.role)}，只能查看。改本体需要管理员。</p>
      </div>` : ""}
    </div>
    <div class="sub-head"><span class="eyebrow">本体变更记录</span><span class="hair"></span></div>
    <div class="slog">${log}</div>`;
}

/* ---------------------------------------------------------------------
   §9  组装与事件
   --------------------------------------------------------------------- */
function renderAll() {
  renderConcepts(); renderStats(); renderLeft();
  if (state.mode === "model") { renderModelMid(); renderModelRight(); }
  else { renderDetail(); renderRunner(); }
  measureNodes();
}

async function openEditor(kind, id) {
  if (state.mode !== "model") { state.mode = "model"; syncGraph(); }
  state.editing = { kind, id };
  if (!state.tables.length) state.tables = (await api("/tables")).tables;
  state.schemaLog = (await api("/schema-log")).entries;
  renderAll(); kick();
}

async function setMode(m) {
  if (state.mode === m) return;
  state.mode = m; state.highlight = null;
  if (m === "model" && !state.tables.length) {
    state.tables = (await api("/tables")).tables;
    state.schemaLog = (await api("/schema-log")).entries;
  }
  syncGraph(); renderAll(); kick();
}

async function reloadAfterWrite(createdKeys) {
  invalidate();
  await loadOntology();
  await loadActions();
  await loadGraph();
  await loadAudit();
  (createdKeys || []).forEach(flash);
  if (state.selected) {
    const [type, ...rest] = state.selected.split(":");
    try {
      state.detail = await api(`/objects/${encodeURIComponent(type)}/${encodeURIComponent(rest.join(":"))}`);
    } catch { state.selected = null; state.detail = null; }
  }
  await prepareRunner();
  renderAll(); kick();
}

document.addEventListener("click", async ev => {
  const hit = s => ev.target.closest(s);

  const m = hit(".mode"); if (m) return setMode(m.dataset.mode);
  if (hit("#relayout")) return relayout();
  if (hit("#refresh")) return reloadAfterWrite();

  const ed = hit("[data-edit]"); if (ed) return openEditor(ed.dataset.edit, ed.dataset.id);

  const nw = hit("[data-new]");
  if (nw) {
    state.draftTable = null; state.draftFk = null;
    return openEditor("new-" + nw.dataset.new, null);
  }

  if (hit("#nt-create")) {
    const body = {
      apiName: document.getElementById("nt-api").value.trim(),
      displayName: document.getElementById("nt-cn").value.trim(),
      sourceTable: document.getElementById("nt-table").value,
      pkColumn: document.getElementById("nt-pk").value,
      idPrefix: document.getElementById("nt-prefix").value.trim(),
    };
    try {
      const r = await api("/ontology/object-types", { method: "POST", body });
      invalidate(); state.tables = (await api("/tables")).tables;
      await loadOntology(); await loadGraph();
      toast(`已创建 ${r.added}：映射 ${r.mapped.length} 列。${r.hint}`, "ok");
      await openEditor("ot", r.added);
    } catch (e) { toast(e.message); }
    return;
  }

  if (hit("#nl-create")) {
    const [fkTable, fkColumn] = document.getElementById("nl-fk").value.split(".");
    const body = {
      apiName: document.getElementById("nl-api").value.trim(),
      fromType: document.getElementById("nl-from").value,
      toType: document.getElementById("nl-to").value,
      cardinality: document.getElementById("nl-card").value,
      fwdLabel: document.getElementById("nl-fwd").value.trim(),
      invLabel: document.getElementById("nl-inv").value.trim(),
      fkTable, fkColumn,
    };
    try {
      const r = await api("/ontology/link-types", { method: "POST", body });
      await loadOntology(); await loadGraph();
      toast(`已创建链接 ${r.added}`, "ok");
      await openEditor("lt", r.added);
    } catch (e) { toast(e.message); }
    return;
  }

  if (hit("#na-create")) {
    let spec;
    try { spec = JSON.parse(document.getElementById("na-json").value); }
    catch (e) { return toast("JSON 解析失败：" + e.message); }
    const id = document.getElementById("na-api").value.trim();
    if (!id) return toast("API 名不能空");
    if (AT(id)) return toast(`已经有一个叫 ${id} 的操作类型了`);
    try {
      await api(`/ontology/action-types/${encodeURIComponent(id)}`, {
        method: "PUT",
        body: {
          ...spec,
          displayName: document.getElementById("na-cn").value.trim() || id,
          description: document.getElementById("na-desc").value.trim(),
          requiredRole: document.getElementById("na-role").value || null,
        },
      });
      await loadOntology(); await loadActions();
      toast(`已创建操作 ${id}`, "ok");
      await openEditor("at", id);
    } catch (e) { toast(e.message); }
    return;
  }

  const delOt = hit("[data-del-ot]");
  if (delOt) {
    try {
      const r = await api(`/ontology/object-types/${encodeURIComponent(delOt.dataset.delOt)}`, { method: "DELETE" });
      invalidate(); await loadOntology(); await loadGraph();
      state.editing = null; renderAll();
      toast(`已解除绑定，表 ${r.sourceTableKept} 和数据都没动`, "ok");
    } catch (e) { toast(e.message); }
    return;
  }

  const hl = hit("[data-hl]");
  if (hl) {
    const { hl: kind, id } = hl.dataset;
    state.highlight = (state.highlight && state.highlight.kind === kind && state.highlight.id === id)
      ? null : { kind, id };
    renderLeft(); kick(); return;
  }

  const tab = hit("[data-action]");
  if (tab) {
    state.activeAction = tab.dataset.action; state.banner = null;
    await prepareRunner();
    document.getElementById("col-right").scrollTo({ top: 0, behavior: "smooth" });
    return;
  }
  const go = hit("[data-goto]"); if (go) return selectObject(go.dataset.goto);
  const run = hit("[data-run]");
  if (run) {
    state.activeAction = run.dataset.run; state.banner = null;
    await prepareRunner();
    document.getElementById("col-right").scrollTo({ top: 0, behavior: "smooth" });
    return;
  }

  if (hit("#apply")) {
    const action = AT(state.activeAction);
    const btn = document.getElementById("apply");
    btn.disabled = true; state.banner = null;
    try {
      const res = await api(`/actions/${encodeURIComponent(action.id)}/apply`, {
        method: "POST",
        body: { params: state.form, versions: versionsFor(action, state.form) },
      });
      const created = res.edits.filter(e => e.op === "create").map(e => `${e.type}:${e.pk}`);
      if (created.length) state.selected = created[0];
      await reloadAfterWrite(created);
    } catch (e) {
      state.banner = { kind: e.kind, text: e.message };
      await loadAudit();          // 失败也留痕，日志要刷新
      renderRunnerState(); renderAudit(); renderStats();
    }
    return;
  }

  /* --- 建模：改映射 --- */
  const addProp = hit("[data-add-prop]");
  if (addProp) {
    const body = {
      apiName: document.getElementById("np-id").value.trim(),
      displayName: document.getElementById("np-cn").value.trim(),
      dataType: document.getElementById("np-type").value,
      sourceColumn: document.getElementById("np-col").value,
    };
    try {
      await api(`/ontology/properties/${encodeURIComponent(addProp.dataset.addProp)}`,
        { method: "POST", body });
      invalidate(); await loadOntology(); await openEditor("ot", addProp.dataset.addProp);
    } catch (e) { toast(e.message); }
    return;
  }
  const unmap = hit("[data-unmap]");
  if (unmap) {
    try {
      await api(`/ontology/properties/${encodeURIComponent(state.editing.id)}/${encodeURIComponent(unmap.dataset.unmap)}`,
        { method: "DELETE" });
      invalidate(); await loadOntology(); await openEditor("ot", state.editing.id);
    } catch (e) { toast(e.message); }
    return;
  }
  const delLink = hit("[data-del-link]");
  if (delLink) {
    try {
      await api(`/ontology/link-types/${encodeURIComponent(delLink.dataset.delLink)}`, { method: "DELETE" });
      await loadOntology(); await loadGraph(); state.editing = null; renderAll();
    } catch (e) { toast(e.message); }
    return;
  }
  const saveAct = hit("[data-save-action]");
  if (saveAct) {
    let body;
    try { body = JSON.parse(document.getElementById("at-json").value); }
    catch (e) { return toast("JSON 解析失败：" + e.message); }
    const a = AT(saveAct.dataset.saveAction);
    try {
      await api(`/ontology/action-types/${encodeURIComponent(a.id)}`,
        { method: "PUT", body: { ...body, displayName: a.cn, description: a.desc } });
      await loadActions(); await openEditor("at", a.id);
      toast("已保存", "ok");
    } catch (e) { toast(e.message); }
    return;
  }
});

document.addEventListener("input", ev => {
  const f = ev.target.closest("[data-p]");
  if (f) {
    state.form[f.dataset.p] = f.type === "number" ? Number(f.value) : f.value;
    state.banner = null;
    refreshPreview();       // 输入框不重建，光标留在原处
    return;
  }
});

document.addEventListener("change", async ev => {
  // 换了表 / 换了外键，表单要跟着重画（列清单和默认值都变了）
  if (ev.target.id === "nt-table") {
    state.draftTable = ev.target.value; renderModelMid(); return;
  }
  if (ev.target.id === "nl-fk") {
    state.draftFk = ev.target.value; renderModelMid(); return;
  }
  const cn = ev.target.closest("[data-prop-cn]");
  if (cn && state.editing) {
    try {
      await api(`/ontology/properties/${encodeURIComponent(state.editing.id)}/${encodeURIComponent(cn.dataset.propCn)}`,
        { method: "PATCH", body: { displayName: cn.value } });
      await loadOntology(); renderLeft();
    } catch (e) { toast(e.message); }
  }
});

document.getElementById("actor").addEventListener("change", async ev => {
  state.actor = state.users.find(u => u.username === ev.target.value);
  state.banner = null;
  await loadActions();
  renderAll();
});

/* 沙箱环境里 alert 会被静默忽略，提示自己实现 */
let toastTimer = null;
function toast(msg, tone) {
  let el = document.getElementById("toast");
  if (!el) { el = document.createElement("div"); el.id = "toast"; el.className = "toast"; document.body.appendChild(el); }
  el.style.borderLeftColor = `var(--${tone || "bad"})`;
  el.textContent = msg;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { const t = document.getElementById("toast"); if (t) t.remove(); }, 3600);
}

matchMedia("(prefers-color-scheme: dark)").addEventListener("change", refreshTheme);
new MutationObserver(refreshTheme).observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });

(async function boot() {
  refreshTheme();
  try {
    await loadOntology();
    await loadActions();
    await loadGraph();
    await loadAudit();
    await prepareRunner();
    renderAll();
    kick();
  } catch (e) {
    document.getElementById("mid-body").innerHTML =
      `<div class="empty"><p class="lead">连不上后端</p>${esc(e.message)}<br><br>
       先建库：<code>python -m server.initdb</code>，再启动服务。</div>`;
  }
})();
