// Records the README animations from the real front end.
//   node docs/demo/record.mjs            (all scenes)
//   node docs/demo/record.mjs poem       (one scene)
// Needs: Microsoft Edge or Chrome, ffmpeg, python3 with the project requirements.
import { spawn, execFileSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(HERE, "../..");
const OUT = path.join(ROOT, "docs/media");
const BROWSERS = ["/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", "google-chrome", "chromium"];
const BROWSER = process.env.BROWSER || BROWSERS.find((b) => !b.startsWith("/") || fs.existsSync(b));
const W = 1280, H = 800;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function waitFor(fn, what, ms = 20000) {
  const t0 = Date.now();
  for (;;) {
    try { const v = await fn(); if (v) return v; } catch {}
    if (Date.now() - t0 > ms) throw new Error(`timed out waiting for ${what}`);
    await sleep(200);
  }
}

class Page {
  constructor(ws) {
    this.ws = ws; this.id = 0; this.pend = new Map(); this.frames = []; this.recording = false; this.dir = "";
    ws.onmessage = (m) => {
      const d = JSON.parse(m.data);
      if (d.id && this.pend.has(d.id)) { this.pend.get(d.id)(d); this.pend.delete(d.id); }
      else if (d.method === "Runtime.exceptionThrown") (this.errors ||= []).push(d.params.exceptionDetails.exception?.description || d.params.exceptionDetails.text);
    };
  }
  send(method, params = {}) { return new Promise((r) => { const id = ++this.id; this.pend.set(id, r); this.ws.send(JSON.stringify({ id, method, params })); }); }
  async ev(expr) {
    const r = await this.send("Runtime.evaluate", { expression: expr, returnByValue: true, awaitPromise: true });
    if (r.result.exceptionDetails) throw new Error(`${expr.slice(0, 80)} -> ${r.result.exceptionDetails.exception?.description}`);
    return r.result.result.value;
  }
  async startRecording(dir) {
    this.dir = dir; fs.mkdirSync(dir, { recursive: true }); this.recording = true; this.frames = [];
    this.loop = (async () => {
      while (this.recording) {
        const r = await this.send("Page.captureScreenshot", { format: "jpeg", quality: 72 });
        if (!r.result) continue;
        const name = `f${String(this.frames.length).padStart(5, "0")}.jpg`;
        fs.writeFileSync(path.join(dir, name), Buffer.from(r.result.data, "base64"));
        this.frames.push({ name, t: Date.now() });
      }
    })();
  }
  async stopRecording() { this.recording = false; await this.loop; }
  async hold(ms) { await sleep(ms); }

  async setup() {
    await this.ev(`(() => {
      const mk = (id, css, html) => { const d = document.createElement("div"); d.id = id; d.setAttribute("popover", "manual");
        d.style.cssText = "position:fixed;inset:auto;margin:0;border:0;padding:0;background:none;overflow:visible;pointer-events:none;" + css;
        if (html) d.innerHTML = html; document.body.append(d); d.showPopover(); return d; };
      mk("rec-cur", "left:0;top:0;width:26px;height:26px;transform:translate(640px,420px);transition:transform .45s cubic-bezier(.4,0,.2,1);filter:drop-shadow(0 2px 3px rgba(0,0,0,.55))",
        '<svg width="26" height="26" viewBox="0 0 24 24"><path d="M4 2l16 9-7 2-3 7z" fill="#fff" stroke="#111" stroke-width="1.4" stroke-linejoin="round"/></svg>');
      mk("rec-ring", "left:0;top:0;width:34px;height:34px;margin:-8px 0 0 -8px;border-radius:50%;border:2px solid #fff;opacity:0;transform:translate(640px,420px)");
      mk("rec-cap", "left:50%;bottom:26px;transform:translateX(-50%);max-width:80%;padding:9px 20px;border-radius:999px;background:rgba(0,0,0,.72);color:#fff;font:500 15px/1.4 -apple-system,'PingFang SC',sans-serif;letter-spacing:.04em;white-space:nowrap;opacity:0;transition:opacity .3s", "");
    })()`);
  }
  async top() { await this.ev(`["rec-ring","rec-cur","rec-cap"].forEach((i)=>{const e=document.getElementById(i);try{e.hidePopover()}catch{};e.showPopover()})`); }
  async cap(text) {
    await this.ev(`(() => { const c = document.getElementById("rec-cap"); c.textContent = ${JSON.stringify(text)}; c.style.opacity = ${text ? 1 : 0}; })()`);
    await this.top();
  }
  async center(expr) {
    const measure = () => this.ev(`(() => { const e = (${expr}); if (!e) return null; e.scrollIntoView({block:"nearest"}); const b = e.getBoundingClientRect(); return [b.left + b.width / 2, b.top + b.height / 2]; })()`);
    let prev = await measure();
    if (!prev) throw new Error(`element not found: ${expr.slice(0, 100)}`);
    for (let i = 0; i < 20; i++) {
      await sleep(120);
      const cur = await measure();
      if (cur && Math.abs(cur[0] - prev[0]) < 0.5 && Math.abs(cur[1] - prev[1]) < 0.5) return cur;
      prev = cur || prev;
    }
    return prev;
  }
  async moveTo(x, y) {
    await this.ev(`document.getElementById("rec-cur").style.transform = "translate(${x}px,${y}px)"; document.getElementById("rec-ring").style.transform = "translate(${x}px,${y}px)"`);
    await this.send("Input.dispatchMouseEvent", { type: "mouseMoved", x, y });
    await sleep(520);
  }
  async click(expr, after = 700) {
    let [x, y] = await this.center(expr);
    await this.moveTo(x, y);
    const [x2, y2] = await this.center(expr);
    if (Math.abs(x2 - x) > 1 || Math.abs(y2 - y) > 1) { x = x2; y = y2; await this.moveTo(x, y); }
    await this.ev(`(() => { const r = document.getElementById("rec-ring"); r.style.transition = "none"; r.style.opacity = .9; r.style.scale = .5;
      requestAnimationFrame(() => { r.style.transition = "opacity .45s, scale .45s"; r.style.opacity = 0; r.style.scale = 1.5; }); })()`);
    await this.send("Input.dispatchMouseEvent", { type: "mousePressed", x, y, button: "left", clickCount: 1 });
    await sleep(70);
    await this.send("Input.dispatchMouseEvent", { type: "mouseReleased", x, y, button: "left", clickCount: 1 });
    await sleep(after);
    await this.top();
  }
  async type(expr, text, gap = 75) {
    await this.click(expr, 250);
    for (const ch of text) { await this.send("Input.insertText", { text: ch }); await sleep(gap + Math.random() * 40); }
    await sleep(350);
  }
  async idle(timeout = 20000) {
    await waitFor(() => this.ev(`!document.querySelector(".typing, .pending, .tmp-busy")`), "typing to finish", timeout);
    await sleep(500);
  }
  async save(name) {
    await this.stopRecording();
    const f = this.frames; if (f.length < 3) throw new Error("too few frames");
    const lines = ["ffconcat version 1.0"];
    f.forEach((fr, i) => {
      lines.push(`file '${fr.name}'`);
      if (i < f.length - 1) lines.push(`duration ${Math.max(0.04, (f[i + 1].t - fr.t) / 1000).toFixed(3)}`);
    });
    lines.push(`file '${f[f.length - 1].name}'`);
    fs.writeFileSync(path.join(this.dir, "list.txt"), lines.join("\n"));
    fs.mkdirSync(OUT, { recursive: true });
    const gif = path.join(OUT, `${name}.gif`);
    execFileSync("ffmpeg", ["-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", path.join(this.dir, "list.txt"),
      "-vf", "fps=8,scale=860:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=96:stats_mode=diff[p];[b][p]paletteuse=dither=bayer:bayer_scale=4:diff_mode=rectangle",
      "-loop", "0", gif]);
    const kb = Math.round(fs.statSync(gif).size / 1024);
    console.log(`  ${name}.gif  ${f.length} frames  ${((f[f.length - 1].t - f[0].t) / 1000).toFixed(1)}s  ${kb} KB`);
  }
}

const day = (n) => `(() => { const d = new Date(); d.setDate(d.getDate() - ${n}); const p = (v) => String(v).padStart(2, "0"); return document.querySelector('#grid .day[data-k="' + d.getFullYear() + "-" + p(d.getMonth() + 1) + "-" + p(d.getDate()) + '"]'); })()`;
const btn = (root, text) => `[...document.querySelectorAll("${root} button, ${root} a")].find((b) => b.textContent.trim() === ${JSON.stringify(text)})`;
const ready = (p, s, what) => waitFor(() => p.ev(`!!document.querySelector(${JSON.stringify(s)})`), what || s);
const api = (p, url) => p.ev(`fetch(${JSON.stringify(url)}).then((r) => r.json())`);
const must = (cond, what) => { if (!cond) throw new Error(`assertion failed: ${what}`); };
const sel = (s) => `document.querySelector(${JSON.stringify(s)})`;
const tab = (v) => sel(`.tabs [data-view="${v}"]`);
const swatch = (k) => sel(`#sw [data-k="${k}"]`);
const send = async (p, text) => { await p.type(sel("#chat-input"), text); await p.click(sel("#send-btn"), 400); await p.idle(); };

const SCENES = {
  async calendar(p) {
    await p.cap("日历：每个有记录的日子是一颗封面圆点"); await p.hold(1800);
    await p.click(day(6)); await p.hold(1500);
    await p.click(day(3)); await p.hold(1300);
    await p.cap("配色会跟着最近看的片走，也可以手动换");
    for (const k of ["fog", "neon", "blood", "olive", "rose", "cream", "sage"]) { await p.click(swatch(k), 1000); }
    await p.cap("点「自动」，回到按观影类型推荐的配色");
    await p.click(sel('#sw [data-k="auto"]'), 1800);
  },
  async "add-rate"(p) {
    await p.cap("搜索一部电影，选「看过」或「想看」");
    await p.type(sel("#search-input"), "沙丘");
    await p.send("Input.dispatchKeyEvent", { type: "keyDown", key: "Enter", code: "Enter", windowsVirtualKeyCode: 13, text: "\r" });
    await p.send("Input.dispatchKeyEvent", { type: "keyUp", key: "Enter", code: "Enter", windowsVirtualKeyCode: 13 });
    await waitFor(() => p.ev(`!!document.querySelector("#search-results .result")`), "search results");
    await p.hold(900);
    await p.click(btn("#search-results .result-acts", "看过"), 1500);
    await p.cap("点星星打分");
    await p.click(sel("#hero .rate button:nth-child(5)"), 1200);
    await p.cap("写一句感受，加上标签");
    await p.click(btn("#hero", "编辑"), 600);
    await p.type(sel("#hero .edit textarea"), "沙漠里的风声比台词更像台词。", 60);
    await p.click(btn("#hero .edit", "保存"), 1800);
    const e = (await api(p, "/api/entries")).entries.find((x) => x.title === "沙丘");
    must(e && e.status === "watched" && e.rating === 5 && e.note.includes("风声"), "沙丘 saved with 5 stars and note");
  },
  async "agent-chat"(p) {
    await p.cap("直接和 Agent 说：它会查片、写日记");
    await send(p, "昨天看了盗梦空间，4 星，梦境一层套一层，最后那个陀螺转得我睡不着。");
    await p.cap("日历已经多了一条记录"); await p.hold(2200);
    await p.cap("点进某部电影，聊聊这一部，需要时联网找来源");
    await p.click(day(6), 900);
    await p.click(btn("#hero", "聊聊这部 →"), 700);
    await send(p, "花样年华结尾，吴哥窟的墙洞里到底说了什么？");
    await p.hold(2600);
    const all = (await api(p, "/api/entries")).entries;
    must(all.some((x) => x.title === "盗梦空间" && x.rating === 4), "盗梦空间 added by the agent with 4 stars");
    must(await p.ev(`!!document.querySelector("#messages .sources a")`), "web answer shows source links");
  },
  async wishlist(p) {
    await p.cap("看到想看的片，先放进「想看」");
    await p.type(sel("#search-input"), "沙丘");
    await p.send("Input.dispatchKeyEvent", { type: "keyDown", key: "Enter", code: "Enter", windowsVirtualKeyCode: 13, text: "\r" });
    await p.send("Input.dispatchKeyEvent", { type: "keyUp", key: "Enter", code: "Enter", windowsVirtualKeyCode: 13 });
    await waitFor(() => p.ev(`!!document.querySelector("#search-results .result")`), "search results");
    await p.hold(700);
    await p.click(btn("#search-results .result-acts", "想看"), 1800);
    await p.cap("想看的片不进日历，也不影响配色");
    await p.click(sel("#wish button:last-child"), 1500);
    await p.cap("看完了：一键转成已看，直接补评分和感受");
    await p.click(btn("#hero", "看完了 →"), 900);
    await ready(p, "#hero .edit textarea", "edit form after marking watched");
    await p.hold(500);
    await p.click(sel("#hero .rate button:nth-child(4)"), 900);
    await p.click(btn("#hero", "编辑"), 600);
    await ready(p, "#hero .edit textarea", "edit form");
    await p.ev(`document.querySelector("#hero .edit textarea").value = ""`);
    await p.type(sel("#hero .edit textarea"), "终于看了，银幕比想象中大。", 60);
    await p.click(btn("#hero .edit", "保存"), 1800);
    const e = (await api(p, "/api/entries")).entries.find((x) => x.title === "爱乐之城");
    must(e && e.status === "watched" && e.rating === 4 && e.note.includes("银幕"), "爱乐之城 moved to watched with 4 stars and note");
  },
  async "quote-collage"(p) {
    await p.cap("选一部电影，点「台词海报」");
    await p.click(day(8), 1000);
    await waitFor(() => p.ev(`document.querySelector("#hero .title")?.textContent.includes("死亡诗社")`), "死亡诗社 in hero");
    await p.click(btn("#hero", "台词海报"), 900);
    await p.cap("从 Wikiquote 查台词，可以多选（最多 4 句）");
    await p.click(sel("#quote-lookup"), 1200);
    await waitFor(() => p.ev(`document.querySelectorAll("#quote-cands .cand").length >= 3`), "quote candidates");
    await p.hold(700);
    for (const i of [1, 2, 3]) await p.click(`document.querySelectorAll("#quote-cands .cand input")[${i - 1}]`, 600);
    await p.hold(700);
    await p.cap("用这部片的 TMDB 剧照拼成海报，几秒出图");
    await p.click(sel("#poster-submit"), 600);
    await waitFor(() => p.ev(`document.getElementById("image-dialog").open`), "collage dialog", 40000);
    await p.hold(3200);
    const dims = await p.ev(`(() => { const i = document.getElementById("image-full"); return [i.naturalWidth, i.naturalHeight]; })()`);
    must(dims[0] === 1024 && dims[1] === 1536, `collage is 1024x1536, got ${dims}`);
    const d = await api(p, "/api/entries/1");
    must(d.posters.length === 1 && d.posters[0].quote.split("\n").length === 3, "one collage poster with 3 quotes saved");
  },
  async poem(p) {
    await p.cap("一键成诗：选一位诗人的文风");
    await p.click(day(6), 900);
    await p.click(btn("#hero", "一键成诗"), 600);
    await ready(p, '#poem-poets button[data-k="dai_wangshu"]', "poet buttons");
    await p.hold(700);
    await p.click(sel('#poem-poets button[data-k="dai_wangshu"]'), 400);
    await p.idle(30000); await p.hold(1200);
    await p.cap("保存下来，之后在「收藏」里翻看");
    await p.click(sel("#poem-save"), 900);
    await p.cap("不喜欢就换一位，中外诗人共 22 位");
    await p.click(sel('#poem-poets button[data-k="szymborska"]'), 400);
    await p.hold(300);
    await p.idle(30000); await p.hold(1400);
    await p.click(sel("#poem-close"), 700);
    await p.cap("收藏页：诗、台词、截图放在一起");
    await p.click(tab("clips"), 2400);
    const poems = (await api(p, "/api/poems")).poems;
    must(poems.length === 1 && poems[0].style_name === "戴望舒", "exactly one saved poem, by 戴望舒");
    must(await p.ev(`document.getElementById("clipwall").textContent.includes("旗袍与雨")`), "clips page shows the poem");
  },
};

async function runScene(name, port) {
  const data = fs.mkdtempSync(path.join(os.tmpdir(), "md-demo-"));
  const srv = spawn("python3", [path.join(HERE, "demo_server.py"), "--port", String(port), "--data", data], { cwd: ROOT, stdio: "inherit" });
  const prof = fs.mkdtempSync(path.join(os.tmpdir(), "md-prof-"));
  const br = spawn(BROWSER, ["--headless=new", "--disable-gpu", `--remote-debugging-port=${port + 100}`, `--window-size=${W},${H}`,
    "--hide-scrollbars", "--force-device-scale-factor=1", `--user-data-dir=${prof}`, "about:blank"], { stdio: "ignore" });
  const frames = fs.mkdtempSync(path.join(os.tmpdir(), "md-frames-"));
  try {
    await waitFor(async () => (await fetch(`http://127.0.0.1:${port}/api/status`)).ok, "demo server");
    const tabs = await waitFor(async () => { const t = await (await fetch(`http://127.0.0.1:${port + 100}/json`)).json(); return t.find((x) => x.type === "page"); }, "browser");
    const ws = new WebSocket(tabs.webSocketDebuggerUrl); await new Promise((r) => (ws.onopen = r));
    const p = new Page(ws);
    await p.send("Runtime.enable"); await p.send("Page.enable");
    await p.send("Emulation.setDeviceMetricsOverride", { width: W, height: H, deviceScaleFactor: 1, mobile: false });
    await p.send("Page.navigate", { url: `http://127.0.0.1:${port}/` });
    await waitFor(() => p.ev(`document.querySelectorAll("#grid .day.has").length > 0`), "calendar");
    await waitFor(() => p.ev(`(() => { const im = [...document.querySelectorAll("#grid .orb img, #hero img")]; return im.length > 0 && im.every((i) => i.complete && i.naturalWidth > 0); })()`),
      "cover images (is image.tmdb.org reachable?)", 30000);
    await sleep(1200);
    await p.setup();
    await p.startRecording(frames);
    try { await SCENES[name](p); }
    catch (err) {
      const shot = await p.send("Page.captureScreenshot", { format: "png" });
      const out = path.join(os.tmpdir(), `md-fail-${name}.png`);
      fs.writeFileSync(out, Buffer.from(shot.result.data, "base64"));
      const state = await p.ev(`JSON.stringify({ dialogs: [...document.querySelectorAll("dialog")].filter((d) => d.open).map((d) => d.id), hero: document.querySelector("#hero .title")?.textContent, err: document.querySelector(".error:not([hidden])")?.textContent })`);
      console.log(`  failure state: ${state}\n  screenshot: ${out}`);
      throw err;
    }
    await p.hold(600);
    await p.save(name);
    if (p.errors?.length) console.log("  page errors:", p.errors);
    ws.close();
  } finally {
    const exited = (c) => new Promise((r) => { if (c.exitCode !== null) r(); else c.once("exit", r); });
    br.kill(); srv.kill();
    await Promise.race([Promise.all([exited(br), exited(srv)]), sleep(5000)]);
    for (const dir of [data, prof, frames]) {
      try { fs.rmSync(dir, { recursive: true, force: true, maxRetries: 8, retryDelay: 300 }); }
      catch (e) { console.warn(`  could not remove ${dir}: ${e.code}`); }
    }
  }
}

const wanted = process.argv.slice(2);
const names = wanted.length ? wanted : Object.keys(SCENES);
let port = 8140;
for (const n of names) {
  if (!SCENES[n]) { console.error(`unknown scene ${n}; choose from ${Object.keys(SCENES).join(", ")}`); process.exit(2); }
  console.log(`recording ${n}`);
  try { await runScene(n, port++); } catch (e) { console.error(`  FAILED ${n}: ${e.message}`); process.exitCode = 1; }
}
process.exit(process.exitCode || 0);
