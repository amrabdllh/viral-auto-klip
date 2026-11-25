/* Viral Auto Klip Studio — inti: ikon, komponen UI, API, job, hasil, shell */
"use strict";

const PAGES = {};
const STATE = { status: null, catalog: null, prefill: {}, pollers: new Set() };

/* ================================================================ ikon (stroke 24px) */
const ICONS = {
  home: "M3 10.5 12 3l9 7.5V20a1 1 0 0 1-1 1h-5v-6H9v6H4a1 1 0 0 1-1-1z",
  folder: "M3 6.5A1.5 1.5 0 0 1 4.5 5H9l2 2.5h8.5A1.5 1.5 0 0 1 21 9v9.5a1.5 1.5 0 0 1-1.5 1.5h-15A1.5 1.5 0 0 1 3 18.5z",
  queue: "M8 6h13M8 12h13M8 18h13M3.5 6h.01M3.5 12h.01M3.5 18h.01",
  sparkles: "M12 3.5l1.7 4.6 4.6 1.7-4.6 1.7L12 16.1l-1.7-4.6-4.6-1.7 4.6-1.7zM19 15.5l.8 2 2 .8-2 .8-.8 2-.8-2-2-.8 2-.8z",
  scissors: "M6 9a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM6 21a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM20 4 8.1 15.9M14.5 14.5 20 20M8.1 8.1 12 12",
  splitcam: "M4 3h16a1 1 0 0 1 1 1v16a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1zM3 10h18M14 6.5h4",
  ball: "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM12 7.5l3.6 2.6-1.4 4.2H9.8l-1.4-4.2zM12 3v4.5M21 10.5l-5.4-.4M17.6 19.2l-3.4-4.9M6.4 19.2l3.4-4.9M3 10.5l5.4-.4",
  layers: "M12 3 2.5 8 12 13l9.5-5zM2.5 12.5 12 17.5l9.5-5M2.5 16.5 12 21.5l9.5-5",
  wand: "M15 4V2M15 16v-2M8 9h2M20 9h2M17.8 11.8 19 13M17.8 6.2 19 5M12.2 6.2 11 5M14 10 3 21",
  book: "M4 19.5A2.5 2.5 0 0 1 6.5 17H20V3H6.5A2.5 2.5 0 0 0 4 5.5zM4 19.5A2.5 2.5 0 0 0 6.5 22H20v-5",
  shapes: "M8.3 10a.7.7 0 0 1-.6-1.1l3.7-6a.7.7 0 0 1 1.2 0l3.7 6a.7.7 0 0 1-.6 1.1zM3 14.5h6.5V21H3zM17.5 21a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7z",
  chat: "M21 15a2 2 0 0 1-2 2H8l-5 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z",
  pen: "M12 20h9M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z",
  mic: "M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3zM19 10v2a7 7 0 0 1-14 0v-2M12 19v3",
  voice: "M9 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8zM2 21a7 7 0 0 1 14 0M18 8a3 3 0 0 1 0 6M21 5a7 7 0 0 1 0 12",
  music: "M9 18V5l12-2v13M9 18a3 3 0 1 1-6 0 3 3 0 0 1 6 0zM21 16a3 3 0 1 1-6 0 3 3 0 0 1 6 0z",
  film: "M4 3h16a1 1 0 0 1 1 1v16a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1zM7 3v18M17 3v18M3 7.5h4M3 12h18M3 16.5h4M17 7.5h4M17 16.5h4",
  merge: "M6 3v12M18 9a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM6 21a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM18 9a9 9 0 0 1-9 9",
  repeat: "M17 2l4 4-4 4M3 11v-1a4 4 0 0 1 4-4h14M7 22l-4-4 4-4M21 13v1a4 4 0 0 1-4 4H3",
  phone: "M8 2h8a2 2 0 0 1 2 2v16a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2zM11 18h2",
  chart: "M3 3v18h18M7 15l4-4 3 3 5-6",
  bulb: "M9 18h6M10 22h4M12 2a7 7 0 0 0-4 12.7V17h8v-2.3A7 7 0 0 0 12 2z",
  heading: "M6 4v16M18 4v16M6 12h12",
  doc: "M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8zM14 2v6h6M8 13h8M8 17h5",
  image: "M5 3h14a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2zM8.5 10a1.5 1.5 0 1 0 0-3 1.5 1.5 0 0 0 0 3zM21 15l-5-5L5 21",
  calendar: "M5 4h14a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2zM16 2v4M8 2v4M3 10h18",
  tag: "M20.6 13.4 13.4 20.6a2 2 0 0 1-2.8 0L3 13V3h10l7.6 7.6a2 2 0 0 1 0 2.8zM7.5 7.5h.01",
  sliders: "M4 21v-7M4 10V3M12 21v-9M12 8V3M20 21v-5M20 12V3M1 14h6M9 8h6M17 16h6",
  download: "M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M7 10l5 5 5-5M12 15V3",
  upload: "M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M17 8l-5-5-5 5M12 3v12",
  copy: "M10 9h9a1 1 0 0 1 1 1v10a1 1 0 0 1-1 1h-9a1 1 0 0 1-1-1V10a1 1 0 0 1 1-1zM5 15H4a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1h10a1 1 0 0 1 1 1v1",
  send: "M22 2 11 13M22 2l-7 20-4-9-9-4z",
  plus: "M12 5v14M5 12h14",
  x: "M18 6 6 18M6 6l12 12",
  check: "M20 6 9 17l-5-5",
  up: "M18 15l-6-6-6 6", down: "M6 9l6 6 6-6", left: "M15 18l-6-6 6-6", right: "M9 18l6-6-6-6",
  refresh: "M21 12a9 9 0 1 1-2.6-6.4L21 8M21 3v5h-5",
  trash: "M3 6h18M8 6V4h8v2M19 6l-1 14a1 1 0 0 1-1 1H7a1 1 0 0 1-1-1L5 6M10 11v6M14 11v6",
  menu: "M3 6h18M3 12h18M3 18h18",
  moon: "M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z",
  zap: "M13 2 3 14h9l-1 8 10-12h-9z",
  link: "M10 13a5 5 0 0 0 7.5.5l3-3a5 5 0 0 0-7-7l-1.7 1.7M14 11a5 5 0 0 0-7.5-.5l-3 3a5 5 0 0 0 7 7l1.7-1.7",
  terminal: "M4 17l6-6-6-6M12 19h8",
  info: "M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20zM12 16v-4M12 8h.01",
  alert: "M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0zM12 9v4M12 17h.01",
  pause: "M6 4h4v16H6zM14 4h4v16h-4z",
  play: "M7 4.5v15l12.5-7.5z",
  dup: "M8 8h12v12H8zM4 16V4h12",
  eye: "M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12zM12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6z",
  clock: "M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20zM12 6v6l4 2",
  live: "M12 13a1 1 0 1 0 0-2 1 1 0 0 0 0 2zM8.5 8.5a5 5 0 0 0 0 7M15.5 8.5a5 5 0 0 1 0 7M5.6 5.6a9 9 0 0 0 0 12.8M18.4 5.6a9 9 0 0 1 0 12.8",
  stop: "M7 7h10v10H7z",
};
function icon(name, cls = "") {
  const fill = name === "play";
  return `<svg class="ic${fill ? " fill" : ""}${cls ? " " + cls : ""}" viewBox="0 0 24 24" aria-hidden="true"><path d="${ICONS[name] || ICONS.info}"/></svg>`;
}

const NAV = [
  { group: "Utama" },
  { id: "home", label: "Beranda", ico: "home" },
  { id: "library", label: "Video Saya", ico: "folder" },
  { id: "jobs", label: "Antrean Job", ico: "queue" },
  { group: "Klip dari video" },
  { id: "analisis", label: "Analisis AI", ico: "sparkles", desc: "Klip viral otomatis" },
  { id: "live", label: "Klip Live", ico: "live", desc: "Klip saat streamer masih live", isNew: true },
  { id: "autopilot", label: "Auto Pilot", ico: "zap", desc: "Channel baru → klip otomatis", isNew: true },
  { id: "manual", label: "Atur Manual", ico: "scissors", desc: "Pilih momen sendiri" },
  { id: "splitcam", label: "Split Cam", ico: "splitcam", desc: "Facecam + gameplay" },
  { id: "bola", label: "Highlight Bola", ico: "ball", desc: "Momen gol otomatis", isNew: true },
  { id: "tempel", label: "Konten Tempel", ico: "layers", desc: "Tempel teks & gambar" },
  { group: "Studio AI" },
  { id: "creator", label: "Content Creator", ico: "wand", desc: "Video faceless dari topik", isNew: true },
  { id: "story", label: "Story Studio", ico: "book", desc: "Cerita bergambar" },
  { id: "motion", label: "Motion Graphic", ico: "shapes", desc: "Video penjelasan animasi" },
  { id: "chat", label: "Chat AI", ico: "chat", desc: "Brainstorm ide" },
  { id: "prompt", label: "Buat Prompt", ico: "pen", desc: "Prompt gambar & video" },
  { group: "Audio" },
  { id: "tts", label: "Teks ke Suara", ico: "mic", desc: "Narasi dari teks" },
  { id: "clone", label: "Clone Suara", ico: "voice", desc: "Suaramu sendiri" },
  { id: "music", label: "Musik & SFX", ico: "music", desc: "Musik latar AI" },
  { group: "Edit cepat" },
  { id: "editor", label: "Video Editor", ico: "film", desc: "Potong, teks, musik" },
  { id: "merge", label: "Gabung Video", ico: "merge", desc: "Gabung + transisi" },
  { id: "loop", label: "Ulang Video", ico: "repeat", desc: "Loop tanpa sambungan" },
  { id: "wa", label: "WA Story HD", ico: "phone", desc: "Status WA tajam" },
  { group: "SEO & riset" },
  { id: "analytics", label: "Analitik Viral", ico: "chart", desc: "Tren & jam upload" },
  { id: "ideas", label: "Saran Viral AI", ico: "bulb", desc: "Ide konten per niche" },
  { id: "title", label: "Optimasi Judul", ico: "heading", desc: "Judul, deskripsi, tag" },
  { id: "transcript", label: "Transkrip AI", ico: "doc", desc: "TXT, SRT, terjemahan" },
  { id: "thumb", label: "Unduh Thumbnail", ico: "image", desc: "Resolusi tertinggi" },
  { group: "Distribusi" },
  { id: "upload", label: "Jadwal Upload", ico: "calendar", desc: "Jadwal posting" },
  { id: "brand", label: "Brand Kit", ico: "tag", desc: "Logo di semua klip" },
  { id: "settings", label: "Pengaturan", ico: "sliders" },
];

/* ================================================================ util */
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fileUrl = (p, dl = false) => `/api/file?path=${encodeURIComponent(p)}${dl ? "&download=1" : ""}`;
const baseName = (p) => String(p || "").split(/[\\/]/).pop();
const fmtDur = (s) => { s = Math.max(0, Math.round(+s || 0)); const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60); return h ? `${h}:${String(m).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}` : `${m}:${String(s % 60).padStart(2, "0")}`; };
const fmtTime = (s) => { s = Math.max(0, +s || 0); const m = Math.floor(s / 60); return `${m}:${(s % 60).toFixed(1).padStart(4, "0")}`; };
const fmtBytes = (b) => { b = +b || 0; const u = ["B", "KB", "MB", "GB", "TB"]; let i = 0; while (b >= 1024 && i < 4) { b /= 1024; i++; } return `${b.toFixed(i ? 1 : 0)} ${u[i]}`; };
const fmtNum = (n) => new Intl.NumberFormat("id-ID", { notation: +n >= 10000 ? "compact" : "standard", maximumFractionDigits: 1 }).format(+n || 0);
const fmtIDR = (n) => "Rp " + new Intl.NumberFormat("id-ID", { notation: +n >= 1e7 ? "compact" : "standard", maximumFractionDigits: 1 }).format(Math.round(+n || 0));
const fmtDate = (ts) => (ts ? new Date(ts * 1000).toLocaleString("id-ID", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }) : "—");
const isVideo = (p) => /\.(mp4|mov|mkv|webm|m4v)$/i.test(p || "");
const isAudio = (p) => /\.(mp3|wav|m4a|aac|ogg|flac)$/i.test(p || "");
const isImage = (p) => /\.(png|jpe?g|webp|gif)$/i.test(p || "");
const store = {
  get(k, d) { try { const v = localStorage.getItem("vak:" + k); return v == null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { localStorage.setItem("vak:" + k, JSON.stringify(v)); } catch { /* storage diblokir */ } },
};

async function api(path, opts = {}) {
  const init = { method: opts.method || (opts.body !== undefined ? "POST" : "GET"), headers: {} };
  if (opts.body !== undefined) { init.headers["Content-Type"] = "application/json"; init.body = JSON.stringify(opts.body); }
  const res = await fetch(path, init);
  const text = await res.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch { data = { detail: text }; }
  if (!res.ok) {
    let msg = data && (data.detail ?? data.message);
    if (typeof msg === "object") msg = msg.message || JSON.stringify(msg);
    const err = new Error(msg || `Server menjawab ${res.status}`);
    err.status = res.status;
    throw err;
  }
  return data;
}

function toast(msg, bad = false, ms = 4200) {
  const el = document.createElement("div");
  el.className = "toast" + (bad ? " bad" : "");
  el.innerHTML = `${icon(bad ? "alert" : "check")}<span>${esc(msg)}</span>`;
  $("#toasts").appendChild(el);
  setTimeout(() => el.remove(), ms);
}
const fail = (e) => toast(e.message || String(e), true, 7000);

function copyText(text, label = "Disalin") {
  const done = () => toast(label);
  const fallback = () => {
    const ta = document.createElement("textarea"); ta.value = text; document.body.appendChild(ta); ta.select();
    try { document.execCommand("copy"); done(); } catch { toast("Gagal menyalin", true); } ta.remove();
  };
  if (navigator.clipboard) navigator.clipboard.writeText(text).then(done, fallback); else fallback();
}

async function busy(btn, fn) {
  if (!btn) return fn();
  btn.classList.add("busy"); btn.disabled = true;
  try { return await fn(); } finally { btn.classList.remove("busy"); btn.disabled = false; }
}

/* ================================================================ komponen (string HTML) */
const UI = {
  btn(label, { ico, kind = "", size = "", id = "", attrs = "", href = "", title = "" } = {}) {
    const cls = ["btn", kind, size, !label ? "icon" : ""].filter(Boolean).join(" ");
    const trail = ico === "right" && label;
    const inner = `${ico && !trail ? icon(ico) : ""}${label ? `<span>${esc(label)}</span>` : ""}${trail ? icon(ico) : ""}`;
    const t = title || (!label ? "" : "");
    if (href) return `<a class="${cls}" href="${href}" ${id ? `id="${id}"` : ""} ${t ? `title="${esc(t)}" aria-label="${esc(t)}"` : ""} ${attrs}>${inner}</a>`;
    return `<button type="button" class="${cls}" ${id ? `id="${id}"` : ""} ${title ? `title="${esc(title)}" aria-label="${esc(title)}"` : ""} ${attrs}>${inner}</button>`;
  },
  field(label, control, { hint = "", val = "", cls = "" } = {}) {
    return `<label class="field ${cls}"><span class="lbl">${esc(label)}${val}</span>${control}${hint ? `<span class="hint">${hint}</span>` : ""}</label>`;
  },
  text(name, label, { value = "", placeholder = "", hint = "", type = "text", cls = "" } = {}) {
    // API key: "new-password" (bukan "off", yang diabaikan Chrome) agar browser/password manager tidak mengisi
    // password login tersimpan ke kolom key — nilai isian otomatis bisa terbaca kosong oleh JS sehingga form menolak
    return UI.field(label, `<input type="${type}" name="${name}" value="${esc(value)}" placeholder="${esc(placeholder)}" ${type === "password" ? 'autocomplete="new-password" data-lpignore="true" data-1p-ignore spellcheck="false"' : ""}>`, { hint, cls });
  },
  num(name, label, { value = "", min = "", max = "", step = "", hint = "", cls = "" } = {}) {
    // "detik · 0 = otomatis" → satuan di kanan label, sisanya jadi hint
    const [unit, ...rest] = String(hint).split(" · ");
    const isUnit = unit && unit.length <= 8 && !/\s.*\s/.test(unit);
    return UI.field(label, `<input type="number" name="${name}" value="${esc(value)}" ${min !== "" ? `min="${min}"` : ""} ${max !== "" ? `max="${max}"` : ""} ${step ? `step="${step}"` : ""}>`,
      { hint: isUnit ? rest.join(" · ") : hint, cls, val: isUnit ? `<span class="val">${esc(unit)}</span>` : "" });
  },
  area(name, label, { value = "", placeholder = "", rows = 4, hint = "", cls = "" } = {}) {
    return UI.field(label, `<textarea name="${name}" rows="${rows}" placeholder="${esc(placeholder)}">${esc(value)}</textarea>`, { hint, cls });
  },
  options(list, sel) {
    return list.map((o) => { const v = typeof o === "object" ? o.v : o; const l = typeof o === "object" ? o.l : o; return `<option value="${esc(v)}" ${String(v) === String(sel) ? "selected" : ""} ${o.disabled ? "disabled" : ""}>${esc(l)}</option>`; }).join("");
  },
  select(name, label, list, value, { hint = "", cls = "", numeric = false } = {}) {
    return UI.field(label, `<select name="${name}" ${numeric ? "data-num" : ""}>${UI.options(list, value)}</select>`, { hint, cls });
  },
  range(name, label, { value, min, max, step = 1, fmt = "x", hint = "" } = {}) {
    const show = (v) => (fmt === "%" ? Math.round(v * 100) + "%" : fmt === "x" ? (+v).toFixed(2) + "×" : fmt === "s" ? v + " dtk" : v);
    return `<label class="field"><span class="lbl">${esc(label)}<span class="val" data-rv>${show(value)}</span></span>
      <input type="range" name="${name}" min="${min}" max="${max}" step="${step}" value="${value}" data-fmt="${fmt}">${hint ? `<span class="hint">${hint}</span>` : ""}</label>`;
  },
  toggle(name, label, { checked = true, sub = "" } = {}) {
    return `<label class="switch"><input type="checkbox" name="${name}" ${checked ? "checked" : ""}><span>${esc(label)}${sub ? `<span class="sub">${esc(sub)}</span>` : ""}</span></label>`;
  },
  tri(name, label) { return UI.select(name, label, [{ v: "", l: "Ikut preset" }, { v: "1", l: "Aktif" }, { v: "0", l: "Mati" }], "", {}).replace("<select", "<select data-tri"); },
  panel({ title = "", sub = "", head = "", body = "", foot = "", id = "", cls = "", bodyCls = "" } = {}) {
    return `<section class="panel ${cls}" ${id ? `id="${id}"` : ""}>
      ${title || head ? `<div class="panel-head"><div class="ph-title"><h2>${esc(title)}</h2>${sub ? `<p>${sub}</p>` : ""}</div>${head ? `<div class="btn-row">${head}</div>` : ""}</div>` : ""}
      <div class="panel-body ${bodyCls}">${body}</div>${foot ? `<div class="panel-foot">${foot}</div>` : ""}</section>`;
  },
  section(title, body) { return `<div class="section">${title ? `<div class="section-title">${esc(title)}</div>` : ""}${body}</div>`; },
  more(title, body, open = false) { return `<details class="more" ${open ? "open" : ""}><summary>${icon("right")}${esc(title)}</summary><div class="stack lg">${body}</div></details>`; },
  banner(kind, html, ico) { return `<div class="banner ${kind}">${icon(ico || (kind === "info" ? "info" : "alert"))}<div class="b-body">${html}</div></div>`; },
  empty(ico, title, text = "", action = "") { return `<div class="empty"><div class="e-ic">${icon(ico)}</div><b>${esc(title)}</b>${text ? `<p>${text}</p>` : ""}${action}</div>`; },
  pill(text, kind = "") { return `<span class="pill ${kind}">${esc(text)}</span>`; },
  stat(k, v, sub = "") { return `<div class="stat"><div class="s-k">${esc(k)}</div><div class="s-v">${v}</div>${sub ? `<div class="s-sub">${esc(sub)}</div>` : ""}</div>`; },
  steps(items) { return `<ol class="steps-list">${items.map((t) => `<li><span>${t}</span></li>`).join("")}</ol>`; },
  aside(title, items, extra = "") { return UI.panel({ title, body: `${UI.steps(items)}${extra}` }); },
};

function pageHead(title, desc, actions = "") {
  return `<div class="page-head"><div class="ph-text"><h1>${esc(title)}</h1>${desc ? `<p>${desc}</p>` : ""}</div>${actions ? `<div class="btn-row">${actions}</div>` : ""}</div>`;
}

/** Hidupkan elemen dinamis di root: nilai range, seg control. */
function hydrate(root) {
  $$("input[type=range][data-fmt]", root).forEach((r) => {
    const out = r.closest(".field")?.querySelector("[data-rv]");
    const f = r.dataset.fmt;
    const show = (v) => (f === "%" ? Math.round(v * 100) + "%" : f === "x" ? (+v).toFixed(2) + "×" : f === "s" ? v + " dtk" : v);
    r.addEventListener("input", () => out && (out.textContent = show(r.value)));
  });
}

function formValues(root) {
  const out = {};
  $$("[name]", root).forEach((el) => {
    if (el.type === "checkbox") out[el.name] = el.checked;
    else if (el.type === "number" || el.type === "range" || el.dataset.num !== undefined) out[el.name] = el.value === "" ? null : +el.value;
    else if (el.dataset.tri !== undefined) out[el.name] = el.value === "" ? null : el.value === "1";
    else out[el.name] = el.value;
  });
  return out;
}
/* (v7) isi form dari objek (dipakai Resep) — kebalikan formValues */
function fillForm(root, values) {
  $$("[name]", root).forEach((el) => {
    if (!(el.name in values)) return;
    const v = values[el.name];
    if (el.type === "checkbox") el.checked = !!v;
    else if (el.dataset.tri !== undefined) el.value = v == null ? "" : v ? "1" : "0";
    else el.value = v == null ? "" : String(v);
    el.dispatchEvent(new Event("change", { bubbles: true }));
  });
}
const dropEmpty = (p, keys) => { keys.forEach((k) => { if (p[k] === "" || p[k] == null) delete p[k]; }); return p; };

function seg(el, options, value, onChange, { block = false } = {}) {
  el.className = "seg" + (block ? " block" : "");
  el.innerHTML = options.map((o) => `<button type="button" data-v="${esc(o.v)}" class="${o.v === value ? "on" : ""}">${o.ico ? icon(o.ico) : ""}${esc(o.l)}</button>`).join("");
  el.dataset.value = value;
  el.onclick = (e) => {
    const b = e.target.closest("button"); if (!b) return;
    $$("button", el).forEach((x) => x.classList.toggle("on", x === b));
    el.dataset.value = b.dataset.v; onChange && onChange(b.dataset.v);
  };
}

function choiceGrid(el, items, value, onChange) {
  el.className = "choice-grid";
  el.innerHTML = items.map((it) => `<button type="button" class="choice ${it.v === value ? "on" : ""}" data-v="${esc(it.v)}"><b>${esc(it.l)}</b>${it.d ? `<span>${esc(it.d)}</span>` : ""}</button>`).join("");
  el.onclick = (e) => { const b = e.target.closest(".choice"); if (!b) return; $$(".choice", el).forEach((x) => x.classList.toggle("on", x === b)); onChange(b.dataset.v); };
}

/* ================================================================ modal */
function modal({ title, sub = "", body = "", foot = "", lg = false }, onMount) {
  const box = $("#modalBox");
  box.className = "modal" + (lg ? " lg" : "");
  box.innerHTML = `<div class="modal-head"><div><h2>${esc(title)}</h2>${sub ? `<p>${sub}</p>` : ""}</div>${UI.btn("", { ico: "x", kind: "ghost", size: "sm", title: "Tutup", attrs: "data-close" })}</div>
    <div class="modal-body">${body}</div>${foot ? `<div class="modal-foot">${foot}</div>` : ""}`;
  $("#modal").hidden = false;
  hydrate(box);
  if (onMount) onMount(box);
  setTimeout(() => $("input, textarea, select", box)?.focus(), 30);
}
function closeModal() { $("#modal").hidden = true; $("#modalBox").innerHTML = ""; }
function confirmModal(title, text, okLabel, onOk, danger = true) {
  modal({ title, body: `<p class="ink2">${text}</p>`, foot: UI.btn("Batal", { attrs: "data-close" }) + UI.btn(okLabel, { kind: danger ? "primary" : "primary", attrs: "data-ok" }) }, (m) => {
    $("[data-ok]", m).onclick = async (e) => { await busy(e.currentTarget, onOk); closeModal(); };
  });
}
document.addEventListener("click", (e) => { if (e.target.id === "modal" || e.target.closest("[data-close]")) closeModal(); });
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !$("#modal").hidden) closeModal(); });

/* ================================================================ upload */
function uploadFile(file, onProgress) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    const fd = new FormData(); fd.append("file", file);
    xhr.open("POST", "/api/upload");
    xhr.upload.onprogress = (e) => e.lengthComputable && onProgress && onProgress(Math.round((e.loaded / e.total) * 100));
    xhr.onload = () => {
      let data = {}; try { data = JSON.parse(xhr.responseText); } catch { /* ignore */ }
      if (xhr.status >= 200 && xhr.status < 300) resolve(data); else reject(new Error(data.detail || `Upload gagal (${xhr.status})`));
    };
    xhr.onerror = () => reject(new Error("Upload gagal — server tidak terjangkau"));
    xhr.send(fd);
  });
}

/** Dropzone (klik atau seret file). */
function dropzone(el, { accept = "video/*", title = "Upload file", sub = "Seret ke sini atau klik", multiple = false, ico = "upload", onUploaded }) {
  el.innerHTML = `<div class="dropzone" tabindex="0" role="button">${icon(ico)}<div class="dz-text"><b>${esc(title)}</b><span data-st>${esc(sub)}</span></div>
    <input type="file" accept="${esc(accept)}" ${multiple ? "multiple" : ""} hidden></div><div class="progress-line" hidden><i></i></div>`;
  const dz = $(".dropzone", el), input = $("input", el), st = $("[data-st]", el), pl = $(".progress-line", el), bar = $("i", pl);
  async function handle(files) {
    for (const file of files) {
      try {
        pl.hidden = false; st.textContent = `Mengunggah ${file.name}…`;
        const info = await uploadFile(file, (p) => { bar.style.width = p + "%"; st.textContent = `Mengunggah ${file.name} · ${p}%`; });
        st.textContent = sub; pl.hidden = true; bar.style.width = "0";
        onUploaded && onUploaded(info);
      } catch (e) { st.textContent = sub; pl.hidden = true; fail(e); }
    }
  }
  dz.onclick = () => input.click();
  dz.onkeydown = (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); input.click(); } };
  input.onchange = () => { handle(Array.from(input.files)); input.value = ""; };
  dz.ondragover = (e) => { e.preventDefault(); dz.classList.add("over"); };
  dz.ondragleave = () => dz.classList.remove("over");
  dz.ondrop = (e) => { e.preventDefault(); dz.classList.remove("over"); handle(Array.from(e.dataTransfer.files).slice(0, multiple ? 50 : 1)); };
}

/** Tombol upload kecil. */
function uploadButton(el, { accept = "*/*", label = "Upload", multiple = false, onUploaded }) {
  el.innerHTML = `<label class="btn sm">${icon("upload")}<span data-l>${esc(label)}</span><input type="file" accept="${esc(accept)}" ${multiple ? "multiple" : ""} hidden></label>`;
  const input = $("input", el), l = $("[data-l]", el);
  input.onchange = async () => {
    for (const file of input.files) {
      try { l.textContent = "0%"; const info = await uploadFile(file, (p) => (l.textContent = p + "%")); onUploaded && onUploaded(info); }
      catch (e) { fail(e); }
    }
    l.textContent = label; input.value = "";
  };
}

/** Sumber video: link atau upload. value() → {url} | {path} | null */
function sourceInput(el, { prefill = "", placeholder = "Tempel link YouTube, TikTok, Instagram, atau Facebook" } = {}) {
  el.innerHTML = `<div class="source">
    <div class="input-row"><input type="url" data-url placeholder="${esc(placeholder)}" value="${esc(prefill)}" aria-label="Link video"></div>
    <div class="or">atau</div><div data-dz></div>
    <div class="file-chip" data-chip hidden>${icon("film")}<div class="li-main" style="flex:1;min-width:0"><b class="clip1" data-name></b><span class="tiny muted" data-meta></span></div>${UI.btn("", { ico: "x", kind: "ghost", size: "sm", title: "Hapus file", attrs: "data-clear" })}</div></div>`;
  const state = { path: "", info: null };
  const url = $("[data-url]", el), chip = $("[data-chip]", el), dzBox = $("[data-dz]", el);
  const show = (info) => {
    $("[data-name]", el).textContent = info?.name || baseName(state.path);
    $("[data-meta]", el).textContent = [info?.duration ? fmtDur(info.duration) : "", info?.width ? `${info.width}×${info.height}` : "", info?.size ? fmtBytes(info.size) : ""].filter(Boolean).join(" · ");
    chip.hidden = false; dzBox.hidden = true; url.value = ""; url.disabled = true;
  };
  dropzone(dzBox, { accept: "video/*,.mkv", title: "Upload video dari laptop", sub: "Seret file ke sini atau klik · maks 2 GB",
    onUploaded: (info) => { state.path = info.path; state.info = info; show(info); el.dispatchEvent(new CustomEvent("source", { detail: info })); } });
  $("[data-clear]", el).onclick = () => { state.path = ""; state.info = null; chip.hidden = true; dzBox.hidden = false; url.disabled = false; };
  return {
    value() { if (state.path) return { path: state.path }; const u = url.value.trim(); return u ? { url: u } : null; },
    setPath(p, info) { state.path = p; state.info = info || null; show(info || { name: baseName(p) }); },
    get info() { return state.info; },
  };
}
function requireSource(src) {
  const v = src.value();
  if (!v) toast("Masukkan link atau upload video dulu", true);
  return v;
}

/* ================================================================ picker suara & musik */
async function voicePicker(el, { provider, voice } = {}) {
  const caps = STATE.status?.media?.tts || {};
  provider = provider || STATE.status?.media?.defaults?.tts || "edge";
  el.innerHTML = `<div class="fields three">
      ${UI.field("Mesin suara", `<select data-p>${Object.entries(caps).map(([k, v]) => `<option value="${k}" ${k === provider ? "selected" : ""} ${v.ready ? "" : "disabled"}>${esc(v.label)}${v.ready ? "" : " · butuh key"}</option>`).join("")}</select>`)}
      ${UI.field("Suara", `<select data-v><option>Memuat…</option></select>`)}
      ${UI.range("_speed", "Kecepatan", { value: 1, min: 0.7, max: 1.4, step: 0.05, fmt: "x" })}
    </div>
    <div class="row">${UI.btn("Dengarkan contoh", { ico: "play", size: "sm", attrs: "data-try" })}<audio data-a controls hidden style="height:32px;max-width:100%"></audio></div>`;
  hydrate(el);
  const pSel = $("[data-p]", el), vSel = $("[data-v]", el), sp = $("[name=_speed]", el);
  sp.removeAttribute("name");
  async function load() {
    vSel.innerHTML = "<option>Memuat…</option>";
    try {
      const r = await api(`/api/tts/voices?provider=${pSel.value}`);
      const def = voice || STATE.status?.media?.defaults?.voice;
      vSel.innerHTML = r.voices.map((v) => `<option value="${esc(v.id)}" ${v.id === def ? "selected" : ""}>${esc(v.label)}</option>`).join("") || "<option value=''>Default</option>";
    } catch { vSel.innerHTML = "<option value=''>Default</option>"; }
  }
  pSel.onchange = load;
  $("[data-try]", el).onclick = (ev) => busy(ev.currentTarget, async () => {
    try {
      const job = await api("/api/jobs", { body: { kind: "tts", title: "Contoh suara", params: { text: "Halo! Ini contoh suara narator untuk videomu.", provider: pSel.value, voice: vSel.value, speed: +sp.value } } });
      const done = await waitJob(job.id);
      const a = $("[data-a]", el); a.src = fileUrl(done.result.file); a.hidden = false; a.play().catch(() => {});
    } catch (e) { fail(e); }
  });
  await load();
  return { value: () => ({ tts_provider: pSel.value, provider: pSel.value, voice: vSel.value, speed: +sp.value }) };
}

async function musicPicker(el) {
  el.innerHTML = `<div class="input-row"><select data-m aria-label="Musik latar"><option value="">Tanpa musik</option></select><div data-up></div></div>`;
  const sel = $("[data-m]", el);
  const load = async (pick) => {
    const rows = await api("/api/assets?kind=music,upload_audio,sfx").catch(() => []);
    sel.innerHTML = `<option value="">Tanpa musik</option>` + rows.filter((r) => r.exists).map((r) => `<option value="${esc(r.path)}" ${r.path === pick ? "selected" : ""}>${esc(r.name)}</option>`).join("");
  };
  uploadButton($("[data-up]", el), { accept: "audio/*", label: "Upload", onUploaded: (i) => load(i.path) });
  await load();
  return { value: () => sel.value };
}

/* ================================================================ jobs */
async function waitJob(id, onTick) {
  for (;;) {
    const job = await api(`/api/jobs/${id}`);
    onTick && onTick(job);
    if (["done", "error", "cancelled", "interrupted"].includes(job.status)) {
      if (job.status !== "done") throw new Error(job.message || job.status);
      return job;
    }
    await new Promise((r) => setTimeout(r, 1400));
  }
}

const STATUS_LABEL = { queued: "Antre", running: "Berjalan", done: "Selesai", error: "Gagal", cancelled: "Dibatalkan", interrupted: "Terputus" };
const STATUS_KIND = { queued: "", running: "accent run", done: "ok", error: "bad", cancelled: "warn", interrupted: "warn" };
const statusPill = (s) => `<span class="pill ${STATUS_KIND[s] || ""}">${s === "running" ? '<span class="dot"></span>' : ""}${STATUS_LABEL[s] || s}</span>`;

async function runJob(el, kind, params, { title = "", onDone, btn } = {}) {
  if (btn) btn.disabled = true;
  try {
    const job = await api("/api/jobs", { body: { kind, params, title } });
    attachJob(el, job.id, { onDone, btn });
    refreshJobChip();
    el.scrollIntoView({ behavior: "smooth", block: "start" });
    return job;
  } catch (e) { fail(e); if (btn) btn.disabled = false; }
}

function attachJob(el, id, { onDone, btn, inModal = false } = {}) {
  el.hidden = false;
  el.innerHTML = `<div class="stack lg">${UI.panel({ title: "Proses", head: `<span data-s></span>${UI.btn("Log", { ico: "terminal", size: "sm", kind: "ghost", attrs: "data-log" })}${UI.btn("Batalkan", { size: "sm", kind: "danger", attrs: "data-x" })}`,
    body: `<div class="job-card"><div class="job-top"><span class="j-title clip1" data-t>Memulai…</span><span class="j-pct" data-p>0%</span></div>
      <div class="bar" data-bar><i style="width:2%"></i></div><div class="row"><span class="job-msg" data-m></span><span class="spacer"></span><span class="tiny muted num" data-el></span></div>
      <div class="log" data-logbox hidden></div></div>` })}<div data-r></div></div>`;
  const key = Symbol(id);
  STATE.pollers.add(key);
  let showLog = false;
  const t0 = Date.now();
  $("[data-x]", el).onclick = () => api(`/api/jobs/${id}/cancel`, { body: {} }).catch(fail);
  let liveCount = -1;
  $("[data-log]", el).onclick = () => { showLog = !showLog; $("[data-logbox]", el).hidden = !showLog; tick(); };
  async function tick() {
    if (!STATE.pollers.has(key) || !el.isConnected) { STATE.pollers.delete(key); return; }
    let job;
    try { job = await api(`/api/jobs/${id}${showLog ? "?log=1" : ""}`); } catch { setTimeout(tick, 3000); return; }
    const isLive = job.kind === "live_clip";
    $("[data-t]", el).textContent = isLive && job.status === "running" ? (job.title || "Klip Live") + " · " + (job.stage || "") : (job.stage || job.title || job.kind);
    $("[data-s]", el).innerHTML = statusPill(job.status);
    $("[data-p]", el).innerHTML = isLive && job.status === "running" ? '<span class="pill bad live-pill"><span class="dot"></span>LIVE</span>' : job.progress + "%";
    const bar = $("[data-bar]", el);
    bar.className = "bar" + (job.status === "done" ? " done" : ["error", "cancelled", "interrupted"].includes(job.status) ? " err" : "") + (isLive && job.status === "running" ? " indet" : "");
    $("i", bar).style.width = Math.max(2, job.status === "done" ? 100 : job.progress) + "%";
    if (isLive && job.status === "running") {
      if (!$("[data-stop]", el)) {
        $("[data-x]", el).insertAdjacentHTML("beforebegin", UI.btn("Selesai rekam", { ico: "stop", size: "sm", attrs: 'data-stop title="Berhenti merekam, sisa rekaman tetap dijadikan klip"' }));
        $("[data-stop]", el).onclick = (e) => busy(e.currentTarget, async () => { await api(`/api/jobs/${id}/stop-live`, { body: {} }); toast("Rekaman dihentikan — sisa rekaman sedang diproses"); });
      }
      const n = job.result?.clips?.length || 0;
      if (n !== liveCount) {
        liveCount = n;
        const r = $("[data-r]", el);
        if (n) renderResult(r, job);
        else r.innerHTML = UI.empty("live", "Klip pertama sedang dibuat", "Tiap jendela rekaman (±10 menit) ditranskrip, dipilih momennya oleh AI dengan bantuan lonjakan chat, lalu dirender. Klip muncul di sini otomatis.");
      }
    }
    $("[data-stop]", el) && ($("[data-stop]", el).hidden = job.status !== "running");
    $("[data-m]", el).textContent = job.message || "";
    const sec = Math.round(((job.status === "running" || job.status === "queued" ? Date.now() / 1000 : job.updated) - job.created));
    $("[data-el]", el).textContent = sec > 0 ? fmtDur(sec) : "";
    if (showLog && job.log != null) { const lb = $("[data-logbox]", el); lb.textContent = job.log || "(belum ada log)"; lb.scrollTop = lb.scrollHeight; }
    const finished = ["done", "error", "cancelled", "interrupted"].includes(job.status);
    $("[data-x]", el).hidden = finished;
    if (finished) {
      STATE.pollers.delete(key);
      if (btn) btn.disabled = false;
      refreshJobChip();
      if (job.status === "done") { renderResult($("[data-r]", el), job); onDone && onDone(job); if (!inModal) toast(job.message || "Selesai"); }
      else if (job.status !== "cancelled") {
        $("[data-r]", el).innerHTML = UI.banner("bad", `<b>Gagal:</b> ${esc(job.message)}<div class="row" style="margin-top:10px">${UI.btn("Ulangi", { ico: "refresh", size: "sm", attrs: "data-retry" })}${UI.btn("Lihat log", { ico: "terminal", size: "sm", kind: "ghost", attrs: "data-showlog" })}</div>`);
        $("[data-retry]", el).onclick = async () => { const j = await api(`/api/jobs/${id}/retry`, { body: {} }); attachJob(el, j.id, { onDone, btn }); };
        $("[data-showlog]", el).onclick = () => $("[data-log]", el).click();
      }
      return;
    }
    setTimeout(tick, 1400);
  }
  tick();
}

/* ================================================================ hasil & kartu media */
function mediaCard(c, { selectable = false } = {}) {
  const meta = c.meta || {};
  const path = c.file_path || c.file || c.path;
  const title = c.ai_title || meta.ai_title || c.title || meta.title || c.hook_text || c.name || baseName(path);
  const rawTags = c.hashtags ?? meta.hashtags ?? [];
  const tags = (Array.isArray(rawTags) ? rawTags : String(rawTags).split(/\s+/)).filter(Boolean);
  const tagText = tags.slice(0, 3).map((t) => (String(t).startsWith("#") ? String(t) : "#" + String(t))).join(" ");
  const baseCaption = c.upload_caption || meta.upload_caption || meta.description || "";
  // sama dengan yang benar-benar diunggah: caption AI + hashtag AI + hashtag tambahan dari Pengaturan
  const capTags = withGlobalTags(tags).filter((t) => !baseCaption.toLowerCase().includes(t.toLowerCase()));
  const caption = [baseCaption, capTags.join(" ")].filter(Boolean).join("\n\n").trim();
  const thumb = c.thumbnail || meta.thumbnail;
  const score = c.score ?? meta.score;
  const dur = c.duration ?? meta.duration;
  const id = c.asset_id || c.id || "";
  const wide = (meta.width || 0) > (meta.height || 0);
  let media;
  if (isAudio(path)) media = `<div class="m-thumb audio"><audio controls preload="none" src="${fileUrl(path)}" style="width:88%"></audio></div>`;
  else if (isVideo(path)) media = `<div class="m-thumb ${wide ? "wide" : ""}"><video controls preload="none" ${thumb ? `poster="${fileUrl(thumb)}"` : ""} src="${fileUrl(path)}"></video>`;
  else if (isImage(path)) media = `<div class="m-thumb"><img src="${fileUrl(path)}" alt="" loading="lazy">`;
  else media = `<div class="m-thumb audio">${icon("doc")}`;
  if (!isAudio(path)) media += `<div class="m-badges">${score ? `<span class="m-badge score">${Math.round(score)}</span>` : ""}${dur ? `<span class="m-badge">${fmtDur(dur)}</span>` : ""}</div></div>`;
  const metaLine = [c.clock ? "Live " + c.clock : "", (c.category || meta.category) && !String(c.category || meta.category).startsWith("manual") ? (c.category || meta.category) : "", tagText, c.fyp_grade ? "FYP " + c.fyp_grade : "", c.size ? fmtBytes(c.size) : "", c.size_mb ? c.size_mb + " MB" : "", meta.provider || ""].filter(Boolean).join(" · ");
  return `<article class="media" data-asset="${esc(id)}" data-path="${esc(path)}" data-cap="${esc(caption)}">${media}
    ${selectable ? `<input type="checkbox" class="m-sel" aria-label="Pilih">` : ""}
    <div class="m-body"><div class="m-title" title="${esc(title)}">${esc(title)}</div><div class="m-meta">${esc(metaLine) || "&nbsp;"}</div></div>
    <div class="m-actions">${UI.btn("Unduh", { ico: "download", size: "sm", href: fileUrl(path, true) })}<span class="spacer"></span>
      ${(c.subtitle_live || meta.subtitle_live) && isVideo(path) ? UI.btn("", { ico: "pen", size: "sm", kind: "ghost", title: "Edit subtitle (teks & gaya) lalu burn ulang", attrs: "data-subedit" }) : ""}
      ${caption ? UI.btn("", { ico: "copy", size: "sm", kind: "ghost", title: "Salin caption & hashtag", attrs: `data-copy="${esc(caption)}"` }) : ""}
      ${isVideo(path) && id ? UI.btn("", { ico: "calendar", size: "sm", kind: "ghost", title: "Jadwalkan upload", attrs: "data-sched" }) : ""}
      ${isVideo(path) ? UI.btn("", { ico: "upload", size: "sm", kind: "ghost", title: "Upload ke YouTube (headless, tanpa popup)", attrs: "data-yt" }) : ""}
      ${isVideo(path) ? UI.btn("", { ico: "music", size: "sm", kind: "ghost", title: "Upload ke TikTok", attrs: "data-tt" }) : ""}</div></article>`;
}

/** Hashtag klip + hashtag tambahan dari Pengaturan (GLOBAL_HASHTAGS), tanpa duplikat — sama dengan core/autopublish. */
function withGlobalTags(tags) {
  const out = [];
  const extra = String(STATE.status?.global_hashtags || "").match(/#?[\p{L}\p{N}_]+/gu) || [];
  for (const t of [...(tags || []), ...extra]) {
    const tag = "#" + String(t).replace(/^#/, "").replace(/[^\p{L}\p{N}_]/gu, "");
    if (tag.length > 1 && !out.some((x) => x.toLowerCase() === tag.toLowerCase())) out.push(tag);
  }
  return out.slice(0, 15);
}

function bindCards(root) {
  if (root.dataset.bound) return;
  root.dataset.bound = "1";
  root.addEventListener("click", (e) => {
    const cp = e.target.closest("[data-copy]");
    if (cp) return copyText(cp.dataset.copy, "Disalin");
    const se = e.target.closest("[data-subedit]");
    if (se) return subtitleEditor(se.closest(".media"));
    const sc = e.target.closest("[data-sched]");
    if (sc) return scheduleModal({ asset_id: sc.closest(".media").dataset.asset });
    const yt = e.target.closest("[data-yt]");
    if (yt) {
      const card = yt.closest(".media");
      const path = card.dataset.path;
      const caption = card.dataset.cap || "";
      const title = (card.querySelector(".m-title")?.textContent || baseName(path)).slice(0, 100);
      return busy(yt, async () => {
        try {
          const job = await api("/jobs", { body: { kind: "upload_browser", title: "Upload YouTube · " + title, params: { path, title, description: caption, privacy: "private" } } });
          toast("Upload berjalan di latar belakang — tidak ada jendela yang muncul");
          const done = await waitJob(job.id);
          toast(done.result?.skipped ? (done.message || "Sudah pernah diupload — dilewati") : done.status === "done" ? "Terupload ke YouTube (private)" : "Upload gagal: " + (done.message || "lihat Log Job"));
        } catch (err) { fail(err); }
      });
    }
    const tt = e.target.closest("[data-tt]");
    if (tt) {
      const card = tt.closest(".media");
      const path = card.dataset.path;
      const caption = card.dataset.cap || "";
      const title = (card.querySelector(".m-title")?.textContent || baseName(path)).slice(0, 100);
      return busy(tt, async () => {
        try {
          const job = await api("/jobs", { body: { kind: "upload_tiktok", title: "Upload TikTok · " + title, params: { path, description: caption } } });
          toast("Upload TikTok berjalan di latar belakang");
          const done = await waitJob(job.id);
          toast(done.result?.skipped ? (done.message || "Sudah pernah diupload — dilewati") : done.status === "done" ? "TikTok: " + ((done.result?.upload?.status) || "selesai") : "Upload gagal: " + (done.message || "lihat Log Job"));
        } catch (err) { fail(err); }
      });
    }
  });
  root.addEventListener("change", (e) => { if (e.target.classList.contains("m-sel")) e.target.closest(".media").classList.toggle("selected", e.target.checked); });
}

function renderResult(el, job) {
  const r = job.result || {};
  if (r.live_job) {
    el.innerHTML = UI.banner("info", `<b>Video ini masih live.</b> Mode Live sudah dimulai: siaran direkam dan klip dibuat tiap ±10 menit, dibantu lonjakan chat penonton.
      <div class="row" style="margin-top:10px">${UI.btn("Pantau Klip Live", { ico: "live", size: "sm", kind: "primary", href: "#live?job=" + encodeURIComponent(r.live_job) })}</div>`, "live");
    return;
  }
  if (r.clips && r.clips.length) {
    const paths = r.clips.map((c) => c.file_path).filter(Boolean);
    el.innerHTML = UI.panel({ title: r.live ? `${r.clips.length} klip dari siaran live` : `${r.clips.length} hasil siap`,
      sub: r.live ? esc([r.channel, r.windows ? `${r.windows} jendela` : "", r.chat ? `${r.chat} chat terbaca` : ""].filter(Boolean).join(" · ")) : r.ai ? `AI: ${esc(r.ai)}` : "",
      head: UI.btn("Unduh semua", { ico: "download", size: "sm", attrs: "data-zip" }) + UI.btn("Jadwalkan semua", { ico: "calendar", size: "sm", attrs: "data-bulk" }),
      body: `<div class="media-grid">${r.clips.map((c) => mediaCard(c)).join("")}</div>` });
    $("[data-zip]", el).onclick = (e) => busy(e.currentTarget, () => downloadZip(paths, job.title || "klip"));
    $("[data-bulk]", el).onclick = () => bulkScheduleModal(r.clips.map((c) => c.asset_id).filter(Boolean));
  } else if (r.file && (isVideo(r.file) || isAudio(r.file))) {
    const caps = r.storyboard?.captions || {};
    const tags = withGlobalTags(r.storyboard?.hashtags || []).join(" ");
    const side = Object.entries(caps).map(([k, v]) => `<div class="stack"><div class="row"><b class="small">Caption ${esc(k)}</b><span class="spacer"></span>${UI.btn("Salin", { ico: "copy", size: "sm", kind: "ghost", attrs: `data-copy="${esc(v + "\n\n" + tags)}"` })}</div><p class="small ink2">${esc(v)}</p></div>`).join('<hr style="border:0;border-top:1px solid var(--line);margin:0">');
    el.innerHTML = UI.panel({ title: "Hasil", sub: esc(r.storyboard?.title || job.title || ""), body: `<div class="layout wide-aside" style="grid-template-columns:minmax(0,300px) minmax(0,1fr)">
      <div>${mediaCard({ ...r, file_path: r.file, title: r.storyboard?.title || job.title })}</div>
      <div class="stack lg">${side}${r.narration ? `<div class="stack"><div class="row"><b class="small">Narasi</b><span class="spacer"></span>${UI.btn("Salin", { ico: "copy", size: "sm", kind: "ghost", attrs: `data-copy="${esc(r.narration)}"` })}</div><p class="small ink2" style="white-space:pre-wrap;max-height:260px;overflow:auto">${esc(r.narration)}</p></div>` : ""}
      ${!side && !r.narration ? `<dl class="kv"><dt>Durasi</dt><dd class="num">${fmtDur(r.duration)}</dd>${r.provider ? `<dt>Mesin</dt><dd>${esc(r.provider)} · ${esc(r.voice || "")}</dd>` : ""}<dt>Waktu proses</dt><dd class="num">${fmtDur(r.elapsed_sec)}</dd></dl>` : ""}</div></div>` });
  } else if (r.text !== undefined && r.segments) {
    el.innerHTML = transcriptView(r);
  } else {
    el.innerHTML = UI.banner("info", `Selesai. Folder hasil: <code>${esc(r.output_folder || "")}</code>`);
  }
  bindCards(el);
}

function transcriptView(r) {
  const files = r.files || {};
  const timed = r.segments.filter((s) => s.end).length;
  return UI.panel({ title: r.title || "Transkrip", sub: `${esc(r.origin || "")} · ${(r.text || "").split(/\s+/).filter(Boolean).length} kata`,
    head: Object.entries(files).map(([k, p]) => UI.btn(k.toUpperCase(), { ico: "download", size: "sm", href: fileUrl(p, true) })).join("") + UI.btn("Salin", { ico: "copy", size: "sm", attrs: `data-copy="${esc(r.text)}"` }),
    body: `<div class="log" style="max-height:520px;font-family:var(--font);font-size:13.5px;line-height:1.7">${timed ? r.segments.map((s) => `<span class="num muted">${fmtDur(s.start)}</span>  ${esc(s.text)}`).join("\n") : esc(r.text)}</div>` });
}

async function downloadZip(paths, name) {
  try {
    const res = await fetch("/api/zip", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ paths, name }) });
    if (!res.ok) throw new Error("Gagal membuat ZIP");
    const blob = await res.blob();
    const a = document.createElement("a"); a.href = URL.createObjectURL(blob); a.download = `${name.replace(/[^\w-]+/g, "_")}.zip`; a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 10000);
  } catch (e) { fail(e); }
}

/* ================================================================ jadwal upload */
function localInputValue(ts) { const d = new Date(ts * 1000); d.setMinutes(d.getMinutes() - d.getTimezoneOffset()); return d.toISOString().slice(0, 16); }
function platformToggles(selected = ["youtube"]) {
  const pf = STATE.status?.platforms || {};
  return `<div class="stack" style="gap:2px">${Object.entries(pf).map(([k, v]) => `<label class="switch"><input type="checkbox" data-pf="${k}" ${selected.includes(k) ? "checked" : ""}><span>${esc(v.label)} ${v.ready ? UI.pill("siap", "ok") : UI.pill("belum diset", "warn")}</span></label>`).join("")}</div>`;
}
const pickedPlatforms = (m) => $$("[data-pf]", m).filter((c) => c.checked).map((c) => c.dataset.pf);

/* (v7) editor subtitle live: ubah teks per layar / gaya / matikan, lalu burn ulang ke klip yang sama */
function subtitleEditor(card) {
  const path = card.dataset.path;
  const fmtT = (t) => `${Math.floor(t / 60)}:${(t % 60).toFixed(1).padStart(4, "0")}`;
  modal({ title: "Edit subtitle", sub: "Ubah teks atau gaya, lalu burn ulang. Klip tidak dirender ulang; ±10–30 detik.", lg: true,
    body: `<div data-sl>${UI.empty("doc", "Memuat…")}</div>`,
    foot: UI.btn("Kembalikan asli", { kind: "ghost", attrs: "data-reset" }) + `<span class="spacer"></span>` +
      UI.btn("Batal", { attrs: "data-close" }) + UI.btn("Burn ulang", { kind: "primary", ico: "check", attrs: "data-burn" }) }, async (m) => {
    let data;
    const draw = () => {
      const styles = (typeof CAPTION_STYLES === "function" ? CAPTION_STYLES() : []);
      $("[data-sl]", m).innerHTML = `<div class="fields two">
          ${UI.field("Gaya subtitle", `<select data-style>${UI.options(styles, data.style)}</select>`,
            { hint: `<img data-cap-prev src="/api/caption-preview?style=${encodeURIComponent(data.style)}" alt="" style="width:100%;max-width:360px;border-radius:8px;display:block;margin-top:6px">` })}
          <div class="stack">${UI.toggle("sl_on", "Tampilkan subtitle", { checked: data.enabled !== false })}
            <p class="small muted">${data.edited_at ? "Terakhir diubah " + new Date(data.edited_at * 1000).toLocaleString() : "Belum pernah diubah."}
            ${data.tempo > 1.0001 ? ` Klip dipercepat ${(+data.tempo).toFixed(2)}×, waktu di bawah memakai timeline sebelum tempo.` : ""}</p></div></div>
        <div class="stack" style="max-height:46vh;overflow:auto;margin-top:12px">${data.segments.map((s) => `<div class="row nw" style="gap:10px">
            <span class="small muted num" style="min-width:92px">${fmtT(s.start)}–${fmtT(s.end)}</span>
            <input type="text" data-seg="${s.i}" value="${esc(s.text)}" data-orig="${esc(s.text)}" style="flex:1" placeholder="(kosong = layar dihapus)"></div>`).join("")}</div>`;
      $("[data-style]", m).onchange = (e) => { const img = $("img[data-cap-prev]", m); if (img) img.src = `/api/caption-preview?style=${encodeURIComponent(e.target.value)}`; };
    };
    try {
      data = await api("/api/subtitle?path=" + encodeURIComponent(path));
      if (!data.available) { $("[data-sl]", m).innerHTML = UI.banner("info", "Klip ini tidak punya data subtitle live (dirender sebelum fitur ini ada, atau fiturnya dimatikan di Pengaturan → Subtitle)."); return; }
      draw();
    } catch (err) { fail(err); return; }
    const refreshCard = () => {
      const v = card.querySelector("video");
      if (v) { const src = v.getAttribute("src").replace(/([?&])t=\d+/, "").replace(/[?&]$/, ""); v.src = src + (src.includes("?") ? "&" : "?") + "t=" + Date.now(); v.load(); }
    };
    const run = (btn, body) => busy(btn, async () => {
      try {
        const r = await api("/api/subtitle/reburn", { body: { path, ...body } });
        toast(`Subtitle di-burn ulang (${r.seconds} dtk)`);
        refreshCard();
        closeModal();
      } catch (err) { fail(err); }
    });
    $("[data-burn]", m).onclick = (e) => {
      if (!data?.available) return;
      const edits = {};
      m.querySelectorAll("[data-seg]").forEach((inp) => { if (inp.value !== inp.dataset.orig) edits[inp.dataset.seg] = inp.value; });
      run(e.currentTarget, { style: $("[data-style]", m).value, edits, enabled: $("[name=sl_on]", m).checked });
    };
    $("[data-reset]", m).onclick = (e) => { if (data?.available) run(e.currentTarget, { reset: true }); };
  });
}

function scheduleModal({ asset_id, file_path, onSaved } = {}) {
  const soon = Math.ceil(Date.now() / 1000 / 900) * 900 + 900;
  modal({ title: "Jadwalkan upload", sub: "Judul & deskripsi diisi otomatis dari klip bila dikosongkan.",
    body: `${UI.field("Judul", `<input type="text" data-title placeholder="Otomatis dari klip">`)}
      ${UI.field("Deskripsi & hashtag", `<textarea data-desc rows="3" placeholder="Otomatis dari klip"></textarea>`)}
      ${UI.field("Waktu upload", `<input type="datetime-local" data-at value="${localInputValue(soon)}">`, { hint: "Server harus tetap menyala saat jadwal tiba." })}
      ${UI.field("Platform", platformToggles())}`,
    foot: UI.btn("Upload sekarang", { kind: "ghost", attrs: "data-now" }) + UI.btn("Simpan jadwal", { kind: "primary", attrs: "data-save" }) }, (m) => {
    // Isi judul & deskripsi dari metadata klip supaya langsung terpasang saat upload.
    if (asset_id) api(`/api/assets/${asset_id}`).then((a) => {
      const cp = a.copy || {};
      const t = $("[data-title]", m), d = $("[data-desc]", m);
      if (t && !t.value) t.value = cp.title || "";
      if (d && !d.value) d.value = cp.description || "";
    }).catch(() => {});
    const collect = (now) => ({ asset_id: asset_id || "", file_path: file_path || "", title: $("[data-title]", m).value, description: $("[data-desc]", m).value,
      platforms: pickedPlatforms(m), run_at: now ? Date.now() / 1000 : new Date($("[data-at]", m).value).getTime() / 1000 });
    $("[data-save]", m).onclick = (e) => busy(e.currentTarget, async () => { try { await api("/api/schedules", { body: collect(false) }); toast("Jadwal tersimpan"); closeModal(); onSaved && onSaved(); } catch (err) { fail(err); } });
    $("[data-now]", m).onclick = (e) => busy(e.currentTarget, async () => {
      try { const s = await api("/api/schedules", { body: collect(true) }); const r = await api(`/api/schedules/${s.id}/run-now`, { body: {} }); toast(r.status === "done" ? "Upload berhasil" : "Upload gagal — lihat Jadwal Upload", r.status !== "done"); closeModal(); onSaved && onSaved(); } catch (err) { fail(err); }
    });
  });
}

function bulkScheduleModal(assetIds, onSaved) {
  if (!assetIds.length) return toast("Pilih klip dulu", true);
  const ready = Object.entries(STATE.status?.platforms || {}).filter(([, v]) => v.ready).map(([k]) => k);
  const PF = { youtube: "YouTube", tiktok: "TikTok" };
  const dayName = (ts) => new Date(ts * 1000).toLocaleDateString("id-ID", { weekday: "short", day: "numeric", month: "short" });
  modal({ title: `Jadwalkan ${assetIds.length} klip`, sub: "Klip diunggah otomatis saat jadwalnya tiba. Klip skor tertinggi dapat jam paling awal.",
    body: `<div class="stack" style="gap:2px">
        <label class="switch"><input type="radio" name="bm" value="settings" checked><span>Ikuti jam upload di Pengaturan (disarankan)<span class="sub">Jam & batas klip per hari diambil dari Pengaturan → Upload otomatis (Senin–Jumat / Sabtu–Minggu). Slot yang sudah terisi dilewati.</span></span></label>
        <label class="switch"><input type="radio" name="bm" value="gap"><span>Jeda tetap<span class="sub">Upload berurutan tiap N menit, tanpa melihat jam ramai.</span></span></label></div>
      <div class="fields two">${UI.field("Mulai dari", `<input type="datetime-local" data-at value="${localInputValue(Date.now() / 1000)}">`)}
        <div data-gapf hidden>${UI.field("Jeda antar upload (menit)", `<input type="number" data-gap min="5" max="1440" value="60">`)}</div></div>
      ${UI.field("Platform", platformToggles(ready.length ? ready : ["youtube"]))}
      <label class="switch" data-ref hidden><input type="checkbox" data-re><span>Unggah ulang klip yang sudah pernah diunggah<span class="sub">Centang hanya bila videonya SUDAH kamu hapus di YouTube/TikTok — kalau belum, hasilnya video dobel.</span></span></label>
      <div data-prev class="small"></div>`,
    foot: UI.btn("Batal", { attrs: "data-close" }) + UI.btn("Jadwalkan", { kind: "primary", attrs: "data-save" }) }, (m) => {
    const mode = () => $("input[name=bm]:checked", m).value;
    const body = (preview) => ({ asset_ids: assetIds, mode: mode() === "settings" ? "settings" : "", preview, reupload: $("[data-re]", m).checked,
      gap_min: mode() === "gap" ? Math.max(5, parseInt($("[data-gap]", m).value || "60", 10)) : 0,
      start_at: new Date($("[data-at]", m).value).getTime() / 1000, platforms: pickedPlatforms(m) });
    let seq = 0;
    const preview = async () => {
      $("[data-gapf]", m).hidden = mode() !== "gap";
      const box = $("[data-prev]", m);
      if (mode() !== "settings") { box.innerHTML = ""; return; }
      if (!pickedPlatforms(m).length) { box.innerHTML = '<p class="muted">Pilih minimal satu platform.</p>'; return; }
      const my = ++seq; box.innerHTML = '<p class="muted">Menghitung jadwal…</p>';
      try {
        const r = await api("/api/schedules/bulk", { body: body(true) });
        if (my !== seq) return;
        $("[data-ref]", m).hidden = !r.uploaded?.length && !$("[data-re]", m).checked;
        const last = r.days[r.days.length - 1];
        const full = r.days.filter((d) => d.clips >= r.per_day).length;
        box.innerHTML = `<p style="margin:10px 0 6px"><b>${r.clips} klip → ${r.days.length} hari</b>${r.days.length ? ` (${esc(dayName(r.days[0].ts))} – ${esc(dayName(last.ts))})` : ""} · maks ${r.per_day} klip/hari
          ${full && full < r.days.length ? `<span class="muted"> · ${full} hari penuh + ${r.days.length - full} hari sisa</span>` : ""}</p>
          ${r.already ? `<p class="tiny muted" style="margin:0 0 6px">${r.already} klip sudah punya jadwal aktif — dilewati agar tidak terunggah dua kali.</p>` : ""}
          ${r.uploaded?.length ? `<p class="tiny ink2" style="margin:0 0 6px">${r.uploaded.length} klip <b>sudah pernah diunggah</b> (${r.uploaded.slice(0, 4).map((u) => `${esc(u.title.slice(0, 30))} → ${u.platforms.map((pf) => PF[pf] || pf).join("+")}`).join("; ")}${r.uploaded.length > 4 ? "; …" : ""}) — ${r.reupload ? "<b>akan diunggah ulang</b>." : "dilewati di platform itu agar tidak dobel."}</p>` : ""}
          <div class="list" style="max-height:220px;overflow:auto">${r.days.map((d) => `<div class="row nw" style="padding:4px 0;gap:10px"><b class="nowrap" style="min-width:96px">${esc(dayName(d.ts))}</b><span class="muted nowrap">${d.clips} klip</span>
            <span class="tiny">${Object.entries(d.slots).map(([pf, t]) => `${PF[pf] || pf} ${t.join(", ")}`).join(" · ")}</span></div>`).join("")}</div>
          <p class="tiny muted" style="margin:6px 0 0">Jam bisa geser beberapa menit agar tidak terlihat seperti bot. <a href="#settings" data-close>Ubah jam di Pengaturan</a></p>`;
      } catch (err) { if (my === seq) box.innerHTML = `<p class="bad">${esc(err.message || String(err))}</p>`; }
    };
    m.addEventListener("change", (e) => { if (e.target.matches("input[name=bm], [data-at], [data-pf], [data-gap], [data-re]")) preview(); });
    preview();
    $("[data-save]", m).onclick = (e) => busy(e.currentTarget, async () => {
      try {
        const r = await api("/api/schedules/bulk", { body: body(false) });
        if (r.mode === "settings") toast(`${r.clips} klip dijadwalkan dalam ${r.days.length} hari` + (r.already ? ` · ${r.already} sudah terjadwal (dilewati)` : ""));
        else toast(`${r.length} klip dijadwalkan tiap ${body(false).gap_min} menit`);
        closeModal(); onSaved && onSaved();
      } catch (err) { fail(err); }
    });
  });
}

function libraryPicker(onPick, { kinds = "clip,video,upload_video,source", multi = false, title = "Pilih dari Video Saya" } = {}) {
  modal({ title, lg: true, body: `<div data-lp>${UI.empty("folder", "Memuat…")}</div>`, foot: multi ? UI.btn("Batal", { attrs: "data-close" }) + UI.btn("Pilih", { kind: "primary", attrs: "data-ok" }) : "" }, async (m) => {
    const rows = (await api(`/api/assets?kind=${kinds}`)).filter((a) => a.exists && isVideo(a.path));
    const box = $("[data-lp]", m);
    box.innerHTML = rows.length ? `<div class="media-grid">${rows.map((a) => mediaCard({ ...a, title: a.meta?.title || a.name }, { selectable: true })).join("")}</div>` : UI.empty("folder", "Belum ada video", "Hasil klip dan video akan muncul di sini.");
    bindCards(box);
    if (multi) $("[data-ok]", m).onclick = () => { const ids = $$(".media", box).filter((x) => $(".m-sel", x).checked).map((x) => x.dataset.asset); closeModal(); onPick(rows.filter((r) => ids.includes(r.id))); };
    else box.addEventListener("change", (e) => { if (!e.target.classList.contains("m-sel")) return; const id = e.target.closest(".media").dataset.asset; closeModal(); onPick(rows.find((r) => r.id === id)); });
  });
}

/* ================================================================ shell */
function renderNav() {
  $("#nav").innerHTML = NAV.map((n) => (n.group ? `<div class="nav-group">${esc(n.group)}</div>`
    : `<a href="#${n.id}" data-id="${n.id}">${icon(n.ico)}<span>${esc(n.label)}</span>${n.isNew ? `<span class="pill accent">Baru</span>` : ""}</a>`)).join("");
  $("#menuBtn").innerHTML = icon("menu");
  $("#themeBtn").innerHTML = icon("moon");
  $("#newBtn").innerHTML = `${icon("plus")}<span>Buat klip</span>`;
}

async function refreshStatus() {
  try {
    STATE.status = await api("/api/status");
    const ai = STATE.status.ai;
    const prov = (ai.providers || []).find((p) => p.provider === ai.provider);
    const chip = $("#aiChip");
    chip.className = "chip hide-sm " + (prov?.configured ? "ok" : "warn");
    chip.innerHTML = `<span class="dot"></span>${esc(ai.provider)}${ai.model ? ` · ${esc(ai.model)}` : ""}${prov?.configured ? "" : " · belum diset"}`;
    chip.title = "Provider AI aktif — ubah di Pengaturan";
    refreshJobChip(STATE.status);
  } catch { /* server mati */ }
}
async function refreshJobChip(st) {
  try {
    st = st || (await api("/api/status"));
    const chip = $("#jobChip"), cur = st.current_job;
    if (cur || st.queued) {
      chip.hidden = false; chip.className = "chip run";
      chip.innerHTML = `<span class="dot"></span><span class="clip1" style="max-width:220px">${cur ? `${esc(cur.title || cur.kind)} · ${cur.progress}%` : "Antre"}</span>${st.queued ? ` <span class="pill">+${st.queued}</span>` : ""}`;
    } else chip.hidden = true;
  } catch { /* ignore */ }
}

function route() {
  STATE.pollers.clear();
  const raw = location.hash.replace(/^#/, "") || "home";
  const [id, qs] = raw.split("?");
  const page = PAGES[id] || PAGES.home;
  const params = Object.fromEntries(new URLSearchParams(qs || ""));
  $$("#nav a").forEach((a) => a.classList.toggle("active", a.dataset.id === id));
  const label = NAV.find((n) => n.id === id)?.label || "Beranda";
  $("#pageTitle").textContent = label;
  document.title = `${label} · Viral Auto Klip Studio`;
  closeSidebar();
  const view = $("#view");
  view.innerHTML = "";
  window.scrollTo(0, 0);
  try { page.render(view, params); hydrate(view); } catch (e) { view.innerHTML = UI.banner("bad", esc(e.message)); console.error(e); }
}

function keyBanner(needs) {
  const st = STATE.status;
  if (!st) return "";
  const msgs = [];
  if (needs.includes("llm")) {
    const prov = (st.ai.providers || []).find((p) => p.provider === st.ai.provider);
    if (!prov?.configured) msgs.push(`Provider AI <b>${esc(st.ai.provider)}</b> belum punya API key — fitur ini butuh LLM.`);
  }
  if (needs.includes("eleven") && !st.media.elevenlabs) msgs.push("Fitur ini butuh <b>ElevenLabs API key</b>.");
  if (needs.includes("ffmpeg") && !st.ffmpeg) msgs.push("<b>FFmpeg</b> tidak ditemukan — jalankan start.bat sekali.");
  return msgs.length ? UI.banner("warn", `${msgs.join("<br>")} <a href="#settings">Buka Pengaturan</a>`) : "";
}

function closeSidebar() { $("#sidebar").classList.remove("open"); $("#scrim").classList.remove("on"); }
function initShell() {
  const saved = store.get("theme", "");
  if (saved) document.documentElement.dataset.theme = saved;
  $("#themeBtn").onclick = () => {
    const cur = document.documentElement.dataset.theme || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
    const next = cur === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next; store.set("theme", next);
  };
  $("#menuBtn").onclick = () => { $("#sidebar").classList.add("open"); $("#scrim").classList.add("on"); };
  $("#scrim").onclick = closeSidebar;
}

async function boot() {
  renderNav();
  initShell();
  await Promise.all([refreshStatus(), api("/api/catalog").then((c) => (STATE.catalog = c)).catch(() => {})]);
  window.addEventListener("hashchange", route);
  route();
  setInterval(refreshJobChip, 5000);
  setInterval(refreshStatus, 60000);
}
