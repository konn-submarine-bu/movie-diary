"use strict";

/* ---------- helpers ---------- */
const $ = (id) => document.getElementById(id);
const RM = matchMedia("(prefers-reduced-motion: reduce)").matches;
const root = document.documentElement;
const pad = (n) => String(n).padStart(2, "0");
const dkey = (y, m, d) => `${y}-${pad(m + 1)}-${pad(d)}`;
const CN = ["一", "二", "三", "四", "五", "六", "七", "八", "九", "十", "十一", "十二"];
const WEEK = "日一二三四五六";
const isUrl = (u) => typeof u === "string" && /^https?:\/\//.test(u);

function hue(t) {
  let h = 0;
  for (const c of t) h = (h * 31 + c.charCodeAt(0)) % 360;
  return h;
}
function todayKey() {
  const d = new Date();
  return dkey(d.getFullYear(), d.getMonth(), d.getDate());
}
function dateLabel(k) {
  const [y, m, d] = k.split("-").map(Number);
  return `${m}月${d}日 周${WEEK[new Date(y, m - 1, d).getDay()]}`;
}

/* ---------- theme config: edit here to change the look or the genre mapping ---------- */
const THEMES = {
  midnight: { name: "午夜胶片", hint: "剧情 · 默认", bg: "#101116", ac: "#e0a458", fx: "star", light: false },
  fog:      { name: "冷雾",     hint: "悬疑 / 犯罪 / 惊悚", bg: "#0b1214", ac: "#5cc4c0", fx: "rain", light: false },
  neon:     { name: "霓虹",     hint: "科幻 / 奇幻", bg: "#090919", ac: "#8b7bff", fx: "streak", light: false },
  blood:    { name: "暗红",     hint: "恐怖", bg: "#110707", ac: "#e0454b", fx: "ember", light: false },
  olive:    { name: "军绿",     hint: "战争 / 历史 / 冒险", bg: "#11140d", ac: "#b4c46a", fx: "dust", light: false },
  rose:     { name: "蔷薇",     hint: "爱情 / 音乐", bg: "#f7ece8", ac: "#c9506a", fx: "petal", light: true },
  cream:    { name: "奶油",     hint: "喜剧 / 动画", bg: "#fbf1d9", ac: "#ee6c3a", fx: "bokeh", light: true },
  sage:     { name: "鼠尾草",   hint: "家庭 / 纪录", bg: "#e9f0e5", ac: "#3d8c68", fx: "firefly", light: true },
};
// Genre names follow TMDB's zh-CN labels. Genres not listed here (e.g. 动作) don't vote.
const GENRE_THEME = {
  剧情: "midnight",
  悬疑: "fog", 犯罪: "fog", 惊悚: "fog",
  科幻: "neon", 奇幻: "neon",
  恐怖: "blood",
  战争: "olive", 历史: "olive", 西部: "olive", 冒险: "olive",
  爱情: "rose", 音乐: "rose",
  喜剧: "cream", 动画: "cream",
  家庭: "sage", 纪录: "sage",
};
const RECENT_N = 5; // auto theme looks at this many most recent watched movies
const THEME_KEY = "movie-diary.theme";

/* ---------- state ---------- */
const today0 = new Date();
const state = {
  entries: [], byDate: new Map(), undated: [],
  activeId: null, busy: false, chatTok: 0, heroTok: 0, msgSeq: 0, posterEntryId: null,
  y: today0.getFullYear(), m: today0.getMonth(), sel: null, pick: 0, focus: null, editing: null, wish: [], clips: [], otdTok: 0, view: "cal", clipEntryId: null, poem: { entryId: null, style: null, styles: [], busy: false, text: "" },
  mode: "auto", auto: null, theme: null,
};

/* ---------- api / dom ---------- */
class ApiError extends Error {
  constructor(code, message) {
    super(message);
    this.code = code;
  }
}

async function api(method, path, body) {
  let resp;
  try {
    resp = await fetch(path, {
      method,
      headers: body ? { "Content-Type": "application/json" } : undefined,
      body: body ? JSON.stringify(body) : undefined,
    });
  } catch (err) {
    throw new ApiError("network_unreachable", `${err.name}: ${err.message}`);
  }
  if (resp.status === 204) return null;
  let data = null;
  try {
    data = await resp.json();
  } catch (err) {
    throw new ApiError("response_invalid", `HTTP ${resp.status}, body is not JSON (${err.name})`);
  }
  if (!resp.ok) throw new ApiError(data.error_code || "unknown_error", data.error || `HTTP ${resp.status}`);
  return data;
}

function el(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props)) {
    if (key === "class") node.className = value;
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else if (value === true) node.setAttribute(key, "");
    else if (value !== false && value != null) node.setAttribute(key, value);
  }
  for (const child of children) if (child != null) node.append(child);
  return node;
}

function showError(box, err) {
  box.replaceChildren(el("code", {}, err.code || "error"), ` ${err.message}`);
  box.hidden = false;
}
const clearError = (box) => { box.hidden = true; box.replaceChildren(); };

// Render assistant text: plain text nodes, [title](url) becomes a link. Never innerHTML.
function renderText(container, text) {
  const link = /\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g;
  let last = 0;
  for (const m of text.matchAll(link)) {
    container.append(text.slice(last, m.index));
    container.append(el("a", { href: m[2], target: "_blank", rel: "noopener noreferrer" }, m[1]));
    last = m.index + m[0].length;
  }
  container.append(text.slice(last));
}

// Wrap the 「…」 parts of a sentence in <b>.
function emphasize(container, text) {
  text.split(/「(.+?)」/).forEach((part, i) => container.append(i % 2 ? el("b", {}, part) : part));
}

/* ---------- typewriter & count-up ---------- */
const toks = {};
function type(node, text, key, delay = 600, onTick) {
  const tok = (toks[key] = (toks[key] || 0) + 1);
  if (RM) { node.textContent = text; if (onTick) onTick(); return; }
  node.textContent = "";
  node.classList.add("typing");
  let i = 0;
  const tick = () => {
    if (tok !== toks[key]) return;
    node.textContent = text.slice(0, ++i);
    if (onTick) onTick();
    if (i < text.length) setTimeout(tick, 30 + Math.random() * 40);
    else node.classList.remove("typing");
  };
  setTimeout(tick, delay);
}
function countTo(node, to, dec) {
  if (RM) { node.textContent = to.toFixed(dec); return; }
  const t0 = performance.now();
  const f = (t) => {
    const k = Math.min((t - t0) / 800, 1), e = 1 - Math.pow(1 - k, 3);
    node.textContent = (to * e).toFixed(dec);
    if (k < 1) requestAnimationFrame(f);
  };
  requestAnimationFrame(f);
}

/* ---------- particles: one canvas, one kind per theme ---------- */
const cv = $("fx"), cx = cv.getContext("2d");
let W = 0, H = 0, parts = [], fx = null, pending = null, fade = 1, fadeT = 1;
const rnd = (a, b) => a + Math.random() * (b - a);
const COUNT = { star: 90, rain: 120, streak: 26, ember: 56, dust: 70, petal: 30, bokeh: 18, firefly: 44 };

function mk(init) {
  const p = { x: rnd(0, W), y: init ? rnd(0, H) : 0, ph: rnd(0, 6.28), s: rnd(.4, 1) };
  switch (fx.kind) {
    case "star": p.r = rnd(.4, 1.5); p.tw = rnd(.6, 2); break;
    case "rain": p.len = rnd(12, 28); p.v = rnd(520, 820); p.y = init ? rnd(0, H) : -30; break;
    case "streak": p.len = rnd(70, 170); p.v = rnd(90, 210); p.x = init ? rnd(0, W) : -180; p.y = rnd(0, H); break;
    case "ember": p.r = rnd(.8, 2.4); p.v = rnd(22, 64); p.y = init ? rnd(0, H) : H + 10; break;
    case "dust": p.r = rnd(.6, 1.8); p.vx = rnd(4, 14); p.vy = rnd(-4, 4); break;
    case "petal": p.r = rnd(5, 9); p.v = rnd(26, 52); p.y = init ? rnd(0, H) : -20; p.rot = rnd(0, 6.28); p.vr = rnd(-1, 1); break;
    case "bokeh": p.r = rnd(26, 90); p.vx = rnd(-6, 6); p.vy = rnd(-9, -2); p.a = rnd(.06, .16); break;
    case "firefly": p.r = rnd(1, 2.6); p.vx = rnd(-12, 12); p.vy = rnd(-12, 12); break;
  }
  return p;
}
function seed() { if (fx) parts = Array.from({ length: COUNT[fx.kind] }, () => mk(true)); }
function resize() {
  const dpr = Math.min(devicePixelRatio || 1, 2);
  W = innerWidth; H = innerHeight;
  cv.width = W * dpr; cv.height = H * dpr;
  cx.setTransform(dpr, 0, 0, dpr, 0, 0);
  seed(); if (RM) frame(0, 0);
}
function setFx(kind, color, light) {
  pending = { kind, color, light };
  if (RM || !fx) commit(); else fadeT = 0;
}
function commit() { fx = pending; pending = null; seed(); fadeT = 1; if (RM) frame(0, 0); }

function frame(dt, t) {
  cx.clearRect(0, 0, W, H);
  if (!fx) return;
  cx.globalCompositeOperation = fx.light ? "source-over" : "lighter";
  cx.fillStyle = fx.color; cx.strokeStyle = fx.color; cx.lineWidth = 1;
  for (const p of parts) {
    switch (fx.kind) {
      case "star":
        cx.globalAlpha = fade * (.2 + .8 * (.5 + .5 * Math.sin(t * p.tw + p.ph))) * p.s;
        cx.beginPath(); cx.arc(p.x, p.y, p.r, 0, 6.283); cx.fill(); break;
      case "rain":
        p.y += p.v * dt; p.x -= p.v * .15 * dt;
        if (p.y > H + 30) { p.y = -30; p.x = rnd(0, W + 120); }
        cx.globalAlpha = fade * .26 * p.s;
        cx.beginPath(); cx.moveTo(p.x, p.y); cx.lineTo(p.x + p.len * .15, p.y - p.len); cx.stroke(); break;
      case "streak": {
        p.x += p.v * dt; p.y -= p.v * .28 * dt;
        if (p.x > W + 200 || p.y < -80) { p.x = -180; p.y = rnd(0, H); }
        const g = cx.createLinearGradient(p.x - p.len, p.y + p.len * .28, p.x, p.y);
        g.addColorStop(0, "transparent"); g.addColorStop(1, fx.color);
        cx.globalAlpha = fade * .5 * p.s; cx.strokeStyle = g; cx.lineWidth = 1.4;
        cx.beginPath(); cx.moveTo(p.x - p.len, p.y + p.len * .28); cx.lineTo(p.x, p.y); cx.stroke(); break;
      }
      case "ember":
        p.y -= p.v * dt; p.x += Math.sin(t + p.ph) * 12 * dt;
        if (p.y < -10) { p.y = H + 10; p.x = rnd(0, W); }
        cx.globalAlpha = fade * (.12 + .75 * (p.y / H)) * p.s;
        cx.beginPath(); cx.arc(p.x, p.y, p.r, 0, 6.283); cx.fill(); break;
      case "dust":
        p.x += p.vx * dt; p.y += p.vy * dt;
        if (p.x > W + 5) p.x = -5; if (p.y < -5) p.y = H + 5; if (p.y > H + 5) p.y = -5;
        cx.globalAlpha = fade * (.2 + .15 * Math.sin(t + p.ph));
        cx.beginPath(); cx.arc(p.x, p.y, p.r, 0, 6.283); cx.fill(); break;
      case "petal":
        p.y += p.v * dt; p.x += (Math.sin(t * 1.1 + p.ph) * 22 + 8) * dt; p.rot += p.vr * dt;
        if (p.y > H + 20) { p.y = -20; p.x = rnd(0, W); }
        cx.globalAlpha = fade * .5 * p.s + .1;
        cx.beginPath(); cx.ellipse(p.x, p.y, p.r, p.r * .55, p.rot, 0, 6.283); cx.fill(); break;
      case "bokeh":
        p.x += p.vx * dt; p.y += p.vy * dt;
        if (p.y < -p.r) { p.y = H + p.r; p.x = rnd(0, W); }
        if (p.x < -p.r) p.x = W + p.r; if (p.x > W + p.r) p.x = -p.r;
        cx.globalAlpha = fade * p.a;
        cx.beginPath(); cx.arc(p.x, p.y, p.r, 0, 6.283); cx.fill(); break;
      case "firefly": {
        p.vx = Math.max(-18, Math.min(18, p.vx + rnd(-1, 1) * 30 * dt));
        p.vy = Math.max(-18, Math.min(18, p.vy + rnd(-1, 1) * 30 * dt));
        p.x += p.vx * dt; p.y += p.vy * dt;
        if (p.x < 0) p.x = W; if (p.x > W) p.x = 0; if (p.y < 0) p.y = H; if (p.y > H) p.y = 0;
        const a = (.3 + .7 * (.5 + .5 * Math.sin(t * 1.4 + p.ph))) * fade;
        const g = cx.createRadialGradient(p.x, p.y, 0, p.x, p.y, p.r * 7);
        g.addColorStop(0, fx.color); g.addColorStop(1, "transparent");
        cx.globalAlpha = a * (fx.light ? .6 : 1); cx.fillStyle = g;
        cx.beginPath(); cx.arc(p.x, p.y, p.r * 7, 0, 6.283); cx.fill(); cx.fillStyle = fx.color; break;
      }
    }
  }
  cx.globalAlpha = 1;
}


/* ---------- pointer-driven light and cover tilt ---------- */
let mx = innerWidth * .7, my = innerHeight * .3, tx = mx, ty = my, last = performance.now();
addEventListener("pointermove", (e) => { tx = e.clientX; ty = e.clientY; });
function loop(now) {
  const dt = Math.min((now - last) / 1000, .05); last = now;
  const k = Math.min(dt * 6, 1);
  mx += (tx - mx) * k; my += (ty - my) * k;
  $("light").style.transform = `translate(${mx}px,${my}px)`;
  const hero = $("hero");
  hero.style.setProperty("--rx", (((my / H) - .5) * -9).toFixed(2) + "deg");
  hero.style.setProperty("--ry", (((mx / W) - .5) * 14).toFixed(2) + "deg");
  hero.style.setProperty("--gx", (50 + (mx / W - .5) * 80).toFixed(1) + "%");
  if (fade !== fadeT) {
    fade += Math.sign(fadeT - fade) * dt * 3;
    fade = fadeT === 0 ? Math.max(0, fade) : Math.min(1, fade);
    if (pending && fade <= 0) commit();
  }
  frame(dt, now / 1000);
  requestAnimationFrame(loop);
}

/* ---------- theme ---------- */
// Auto theme: vote with the most recent RECENT_N movies watched up to the end of the viewed month.
function autoTheme() {
  const cutoff = dkey(state.y, state.m, new Date(state.y, state.m + 1, 0).getDate());
  const recent = state.entries
    .filter((e) => e.status !== "wishlist" && validDate(e.watched_on) && e.watched_on <= cutoff)
    .sort((a, b) => b.watched_on.localeCompare(a.watched_on) || b.id - a.id)
    .slice(0, RECENT_N);
  const score = {}, count = {}, labels = {};
  for (const e of recent) {
    const hit = [...new Set((e.genres || []).map((g) => GENRE_THEME[g]).filter(Boolean))];
    if (!hit.length) continue;
    const w = (0.7 + 0.1 * (e.rating || 3)) / hit.length;
    for (const k of hit) {
      score[k] = (score[k] || 0) + w;
      count[k] = (count[k] || 0) + 1;
      (labels[k] ||= new Set());
      e.genres.filter((g) => GENRE_THEME[g] === k).forEach((g) => labels[k].add(g));
    }
  }
  const key = Object.keys(score).sort((a, b) => score[b] - score[a])[0];
  if (!key) {
    return { key: "midnight", reason: recent.length
      ? "最近的片子还没有类型信息，先用默认的「午夜胶片」。"
      : "还没有观影记录，先用默认的「午夜胶片」。" };
  }
  const tag = [...labels[key]].slice(0, 2).join(" / ");
  return { key, reason: `最近看的 ${recent.length} 部里，${count[key]} 部偏「${tag}」，所以用「${THEMES[key].name}」。` };
}
function applyTheme(key) {
  if (state.theme === key) return;
  state.theme = key;
  const t = THEMES[key];
  root.dataset.theme = key;
  root.classList.toggle("is-light", t.light);
  setFx(t.fx, t.ac, t.light);
}
function refreshTheme() {
  state.auto = autoTheme();
  const key = state.mode === "auto" ? state.auto.key : state.mode;
  applyTheme(key);
  const why = $("why");
  why.replaceChildren();
  if (state.mode === "auto") emphasize(why, state.auto.reason);
  else emphasize(why, `已手动锁定「${THEMES[key].name}」，点「自动」恢复按观影类型换色。`);
  document.querySelectorAll("#sw [data-k]").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.k === state.mode)));
}
function setMode(mode) {
  state.mode = mode;
  try { localStorage.setItem(THEME_KEY, mode); } catch { /* storage unavailable: choice just won't persist */ }
  refreshTheme();
  renderStats(false);
}
function initSwatches() {
  const sw = $("sw");
  sw.append(el("button", { type: "button", class: "auto", "data-k": "auto", onclick: () => setMode("auto") }, "自动"));
  for (const [k, t] of Object.entries(THEMES)) {
    const b = el("button", {
      type: "button", class: "dot", "data-k": k, title: `${t.name} · ${t.hint}`,
      "aria-label": `${t.name}，${t.hint}`, onclick: () => setMode(k),
    });
    b.style.background = `linear-gradient(135deg, ${t.bg} 48%, ${t.ac} 52%)`;
    b.style.boxShadow = `0 0 0 1px ${t.ac}55`;
    sw.append(b);
  }
}

/* ---------- data ---------- */
const validDate = (s) => /^\d{4}-\d{2}-\d{2}$/.test(s || "");

function indexEntries() {
  state.byDate = new Map();
  state.undated = [];
  state.wish = state.entries.filter((e) => e.status === "wishlist");
  for (const e of state.entries) {
    if (e.status === "wishlist") continue;
    if (validDate(e.watched_on)) {
      if (!state.byDate.has(e.watched_on)) state.byDate.set(e.watched_on, []);
      state.byDate.get(e.watched_on).push(e);
    } else {
      state.undated.push(e);
    }
  }
}
async function loadEntries() {
  const data = await api("GET", "/api/entries");
  state.entries = data.entries;
  indexEntries();
}
const entryById = (id) => state.entries.find((e) => e.id === id);

// Re-render everything that depends on the entry list without replaying entrance animations.
function refreshAll() {
  renderMonth(false);
  renderHero(true);
  renderWall();
  renderWish();
  renderOtd();
}

/* ---------- cover / orb images ---------- */
function coverImg(e) {
  if (!isUrl(e.poster_url)) return null;
  const img = el("img", { src: e.poster_url, alt: "", loading: "lazy", referrerpolicy: "no-referrer" });
  img.addEventListener("error", () => img.remove());
  return img;
}
function coverBox(e, cls = "cover") {
  const box = el("div", { class: cls, style: `--h:${hue(e.title)}` });
  const img = coverImg(e);
  if (img) box.append(img);
  box.append(el("span", {}, e.title));
  return box;
}

/* ---------- month / calendar ---------- */
function monthEntries() {
  const prefix = `${state.y}-${pad(state.m + 1)}`;
  return state.entries.filter((e) => e.status !== "wishlist" && validDate(e.watched_on) && e.watched_on.startsWith(prefix));
}
function renderStats(animate) {
  const list = monthEntries(), box = $("stats");
  box.replaceChildren();
  if (!list.length) { box.textContent = "这个月还是空白"; return; }
  const rated = list.filter((e) => e.rating);
  const avg = rated.length ? rated.reduce((s, e) => s + e.rating, 0) / rated.length : null;
  const num = (to, dec) => {
    const b = el("b", {}, animate ? "0" : to.toFixed(dec));
    if (animate) countTo(b, to, dec);
    return b;
  };
  box.append(num(list.length, 0), " 部");
  if (avg != null) box.append(el("i", {}, "·"), "均分 ", num(avg, 1));
  box.append(el("i", {}, "·"), "色调 ", el("b", {}, THEMES[state.mode === "auto" ? state.auto.key : state.mode].name));
}
function renderMonth(animate = true) {
  const { y, m } = state;
  $("mnum").replaceChildren(String(m + 1), el("small", {}, "月"));
  $("mnum").setAttribute("aria-label", `${y}年${m + 1}月`);
  $("msub").textContent = `${y} · ${CN[m]}月`;

  refreshTheme();
  renderStats(animate);

  const grid = $("grid"), frag = document.createDocumentFragment(), tk = todayKey();
  grid.classList.toggle("no-anim", !animate);
  for (const w of "一二三四五六日") frag.append(el("div", { class: "dow", "aria-hidden": "true" }, w));
  const first = (new Date(y, m, 1).getDay() + 6) % 7, total = new Date(y, m + 1, 0).getDate();
  for (let i = 0; i < first; i++) frag.append(el("div", { class: "day blank", "aria-hidden": "true" }));
  for (let d = 1; d <= total; d++) {
    const k = dkey(y, m, d), list = state.byDate.get(k) || [];
    const b = el("button", {
      type: "button", class: "day" + (list.length ? " has" : "") + (k === tk ? " today" : "") + (k === state.sel && state.focus == null ? " sel" : ""),
      "data-k": k, style: `--i:${first + d}`, "aria-pressed": String(k === state.sel && state.focus == null),
      "aria-label": `${m + 1}月${d}日` + (list.length ? `，看了${list.map((x) => `《${x.title}》`).join("、")}` : "，没有记录"),
      onclick: () => selectDay(k),
    });
    list.slice(0, 2).forEach((e, idx) => {
      const orb = el("i", { class: "orb" + (idx ? " b" : ""), style: `--h:${hue(e.title)}` });
      const img = coverImg(e);
      if (img) orb.append(img);
      b.append(orb);
    });
    b.append(el("span", { class: "n" }, String(d)));
    if (list.length) b.append(el("span", { class: "tip" }, list.map((x) => x.title).join(" · ")));
    frag.append(b);
  }
  grid.replaceChildren(frag);

  const und = $("undated");
  und.hidden = !state.undated.length;
  und.replaceChildren();
  if (state.undated.length) {
    und.append(`${state.undated.length} 部没有标日期：`);
    state.undated.forEach((e) => und.append(el("button", { type: "button", onclick: () => focusEntry(e.id) }, e.title)));
  }
}

function defaultDay() {
  const { y, m } = state;
  const now = new Date();
  if (y === now.getFullYear() && m === now.getMonth()) return todayKey();
  const prefix = `${y}-${pad(m + 1)}`;
  const days = [...state.byDate.keys()].filter((k) => k.startsWith(prefix)).sort();
  return days.length ? days[days.length - 1] : `${prefix}-01`;
}
function markSelected() {
  document.querySelectorAll("#grid .day[data-k]").forEach((b) => {
    const on = state.focus == null && b.dataset.k === state.sel;
    b.classList.toggle("sel", on);
    b.setAttribute("aria-pressed", String(on));
  });
}
function selectDay(k) {
  state.sel = k;
  state.focus = null;
  state.pick = 0;
  state.editing = null;
  markSelected();
  renderHero(false);
}
function focusEntry(id) {
  state.focus = id;
  state.pick = 0;
  state.editing = null;
  markSelected();
  renderHero(false);
}
function goTo(k) {
  const [y, m] = k.split("-").map(Number);
  state.y = y; state.m = m - 1; state.sel = k; state.focus = null;
  renderMonth(true);
  selectDay(k);
}
function shiftMonth(dm) {
  const d = new Date(state.y, state.m + dm, 1);
  state.y = d.getFullYear(); state.m = d.getMonth();
  state.sel = defaultDay(); state.focus = null;
  renderMonth(true);
  selectDay(state.sel);
  if (!RM) $("mnum").animate(
    [{ opacity: 0, transform: `translateY(${dm * 16}px)`, filter: "blur(10px)" }, { opacity: 1, transform: "none", filter: "blur(0)" }],
    { duration: 700, easing: "cubic-bezier(.2,.7,.2,1)" });
}

/* ---------- hero ---------- */
function heroList() {
  if (state.focus != null) { const e = entryById(state.focus); return e ? [e] : []; }
  return state.byDate.get(state.sel) || [];
}
function ratingButtons(e) {
  return el("span", { class: "rate", role: "group", "aria-label": "评分" },
    ...[1, 2, 3, 4, 5].map((n) => el("button", {
      type: "button", class: n <= (e.rating || 0) ? "on" : "", "aria-label": `${n} 星`,
      "aria-pressed": String(n === e.rating), onclick: () => patchEntry(e.id, { rating: n }),
    }, "★")));
}
function editForm(e) {
  const note = el("textarea", { rows: "3", maxlength: "2000", "aria-label": "一句话感受", placeholder: "一句话感受" });
  note.value = e.note || "";
  const tags = el("input", { type: "text", "aria-label": "标签，用逗号分隔", placeholder: "标签，用逗号分隔" });
  tags.value = (e.tags || []).join(", ");
  const date = el("input", { type: "date", "aria-label": "观影日期" });
  date.value = validDate(e.watched_on) ? e.watched_on : "";
  const form = el("form", { class: "edit" }, note, tags, date,
    el("div", { class: "acts" },
      el("button", { type: "submit", class: "lnk strong" }, "保存"),
      el("button", { type: "button", class: "lnk", onclick: () => { state.editing = null; renderHero(true); } }, "取消")));
  form.addEventListener("submit", (ev) => {
    ev.preventDefault();
    const list = tags.value.split(/[,，]/).map((t) => t.trim()).filter(Boolean);
    patchEntry(e.id, { note: note.value.trim(), tags: list, watched_on: date.value });
  });
  return form;
}
function heroEmpty(hero) {
  const future = state.sel > todayKey();
  hero.append(el("div", { class: "txt" },
    el("p", { class: "eyebrow" }, state.entries.length ? dateLabel(state.sel) : "电影日记"),
    el("h1", { class: "title faint" }, state.entries.length
      ? (future ? "这一天还没到来" : "这一天，银幕是黑的")
      : "日记还是空的"),
    el("p", { class: "sub" }, state.entries.length
      ? (future ? "" : "没有记录。想补记的话，搜索一部电影。")
      : "搜索一部电影，记下你的第一句感受。"),
    el("div", { class: "acts" }, el("button", { type: "button", class: "lnk", onclick: () => $("search-input").focus() }, "+ 记一部 →"))));
}
function renderHero(quiet) {
  const hero = $("hero"), list = heroList(), tok = ++state.heroTok;
  hero.className = "hero" + (quiet ? " quiet" : "") + (list.length ? "" : " empty");
  hero.replaceChildren();
  if (!list.length) {
    heroEmpty(hero);
    if (state.activeId != null) setScope(null);
    return;
  }
  state.pick = Math.min(state.pick, list.length - 1);
  const e = list[state.pick], h = hue(e.title);
  root.style.setProperty("--mh", h);
  if (state.activeId != null && state.activeId !== e.id) setScope(null);

  const wished = e.status === "wishlist";
  const eyebrow = (wished ? "想看" : state.focus != null ? "未标日期" : dateLabel(state.sel)) + (list.length > 1 ? ` · 第 ${state.pick + 1} / ${list.length} 部` : "");
  const meta = [e.year, e.director, (e.genres || []).join(" / ")].filter(Boolean).join(" · ");
  const title = el("h1", { class: "title", "aria-label": e.title },
    ...[...e.title].map((c, i) => el("span", { "aria-hidden": "true", style: `--c:${i}` }, c === " " ? " " : c)));
  const noteBox = el("blockquote", { class: "note" });
  const gallery = el("div", { class: "gallery" });
  const txt = el("div", { class: "txt" },
    el("p", { class: "eyebrow" }, eyebrow), title,
    el("p", { class: "sub" }, el("span", {}, meta || "—"), ratingButtons(e)));

  if (state.editing === e.id) {
    txt.append(editForm(e));
  } else {
    if (e.note) {
      const typed = el("span", { class: "ty", "aria-hidden": "true" });
      noteBox.append(el("span", { class: "sr-only" }, e.note), typed);
      type(typed, e.note, "note", quiet ? 0 : 600);
    } else {
      noteBox.classList.add("faint");
      noteBox.append("还没有写下感受。点「编辑」补一句。");
    }
    txt.append(noteBox);
    if ((e.tags || []).length) txt.append(el("p", { class: "tags" }, ...e.tags.map((t) => el("span", {}, t))));
    if (list.length > 1) {
      txt.append(el("div", { class: "picker", role: "tablist", "aria-label": "当天的电影" },
        ...list.map((x, i) => el("button", {
          type: "button", role: "tab", "aria-selected": String(i === state.pick),
          onclick: () => { state.pick = i; state.editing = null; renderHero(false); },
        }, x.title))));
    }
    const link = (label, fn, cls = "") => el("button", { type: "button", class: `lnk ${cls}`.trim(), onclick: fn }, label);
    txt.append(el("div", { class: "acts" },
      wished ? link("看完了 →", () => markWatched(e), "strong") : null,
      link("聊聊这部 →", () => { setScope(e.id); $("chat-input").focus(); }),
      wished ? null : link("一键成诗", () => openPoemDialog(e), "strong"),
      link("编辑", () => { state.editing = e.id; renderHero(true); }),
      link("删除", () => removeEntry(e), "danger")));
    txt.append(el("div", { class: "acts sub-acts" },
      link("+ 收藏台词", () => openClipDialog(e)),
      link("+ 上传截图", () => pickShot(e)),
      wished ? null : link("台词海报", () => openPosterDialog(e))));
    txt.append(gallery);
  }
  hero.append(coverBox(e), txt);
  if (state.editing !== e.id) loadGallery(e.id, gallery, tok);
}
async function loadGallery(id, box, tok) {
  try {
    const d = await api("GET", `/api/entries/${id}`);
    if (tok !== state.heroTok) return;
    const quotes = (d.clips || []).filter((c) => c.kind === "quote");
    const shots = (d.clips || []).filter((c) => c.kind === "shot");
    box.replaceChildren(
      ...(d.poems || []).map((p) => poemCard(p)),
      ...quotes.map((c) => quoteCard(c, id)),
      ...shots.map((c) => shotThumb(c, id)),
      ...(d.posters || []).map(posterImg));
  } catch (err) {
    if (tok === state.heroTok) box.replaceChildren(el("span", { class: "notice" }, `海报加载失败 ${err.code}: ${err.message}`));
  }
}

/* ---------- entry edits ---------- */
async function patchEntry(id, fields) {
  clearError($("chat-error"));
  try {
    await api("PATCH", `/api/entries/${id}`, fields);
    await loadEntries();
    state.editing = null;
    if (fields.watched_on && validDate(fields.watched_on) && fields.watched_on !== state.sel) {
      state.y = Number(fields.watched_on.slice(0, 4)); state.m = Number(fields.watched_on.slice(5, 7)) - 1;
      state.sel = fields.watched_on; state.focus = null;
      renderMonth(true);
      markSelected();
      state.pick = Math.max(0, (state.byDate.get(state.sel) || []).findIndex((x) => x.id === id));
      renderHero(false);
      renderWall();
    } else {
      refreshAll();
    }
  } catch (err) {
    showError($("chat-error"), err);
  }
}
async function removeEntry(e) {
  if (!confirm(`删除《${e.title}》及其对话和海报？此操作不可恢复。`)) return;
  try {
    await api("DELETE", `/api/entries/${e.id}`);
    await loadEntries();
    state.editing = null;
    if (state.focus === e.id) state.focus = null;
    if (state.activeId === e.id) setScope(null);
    refreshAll();
  } catch (err) {
    showError($("chat-error"), err);
  }
}

/* ---------- search & add ---------- */
const results = () => $("search-results");
function closeResults() { results().hidden = true; }
async function onSearch(ev) {
  ev.preventDefault();
  const q = $("search-input").value.trim();
  clearError($("diary-error"));
  if (!q) return closeResults();
  results().replaceChildren(el("p", { class: "muted" }, "搜索中…"));
  results().hidden = false;
  try {
    const { results: found } = await api("GET", `/api/movies/search?q=${encodeURIComponent(q)}`);
    if (!found.length) {
      results().replaceChildren(el("p", { class: "muted" }, "没有找到匹配的电影"));
      return;
    }
    results().replaceChildren(...found.map((r) => {
      const th = isUrl(r.poster_url)
        ? el("img", { class: "thumb", src: r.poster_url, alt: "", loading: "lazy", referrerpolicy: "no-referrer" })
        : el("div", { class: "thumb empty" });
      return el("div", { class: "result" }, th,
        el("div", {},
          el("div", { class: "card-title" }, r.title),
          el("div", { class: "card-meta" }, `${r.year || "年份未知"}${r.original_title ? " · " + r.original_title : ""}`),
          el("div", { class: "result-acts" },
            el("button", { type: "button", onclick: () => addMovie(r, "watched") }, "看过"),
            el("button", { type: "button", onclick: () => addMovie(r, "wishlist") }, "想看"))));
    }));
  } catch (err) {
    closeResults();
    showError($("diary-error"), err);
  }
}
async function addMovie(movie, status = "watched") {
  clearError($("diary-error"));
  const tk = todayKey();
  const watched = state.focus == null && validDate(state.sel) && state.sel <= tk ? state.sel : tk;
  try {
    const entry = await api("POST", "/api/entries", {
      tmdb_id: movie.tmdb_id, title: movie.title, original_title: movie.original_title,
      year: movie.year, poster_url: movie.poster_url, status,
      watched_on: status === "watched" ? watched : "",
    });
    closeResults();
    $("search-input").value = "";
    await loadEntries();
    if (status === "wishlist") {
      renderMonth(false); renderWall(); renderWish();
      setView("wish");
      return;
    }
    state.editing = entry.id;
    goTo(watched);
    state.pick = Math.max(0, (state.byDate.get(watched) || []).findIndex((x) => x.id === entry.id));
    renderHero(false);
    renderWall(); renderWish(); renderOtd();
  } catch (err) {
    closeResults();
    showError($("diary-error"), err);
  }
}

/* ---------- chat ---------- */
const chatPath = () => (state.activeId == null ? "/api/chat" : `/api/entries/${state.activeId}/chat`);

function posterImg(p) {
  return el("img", { src: p.url, alt: `台词海报：${p.quote}`, loading: "lazy", onclick: () => openImage(p) });
}
function addMessage(m, animate = false) {
  const box = $("messages");
  const body = el("div", { class: `m ${m.role === "assistant" ? "a" : "u"}` });
  const text = el("span", {});
  body.append(text);
  box.append(body);
  const scroll = () => { box.scrollTop = box.scrollHeight; };
  if (animate && !/\]\(https?:\/\//.test(m.content)) type(text, m.content, `chat${++state.msgSeq}`, 0, scroll);
  else renderText(text, m.content);
  if (m.posters && m.posters.length) {
    body.append(el("div", { class: "imgs" }, ...m.posters.map(posterImg)));
    for (const p of m.posters) {
      if (p.fallback_code) {
        body.append(el("div", { class: "notice" }, `生图失败，已使用渐变背景（${p.fallback_code}）：${p.fallback_message}`));
      }
    }
  }
  const sources = ((m.meta && m.meta.sources) || []).filter((s) => isUrl(s.url));
  if (sources.length) {
    body.append(el("div", { class: "sources" }, "来源",
      ...sources.map((s) => el("a", { href: s.url, target: "_blank", rel: "noopener noreferrer" }, s.title || s.url))));
  }
  scroll();
  return body;
}
async function loadChat() {
  const tok = ++state.chatTok;
  const { messages } = await api("GET", chatPath());
  if (tok !== state.chatTok) return;
  $("messages").replaceChildren();
  messages.forEach((m) => addMessage(m, false));
}
function renderSuggestions() {
  const picks = state.activeId == null
    ? ["根据我的日记推荐一部片", "我最近的观影口味是什么样的"]
    : ["我看完最大的感受是…", "帮我找找这部片的解读", "这句台词做成海报", "我有个模糊的灵感，帮我展开"];
  $("suggestions").replaceChildren(...picks.map((t) => el("button", {
    type: "button", onclick: () => {
      if (t === "这句台词做成海报") return openPosterDialog(entryById(state.activeId));
      $("chat-input").value = t.endsWith("…") ? t.slice(0, -1) : t;
      $("chat-input").focus();
    },
  }, t)));
}
function setScope(id) {
  state.activeId = id;
  clearError($("chat-error"));
  const e = id == null ? null : entryById(id);
  $("chat-scope").textContent = e ? `《${e.title}》 · 单片对话` : "全局对话 · 聊口味、要推荐";
  $("global-btn").hidden = id == null;
  renderSuggestions();
  loadChat().catch((err) => showError($("chat-error"), err));
}
async function onSend(ev) {
  ev.preventDefault();
  const input = $("chat-input");
  const text = input.value.trim();
  if (!text || state.busy) return;
  state.busy = true;
  $("send-btn").disabled = true;
  clearError($("chat-error"));
  const scope = state.activeId;
  addMessage({ role: "user", content: text });
  input.value = "";
  const pendingMsg = el("div", { class: "m a pending" }, "正在思考…");
  $("messages").append(pendingMsg);
  $("messages").scrollTop = $("messages").scrollHeight;
  try {
    const res = await api("POST", chatPath(), { message: text });
    pendingMsg.remove();
    if (scope === state.activeId) addMessage(res.message, true);
    if (res.diary_changed) {
      await loadEntries();
      refreshAll();
    }
  } catch (err) {
    pendingMsg.remove();
    showError($("chat-error"), err);
    input.value = text;
  } finally {
    state.busy = false;
    $("send-btn").disabled = false;
    input.focus();
  }
}
async function onClear() {
  if (!confirm("清空当前对话记录？日记条目不受影响。")) return;
  clearError($("chat-error"));
  try {
    await api("DELETE", chatPath());
    $("messages").replaceChildren();
  } catch (err) {
    showError($("chat-error"), err);
  }
}

/* ---------- poster dialog ---------- */
/* ---------- poster dialog: pick quotes, then collage or AI ---------- */
const PQ = { entryId: null, mode: "collage", groups: [], looked: false };
const quoteLines = () => $("poster-quote").value.split("\n").map((s) => s.trim()).filter(Boolean);
const quoteLimit = () => (PQ.mode === "collage" ? 4 : 1);

function setPosterMode(mode) {
  PQ.mode = mode;
  clearError($("poster-error"));
  document.querySelectorAll(".modes button").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.mode === mode)));
  $("poster-submit").textContent = mode === "collage" ? "拼贴生成" : "AI 生成";
  syncQuotePicks();
}
function syncQuotePicks() {
  const lines = quoteLines(), limit = quoteLimit();
  const over = lines.length > limit;
  $("poster-count").textContent = lines.length
    ? `已选 ${lines.length} 句，${PQ.mode === "collage" ? "最多 4 句" : "AI 生图只用 1 句"}${over ? "，请删去多余的" : ""}`
    : (PQ.mode === "collage" ? "最多 4 句，句子越多版面越满" : "AI 生图只用 1 句");
  $("poster-count").classList.toggle("warn", over);
  document.querySelectorAll("#quote-cands input[type=checkbox]").forEach((cb) => {
    cb.checked = lines.includes(cb.value);
    cb.disabled = !cb.checked && lines.length >= limit;
  });
  const picked = [...document.querySelectorAll("#quote-cands input:checked")].filter((cb) => cb.dataset.fresh === "1");
  $("quote-save").hidden = !picked.length;
}
function toggleQuote(text, on) {
  const lines = quoteLines().filter((l) => l !== text);
  if (on) lines.push(text);
  $("poster-quote").value = lines.join("\n");
  syncQuotePicks();
}
function candRow(q, fresh) {
  return el("label", { class: "cand" },
    el("input", { type: "checkbox", value: q.text, "data-fresh": fresh ? "1" : "0", onchange: (ev) => toggleQuote(q.text, ev.target.checked) }),
    el("span", {}, q.text, q.speaker ? el("em", {}, ` — ${q.speaker}`) : null));
}
function renderCands(saved) {
  const box = $("quote-cands");
  box.replaceChildren();
  if (saved.length) {
    box.append(el("div", { class: "cand-group" }, el("h4", {}, "我收藏的台词"), ...saved.map((q) => candRow(q, false))));
  }
  PQ.groups.forEach((g) => {
    const fresh = g.quotes.filter((q) => !q.saved);
    if (!fresh.length) return;
    box.append(el("div", { class: "cand-group" },
      el("h4", {}, g.lang === "zh" ? "Wikiquote 中文" : "Wikiquote 英文原文",
        el("a", { href: g.source, target: "_blank", rel: "noopener noreferrer" }, " 来源 ↗")),
      ...fresh.map((q) => candRow(q, true))));
  });
  if (PQ.looked && !PQ.groups.length) {
    box.append(el("p", { class: "muted" }, "Wikiquote 没有这部片的台词。可以直接在上面输入，或先去「收藏」里存几句。"));
  }
  syncQuotePicks();
}
async function lookupQuotes() {
  const btn = $("quote-lookup");
  btn.disabled = true; btn.textContent = "查询中…";
  clearError($("poster-error"));
  try {
    const d = await api("GET", `/api/entries/${PQ.entryId}/quote-candidates`);
    PQ.groups = d.groups; PQ.looked = true;
    renderCands(await savedQuotes());
  } catch (err) {
    showError($("poster-error"), err);
  } finally {
    btn.disabled = false; btn.textContent = "重新查询";
  }
}
async function savedQuotes() {
  const d = await api("GET", `/api/entries/${PQ.entryId}`);
  return (d.clips || []).filter((c) => c.kind === "quote");
}
async function saveSelectedQuotes() {
  const texts = [...document.querySelectorAll("#quote-cands input:checked")].filter((cb) => cb.dataset.fresh === "1").map((cb) => cb.value);
  const btn = $("quote-save");
  btn.disabled = true;
  clearError($("poster-error"));
  let failed = null;
  for (const text of texts) {
    try {
      await api("POST", `/api/entries/${PQ.entryId}/clips`, { kind: "quote", text });
    } catch (err) {
      if (err.code !== "clip_already_exists") failed = err;
    }
  }
  btn.disabled = false;
  if (failed) showError($("poster-error"), failed);
  const lines = quoteLines();
  await lookupQuotes();
  $("poster-quote").value = lines.join("\n");
  syncQuotePicks();
  renderHero(true);
}
function openPosterDialog(entry) {
  if (!entry) return;
  Object.assign(PQ, { entryId: entry.id, groups: [], looked: false });
  state.posterEntryId = entry.id;
  $("poster-movie").textContent = `《${entry.title}》`;
  $("poster-quote").value = "";
  $("quote-lookup").textContent = "查 Wikiquote 台词";
  $("quote-cands").replaceChildren();
  clearError($("poster-error"));
  setPosterMode("collage");
  $("poster-dialog").showModal();
  savedQuotes().then((s) => { if (PQ.entryId === entry.id && !PQ.looked) renderCands(s); })
    .catch((err) => showError($("poster-error"), err));
  $("poster-quote").focus();
}
async function onPosterSubmit(ev) {
  ev.preventDefault();
  const quotes = quoteLines();
  if (!quotes.length || state.posterEntryId == null) return;
  if (quotes.length > quoteLimit()) {
    showError($("poster-error"), { code: "poster_too_many_quotes", message: `${PQ.mode === "collage" ? "拼贴最多 4 句" : "AI 生图只能用 1 句"}，现在选了 ${quotes.length} 句` });
    return;
  }
  const submit = $("poster-submit");
  const idle = submit.textContent;
  submit.disabled = true;
  submit.textContent = PQ.mode === "collage" ? "拼贴中…" : "生成中，约需 2-3 分钟，请别关闭…";
  clearError($("poster-error"));
  try {
    const poster = PQ.mode === "collage"
      ? await api("POST", `/api/entries/${state.posterEntryId}/collage`, { quotes })
      : await api("POST", `/api/entries/${state.posterEntryId}/posters`, { quote: quotes[0] });
    $("poster-dialog").close();
    renderHero(true);
    if (poster.fallback_code) {
      showError($("chat-error"), { code: poster.fallback_code, message: `生图失败，已使用渐变背景：${poster.fallback_message}` });
    }
    openImage(poster);
  } catch (err) {
    showError($("poster-error"), err);
  } finally {
    submit.disabled = false;
    submit.textContent = idle;
  }
}
function openImage(p) {
  $("image-full").src = p.url;
  $("image-full").alt = `台词海报：${p.quote}`;
  $("image-download").href = p.url;
  $("image-download").setAttribute("download", `poster-${p.id}.png`);
  $("image-dialog").showModal();
}

/* ---------- list wall ---------- */
function renderWall() {
  $("wall").replaceChildren(...state.entries.filter((e) => e.status !== "wishlist").map((e, i) => {
    const b = el("button", {
      type: "button", style: `--i:${i}`,
      onclick: () => {
        setView("cal");
        if (validDate(e.watched_on)) {
          goTo(e.watched_on);
          state.pick = Math.max(0, (state.byDate.get(e.watched_on) || []).findIndex((x) => x.id === e.id));
          renderHero(false);
        } else {
          focusEntry(e.id);
        }
      },
    }, coverBox(e), el("div", { class: "t" }, e.title),
    el("div", { class: "s" }, `${validDate(e.watched_on) ? e.watched_on.slice(5) : "未标日期"} · ${e.rating ? "★".repeat(e.rating) : "未评分"}`));
    return b;
  }));
}


/* ---------- on this day ---------- */
async function renderOtd() {
  const box = $("otd"), tok = ++state.otdTok;
  try {
    const d = await api("GET", `/api/on-this-day?on=${todayKey()}`);
    if (tok !== state.otdTok) return;
    box.replaceChildren();
    if (!d.entries.length) { box.hidden = true; return; }
    d.entries.slice(0, 3).forEach((e) => {
      const label = e.years_ago === 1 ? "一年前的今天" : `${e.years_ago} 年前的今天`;
      box.append(el("button", { type: "button", onclick: () => { setView("cal"); goTo(e.watched_on); state.pick = Math.max(0, (state.byDate.get(e.watched_on) || []).findIndex((x) => x.id === e.id)); renderHero(false); } },
        el("span", { class: "otd-label" }, label), `你看了《${e.title}》`,
        e.note ? el("span", { class: "otd-note" }, `「${e.note.length > 28 ? e.note.slice(0, 28) + "…" : e.note}」`) : null));
    });
    box.hidden = false;
  } catch (err) {
    box.hidden = true; // a failed nicety must not block the page, but leave a trace for debugging
    console.warn("on-this-day failed", err.code, err.message);
  }
}

/* ---------- wishlist ---------- */
function renderWish() {
  const box = $("wish");
  box.replaceChildren();
  if (!state.wish.length) {
    box.append(el("p", { class: "wall-empty" }, "想看清单是空的。搜索一部电影，选「想看」放进来；也可以在聊天里说“我想看……”。"));
    return;
  }
  box.append(...state.wish.map((e, i) => el("button", {
    type: "button", style: `--i:${i}`, onclick: () => { setView("cal"); focusEntry(e.id); },
  }, coverBox(e), el("div", { class: "t" }, e.title),
  el("div", { class: "s" }, [e.year, (e.genres || []).slice(0, 2).join(" / ")].filter(Boolean).join(" · ") || "想看"))));
}
async function markWatched(e) {
  const watched = state.sel && state.sel <= todayKey() ? state.sel : todayKey();
  try {
    await api("PATCH", `/api/entries/${e.id}`, { status: "watched", watched_on: watched });
    await loadEntries();
    goTo(watched);
    state.editing = e.id; // after goTo, because selectDay clears it; lets the user add a rating and a note right away
    state.pick = Math.max(0, (state.byDate.get(watched) || []).findIndex((x) => x.id === e.id));
    renderHero(false);
    renderWall(); renderWish(); renderOtd();
  } catch (err) {
    showError($("chat-error"), err);
  }
}

/* ---------- clips: quotes and screenshots ---------- */
function quoteCard(c, entryId) {
  return el("figure", { class: "quote" },
    el("blockquote", {}, c.text),
    c.note ? el("figcaption", {}, c.note) : null,
    el("button", { type: "button", class: "x", "aria-label": "删除这句台词", onclick: () => deleteClip(c, entryId) }, "×"));
}
function shotThumb(c, entryId) {
  const wrap = el("div", { class: "shot" },
    el("img", { src: c.url, alt: c.note || "截图", loading: "lazy", onclick: () => openImage({ url: c.url, quote: c.note || "截图", id: `shot-${c.id}` }) }),
    el("button", { type: "button", class: "x", "aria-label": "删除这张截图", onclick: () => deleteClip(c, entryId) }, "×"));
  return wrap;
}
async function deleteClip(c, entryId) {
  if (!confirm(c.kind === "quote" ? "删除这句台词？" : "删除这张截图？")) return;
  try {
    await api("DELETE", `/api/clips/${c.id}`);
    if (state.view === "clips") renderClipWall(); else renderHero(true);
  } catch (err) {
    showError($("chat-error"), err);
  }
}
function openClipDialog(entry) {
  state.clipEntryId = entry.id;
  $("clip-movie").textContent = `《${entry.title}》`;
  $("clip-text").value = ""; $("clip-note").value = "";
  clearError($("clip-error"));
  $("clip-dialog").showModal();
  $("clip-text").focus();
}
async function onClipSubmit(ev) {
  ev.preventDefault();
  const text = $("clip-text").value.trim();
  if (!text || state.clipEntryId == null) return;
  const submit = $("clip-submit");
  submit.disabled = true;
  clearError($("clip-error"));
  try {
    await api("POST", `/api/entries/${state.clipEntryId}/clips`, { kind: "quote", text, note: $("clip-note").value.trim() });
    $("clip-dialog").close();
    renderHero(true);
  } catch (err) {
    showError($("clip-error"), err);
  } finally {
    submit.disabled = false;
  }
}
function pickShot(entry) {
  state.clipEntryId = entry.id;
  $("shot-input").value = "";
  $("shot-input").click();
}
async function onShotChosen() {
  const file = $("shot-input").files[0];
  if (!file || state.clipEntryId == null) return;
  clearError($("chat-error"));
  try {
    const resp = await fetch(`/api/entries/${state.clipEntryId}/clips/shot`, {
      method: "POST", headers: { "Content-Type": file.type || "application/octet-stream" }, body: file,
    }).catch((err) => { throw new ApiError("network_unreachable", `${err.name}: ${err.message}`); });
    if (!resp.ok) {
      let data = {};
      try { data = await resp.json(); } catch { /* non-JSON error body */ }
      throw new ApiError(data.error_code || "unknown_error", data.error || `HTTP ${resp.status}`);
    }
    renderHero(true);
  } catch (err) {
    showError($("chat-error"), err);
  }
}
async function renderClipWall() {
  const box = $("clipwall");
  try {
    const [{ clips }, { poems }] = await Promise.all([api("GET", "/api/clips"), api("GET", "/api/poems")]);
    state.clips = clips;
    box.replaceChildren();
    poems.forEach((p, i) => { const c = poemCard(p, true); c.style.setProperty("--i", i); box.append(c); });
    if (!clips.length && !poems.length) {
      box.append(el("p", { class: "wall-empty" }, "还没有收藏。在某部电影的详情里点「+ 收藏台词」「+ 上传截图」，或把「一键成诗」写的诗保存下来。"));
      return;
    }
    const open = (c) => { setView("cal"); focusEntryOrDay(c.entry_id); };
    clips.forEach((c, i) => {
      const card = c.kind === "quote"
        ? el("figure", { class: "quote big", style: `--i:${i}` }, el("blockquote", {}, c.text),
          el("figcaption", {}, `《${c.title}》${c.note ? " · " + c.note : ""}`))
        : el("div", { class: "shot big", style: `--i:${i}` },
          el("img", { src: c.url, alt: c.note || `${c.title} 截图`, loading: "lazy", onclick: () => openImage({ url: c.url, quote: c.note || c.title, id: `shot-${c.id}` }) }),
          el("figcaption", {}, `《${c.title}》${c.note ? " · " + c.note : ""}`));
      card.append(el("button", { type: "button", class: "x", "aria-label": "删除", onclick: () => deleteClip(c) }, "×"));
      card.append(el("button", { type: "button", class: "go", onclick: () => open(c) }, "去这部 →"));
      box.append(card);
    });
  } catch (err) {
    showError($("diary-error"), err);
  }
}
function focusEntryOrDay(id) {
  const e = entryById(id);
  if (!e) return;
  if (e.status !== "wishlist" && validDate(e.watched_on)) {
    goTo(e.watched_on);
    state.pick = Math.max(0, (state.byDate.get(e.watched_on) || []).findIndex((x) => x.id === e.id));
    renderHero(false);
  } else focusEntry(id);
}

/* ---------- one-click poem ---------- */
async function openPoemDialog(entry) {
  const p = state.poem;
  p.entryId = entry.id; p.text = ""; p.last = null;
  $("poem-save").disabled = true; $("poem-save").textContent = "保存";
  $("poem-movie").textContent = `《${entry.title}》`;
  $("poem-out").replaceChildren(el("p", { class: "muted" }, "选一位诗人，点名字就开始写。"));
  clearError($("poem-error"));
  $("poem-dialog").showModal();
  if (!p.styles.length) {
    try {
      p.styles = (await api("GET", "/api/poem-styles")).styles;
    } catch (err) {
      showError($("poem-error"), err);
      return;
    }
  }
  const groups = [["cn", "中国"], ["foreign", "外国"]];
  $("poem-poets").replaceChildren(...groups.map(([g, label]) => el("div", { class: "poet-group", role: "group", "aria-label": `${label}诗人` },
    el("span", { class: "poet-label" }, label),
    ...p.styles.filter((s) => s.group === g).map((s) => el("button", {
      type: "button", "data-k": s.key, "aria-pressed": String(s.key === p.style), title: s.tagline,
      onclick: () => writePoem(s.key),
    }, s.name)))));
}
async function writePoem(key) {
  const p = state.poem;
  if (p.busy || p.entryId == null) return;
  p.busy = true; p.style = key;
  document.querySelectorAll("#poem-poets button").forEach((b) => { b.setAttribute("aria-pressed", String(b.dataset.k === key)); b.disabled = true; });
  $("poem-again").disabled = true; $("poem-save").disabled = true;
  clearError($("poem-error"));
  const style = p.styles.find((s) => s.key === key);
  $("poem-out").replaceChildren(el("p", { class: "muted pending" }, `${style ? style.name : ""}正在落笔…`));
  const entryId = p.entryId;
  try {
    const res = await api("POST", `/api/entries/${entryId}/poem`, { style: key });
    if (entryId !== p.entryId) return;
    p.text = (res.title ? res.title + "\n\n" : "") + res.body;
    p.last = { style: key, title: res.title, body: res.body, saved: false };
    $("poem-save").disabled = false; $("poem-save").textContent = "保存";
    const out = $("poem-out");
    out.replaceChildren(
      res.title ? el("h4", {}, res.title) : null,
      el("div", { class: "lines" }),
      el("p", { class: "by" }, `仿 ${res.style.name} · ${res.style.tagline}（AI 创作，仅借文风）`));
    type(out.querySelector(".lines"), res.body, "poem", 0);
  } catch (err) {
    $("poem-out").replaceChildren();
    showError($("poem-error"), err);
  } finally {
    p.busy = false;
    document.querySelectorAll("#poem-poets button").forEach((b) => { b.disabled = false; });
    $("poem-again").disabled = false;
  }
}
async function savePoem() {
  const p = state.poem;
  if (!p.last || p.last.saved || p.entryId == null) return;
  const btn = $("poem-save");
  btn.disabled = true;
  clearError($("poem-error"));
  try {
    await api("POST", `/api/entries/${p.entryId}/poems`, { style: p.last.style, title: p.last.title, body: p.last.body });
    p.last.saved = true;
    btn.textContent = "已保存";
    renderHero(true);
  } catch (err) {
    if (err.code === "poem_already_saved") { p.last.saved = true; btn.textContent = "已保存"; return; }
    btn.disabled = false;
    showError($("poem-error"), err);
  }
}
async function deletePoem(poem) {
  if (!confirm("删除这首诗？")) return;
  try {
    await api("DELETE", `/api/poems/${poem.id}`);
    if (state.view === "clips") renderClipWall(); else renderHero(true);
  } catch (err) {
    showError($("chat-error"), err);
  }
}
function poemCard(poem, withMovie = false) {
  return el("figure", { class: "poemcard" + (withMovie ? " big" : "") },
    poem.title ? el("h5", {}, poem.title) : null,
    el("div", { class: "lines" }, poem.body),
    el("figcaption", {}, `${withMovie ? `《${poem.movie}》 · ` : ""}仿 ${poem.style_name} · AI 创作`),
    el("button", { type: "button", class: "x", "aria-label": "删除这首诗", onclick: () => deletePoem(poem) }, "×"));
}
async function copyPoem() {
  if (!state.poem.text) return;
  try {
    await navigator.clipboard.writeText(state.poem.text);
    $("poem-copy").textContent = "已复制";
    setTimeout(() => { $("poem-copy").textContent = "复制"; }, 1600);
  } catch (err) {
    showError($("poem-error"), { code: "clipboard_failed", message: `${err.name}: ${err.message}` });
  }
}

/* ---------- status & view ---------- */
async function loadStatus() {
  const box = $("status");
  try {
    const s = await api("GET", "/api/status");
    const dot = (name, ok, detail) => el("span", { class: ok ? "" : "off" }, `${name}${detail || ""}`);
    box.replaceChildren(dot("LLM", s.llm), dot("生图", s.image), dot("TMDB", s.tmdb), dot("搜索", true, ` ${s.search}`));
  } catch (err) {
    box.replaceChildren(el("span", { class: "off" }, err.code));
  }
}
function setView(v) {
  state.view = v;
  document.body.dataset.view = v;
  if (v === "clips") renderClipWall();
  document.querySelectorAll(".tabs button").forEach((x) => x.setAttribute("aria-selected", String(x.dataset.view === v)));
}

/* ---------- boot ---------- */
function boot() {
  try {
    const saved = localStorage.getItem(THEME_KEY);
    if (saved === "auto" || THEMES[saved]) state.mode = saved;
  } catch { /* storage unavailable */ }

  document.querySelectorAll(".tabs button").forEach((b) => b.addEventListener("click", () => setView(b.dataset.view)));
  $("prev").addEventListener("click", () => shiftMonth(-1));
  $("next").addEventListener("click", () => shiftMonth(1));
  $("today").addEventListener("click", () => goTo(todayKey()));
  $("search-form").addEventListener("submit", onSearch);
  $("search-input").addEventListener("keydown", (ev) => { if (ev.key === "Escape") closeResults(); });
  document.addEventListener("click", (ev) => { if (!ev.target.closest(".search")) closeResults(); });
  $("chat-form").addEventListener("submit", onSend);
  $("chat-input").addEventListener("keydown", (ev) => {
    if (ev.key === "Enter" && !ev.shiftKey && !ev.isComposing) { ev.preventDefault(); $("chat-form").requestSubmit(); }
  });
  $("clear-btn").addEventListener("click", onClear);
  $("global-btn").addEventListener("click", () => setScope(null));
  $("poster-form").addEventListener("submit", onPosterSubmit);
  $("poster-cancel").addEventListener("click", () => $("poster-dialog").close());
  $("poster-quote").addEventListener("input", syncQuotePicks);
  $("quote-lookup").addEventListener("click", lookupQuotes);
  $("quote-save").addEventListener("click", saveSelectedQuotes);
  document.querySelectorAll(".modes button").forEach((b) => b.addEventListener("click", () => setPosterMode(b.dataset.mode)));
  $("image-close").addEventListener("click", () => $("image-dialog").close());
  $("clip-form").addEventListener("submit", onClipSubmit);
  $("clip-cancel").addEventListener("click", () => $("clip-dialog").close());
  $("shot-input").addEventListener("change", onShotChosen);
  $("poem-close").addEventListener("click", () => $("poem-dialog").close());
  $("poem-copy").addEventListener("click", copyPoem);
  $("poem-save").addEventListener("click", savePoem);
  $("poem-again").addEventListener("click", () => { if (state.poem.style) writePoem(state.poem.style); });
  addEventListener("resize", resize);

  initSwatches();
  resize();
  if (RM) $("light").style.transform = `translate(${mx}px,${my}px)`;
  else requestAnimationFrame(loop);
  loadStatus();

  state.sel = todayKey();
  renderMonth(true);
  renderHero(false);
  setScope(null);
  loadEntries()
    .then(() => { renderMonth(true); renderHero(false); renderWall(); renderWish(); renderOtd(); })
    .catch((err) => showError($("diary-error"), err));
}

boot();
