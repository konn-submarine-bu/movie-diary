"use strict";
/* 「你收藏的」: save texts and images from outside the diary, and chat about them.
   Loaded after app.js and shares its globals ($, el, api, ApiError, showError, clearError, renderText, openImage). */
(() => {
  const S = { mode: "text", file: null, platform: "", platforms: [], pieces: [], busy: false, loaded: false, chatTok: 0, editing: null };
  const SUGGEST = ["我收藏的这些文字，有哪几个反复出现的主题？", "最近存的那段，帮我拆解一下", "关于这个话题，替我说一段：", "这些收藏里有没有互相矛盾的观点？"];

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

  async function upload(file, meta) {
    const qs = new URLSearchParams(Object.entries(meta).filter(([, v]) => v));
    const resp = await fetch(`/api/pieces/image?${qs}`, {
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
      else await upload(S.file, meta);
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
  function card(p, i) {
    const body = p.kind === "image"
      ? el("img", { src: p.image_url, alt: p.title || "收藏的图片", loading: "lazy",
        onclick: () => openImage({ url: p.image_url, quote: p.title || "收藏的图片", id: `piece-${p.id}` }) })
      : el("blockquote", {}, p.text);
    const ask = el("button", { type: "button", class: "lnk", onclick: () => {
      const input = $("pc-chat-input");
      input.value = `聊聊 #${p.id} 这段：`;
      input.focus();
    } }, "聊这段");
    return el("figure", { class: `pc-card ${p.kind}`, style: `--i:${Math.min(i, 12)}` },
      body, source(p), noteView(p),
      el("div", { class: "pc-card-acts" }, ask),
      el("button", { type: "button", class: "x", "aria-label": "删除这条收藏", onclick: () => remove(p) }, "×"));
  }
  function renderList() {
    const list = $("pc-list");
    $("pc-count").textContent = S.pieces.length ? `共 ${S.pieces.length} 条` : "";
    if (!S.pieces.length) {
      list.replaceChildren(el("p", { class: "wall-empty" }, "还没有收藏。读到打动你的文字，粘贴进来，再随手写一句它为什么打动你。之后可以在右边和我聊这些内容。"));
      return;
    }
    list.replaceChildren(...S.pieces.map(card));
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
  }

  function addMsg(m) {
    const box = $("pc-msgs");
    const span = el("span", {});
    renderText(span, m.content);
    box.append(el("div", { class: `m ${m.role === "assistant" ? "a" : "u"}` }, span));
    box.scrollTop = box.scrollHeight;
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
    if (!text || S.sending) return;
    S.sending = true;
    $("pc-send").disabled = true;
    clearError($("pc-chat-error"));
    addMsg({ role: "user", content: text });
    input.value = "";
    const pending = el("div", { class: "m a pending" }, "正在翻你的收藏…");
    $("pc-msgs").append(pending);
    $("pc-msgs").scrollTop = $("pc-msgs").scrollHeight;
    try {
      const res = await api("POST", "/api/pieces/chat", { message: text });
      pending.remove();
      addMsg(res.message);
    } catch (err) {
      pending.remove();
      $("pc-msgs").lastElementChild?.remove();
      showError($("pc-chat-error"), err);
      input.value = text;
    } finally {
      S.sending = false;
      $("pc-send").disabled = false;
      input.focus();
    }
  }
  async function onClear() {
    if (!confirm("清空和「你收藏的」的对话？收藏本身不受影响。")) return;
    try {
      await api("DELETE", "/api/pieces/chat");
      $("pc-msgs").replaceChildren();
    } catch (err) { showError($("pc-chat-error"), err); }
  }

  window.renderPieces = () => {
    if (!S.loaded) {
      S.loaded = true;
      loadChat().catch((err) => showError($("pc-chat-error"), err));
    }
    loadPieces().catch((err) => showError($("pc-error"), err));
  };

  document.querySelectorAll("[data-pcmode]").forEach((b) => b.addEventListener("click", () => setMode(b.dataset.pcmode)));
  $("pc-form").addEventListener("submit", onSubmit);
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
