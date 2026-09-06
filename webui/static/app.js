/* ============================================================
   wm2 水印工作台 前端(无框架 SPA)
   ============================================================ */
"use strict";

const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

async function api(path, opts = {}) {
  const r = await fetch(path, opts);
  let body = null;
  try { body = await r.json(); } catch { /* empty */ }
  if (!r.ok) throw new Error((body && (body.detail || body.message)) || `HTTP ${r.status}`);
  return body;
}
const post = (p, b) => api(p, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(b) });

/* ---------- 格式化 ---------- */
const fmtSize = mb => mb >= 1024 ? (mb / 1024).toFixed(2) + " GB" : (+mb).toFixed(1) + " MB";
const fmtDur = s => { s = +s || 0; const m = Math.floor(s / 60), sec = Math.round(s % 60); return m ? `${m}分${sec}秒` : `${sec}秒`; };
const fmtEta = s => s == null ? "—" : (s >= 60 ? `${Math.floor(s / 60)}分${Math.round(s % 60)}秒` : `${s}秒`);
const fmtClock = s => { s = +s || 0; const h = Math.floor(s / 3600), m = Math.floor(s % 3600 / 60), x = s % 60; const p = n => String(Math.floor(n)).padStart(2, "0"); return (h ? `${p(h)}:` : "") + `${p(m)}:${p(x.toFixed(1).padStart(4, "0"))}`; };
const parseClock = str => { str = String(str).trim(); if (!str) return NaN; if (!str.includes(":")) return parseFloat(str); const parts = str.split(":").map(parseFloat); if (parts.some(isNaN)) return NaN; return parts.length === 3 ? parts[0] * 3600 + parts[1] * 60 + parts[2] : parts[0] * 60 + parts[1]; };
const parseClockMs = str => { const t = parseClock(str); return t ? t.toFixed(1) : String(str).trim(); };
const fmtTime = iso => (iso || "").replace("T", " ").slice(5, 16);
const ST = { queued: "排队中", running: "运行中", done: "完成", failed: "失败", canceled: "已取消", interrupted: "已中断", hit: "命中", miss: "未检出", unknown: "未知 ID" };
const KIND = { embed_video: "打水印·视频", embed_images: "打水印·图片", extract_image: "提取·图片", extract_video: "提取·时间点", extract_scan: "提取·全片扫描" };

/* ---------- Toast / 弹层 ---------- */
function toast(msg, type = "") {
  const d = document.createElement("div");
  d.className = "toast " + type; d.textContent = msg;
  $("#toasts").appendChild(d);
  setTimeout(() => { d.style.opacity = "0"; d.style.transition = "opacity .3s"; setTimeout(() => d.remove(), 320); }, 3400);
}
function openModal({ title, body, footer, lg, onClose }) {
  const root = $("#modal-root");
  const ov = document.createElement("div");
  ov.className = "overlay";
  ov.innerHTML = `<div class="modal ${lg ? "lg" : ""}">
      <header><span>◈ ${esc(title)}</span><button class="ibtn x" title="关闭">✕</button></header>
      <div class="body"></div>${footer ? "<footer></footer>" : ""}</div>`;
  const close = () => { ov.remove(); onClose && onClose(); };
  ov.addEventListener("mousedown", e => { if (e.target === ov) close(); });
  $(".x", ov).onclick = close;
  const bodyEl = $(".body", ov); typeof body === "string" ? bodyEl.innerHTML = body : bodyEl.appendChild(body);
  if (footer) { const f = $(".modal footer", ov); if (Array.isArray(footer)) footer.forEach(b => f.appendChild(b)); }
  ov.close = close;
  root.appendChild(ov);
  return ov;
}
function btn(label, cls = "", fn) {
  const b = document.createElement("button");
  b.className = "btn " + cls; b.textContent = label; b.onclick = fn; return b;
}
function confirmBox({ title, html, confirmLabel = "确认", danger = false, needCheck = null }, onOk) {
  const body = document.createElement("div");
  body.innerHTML = html + (needCheck ? `<label class="row" style="margin-top:12px;cursor:pointer"><input type="checkbox" id="ck-ack"><span>${esc(needCheck)}</span></label>` : "");
  const ov = openModal({ title, body, footer: true });
  const fEl = $(".modal footer", ov);
  const ok = btn(confirmLabel, danger ? "danger" : "pri", async () => {
    if (needCheck && !$("#ck-ack", body).checked) return toast("请先勾选确认", "err");
    ok.disabled = true;
    try { await onOk(); ov.close(); } catch (e) { toast(e.message, "err"); ok.disabled = false; }
  });
  fEl.appendChild(btn("取消", "", () => ov.close()));
  fEl.appendChild(ok);
  if (needCheck) ok.disabled = true, $("#ck-ack", body).onchange = e => ok.disabled = !e.target.checked;
  return ov;
}
function drawer(title, bodyEl, opts = {}) {
  const sheet = opts.mode === "sheet";
  const root = $("#modal-root");
  const ov = document.createElement("div");
  ov.className = "overlay" + (sheet ? " top" : "");
  ov.style.background = "rgba(33,26,16,.2)";
  ov.innerHTML = sheet
    ? `<div class="sheet ${opts.wide ? "wide" : ""}" ${opts.width ? `style="width:${opts.width}"` : ""}><header><span>◈ ${esc(title)}</span>
        <button class="ibtn x" style="margin-left:auto">✕</button></header><div class="body"></div></div>`
    : `<div class="drawer ${opts.wide ? "wide" : ""}"><header><span>◈ ${esc(title)}</span>
        <button class="ibtn x" style="margin-left:auto">✕</button></header><div class="body"></div></div>`;
  const close = () => ov.remove();
  ov.addEventListener("mousedown", e => { if (e.target === ov) close(); });
  $(".x", ov).onclick = close;
  $(".body", ov).appendChild(bodyEl);
  root.appendChild(ov);
  return { close, el: ov, box: $(sheet ? ".sheet" : ".drawer", ov) };
}

/* ---------- 通用小件 ---------- */
const chip = st => `<span class="chip ${st}">${ST[st] || st}</span>`;
const idchip = h => `<span class="idchip">${esc(h)}</span>`;
function bar(done, total, idle) {
  const pct = total ? Math.min(100, done / total * 100) : 0;
  return `<div class="bar-line"><div class="bar ${idle ? "idle" : ""}"><i style="width:${pct}%"></i></div>
      <span class="pct">${total ? pct.toFixed(1) + "%" : "—"}</span></div>`;
}
async function copyText(t) {
  try { await navigator.clipboard.writeText(t); toast("已复制到剪贴板", "ok"); }
  catch { const ta = document.createElement("textarea"); ta.value = t; document.body.appendChild(ta); ta.select(); document.execCommand("copy"); ta.remove(); toast("已复制到剪贴板", "ok"); }
}
async function deriveId(text) {
  const buf = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
  return [...new Uint8Array(buf).slice(0, 4)].map(b => b.toString(16).padStart(2, "0")).join("");
}

/* ============================================================
   路由 & 全局轮询
   ============================================================ */
const NAV = [
  ["dash", "总览", '<path d="M3 3h8v8H3zM13 3h8v5h-8zM13 12h8v9h-8zM3 13h8v8H3z" fill="none" stroke="currentColor" stroke-width="2"/>'],
  ["embed", "打水印", '<path d="M12 3l7 7-9 9H5v-5z M14 5l5 5" fill="none" stroke="currentColor" stroke-width="2"/>'],
  ["extract", "查水印", '<circle cx="11" cy="11" r="7" fill="none" stroke="currentColor" stroke-width="2"/><path d="M16.5 16.5L21 21" stroke="currentColor" stroke-width="2.4"/>'],
  ["library", "成品库", '<path d="M4 4h7v7H4zM13 4h7v7h-7zM4 13h7v7H4zM13 13h7v7h-7z" fill="none" stroke="currentColor" stroke-width="2"/>'],
  ["works", "作品码本", '<path d="M5 4h11l3 3v13H5zM8 9h8M8 13h8M8 17h5" fill="none" stroke="currentColor" stroke-width="2"/>'],
];
const PAGES = {};
let curPage = null;

function renderNav() {
  $("#nav").innerHTML = NAV.map(([id, name, svg]) =>
    `<a href="#/${id}" data-id="${id}" class="${curPage === id ? "on" : ""}"><svg viewBox="0 0 24 24">${svg}</svg>${name}</a>`).join("");
}
function route() {
  const id = (location.hash.replace(/^#\//, "") || "dash").split("?")[0];
  curPage = PAGES[id] ? id : "dash";
  renderNav();
  $("#main").innerHTML = "";
  PAGES[curPage]($("#main"));
  window.scrollTo(0, 0);
}
window.addEventListener("hashchange", route);

async function globalTick() {
  try {
    const ov = await api("/api/overview");
    $("#qbadge").textContent = (ov.queue?.running || 0) + (ov.queue?.queued || 0);
    const g = ov.gpu || {};
    $("#foot-gpu").textContent = g.ok ? g.name.replace("NVIDIA GeForce ", "") : "GPU 不可用";
    $("#foot-dot").className = "dot " + (g.ok ? "ok" : "bad");
    if (pageTick) pageTick(ov);
  } catch { /* 后端未就绪 */ }
}
let pageTick = null;
setInterval(globalTick, 1500);

function head(main, title, sub, right = "") {
  main.innerHTML = `<div class="page-head"><h2>${title}</h2><span class="sub">${sub}</span><span class="sp"></span>${right}</div>` + `<div id="page-body"></div>`;
  return $("#page-body");
}

/* ============================================================
   共用:素材选择器(本机路径 / 上传 双通道,R2.1/R3.1)
   ============================================================ */
function sourcePicker({ id, multi = false, accept = "media", onChange }) {
  const st = { tab: "path", sources: [] };
  const box = document.createElement("div");
  const exts = accept === "video" ? "视频(mp4/mov/mkv/avi/flv/webm)" : accept === "image" ? "图片(png/jpg/jpeg)" : "视频或图片";
  const render = () => {
    box.innerHTML = `
      <div class="seg">
        <button data-t="path" class="${st.tab === "path" ? "on" : ""}">本机路径</button>
        <button data-t="upload" class="${st.tab === "upload" ? "on" : ""}">上传文件</button>
      </div>
      <div id="${id}-pane" style="margin-top:12px"></div>
      <div id="${id}-list"></div>`;
    const pane = $(`#${id}-pane`, box);
    if (st.tab === "path") {
      pane.innerHTML = `<div class="row">
          <input style="flex:1;min-width:220px" id="${id}-path" placeholder="F:\\media\\xxx.mp4 或点「浏览」">
          <button class="btn" id="${id}-browse">浏览…</button>
          <button class="btn dark" id="${id}-add">加入</button></div>`;
      $(`#${id}-browse`, pane).onclick = () => browseModal(accept, pick => { $(`#${id}-path`, pane).value = pick; });
      $(`#${id}-add`, pane).onclick = () => addSrc($(`#${id}-path`, pane).value.trim());
      $(`#${id}-path`, pane).addEventListener("keydown", e => { if (e.key === "Enter") addSrc($(`#${id}-path`, pane).value.trim()); });
    } else {
      pane.innerHTML = `
        <div class="srcbox" id="${id}-drop"><b>点击选择或拖入文件</b><p>${exts} · 大文件流式写入 data\\,支持 ≥2GB</p></div>
        <input type="file" id="${id}-file" multiple ${accept !== "image" ? "" : 'accept=".png,.jpg,.jpeg"'} style="display:none">
        <div class="bar idle" id="${id}-upbar" style="display:none;margin-top:10px"><i style="width:0%"></i></div>
        <div class="small muted" id="${id}-uptext" style="margin-top:4px"></div>`;
      const drop = $(`#${id}-drop`, pane), fi = $(`#${id}-file`, pane);
      drop.onclick = () => fi.click();
      fi.onchange = () => uploadFiles([...fi.files]);
      drop.ondragover = e => { e.preventDefault(); drop.classList.add("over"); };
      drop.ondragleave = () => drop.classList.remove("over");
      drop.ondrop = e => { e.preventDefault(); drop.classList.remove("over"); uploadFiles([...e.dataTransfer.files]); };
    }
    renderList();
  };
  const renderList = () => {
    $(`#${id}-list`, box).innerHTML = st.sources.map((s, i) => `
      <div class="srcitem"><span class="nm">${esc(s.name)}</span>
        <span class="meta">${s.info ? esc(s.info) : fmtSize(s.size_mb || 0)}</span>
        <button class="ibtn red" data-i="${i}" title="移除">✕</button></div>`).join("");
    $$(`#${id}-list .ibtn`, box).forEach(b => b.onclick = () => { st.sources.splice(+b.dataset.i, 1); renderList(); onChange(st.sources); });
  };
  const addSrc = async p => {
    if (!p) return toast("请填写或选择路径", "err");
    try {
      const info = await api("/api/probe?path=" + encodeURIComponent(p));
      if (!multi && st.sources.length) st.sources = [];
      if (!multi && st.sources.length >= 1) return;
      if (st.sources.some(s => s.path === info.path)) return toast("该文件已在列表", "err");
      st.sources.push({ path: info.path, name: info.name, size_mb: info.size_mb, probe: info });
      onChange(st.sources); renderList();
    } catch (e) { toast(e.message, "err"); }
  };
  const uploadFiles = files => {
    if (!files.length) return;
    let i = 0;
    const next = () => {
      if (i >= files.length) { $(`#${id}-upbar`, pane2()).style.display = "none"; return; }
      const f = files[i++];
      const xhr = new XMLHttpRequest();
      const barEl = $(`#${id}-upbar`, pane2()), txt = $(`#${id}-uptext`, pane2());
      barEl.style.display = ""; txt.textContent = `上传中 ${f.name}`;
      xhr.upload.onprogress = e => { if (e.lengthComputable) { $("i", barEl).style.width = (e.loaded / e.total * 100) + "%"; txt.textContent = `上传中 ${f.name} · ${(e.loaded / 2 ** 20).toFixed(0)}/${(e.total / 2 ** 20).toFixed(0)} MB`; } };
      xhr.onload = () => {
        if (xhr.status !== 200) { toast(`上传失败: ${xhr.responseText || xhr.status}`, "err"); return next(); }
        const r = JSON.parse(xhr.responseText);
        if (!multi && st.sources.length) st.sources = [];
        const src = { path: r.path, name: r.name, size_mb: r.size_mb };
        st.sources.push(src);
        // 上传后补 probe:查水印页的时间条/时长渲染依赖 s.probe,旧实现上传视频无时间选择(只能 t=0 提交)
        api("/api/probe?path=" + encodeURIComponent(r.path))
          .then(p => { src.probe = p; onChange(st.sources); renderList(); next(); })
          .catch(() => { onChange(st.sources); renderList(); next(); });
      };
      xhr.onerror = () => { toast(`上传失败: ${f.name}`, "err"); next(); };
      xhr.open("POST", `/api/upload?name=${encodeURIComponent(f.name)}`);
      xhr.send(f);
    };
    const pane2 = () => $(`#${id}-pane`, box);
    next();
  };
  box.$add = addSrc;
  box.addEventListener("click", e => {
    const t = e.target.closest(".seg button");
    if (t) { st.tab = t.dataset.t; render(); }
  });
  render();
  return box;
}

/* 目录浏览弹窗 */
function browseModal(accept, onPick) {
  let cur = null;
  const body = document.createElement("div");
  const ov = openModal({ title: "选择文件", body, lg: true });
  const nav = async p => {
    body.innerHTML = `<div class="spin-center"><span class="spin"></span>读取目录…</div>`;
    try {
      const d = await api("/api/browse" + (p ? "?path=" + encodeURIComponent(p) : ""));
      cur = d;
      const okExt = f => accept === "video" ? /\.(mp4|mov|mkv|avi|flv|webm)$/i.test(f) :
        accept === "image" ? /\.(png|jpe?g)$/i :
        /\.(mp4|mov|mkv|avi|flv|webm|png|jpe?g)$/i;
      body.innerHTML = `
        <div class="row" style="margin-bottom:8px">
          <button class="btn sm" id="bv-up" ${d.parent ? "" : "disabled"}>↑ 上级</button>
          <span class="crumb mono">${esc(d.path || "计算机 · 选择磁盘")}</span></div>
        <div class="browse-list">
          ${d.drives ? d.drives.map(x => `<div class="bi dir" data-p="${esc(x.path)}">磁盘 ${esc(x.name)}</div>`).join("") : ""}
          ${(d.dirs || []).map(x => `<div class="bi dir" data-p="${esc(x.path)}">📁 ${esc(x.name)}</div>`).join("")}
          ${(d.files || []).filter(f => okExt(f.name)).map(x => `<div class="bi" data-pick="${esc(x.path)}">🎞 ${esc(x.name)}<span class="sz">${x.size_mb} MB</span></div>`).join("") || (d.drives ? "" : '<div class="bi muted">(无媒体文件)</div>')}
        </div>`;
      $("#bv-up", body).onclick = () => nav(d.parent);
      $$("[data-p]", body).forEach(b => b.onclick = () => nav(b.dataset.p));
      $$("[data-pick]", body).forEach(b => b.onclick = () => { ov.close(); onPick(b.dataset.pick); });
    } catch (e) { body.innerHTML = `<div class="warn-box red">${esc(e.message)}</div>`; }
  };
  nav();
}

/* 任务行内进度卡 */
function jobPanel(j) {
  const p = j.progress || {};
  return `<div class="card tight" data-job="${j.id}">
    <div class="row" style="justify-content:space-between">
      <b>${esc(j.label)}</b><span>${chip(j.status)}</span></div>
    ${bar(p.done || 0, p.total || 0, j.status === "queued")}
    <div class="row small" style="justify-content:space-between;margin-top:5px">
      <span class="muted">${esc(p.phase || "")}${p.total ? ` · ${p.done}/${p.total}${j.kind === "embed_video" ? " 帧" : ""}` : ""}${p.eta_s != null && j.status === "running" ? ` · 剩余约 ${fmtEta(p.eta_s)}` : ""}</span>
      ${["queued", "running"].includes(j.status) ? `<button class="btn sm danger" data-cancel="${j.id}">取消</button>` : ""}
    </div></div>`;
}
function bindJobPanels(root, refresh) {
  $$("[data-cancel]", root).forEach(b => b.onclick = async () => {
    try { await post(`/api/jobs/${b.dataset.cancel}/cancel`); toast("已请求取消", "ok"); refresh(); } catch (e) { toast(e.message, "err"); }
  });
}

/* ============================================================
   页面:总览
   ============================================================ */
PAGES.dash = main => {
  const body = head(main, "总览", "系统自检 · GPU 队列 · 最近动态");
  body.innerHTML = `
    <div class="grid g4" id="ov-cards"></div>
    <div class="grid g2" style="margin-top:18px">
      <div class="card"><h3>GPU 任务队列 <span class="tag">串行执行 · 同时只跑一个</span></h3><div id="ov-queue"></div></div>
      <div class="card"><h3>最近任务</h3><div id="ov-recent"></div></div>
    </div>`;
  const refresh = async () => {
    try {
      const ov = await api("/api/overview");
      const g = ov.gpu || {};
      const t = ov.torch || {};
      $("#ov-cards").innerHTML = `
        <div class="stat"><div class="k"><span class="dot ${g.ok ? "ok" : "bad"}"></span>GPU</div>
          <div class="v">${esc(g.ok ? g.name : "不可用")}</div>
          <div class="small muted">${g.ok ? `显存 ${g.used_mb}/${g.total_mb} MB` : "nvidia-smi 不可达"}</div></div>
        <div class="stat"><div class="k"><span class="dot ${t.cuda ? "ok" : (t.cuda == null ? "warn" : "bad")}"></span>torch CUDA</div>
          <div class="v">${t.cuda == null ? "检测中…" : t.cuda ? "可用" : "不可用"} <small>${esc(t.name || "")}</small></div></div>
        <div class="stat"><div class="k"><span class="dot ${ov.ffmpeg_ok ? "ok" : "bad"}"></span>FFmpeg</div>
          <div class="v">${ov.ffmpeg_ok ? "可达" : "缺失"}</div></div>
        <div class="stat"><div class="k"><span class="dot ${ov.weights_ok ? "ok" : "bad"}"></span>模型权重</div>
          <div class="v">${ov.weights_ok ? "wam_mit.pth" : "缺失!"}</div></div>
        <div class="stat"><div class="k"><span class="dot ok"></span>码本作品</div>
          <div class="v">${ov.codebook_count} <small>个</small></div></div>
        <div class="stat"><div class="k"><span class="dot ${ov.disk.free_gb < 20 ? "warn" : "ok"}"></span>磁盘水位</div>
          <div class="v">${ov.disk.free_gb} <small>GB 可用 / 共 ${ov.disk.total_gb} GB</small></div></div>
        <div class="stat"><div class="k"><span class="dot ok"></span>输入区 data\\</div><div class="v small">${esc(ov.data_dir)}</div></div>
        <div class="stat"><div class="k"><span class="dot ok"></span>成品区 output\\</div><div class="v small">${esc(ov.output_dir)}</div></div>`;
      const jobs = await api("/api/jobs?limit=60");
      const act = jobs.filter(j => ["running", "queued"].includes(j.status));
      $("#ov-queue").innerHTML = act.length ? act.map(jobPanel).join("") :
        `<div class="empty"><div class="big">队列空闲</div>提交打水印或提取任务后,这里显示实时进度</div>`;
      bindJobPanels($("#ov-queue"), refresh);
      const rec = jobs.slice(0, 8);
      $("#ov-recent").innerHTML = rec.length ? `<table><tbody>` + rec.map(j => `
        <tr><td class="small muted mono">${fmtTime(j.created_at)}</td>
        <td>${esc(j.label)}</td><td>${chip(j.status)}</td>
        <td><button class="btn sm" data-see="${j.id}">详情</button></td></tr>`).join("") + "</tbody></table>" :
        `<div class="empty"><div class="big">还没有任务</div>去 <a href="#/embed">打水印</a> 或 <a href="#/extract">查水印</a></div>`;
      $$("[data-see]", $("#ov-recent")).forEach(b => b.onclick = () => location.hash = "#/library");
    } catch (e) { $("#ov-cards").innerHTML = `<div class="warn-box red">后端连接失败:${esc(e.message)}</div>`; }
  };
  refresh();
  pageTick = () => { };
  const iv = setInterval(refresh, 2500);
  pageTick = () => { };
  window.addEventListener("hashchange", () => clearInterval(iv), { once: true });
};

/* ============================================================
   页面:打水印
   ============================================================ */
PAGES.embed = main => {
  const body = head(main, "打水印", "作品 → 素材 → 参数 → 入队(GPU 串行)");
  body.innerHTML = `
    <div id="em-queue"></div>
    <div class="card"><h3><span class="no">1</span> 选作品 <span class="tag">决定嵌入哪个版权 ID</span></h3>
      <div class="row">
        <select id="em-work" style="flex:1;min-width:260px"></select>
        <button class="btn" id="em-newwork">＋ 新建作品</button>
        <button class="btn sm" id="em-reload">刷新</button></div>
      <div id="em-workinfo" style="margin-top:10px"></div></div>
    <div class="card"><h3><span class="no">2</span> 选素材 <span class="tag">视频逐个排队 · 多图合并为一个批量任务</span></h3>
      <div id="em-src"></div></div>
    <div class="card"><h3><span class="no">3</span> 参数</h3>
      <div class="grid g2">
        <div>
          <div class="field"><label>视频画质 CRF <span class="muted small">(越小越清晰、文件越大;成品默认 14)</span></label>
            <div class="row"><input type="range" id="em-crf" min="8" max="28" step="1" value="14" style="flex:1">
            <b class="mono" id="em-crfv">14</b></div></div>
          <div class="field"><label>嵌入强度 scaling_w <span class="muted small">(越大越鲁棒、画质越低;盲提取,不影响已有成品)</span></label>
            <div class="grid g3" id="em-presets"></div>
            <div class="row" style="margin-top:10px"><input type="range" id="em-sw" min="1" max="4" step="0.1" value="2" style="flex:1">
              <b class="mono" id="em-swv">2.0</b></div>
            <div class="hint" id="em-swhint"></div></div>
          <div class="field"><label>色彩回补 <span class="muted small">(视频专属:自动抵消打水印带来的泛黄/泛紫,2026-09-06 小样实测)</span></label>
            <div class="row"><label style="display:flex;gap:8px;align-items:center;cursor:pointer">
              <input type="checkbox" id="em-comp" checked> 开启(随强度自动配量)</label>
              <b class="mono" id="em-compv">0.50</b></div>
            <div class="hint">打水印前红绿各预减 N 级(等效补蓝;不动蓝色通道,防白底 255 削顶)。图片走无损链路无需回补</div></div>
        </div>
        <div>
          <div class="result-plate" id="em-summary"></div>
          <button class="btn pri" id="em-submit" style="width:100%;justify-content:center;padding:12px;font-size:15px;margin-top:12px">提交任务 → 盖章入队</button>
          <div class="hint" style="margin-top:8px">提交后任务进入 GPU 串行队列;完成后自动抽帧自检,成品写入 output\\</div>
        </div>
      </div></div>`;
  const st = { works: [], workId: null, srcs: [], preset: "std", sw: 2.0, compOn: true };

  const PRESETS = [
    ["low", "隐形优先", "视频 1.5 / 图片 2.0", "画质极致(PSNR ~40-42.5)。8bit 小样实测:直解+crf23 重编码 100%,截图可用(2026-09-06)"],
    ["std", "标准", "视频 2.0 / 图片 2.5", "全部 14 项验收达标的档位;日常出稿选这个"],
    ["rob", "鲁棒优先", "视频 3.0 / 图片 3.0", "组合搬运等严苛场景;代价 PSNR 降至约 35(低于 36 达标线)"],
  ];
  const renderPresets = () => {
    $("#em-presets").innerHTML = PRESETS.map(([k, n, v, d]) =>
      `<div class="preset ${st.preset === k ? "on" : ""}" data-p="${k}"><b>${n}</b><span class="v">${v}</span><p>${d}</p></div>`).join("");
    $$("#em-presets .preset").forEach(p => p.onclick = () => {
      st.preset = p.dataset.p;
      st.sw = { low: 1.5, std: 2.0, rob: 3.0 }[st.preset];
      $("#em-sw").value = st.sw; renderPresets(); syncHint();
    });
  };
  // 色彩回补量随强度线性配量(实测锚点: sw1.5→0.4, sw2.0→0.5; 见 experiments/t3_compensate)
  const compFor = sw => +Math.min(0.8, Math.max(0.3, 0.4 + 0.2 * (sw - 1.5))).toFixed(2);
  const syncHint = () => {
    const img = st.srcs.length && st.srcs.every(s => /\.(png|jpe?g)$/i.test(s.name));
    const delta = (20 * Math.log10(st.sw / 2.0)).toFixed(1);
    $("#em-swv").textContent = st.sw.toFixed(1);
    $("#em-swhint").innerHTML = img
      ? `图片模式建议 ${st.sw < 2 ? 2.0 : st.sw > 2.9 ? 3.0 : 2.5}(预设档已按视频/图片区分)。相对标准档画质变化 ≈ <b class="mono">${delta} dB</b>`
      : `相对标准档(2.0)画质变化 ≈ <b class="mono">${delta} dB</b>(${delta < 0 ? "更低" : "更高"})`;
    $("#em-compv").textContent = st.compOn ? compFor(st.sw).toFixed(2) : "关";
    renderSummary();
  };
  const renderSummary = () => {
    const w = st.works.find(x => x.id_hex === st.workId);
    const nV = st.srcs.filter(s => /\.(mp4|mov|mkv|avi|flv|webm)$/i.test(s.name)).length;
    const nI = st.srcs.length - nV;
    $("#em-summary").innerHTML = `
      <div class="kv">
        <dt>作品</dt><dd>${w ? esc(w.name) : '<span class="muted">未选择</span>'} ${w ? idchip(w.id_hex) : ""}</dd>
        <dt>素材</dt><dd>${nV ? `${nV} 个视频` : ""}${nV && nI ? " + " : ""}${nI ? `${nI} 张图片` : ""}${!st.srcs.length ? '<span class="muted">未选择</span>' : ""}</dd>
        <dt>画质</dt><dd>crf ${$("#em-crf").value}</dd>
        <dt>强度</dt><dd>scaling_w ${st.sw.toFixed(1)}</dd>
        <dt>回补</dt><dd>${nV ? (st.compOn ? `comp ${compFor(st.sw)} (红绿预减)` : "关闭") : (nI ? "图片无需" : `comp ${compFor(st.sw)} (红绿预减)`)}</dd>
      </div>
      ${nV && nI ? '<div class="warn-box">视频与图片请分开提交</div>' : ""}`;
  };
  const loadWorks = async keep => {
    st.works = await api("/api/works");
    $("#em-work").innerHTML = st.works.map(w =>
      `<option value="${w.id_hex}">${esc(w.name)} · ${w.id_hex}</option>`).join("") || "<option value=''>— 还没有作品,先新建 —</option>";
    st.workId = keep && st.works.some(w => w.id_hex === keep) ? keep : (st.works[0] || {}).id_hex || null;
    if (st.workId) $("#em-work").value = st.workId;
    renderWorkInfo();
  };
  const renderWorkInfo = () => {
    const w = st.works.find(x => x.id_hex === st.workId);
    $("#em-workinfo").innerHTML = w ? `<div class="small"><span class="idchip">${w.id_hex}</span>
      <span class="mono muted">已用于 ${w.usage} 个成品</span><div style="margin-top:4px" class="muted">${esc(w.text)}</div></div>` : "";
    renderSummary();
  };
  $("#em-crf").oninput = e => { $("#em-crfv").textContent = e.target.value; renderSummary(); };
  $("#em-sw").oninput = e => { st.sw = +e.target.value; st.preset = "custom"; renderPresets(); syncHint(); };
  $("#em-comp").onchange = e => { st.compOn = e.target.checked; syncHint(); };
  $("#em-work").onchange = e => { st.workId = e.target.value; renderWorkInfo(); };
  $("#em-reload").onclick = () => loadWorks(st.workId).catch(e => toast(e.message, "err"));
  $("#em-newwork").onclick = () => workModal(null, () => loadWorks(null).then(() => {
    const nw = st.works[st.works.length - 1];
    if (nw) { st.workId = nw.id_hex; $("#em-work").value = nw.id_hex; renderWorkInfo(); }
  }));
  const picker = sourcePicker({ id: "em", multi: true, onChange: ss => { st.srcs = ss; syncHint(); } });
  $("#em-src").appendChild(picker);
  $("#em-submit").onclick = async () => {
    if (!st.workId) return toast("请先新建或选择作品", "err");
    if (!st.srcs.length) return toast("请先选择素材", "err");
    const nV = st.srcs.filter(s => /\.(mp4|mov|mkv|avi|flv|webm)$/i.test(s.name)).length;
    const nI = st.srcs.length - nV;
    if (nV && nI) return toast("视频与图片请分开提交", "err");
    $("#em-submit").disabled = true;
    try {
      const made = await post("/api/jobs/embed", { kind: nV ? "video" : "images", sources: st.srcs.map(s => s.path), work_id: st.workId, crf: +$("#em-crf").value, scaling_w: st.sw, comp: nV && st.compOn ? compFor(st.sw) : 0 });
      toast(`已入队 ${made.length} 个任务`, "ok");
      refreshQueue();
      $("#em-queue").scrollIntoView({ behavior: "smooth" });
    } catch (e) { toast(e.message, "err"); }
    $("#em-submit").disabled = false;
  };
  const refreshQueue = async () => {
    const jobs = await api("/api/jobs?limit=60");
    const mine = jobs.filter(j => j.kind.startsWith("embed") && ["running", "queued"].includes(j.status));
    $("#em-queue").innerHTML = mine.length ? mine.map(jobPanel).join("") : "";
    bindJobPanels($("#em-queue"), refreshQueue);
  };
  renderPresets(); syncHint();
  loadWorks().catch(e => toast(e.message, "err"));
  refreshQueue();
  pageTick = () => { };
  const iv = setInterval(refreshQueue, 1500);
  window.addEventListener("hashchange", () => clearInterval(iv), { once: true });
};

/* 作品新建/编辑弹窗 */
function workModal(editWork, onDone) {
  const isEdit = !!editWork;
  const body = document.createElement("div");
  body.innerHTML = `
    <div class="field"><label class="req">作品名称</label><input id="wk-name" placeholder="例:教程视频系列" value="${esc(editWork?.name || "")}"></div>
    <div class="field"><label class="${isEdit ? "" : "req"}">版权文本</label>
      <textarea id="wk-text" rows="4" ${isEdit ? "disabled" : ""} placeholder="例:©Yin(KYin_Code)版权所有|禁止搬运 Bili:12345">${esc(editWork?.text || "")}</textarea>
      ${isEdit ? '<div class="hint">版权文本不可修改——改文本即改 ID,存量成品将无法命中。如需新文本,请新建作品。</div>'
                : '<div class="hint">ID = SHA-256(文本) 前 4 字节,由系统自动派生,不可手填。</div>'}</div>
    ${isEdit ? `<div class="field"><label>备注</label><input id="wk-note" value="${esc(editWork?.note || "")}"></div>` : `
    <div class="result-plate"><div class="kv">
      <dt>派生 ID</dt><dd><span class="idchip" id="wk-id">————</span></dd>
      <dt>冲突检查</dt><dd id="wk-conflict" class="small">—</dd></div></div>`}`;
  const ov = openModal({ title: isEdit ? "编辑作品" : "新建作品", body, footer: true });
  const fEl = $(".modal footer", ov);
  const ok = btn(isEdit ? "保存" : "创建作品", "pri", async () => {
    try {
      if (isEdit) { await api(`/api/works/${editWork.id_hex}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name: $("#wk-name", body).value, note: $("#wk-note", body).value }) }); toast("已保存", "ok"); }
      else {
        await post("/api/works", { name: $("#wk-name", body).value, text: $("#wk-text", body).value });
        toast("作品已创建并登记入码本", "ok");
      }
      ov.close(); onDone && onDone();
    } catch (e) { toast(e.message, "err"); }
  });
  fEl.appendChild(btn("取消", "", () => ov.close()));
  fEl.appendChild(ok);
  if (!isEdit) {
    const sync = async () => {
      const t = $("#wk-text", body).value;
      if (!t) { $("#wk-id", body).textContent = "————"; $("#wk-conflict", body).textContent = "—"; return; }
      const id = await deriveId(t);
      $("#wk-id", body).textContent = id;
      const dup = App.works.find(w => w.id_hex === id);
      $("#wk-conflict", body).innerHTML = dup ? `<span style="color:var(--red)">✗ 与作品「${esc(dup.name)}」ID 冲突,请修改文本</span>` :
        App.works.some(w => w.text === t) ? '<span style="color:var(--red)">✗ 该文本已存在</span>' :
        '<span style="color:var(--green)">✓ 无冲突</span>';
      ok.disabled = !!dup;
    };
    $("#wk-text", body).addEventListener("input", sync);
    sync();
  }
  if (isEdit) ok.disabled = !editWork?.name;
  $("#wk-name", body).addEventListener("input", e => { if (isEdit) ok.disabled = !e.target.value.trim(); });
}

/* ============================================================
   页面:查水印
   ============================================================ */
PAGES.extract = main => {
  const body = head(main, "查水印", "取证提取 · 策略链 · 结果自动留存");
  body.innerHTML = `
    <div class="tabs">
      <button data-m="image" class="on">图片提取</button>
      <button data-m="video">视频时间点</button>
      <button data-m="scan">全片扫描</button></div>
    <div id="ex-body"></div>
    <div class="card"><h3>提取历史 <span class="tag">最近 8 次 · 全部记录见成品库</span></h3><div id="ex-hist"></div></div>`;
  let mode = "image";
  const st = { srcs: [], t: 0, window: 0.5, interval: 5, probe: null, curJob: null };
  $$(".tabs button", body).forEach(b => b.onclick = () => {
    mode = b.dataset.m;
    $$(".tabs button", body).forEach(x => x.classList.toggle("on", x === b));
    renderMode();
  });

  const renderMode = () => {
    const el = $("#ex-body");
    el.innerHTML = "";
    const card = document.createElement("div");
    card.className = "card";
    card.innerHTML = `<h3>待检素材 <span class="tag">${{ image: "单张图片", video: "视频 + 时间点邻域", scan: "按间隔抽样打点" }[mode]}</span></h3><div id="ex-src"></div><div id="ex-extra"></div>
      <div class="row" style="margin-top:14px"><button class="btn pri" id="ex-go" style="padding:10px 26px">开始提取</button>
      <span class="small muted" id="ex-note">${mode === "image" ? "常规命中 ≤5 秒(引擎常驻)" : mode === "video" ? "60 帧邻域典型 3~30 秒" : "按间隔逐点提取,时长取决于片长与间隔"}</span></div>`;
    el.appendChild(card);
    $("#ex-src").appendChild(sourcePicker({ id: "ex", multi: false, accept: mode === "image" ? "image" : "video", onChange: onSrc }));
    const result = document.createElement("div");
    result.id = "ex-result";
    el.appendChild(result);
    renderExtra();
    $("#ex-go").onclick = submit;
  };

  const onSrc = ss => {
    st.srcs = ss;
    st.probe = ss[0]?.probe || null;
    renderExtra();
    renderResult(null);
  };
  const renderExtra = () => {
    const el = $("#ex-extra"); if (!el) return;
    if (mode === "image") { el.innerHTML = st.probe ? `<div class="preview-strip"><img class="thumb" style="width:150px;height:84px;object-fit:contain" src="/api/raw?path=${encodeURIComponent(st.srcs[0].path)}"><div class="kv"><dt>文件</dt><dd>${esc(st.probe.name)}</dd><dt>尺寸</dt><dd>${st.probe.w}×${st.probe.h}</dd></div></div>` : ""; return; }
    const dur = st.probe?.duration || 0;
    el.innerHTML = `
      <div class="preview-strip">
        <img class="thumb" id="ex-frame" ${st.probe ? `src="/api/frame?path=${encodeURIComponent(st.srcs[0].path)}&t=${st.t}"` : ""}>
        <div style="flex:1;min-width:240px">
          ${st.probe ? `<div class="small muted mono" style="margin-bottom:4px">${esc(st.probe.name)} · ${st.probe.w}×${st.probe.h} · ${fmtDur(st.probe.duration)} · ${fmtSize(st.probe.size_mb)}</div>` : ""}
          <div class="row"><input id="ex-time" class="mono" style="width:110px" placeholder="mm:ss 或秒" value="${fmtClock(st.t)}">
            <input type="range" id="ex-slider" min="0" max="${dur}" step="0.1" value="${st.t}" style="flex:1" ${st.probe ? "" : "disabled"}>
            <span class="mono small muted" id="ex-dur">${st.probe ? "/ " + fmtClock(dur) : ""}</span></div>
          ${mode === "video" ? `<div class="row" style="margin-top:8px"><span class="small">邻域窗口 ±</span>
            <input type="range" id="ex-win" min="0" max="5" step="0.1" value="${st.window}" style="flex:1">
            <b class="mono small" id="ex-winv">${st.window.toFixed(1)}s</b>
            <span class="small muted">约 ${Math.round(st.window * (st.probe?.fps || 60) * 2)} 帧</span></div>` :
      `<div class="row" style="margin-top:8px"><span class="small">抽样间隔</span>
            <input type="number" id="ex-itv" min="1" max="60" value="${st.interval}" style="width:80px"> <span class="small">秒</span>
            <span class="small muted">将抽取约 ${dur ? Math.ceil(dur / st.interval) : "—"} 个点</span></div>`}
        </div></div>`;
    if (!st.probe) return;
    const updFrame = debounce(() => { $("#ex-frame").src = `/api/frame?path=${encodeURIComponent(st.srcs[0].path)}&t=${st.t}`; }, 350);
    const applyTime = v => { st.t = Math.max(0, Math.min(v, dur)); $("#ex-slider").value = st.t; updFrame(); };
    $("#ex-slider").oninput = e => { st.t = +e.target.value; $("#ex-time").value = fmtClock(st.t); updFrame(); };
    $("#ex-time").oninput = e => { const v = parseClock(e.target.value); if (!isNaN(v)) applyTime(v); };
    $("#ex-time").onchange = e => { const v = parseClock(e.target.value); if (isNaN(v)) e.target.value = fmtClock(st.t); else { applyTime(v); e.target.value = fmtClock(st.t); } };
    if (mode === "video") { $("#ex-win").oninput = e => { st.window = +e.target.value; $("#ex-winv").textContent = st.window.toFixed(1) + "s"; renderExtra(); }; }
    else { $("#ex-itv").onchange = e => { st.interval = Math.max(1, +e.target.value || 5); renderExtra(); }; }
  };
  const submit = async () => {
    if (!st.srcs.length) return toast("请先选择待检素材", "err");
    const b = { mode, path: st.srcs[0].path };
    if (mode === "video") { b.t = st.t; b.window = st.window; }
    if (mode === "scan") b.interval = st.interval;
    try {
      const r = await post("/api/jobs/extract", b);
      st.curJob = r.id;
      renderResult({ status: "wait" });
      pollJob();
    } catch (e) { toast(e.message, "err"); }
  };
  let pollIv = null;
  const pollJob = async () => {
    clearInterval(pollIv);
    pollIv = setInterval(async () => {
      if (!st.curJob) return clearInterval(pollIv);
      try {
        const j = await api(`/api/jobs/${st.curJob}`);
        if (j.status === "running" || j.status === "queued") renderProgress(j);
        else { clearInterval(pollIv); renderResult(j); loadHist(); }
      } catch { }
    }, 1000);
  };
  const renderProgress = j => {
    const p = j.progress;
    $("#ex-result").innerHTML = `<div class="card"><h3>提取中…</h3>${bar(p.done, p.total, j.status === "queued")}
      <div class="small muted" style="margin-top:6px">${esc(p.phase || "")}${p.total ? ` · ${p.done}/${p.total}` : ""}</div></div>`;
  };
  const renderResult = j => {
    const el = $("#ex-result"); if (!el) return;
    if (!j) { el.innerHTML = ""; return; }
    if (j.status === "wait") { el.innerHTML = `<div class="card"><div class="spin-center"><span class="spin"></span>等待引擎…</div></div>`; return; }
    if (j.status !== "done") {
      el.innerHTML = `<div class="card"><h3>结果</h3><div class="warn-box red"><b>任务${ST[j.status]}</b>${j.error ? ":" + esc(j.error) : ""}</div>
        ${j.status === "interrupted" || j.status === "failed" ? `<button class="btn sm" onclick="requeueJob('${j.id}',this)">重新排队</button>` : ""}</div>`;
      return;
    }
    const r = j.result || {};
    const stampHtml = r.status === "hit" ? `<span class="stamp anim">命中</span>` :
      r.status === "unknown" ? `<span class="stamp anim gray">未知 ID</span>` : `<span class="stamp gray">未检出</span>`;
    const stratChips = Object.entries(r.strats || {}).map(([s, n]) => `<span class="chip hit">${esc(s)} ×${n}</span>`).join("");
    let detail = "";
    if (r.kind === "image") detail = `<div class="kv"><dt>策略</dt><dd>${esc(r.strategy || "—")}</dd><dt>置信度</dt><dd>${r.conf ?? "—"}</dd></div>`;
    if (r.kind === "video") detail = `<div class="kv"><dt>邻域帧数</dt><dd>${r.frames}</dd><dt>命中帧</dt><dd>${r.hit_frames ?? 0}</dd><dt>时间点</dt><dd>${fmtClock(r.t)} ±${r.window}s</dd></div><div class="stratchips">${stratChips || ""}</div>`;
    if (r.kind === "scan") {
      const dur = r.duration || 1;
      const segs = (r.segments || []).map(s => `<div class="seg-hit" style="left:${s.start / dur * 100}%;width:${Math.max(0.6, (s.end - s.start) / dur * 100)}%" title="${fmtClock(s.start)} ~ ${fmtClock(s.end)}"></div>`).join("");
      const unks = (r.points || []).filter(p => p.status === "unknown").map(p => `<div class="seg-unk" style="left:${p.t / dur * 100}%;width:0.5%"></div>`).join("");
      detail = `<div class="timeline">${segs}${unks}</div><div class="tl-scale"><span>0:00</span><span>${fmtClock(dur)}</span></div>
        <div class="kv" style="margin-top:8px"><dt>抽样点</dt><dd>${r.frames} 个 · 间隔 ${r.interval}s</dd>
        <dt>命中段</dt><dd>${(r.segments || []).map(s => `${fmtClock(s.start)} ~ ${fmtClock(s.end)}`).join("、") || "—"}</dd>
        <dt>命中点</dt><dd>${r.hit_frames ?? 0}/${r.frames}</dd></div>`;
    }
    el.innerHTML = `<div class="card"><h3>取证结果 <span class="tag">${esc(j.label)} · ${(r.ms / 1000).toFixed(1)}s</span></h3>
      <div class="result-plate">
        <div class="row" style="justify-content:space-between">
          <div>${stampHtml}</div>
          <div style="text-align:right">${r.id_hex ? idchip(r.id_hex) : ""}<div class="small muted">${r.name ? esc(r.name) : ""}</div></div></div>
        ${r.status === "hit" ? `<div class="bigtext">${esc(r.text)}</div>` :
        r.status === "unknown" ? `<div class="warn-box">水印已解出,但该 ID 未在码本登记(可能条目已删除)。ID 值:<b class="mono">${esc(r.id_hex)}</b></div>` :
        '<div class="muted small" style="margin-top:6px">画面中未解出本项目水印;若怀疑有水印,可换时间点/加窗重试。</div>'}
        ${detail}
      </div>
      <div class="row" style="margin-top:12px">
        ${r.text ? `<button class="btn" id="ex-copy">复制版权文本</button>` : ""}
        <button class="btn" id="ex-export">导出取证 JSON</button>
        ${r.status !== "hit" && mode === "image" ? "" : ""}</div></div>`;
    if (r.text) $("#ex-copy").onclick = () => copyText(r.text);
    $("#ex-export").onclick = () => location.href = `/api/jobs/${j.id}/export`;
  };
  const loadHist = async () => {
    const jobs = await api("/api/jobs?limit=60");
    const hist = jobs.filter(j => j.kind.startsWith("extract") && j.status === "done").slice(0, 8);
    $("#ex-hist").innerHTML = hist.length ? `<table><thead><tr><th>时间</th><th>任务</th><th>结果</th><th>ID</th><th>耗时</th><th></th></tr></thead><tbody>` +
      hist.map(j => { const r = j.result || {}; return `<tr>
        <td class="small mono muted">${fmtTime(j.finished_at || j.created_at)}</td>
        <td class="ellipsis" title="${esc(j.params?.path || "")}">${esc(j.label)}</td>
        <td>${chip(r.status || j.status)}</td>
        <td>${r.id_hex ? `<span class="mono small">${r.id_hex}</span>` : "—"}</td>
        <td class="small mono">${r.ms ? (r.ms / 1000).toFixed(1) + "s" : "—"}</td>
        <td><button class="btn sm" data-h="${j.id}">详情</button></td></tr>`; }).join("") + "</tbody></table>" :
      '<div class="empty">暂无提取记录</div>';
    $$("[data-h]", $("#ex-hist")).forEach(b => b.onclick = () => extractDetail(b.dataset.h));
  };
  renderMode();
  loadHist();
  pageTick = () => { };
  const iv = setInterval(loadHist, 4000);
  window.addEventListener("hashchange", () => { clearInterval(iv); clearInterval(pollIv); }, { once: true });
};

function debounce(fn, ms) { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; }
async function requeueJob(id, btnEl) {
  try { await post(`/api/jobs/${id}/requeue`); toast("已重新排队", "ok"); btnEl?.remove(); } catch (e) { toast(e.message, "err"); }
}

/* 提取任务详情抽屉 */
function extractDetail(id) {
  api(`/api/jobs/${id}`).then(j => {
    const body = document.createElement("div");
    const r = j.result || {};
    body.innerHTML = `
      <div class="kv" style="margin-bottom:12px">
        <dt>任务</dt><dd>${esc(j.label)}</dd><dt>状态</dt><dd>${chip(j.status)}</dd>
        <dt>创建</dt><dd>${esc(j.created_at)}</dd>
        <dt>参数</dt><dd class="small mono">${esc(JSON.stringify(j.params))}</dd></div>
      ${r.status ? `<div class="result-plate"><div class="kv">
        <dt>结果</dt><dd><b>${{ hit: "命中", unknown: "未知 ID " + (r.id_hex || ""), miss: "未检出" }[r.status]}</b></dd>
        ${r.text ? `<dt>文本</dt><dd>${esc(r.text)}</dd>` : ""}
        ${r.strategy ? `<dt>策略</dt><dd>${esc(r.strategy)}</dd>` : ""}
        ${r.frames ? `<dt>帧</dt><dd>${r.hit_frames ?? ""}/${r.frames}</dd>` : ""}
        <dt>耗时</dt><dd>${r.ms ? (r.ms / 1000).toFixed(1) + "s" : "—"}</dd></div></div>` : ""}
      ${j.error ? `<div class="warn-box red">${esc(j.error)}</div>` : ""}
      <div class="row" style="margin-top:12px">
        ${r.text ? `<button class="btn sm" id="dt-copy">复制文本</button>` : ""}
        <a class="btn sm" href="/api/jobs/${j.id}/export">导出 JSON</a></div>`;
    const d = drawer(j.label, body);
    if (r.text) $("#dt-copy", body).onclick = () => copyText(r.text);
  }).catch(e => toast(e.message, "err"));
}

/* ============================================================
   媒体预览 / 对比(成品库)
   ============================================================ */
function psnrLine(f) {
  const m = f.meta || {};
  const v = m.psnr?.mean ?? m.psnr_mean;
  if (!v) return "";
  const mn = m.psnr?.min ?? m.psnr_min;
  return `PSNR ${v}${mn ? ` (min ${mn})` : ""}<br>`;
}

/* ---- 缩放查看器(滚动式):普通滚轮滚动内容,Ctrl+滚轮缩放,拖动平移 ----
   contain=整图可见;cover=铺满容器宽(高超出部分滚动)。宽度按像素驱动,原生滚动条。 */
function makeZGroup(onChange, natW) {
  const g = {
    panes: [], width: 0, onChange, natW, lockUntil: 0,
    maxScroll(p, axis) {
      return axis === "x" ? Math.max(0, p.wrap.scrollWidth - p.wrap.clientWidth)
                          : Math.max(0, p.wrap.scrollHeight - p.wrap.clientHeight);
    },
    minW() { return this.panes[0] ? this.panes[0].containW() : 60; },
    setAll(px, anchor) {
      px = Math.round(Math.max(px, this.minW()));
      this.lockUntil = performance.now() + 90;
      const multi = this.panes.length > 1;
      // 多屏: 取第一屏的"中心内容点"作为全局锚,所有屏围绕同一点缩放 -> 恒对齐
      const ref = this.panes[0];
      const refOld = Math.max(1, ref.getW());
      const axc = ref.wrap.scrollLeft + ref.wrap.clientWidth / 2;
      const ayc = ref.wrap.scrollTop + ref.wrap.clientHeight / 2;
      for (const p of this.panes) {
        const old = Math.max(1, p.getW());
        const sl = p.wrap.scrollLeft, st = p.wrap.scrollTop;
        p.media.style.width = px + "px";
        if (!multi && anchor && anchor.pane === p && anchor.ax != null) {
          // 单屏(预览): 朝光标缩放
          p.wrap.scrollLeft = Math.max(0, ((sl + anchor.ax) / old) * px - anchor.ax);
          p.wrap.scrollTop = Math.max(0, ((st + anchor.ay) / old) * px - anchor.ay);
        } else {
          // 内容点 (axc, ayc)(按 old 比例)缩放后仍居中
          p.wrap.scrollLeft = Math.max(0, axc * px / refOld - p.wrap.clientWidth / 2);
          p.wrap.scrollTop = Math.max(0, ayc * px / refOld - p.wrap.clientHeight / 2);
        }
      }
      this.width = px;
      this.onChange && this.onChange(px);
    },
    syncScroll(from) {
      if (performance.now() < this.lockUntil) return;
      this.lockUntil = performance.now() + 60;
      const fx = this.maxScroll(from, "x") ? from.wrap.scrollLeft / this.maxScroll(from, "x") : 0;
      const fy = this.maxScroll(from, "y") ? from.wrap.scrollTop / this.maxScroll(from, "y") : 0;
      for (const p of this.panes) if (p !== from) {
        p.wrap.scrollLeft = fx * this.maxScroll(p, "x");
        p.wrap.scrollTop = fy * this.maxScroll(p, "y");
      }
    },
  };
  return g;
}

function zpaneAttach(group, wrap, media, nw, nh, opts = {}) {
  const containW = () => {
    const bw = Math.max(wrap.clientWidth, 320), bh = Math.max(wrap.clientHeight, 240);
    return Math.max(60, Math.floor(Math.min(bw / nw, bh / nh) * nw));
  };
  const coverW = () => {
    const bw = Math.max(wrap.clientWidth, 320), bh = Math.max(wrap.clientHeight, 240);
    return Math.max(containW(), Math.floor(Math.max(bw / nw, bh / nh) * nw));
  };
  const getW = () => Math.round(parseFloat(media.style.width) || containW());
  const pane = { wrap, media, containW, coverW, getW };
  group.panes.push(pane);

  wrap.addEventListener("wheel", e => {
    if (!e.ctrlKey) return;                      // 普通滚轮 = 原生滚动查看
    e.preventDefault();
    const r = wrap.getBoundingClientRect();
    group.setAll(group.width * (e.deltaY < 0 ? 1.18 : 1 / 1.18),
                 { pane, ax: e.clientX - r.left, ay: e.clientY - r.top });
  }, { passive: false });

  wrap.addEventListener("scroll", () => group.syncScroll(pane));

  wrap.addEventListener("dblclick", e => {
    e.preventDefault();
    const r = wrap.getBoundingClientRect();
    const target = getW() > containW() + 2 ? containW() : coverW();
    group.setAll(target, { pane, ax: e.clientX - r.left, ay: e.clientY - r.top });
  });

  if (opts.drag) {                                 // 拖动平移(内容超出容器时)
    wrap.addEventListener("pointerdown", e => {
      if (e.button !== 0) return;
      if (wrap.scrollWidth <= wrap.clientWidth + 1 && wrap.scrollHeight <= wrap.clientHeight + 1) return;
      e.preventDefault();
      wrap.setPointerCapture(e.pointerId);
      const move = ev => { wrap.scrollLeft -= ev.movementX; wrap.scrollTop -= ev.movementY; };
      const up = () => { wrap.removeEventListener("pointermove", move); wrap.removeEventListener("pointerup", up); };
      wrap.addEventListener("pointermove", move);
      wrap.addEventListener("pointerup", up);
    });
  }
  return pane;
}

function previewDrawer(f) {
  const body = document.createElement("div");
  const enc = encodeURIComponent;
  const isImg = f.kind === "image";
  const MEDIA_H = Math.max(380, window.innerHeight - 200);   // 视口几乎全屏高
  const d = drawer(`预览 · ${f.name}`, body, { mode: "sheet", wide: true });
  d.box.style.width = Math.min(1400, window.innerWidth * 0.96) + "px";   // 固定大窗口
  body.innerHTML = `
    <div class="zoomwrap" id="pv-zoom" style="height:${MEDIA_H}px">${isImg
      ? `<img id="pv-media" src="/api/raw?path=${enc(f.path)}" alt="">`
      : `<video id="pv-media" controls preload="metadata" src="/api/raw?path=${enc(f.path)}"></video>`}</div>
    <div class="row small muted" style="margin-top:8px;justify-content:center;gap:10px">
      <button class="btn sm" id="pv-minus" title="缩小">−</button>
      <button class="btn sm" id="pv-plus" title="放大">＋</button>
      <button class="btn sm" id="pv-11" title="原始像素">1:1</button>
      <button class="btn sm" id="pv-cover" title="铺满宽度">铺满</button>
      <button class="btn sm" id="pv-contain" title="整图可见">全图</button>
      <span>普通滚轮滚动查看 · Ctrl+滚轮缩放 · 拖动平移 · 双击 铺满/全图</span>
      <span class="mono" id="pv-zoompct">—</span></div>
    ${isImg ? "" : `<div class="warn-box hidden" style="margin-top:10px">浏览器无法直接播放该格式,请用列表中的「打开」调系统播放器观看。</div>`}`;
  const wrap = $("#pv-zoom", body), media = $("#pv-media", body);
  const wire = (nw, nh) => {
    if (!nw || !nh) return;
    const grp = makeZGroup(px => { $("#pv-zoompct", body).textContent = Math.round(px / nw * 100) + "%"; }, nw);
    zpaneAttach(grp, wrap, media, nw, nh, { drag: isImg });
    grp.setAll(grp.panes[0].containW());   // 预览默认整图可见(先全局再细节)
    $("#pv-minus", body).onclick = () => grp.setAll(grp.width / 1.3);
    $("#pv-plus", body).onclick = () => grp.setAll(grp.width * 1.3);
    $("#pv-11", body).onclick = () => grp.setAll(nw);
    $("#pv-cover", body).onclick = () => grp.setAll(grp.panes[0].coverW());
    $("#pv-contain", body).onclick = () => grp.setAll(grp.panes[0].containW());
  };
  if (isImg) {
    media.addEventListener("load", () => wire(media.naturalWidth, media.naturalHeight));
    if (media.complete && media.naturalWidth) wire(media.naturalWidth, media.naturalHeight);
  } else {
    media.addEventListener("loadedmetadata", () => wire(media.videoWidth, media.videoHeight));
    media.onerror = () => { media.style.display = "none"; $(".warn-box", body).classList.remove("hidden"); };
  }
}

/* ---- 对比屏缩放(transform 式):共享归一化焦点,三屏恒对齐同一内容区域 ----
   状态 = 缩放倍率 s + 焦点 (fx,fy)(各屏中心所显示的媒体内容点,0~1)。
   各屏内容盒尺寸不同,位移按各屏内容盒单独换算 -> 任一屏缩放/平移,三屏看到同一区域。
   普通滚轮滚动页面,Ctrl+滚轮缩放,拖动平移,双击放大/复位。 */
function xformGroup(onChange) {
  const g = {
    s: 1, fx: 0.5, fy: 0.5, panes: [], onChange,
    apply() {
      for (const p of this.panes) {
        const c = p.contentBox();
        const tx = (0.5 - this.fx) * c.cw * this.s, ty = (0.5 - this.fy) * c.ch * this.s;
        p.media.style.transform = `translate(${tx}px, ${ty}px) scale(${this.s})`;
      }
      this.onChange && this.onChange(this.s);
    },
    clampFocus() {                       // 焦点保持在所有屏都不露出媒体边界的范围内(不满足则回中)
      let lx = 0, hx = 1, ly = 0, hy = 1;
      for (const p of this.panes) {
        const c = p.contentBox();
        const mx = p.wrap.clientWidth / (2 * this.s * c.cw), my = p.wrap.clientHeight / (2 * this.s * c.ch);
        lx = Math.max(lx, mx); hx = Math.min(hx, 1 - mx);
        ly = Math.max(ly, my); hy = Math.min(hy, 1 - my);
      }
      this.fx = lx >= hx ? 0.5 : Math.min(hx, Math.max(lx, this.fx));
      this.fy = ly >= hy ? 0.5 : Math.min(hy, Math.max(ly, this.fy));
    },
    zoomAt(factor, pt, pane) {           // pt 为相对屏中心的偏移;给了则把光标处内容点作为三屏新焦点
      const s2 = Math.min(16, Math.max(1, this.s * factor));
      if (s2 === this.s) { if (s2 === 1) this.reset(); return; }
      if (pt && pane) {
        const c = pane.contentBox();
        const uc = this.fx + pt.x / (this.s * c.cw), vc = this.fy + pt.y / (this.s * c.ch);
        this.fx = uc - pt.x / (s2 * c.cw);
        this.fy = vc - pt.y / (s2 * c.ch);
      }
      this.s = s2;
      this.clampFocus(); this.apply();
    },
    zoomTo(target, pt, pane) { this.zoomAt(target / this.s, pt, pane); },
    pan(dx, dy, pane) {                  // 屏内拖动位移(px)-> 焦点平移(三屏同享)
      const c = pane.contentBox();
      this.fx -= dx / (this.s * c.cw);
      this.fy -= dy / (this.s * c.ch);
      this.clampFocus(); this.apply();
    },
    reset() { this.s = 1; this.fx = 0.5; this.fy = 0.5; this.apply(); },
  };
  return g;
}

function xformAttach(grp, wrap, media) {
  const pane = {
    wrap, media,
    contentBox() {                       // contain 适配后画面在本屏的实际显示尺寸
      const w = Math.max(wrap.clientWidth, 1), h = Math.max(wrap.clientHeight, 1);
      const nw = media.videoWidth || media.naturalWidth, nh = media.videoHeight || media.naturalHeight;
      if (!nw || !nh) return { cw: w, ch: h };
      return { cw: Math.min(w, h * nw / nh), ch: Math.min(h, w * nh / nw) };
    },
  };
  grp.panes.push(pane);
  const prevChange = grp.onChange;
  grp.onChange = s => {
    prevChange && prevChange(s);
    wrap.style.cursor = s > 1 ? "grab" : "zoom-in";
  };
  const rel = e => {
    const r = wrap.getBoundingClientRect();
    return { x: e.clientX - r.left - r.width / 2, y: e.clientY - r.top - r.height / 2 };
  };
  wrap.addEventListener("wheel", e => {
    if (!e.ctrlKey) return;                  // 普通滚轮放行
    e.preventDefault();
    grp.zoomAt(e.deltaY < 0 ? 1.18 : 1 / 1.18, rel(e), pane);
  }, { passive: false });
  wrap.addEventListener("dblclick", e => {
    e.preventDefault();
    grp.zoomAt(grp.s > 1 ? 1 / grp.s : 2, rel(e), pane);
  });
  wrap.addEventListener("pointerdown", e => {
    if (grp.s <= 1 || e.button !== 0) return;
    e.preventDefault();
    const move = ev => grp.pan(ev.movementX, ev.movementY, pane);   // 抓手式:内容跟随指针
    const up = () => { window.removeEventListener("pointermove", move); window.removeEventListener("pointerup", up); };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  });
  grp.apply();
  return {
    reset: () => grp.reset(),
    coverFactor: () => {                    // 铺满: 以屏高定标
      const natH = media.videoHeight || media.naturalHeight;
      const natW = media.videoWidth || media.naturalWidth;
      if (!natH || !natW) return 1;
      return Math.max(1, wrap.clientHeight / (wrap.clientWidth * natH / natW));
    },
    scale11: () => {                        // 1:1 原始像素(相对本屏内容宽)
      const natW = media.videoWidth || media.naturalWidth;
      if (!natW) return 1;
      return Math.max(1, natW / Math.max(pane.contentBox().cw, 1));
    },
  };
}

function compareDrawer(f) {
  const src = f.source || (f.meta && f.meta.source) || "";
  const enc = encodeURIComponent;
  const baseName = s => String(s || "").split("\\").pop();
  const body = document.createElement("div");
  const AMP_SEL = `<select id="cmp-amp"><option value="4">×4</option><option value="8" selected>×8</option><option value="16">×16</option></select>`;
  const PANE_H = Math.max(320, window.innerHeight - 310);
  const ZOOM_BTNS = `<button class="btn sm" id="cmp-contain" title="整幅画面适配屏幕">全图</button>
      <button class="btn sm" id="cmp-cover" title="以屏高铺满">铺满</button>
      <button class="btn sm" id="cmp-11" title="原始像素">1:1</button>
      <span class="mono small" id="cmp-zoompct">适应</span>`;

  if (f.kind === "image") {
    body.innerHTML = `
      <div class="cmp-grid two">
        <figure><figcaption>原片 · ${esc(baseName(src))}</figcaption>
          <div class="zoomwrap fill" id="cmp-wa" style="height:${PANE_H}px"><img src="/api/raw?path=${enc(src)}"></div></figure>
        <figure><figcaption>成品 · ${esc(f.name)}</figcaption>
          <div class="zoomwrap fill" id="cmp-wb" style="height:${PANE_H}px"><img src="/api/raw?path=${enc(f.path)}"></div></figure>
      </div>
      <figure class="difffig"><figcaption>差异放大 ${AMP_SEL} · 整图 PSNR <b id="cmp-psnr" class="mono">—</b>
        <span style="flex:1"></span>${ZOOM_BTNS}</figcaption>
        <div class="zoomwrap fill" id="cmp-wd" style="height:${Math.max(240, PANE_H - 80)}px"><img id="cmp-diff" alt=""></div></figure>
      <div class="small muted" style="margin-top:8px">差异图 = 逐像素差值 ×N,接近全黑即一致(隐形水印正常形态)。普通滚轮滚动页面 · Ctrl+滚轮缩放 · 拖动平移 · 双击放大/复位 · 三屏联动。</div>`;
    drawer(`对比 · ${f.name}`, body, { mode: "sheet", wide: true, width: "calc(100vw - 24px)" });
    let amp = 8, tok = 0, durl = null;
    const img = $("#cmp-diff", body), psnrEl = $("#cmp-psnr", body);
    const grp = xformGroup(s => { $("#cmp-zoompct", body).textContent = s > 1 ? Math.round(s * 100) + "%" : "适应"; });
    const za = xformAttach(grp, $("#cmp-wa", body), $("#cmp-wa img", body));
    const zb = xformAttach(grp, $("#cmp-wb", body), $("#cmp-wb img", body));
    const zd = xformAttach(grp, $("#cmp-wd", body), img);
    $("#cmp-contain", body).onclick = () => grp.reset();
    $("#cmp-cover", body).onclick = () => grp.zoomTo(Math.max(za.coverFactor(), zb.coverFactor(), zd.coverFactor()));
    $("#cmp-11", body).onclick = () => grp.zoomTo(Math.max(za.scale11(), zb.scale11(), zd.scale11()));
    // 各屏高度按图片纵横比自适应:画面恰好铺满屏宽(无黑边,整页滚动查看)
    const fitImgPanes = () => {
      const im = $("#cmp-wa img", body);
      if (!im.naturalWidth) return;
      const di = $("#cmp-diff", body);
      const dw = di.complete && di.naturalWidth ? di.naturalWidth : im.naturalWidth;
      const dh = di.complete && di.naturalHeight ? di.naturalHeight : im.naturalHeight;
      for (const [wrap, nw, nh] of [
        [$("#cmp-wa", body), im.naturalWidth, im.naturalHeight],
        [$("#cmp-wb", body), im.naturalWidth, im.naturalHeight],
        [$("#cmp-wd", body), dw, dh],
      ]) wrap.style.height = Math.max(240, Math.min(Math.round(wrap.clientWidth * nh / nw), 1600)) + "px";
      grp.apply();
    };
    $("#cmp-wa img", body).addEventListener("load", fitImgPanes);
    img.addEventListener("load", fitImgPanes);
    fitImgPanes();
    const upd = () => {
      const my = ++tok;
      fetch(`/api/diff?path=${enc(f.path)}&source=${enc(src)}&t=0&amp=${amp}`)
        .then(r => { if (!r.ok) throw new Error("diff"); const p = r.headers.get("X-PSNR"); return r.blob().then(bl => ({ bl, p })); })
        .then(({ bl, p }) => { if (my !== tok) return; psnrEl.textContent = p + " dB"; if (durl) URL.revokeObjectURL(durl); durl = URL.createObjectURL(bl); img.src = durl; })
        .catch(() => { psnrEl.textContent = "—"; });
    };
    $("#cmp-amp", body).onchange = e => { amp = +e.target.value; upd(); };
    upd();
    return;
  }

  // ---- 视频对比:原片为主时钟,成品从机静音,rAF 盯漂移 ----
  body.innerHTML = `
    <div class="cmp-grid two">
      <figure><figcaption>▶ 原片(主时钟,出声)· ${esc(baseName(src))}</figcaption>
        <div class="zoomwrap fill" id="cmp-wa" style="height:${PANE_H}px"><video id="cmp-a" src="/api/raw?path=${enc(src)}" preload="auto" playsinline></video></div></figure>
      <figure><figcaption>成品(跟随同步,静音)· ${esc(f.name)}</figcaption>
        <div class="zoomwrap fill" id="cmp-wb" style="height:${PANE_H}px"><video id="cmp-b" src="/api/raw?path=${enc(f.path)}" preload="auto" muted playsinline></video></div></figure>
    </div>
    <div class="cmp-controls" style="margin:12px 0">
      <button class="btn sm pri" id="cmp-play" style="min-width:86px">▶ 播放</button>
      <input type="range" id="cmp-slider" min="0" max="${(f.duration || 0).toFixed(2)}" step="0.02" value="0" style="flex:1;min-width:180px">
      <span class="mono small" id="cmp-time">00:00.0 / ${fmtClock(f.duration || 0)}</span>
      <button class="btn sm" id="cmp-prevf" title="后退一帧">⟨1帧</button>
      <button class="btn sm" id="cmp-nextf" title="前进一帧">1帧⟩</button>
      <select id="cmp-speed"><option value="1">1×</option><option value="0.5">0.5×</option><option value="0.25">0.25×</option></select>
      ${ZOOM_BTNS}
    </div>
    <figure class="difffig"><figcaption>差异放大(暂停/拖动/步进时刷新)${AMP_SEL} · 本帧 PSNR <b id="cmp-psnr" class="mono">—</b>
      <span style="flex:1"></span><span class="small muted">三屏缩放联动 · Ctrl+滚轮缩放 · 拖动平移 · 双击放大/复位</span></figcaption>
      <div class="zoomwrap fill" id="cmp-wd" style="height:${Math.max(240, PANE_H - 80)}px"><img id="cmp-diff" alt=""></div></figure>
    <div class="warn-box hidden" id="cmp-afall" style="margin-top:10px">原片格式浏览器无法直接播放(常见于 HEVC 未装系统解码扩展)。
      <button class="btn sm" id="cmp-tc">生成浏览器预览版</button> <span class="small" id="cmp-tcst"></span>
      <div class="small muted">纯 CPU 转码一次并缓存(不占用 GPU 队列),完成后自动替换左屏。</div></div>
    <div class="small muted" style="margin-top:8px">进度条只负责取时间:拖动/点击后两路视频同时暂停、并行解码到目标帧,两路都解完才解除播放禁用,是否播放由你手动决定(解码期间播放键禁用);播放中漂移用变速平滑追(0.8×~1.2×),偏差过大才重新跳转。「±1帧」逐帧对齐检查;差异图全黑 = 两帧一致。</div>`;
  drawer(`对比 · ${f.name}`, body, { mode: "sheet", wide: true, width: "calc(100vw - 24px)" });

  const $id = s => $(s, body);
  const a = $id("#cmp-a"), b = $id("#cmp-b"), slider = $id("#cmp-slider"), playBtn = $id("#cmp-play"),
        timeEl = $id("#cmp-time"), diffImg = $id("#cmp-diff"), psnrEl = $id("#cmp-psnr"), ampSel = $id("#cmp-amp");
  let amp = 8, tok = 0, durl = null, ready = 0, sliderDrag = false, scrubTimer = null;
  // 模型:进度条只是时间轴,只负责算时间。所有跳转 = 两个视频并行直跳同一时间(互不等待);
  // 播放中漂移用变速(0.9×/1.1×)平滑追,偏差 >250ms 才双 seek。
  const EPS = 0.02;                            // 小于半帧视为已对齐,不再触发 seek
  const near = (x, y) => Math.abs(x - y) < EPS;
  const updDiff = () => {
    const my = ++tok;
    fetch(`/api/diff?path=${enc(f.path)}&source=${enc(src)}&t=${a.currentTime.toFixed(2)}&amp=${amp}`)
      .then(r => { if (!r.ok) throw new Error("diff"); const p = r.headers.get("X-PSNR"); return r.blob().then(bl => ({ bl, p })); })
      .then(({ bl, p }) => { if (my !== tok) return; psnrEl.textContent = p + " dB"; if (durl) URL.revokeObjectURL(durl); durl = URL.createObjectURL(bl); diffImg.src = durl; })
      .catch(() => { psnrEl.textContent = "—"; });
  };
  const updDiffSoon = debounce(updDiff, 260);
  const syncNow = () => { if (b.readyState > 0 && !b.seeking && !near(b.currentTime, a.currentTime)) b.currentTime = a.currentTime; };
  b.addEventListener("loadedmetadata", () => syncNow());   // 右屏装载完成后再对齐一次(覆盖装载期赋值失效)
  // —— 解码闸门:跳转时两路强制暂停、并行直跳;两路都解出目标帧(seeked + readyState≥2)才解除禁用 ——
  // 极端模式:放行后一律保持暂停,是否播放完全由用户手动决定(等待期间播放键禁用,点击无效)。
  let gate = 0, gateGuard = 0;
  const gateOpen = () => {
    if (gate > 0 || a.seeking || b.seeking || a.readyState < 2 || b.readyState < 2) return;   // 任一路没解完就继续等
    clearTimeout(gateGuard);
    playBtn.disabled = false;
    playBtn.textContent = "▶ 播放";                             // 放行后保持暂停,等用户手动播放
    updDiffSoon();                                              // 放行时差异图对准的就是屏幕画面
  };
  const seekGate = () => { if (gate > 0) gate--; gateOpen(); };
  a.addEventListener("seeked", seekGate);
  b.addEventListener("seeked", seekGate);
  ["loadeddata", "canplay"].forEach(ev => { a.addEventListener(ev, gateOpen); b.addEventListener(ev, gateOpen); });
  // 双视频并行直跳同一时间,互不等待
  const seekBoth = t => {
    const dur = a.duration || f.duration || 0;
    if (dur > 0) t = Math.max(0, Math.min(t, dur));
    const needA = a.readyState > 0 && (a.seeking || !near(a.currentTime, t));
    const needB = b.readyState > 0 && (b.seeking || !near(b.currentTime, t));
    if (!needA && !needB) return;
    a.pause(); b.pause();                          // 闸门:跳转期间禁止播放
    playBtn.disabled = true; playBtn.textContent = "⏳ 解码中…";
    clearTimeout(gateGuard);
    gate = (needA ? 1 : 0) + (needB ? 1 : 0);      // 等两路的 seeked
    gateGuard = setTimeout(() => { gate = 0; gateOpen(); }, 3000);   // 兜底:信号丢失也强制放行
    if (needA) a.currentTime = t;
    if (needB) b.currentTime = t;
    beginSettle();
  };
  a.addEventListener("loadedmetadata", () => { if (++ready === 2) { slider.max = a.duration.toFixed(2); fitPaneHeights(); updDiffSoon(); } });
  b.addEventListener("loadedmetadata", () => { if (++ready === 2) { slider.max = a.duration.toFixed(2); fitPaneHeights(); updDiffSoon(); } });
  // 各屏高度按媒体纵横比自适应:画面恰好铺满屏宽(差异屏占满整窗宽,整页滚动查看,无黑边)
  const fitPaneHeights = () => {
    const nw = a.videoWidth, nh = a.videoHeight;
    if (!nw || !nh) return;
    for (const id of ["cmp-wa", "cmp-wb", "cmp-wd"]) {
      const w = $id("#" + id);
      w.style.height = Math.max(240, Math.min(Math.round(w.clientWidth * nh / nw), 1600)) + "px";
    }
    grp.apply();
  };
  const grp = xformGroup(s => { $id("#cmp-zoompct").textContent = s > 1 ? Math.round(s * 100) + "%" : "适应"; });
  const za = xformAttach(grp, $id("#cmp-wa"), a);
  const zb = xformAttach(grp, $id("#cmp-wb"), b);
  const zd = xformAttach(grp, $id("#cmp-wd"), diffImg);
  $id("#cmp-contain").onclick = () => grp.reset();
  $id("#cmp-cover").onclick = () => grp.zoomTo(Math.max(za.coverFactor(), zb.coverFactor(), zd.coverFactor()));
  $id("#cmp-11").onclick = () => grp.zoomTo(Math.max(za.scale11(), zb.scale11(), zd.scale11()));
  let settleUntil = 0, bPausedSince = 0;
  const beginSettle = () => { settleUntil = performance.now() + 300; };   // 起步瞬间给解码器一点热身期
  a.addEventListener("play", () => { playBtn.textContent = "⏸ 暂停"; });
  a.addEventListener("playing", () => { beginSettle(); if (!a.paused && b.paused && !sliderDrag && !b.seeking) b.play(); });
  a.addEventListener("waiting", () => { if (!a.paused && !b.paused) b.pause(); });   // 左屏供流停顿,右屏同步冻结
  a.addEventListener("pause", () => { b.pause(); b.playbackRate = a.playbackRate; syncNow(); if (gate === 0) playBtn.textContent = "▶ 播放"; updDiffSoon(); });
  a.addEventListener("ratechange", () => { b.playbackRate = a.playbackRate; });
  a.addEventListener("timeupdate", () => {
    if (!sliderDrag) slider.value = a.currentTime;
    timeEl.textContent = `${fmtT(a.currentTime)} / ${fmtT(a.duration || f.duration || 0)}`;
  });
  (function raf() {
    if (!body.isConnected) return;
    // 看门狗:左播右停超过 500ms(任何竞态卡住)强制恢复,兜底防"右屏冻住"
    if (b.paused && !bPausedSince) bPausedSince = performance.now();
    if (!b.paused) bPausedSince = 0;
    if (!a.paused && b.paused && !sliderDrag && !b.seeking &&
        bPausedSince && performance.now() - bPausedSince > 500) {
      bPausedSince = 0;
      b.play();
    }
    // 漂移校正:全程变速平滑追(0.8×/1.2×),只有真失步(>250ms)才双 seek
    if (!a.paused && !a.seeking && !b.seeking && performance.now() > settleUntil) {
      const drift = b.currentTime - a.currentTime;          // 正 = 右屏超前
      const target = Math.abs(drift) > 0.25 ? null
        : drift < -0.033 ? a.playbackRate * 1.2
        : drift > 0.033 ? a.playbackRate * 0.8
        : a.playbackRate;
      if (target == null) {
        if (b.readyState > 0) seekBoth(a.currentTime);      // 真失步:两路并行直跳拉回
      }
      else if (Math.abs(b.playbackRate - target) > 1e-6) b.playbackRate = target;
    }
    requestAnimationFrame(raf);
  })();
  playBtn.onclick = () => {
    if (gate > 0 || playBtn.disabled) return;   // 解码闸门未放行:点击无效
    if (a.paused) {
      if (!b.seeking && !near(b.currentTime, a.currentTime)) b.currentTime = a.currentTime;   // 起播前右屏对齐左屏
      a.play(); b.play();
    } else { a.pause(); }   // a 的 pause 事件会同步暂停右屏
  };
  slider.addEventListener("input", () => {
    if (!sliderDrag) { sliderDrag = true; a.pause(); }   // 拖动开始:两路暂停
    b.pause();
    timeEl.textContent = `${fmtT(+slider.value)} / ${fmtT(a.duration || f.duration || 0)}`;
    clearTimeout(scrubTimer);
    scrubTimer = setTimeout(() => seekBoth(+slider.value), 120);
  });
  slider.addEventListener("change", () => {
    sliderDrag = false;
    clearTimeout(scrubTimer);
    seekBoth(+slider.value);   // 跳转后保持暂停,播放由用户手动触发
  });
  const fps = f.fps || 30;
  const step = dir => {
    a.pause();
    const t = Math.min(Math.max(a.currentTime + dir / fps, 0), a.duration || 0);
    slider.value = t;
    seekBoth(t);
  };
  $id("#cmp-prevf").onclick = () => step(-1);
  $id("#cmp-nextf").onclick = () => step(1);
  $id("#cmp-speed").onchange = e => { a.playbackRate = b.playbackRate = +e.target.value; };
  ampSel.onchange = e => { amp = +e.target.value; updDiffSoon(); };
  a.addEventListener("error", () => {
    const box = $id("#cmp-afall");
    box.classList.remove("hidden");
    $id("#cmp-tc").onclick = async () => {
      $id("#cmp-tcst").textContent = "转码中…(纯 CPU,长片约 1~4 分钟)";
      await fetch(`/api/transcode_preview?path=${enc(src)}&start=1`);
      const iv = setInterval(async () => {
        if (!body.isConnected) { clearInterval(iv); return; }
        try {
          const st = await fetch(`/api/transcode_preview?path=${enc(src)}`).then(r => r.json());
          if (st.status === "ready") { clearInterval(iv); a.src = st.url; a.load(); box.classList.add("hidden"); }
          else if (st.status === "failed") { clearInterval(iv); $id("#cmp-tcst").textContent = "转码失败:" + (st.error || ""); }
        } catch { /* 忽略瞬时错误 */ }
      }, 2500);
    };
  });
}
function mediaThumb(f) {
  const u = f.kind === "image"
    ? "/api/raw?path=" + encodeURIComponent(f.path)
    : "/api/frame?path=" + encodeURIComponent(f.path) + "&t=0.1";
  return `<img class="libthumb" loading="lazy" src="${u}" alt="">`;
}

/* ============================================================
   页面:成品库
   ============================================================ */
PAGES.library = main => {
  const body = head(main, "成品库", "output\\ 成品 · 嵌入与提取历史(R4)");
  body.innerHTML = `
    <div class="tabs"><button data-t="prod" class="on">成品</button><button data-t="em">嵌入历史</button><button data-t="ex">提取历史</button></div>
    <div id="lb-body"></div>`;
  let tab = "prod";
  $$(".tabs button", body).forEach(b => b.onclick = () => {
    tab = b.dataset.t;
    $$(".tabs button", body).forEach(x => x.classList.toggle("on", x === b));
    render();
  });
  const render = async () => {
    const el = $("#lb-body");
    el.innerHTML = `<div class="card"><div class="spin-center"><span class="spin"></span>加载…</div></div>`;
    try {
      if (tab === "prod") {
        const items = await api("/api/products");
        const byPath = {};
        items.forEach(f => byPath[f.path] = f);
        el.innerHTML = `<div class="card">${items.length ? `<table>
          <thead><tr><th>名称</th><th>规格</th><th>大小</th><th>时间</th><th>参数</th><th style="width:270px">操作</th></tr></thead><tbody>
          ${items.map(f => `<tr>
            <td><div class="row" style="flex-wrap:nowrap;gap:9px"><span data-thumb="${esc(f.path)}" title="点击预览">${mediaThumb(f)}</span>
              <span class="ellipsis mono small" style="max-width:330px" title="${esc(f.name)}">${esc(f.name)}</span></div></td>
            <td class="small mono">${f.codec || "?"}${f.h ? ` ${f.w}×${f.h}` : ""}${f.fps ? ` @${f.fps}` : ""}</td>
            <td class="small mono">${fmtSize(f.size_mb)}</td>
            <td class="small mono muted">${f.modified}</td>
            <td class="small muted">${psnrLine(f)}${f.meta?.scaling_w ? `sw ${f.meta.scaling_w} · crf ${f.meta.crf}` : ""}${f.meta?.id_hex ? `<br><span class="mono">${f.meta.id_hex}</span>` : ""}</td>
            <td><div class="row" style="flex-wrap:nowrap">
              <button class="btn sm" data-prev="${esc(f.path)}">预览</button>
              ${f.source_exists ? `<button class="btn sm dark" data-cmp="${esc(f.path)}" title="与原片并排同步对比">对比</button>` : ""}
              <button class="btn sm" data-open="${esc(f.path)}">打开</button>
              <a class="btn sm" href="/api/download?path=${encodeURIComponent(f.path)}">下载</a></div></td>
          </tr>`).join("")}</tbody></table>` : `<div class="empty"><div class="big">还没有成品</div>提交打水印任务后,成品出现在这里</div>`}</div>`;
        const byP = p => byPath[p];
        $$("[data-prev]", el).forEach(b => b.onclick = () => previewDrawer(byP(b.dataset.prev)));
        $$("[data-cmp]", el).forEach(b => b.onclick = () => compareDrawer(byP(b.dataset.cmp)));
        $$("[data-thumb]", el).forEach(s => s.onclick = () => previewDrawer(byP(s.dataset.thumb)));
        $$("[data-open]", el).forEach(b => b.onclick = async () => {
          try { await post("/api/open", { path: b.dataset.open }); } catch (e) { toast(e.message, "err"); }
        });
      } else {
        const jobs = (await api("/api/jobs?limit=200")).filter(j => tab === "em" ? j.kind.startsWith("embed") : j.kind.startsWith("extract"));
        el.innerHTML = `<div class="card">${jobs.length ? `<table>
          <thead><tr><th>时间</th><th>任务</th><th>状态</th><th>进度 / 结果</th><th style="width:190px">操作</th></tr></thead><tbody>
          ${jobs.map(j => `<tr data-jobrow="${j.id}">
            <td class="small mono muted">${fmtTime(j.created_at)}</td>
            <td class="ellipsis" title="${esc(j.label)}">${KIND[j.kind] || j.kind}<div class="sub">${esc(j.label.replace(KIND[j.kind] || "", "").replace("· ", ""))}</div></td>
            <td>${chip(j.status)}${j.status === "interrupted" || j.status === "failed" ? `<div class="sub"><button class="navlink" onclick="requeueJob('${j.id}',this)">重新排队</button></div>` : ""}</td>
            <td class="small">${jobCell(j)}</td>
            <td><div class="row">
              ${["queued", "running"].includes(j.status) ? `<button class="btn sm danger" data-cancel="${j.id}">取消</button>` : ""}
              <button class="btn sm" data-detail="${j.id}">详情</button></div></td></tr>`).join("")}
          </tbody></table>` : '<div class="empty">暂无记录</div>'}</div>`;
        $$("[data-cancel]", el).forEach(b => b.onclick = async () => { try { await post(`/api/jobs/${b.dataset.cancel}/cancel`); toast("已请求取消", "ok"); render(); } catch (e) { toast(e.message, "err"); } });
        $$("[data-detail]", el).forEach(b => b.onclick = () => (tab === "em" ? embedDetail : extractDetail)(b.dataset.detail, render));
      }
    } catch (e) { el.innerHTML = `<div class="warn-box red">${esc(e.message)}</div>`; }
  };
  function jobCell(j) {
    if (j.status === "running" || j.status === "queued") {
      const p = j.progress;
      return `${bar(p.done, p.total, j.status === "queued")}<div class="sub">${esc(p.phase || "")} ${p.total ? `${p.done}/${p.total}` : ""}</div>`;
    }
    const r = j.result || {};
    if (j.kind.startsWith("embed")) {
      if (j.status === "done") return `<span class="st-text st-done">成品已落盘</span> ${r.psnr ? `<span class="sub mono">PSNR ${r.psnr.mean ?? r.psnr?.mean ?? "—"}</span>` : ""}${r.selfchecks ? `<div class="sub">${r.selfchecks.every(s => s.hit) ? "自检 ✓ 命中" : "自检 ⚠ 未完全命中"}</div>` : ""}`;
      return j.error ? `<span class="sub" style="color:var(--red)">${esc(j.error.slice(0, 60))}</span>` : "—";
    }
    if (j.status === "done") {
      const map = { hit: "命中", unknown: "未知 ID " + (r.id_hex || ""), miss: "未检出" };
      return `<span class="st-text">${map[r.status] || "—"}</span>${r.text ? `<div class="sub ellipsis" style="max-width:260px">${esc(r.text)}</div>` : ""}`;
    }
    return j.error ? `<span class="sub" style="color:var(--red)">${esc(j.error.slice(0, 60))}</span>` : "—";
  }
  render();
  pageTick = () => { };
  const iv = setInterval(() => { if (tab !== "prod") render(); }, 2500);
  window.addEventListener("hashchange", () => clearInterval(iv), { once: true });
};

/* 嵌入任务详情抽屉(含日志) */
async function embedDetail(id, onRefresh) {
  const body = document.createElement("div");
  const d = drawer("任务详情", body);
  let iv = null;
  const load = async () => {
    const j = await api(`/api/jobs/${id}`);
    const r = j.result || {};
    body.innerHTML = `
      <div class="kv">
        <dt>任务</dt><dd>${esc(j.label)}</dd><dt>状态</dt><dd>${chip(j.status)}</dd>
        <dt>参数</dt><dd class="small mono">crf ${j.params?.crf} · scaling_w ${j.params?.scaling_w}${j.params?.comp ? ` · comp ${j.params.comp}` : ""}</dd>
        <dt>创建 / 开始</dt><dd class="small">${esc(j.created_at || "")} / ${esc(j.started_at || "—")}</dd></div>
      ${j.progress?.total ? `<div style="margin:10px 0">${bar(j.progress.done, j.progress.total, j.status === "queued")}<div class="small muted">${esc(j.progress.phase || "")} ${j.progress.done}/${j.progress.total} 帧</div></div>` : ""}
      ${j.error ? `<div class="warn-box red">${esc(j.error)}</div>` : ""}
      ${j.status === "done" ? `<div class="result-plate"><div class="kv">
          <dt>成品</dt><dd class="small mono">${esc(r.out || "")}</dd>
          <dt>大小</dt><dd>${r.size_mb ? fmtSize(r.size_mb) : (r.items ? r.items.length + " 张" : "—")}</dd>
          <dt>帧数</dt><dd>${r.frames ?? "—"}</dd>
          <dt>PSNR</dt><dd>${r.psnr ? `${r.psnr.mean} (min ${r.psnr.min})` : (r.items && r.items.length ? r.items.map(x => x.psnr).join(" / ") + " dB" : "—")}</dd>
          ${r.selfchecks ? `<dt>自检</dt><dd>${r.selfchecks.map(s => `<span class="chip ${s.hit ? "hit" : "unknown"}">${s.hit ? "命中" : "未命中"} acc=${s.acc}</span>`).join(" ")}</dd>` : ""}
        </div></div>
        <div class="row" style="margin-top:10px"><button class="btn sm" id="ed-open">在播放器/看图器打开</button></div>` : ""}
      ${["interrupted", "failed", "canceled"].includes(j.status) ? `<div class="row" style="margin-top:10px"><button class="btn sm" id="ed-re">重新排队</button></div>` : ""}
      <h3 style="font:700 14px var(--serif);margin:16px 0 6px">任务日志</h3>
      <div class="logbox" id="ed-log">加载中…</div>`;
    $("#ed-log", body).textContent = await api(`/api/jobs/${id}/log`).catch(() => "(无日志)");
    $("#ed-log", body).scrollTop = 1e9;
    const openBtn = $("#ed-open", body);
    if (openBtn) openBtn.onclick = async () => { try { await post("/api/open", { path: r.out }); } catch (e) { toast(e.message, "err"); } };
    const reBtn = $("#ed-re", body);
    if (reBtn) reBtn.onclick = async () => { await requeueJob(id); load(); onRefresh && onRefresh(); };
    if (["running", "queued"].includes(j.status)) {
      if (!iv) iv = setInterval(() => d.el.isConnected && load(), 2500);
    } else if (iv) { clearInterval(iv); iv = null; }
  };
  const mo = new MutationObserver(() => { if (!d.el.isConnected && iv) { clearInterval(iv); mo.disconnect(); } });
  mo.observe(document.body, { childList: true });
  await load();
}

/* ============================================================
   页面:作品码本
   ============================================================ */
PAGES.works = main => {
  const body = head(main, "作品码本", "版权文本 ↔ 水印 ID 登记表(R1)", '<button class="btn pri" id="wk-new">＋ 新建作品</button>');
  body.innerHTML = `<div class="card" id="wk-list"></div>
    <div class="card tight"><div class="small muted">
      派生规则:<span class="mono">ID = SHA-256(版权文本 UTF-8) 前 4 字节(大端),32 bit</span> — 规则不可变,已发布成品的提取依赖它。
      提取端(命令行 tools/extract_wm.py 与本页)共用同一份 <span class="mono">tools/codebook.json</span>。</div></div>`;
  const load = async () => {
    const ws = await api("/api/works");
    App.works = ws;
    $("#wk-list").innerHTML = ws.length ? `<table>
      <thead><tr><th>作品</th><th>ID</th><th>版权文本</th><th>成品</th><th>创建</th><th style="width:120px">操作</th></tr></thead><tbody>
      ${ws.map(w => `<tr>
        <td><b>${esc(w.name)}</b>${w.note ? `<div class="sub">${esc(w.note)}</div>` : ""}</td>
        <td>${idchip(w.id_hex)}</td>
        <td class="ellipsis" style="max-width:360px" title="${esc(w.text)}">${esc(w.text)}</td>
        <td class="mono small">${w.usage}</td>
        <td class="small mono muted">${fmtTime(w.created_at)}</td>
        <td><div class="row"><button class="btn sm" data-edit="${w.id_hex}">编辑</button>
          <button class="ibtn red" data-del="${w.id_hex}" title="删除">🗑</button></div></td></tr>`).join("")}
      </tbody></table>` : `<div class="empty"><div class="big">码本为空</div>新建第一个作品,拿到版权 ID 后即可打水印</div>`;
    $("#wk-new").onclick = () => workModal(null, load);
    $$("[data-edit]", body).forEach(b => b.onclick = () => workModal(ws.find(w => w.id_hex === b.dataset.edit), load));
    $$("[data-del]", body).forEach(b => b.onclick = () => {
      const w = ws.find(x => x.id_hex === b.dataset.del);
      confirmBox({
        title: "删除作品", danger: true, confirmLabel: "删除",
        html: `<p>确定删除作品 <b>「${esc(w.name)}」</b>(<span class="mono">${w.id_hex}</span>)?</p>
           <div class="warn-box red">已用该 ID 打水印的<b>存量成品仍可解出 ID</b>,但删除后新提取将显示<b>「未知 ID」</b>,无法还原版权文本。此操作不可撤销(可手工重建同文本作品恢复)。</div>`,
        needCheck: "我已了解删除后的影响",
      }, async () => { await api(`/api/works/${w.id_hex}`, { method: "DELETE" }); toast("已删除", "ok"); load(); });
    });
  };
  load();
  pageTick = () => { };
};

/* ---------- 启动 ---------- */
const App = { works: [] };
route();
globalTick();
