"use strict";
/* 「你收藏的」: two panes. 收藏 saves texts and images; 小满 is the thinker shaped by them, with a mind and a chat.
   Loaded after app.js and shares its globals ($, el, api, ApiError, showError, clearError, renderText, openImage). */
(() => {
  const S = { mode: "text", file: null, platform: "", platforms: [], pieces: [], busy: false, loaded: false, chatTok: 0, editing: null,
    ocrEditing: null, retrying: new Set(), attach: null, attaching: false, sending: false, clearing: false, pane: "store", filter: "" };
  const SUGGEST = ["小满，你最在意的是什么？", "你和我哪里想得不一样？", "最近读到的那段，改变了你什么？", "你现在最拿不准的是什么？"];
  const MIND = { busy: false, again: false, state: null, timer: 0 };
  // Chat images are pasted or dropped onto the chat column; pieces are pasted or dropped onto the form.

  function setPane(pane, focus = true) {
    S.pane = pane;
    $("pieces").dataset.pane = pane;
    document.querySelectorAll(".pc-switch [data-pane]").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.pane === pane)));
    $("pc-store").hidden = pane !== "store";
    $("pc-talk").hidden = pane !== "talk";
    try { localStorage.setItem("pc-pane", pane); } catch { /* storage off */ }
    if (pane === "talk") {
      const box = $("pc-msgs");
      box.scrollTop = box.scrollHeight;
      if (focus) $("pc-chat-input").focus();
    }
  }
  function setMode(mode) {
    S.mode = mode;
    document.querySelectorAll("[data-pcmode]").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.pcmode === mode)));
    $("pc-text").hidden = mode !== "text";
    $("pc-drop").hidden = mode !== "image";
    clearError($("pc-error"));
  }
  function setFile(file) {
    if (!file) return;
    if (!file.type.startsWith("image/")) {
      showError($("pc-error"), { code: "image_invalid", message: `${file.name} 不是图片` });
      return;
    }
    S.file = file;
    setMode("image");
    $("pc-file").textContent = file.name || "粘贴的图片";
    const img = $("pc-preview");
    if (img.src) URL.revokeObjectURL(img.src);
    img.src = URL.createObjectURL(file);
    img.hidden = false;
  }
  function clearFile() {
    S.file = null;
    $("pc-image").value = "";
    const img = $("pc-preview");
    if (img.src) URL.revokeObjectURL(img.src);
    img.removeAttribute("src");
    img.hidden = true;
    $("pc-file").textContent = "也可以直接把图片拖到这里，或粘贴截图";
  }
  function renderPlatforms() {
    $("pc-plats").replaceChildren(...S.platforms.map((p) => el("button", {
      type: "button", "aria-pressed": String(S.platform === p),
      onclick: () => { S.platform = S.platform === p ? "" : p; renderPlatforms(); },
    }, p)));
  }

  async function upload(file, meta, path = "/api/pieces/image") {
    const qs = new URLSearchParams(Object.entries(meta || {}).filter(([, v]) => v));
    const resp = await fetch(`${path}${qs.toString() ? "?" + qs : ""}`, {
      method: "POST", headers: { "Content-Type": file.type || "application/octet-stream" }, body: file,
    }).catch((err) => { throw new ApiError("network_unreachable", `${err.name}: ${err.message}`); });
    let data = {};
    try { data = await resp.json(); } catch { /* non-JSON body */ }
    if (!resp.ok) throw new ApiError(data.error_code || "unknown_error", data.error || `HTTP ${resp.status}`);
    return data;
  }
  async function onSubmit(ev) {
    ev.preventDefault();
    if (S.busy) return;
    const meta = { title: $("pc-title").value.trim(), platform: S.platform, url: $("pc-url").value.trim(), note: $("pc-note").value.trim() };
    const text = $("pc-text").value.trim();
    if (S.mode === "text" && !text) return showError($("pc-error"), { code: "piece_empty", message: "先粘贴一段文字" });
    if (S.mode === "image" && !S.file) return showError($("pc-error"), { code: "image_empty", message: "先选一张图片" });
    S.busy = true;
    $("pc-submit").disabled = true;
    clearError($("pc-error"));
    try {
      if (S.mode === "text") await api("POST", "/api/pieces", { text, ...meta });
      else {
        $("pc-submit").textContent = "小满在读图…";
        const saved = await upload(S.file, meta);
        if (saved.ocr_status === "failed") showError($("pc-error"), { code: "image_transcribe_failed", message: `图片已存下，但没能转成文字：${saved.ocr_error}。可以在卡片上点「重新读图」。` });
      }
      ["pc-text", "pc-title", "pc-url", "pc-note"].forEach((id) => { $(id).value = ""; });
      S.platform = "";
      renderPlatforms();
      clearFile();
      await loadPieces();
    } catch (err) {
      showError($("pc-error"), err);
    } finally {
      S.busy = false;
      $("pc-submit").disabled = false;
      $("pc-submit").textContent = "交给小满";
    }
  }

  function source(p) {
    const bits = [`#${p.id}`, p.platform, p.title].filter(Boolean).join(" · ");
    const box = el("p", { class: "pc-src" }, bits);
    if (/^https?:\/\//.test(p.url)) box.append(" ", el("a", { href: p.url, target: "_blank", rel: "noopener noreferrer" }, "原文 ↗"));
    return box;
  }
  function noteView(p) {
    if (S.editing === p.id) {
      const input = el("input", { type: "text", maxlength: "500", value: p.note, placeholder: "写一句批注", "aria-label": "批注" });
      const save = async () => {
        try {
          await api("PATCH", `/api/pieces/${p.id}`, { note: input.value.trim() });
          S.editing = null;
          await loadPieces();
        } catch (err) { showError($("pc-error"), err); }
      };
      input.addEventListener("keydown", (e) => {
        if (e.key === "Enter" && !e.isComposing) { e.preventDefault(); save(); }
        if (e.key === "Escape") { S.editing = null; renderList(); }
      });
      setTimeout(() => input.focus(), 0);
      return el("div", { class: "pc-note editing" }, input,
        el("button", { type: "button", class: "lnk strong", onclick: save }, "保存"),
        el("button", { type: "button", class: "lnk", onclick: () => { S.editing = null; renderList(); } }, "取消"));
    }
    const edit = () => { S.editing = p.id; renderList(); };
    return p.note
      ? el("p", { class: "pc-note" }, el("b", {}, "我："), p.note, el("button", { type: "button", class: "lnk", onclick: edit }, "改"))
      : el("p", { class: "pc-note empty" }, el("button", { type: "button", class: "lnk", onclick: edit }, "+ 写一句批注"));
  }
  async function retry(p) {
    S.retrying.add(p.id);
    renderList();
    try {
      const got = await api("POST", `/api/pieces/${p.id}/transcribe`);
      if (got.ocr_status === "failed") showError($("pc-error"), { code: "image_transcribe_failed", message: got.ocr_error });
      else clearError($("pc-error"));
    } catch (err) { showError($("pc-error"), err); }
    S.retrying.delete(p.id);
    await loadPieces().catch((err) => showError($("pc-error"), err));
  }
  function ocrView(p) {
    if (S.retrying.has(p.id)) return el("p", { class: "pc-ocr pending" }, "正在读图…");
    if (S.ocrEditing === p.id) {
      const words = el("textarea", { rows: "4", maxlength: "5000", "aria-label": "图中文字" });
      words.value = p.text;
      const cap = el("input", { type: "text", maxlength: "300", value: p.caption, "aria-label": "画面描述", placeholder: "画面描述" });
      const save = async () => {
        try {
          await api("PATCH", `/api/pieces/${p.id}`, { text: words.value, caption: cap.value });
          S.ocrEditing = null;
          await loadPieces();
        } catch (err) { showError($("pc-error"), err); }
      };
      setTimeout(() => words.focus(), 0);
      return el("div", { class: "pc-ocr editing" }, words, cap, el("div", {},
        el("button", { type: "button", class: "lnk strong", onclick: save }, "保存"),
        el("button", { type: "button", class: "lnk", onclick: () => { S.ocrEditing = null; renderList(); } }, "取消")));
    }
    if (p.ocr_status === "failed") {
      return el("p", { class: "pc-ocr failed", title: p.ocr_error }, "没能读出图中文字。",
        el("button", { type: "button", class: "lnk", onclick: () => retry(p) }, "重新读图"));
    }
    const box = el("details", { class: "pc-ocr" }, el("summary", {}, p.text ? "图中文字" : "画面描述"));
    if (p.text) box.append(el("blockquote", {}, p.text));
    if (p.caption) box.append(el("p", { class: "cap" }, p.caption));
    box.append(el("div", {},
      el("button", { type: "button", class: "lnk", onclick: () => { S.ocrEditing = p.id; renderList(); } }, "改错字"),
      el("button", { type: "button", class: "lnk", onclick: () => retry(p) }, "重新读图")));
    return box;
  }
  function card(p, i) {
    const body = p.kind === "image"
      ? el("div", {}, el("img", { src: p.image_url, alt: p.caption || p.title || "收藏的图片", loading: "lazy",
        onclick: () => openImage({ url: p.image_url, quote: p.title || "收藏的图片", id: `piece-${p.id}` }) }), ocrView(p))
      : el("blockquote", {}, p.text);
    const ask = el("button", { type: "button", class: "lnk", onclick: () => {
      setPane("talk");
      const input = $("pc-chat-input");
      input.value = `你怎么看 #${p.id} 这段？`;
      input.focus();
    } }, "问小满这段");
    return el("figure", { class: `pc-card ${p.kind}`, "data-id": p.id, style: `--i:${Math.min(i, 12)}` },
      body, source(p), noteView(p),
      el("div", { class: "pc-card-acts" }, ask),
      el("button", { type: "button", class: "x", "aria-label": "删除这条收藏", onclick: () => remove(p) }, "×"));
  }
  function cited() {
    const m = MIND.state && MIND.state.mind;
    const ids = new Set();
    if (m) [...m.views, ...m.tastes, ...m.doubts, ...m.psychology].forEach((it) => it.sources.forEach((i) => ids.add(i)));
    return ids;
  }
  function renderOverview() {
    const box = $("pc-overview");
    const all = S.pieces;
    $("pc-tab-count").textContent = all.length ? `已交给小满 ${all.length} 条` : "把读到的东西交给小满";
    if (!all.length) {
      box.replaceChildren(el("h4", {}, "概览"),
        el("p", { class: "ov-empty" }, "这里会汇总你交给小满的东西：多少段文字、多少张图、来自哪些地方。"));
      return;
    }
    const texts = all.filter((p) => p.kind === "text").length;
    const noted = all.filter((p) => p.note).length;
    const used = cited();
    const inMind = all.filter((p) => used.has(p.id)).length;
    const plats = {};
    all.forEach((p) => { const k = p.platform || "未标来源"; plats[k] = (plats[k] || 0) + 1; });
    const rows = Object.entries(plats).sort((a, b) => b[1] - a[1]);
    const top = rows[0][1];
    const stat = (n, label) => el("div", { class: "ov-stat" }, el("b", {}, String(n)), el("span", {}, label));
    const label = (p) => (p.title || p.text || p.caption || "图片").replace(/\s+/g, " ").slice(0, 20);
    box.replaceChildren(
      el("h4", {}, "概览"),
      el("div", { class: "ov-stats" }, stat(all.length, "条收藏"), stat(texts, "段文字"), stat(all.length - texts, "张图"), stat(noted, "条有批注")),
      el("p", { class: "ov-mind" }, inMind ? `其中 ${inMind} 条成了小满某个看法的来源` : "小满还没从这些收藏里形成看法"),
      el("h4", {}, "来自"),
      el("ul", { class: "ov-plats" }, ...rows.map(([k, n]) => el("li", {},
        el("button", { type: "button", "aria-pressed": String(S.filter === k), title: `只看${k}`,
          onclick: () => { S.filter = S.filter === k ? "" : k; renderList(); } },
          el("span", {}, k), el("i", { style: `--w:${Math.max(8, Math.round((n / top) * 100))}%` }), el("b", {}, String(n)))))),
      el("h4", {}, "最近交来的"),
      el("ol", { class: "ov-recent" }, ...all.slice(0, 5).map((p) => el("li", {},
        el("button", { type: "button", onclick: () => jump(p.id) }, el("em", {}, `#${p.id}`),
          p.kind === "image" ? el("small", {}, "图") : null, label(p))))));
  }
  function renderList() {
    const list = $("pc-list");
    renderOverview();
    const shown = S.filter ? S.pieces.filter((p) => (p.platform || "未标来源") === S.filter) : S.pieces;
    $("pc-count").textContent = !S.pieces.length ? "" : S.filter ? `${S.filter} · ${shown.length} 条` : `全部 ${S.pieces.length} 条`;
    $("pc-filter").replaceChildren(...(S.filter ? [el("button", { type: "button", onclick: () => { S.filter = ""; renderList(); } }, "看全部")] : []));
    if (!S.pieces.length) {
      list.replaceChildren(el("p", { class: "wall-empty" }, "还没有收藏。读到打动你的文字，粘贴进来，再随手写一句它为什么打动你。小满会读这些东西，慢慢长出自己的看法。"));
      return;
    }
    list.replaceChildren(...shown.map(card));
  }
  async function remove(p) {
    if (!confirm("删除这条收藏？")) return;
    try {
      await api("DELETE", `/api/pieces/${p.id}`);
      await loadPieces();
    } catch (err) { showError($("pc-error"), err); }
  }
  async function loadPieces() {
    const res = await api("GET", "/api/pieces");
    S.pieces = res.pieces;
    if (!S.platforms.length) { S.platforms = res.platforms; renderPlatforms(); }
    renderList();
    refreshMind();
  }

  /* ---- 小满的想法 ---- */
  function cite(ids) {
    return ids.map((i) => el("button", { type: "button", class: "pc-cite", title: `看 #${i}`, onclick: () => jump(i) }, `#${i}`));
  }
  function jump(id) {
    if (S.pane !== "store") setPane("store", false);
    if (S.filter && !document.querySelector(`.pc-card[data-id="${id}"]`)) { S.filter = ""; renderList(); }
    const node = document.querySelector(`.pc-card[data-id="${id}"]`);
    if (!node) return;
    node.scrollIntoView({ behavior: "smooth", block: "center" });
    node.classList.remove("flash");
    void node.offsetWidth;
    node.classList.add("flash");
  }
  function reveal(label, path, display) {
    const line = el("span", { class: "pc-cites" });
    line.append(el("button", { type: "button", class: "pc-cite", onclick: async () => {
      try { line.replaceChildren(display(await api("GET", path))); }
      catch (err) { showError($("pc-chat-error"), err); }
    } }, label));
    return line;
  }
  function mindList(title, items) {
    if (!items.length) return null;
    return el("div", { class: "pc-mind-sec" }, el("h4", {}, title), el("ul", {}, ...items.map((it) => {
      const chats = [...new Set(it.evidence.filter((e) => e.kind === "chat").map((e) => e.source_id))];
      const node = el("li", {}, it.text, " ", el("span", { class: "pc-cites" }, "来自 ", ...cite(it.sources),
        ...chats.map((id) => reveal(`对话 #${id}`, `/api/pieces/chat/${id}`, (m) => `「${m.content}」`))));
      if (it.reason) node.append(el("div", { class: "muted" }, `变化原因：${it.reason}`));
      if (it.replaces) node.append(reveal("原先怎么想", `/api/pieces/mind/nodes/${it.replaces}`, (n) => `原先：${n.text}`));
      if (it.kind === "user") node.append(el("button", { type: "button", class: "pc-cite", onclick: async () => {
        try { await api("DELETE", `/api/pieces/mind/memories/${it.id}`); refreshMind(); }
        catch (err) { showError($("pc-chat-error"), err); }
      } }, "忘记这条"));
      return node;
    })));
  }
  function renderMind() {
    const st = MIND.state;
    if (!st) return;
    const status = $("pc-mind-status");
    const m0 = st.mind;
    const n = m0.views.length + m0.tastes.length + m0.doubts.length + m0.psychology.length;
    if (st.pending && !st.error) status.textContent = `还有 ${st.pending} 处变化待消化…`;
    else if (st.error) status.textContent = "上次没想完";
    else status.textContent = st.total ? `读过 ${st.total} 条 · ${n} 个念头` : "还什么都没读过";
    $("pc-tab-mood").textContent = st.busy ? "正在读…" : n ? `${n} 个念头` : "和小满聊聊";
    // The orb grows with what 小满 has read and thought: size from pieces, brightness from mind items.
    const orb = $("xm-orb");
    orb.style.setProperty("--g", String(Math.min(1, Math.log2(1 + st.total) / 6)));
    orb.style.setProperty("--l", String(Math.min(1, n / 16)));
    document.querySelectorAll(".xm-orb").forEach((o) => o.classList.toggle("thinking", st.busy));
    const body = $("pc-mind-body");
    const m = st.mind;
    const parts = n || m.user.length ? [mindList("我相信", m.views), mindList("我偏爱", m.tastes),
      mindList("我拿不准", m.doubts), mindList("我的思考倾向", m.psychology), mindList("你让我记住的", m.user)]
      : [el("p", { class: "muted pc-mind-empty" }, st.total ? "小满还在读，暂时没形成看法。" : "小满还什么都没读过。先去「收藏」交一点东西给小满。")];
    if (st.error && !st.busy) {
      parts.push(el("p", { class: "pc-mind-err" }, `更新失败：${st.error} `,
        el("button", { type: "button", class: "lnk", onclick: () => refreshMind(true) }, "再试一次")));
    }
    body.replaceChildren(...parts.filter(Boolean));
  }
  async function refreshMind(force = false) {
    if (MIND.busy) { MIND.again = force || MIND.again || "read"; return; }
    clearTimeout(MIND.timer);
    MIND.busy = true;
    try {
      MIND.state = force ? await api("POST", "/api/pieces/mind/refresh") : await api("GET", "/api/pieces/mind");
    } catch (err) {
      MIND.state = await api("GET", "/api/pieces/mind").catch(() => MIND.state);
    } finally {
      MIND.busy = false;
      renderMind();
      renderOverview();
      if (MIND.again) { const retry = MIND.again === true; MIND.again = false; refreshMind(retry); }
      else if (MIND.state?.retryable) MIND.timer = setTimeout(() => refreshMind(), MIND.state.error ? 5000 : 1500);
    }
  }

  function saveButton(m) {
    const saved = m.meta && m.meta.saved_piece_id;
    if (saved) return el("span", { class: "pc-saved" }, `已存为 #${saved}`);
    const btn = el("button", { type: "button", class: "lnk" }, "存进收藏");
    btn.addEventListener("click", async () => {
      btn.disabled = true;
      btn.textContent = "正在存、读图…";
      clearError($("pc-chat-error"));
      try {
        const piece = await api("POST", `/api/pieces/chat/${m.id}/save`, {});
        btn.replaceWith(el("span", { class: "pc-saved" }, `已存为 #${piece.id}`));
        if (piece.ocr_status === "failed") showError($("pc-chat-error"), { code: "image_transcribe_failed", message: `已存下，但没能读出文字：${piece.ocr_error}` });
        await loadPieces();
      } catch (err) {
        showError($("pc-chat-error"), err);
        btn.disabled = false;
        btn.textContent = "存进收藏";
      }
    });
    return btn;
  }
  function addMsg(m) {
    const box = $("pc-msgs");
    const node = el("div", { class: `m ${m.role === "assistant" ? "a" : "u"}` });
    if (m.image_url) {
      node.append(el("img", { class: "pc-mimg", src: m.image_url, alt: "对话里发的图片", loading: "lazy",
        onclick: () => openImage({ url: m.image_url, quote: "对话里发的图片", id: `chat-${m.id || "new"}` }) }));
      if (m.id) node.append(el("div", { class: "pc-mact" }, saveButton(m)));
    }
    if (m.content) { const span = el("span", {}); renderText(span, m.content); node.append(span); }
    if (m.id && m.role === "user" && m.content) {
      node.append(el("div", { class: "pc-mact" }, el("button", { type: "button", class: "lnk", onclick: async () => {
        const topic = window.prompt("确认把这句话原样保存为用户记忆。它适用于哪个主题？（例如：电影）", "");
        if (!topic?.trim()) return;
        try { await api("POST", `/api/pieces/chat/${m.id}/remember`, { topic: topic.trim() }); refreshMind(); }
        catch (err) { showError($("pc-chat-error"), err); }
      } }, "记住这句话")));
    }
    box.append(node);
    box.scrollTop = box.scrollHeight;
    return node;
  }
  function renderAttach() {
    const box = $("pc-attach");
    if (!S.attach && !S.attaching) { box.hidden = true; box.replaceChildren(); return; }
    box.hidden = false;
    box.replaceChildren(
      S.attaching ? el("span", { class: "muted" }, "正在上传图片…") : el("img", { src: S.attach.image_url, alt: "要发送的图片" }),
      S.attaching ? null : el("button", { type: "button", class: "lnk", onclick: () => { S.attach = null; renderAttach(); } }, "移除"));
  }
  async function attach(file) {
    if (!file || !file.type.startsWith("image/")) return;
    S.attaching = true;
    renderAttach();
    clearError($("pc-chat-error"));
    try {
      S.attach = await upload(file, null, "/api/pieces/chat/image");
    } catch (err) {
      showError($("pc-chat-error"), err);
    } finally {
      S.attaching = false;
      renderAttach();
    }
  }
  async function loadChat() {
    const tok = ++S.chatTok;
    const { messages } = await api("GET", "/api/pieces/chat");
    if (tok !== S.chatTok) return;
    $("pc-msgs").replaceChildren();
    messages.forEach(addMsg);
  }
  async function onSend(ev) {
    ev.preventDefault();
    const input = $("pc-chat-input");
    const text = input.value.trim();
    const pic = S.attach;
    const useForGrowth = $("pc-use-for-growth").checked;
    if ((!text && !pic) || S.sending || S.attaching || S.clearing) return;
    S.sending = true;
    $("pc-send").disabled = true;
    $("pc-clear").disabled = true;
    clearError($("pc-chat-error"));
    const mine = addMsg({ role: "user", content: text, image_url: pic ? pic.image_url : "" });
    input.value = "";
    $("pc-use-for-growth").checked = false;
    S.attach = null;
    renderAttach();
    const pending = $("pc-thinking").content.firstElementChild.cloneNode(true);
    $("pc-msgs").append(pending);
    $("pc-msgs").scrollTop = $("pc-msgs").scrollHeight;
    try {
      const res = await api("POST", "/api/pieces/chat", { message: text, image: pic ? pic.image : "", use_for_growth: useForGrowth });
      pending.remove();
      mine.replaceWith(addMsg(res.user_message));
      addMsg(res.message);
      if (useForGrowth) refreshMind();
    } catch (err) {
      pending.remove();
      mine.remove();
      showError($("pc-chat-error"), err);
      input.value = text;
      $("pc-use-for-growth").checked = useForGrowth;
      S.attach = pic;
      renderAttach();
    } finally {
      S.sending = false;
      $("pc-send").disabled = false;
      $("pc-clear").disabled = false;
      input.focus();
    }
  }
  async function onClear() {
    if (S.sending || S.clearing || !confirm("清空和小满的对话？收藏会保留，但依赖这些对话的看法和记忆会失效。")) return;
    S.clearing = true;
    $("pc-clear").disabled = true;
    $("pc-send").disabled = true;
    try {
      await api("DELETE", "/api/pieces/chat");
      ++S.chatTok;
      $("pc-msgs").replaceChildren();
      refreshMind();
    } catch (err) { showError($("pc-chat-error"), err); }
    finally {
      S.clearing = false;
      $("pc-clear").disabled = false;
      $("pc-send").disabled = false;
    }
  }

  window.renderPieces = () => {
    if (!S.loaded) {
      S.loaded = true;
      let saved = "store";
      try { saved = localStorage.getItem("pc-pane") || "store"; } catch { /* storage off */ }
      setPane(saved === "talk" ? "talk" : "store", false);
      loadChat().catch((err) => showError($("pc-chat-error"), err));
    }
    loadPieces().catch((err) => showError($("pc-error"), err));
  };

  document.querySelectorAll("[data-pcmode]").forEach((b) => b.addEventListener("click", () => setMode(b.dataset.pcmode)));
  $("pc-form").addEventListener("submit", onSubmit);
  document.querySelectorAll(".pc-switch [data-pane]").forEach((b) => b.addEventListener("click", () => setPane(b.dataset.pane)));
  $("pc-pick").addEventListener("click", () => $("pc-image").click());
  $("pc-image").addEventListener("change", () => setFile($("pc-image").files[0]));
  const drop = $("pc-form");
  drop.addEventListener("dragover", (e) => { if ([...e.dataTransfer.items].some((i) => i.kind === "file")) { e.preventDefault(); drop.classList.add("over"); } });
  drop.addEventListener("dragleave", () => drop.classList.remove("over"));
  drop.addEventListener("drop", (e) => { e.preventDefault(); drop.classList.remove("over"); setFile(e.dataTransfer.files[0]); });
  drop.addEventListener("paste", (e) => {
    const file = [...e.clipboardData.files].find((f) => f.type.startsWith("image/"));
    if (file) { e.preventDefault(); setFile(file); }
  });
  $("pc-chat-form").addEventListener("submit", onSend);
  $("pc-attach-btn").addEventListener("click", () => $("pc-chat-image").click());
  $("pc-chat-image").addEventListener("change", () => { attach($("pc-chat-image").files[0]); $("pc-chat-image").value = ""; });
  const chatBox = document.querySelector(".pc-chat");
  chatBox.addEventListener("paste", (e) => {
    const file = [...e.clipboardData.files].find((f) => f.type.startsWith("image/"));
    if (file) { e.preventDefault(); attach(file); }
  });
  chatBox.addEventListener("dragover", (e) => { if ([...e.dataTransfer.items].some((i) => i.kind === "file")) { e.preventDefault(); chatBox.classList.add("over"); } });
  chatBox.addEventListener("dragleave", () => chatBox.classList.remove("over"));
  chatBox.addEventListener("drop", (e) => { e.preventDefault(); chatBox.classList.remove("over"); attach(e.dataTransfer.files[0]); });
  $("pc-chat-input").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); $("pc-chat-form").requestSubmit(); }
  });
  $("pc-clear").addEventListener("click", onClear);
  $("pc-sug").replaceChildren(...SUGGEST.map((t) => el("button", { type: "button", onclick: () => {
    $("pc-chat-input").value = t;
    $("pc-chat-input").focus();
  } }, t)));
  if (document.body.dataset.view === "pieces") window.renderPieces();
})();
