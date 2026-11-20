"use strict";
/* Per-movie page: film info, chat with input completion, and the "impression sea".
   Loaded after app.js and shares its globals ($, el, api, state, entryById, ...). */
(() => {
  const clamp = (v, a, b) => Math.min(Math.max(v, a), b);
  const STATE = { draft: "待确认", ok: "已采纳", edit: "已校准", rej: "已驳回" };
  const KIND = {
    one:  { label: "一句话印象", h: 38,  r: 94, home: [.5, .24] },
    feel: { label: "感受",       h: 195, r: 70, home: [.24, .5] },
    rate: { label: "评价",       h: 345, r: 62, home: [.77, .47] },
    idea: { label: "灵感",       h: 265, r: 62, home: [.5, .74] },
    word: { label: "印象词",     h: 152, r: 38, home: null },
    mine: { label: "我的补充",   h: 95,  r: 50, home: [.72, .8] },
  };
  const WORD_SLOTS = [[.13, .2], [.87, .17], [.8, .7], [.2, .82], [.36, .9], [.9, .45], [.1, .52]];
  const kindOf = (d) => KIND[d.kind] || KIND.mine;
  const hashOf = (id) => Math.imul((Number(id) || 1) ^ 0x9e3779b9, 2654435761) >>> 0;
  const shortOf = (t) => { t = t.replace(/[。.!！]$/, ""); return t.length > 11 ? `${t.slice(0, 10)}…` : t; };

  const P = {
    ready: false, open: false, fromCal: false, id: null, entry: null, tok: 0, prevView: "cal", dirty: false,
    info: null, rev: null, tab: "rev", bubbles: [], cur: null, editing: false, busy: false,
    W: 0, H: 0, scale: 1, raf: 0, refreshing: false, again: false, inflight: 0, seq: 0, failed: false,
    hint: { tok: 0, timer: 0, composing: false, cands: [], words: [], idx: 0 },
  };
  const mp = $("mp"), film = $("mp-film"), msgs = $("mp-msgs"), inp = $("mp-inp"), ghost = $("mp-ghost"), sug = $("mp-sug");
  const wrap = $("mp-seawrap"), layer = $("mp-bubbles"), pop = $("mp-pop"), seaErr = $("mp-sea-error"), chatErr = $("mp-chat-error");
  const sendBtn = $("mp-send");
  const PERSONA = {};
  const UNKNOWN_PERSONA = { name: "角色", role: "", hue: 200 };
  let personaReady = null;
  const fill = (node, ...kids) => node.replaceChildren(...kids.filter((x) => x != null && x !== false));
  const alive = (tok) => P.open && tok === P.tok;

  /* ---------- small ui helpers ---------- */
  function setSaved(text, bad) {
    $("mp-saved").replaceChildren(el("i", { style: bad ? "background:#e5786d;box-shadow:0 0 8px #e5786d" : "" }), text);
  }
  async function write(method, path, body) {
    P.inflight++;
    setSaved("保存中…");
    try {
      const res = await api(method, path, body);
      if (--P.inflight === 0) setSaved("已自动保存");
      return res;
    } catch (err) {
      if (--P.inflight === 0) setSaved("保存失败", true);
      throw err;
    }
  }
  function setStatus(text, mode) {
    const box = $("mp-status");
    box.classList.toggle("done", mode === "done");
    box.classList.toggle("err", mode === "err");
    $("mp-stxt").textContent = text;
  }

  /* ---------- open / leave ---------- */
  function route() {
    const m = /^#\/movie\/(\d+)$/.exec(location.hash);
    if (!m) { if (P.open) leave(); return; }
    open(Number(m[1]));
  }
  function go(id) {
    history.replaceState(null, "", `#/movie/${id}`);
    open(id);
  }
  function open(id) {
    if (P.open && P.id === id) return;
    const e = entryById(id);
    if (!e) {
      history.replaceState(null, "", location.pathname + location.search);
      if (P.open) leave();
      showError($("diary-error"), { code: "entry_not_found", message: `没有 id 为 ${id} 的电影` });
      return;
    }
    if (P.open) teardown();
    else P.prevView = state.view === "movie" ? "cal" : state.view;
    Object.assign(P, { open: true, id, entry: e, info: null, rev: null, tab: "rev", dirty: false, busy: false, failed: false, refreshing: false, again: false });
    const tok = ++P.tok;
    mp.style.setProperty("--h", hue(e.title));
    document.title = `《${e.title}》· 电影日记`;
    setView("movie");
    clearError($("mp-error")); clearError(chatErr); clearError(seaErr);
    scrollTo(0, 0);
    setSaved("已自动保存");
    buildFilm();
    renderStrip();
    renderCal();
    $("mp-ctitle").textContent = `聊聊《${e.title}》`;
    msgs.replaceChildren();
    sendBtn.disabled = false;
    inp.value = ""; autosize(); clearHints();
    setStatus("正在读取这部电影的印象…", "busy");
    $("mp-again").disabled = true;
    measure();
    cancelAnimationFrame(P.raf);
    P.raf = requestAnimationFrame(tick);
    loadFilm(tok);
    loadReviews(tok);
    loadChat(tok);
    loadImpressions(tok, true);
  }
  function teardown() {
    flushSaves();
    cancelAnimationFrame(P.raf);
    closePop(true);
    clearTimeout(P.hint.timer);
    P.hint.tok++;
    P.tok++;
    P.bubbles.forEach((b) => b.el.remove());
    P.bubbles = [];
    P.open = false;
  }
  function leave() {
    const id = P.id;
    teardown();
    document.title = "电影日记";
    setView(P.prevView);
    if (P.dirty) refreshAll();
    if (P.prevView === "cal") focusEntryOrDay(id);
  }

  /* ---------- film column ---------- */
  function buildFilm() {
    const tabs = el("div", { class: "mp-tabs", role: "tablist" },
      ...[["rev", "网络评价"], ["sim", "相似电影"], ["intro", "简介"]].map(([k, label]) => el("button", {
        type: "button", role: "tab", "data-tab": k, "aria-selected": String(k === P.tab),
        onclick: () => {
          P.tab = k;
          tabs.querySelectorAll("button").forEach((x) => x.setAttribute("aria-selected", String(x.dataset.tab === k)));
          film.querySelectorAll(".mp-pane").forEach((p) => { p.hidden = p.dataset.tab !== k; });
        },
      }, label)));
    const e = P.entry;
    const when = e.status !== "wishlist" && validDate(e.watched_on) ? dateLabel(e.watched_on) : (e.status === "wishlist" ? "想看清单" : "");
    film.replaceChildren(
      el("p", { class: "mp-h" }, el("span", {}, "THE FILM"), el("em", {}, when)),
      el("div", { id: "mp-head" }),
      tabs,
      ...["rev", "sim", "intro"].map((k) => el("div", { class: "mp-pane", "data-tab": k, hidden: k !== P.tab })));
    film.scrollTop = 0;
    fillHead(); fillReviews(); fillSims(); fillIntro();
  }
  const pane = (k) => film.querySelector(`.mp-pane[data-tab="${k}"]`);
  function fillHead() {
    const e = P.entry, i = P.info || {};
    const orig = i.original_title || e.original_title;
    const line1 = [];
    if (e.year) line1.push(el("b", {}, String(e.year)));
    if (e.director) line1.push(`${e.director} 导演`);
    if (i.runtime) line1.push(`${i.runtime} 分钟`);
    if ((i.countries || []).length) line1.push(i.countries.join(" / "));
    const meta = el("p", { class: "mp-meta" });
    const rows = [
      line1.flatMap((x, n) => (n ? [" · ", x] : [x])),
      (e.genres || []).length ? [e.genres.join(" / ")] : [],
      (i.cast || []).length ? [i.cast.slice(0, 5).join(" · ")] : [],
    ].filter((r) => r.length);
    rows.forEach((r, n) => { if (n) meta.append(el("br")); meta.append(...r); });
    const ratings = (i.ratings || []).filter((r) => r && r.value);
    const kws = ((i.keywords || []).length ? i.keywords : (e.tags || [])).slice(0, 12);
    fill($("mp-head"), 
      coverBox(e, "mp-cover"),
      el("h1", { class: "mp-ftitle" }, e.title),
      orig && orig !== e.title ? el("p", { class: "mp-en" }, orig) : null,
      i.tagline ? el("p", { class: "mp-tagline" }, i.tagline) : null,
      rows.length ? meta : null,
      ratings.length ? el("div", { class: "mp-score" }, ...ratings.map((r) => el("div", { title: r.votes ? `${r.votes} 人评分` : null }, el("b", {}, String(r.value)), r.source))) : null,
      kws.length ? el("div", { class: "mp-kw" }, ...kws.map((k) => el("span", {}, k))) : null);
  }
  function fillReviews() {
    const box = pane("rev"), r = P.rev;
    if (!box) return;
    if (!r || r.loading) return box.replaceChildren(el("p", { class: "mp-quiet" }, "正在查找网络评价…"));
    if (r.err) return box.replaceChildren(el("p", { class: "mp-quiet" }, `暂时没有取到网络评价（${r.err}）`));
    if (!r.reviews.length && !r.summary) return box.replaceChildren(el("p", { class: "mp-quiet" }, "没有找到网络评价。"));
    fill(box,
      r.summary ? el("p", { class: "mp-sum" }, r.summary) : null,
      ...r.reviews.map((v) => el("div", { class: "mp-web" },
        el("div", { class: "src" }, el("span", {}, v.title || "网络评价"),
          isUrl(v.url) ? el("a", { href: v.url, target: "_blank", rel: "noopener noreferrer" }, "原文 ↗") : null),
        v.snippet ? el("q", {}, v.snippet) : null)));
  }
  function fillSims() {
    const box = pane("sim"), list = (P.info && P.info.similar) || [];
    if (!box) return;
    if (!list.length) return box.replaceChildren(el("p", { class: "mp-quiet" }, P.info ? "没有相似电影的数据。" : "相似电影需要配置 TMDB。"));
    box.replaceChildren(el("div", { class: "mp-sims" }, ...list.slice(0, 9).map((s) => {
      const p = el("div", { class: "p" });
      if (isUrl(s.poster_url)) {
        const img = el("img", { src: s.poster_url, alt: "", loading: "lazy", referrerpolicy: "no-referrer" });
        img.addEventListener("error", () => img.remove());
        p.append(img);
      }
      const known = state.entries.find((x) => s.tmdb_id != null && x.tmdb_id === s.tmdb_id);
      return el("button", { type: "button", class: "mp-sim", style: `--sh:${hue(s.title || "")}`, onclick: () => openSimilar(s) },
        p, el("b", {}, s.title), el("span", {}, [s.year, known ? (known.status === "wishlist" ? "在想看清单里" : "已看过") : ""].filter(Boolean).join(" · ")));
    })));
  }
  function fillIntro() {
    const box = pane("intro");
    if (box) box.replaceChildren(P.entry.overview ? el("p", { style: "margin:0" }, P.entry.overview) : el("p", { class: "mp-quiet" }, "还没有简介。"));
  }
  async function openSimilar(s) {
    const known = state.entries.find((x) => s.tmdb_id != null && x.tmdb_id === s.tmdb_id);
    if (known) return go(known.id);
    if (!confirm(`把《${s.title}》加入想看清单？`)) return;
    clearError($("mp-error"));
    try {
      await write("POST", "/api/entries", { tmdb_id: s.tmdb_id, title: s.title, year: s.year, poster_url: s.poster_url || "", status: "wishlist" });
      await loadEntries();
      P.dirty = true;
      fillSims();
      setSaved(`已把《${s.title}》加入想看`);
    } catch (err) { showError($("mp-error"), err); }
  }
  async function loadFilm(tok) {
    try {
      const { info } = await api("GET", `/api/entries/${P.id}/film`);
      if (!alive(tok)) return;
      P.info = info;
    } catch { if (!alive(tok)) return; P.info = null; }
    fillHead(); fillSims();
  }
  async function loadReviews(tok) {
    P.rev = { loading: true }; fillReviews();
    try {
      const d = await api("GET", `/api/entries/${P.id}/reviews`);
      if (!alive(tok)) return;
      P.rev = { summary: d.summary || "", reviews: (d.reviews || []).filter((v) => v && (v.snippet || v.title)) };
    } catch (err) { if (!alive(tok)) return; P.rev = { err: err.code || "error" }; }
    fillReviews();
  }

  /* ---------- top strip + calendar preview ---------- */
  function renderStrip() {
    const e = P.entry, strip = $("mp-strip");
    strip.replaceChildren();
    if (e.status === "wishlist" || !validDate(e.watched_on)) {
      strip.append(el("span", { class: "mp-mon" }, e.status === "wishlist" ? "想看清单" : "未标日期"));
      return;
    }
    const prefix = e.watched_on.slice(0, 7);
    strip.append(el("span", { class: "mp-mon" }, `${Number(prefix.slice(5))} 月`));
    [...state.byDate.keys()].filter((k) => k.startsWith(prefix)).sort().forEach((k) => {
      for (const x of state.byDate.get(k)) {
        const day = Number(k.slice(8));
        const b = el("button", {
          type: "button", class: `mp-mini${x.id === P.id ? " cur" : ""}`, style: `--h:${hue(x.title)}`, title: x.title,
          "aria-label": `《${x.title}》${day}日`, "aria-current": x.id === P.id ? "true" : null, onclick: () => go(x.id),
        }, String(day));
        if (x.note) b.append(el("i", { class: "mp-dot" }));
        strip.append(b);
      }
    });
  }
  function renderCal() {
    const e = P.entry, box = $("mp-cal");
    const one = P.bubbles.find((b) => b.d.kind === "one" && (b.d.status === "ok" || b.d.status === "edit"));
    const draft = P.bubbles.find((b) => b.d.kind === "one" && b.d.status === "draft");
    const stars = e.rating ? "★".repeat(e.rating) : "";
    const orb = el("div", { class: "mp-orb", style: `--h:${hue(e.title)}` });
    const img = coverImg(e);
    if (img) orb.append(img);
    orb.append(el("span", {}, validDate(e.watched_on) ? String(Number(e.watched_on.slice(8))) : "·"));
    let tip;
    if (e.note) {
      tip = el("div", { class: "mp-tip" }, e.note, el("small", {}, [e.title, stars, one ? (one.d.status === "edit" ? "已校准" : "已采纳") : "来自日记"].filter(Boolean).join(" · ")));
    } else if (draft) {
      tip = el("div", { class: "mp-tip faint" }, draft.d.text || draft.d.short, el("small", {}, "待确认，采纳后才会出现在日历里"));
    } else tip = el("div", { class: "mp-tip faint" }, "还没有写下感受");
    box.replaceChildren(el("span", { class: "k" }, "日历里"), orb, tip);
  }
  function syncEntry(entry) {
    if (!entry) return;
    const i = state.entries.findIndex((x) => x.id === entry.id);
    if (i >= 0) state.entries[i] = entry; else state.entries.push(entry);
    indexEntries();
    P.dirty = true;
    if (entry.id === P.id) { P.entry = entry; renderStrip(); renderCal(); }
  }

  /* ---------- chat ---------- */
  const scrollEnd = () => { msgs.scrollTop = msgs.scrollHeight; };
  const turnCount = () => msgs.querySelectorAll(".mp-m.me").length;
  function updateChatInfo() { $("mp-cinfo").textContent = `共 ${turnCount()} 轮 · 仅关于这部电影`; }
  const msgById = (id) => msgs.querySelector(`.mp-m[data-id="${Number(id)}"]`);
  function turnLabel(id) {
    const node = msgById(id);
    if (!node) return null;
    let n = 0;
    for (const x of msgs.querySelectorAll(".mp-m")) { if (x.classList.contains("me")) n++; if (x === node) break; }
    return n ? `第 ${n} 轮` : "开场";
  }
  function glowBubbles(id, on) {
    P.bubbles.forEach((b) => { if ((b.d.source_msg_ids || []).includes(Number(id))) b.el.classList.toggle("glow", on); });
  }
  function loadPersonas() {
    if (!personaReady) {
      personaReady = api("GET", "/api/personas").then(({ personas }) => {
        personas.forEach((p) => { PERSONA[p.key] = p; });
        buildSummon(personas);
      }).catch(() => { personaReady = null; });
    }
    return personaReady;
  }
  function buildSummon(personas) {
    const box = $("mp-summon");
    box.replaceChildren(el("span", { class: "lab" }, "请来聊聊"), ...personas.map((p) => {
      const b = el("button", { type: "button", title: p.tagline, "aria-label": `请${p.name}（${p.role}）来聊聊` }, p.name, el("small", {}, p.role));
      b.style.setProperty("--ph", p.hue);
      b.addEventListener("click", () => {
        const sep = inp.value && !/\s$/.test(inp.value) ? " " : "";
        inp.value = `${inp.value}${sep}@${p.name} `;
        autosize(); inp.focus();
        inp.setSelectionRange(inp.value.length, inp.value.length);
        scheduleHints();
      });
      return b;
    }));
    box.hidden = false;
  }
  const calledIn = (t) => Object.values(PERSONA).filter((p) => t.includes(`@${p.name}`) || t.includes(`＠${p.name}`));
  function tagPersona(node, p) {
    node.dataset.who = p.role ? `${p.name} · ${p.role}` : p.name;
    node.style.setProperty("--ph", p.hue);
  }
  async function quoteMsg(node, btn) {
    const tok = P.tok;
    btn.disabled = true;
    clearError(chatErr);
    try {
      await write("POST", `/api/entries/${P.id}/impressions/from-message`, { message_id: Number(node.dataset.id) });
      btn.textContent = "已放进印象之海";
      if (alive(tok)) loadImpressions(tok);
    } catch (err) {
      if (!alive(tok)) return;
      if (err.code === "message_already_quoted") btn.textContent = "已在印象之海";
      else { btn.disabled = false; showError(chatErr, err); }
    }
  }
  function addMsg(m, animate) {
    msgs.querySelector(".mp-empty")?.remove();
    const ai = m.role === "assistant";
    const node = el("div", { class: `mp-m ${ai ? "ai" : "me"}` });
    if (m.id != null) node.dataset.id = m.id;
    const personaKey = ai && m.meta && m.meta.persona;
    if (personaKey) tagPersona(node, PERSONA[personaKey] || UNKNOWN_PERSONA);
    const text = el("span");
    node.append(text);
    if (!ai) text.textContent = m.content;
    else if (animate && !/\]\(https?:\/\//.test(m.content)) type(text, m.content, `mp${++P.seq}`, 0, scrollEnd);
    else renderText(text, m.content);
    if (m.posters && m.posters.length) {
      node.append(el("div", { class: "imgs" }, ...m.posters.map(posterImg)));
      for (const p of m.posters) if (p.fallback_code) node.append(el("div", { class: "notice" }, `生图失败，已使用渐变背景（${p.fallback_code}）：${p.fallback_message}`));
    }
    const sources = ((m.meta && m.meta.sources) || []).filter((s) => isUrl(s.url));
    if (sources.length) node.append(el("div", { class: "sources" }, "来源", ...sources.map((s) => el("a", { href: s.url, target: "_blank", rel: "noopener noreferrer" }, s.title || s.url))));
    const choices = ai && m.meta && Array.isArray(m.meta.choices) ? m.meta.choices.filter((c) => typeof c === "string" && c.trim()).slice(0, 6) : [];
    if (choices.length) {
      node.append(el("div", { class: "mp-asks" }, ...choices.map((c) => el("button", {
        type: "button", onclick: () => { inp.value = `${c}，`; autosize(); inp.focus(); scheduleHints(); },
      }, c))));
    }
    if (personaKey) {
      const q = el("button", { type: "button", class: "quote", title: "把这句话放进印象之海，作为你的灵感" }, "引用为灵感");
      q.addEventListener("click", () => quoteMsg(node, q));
      node.append(q);
    }
    node.addEventListener("mouseenter", () => glowBubbles(node.dataset.id, true));
    node.addEventListener("mouseleave", () => glowBubbles(node.dataset.id, false));
    msgs.append(node);
    scrollEnd();
    updateChatInfo();
    return node;
  }
  function showEmptyChat() {
    if (msgs.children.length) return;
    msgs.append(el("div", { class: "mp-empty" },
      el("p", { style: "margin:0 0 12px" }, "看完之后，哪个画面或者哪一刻最先浮现在你脑子里？"),
      el("div", { class: "mp-asks" }, ...["我看完最大的感受是…", "帮我找找这部片的解读", "我有个模糊的灵感，帮我展开"].map((t) => el("button", {
        type: "button", onclick: () => { inp.value = t.endsWith("…") ? t.slice(0, -1) : t; autosize(); inp.focus(); scheduleHints(); },
      }, t)))));
  }
  async function loadChat(tok) {
    try {
      await loadPersonas();
      const { messages } = await api("GET", `/api/entries/${P.id}/chat`);
      if (!alive(tok)) return;
      msgs.replaceChildren();
      messages.forEach((m) => addMsg(m, false));
      showEmptyChat();
      updateChatInfo();
      scrollEnd();
      relabelBubbles();
    } catch (err) { if (alive(tok)) showError(chatErr, err); }
  }
  // New messages come back without the user message id, so align real ids from the end of the history.
  async function syncIds(tok) {
    try {
      const { messages } = await api("GET", `/api/entries/${P.id}/chat`);
      if (!alive(tok)) return;
      const nodes = [...msgs.querySelectorAll(".mp-m:not(.pending)")];
      for (let k = 1; k <= Math.min(nodes.length, messages.length); k++) nodes[nodes.length - k].dataset.id = messages[messages.length - k].id;
    } catch { /* ids only matter for highlighting */ }
  }
  async function onSend() {
    const text = inp.value.trim();
    if (!text || P.busy) return;
    const tok = P.tok;
    P.busy = true;
    sendBtn.disabled = true;
    clearError(chatErr); clearHints();
    const mine = addMsg({ role: "user", content: text }, false);
    inp.value = ""; autosize();
    const called = calledIn(text);
    const pending = el("div", { class: "mp-m ai pending" }, called.length ? `${called.map((p) => p.name).join("、")}正在想…` : "正在思考…");
    if (called.length) tagPersona(pending, called[0]);
    msgs.append(pending);
    scrollEnd();
    try {
      const res = await api("POST", `/api/entries/${P.id}/chat`, { message: text });
      if (!alive(tok)) return;
      pending.remove();
      (res.messages || [res.message]).forEach((m) => addMsg(m, true));
      if (res.diary_changed) { await loadEntries(); P.dirty = true; if (alive(tok)) { P.entry = entryById(P.id) || P.entry; renderStrip(); renderCal(); } }
      await syncIds(tok);
      refreshImpressions();
    } catch (err) {
      if (!alive(tok)) return;
      pending.remove(); mine.remove();
      updateChatInfo(); showEmptyChat();
      showError(chatErr, err);
      inp.value = text; autosize();
    } finally {
      if (alive(tok)) { P.busy = false; sendBtn.disabled = false; inp.focus(); }
    }
  }
  async function onClear() {
    if (!confirm("清空这部电影的对话记录？已经整理出来的印象不会被删除。")) return;
    clearError(chatErr);
    try {
      await write("DELETE", `/api/entries/${P.id}/chat`);
      msgs.replaceChildren();
      showEmptyChat(); updateChatInfo();
    } catch (err) { showError(chatErr, err); }
  }

  /* ---------- input completion ---------- */
  function autosize() {
    inp.style.height = "auto";
    inp.style.height = `${Math.min(inp.scrollHeight, 220)}px`;
    ghost.scrollTop = inp.scrollTop;
  }
  function clearHints() {
    const h = P.hint;
    clearTimeout(h.timer);
    h.tok++; h.cands = []; h.words = []; h.idx = 0;
    sug.hidden = true; sug.replaceChildren(); ghost.textContent = "";
  }
  function scheduleHints() {
    const h = P.hint;
    clearHints();
    if (h.composing || P.busy || inp.value.trim().length < 2) return;
    const draft = inp.value, tok = h.tok, id = P.id;
    h.timer = setTimeout(async () => {
      try {
        const d = await api("POST", `/api/entries/${id}/compose-hints`, { draft });
        if (tok !== h.tok || inp.value !== draft || h.composing || !P.open) return;
        h.cands = (d.continuations || []).filter((c) => c && c.text).slice(0, 3);
        h.words = (d.words || []).filter((w) => typeof w === "string" && w).slice(0, 8);
        h.idx = 0;
        renderHints();
      } catch { /* hints are optional, stay quiet */ }
    }, 600);
  }
  function renderHints() {
    const h = P.hint, tail = inp.value.slice(-8).replace(/\s+/g, " ");
    if (!h.cands.length && !h.words.length) return clearHints();
    sug.hidden = false;
    const keep = (e) => e.preventDefault();
    fill(sug,
      h.cands.length ? el("div", { class: "sh" }, el("span", {}, "这样说可以吗？"), el("span", {}, "↑↓ 选择 · Tab 采纳")) : null,
      ...h.cands.map((c, i) => el("button", { type: "button", class: `cand${i === h.idx ? " act" : ""}`, onmousedown: keep, onclick: () => { h.idx = i; acceptHint(); } },
        el("u", {}, `…${tail}`), c.text, c.label ? el("em", {}, c.label) : null)),
      h.words.length ? el("div", { class: "mp-words" }, el("span", { class: "lab" }, "也许你想说的词"),
        ...h.words.map((w) => el("button", { type: "button", onmousedown: keep, onclick: () => { inp.value += w; autosize(); inp.focus(); scheduleHints(); } }, w))) : null);
    ghost.textContent = "";
    if (h.cands.length) ghost.append(document.createTextNode(inp.value), el("i", {}, h.cands[h.idx].text));
  }
  function acceptHint() {
    const h = P.hint;
    if (sug.hidden || !h.cands.length) return false;
    inp.value += h.cands[h.idx].text;
    autosize(); inp.focus(); scheduleHints();
    return true;
  }
  inp.addEventListener("input", () => { autosize(); if (!P.hint.composing) scheduleHints(); else clearHints(); });
  inp.addEventListener("scroll", () => { ghost.scrollTop = inp.scrollTop; });
  inp.addEventListener("compositionstart", () => { P.hint.composing = true; clearHints(); });
  inp.addEventListener("compositionend", () => { P.hint.composing = false; scheduleHints(); });
  inp.addEventListener("keydown", (e) => {
    if (e.isComposing) return;
    if ((e.metaKey || e.ctrlKey) && e.key === "Enter") { e.preventDefault(); onSend(); return; }
    const h = P.hint;
    if (sug.hidden) return;
    if (e.key === "Tab" && h.cands.length) { e.preventDefault(); acceptHint(); }
    else if (e.key === "Escape") { e.preventDefault(); clearHints(); }
    else if ((e.key === "ArrowDown" || e.key === "ArrowUp") && h.cands.length) {
      e.preventDefault();
      h.idx = (h.idx + (e.key === "ArrowDown" ? 1 : h.cands.length - 1)) % h.cands.length;
      renderHints();
    }
  });
  sendBtn.addEventListener("click", onSend);
  document.querySelectorAll("#mp .mp-helpers button").forEach((b) => b.addEventListener("click", () => {
    inp.value = b.dataset.help; autosize(); inp.focus();
    inp.setSelectionRange(inp.value.length, inp.value.length);
    scheduleHints();
  }));
  $("mp-clear").addEventListener("click", onClear);

  /* ---------- impression sea ---------- */
  function measure() {
    const r = wrap.getBoundingClientRect();
    P.W = r.width; P.H = r.height;
    P.scale = clamp(Math.min(P.W / 520, P.H / 600), .72, 1.1);
    P.bubbles.forEach(setRadius);
  }
  new ResizeObserver(() => { if (P.open) measure(); }).observe(wrap);
  function radiusOf(d) {
    const len = (d.short || "").length;
    const grow = d.kind === "word" ? Math.min(8, Math.max(0, len - 3) * 2) : Math.min(10, Math.max(0, len - 12) * .8);
    return Math.round((kindOf(d).r + grow) * P.scale);
  }
  function setRadius(b) { b.r = radiusOf(b.d); b.el.style.setProperty("--r", `${b.r}px`); }
  function homeOf(d) {
    if (d.pos_x != null && d.pos_y != null) return [clamp(d.pos_x, 0, 1), clamp(d.pos_y, 0, 1)];
    const h = hashOf(d.id);
    let [x, y] = kindOf(d).home || WORD_SLOTS[h % WORD_SLOTS.length];
    x += (((h >>> 8) % 100) / 100 - .5) * .12;
    y += (((h >>> 16) % 100) / 100 - .5) * .1;
    return [clamp(x, .08, .92), clamp(y, .12, .88)];
  }
  const byId = (id) => P.bubbles.find((b) => !b.tmp && b.d.id === id);

  function addBubble(d, delay, opts = {}) {
    const [hfx, hfy] = homeOf(d);
    const b = {
      d, hfx, hfy, r: 0, gone: false, active: false, drag: false, freeze: null, saveT: 0, tmp: !!opts.tmp,
      p1: Math.random() * 6.28, p2: Math.random() * 6.28, w1: .16 + Math.random() * .16, w2: .14 + Math.random() * .16, amp: 12 + Math.random() * 10,
    };
    const node = el("div", { class: `mp-bub mp-k-${d.kind}`, tabindex: "0", role: "button" },
      el("div", { class: "in" }, el("small"), el("div", { class: "bst" }), el("span", { class: "lbl" })), el("i", { class: "dt" }));
    node.style.setProperty("--bh", kindOf(d).h);
    node.style.setProperty("--d", `${(-Math.random() * 8).toFixed(1)}s`);
    node.addEventListener("mouseenter", () => hlMsgs(b, true));
    node.addEventListener("mouseleave", () => hlMsgs(b, false));
    node.addEventListener("focus", () => hlMsgs(b, true));
    node.addEventListener("blur", () => hlMsgs(b, false));
    node.addEventListener("keydown", (ev) => {
      if (ev.key !== "Enter" && ev.key !== " ") return;
      ev.preventDefault();
      if (b.d.status === "rej") restore(b); else openPop(b);
    });
    b.el = node;
    layer.append(node);
    P.bubbles.push(b);
    setRadius(b);
    paint(b);
    if (opts.from) { b.x = opts.from[0]; b.y = opts.from[1]; }
    else if (d.status === "rej") { b.x = clamp(hfx, .08, .92) * P.W; b.y = P.H - 30; }
    else { b.x = hfx * P.W; b.y = P.H + b.r + Math.random() * 50; }
    setTimeout(() => { b.active = true; node.classList.add("show"); }, delay);
    return b;
  }
  function paint(b) {
    const d = b.d, k = kindOf(d), node = b.el;
    node.classList.remove("draft", "ok", "edit", "rej");
    node.classList.add(d.status);
    node.querySelector("small").textContent = k.label;
    node.querySelector(".lbl").textContent = d.short;
    node.querySelector(".bst").textContent = d.kind === "rate" && d.stars ? "★".repeat(d.stars) + "☆".repeat(5 - d.stars) : "";
    node.setAttribute("aria-label", `${k.label}：${d.short}，${STATE[d.status] || ""}，${d.status === "rej" ? "按回车放回海面" : "按回车查看详情"}`);
  }
  function relabelBubbles() { if (P.cur) fillPop(P.cur); }
  function removeBubble(b) {
    b.gone = true; clearTimeout(b.saveT); b.el.remove();
    P.bubbles = P.bubbles.filter((x) => x !== b);
    if (P.cur === b) closePop(true);
  }
  function hlMsgs(b, on) {
    (b.d.source_msg_ids || []).forEach((id) => msgById(id)?.classList.toggle("hl", on));
  }
  function updateEmpty() {
    let box = wrap.querySelector(".mp-seaempty");
    const none = !P.bubbles.some((b) => !b.gone);
    if (none && !box) wrap.append(el("div", { class: "mp-seaempty" }, "聊上几句，印象就会从这里浮起来。"));
    else if (!none && box) box.remove();
  }
  function reconcile(list, initial) {
    const seen = new Set();
    list.forEach((d, i) => {
      seen.add(d.id);
      const b = byId(d.id);
      if (b) updateBubble(b, d);
      else addBubble(d, initial ? 150 + i * 160 : i * 220);
    });
    P.bubbles.filter((b) => !b.tmp && !seen.has(b.d.id)).forEach(removeBubble);
    updateEmpty();
    renderCal();
  }
  function updateBubble(b, d) {
    b.d = d;
    if (!b.drag && !b.saveT && d.pos_x != null && d.pos_y != null) { b.hfx = clamp(d.pos_x, 0, 1); b.hfy = clamp(d.pos_y, 0, 1); }
    setRadius(b); paint(b);
    if (P.cur === b) fillPop(b);
  }
  async function loadImpressions(tok = P.tok, initial = false) {
    try {
      const { impressions } = await api("GET", `/api/entries/${P.id}/impressions`);
      if (!alive(tok)) return;
      reconcile(impressions, initial);
      if (initial) {
        setStatus(impressions.length ? `已整理 ${impressions.length} 个印象，点气泡查看和校准` : "还没有整理过这部电影的印象，聊几句或点「重新整理」", "done");
        $("mp-again").disabled = false;
      }
    } catch (err) {
      if (!alive(tok)) return;
      setStatus(`读取印象失败：${err.message}`, "err");
      $("mp-again").disabled = false;
    }
  }
  async function refreshImpressions() {
    if (P.refreshing) { P.again = true; return; }
    const tok = P.tok;
    P.refreshing = true;
    $("mp-again").disabled = true;
    setStatus("正在根据最新一轮对话整理…", "busy");
    try {
      const res = await api("POST", `/api/entries/${P.id}/impressions/refresh`);
      if (!alive(tok)) return;
      reconcile(res.impressions || [], false);
      setStatus(res.message || (res.added ? `已整理完成，新增 ${res.added} 个气泡等你确认` : "已整理完成，这一轮没有新的印象"), "done");
    } catch (err) {
      if (alive(tok)) setStatus(`整理失败（${err.code || "error"}）：${err.message}`, "err");
    } finally {
      if (alive(tok)) {
        P.refreshing = false;
        $("mp-again").disabled = false;
        if (P.again) { P.again = false; refreshImpressions(); }
      } else { P.refreshing = false; P.again = false; }
    }
  }
  $("mp-again").addEventListener("click", refreshImpressions);

  /* physics: every bubble drifts around its home and gently pushes the others away */
  function tick(now) {
    if (!P.open) return;
    const t = now / 1000, { W, H } = P;
    const live = P.bubbles.filter((b) => b.active && !b.gone);
    for (const b of live) {
      if (b.drag) continue;
      let tx, ty;
      if (b.d.status === "rej") { tx = clamp(b.hfx, .08, .92) * W; ty = H - 30 + (RM ? 0 : Math.sin(t * .6 + b.p1) * 5); }
      else if (b.freeze) { tx = b.freeze.x; ty = b.freeze.y; }
      else {
        tx = b.hfx * W + (RM ? 0 : Math.sin(t * b.w1 + b.p1) * b.amp);
        ty = b.hfy * H + (RM ? 0 : Math.cos(t * b.w2 + b.p2) * b.amp * .8);
      }
      if (b.d.status !== "rej") { tx = clamp(tx, b.r + 6, W - b.r - 6); ty = clamp(ty, b.r + 6, H - b.r - 44); }
      b.x += (tx - b.x) * .05; b.y += (ty - b.y) * .05;
    }
    const solid = live.filter((b) => b.d.status !== "rej");
    for (let i = 0; i < solid.length; i++) for (let j = i + 1; j < solid.length; j++) {
      const a = solid[i], c = solid[j];
      const dx = c.x - a.x, dy = c.y - a.y, d = Math.hypot(dx, dy) || .01, min = a.r + c.r + 6;
      if (d < min) {
        const o = (min - d) * .5, nx = dx / d, ny = dy / d, wa = a.drag ? 0 : 1, wc = c.drag ? 0 : 1, s = (wa + wc) || 1;
        a.x -= nx * o * wa / s; a.y -= ny * o * wa / s; c.x += nx * o * wc / s; c.y += ny * o * wc / s;
      }
    }
    for (const b of live) b.el.style.transform = `translate3d(${(b.x - b.r).toFixed(1)}px,${(b.y - b.r).toFixed(1)}px,0)`;
    if (P.cur) placePop(P.cur);
    P.raf = requestAnimationFrame(tick);
  }

  /* drag / click */
  const bubOf = (node) => P.bubbles.find((b) => b.el === node);
  let down = null;
  layer.addEventListener("pointerdown", (e) => {
    const node = e.target.closest(".mp-bub");
    const b = node && bubOf(node);
    if (!b || b.d.status === "rej") return;
    down = { b, px: e.clientX, py: e.clientY, moved: false };
    node.setPointerCapture(e.pointerId);
  });
  layer.addEventListener("pointermove", (e) => {
    if (!down) return;
    const { b } = down;
    if (!down.moved && Math.hypot(e.clientX - down.px, e.clientY - down.py) > 5) { down.moved = true; b.drag = true; b.el.classList.add("drag"); if (P.cur) closePop(); }
    if (down.moved) {
      const r = wrap.getBoundingClientRect();
      b.x = clamp(e.clientX - r.left, b.r + 6, P.W - b.r - 6);
      b.y = clamp(e.clientY - r.top, b.r + 6, P.H - b.r - 44);
      b.hfx = b.x / P.W; b.hfy = b.y / P.H;
    }
  });
  const endDrag = () => {
    if (!down) return;
    const { b, moved } = down;
    down = null;
    b.drag = false; b.el.classList.remove("drag");
    if (moved) queuePos(b); else openPop(b);
  };
  layer.addEventListener("pointerup", endDrag);
  layer.addEventListener("pointercancel", endDrag);
  layer.addEventListener("click", (e) => {
    const node = e.target.closest(".mp-bub");
    const b = node && bubOf(node);
    if (b && b.d.status === "rej") restore(b);
  });

  /* position saves are debounced; leaving the page flushes them */
  function queuePos(b) {
    if (b.tmp) return;
    clearTimeout(b.saveT);
    b.saveT = setTimeout(() => savePos(b), 600);
  }
  function savePos(b) {
    clearTimeout(b.saveT); b.saveT = 0;
    if (b.gone || b.tmp) return;
    const round = (v) => Math.round(clamp(v, 0, 1) * 1e4) / 1e4;
    write("PATCH", `/api/impressions/${b.d.id}`, { pos_x: round(b.hfx), pos_y: round(b.hfy) }).catch((err) => showError(seaErr, err));
  }
  function flushSaves() { P.bubbles.forEach((b) => { if (b.saveT) savePos(b); }); }

  /* ---------- detail pop-up ---------- */
  function openPop(b, opts = {}) {
    if (P.cur && P.cur !== b) closePop();
    P.cur = b; P.editing = false;
    b.freeze = opts.home ? { x: b.hfx * P.W, y: b.hfy * P.H } : { x: b.x, y: b.y };
    fillPop(b);
    pop.classList.add("open");
    placePop(b);
    if (opts.edit) startEdit(b);
  }
  function placePop(b) {
    const pw = 276, ph = pop.offsetHeight, f = b.freeze || { x: b.x, y: b.y }, { W, H } = P;
    const left = clamp(f.x - pw / 2, 4, Math.max(4, W - pw - 4));
    let top = f.y + b.r + 12;
    if (top + ph > H - 4) top = f.y - b.r - 12 - ph;
    pop.style.left = `${left}px`;
    pop.style.top = `${clamp(top, 4, Math.max(4, H - ph - 4))}px`;
  }
  function fillPop(b) {
    const d = b.d, k = kindOf(d);
    pop.style.setProperty("--bh", k.h);
    const pt = el("div", { class: "pt", spellcheck: "false" }, d.text || d.short);
    pt.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey && !e.isComposing && P.editing) { e.preventDefault(); finishEdit(b); }
    });
    const turns = (d.source_msg_ids || []).map((id) => [id, turnLabel(id)]).filter(([, l]) => l);
    const mine = d.kind === "mine";
    const from = d.source_persona ? (PERSONA[d.source_persona] || UNKNOWN_PERSONA) : null;
    const fromNode = from ? el("div", { class: "pfrom" }, `来自 ${from.name}${from.role ? `（${from.role}）` : ""}，由你引用`) : null;
    if (fromNode) fromNode.style.setProperty("--ph", from.hue);
    fill(pop, 
      el("div", { class: "pk" }, el("span", {}, k.label), el("em", { class: `tg ${b.tmp ? "draft" : d.status}` }, b.tmp ? "新的补充" : (STATE[d.status] || d.status))),
      d.kind === "rate" && !b.tmp ? el("div", { class: "pstars" }, ...[1, 2, 3, 4, 5].map((i) => el("button", {
        type: "button", class: i <= (d.stars || 0) ? "on" : "", "aria-label": `${i} 星`, onclick: () => commit(b, { stars: i }),
      }, "★"))) : null,
      pt,
      fromNode,
      turns.length ? el("div", { class: "ps" }, "依据", ...turns.map(([id, label]) => el("button", { type: "button", onclick: () => jumpTo(id) }, label))) : null,
      el("div", { class: "pa" },
        el("button", { type: "button", class: "y", hidden: b.tmp || d.status !== "draft", onclick: () => commit(b, { status: "ok" }) }, "采纳"),
        el("button", { type: "button", class: "e", onclick: () => (P.editing ? finishEdit(b) : startEdit(b)) }, P.editing ? "完成" : "编辑"),
        el("button", { type: "button", class: "n", onclick: () => rejectOrDelete(b) }, b.tmp ? "取消" : (mine ? "删除" : "驳回"))));
    if (P.editing) startEdit(b, true);
  }
  function jumpTo(id) {
    const m = msgById(id);
    if (!m) return;
    m.scrollIntoView({ behavior: RM ? "auto" : "smooth", block: "center" });
    m.classList.add("hl");
    setTimeout(() => m.classList.remove("hl"), 1600);
  }
  function startEdit(b, again) {
    const pt = pop.querySelector(".pt"), eb = pop.querySelector(".e");
    if (!pt) return;
    P.editing = true;
    pt.contentEditable = "plaintext-only";
    if (pt.contentEditable !== "plaintext-only") pt.contentEditable = "true";
    eb.textContent = "完成";
    if (b.tmp && !again) pt.textContent = "";
    pt.focus();
    const r = document.createRange(); r.selectNodeContents(pt); r.collapse(false);
    const s = getSelection(); s.removeAllRanges(); s.addRange(r);
  }
  async function finishEdit(b) {
    const pt = pop.querySelector(".pt");
    if (!pt) return;
    const v = pt.textContent.trim();
    P.editing = false;
    if (b.tmp) {
      if (!v) { removeBubble(b); closePop(true); updateEmpty(); return; }
      return createMine(b, v);
    }
    if (!v || v === (b.d.text || b.d.short)) return fillPop(b);
    await commit(b, { short: b.d.kind === "word" ? v.slice(0, 40) : shortOf(v), text: v });
  }
  function closePop(skipClean) {
    if (!P.cur) return;
    const b = P.cur;
    P.cur = null; P.editing = false; b.freeze = null;
    pop.classList.remove("open");
    if (!skipClean && b.tmp) { removeBubble(b); updateEmpty(); }
  }
  document.addEventListener("pointerdown", (e) => { if (P.cur && !e.target.closest("#mp-pop") && !e.target.closest(".mp-bub")) closePop(); });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && P.open && P.cur) closePop(); });

  /* ---------- changing a bubble ---------- */
  async function commit(b, patch, doneText) {
    clearError(seaErr);
    const prev = { ...b.d }, tok = P.tok;
    if ("status" in patch) b.d.status = patch.status;
    if ("stars" in patch) b.d.stars = patch.stars;
    paint(b);
    if (P.cur === b) fillPop(b);
    try {
      const res = await write("PATCH", `/api/impressions/${prev.id}`, patch);
      if (!alive(tok)) return;
      updateBubble(b, res.impression);
      syncEntry(res.entry);
      if (b.d.kind === "one" || b.d.kind === "rate") await loadImpressions(tok);
      if (doneText && alive(tok)) setStatus(doneText, "done");
    } catch (err) {
      if (!alive(tok)) return;
      b.d = prev; paint(b);
      if (P.cur === b) fillPop(b);
      showError(seaErr, err);
    }
  }
  function restore(b) { commit(b, { status: "draft" }, "已放回海面，等你确认"); }
  async function rejectOrDelete(b) {
    if (b.tmp) { closePop(); return; }
    closePop(true);
    if (b.d.kind !== "mine") {
      b.hfx = clamp(b.hfx, .1, .9);
      return commit(b, { status: "rej" }, "已驳回，它沉到海底了，点一下可以放回来");
    }
    clearError(seaErr);
    const tok = P.tok;
    try {
      const res = await write("DELETE", `/api/impressions/${b.d.id}`);
      if (!alive(tok)) return;
      removeBubble(b); updateEmpty();
      syncEntry(res && res.entry);
      renderCal();
    } catch (err) { if (alive(tok)) showError(seaErr, err); }
  }
  async function createMine(b, v) {
    clearError(seaErr);
    const tok = P.tok;
    try {
      const res = await write("POST", `/api/entries/${P.id}/impressions`, { short: shortOf(v), text: v });
      if (!alive(tok)) return;
      b.tmp = false; b.d = res.impression;
      const [x, y] = homeOf(b.d);
      b.hfx = x; b.hfy = y;
      setRadius(b); paint(b);
      syncEntry(res.entry);
      closePop(true);
      updateEmpty();
    } catch (err) {
      if (!alive(tok)) return;
      P.editing = true;
      showError(seaErr, err);
    }
  }
  $("mp-addb").addEventListener("click", () => {
    if (!P.open) return;
    closePop();
    const b = addBubble({ id: -Date.now(), kind: "mine", short: "写一句", text: "", status: "ok", stars: null, source_msg_ids: [], pos_x: null, pos_y: null },
      0, { tmp: true, from: [P.W * .88, P.H * .82] });
    b.active = true; b.el.classList.add("show");
    updateEmpty();
    openPop(b, { home: true, edit: true });
  });

  /* ---------- wiring ---------- */
  $("mp-back").addEventListener("click", () => {
    if (P.fromCal) history.back();
    else { history.replaceState(null, "", location.pathname + location.search); route(); }
  });
  addEventListener("hashchange", () => {
    if (!P.ready) return;
    if (!P.open && /^#\/movie\/\d+$/.test(location.hash)) P.fromCal = true;
    route();
  });
  addEventListener("beforeunload", flushSaves);
  window.moviePageRoute = () => { P.ready = true; route(); };
  if (window.__entriesReady) window.moviePageRoute();
})();
