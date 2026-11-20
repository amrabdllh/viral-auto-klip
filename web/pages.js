/* Viral Auto Klip Studio — halaman tool (pola: page-head → layout[panel + aside] → hasil) */
"use strict";

const navItem = (id) => NAV.find((n) => n.id === id) || {};
const toolPage = ({ id, desc, needs = [], main, aside = "", extraHead = "" }) =>
  `<div class="page">${pageHead(navItem(id).label, desc, extraHead)}${keyBanner(needs)}
   <div class="layout"><div class="stack lg">${main}</div>${aside ? `<aside>${aside}</aside>` : ""}</div><div id="job" hidden></div></div>`;
const footAction = (label, ico, note = "") => `${note ? `<span class="note">${note}</span>` : '<span class="spacer"></span>'}${UI.btn(label, { ico, kind: "primary", size: "lg", id: "go" })}`;

const LANGS = [{ v: "", l: "Deteksi otomatis" }, { v: "id", l: "Indonesia" }, { v: "en", l: "English" }, { v: "ms", l: "Melayu" }, { v: "jw", l: "Jawa" }];
const ASPECT_CLIP = [{ v: "Vertical", l: "9:16 vertikal (crop wajah)" }, { v: "Fit", l: "9:16 utuh + latar blur" }, { v: "Wide", l: "16:9 lebar" }];
const FIT = [{ v: "blur", l: "Utuh + latar blur" }, { v: "cover", l: "Crop penuh" }, { v: "pad", l: "Letterbox hitam" }];
const VIDEO_MODES = [{ v: "single", l: "1 pembicara" }, { v: "podcast", l: "Podcast / 2 orang" }, { v: "group", l: "Grup 3+ orang" }, { v: "auto", l: "Deteksi otomatis" }];
const WHISPER_DEVICES = [{ v: "", l: "Ikut Pengaturan" }, { v: "elevenlabs", l: "ElevenLabs Scribe (paling akurat)" }, { v: "auto", l: "Whisper di GPU (gratis)" }, { v: "cpu", l: "Whisper di CPU (lambat)" }, { v: "groq", l: "Groq Whisper (cloud)" }];
const EDIT_STYLES = [{ v: "", l: "Ikut Pengaturan" }, { v: "minimal", l: "Minimal" }, { v: "dinamis", l: "Dinamis" }, { v: "maksimal", l: "Maksimal" }];
const captionSelect = (v = "viral") => UI.select("caption_style", "Gaya caption", CAPTION_STYLES(), v);
const CAPTION_STYLES = () => STATE.catalog?.caption_styles || ["viral", "modern", "tiktok_bold", "karaoke", "neon", "gradient", "minimal", "emoji"];
const aiSelects = () => {
  const st = STATE.status?.ai;
  const provs = (st?.providers || []).map((p) => ({ v: p.provider, l: `${p.label || p.provider}${p.configured ? "" : " (belum diset)"}`, disabled: !p.configured }));
  return UI.select("ai_provider", "Provider AI", [{ v: "", l: `Default (${st?.provider || "-"})` }, ...provs], "") +
    UI.select("ai_model", "Model AI", aiModelOptions(""), "");
};
/* Pilihan model untuk provider tertentu ("" = provider default), dari hasil deteksi atau daftar bawaan */
function aiModelOptions(provider) {
  const st = STATE.status?.ai;
  const p = (st?.providers || []).find((x) => x.provider === (provider || st?.provider));
  const def = provider ? p?.default_model : st?.model;
  return [{ v: "", l: `Default (${def || "otomatis"})` }, ...(p?.models || []).map((m) => ({ v: m, l: m }))];
}
document.addEventListener("change", (e) => {
  if (!e.target.matches?.('select[name="ai_provider"]')) return;
  const form = e.target.closest("form, .panel, .modal") || document;
  const sel = form.querySelector('select[name="ai_model"]');
  if (sel) sel.innerHTML = UI.options(aiModelOptions(e.target.value), "");
});

/* ================================================================== HOME */
PAGES.home = { render(view) {
  const groups = [["Klip dari video", ["analisis", "live", "manual", "splitcam", "bola", "tempel"]], ["Studio AI", ["creator", "story", "motion", "chat", "prompt"]],
    ["Audio", ["tts", "clone", "music"]], ["Edit cepat", ["editor", "merge", "loop", "wa"]], ["SEO, riset & distribusi", ["analytics", "ideas", "title", "transcript", "thumb", "upload", "brand"]]];
  view.innerHTML = `<div class="page">
    <section class="hero"><h1>1 video panjang, <em>puluhan klip viral.</em></h1>
      <p>Tempel link — AI memilih momen terbaik, memotong ke 9:16, memberi caption & siap upload.</p>
      <div class="omnibox"><input type="url" id="homeUrl" placeholder="Tempel link YouTube, TikTok, Instagram, atau Facebook…" aria-label="Link video">
        <div class="ob-bar"><div id="homeUp"></div><span class="ob-file" id="homeFile"></span><span class="spacer"></span>
          ${UI.btn("Atur manual", { ico: "scissors", kind: "ghost", size: "sm", href: "#manual" })}${UI.btn("Pengaturan lengkap", { ico: "sliders", kind: "ghost", size: "sm", attrs: 'id="homeAdv"' })}
          ${UI.btn("Buat klip", { ico: "zap", kind: "primary", id: "homeGo" })}</div></div>
      <p class="tiny muted" style="margin-top:12px">Pastikan kamu punya hak atau izin atas video yang diproses.</p></section>
    ${keyBanner(["llm", "ffmpeg"])}<div id="job" hidden></div>
    ${groups.map(([t, ids]) => `<div class="tool-group"><h3>${esc(t)}</h3><div class="tools">${ids.map((id) => { const n = navItem(id);
      return `<a class="tool" href="#${id}"><span class="t-ic">${icon(n.ico)}</span><span class="t-text"><b>${esc(n.label)}</b><span>${esc(n.desc || "")}</span></span></a>`; }).join("")}</div></div>`).join("")}
    ${UI.panel({ title: "Hasil terbaru", head: UI.btn("Lihat semua", { size: "sm", href: "#library", ico: "right" }), body: `<div id="recent">${UI.empty("folder", "Memuat…")}</div>` })}</div>`;
  let uploaded = null;
  uploadButton($("#homeUp"), { accept: "video/*", label: "Upload video", onUploaded: (i) => { uploaded = i; $("#homeUrl").value = ""; $("#homeFile").textContent = `${i.name} · ${fmtDur(i.duration)}`; } });
  const go = (e) => {
    const url = $("#homeUrl").value.trim();
    if (!url && !uploaded) return toast("Tempel link atau upload video dulu", true);
    runJob($("#job"), "clip_quick", url ? { url } : { path: uploaded.path }, { title: "Analisis AI cepat", btn: $("#homeGo") });
  };
  $("#homeGo").onclick = go;
  $("#homeUrl").onkeydown = (e) => e.key === "Enter" && go();
  $("#homeAdv").onclick = () => { location.hash = "#analisis" + ($("#homeUrl").value.trim() ? "?url=" + encodeURIComponent($("#homeUrl").value.trim()) : ""); };
  api("/api/assets?kind=clip,video&limit=10").then((rows) => {
    const el = $("#recent"); if (!el) return;
    rows = rows.filter((r) => r.exists).slice(0, 10);
    el.innerHTML = rows.length ? `<div class="media-grid">${rows.map((a) => mediaCard(a)).join("")}</div>` : UI.empty("film", "Belum ada klip", "Tempel link video di atas untuk membuat klip pertamamu.");
    bindCards(el);
  }).catch(() => {});
} };

/* ================================================================== LIBRARY */
PAGES.library = { render(view) {
  const filters = [{ v: "clip,video", l: "Klip & video" }, { v: "clip", l: "Klip" }, { v: "video", l: "Video AI & edit" }, { v: "audio,music,sfx", l: "Audio" },
    { v: "transcript", l: "Transkrip" }, { v: "upload_video,upload_audio,upload_image,source", l: "Upload & sumber" }];
  view.innerHTML = `<div class="page">${pageHead("Video Saya", "Semua hasil tersimpan di laptopmu (folder output/ dan data/).")}
    <div class="cols-4" id="stor"></div>
    ${UI.panel({ head: `<div id="flt"></div>`, body: `<div class="row" id="selbar"><span class="small muted" id="selcount">0 dipilih</span><span class="spacer"></span>
      ${UI.btn("Pilih semua", { size: "sm", kind: "ghost", id: "selAll" })}${UI.btn("Unduh ZIP", { ico: "download", size: "sm", id: "zipSel" })}${UI.btn("Jadwalkan", { ico: "calendar", size: "sm", id: "schSel" })}${UI.btn("Hapus", { ico: "trash", size: "sm", kind: "danger", id: "delSel" })}</div>
      <div id="lib"></div>` })}</div>`;
  let kind = store.get("libFilter", "clip,video");
  seg($("#flt"), filters, kind, (v) => { kind = v; store.set("libFilter", v); load(); });
  api("/api/storage").then((s) => { $("#stor").innerHTML = UI.stat("Hasil (output/)", fmtBytes(s.output_bytes)) + UI.stat("Data & upload", fmtBytes(s.data_bytes)) + UI.stat("Video sumber", fmtBytes(s.mentahan_bytes)) + UI.stat("Sisa disk", fmtBytes(s.disk_free_bytes)); }).catch(() => {});
  const selected = () => $$("#lib .media").filter((m) => $(".m-sel", m)?.checked);
  const count = () => ($("#selcount").textContent = `${selected().length} dipilih`);
  async function load() {
    $("#lib").innerHTML = UI.empty("folder", "Memuat…");
    const rows = (await api(`/api/assets?kind=${kind}`)).filter((a) => a.exists);
    $("#lib").innerHTML = rows.length ? `<div class="media-grid">${rows.map((a) => mediaCard(a, { selectable: true })).join("")}</div>` : UI.empty("folder", "Belum ada file di kategori ini");
    count();
  }
  bindCards($("#lib"));
  $("#lib").addEventListener("change", count);
  $("#selAll").onclick = () => { const all = $$("#lib .m-sel"); const on = all.some((c) => !c.checked); all.forEach((c) => { c.checked = on; c.closest(".media").classList.toggle("selected", on); }); count(); };
  $("#zipSel").onclick = (e) => { const s = selected(); if (!s.length) return toast("Pilih file dulu", true); busy(e.currentTarget, () => downloadZip(s.map((m) => m.dataset.path), "video_saya")); };
  $("#schSel").onclick = () => bulkScheduleModal(selected().map((m) => m.dataset.asset));
  $("#delSel").onclick = () => {
    const s = selected(); if (!s.length) return toast("Pilih file dulu", true);
    confirmModal(`Hapus ${s.length} file?`, "File dihapus permanen dari disk dan tidak bisa dikembalikan.", "Hapus", async () => {
      for (const c of s) await api(`/api/assets/${c.dataset.asset}`, { method: "DELETE" }).catch(fail);
      toast(`${s.length} file dihapus`); load();
    });
  };
  load().catch(fail);
} };

/* ================================================================== JOBS */
const KIND_LABEL = { live_clip: "Klip Live", live_window: "Jendela Live", clip_quick: "Analisis AI", clip_manual: "Atur Manual", split_cam: "Split Cam", highlight_bola: "Highlight Bola", konten_tempel: "Konten Tempel",
  merge: "Merge", loop: "Loop", wa_story: "WA Story", editor: "Editor", transcript: "Transcript", tts: "TTS", music: "Musik", sfx: "SFX", creator_render: "Video AI", fetch_source: "Unduh sumber" };
PAGES.jobs = { render(view) {
  view.innerHTML = `<div class="page">${pageHead("Antrean Job", "Job berjalan satu per satu di background dan tetap tersimpan walau server di-restart.", UI.btn("Muat ulang", { ico: "refresh", size: "sm", id: "rf" }))}
    ${UI.panel({ bodyCls: "flush", body: `<div class="table-wrap"><table><thead><tr><th>Job</th><th class="hide-sm">Jenis</th><th>Status</th><th class="r">Progres</th><th class="hide-sm">Dibuat</th><th></th></tr></thead><tbody id="jt"></tbody></table></div>` })}</div>`;
  async function load() {
    const rows = await api("/api/jobs?limit=100");
    const tb = $("#jt"); if (!tb) return;
    tb.innerHTML = rows.map((j) => `<tr data-id="${j.id}"><td style="max-width:420px"><b class="clip1" style="display:block">${esc(j.title)}</b><span class="tiny muted clip1" style="display:block">${esc(j.message)}</span></td>
      <td class="hide-sm small">${esc(KIND_LABEL[j.kind] || j.kind)}</td><td>${statusPill(j.status)}</td><td class="num">${j.progress}%</td><td class="hide-sm small nowrap">${fmtDate(j.created)}</td>
      <td class="r"><div class="btn-row" style="justify-content:flex-end;flex-wrap:nowrap">${UI.btn("Buka", { size: "sm", attrs: 'data-a="open"' })}
        ${["running", "queued"].includes(j.status) ? UI.btn("", { ico: "x", size: "sm", kind: "ghost", title: "Batalkan", attrs: 'data-a="cancel"' }) : UI.btn("", { ico: "refresh", size: "sm", kind: "ghost", title: "Jalankan ulang", attrs: 'data-a="retry"' })}
        ${j.status !== "running" ? UI.btn("", { ico: "trash", size: "sm", kind: "ghost", title: "Hapus", attrs: 'data-a="del"' }) : ""}</div></td></tr>`).join("") ||
      `<tr><td colspan="6">${UI.empty("queue", "Belum ada job", "Job dari semua tool akan muncul di sini.")}</td></tr>`;
  }
  $("#jt").onclick = async (e) => {
    const b = e.target.closest("[data-a]"); if (!b) return;
    const id = b.closest("tr").dataset.id;
    try {
      if (b.dataset.a === "open") modal({ title: "Detail job", lg: true, body: `<div data-jd></div>` }, (m) => attachJob($("[data-jd]", m), id, { inModal: true }));
      else if (b.dataset.a === "cancel") { await api(`/api/jobs/${id}/cancel`, { body: {} }); load(); }
      else if (b.dataset.a === "retry") { await api(`/api/jobs/${id}/retry`, { body: {} }); toast("Dijalankan ulang"); load(); }
      else if (b.dataset.a === "del") modal({ title: "Hapus job?", body: `<label class="switch"><input type="checkbox" data-f><span>Hapus juga folder hasilnya di disk</span></label>`,
        foot: UI.btn("Batal", { attrs: "data-close" }) + UI.btn("Hapus", { kind: "primary", attrs: "data-ok" }) }, (m) => {
        $("[data-ok]", m).onclick = async () => { await api(`/api/jobs/${id}?files=${$("[data-f]", m).checked}`, { method: "DELETE" }).catch(fail); closeModal(); load(); };
      });
    } catch (err) { fail(err); }
  };
  $("#rf").onclick = (e) => busy(e.currentTarget, load);
  load().catch(fail);
  const iv = setInterval(() => { if (!$("#jt")) return clearInterval(iv); if ($("#modal").hidden) load().catch(() => {}); }, 4000);
} };

/* ================================================================== ANALISIS AI */
PAGES.analisis = { render(view, q) {
  view.innerHTML = toolPage({ id: "analisis", needs: ["llm", "ffmpeg"],
    desc: "AI membaca seluruh transkrip, memilih momen paling viral, lalu merender klip vertikal dengan reframe wajah, caption animasi, dan editing dinamis.",
    main: UI.panel({ id: "f", body: UI.section("Sumber video", `<div id="src"></div>`) +
      UI.section("Klip", `<div class="fields">${UI.num("num_clips", "Jumlah klip", { value: 8, min: 1, max: 25 })}${UI.num("min_duration", "Durasi minimum", { value: 15, min: 5, max: 120, hint: "detik" })}
        ${UI.num("max_duration", "Durasi maksimum", { value: 60, min: 10, max: 180, hint: "detik" })}${UI.select("output_type", "Rasio", ASPECT_CLIP, "Vertical")}
        ${UI.select("video_mode", "Tipe video", VIDEO_MODES, "single")}${UI.select("language", "Bahasa video", LANGS, "id")}</div>`) +
      UI.section("Tampilan", `<div class="fields">${captionSelect()}${UI.select("edit_style", "Gaya editing", EDIT_STYLES, "")}${UI.text("keyword", "Fokus kata kunci", { placeholder: "mis. investasi, gol", hint: "Opsional — klip yang menyebut kata ini diutamakan." })}</div>
        <div class="switches">${UI.toggle("enable_captions", "Caption otomatis")}${UI.toggle("enable_face_reframe", "Ikuti wajah")}${UI.toggle("hook_card", "Kartu hook di awal")}${UI.toggle("brand_kit", "Logo Brand Kit")}</div>`) +
      UI.more("Pengaturan lanjutan", `<div class="fields">${UI.num("min_score", "Skor viral minimum", { value: 55, min: 0, max: 100 })}${UI.tri("jump_cuts", "Potong jeda")}${UI.tri("dynamic_zoom", "Zoom dinamis")}${UI.tri("progress_bar", "Progress bar")}
        ${UI.tri("sfx", "SFX transisi")}${UI.tri("end_cta", "Ajakan di akhir")}${UI.select("color_grade", "Warna", [{ v: "", l: "Ikut preset" }, "none", "natural", "vibrant", "cinematic"], "")}
        ${UI.tri("cold_open", "Cold open + rewind")}${UI.tri("caption_fx", "Caption animasi + emoji")}${UI.tri("sound_design", "Sound design")}
        ${UI.tri("speaker_cam", "Kamera ikut pembicara")}${UI.tri("broll", "B-roll otomatis")}${UI.tri("shake", "Layar goyang")}
        ${UI.tri("freeze", "Freeze frame")}${UI.tri("lower_third", "Kartu nama")}${UI.tri("censor", "Sensor kata kasar")}${UI.text("speaker_names", "Nama pembicara", { placeholder: "mis. Deddy, Raditya", hint: "Urut sesuai siapa yang bicara duluan. Kosong = nama channel." })}
        ${UI.select("target_platform", "Platform target", ["Semua", "TikTok", "YouTube Shorts", "Instagram Reels"], "Semua")}${UI.text("watermark_text", "Watermark teks", { placeholder: "@akunmu" })}</div>
        <div class="fields">${UI.select("whisper_device", "Mesin transkripsi", WHISPER_DEVICES, "")}
        ${UI.select("whisper_model", "Model Whisper", ["", "small", "medium", "large-v3-turbo", "large-v3"].map((v) => ({ v, l: v || "Ikut Pengaturan" })), "")}${aiSelects()}</div>
        <div class="switches">${UI.toggle("use_llm", "Pakai AI memilih & menulis")}${UI.toggle("publish_package", "Paket upload per platform")}${UI.toggle("audio_enhance", "Perjernih audio")}</div>`),
      foot: footAction("Proses dengan AI", "sparkles", "Rata-rata 1–3 menit per klip di GPU.") }),
    aside: UI.aside("Cara kerja", ["Video diunduh (≥1080p bila tersedia) atau dipakai dari file upload.", "Ucapan ditranskrip per kata (ElevenLabs Scribe, atau Whisper sebagai cadangan).",
      "AI membaca transkrip & memilih momen dengan hook kuat.", "Tiap klip di-reframe mengikuti wajah, diberi caption, jump cut, zoom & thumbnail.",
      "AI menulis judul, caption & hashtag. Hasil bisa langsung dijadwalkan."], UI.banner("info", "Boleh pindah halaman — progres tetap jalan di <a href='#jobs'>Antrean Job</a>.")) });
  const src = sourceInput($("#src"), { prefill: q.url || "" });
  $("#go").onclick = (e) => {
    const s = requireSource(src); if (!s) return;
    const p = dropEmpty(formValues($("#f")), ["whisper_device", "whisper_model", "edit_style", "color_grade", "keyword", "watermark_text", "ai_provider", "ai_model", "speaker_names"]);
    runJob($("#job"), "clip_quick", { ...p, ...s }, { title: "Analisis AI", btn: e.currentTarget });
  };
} };

/* ================================================================== KLIP LIVE */
const BACKLOG = [{ v: 0, l: "Mulai sekarang" }, { v: 10, l: "10 menit terakhir" }, { v: 20, l: "20 menit terakhir" }, { v: 30, l: "30 menit terakhir" }, { v: 58, l: "1 jam terakhir (maksimal)" }];
const WINDOWS = [{ v: 5, l: "Tiap 5 menit (klip lebih cepat)" }, { v: 10, l: "Tiap 10 menit" }, { v: 15, l: "Tiap 15 menit (konteks lebih panjang)" }];
PAGES.live = { render(view, q) {
  view.innerHTML = toolPage({ id: "live", needs: ["llm", "ffmpeg"],
    desc: "Buat klip dari siaran YouTube yang masih berlangsung. Siaran direkam terus, dan tiap beberapa menit AI memilih momen terbaik dengan bantuan lonjakan chat penonton.",
    main: UI.panel({ id: "f", body: UI.section("Siaran", `<div class="fields">${UI.text("url", "Link siaran live", { type: "url", placeholder: "https://www.youtube.com/watch?v=…", value: q.url || "", cls: "span-all" })}
        ${UI.select("backlog_min", "Mulai rekam dari", BACKLOG, 20, { numeric: true, hint: "YouTube menyimpan ±1 jam siaran ke belakang." })}${UI.select("window_min", "Buat klip", WINDOWS, 10, { numeric: true })}
        ${UI.num("clips_per_window", "Maks klip per jendela", { value: 3, min: 1, max: 8 })}${UI.select("language", "Bahasa siaran", LANGS, "id")}</div>`) +
      UI.section("Tampilan", `<div class="fields">${captionSelect()}${UI.select("edit_style", "Gaya editing", EDIT_STYLES, "")}</div>
        <div class="switches">${UI.toggle("use_chat", "Baca chat live", { sub: "Lonjakan chat & Super Chat jadi petunjuk momen seru" })}${UI.toggle("cold_open", "Cold open + rewind")}${UI.toggle("sound_design", "Sound design")}${UI.toggle("caption_fx", "Caption animasi + emoji")}${UI.toggle("hook_card", "Kartu hook di awal")}${UI.toggle("brand_kit", "Logo Brand Kit")}</div>`) +
      UI.more("Pengaturan lanjutan", `<div class="fields">${UI.num("min_score", "Skor viral minimum", { value: 45, min: 0, max: 100 })}${UI.num("min_duration", "Durasi minimum", { value: 15, min: 5, max: 120, hint: "detik" })}
        ${UI.num("max_duration", "Durasi maksimum", { value: 60, min: 10, max: 180, hint: "detik" })}${UI.num("max_hours", "Rekam paling lama", { value: 6, min: 1, max: 12, hint: "jam" })}
        ${UI.select("layout", "Layout", [{ v: "", l: "Otomatis (facecam → split)" }, { v: "gaming", l: "Selalu split facecam + gameplay" }, { v: "face", l: "Ikuti wajah (crop 9:16)" }], "")}
        ${UI.select("whisper_device", "Mesin transkripsi", WHISPER_DEVICES, "")}${aiSelects()}</div>
        <div class="fields">${UI.tri("speaker_cam", "Kamera ikut pembicara")}${UI.tri("broll", "B-roll otomatis")}${UI.tri("shake", "Layar goyang")}
        ${UI.tri("freeze", "Freeze frame")}${UI.tri("lower_third", "Kartu nama")}${UI.tri("censor", "Sensor kata kasar")}
        ${UI.text("speaker_names", "Nama pembicara", { placeholder: "Kosong = nama channel" })}</div>`),
      foot: footAction("Mulai Klip Live", "live", "Klip pertama ±10–15 menit setelah mulai.") }),
    aside: UI.aside("Cara kerja", ["Siaran direkam dari playlist YouTube (bisa mundur sampai ±1 jam), chat live ikut dibaca.",
      "Tiap jendela rekaman ditranskrip, lalu AI memilih momen dengan bantuan lonjakan chat (\u201cWKWK\u201d, \u201canjir\u201d, Super Chat) dan teriakan streamer.",
      "Facecam terdeteksi otomatis: wajah di atas, gameplay di bawah. Klip diberi cold open, caption animasi, emoji, dan sound design.",
      "Klip muncul di bawah satu per satu. Tekan \u201cSelesai rekam\u201d kapan saja — sisa rekaman tetap dijadikan klip."],
      UI.banner("info", "Rekaman jalan di background. Job lain tetap bisa dipakai, dan halaman ini boleh ditutup — pantau dari <a href='#jobs'>Antrean Job</a>.")) });
  if (q.job) attachJob($("#job"), q.job, {});
  $("#go").onclick = (e) => {
    const p = dropEmpty(formValues($("#f")), ["whisper_device", "edit_style", "layout", "ai_provider", "ai_model", "speaker_names"]);
    if (!/^https?:\/\//.test(p.url || "")) return toast("Tempel link siaran YouTube dulu", true);
    runJob($("#job"), "live_clip", p, { title: "Klip Live", btn: e.currentTarget });
  };
} };

/* ================================================================== helper editor video */
async function loadPreview(src, onReady) {
  const s = requireSource(src); if (!s) return;
  if (s.path) return onReady({ path: s.path, ...(src.info || {}) });
  const btn = $("#load");
  await busy(btn, async () => {
    try {
      const job = await api("/api/jobs", { body: { kind: "fetch_source", title: "Unduh sumber", params: { url: s.url } } });
      const done = await waitJob(job.id, (j) => toast(`Mengunduh video… ${j.progress}%`, false, 1300));
      src.setPath(done.result.file, { name: baseName(done.result.file), duration: done.result.duration, width: done.result.width, height: done.result.height });
      onReady({ path: done.result.file, ...done.result });
    } catch (e) { fail(e); }
  });
}
const sourcePanel = (note) => UI.panel({ title: "1 · Sumber video", sub: note, body: `<div id="src"></div>`, foot: `<span class="spacer"></span>${UI.btn("Muat ke editor", { ico: "play", kind: "primary", id: "load" })}` });

function dragBox(stage, box, rect, onChange, { resize = true } = {}) {
  const apply = () => Object.assign(box.style, { left: rect.x * 100 + "%", top: rect.y * 100 + "%", width: rect.w == null ? "auto" : rect.w * 100 + "%", height: rect.h == null ? "auto" : rect.h * 100 + "%" });
  apply();
  box.addEventListener("pointerdown", (e) => {
    e.preventDefault();
    const mode = e.target.classList.contains("b-rs") && resize ? "resize" : "move";
    const r = stage.getBoundingClientRect(), sx = e.clientX, sy = e.clientY, start = { ...rect };
    box.setPointerCapture(e.pointerId);
    const move = (ev) => {
      const dx = (ev.clientX - sx) / r.width, dy = (ev.clientY - sy) / r.height;
      if (mode === "move") { rect.x = Math.min(Math.max(0, start.x + dx), 1 - (rect.w ?? 0.05)); rect.y = Math.min(Math.max(0, start.y + dy), 1 - (rect.h ?? 0.05)); }
      else if (rect.w != null) { rect.w = Math.min(Math.max(0.05, start.w + dx), 1 - rect.x); if (rect.h != null) rect.h = Math.min(Math.max(0.05, start.h + dy), 1 - rect.y); }
      apply(); onChange && onChange(rect);
    };
    const up = () => { box.removeEventListener("pointermove", move); box.removeEventListener("pointerup", up); };
    box.addEventListener("pointermove", move); box.addEventListener("pointerup", up);
  });
}

function markControls(video, el, onAdd, { withTitle = true, addLabel = "Tambah segmen" } = {}) {
  el.innerHTML = `<div class="marks">${UI.btn("Tandai mulai", { size: "sm", attrs: "data-s" })}<span class="t" data-sv>0:00.0</span>
    ${UI.btn("Tandai selesai", { size: "sm", attrs: "data-e" })}<span class="t" data-ev>—</span><span class="spacer"></span>${UI.btn(addLabel, { ico: "plus", size: "sm", kind: "primary", attrs: "data-add" })}</div>
    ${withTitle ? `<input type="text" data-t placeholder="Judul / hook klip (opsional)" style="margin-top:10px">` : ""}`;
  const m = { start: 0, end: 0 };
  $("[data-s]", el).onclick = () => { m.start = video.currentTime; $("[data-sv]", el).textContent = fmtTime(m.start); };
  $("[data-e]", el).onclick = () => { m.end = video.currentTime; $("[data-ev]", el).textContent = fmtTime(m.end); };
  $("[data-add]", el).onclick = () => {
    if (m.end <= m.start) return toast("Tandai selesai setelah titik mulai", true);
    onAdd({ start: +m.start.toFixed(2), end: +m.end.toFixed(2), title: withTitle ? $("[data-t]", el).value.trim() : "" });
    if (withTitle) $("[data-t]", el).value = "";
    m.start = m.end; $("[data-sv]", el).textContent = fmtTime(m.start); $("[data-ev]", el).textContent = "—";
  };
}

/* ================================================================== ATUR MANUAL */
PAGES.manual = { render(view) {
  view.innerHTML = toolPage({ id: "manual", needs: ["ffmpeg"], desc: "Pilih sendiri titik mulai & selesai tiap klip. Caption, reframe wajah dan editing tetap otomatis.",
    main: sourcePanel("Link akan diunduh dulu agar bisa diputar.") + `<div id="ed" hidden class="stack lg">
      ${UI.panel({ title: "2 · Tandai segmen", sub: "Putar video, tandai mulai & selesai, lalu tambahkan.", body: `<div class="stage"><video id="v" controls></video></div><div id="mk"></div>` })}
      ${UI.panel({ id: "f", title: "3 · Render", body: `<div class="fields">${UI.select("output_type", "Rasio", ASPECT_CLIP, "Vertical")}${UI.select("video_mode", "Tipe video", VIDEO_MODES.slice(0, 3), "single")}
        ${captionSelect()}${UI.select("edit_style", "Gaya editing", EDIT_STYLES, "")}${UI.select("language", "Bahasa", LANGS, "id")}</div>
        <div class="switches">${UI.toggle("enable_captions", "Caption otomatis")}${UI.toggle("enable_face_reframe", "Ikuti wajah")}${UI.toggle("hook_card", "Kartu judul di awal", { checked: false })}${UI.toggle("brand_kit", "Logo Brand Kit")}</div>`,
        foot: footAction("Render segmen", "scissors") })}</div>`,
    aside: UI.panel({ title: "Segmen", head: `<span class="pill" id="cnt">0</span>`, body: `<div class="list" id="segs"></div>` }) });
  const src = sourceInput($("#src"));
  const segs = [];
  let path = "";
  const draw = () => {
    $("#cnt").textContent = segs.length;
    $("#segs").innerHTML = segs.map((s, i) => `<div class="list-item"><span class="idx">${i + 1}</span><div class="li-main"><b>${esc(s.title || "Tanpa judul")}</b><span class="num">${fmtTime(s.start)} → ${fmtTime(s.end)} · ${(s.end - s.start).toFixed(1)} dtk</span></div>
      ${UI.btn("", { ico: "play", size: "sm", kind: "ghost", title: "Putar", attrs: `data-play="${i}"` })}${UI.btn("", { ico: "x", size: "sm", kind: "ghost", title: "Hapus", attrs: `data-del="${i}"` })}</div>`).join("")
      || UI.empty("scissors", "Belum ada segmen", "Muat video, lalu tandai mulai & selesai.");
  };
  $("#segs").onclick = (e) => {
    const p = e.target.closest("[data-play]"), d = e.target.closest("[data-del]");
    if (p) { const s = segs[+p.dataset.play]; $("#v").currentTime = s.start; $("#v").play(); }
    if (d) { segs.splice(+d.dataset.del, 1); draw(); }
  };
  draw();
  $("#load").onclick = () => loadPreview(src, (info) => { path = info.path; $("#ed").hidden = false; $("#v").src = fileUrl(path); markControls($("#v"), $("#mk"), (s) => { segs.push(s); draw(); }); $("#ed").scrollIntoView({ behavior: "smooth" }); });
  $("#go").onclick = (e) => {
    if (!segs.length) return toast("Tambah minimal satu segmen", true);
    runJob($("#job"), "clip_manual", { ...dropEmpty(formValues($("#f")), ["edit_style"]), path, ranges: segs }, { title: "Atur Manual", btn: e.currentTarget });
  };
} };

/* ================================================================== SPLIT CAM */
PAGES.splitcam = { render(view) {
  view.innerHTML = toolPage({ id: "splitcam", needs: ["ffmpeg"], desc: "Untuk konten gaming & streaming: area facecam dan gameplay ditumpuk vertikal 9:16.",
    main: sourcePanel("Video landscape dengan facecam di salah satu sudut.") + `<div id="ed" hidden class="stack lg">
      ${UI.panel({ title: "2 · Atur area", sub: "Geser kotak, tarik pojok kanan-bawah untuk mengubah ukuran. Pause di frame yang jelas.", body: `<div class="stage" id="stage"><video id="v" controls muted></video>
        <div class="box" id="bg"><span class="b-lbl">Gameplay</span><span class="b-rs"></span></div><div class="box teal" id="bc"><span class="b-lbl">Facecam</span><span class="b-rs"></span></div></div>
        <div id="mk"></div><div class="small muted" id="segs">Tanpa segmen = seluruh video dirender.</div>` })}
      ${UI.panel({ id: "f", title: "3 · Tata letak", body: `<div class="fields two">${UI.range("cam_ratio", "Tinggi facecam", { value: 0.38, min: 0.2, max: 0.6, step: 0.01, fmt: "%" })}
        ${UI.field("Posisi facecam", `<div id="pos"></div>`)}${captionSelect()}</div><div class="switches">${UI.toggle("enable_captions", "Caption otomatis", { checked: false })}${UI.toggle("brand_kit", "Logo Brand Kit")}</div>`,
        foot: footAction("Render Split Cam", "splitcam") })}</div>`,
    aside: UI.aside("Tips", ["Kotak <b>Facecam</b> (hijau) menandai wajah streamer.", "Kotak <b>Gameplay</b> (oranye) menandai area permainan.", "Atur tinggi facecam 30–40% agar gameplay tetap jelas."]) });
  const src = sourceInput($("#src"));
  const cam = { x: 0.7, y: 0.02, w: 0.28, h: 0.3 }, game = { x: 0, y: 0, w: 1, h: 1 };
  let path = "", camTop = true;
  const segs = [];
  seg($("#pos"), [{ v: "top", l: "Atas" }, { v: "bottom", l: "Bawah" }], "top", (v) => (camTop = v === "top"), { block: true });
  $("#load").onclick = () => loadPreview(src, (info) => {
    path = info.path; $("#ed").hidden = false; $("#v").src = fileUrl(path);
    dragBox($("#stage"), $("#bg"), game); dragBox($("#stage"), $("#bc"), cam);
    markControls($("#v"), $("#mk"), (s) => { segs.push(s); $("#segs").textContent = "Segmen: " + segs.map((x) => `${fmtTime(x.start)}–${fmtTime(x.end)}`).join(", "); }, { withTitle: false });
  });
  $("#go").onclick = (e) => {
    const r = (b) => [b.x, b.y, b.w, b.h].map((n) => +n.toFixed(4));
    runJob($("#job"), "split_cam", { ...formValues($("#f")), path, cam_box: r(cam), game_box: r(game), cam_top: camTop, ranges: segs.length ? segs : undefined }, { title: "Split Cam", btn: e.currentTarget });
  };
} };

/* ================================================================== HIGHLIGHT BOLA */
PAGES.bola = { render(view) {
  view.innerHTML = toolPage({ id: "bola", needs: ["ffmpeg"], desc: "Momen gol & peluang emas dideteksi dari lonjakan sorakan penonton dan kata kunci komentator, lalu dipotong jadi highlight vertikal.",
    main: UI.panel({ id: "f", body: UI.section("Sumber video", `<div id="src"></div>`) +
      UI.section("Highlight", `<div class="fields">${UI.num("num_clips", "Jumlah highlight", { value: 6, min: 1, max: 20 })}${UI.num("pre_sec", "Sebelum puncak", { value: 12, min: 3, max: 40, hint: "detik" })}
        ${UI.num("post_sec", "Sesudah puncak", { value: 8, min: 2, max: 30, hint: "detik" })}${UI.select("aspect", "Rasio", ["9:16", "1:1", "16:9"], "9:16")}${UI.select("fit_mode", "Isi frame", FIT, "blur")}
        ${UI.text("caption_text", "Label di layar", { value: "MOMEN KUNCI" })}</div>
        <div class="switches">${UI.toggle("use_transcript", "Pakai transkrip komentator", { sub: "Lebih akurat, lebih lama" })}${UI.toggle("brand_kit", "Logo Brand Kit")}</div>`) +
      UI.more("Teriakan komentator (TTS)", `${UI.toggle("commentary", "Tambahkan teriakan di puncak momen", { checked: false })}${UI.text("commentary_text", "Kalimat komentator", { value: "Gooool! Momen luar biasa di pertandingan ini!" })}<div id="vp"></div>`),
      foot: footAction("Cari highlight", "ball") }),
    aside: UI.aside("Cara kerja", ["Energi audio diukur tiap 0,5 detik.", "Lonjakan dibanding suasana ±40 detik sekitarnya = momen heboh.", "Kata seperti “gol”, “penalti”, “offside” di transkrip menambah skor.", "Tiap puncak dipotong (sebelum + sesudah) dan dirender vertikal."],
      UI.banner("info", "Paling akurat untuk siaran lengkap dengan suara stadion. Pastikan kamu punya izin atas rekamannya.")) });
  const src = sourceInput($("#src"));
  let vp; voicePicker($("#vp")).then((x) => (vp = x));
  $("#go").onclick = (e) => { const s = requireSource(src); if (s) runJob($("#job"), "highlight_bola", { ...formValues($("#f")), ...(vp ? vp.value() : {}), ...s }, { title: "Highlight Bola", btn: e.currentTarget }); };
} };

/* ================================================================== overlay (Konten Tempel & Editor) */
function overlayEditor(stage, listEl, items) {
  const redraw = () => {
    $$(".ovl", stage).forEach((n) => n.remove());
    items.forEach((it) => {
      const n = document.createElement("div");
      n.className = "ovl";
      if (it.type === "image") n.innerHTML = `<img src="${fileUrl(it.path)}" alt="">`;
      else { n.textContent = it.text; n.style.fontSize = Math.max(8, it.size * (stage.clientHeight || 400)) + "px"; n.style.color = it.color; n.style.background = it.box ? "rgba(0,0,0,.55)" : "transparent"; n.style.padding = it.box ? "2px 6px" : "0"; n.style.whiteSpace = "pre"; }
      stage.appendChild(n);
      dragBox(stage, n, { x: it.x, y: it.y, w: it.type === "image" ? it.w : null, h: null }, (r) => { it.x = +r.x.toFixed(3); it.y = +r.y.toFixed(3); }, { resize: false });
    });
    const vid = $("video", stage);
    if (vid && !vid.dataset.ovl) { vid.dataset.ovl = "1"; vid.addEventListener("loadedmetadata", () => redraw()); }
    listEl.innerHTML = items.map((it, i) => `<div class="list-item" style="flex-wrap:wrap"><span class="idx">${i + 1}</span><div class="li-main"><b>${it.type === "image" ? "Gambar" : esc(it.text)}</b><span>${it.type === "image" ? "Gambar / logo" : "Teks"}</span></div>
      ${UI.btn("", { ico: "trash", size: "sm", kind: "ghost", title: "Hapus", attrs: `data-d="${i}"` })}
      <div class="fields three" style="width:100%">${it.type === "image" ? UI.field("Lebar", `<input type="range" min="0.05" max="1" step="0.01" value="${it.w}" data-w="${i}">`) + UI.field("Opasitas", `<input type="range" min="0.1" max="1" step="0.05" value="${it.opacity}" data-o="${i}">`) : ""}
        ${UI.field("Muncul (dtk)", `<input type="number" step="0.1" min="0" value="${it.start}" data-s="${i}">`)}${UI.field("Hilang (dtk)", `<input type="number" step="0.1" min="0" value="${it.end ?? ""}" placeholder="akhir" data-e="${i}">`)}</div></div>`).join("")
      || UI.empty("layers", "Belum ada elemen", "Tambahkan teks atau gambar, lalu geser di pratinjau.");
  };
  listEl.oninput = (e) => {
    const t = e.target;
    if (t.dataset.w) { items[+t.dataset.w].w = +t.value; const imgs = $$(".ovl", stage); const n = imgs[+t.dataset.w]; if (n) n.style.width = t.value * 100 + "%"; }
    if (t.dataset.o) items[+t.dataset.o].opacity = +t.value;
    if (t.dataset.s) items[+t.dataset.s].start = +t.value;
    if (t.dataset.e) items[+t.dataset.e].end = t.value === "" ? null : +t.value;
  };
  listEl.onclick = (e) => { const d = e.target.closest("[data-d]"); if (d) { items.splice(+d.dataset.d, 1); redraw(); } };
  return redraw;
}
const overlayAddForm = () => `<div class="fields" style="grid-template-columns:minmax(0,1fr) auto auto">${UI.field("Teks", `<input type="text" id="tt" placeholder="Teks yang ditempel">`)}
  ${UI.field("Warna", `<input type="color" id="tc" value="#ffffff">`)}${UI.field("&nbsp;", UI.btn("Teks", { ico: "plus", id: "addT" }))}</div>
  <div class="row"><label class="switch"><input type="checkbox" id="tb" checked><span>Kotak gelap</span></label><span class="spacer"></span><div id="imgUp"></div></div>`;
function bindOverlayAdd(items, redraw) {
  uploadButton($("#imgUp"), { accept: "image/*", label: "Gambar / logo", onUploaded: (i) => { items.push({ type: "image", path: i.path, x: 0.05, y: 0.05, w: 0.3, opacity: 1, start: 0, end: null }); redraw(); } });
  $("#addT").onclick = () => { const t = $("#tt").value.trim(); if (!t) return toast("Tulis teksnya dulu", true); items.push({ type: "text", text: t, x: 0.08, y: 0.1, size: 0.045, color: $("#tc").value, box: $("#tb").checked, start: 0, end: null }); $("#tt").value = ""; redraw(); };
}

/* ================================================================== KONTEN TEMPEL */
PAGES.tempel = { render(view) {
  view.innerHTML = toolPage({ id: "tempel", needs: ["ffmpeg"], desc: "Tempel gambar, logo, stiker, atau teks di atas video. Geser elemen langsung di pratinjau dan atur kapan muncul.",
    main: sourcePanel("") + `<div id="ed" hidden>${UI.panel({ title: "2 · Pratinjau", sub: "Geser elemen untuk memindahkan.", body: `<div class="stage" id="stage"><video id="v" controls muted></video></div>`, foot: footAction("Render", "layers") })}</div>`,
    aside: UI.panel({ title: "Elemen", body: `${overlayAddForm()}<div class="list" id="items"></div>` }) });
  const src = sourceInput($("#src"));
  const items = [];
  let path = "";
  const redraw = overlayEditor($("#stage"), $("#items"), items);
  bindOverlayAdd(items, redraw); redraw();
  $("#load").onclick = () => loadPreview(src, (info) => { path = info.path; $("#ed").hidden = false; $("#v").src = fileUrl(path); redraw(); });
  $("#go").onclick = (e) => { if (!items.length) return toast("Tambah minimal satu elemen", true); runJob($("#job"), "konten_tempel", { path, items: items.map((i) => ({ ...i, end: i.end ?? undefined })) }, { title: "Konten Tempel", btn: e.currentTarget }); };
} };

/* ================================================================== VIDEO EDITOR */
PAGES.editor = { render(view) {
  view.innerHTML = toolPage({ id: "editor", needs: ["ffmpeg"], desc: "Edit cepat satu video: potong, ubah kecepatan & rasio, tempel teks/gambar, caption otomatis, musik latar, dan logo.",
    main: sourcePanel("") + `<div id="ed" hidden class="stack lg">${UI.panel({ title: "2 · Potong & tempel", body: `<div class="stage" id="stage"><video id="v" controls></video></div>
      <div class="marks">${UI.btn("Tandai mulai", { size: "sm", id: "ms" })}<span class="t" id="msv">0:00.0</span>${UI.btn("Tandai selesai", { size: "sm", id: "me" })}<span class="t" id="mev">akhir</span><span class="spacer"></span>${UI.btn("Reset", { size: "sm", kind: "ghost", id: "mr" })}</div>
      ${overlayAddForm()}<div class="list" id="items"></div>` })}
      ${UI.panel({ id: "f", title: "3 · Ekspor", body: `<div class="fields">${UI.select("speed", "Kecepatan", [0.5, 0.75, 1, 1.25, 1.5, 2].map((v) => ({ v, l: v + "×" })), 1, { numeric: true })}
        ${UI.select("aspect", "Rasio", [{ v: "original", l: "Asli" }, "9:16", "1:1", "4:5", "16:9"], "original")}${UI.select("fit_mode", "Isi frame", FIT, "blur")}${captionSelect()}</div>
        ${UI.field("Musik latar", `<div id="mp"></div>`)}<div class="fields two">${UI.range("music_volume", "Volume musik", { value: 0.15, min: 0.03, max: 0.6, step: 0.01, fmt: "%" })}</div>
        <div class="switches">${UI.toggle("subtitles", "Caption otomatis", { checked: false })}${UI.toggle("brand_kit", "Logo Brand Kit")}</div>`, foot: footAction("Ekspor video", "download") })}</div>`,
    aside: UI.aside("Urutan proses", ["Potong sesuai tanda mulai/selesai.", "Ubah kecepatan.", "Ubah rasio (crop / blur / letterbox).", "Tempel teks & gambar (waktu dihitung setelah dipotong).", "Caption otomatis, musik, lalu logo."]) });
  const src = sourceInput($("#src"));
  const items = [], cut = { start: 0, end: 0 };
  let path = "", mp;
  const redraw = overlayEditor($("#stage"), $("#items"), items);
  bindOverlayAdd(items, redraw);
  musicPicker($("#mp")).then((x) => (mp = x));
  $("#ms").onclick = () => { cut.start = $("#v").currentTime; $("#msv").textContent = fmtTime(cut.start); };
  $("#me").onclick = () => { cut.end = $("#v").currentTime; $("#mev").textContent = fmtTime(cut.end); };
  $("#mr").onclick = () => { cut.start = 0; cut.end = 0; $("#msv").textContent = "0:00.0"; $("#mev").textContent = "akhir"; };
  $("#load").onclick = () => loadPreview(src, (info) => { path = info.path; $("#ed").hidden = false; $("#v").src = fileUrl(path); redraw(); });
  $("#go").onclick = (e) => {
    if (cut.end && cut.end <= cut.start) return toast("Titik selesai harus setelah titik mulai", true);
    runJob($("#job"), "editor", { ...formValues($("#f")), path, start: cut.start, end: cut.end || null, items: items.map((i) => ({ ...i, end: i.end ?? undefined })), music_path: mp ? mp.value() : "" }, { title: "Video Editor", btn: e.currentTarget });
  };
} };

/* ================================================================== MERGE */
PAGES.merge = { render(view) {
  view.innerHTML = toolPage({ id: "merge", needs: ["ffmpeg"], desc: "Gabungkan beberapa video jadi satu, atur urutan, dan beri transisi.",
    main: UI.panel({ title: "Daftar video", head: `<span class="pill" id="cnt">0</span>`, body: `<div id="up"></div>
      <div class="input-row"><input type="url" id="u" placeholder="atau tempel link video">${UI.btn("Link", { ico: "plus", id: "addU" })}${UI.btn("Dari Video Saya", { ico: "folder", id: "fromLib" })}</div><div class="list" id="list"></div>` }) +
      UI.panel({ id: "f", title: "Hasil", body: `<div class="fields">${UI.select("transition", "Transisi", STATE.catalog?.transitions || ["none", "fade"], "fade")}${UI.num("transition_sec", "Durasi transisi", { value: 0.5, min: 0.1, max: 2, step: 0.1, hint: "detik" })}
        ${UI.select("aspect", "Rasio", ["9:16", "1:1", "4:5", "16:9"], "9:16")}${UI.select("fit_mode", "Isi frame", FIT, "blur")}</div>`, foot: footAction("Gabungkan", "merge") }),
    aside: UI.aside("Tips", ["Urutan di daftar = urutan di video akhir.", "Video beda ukuran disamakan otomatis.", "Pilih transisi “none” untuk gabung tercepat."]) });
  const list = [];
  const draw = () => {
    $("#cnt").textContent = list.length;
    $("#list").innerHTML = list.map((it, i) => `<div class="list-item"><span class="idx">${i + 1}</span><div class="li-main"><b>${esc(it.label)}</b><span>${it.url ? "Link — diunduh saat proses" : "File lokal"}</span></div>
      ${UI.btn("", { ico: "up", size: "sm", kind: "ghost", title: "Naik", attrs: `data-up="${i}"` })}${UI.btn("", { ico: "down", size: "sm", kind: "ghost", title: "Turun", attrs: `data-dn="${i}"` })}${UI.btn("", { ico: "x", size: "sm", kind: "ghost", title: "Hapus", attrs: `data-d="${i}"` })}</div>`).join("")
      || UI.empty("merge", "Belum ada video", "Tambahkan minimal 2 video.");
  };
  $("#list").onclick = (e) => {
    const t = e.target.closest("button"); if (!t) return;
    const i = +(t.dataset.up ?? t.dataset.dn ?? t.dataset.d);
    if (t.dataset.d !== undefined) list.splice(i, 1);
    if (t.dataset.up !== undefined && i > 0) [list[i - 1], list[i]] = [list[i], list[i - 1]];
    if (t.dataset.dn !== undefined && i < list.length - 1) [list[i + 1], list[i]] = [list[i], list[i + 1]];
    draw();
  };
  dropzone($("#up"), { accept: "video/*", title: "Upload video", sub: "Bisa beberapa file sekaligus", multiple: true, onUploaded: (i) => { list.push({ path: i.path, label: i.name }); draw(); } });
  $("#addU").onclick = () => { const u = $("#u").value.trim(); if (u) { list.push({ url: u, label: u }); $("#u").value = ""; draw(); } };
  $("#fromLib").onclick = () => libraryPicker((rows) => { rows.forEach((a) => list.push({ path: a.path, label: a.meta?.title || a.name })); draw(); }, { multi: true });
  draw();
  $("#go").onclick = (e) => { if (list.length < 2) return toast("Butuh minimal 2 video", true); runJob($("#job"), "merge", { ...formValues($("#f")), items: list.map((x) => (x.path ? { path: x.path } : { url: x.url })) }, { title: "Gabung Video", btn: e.currentTarget }); };
} };

/* ================================================================== LOOP & WA */
PAGES.loop = { render(view) {
  view.innerHTML = toolPage({ id: "loop", needs: ["ffmpeg"], desc: "Ulangi video 2–500× atau sampai durasi target. Crossfade membuat sambungan loop mulus.",
    main: UI.panel({ id: "f", body: UI.section("Sumber video", `<div id="src"></div>`) + UI.section("Loop", `<div class="fields">${UI.num("times", "Jumlah ulang", { value: 3, min: 2, max: 500 })}
      ${UI.num("target_sec", "Atau target durasi", { value: 0, min: 0, max: 36000, hint: "detik · 0 = pakai jumlah ulang" })}${UI.num("crossfade", "Crossfade sambungan", { value: 0.5, min: 0, max: 3, step: 0.1, hint: "detik · 0 = potong biasa" })}</div>`),
      foot: footAction("Buat loop", "repeat") }),
    aside: UI.aside("Kegunaan", ["Video ambience / musik panjang.", "Background live stream.", "Konten satisfying yang diputar berulang."]) });
  const src = sourceInput($("#src"));
  $("#go").onclick = (e) => { const s = requireSource(src); if (s) runJob($("#job"), "loop", { ...formValues($("#f")), ...s }, { title: "Ulang Video", btn: e.currentTarget }); };
} };

PAGES.wa = { render(view) {
  view.innerHTML = toolPage({ id: "wa", needs: ["ffmpeg"], desc: "Video panjang dipotong per segmen & dikompres agar tetap tajam di Status WhatsApp.",
    main: UI.panel({ id: "f", body: UI.section("Sumber video", `<div id="src"></div>`) + UI.section("Status WhatsApp", `<div class="fields">${UI.select("segment_sec", "Durasi per bagian", [{ v: 30, l: "30 detik" }, { v: 60, l: "60 detik" }, { v: 90, l: "90 detik" }], 60, { numeric: true })}
      ${UI.num("max_mb", "Ukuran maks per file", { value: 15.5, min: 5, max: 64, step: 0.5, hint: "MB" })}${UI.select("aspect", "Rasio", [{ v: "9:16", l: "9:16 (Story)" }, { v: "16:9", l: "16:9" }], "9:16")}${UI.select("fit_mode", "Isi frame", FIT, "blur")}</div>`),
      foot: footAction("Optimasi untuk WA", "phone") }),
    aside: UI.aside("Kenapa perlu?", ["WhatsApp mengompres ulang video besar sampai pecah.", "File dibuat di bawah batas ukuran dengan bitrate terukur.", "Unggah berurutan: bagian 01, 02, dst."]) });
  const src = sourceInput($("#src"));
  $("#go").onclick = (e) => { const s = requireSource(src); if (s) runJob($("#job"), "wa_story", { ...formValues($("#f")), ...s }, { title: "WA Story", btn: e.currentTarget }); };
} };

/* ================================================================== TTS */
PAGES.tts = { render(view) {
  view.innerHTML = toolPage({ id: "tts", desc: "Ubah teks jadi narasi. Edge TTS gratis tanpa key; OpenAI, Gemini & ElevenLabs bila key tersedia.",
    main: UI.panel({ id: "f", body: UI.field("Teks", `<textarea name="text" rows="8" placeholder="Tulis narasi di sini…"></textarea>`, { val: `<span class="val" id="cc">0 karakter</span>` }) +
      `<div id="vp"></div>` + UI.more("Opsi lanjutan", `${UI.text("instructions", "Instruksi gaya bicara", { placeholder: "mis. semangat seperti host podcast", hint: "Hanya OpenAI & Gemini." })}
        <div class="fields two">${UI.text("name", "Nama file", { placeholder: "Opsional" })}</div>${UI.toggle("split_paragraphs", "Proses per paragraf lalu gabung", { checked: false, sub: "Untuk teks panjang" })}`),
      foot: footAction("Buat audio", "mic") }),
    aside: UI.panel({ title: "Riwayat audio", body: `<div class="list" id="hist"></div>` }) });
  let vp; voicePicker($("#vp")).then((x) => (vp = x));
  $("[name=text]").oninput = (e) => ($("#cc").textContent = `${e.target.value.length} karakter`);
  const hist = () => api("/api/assets?kind=audio&limit=20").then((rows) => {
    rows = rows.filter((r) => r.exists);
    $("#hist").innerHTML = rows.map((r) => `<div class="list-item" style="flex-wrap:wrap"><div class="li-main"><b>${esc(r.name)}</b><span>${esc(r.meta?.provider || "")} · ${fmtDur(r.meta?.duration)}</span></div>
      ${UI.btn("", { ico: "download", size: "sm", kind: "ghost", title: "Unduh", href: fileUrl(r.path, true) })}<audio controls preload="none" src="${fileUrl(r.path)}" style="width:100%;height:34px"></audio></div>`).join("") || UI.empty("mic", "Belum ada audio");
  });
  hist();
  $("#go").onclick = (e) => { const p = formValues($("#f")); if (!p.text.trim()) return toast("Teks masih kosong", true); runJob($("#job"), "tts", { ...p, ...(vp ? vp.value() : {}) }, { title: "TTS", btn: e.currentTarget, onDone: hist }); };
} };

/* ================================================================== CLONE */
PAGES.clone = { render(view) {
  view.innerHTML = toolPage({ id: "clone", needs: ["eleven"], desc: "Upload rekaman suaramu 10 detik – 3 menit, lalu pakai sebagai narator di TTS & Content Creator.",
    main: UI.panel({ id: "f", body: `<div class="fields two">${UI.text("name", "Nama suara", { placeholder: "mis. Suara Marz" })}${UI.text("description", "Deskripsi", { placeholder: "pria, hangat, santai" })}</div>
      ${UI.field("Rekaman", `<div id="up"></div>`, { hint: "Rekam di ruangan sunyi, bicara natural, tanpa musik." })}<div class="list" id="files"></div>
      ${UI.toggle("remove_noise", "Bersihkan suara latar")}`, foot: footAction("Clone suara", "voice", "Gunakan hanya suaramu sendiri atau dengan izin.") }),
    aside: UI.panel({ title: "Suara tersimpan", body: `<div class="list" id="list"></div>` }) });
  const files = [];
  dropzone($("#up"), { accept: "audio/*,video/*", title: "Upload rekaman suara", sub: "MP3, WAV, M4A, atau video", multiple: true, ico: "mic",
    onUploaded: (i) => { files.push(i); $("#files").innerHTML = files.map((f) => `<div class="list-item"><div class="li-main"><b>${esc(f.name)}</b><span>${fmtDur(f.duration)}</span></div></div>`).join(""); } });
  const load = () => api("/api/voices/cloned").then((r) => {
    $("#list").innerHTML = r.voices.map((v) => `<div class="list-item"><div class="li-main"><b>${esc(v.name)}</b><span class="num">${esc(v.voice_id)}</span></div>
      ${UI.btn("Pakai", { size: "sm", attrs: `data-tts="${esc(v.voice_id)}"` })}${UI.btn("", { ico: "trash", size: "sm", kind: "ghost", title: "Hapus", attrs: `data-del="${esc(v.voice_id)}"` })}</div>`).join("") || UI.empty("voice", "Belum ada suara clone");
  });
  $("#list").onclick = async (e) => {
    const d = e.target.closest("[data-del]"), t = e.target.closest("[data-tts]");
    if (d) confirmModal("Hapus suara ini?", "Suara dihapus dari akun ElevenLabs-mu.", "Hapus", async () => { await api(`/api/voices/cloned/${d.dataset.del}`, { method: "DELETE" }).catch(fail); load(); });
    if (t) { STATE.status.media.defaults.tts = "elevenlabs"; STATE.status.media.defaults.voice = t.dataset.tts; location.hash = "#tts"; }
  };
  load();
  $("#go").onclick = (e) => {
    const p = formValues($("#f"));
    if (!p.name.trim() || !files.length) return toast("Isi nama dan upload rekaman", true);
    busy(e.currentTarget, async () => { try { await api("/api/voices/clone", { body: { ...p, paths: files.map((f) => f.path) } }); toast("Suara berhasil di-clone"); load(); } catch (err) { fail(err); } });
  };
} };

/* ================================================================== MUSIK & SFX */
PAGES.music = { render(view) {
  view.innerHTML = `<div class="page">${pageHead("Musik & SFX", "Buat musik latar & efek suara dengan AI (ElevenLabs), atau simpan musik sendiri ke pustaka untuk Editor & Content Creator.")}${keyBanner(["eleven"])}
    <div class="cols-2">${UI.panel({ id: "fm", title: "Buat musik", body: `${UI.area("prompt", "Deskripsi musik", { rows: 3, placeholder: "lo-fi santai dengan piano lembut, 85 BPM" })}
        ${UI.area("lyrics", "Lirik", { rows: 3, hint: "Opsional — kosong = instrumental." })}<div class="fields two">${UI.num("duration", "Durasi", { value: 30, min: 10, max: 300, hint: "detik" })}</div>${UI.toggle("instrumental", "Instrumental")}`,
        foot: `<span class="spacer"></span>${UI.btn("Buat musik", { ico: "music", kind: "primary", id: "gm" })}` })}
      ${UI.panel({ id: "fs", title: "Efek suara", body: `${UI.area("prompt", "Deskripsi efek", { rows: 3, placeholder: "whoosh cepat untuk transisi, penonton stadion bersorak" })}
        <div class="fields two">${UI.num("duration", "Durasi", { value: 0, min: 0, max: 30, step: 0.5, hint: "detik · 0 = otomatis" })}</div>`,
        foot: `<span class="spacer"></span>${UI.btn("Buat SFX", { ico: "zap", kind: "primary", id: "gs" })}` })}</div>
    <div id="job" hidden></div>
    ${UI.panel({ title: "Pustaka audio", sub: "Dipakai sebagai musik latar di Editor & Content Creator.", head: `<div id="up"></div>`, body: `<div class="list" id="lib"></div>` })}</div>`;
  const lib = () => api("/api/assets?kind=music,sfx,upload_audio&limit=80").then((rows) => {
    rows = rows.filter((r) => r.exists);
    $("#lib").innerHTML = rows.map((r) => `<div class="list-item">${UI.pill(r.kind === "upload_audio" ? "upload" : r.kind)}<div class="li-main"><b>${esc(r.name)}</b></div>
      <audio controls preload="none" src="${fileUrl(r.path)}" style="height:32px;max-width:300px"></audio>${UI.btn("", { ico: "download", size: "sm", kind: "ghost", title: "Unduh", href: fileUrl(r.path, true) })}</div>`).join("") || UI.empty("music", "Pustaka masih kosong");
  });
  uploadButton($("#up"), { accept: "audio/*", label: "Upload musik", multiple: true, onUploaded: lib });
  lib();
  $("#gm").onclick = (e) => { const p = formValues($("#fm")); if (!p.prompt.trim()) return toast("Tulis deskripsi musik", true); runJob($("#job"), "music", p, { title: "Musik AI", btn: e.currentTarget, onDone: lib }); };
  $("#gs").onclick = (e) => { const p = formValues($("#fs")); if (!p.prompt.trim()) return toast("Tulis deskripsi efek", true); runJob($("#job"), "sfx", p, { title: "SFX AI", btn: e.currentTarget, onDone: lib }); };
} };

/* ================================================================== ANALITIK VIRAL */
PAGES.analytics = { render(view) {
  const cats = ["Semua", "Gaming", "Musik", "Komedi", "Hiburan", "Tutorial", "Olahraga", "Teknologi", "Keuangan", "Edukasi"];
  view.innerHTML = `<div class="page">${pageHead("Analitik Viral", "Video yang sedang naik di YouTube Indonesia, skor viral, estimasi pendapatan, dan jam upload terbaik.", UI.btn("Perbarui", { ico: "refresh", size: "sm", id: "rf" }))}
    <div id="cat"></div><div id="out" class="stack lg">${UI.empty("chart", "Memuat data…")}</div></div>`;
  let cat = store.get("anaCat", "Semua");
  seg($("#cat"), cats.map((c) => ({ v: c, l: c })), cat, (v) => { cat = v; store.set("anaCat", v); load(false); });
  async function load(refresh) {
    $("#out").innerHTML = UI.panel({ body: UI.empty("chart", `Mengambil tren ${cat}…`, "Butuh 5–20 detik.") });
    try {
      const d = await api(`/api/analytics?category=${encodeURIComponent(cat)}&refresh=${refresh}`);
      const s = d.summary;
      $("#out").innerHTML = `<div class="cols-4">${UI.stat("Rata-rata views", fmtNum(s.avg_views))}${UI.stat("Est. pendapatan / video", fmtIDR(s.est_revenue_idr), "estimasi kasar")}
        ${UI.stat("Skor viral rata-rata", `${s.viral_score}<small>/100</small>`)}${UI.stat("Persaingan", esc(s.competition))}</div>
        <div class="cols-2">${UI.panel({ title: "Waktu upload terbaik", body: `<dl class="kv" style="grid-template-columns:auto 1fr">${Object.entries(d.best_times).map(([pf, t]) => `<dt>${esc(pf)}</dt><dd><div class="row" style="justify-content:flex-end;gap:6px">${t.map((x) => UI.pill(x, "teal")).join("")}</div></dd>`).join("")}</dl>` })}
          ${UI.panel({ title: "Hashtag teratas", body: `<div class="row">${d.top_hashtags.map((h) => UI.pill("#" + h, "accent")).join("")}</div><p class="tiny muted">${esc(d.note)} Sumber: ${esc(d.source)}.</p>` })}</div>
        ${UI.panel({ title: "Video trending", sub: `${d.videos.length} video`, bodyCls: "flush", body: `<div class="table-wrap"><table><thead><tr><th>Video</th><th class="r">Skor</th><th class="r">Views</th><th class="r hide-sm">Engagement</th><th class="r hide-sm">Est. pendapatan</th><th></th></tr></thead><tbody>
          ${d.videos.map((v) => `<tr><td><div class="row nw">${v.thumbnail ? `<img src="${esc(v.thumbnail)}" alt="" loading="lazy" style="width:96px;aspect-ratio:16/9;object-fit:cover;border-radius:8px;flex-shrink:0">` : ""}
            <div style="min-width:0"><a href="${esc(v.url)}" target="_blank" rel="noopener" style="font-weight:650;text-decoration:none;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden">${esc(v.title)}</a>
            <div class="row" style="gap:6px;margin-top:2px"><span class="tiny muted">${esc(v.channel)}</span>${v.rising ? UI.pill("naik cepat", "ok") : ""}</div></div></div></td>
            <td class="num"><b>${v.viral_score}</b></td><td class="num">${fmtNum(v.views)}</td><td class="num hide-sm">${v.engagement == null ? "—" : v.engagement + "%"}</td><td class="num hide-sm">${fmtIDR(v.est_revenue_idr)}</td>
            <td class="r">${UI.btn("Buat klip", { size: "sm", href: `#analisis?url=${encodeURIComponent(v.url)}` })}</td></tr>`).join("")}</tbody></table></div>` })}`;
    } catch (e) { $("#out").innerHTML = UI.banner("bad", esc(e.message)); }
  }
  $("#rf").onclick = () => load(true);
  load(false);
} };

/* ================================================================== SARAN VIRAL */
PAGES.ideas = { render(view) {
  view.innerHTML = `<div class="page">${pageHead("Saran Viral AI", "AI membaca judul yang sedang ramai di niche-mu lalu memberi pola viral dan ide konten siap eksekusi.")}${keyBanner(["llm"])}
    ${UI.panel({ id: "f", body: `<div class="fields" style="grid-template-columns:minmax(0,2fr) minmax(0,1fr) 120px">${UI.text("niche", "Niche", { placeholder: "mis. keuangan anak muda, horor, resep murah" })}
      ${UI.select("platform", "Platform", ["TikTok & YouTube Shorts", "TikTok", "YouTube Shorts", "Instagram Reels"], "TikTok & YouTube Shorts")}${UI.num("count", "Jumlah ide", { value: 10, min: 3, max: 20 })}</div>`,
      foot: footAction("Cari ide", "bulb") })}<div id="out" class="stack lg"></div></div>`;
  $("#go").onclick = (e) => {
    const p = formValues($("#f")); if (!p.niche.trim()) return toast("Isi niche dulu", true);
    busy(e.currentTarget, async () => {
      $("#out").innerHTML = UI.panel({ body: UI.empty("bulb", "AI sedang menganalisis tren…", "Butuh 10–40 detik.") });
      try {
        const d = await api("/api/tools/viral-ideas", { body: { params: p } });
        $("#out").innerHTML = (d.patterns?.length ? UI.panel({ title: "Pola viral yang terlihat", body: `<ul class="ink2" style="display:grid;gap:6px">${d.patterns.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>` }) : "") +
          `<div class="cols-2">${(d.ideas || []).map((i, n) => UI.panel({ title: i.title, sub: [i.format, i.difficulty].filter(Boolean).map(esc).join(" · "),
            body: `<p><b class="small">Hook</b><br><span class="ink2">${esc(i.hook)}</span></p><p class="small muted">${esc(i.why)}</p>`,
            foot: `${i.tool ? UI.pill(i.tool, "teal") : ""}<span class="spacer"></span>${UI.btn("Buat di Content Creator", { size: "sm", ico: "wand", attrs: `data-i="${n}"` })}` })).join("")}</div>` +
          (d.avoid?.length ? UI.banner("warn", `<b>Hindari:</b> ${d.avoid.map(esc).join(" · ")}`) : "");
        $("#out").onclick = (ev) => { const b = ev.target.closest("[data-i]"); if (!b) return; const i = d.ideas[+b.dataset.i]; STATE.prefill.creator = { source: `${i.title}\nHook: ${i.hook}`, source_type: "topik" }; location.hash = "#creator"; };
      } catch (err) { $("#out").innerHTML = UI.banner("bad", esc(err.message)); }
    });
  };
} };

/* ================================================================== OPTIMASI JUDUL */
PAGES.title = { render(view) {
  view.innerHTML = toolPage({ id: "title", needs: ["llm"], desc: "4 varian judul (SEO, Viral, CTR, Balanced) + deskripsi 250–300 karakter + 15 tag + teks thumbnail.",
    main: UI.panel({ id: "f", body: `<div id="mode"></div><div data-m="text">${UI.area("text", "Judul / topik / ringkasan", { rows: 4, placeholder: "Judul sekarang, topik, atau ringkasan video…" })}</div>
      <div data-m="url" hidden>${UI.text("url", "Link YouTube", { type: "url", placeholder: "https://youtu.be/…", hint: "AI membaca judul, deskripsi & transkripnya." })}</div>
      <div class="fields two">${UI.select("platform", "Platform", ["YouTube", "YouTube Shorts", "TikTok", "Instagram Reels"], "YouTube")}${UI.select("language", "Bahasa", ["Bahasa Indonesia", "English"], "Bahasa Indonesia")}</div>`,
      foot: footAction("Optimasi", "heading") }),
    aside: UI.aside("Yang kamu dapat", ["Judul SEO — kata kunci yang dicari.", "Judul Viral — memancing rasa penasaran.", "Judul CTR — pendek & tajam.", "Balanced — gabungan ketiganya.", "Deskripsi, 15 tag, dan 3 opsi teks thumbnail."]) });
  let mode = "text";
  seg($("#mode"), [{ v: "text", l: "Teks / topik", ico: "pen" }, { v: "url", l: "Link YouTube", ico: "link" }], "text", (v) => { mode = v; $$("[data-m]").forEach((x) => (x.hidden = x.dataset.m !== v)); });
  $("#go").onclick = (e) => {
    const p = formValues($("#f")); if (mode === "text") p.url = ""; else p.text = "";
    if (!p.text.trim() && !p.url.trim()) return toast("Isi teks atau link", true);
    busy(e.currentTarget, async () => {
      const out = $("#job"); out.hidden = false; out.innerHTML = UI.panel({ body: UI.empty("heading", "AI sedang menulis…") });
      try {
        const d = await api("/api/tools/title", { body: { params: p } });
        const tags = (d.tags || []).join(", ");
        out.innerHTML = `<div class="stack lg"><div class="cols-2">${(d.titles || []).map((t) => UI.panel({ head: UI.btn("Salin", { ico: "copy", size: "sm", kind: "ghost", attrs: `data-copy="${esc(t.title)}"` }), title: t.type,
            body: `<h3 style="font-size:16px">${esc(t.title)}</h3><p class="small muted">${esc(t.why || "")}</p>` })).join("")}</div>
          ${UI.panel({ title: "Deskripsi", head: UI.btn("Salin", { ico: "copy", size: "sm", kind: "ghost", attrs: `data-copy="${esc(d.description)}"` }), body: `<p class="ink2">${esc(d.description)}</p>` })}
          ${UI.panel({ title: `Tag (${(d.tags || []).length})`, head: UI.btn("Salin", { ico: "copy", size: "sm", kind: "ghost", attrs: `data-copy="${esc(tags)}"` }), body: `<div class="row">${(d.tags || []).map((t) => UI.pill(t)).join("")}</div>${d.hashtags?.length ? `<div class="row">${d.hashtags.map((t) => UI.pill(t, "teal")).join("")}</div>` : ""}` })}
          ${d.thumbnail_text?.length ? UI.panel({ title: "Teks thumbnail", body: `<div class="row">${d.thumbnail_text.map((t) => UI.pill(t, "accent")).join("")}</div>` }) : ""}</div>`;
        bindCards(out);
      } catch (err) { out.innerHTML = UI.banner("bad", esc(err.message)); }
    });
  };
} };

/* ================================================================== TRANSCRIPT */
PAGES.transcript = { render(view) {
  view.innerHTML = toolPage({ id: "transcript", desc: "Transkrip video/audio jadi TXT, SRT, dan teks ber-timestamp. Link YouTube memakai subtitle bila ada (tanpa download).",
    main: UI.panel({ id: "f", body: UI.section("Sumber", `<div id="src"></div>${UI.field("Atau dokumen / audio", `<div id="doc"></div>`)}`) +
      UI.section("Opsi", `<div class="fields">${UI.select("translate_to", "Terjemahkan ke", [{ v: "", l: "Tidak" }, "Bahasa Indonesia", "English", "Bahasa Melayu", "Bahasa Jawa", "Arabic", "Japanese"], "")}
        ${UI.select("language", "Bahasa audio", LANGS.slice(0, 3), "")}${UI.select("whisper_device", "Mesin", WHISPER_DEVICES, "")}</div>
        ${UI.toggle("prefer_subtitles", "Pakai subtitle YouTube bila ada", { sub: "Jauh lebih cepat" })}`),
      foot: footAction("Buat transkrip", "doc") }),
    aside: UI.aside("Format hasil", ["<b>TXT</b> — teks polos.", "<b>SRT</b> — subtitle untuk editor video.", "<b>Timestamp</b> — siap ditempel di deskripsi YouTube.", "<b>JSON</b> — data per segmen."]) });
  const src = sourceInput($("#src"));
  let docPath = "";
  uploadButton($("#doc"), { accept: ".txt,.srt,.vtt,.pdf,audio/*", label: "Upload audio / TXT / SRT / PDF", onUploaded: (i) => { docPath = i.path; toast(`Dokumen dipilih: ${i.name}`); } });
  $("#go").onclick = (e) => {
    const s = docPath ? { path: docPath } : requireSource(src); if (!s) return;
    runJob($("#job"), "transcript", { ...dropEmpty(formValues($("#f")), ["whisper_device", "translate_to"]), ...s }, { title: "Transcript", btn: e.currentTarget });
  };
} };

/* ================================================================== THUMBNAIL */
PAGES.thumb = { render(view) {
  view.innerHTML = `<div class="page">${pageHead("Thumbnail", "Ambil thumbnail resolusi tertinggi dari video YouTube/TikTok untuk referensi desain.")}
    ${UI.panel({ body: `<div class="input-row"><input type="url" id="u" placeholder="Tempel link video">${UI.btn("Ambil", { ico: "image", kind: "primary", id: "go" })}</div>` })}<div id="out"></div></div>`;
  const go = (e) => {
    const url = $("#u").value.trim(); if (!url) return toast("Tempel link dulu", true);
    busy($("#go"), async () => {
      try {
        const d = await api("/api/tools/thumbnail", { body: { params: { url } } });
        $("#out").innerHTML = UI.panel({ title: d.title, body: `<div class="media-grid" style="grid-template-columns:repeat(auto-fill,minmax(260px,1fr))">${d.thumbnails.slice().reverse().map((t) => `<article class="media"><div class="m-thumb wide"><img src="${esc(t.url)}" alt="" loading="lazy"></div>
          <div class="m-actions"><span class="small muted num">${t.w || "?"}×${t.h || "?"}</span><span class="spacer"></span>${UI.btn("Buka ukuran penuh", { size: "sm", href: esc(t.url), attrs: 'target="_blank" rel="noopener"' })}</div></article>`).join("")}</div>` });
      } catch (err) { fail(err); }
    });
  };
  $("#go").onclick = go; $("#u").onkeydown = (e) => e.key === "Enter" && go();
} };

/* ================================================================== PROMPT */
PAGES.prompt = { render(view) {
  view.innerHTML = toolPage({ id: "prompt", needs: ["llm"], desc: "Ubah ide sederhana jadi prompt profesional untuk generator gambar (Midjourney, Flux, Gemini) atau video AI (Veo, Kling, Runway).",
    main: UI.panel({ id: "f", body: `<div id="tg"></div>${UI.area("idea", "Ide", { rows: 3, placeholder: "mis. kucing astronot minum kopi di bulan, gaya poster film" })}
      <div class="fields three">${UI.text("style", "Gaya", { placeholder: "Opsional" })}${UI.select("aspect", "Rasio", ["9:16", "16:9", "1:1", "4:5"], "9:16")}${UI.num("count", "Jumlah variasi", { value: 4, min: 1, max: 8 })}</div>`,
      foot: footAction("Buat prompt", "pen") }),
    aside: UI.aside("Tips", ["Sebut subjek, aksi, dan suasana.", "Untuk video, sebut gerak kamera (dolly, pan, drone).", "Salin prompt lalu tempel ke generator favoritmu."]) });
  seg($("#tg"), [{ v: "gambar", l: "Gambar", ico: "image" }, { v: "video", l: "Video", ico: "film" }], "gambar");
  $("#go").onclick = (e) => {
    const p = { ...formValues($("#f")), target: $("#tg").dataset.value }; if (!p.idea.trim()) return toast("Tulis ide dulu", true);
    busy(e.currentTarget, async () => {
      try {
        const d = await api("/api/tools/prompt", { body: { params: p } });
        const out = $("#job"); out.hidden = false;
        out.innerHTML = `<div class="cols-2">${(d.prompts || []).map((x) => UI.panel({ title: x.title, head: UI.btn("Salin", { ico: "copy", size: "sm", kind: "ghost", attrs: `data-copy="${esc(x.prompt)}"` }),
          body: `<p class="mono" style="white-space:pre-wrap;line-height:1.6">${esc(x.prompt)}</p>${x.negative ? `<p class="small muted"><b>Negative:</b> ${esc(x.negative)}</p>` : ""}${x.notes ? `<p class="small ink2">${esc(x.notes)}</p>` : ""}` })).join("")}</div>`;
        bindCards(out);
      } catch (err) { fail(err); }
    });
  };
} };

/* ================================================================== CHAT */
PAGES.chat = { render(view) {
  const ideas = ["Buatkan 5 hook untuk video tabungan anak kos", "Jam berapa upload terbaik untuk niche gaming?", "Ide seri konten 7 hari tentang sejarah Nusantara"];
  view.innerHTML = `<div class="page">${pageHead("Chat AI", "Teman brainstorming: ide konten, naskah, hook, strategi upload, dan cara memakai fitur studio.", UI.btn("Percakapan baru", { ico: "plus", size: "sm", id: "clr" }))}${keyBanner(["llm"])}
    <section class="panel chat"><div class="chat-log" id="log"></div><form class="chat-input" id="frm"><textarea id="inp" rows="1" placeholder="Tanya apa saja…  (Enter kirim · Shift+Enter baris baru)"></textarea>${UI.btn("Kirim", { ico: "send", kind: "primary", id: "send" })}</form></section></div>`;
  let msgs = store.get("chat", []);
  const draw = () => {
    $("#log").innerHTML = msgs.length ? msgs.map((m) => `<div class="msg ${m.role === "user" ? "user" : "bot"}">${esc(m.content)}</div>`).join("")
      : UI.empty("chat", "Mulai percakapan", "Contoh pertanyaan:", `<div class="suggests">${ideas.map((t) => `<button class="chip" type="button" data-q="${esc(t)}">${esc(t)}</button>`).join("")}</div>`);
    $("#log").scrollTop = $("#log").scrollHeight;
  };
  draw();
  const send = async () => {
    const text = $("#inp").value.trim(); if (!text) return;
    msgs.push({ role: "user", content: text }); $("#inp").value = ""; draw();
    await busy($("#send"), async () => {
      try { const r = await api("/api/tools/chat", { body: { params: { messages: msgs } } }); msgs.push({ role: "assistant", content: r.reply }); }
      catch (e) { msgs.push({ role: "assistant", content: "Gagal: " + e.message }); }
    });
    store.set("chat", msgs.slice(-60)); draw();
  };
  $("#log").onclick = (e) => { const q = e.target.closest("[data-q]"); if (q) { $("#inp").value = q.dataset.q; send(); } };
  $("#frm").onsubmit = (e) => { e.preventDefault(); send(); };
  $("#send").onclick = send;
  $("#inp").onkeydown = (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } };
  $("#clr").onclick = () => { msgs = []; store.set("chat", []); draw(); };
} };

/* ================================================================== AUTO UPLOAD */
PAGES.upload = { render(view) {
  view.innerHTML = `<div class="page">${pageHead("Jadwal Upload", "Jadwalkan klip ke YouTube Shorts & TikTok. Server mengunggah otomatis saat jadwal tiba (laptop harus menyala).",
    UI.btn("Jadwalkan klip", { ico: "plus", id: "addOne" }) + UI.btn("Smart Cadence", { ico: "calendar", kind: "primary", id: "addBulk" }))}
    <div class="cols-2" id="pf"></div>
    ${UI.panel({ head: `<div class="row nw">${UI.btn("", { ico: "left", size: "sm", title: "Bulan sebelumnya", id: "prev" })}<h2 id="mon" style="min-width:150px;text-align:center"></h2>${UI.btn("", { ico: "right", size: "sm", title: "Bulan berikutnya", id: "next" })}${UI.btn("Hari ini", { size: "sm", kind: "ghost", id: "today" })}</div><span class="spacer"></span><div class="row" id="counts"></div>`,
      bodyCls: "flush", body: `<div class="cal" id="cal"></div>` })}
    ${UI.panel({ title: "Daftar jadwal", bodyCls: "flush", body: `<div class="table-wrap"><table><thead><tr><th>Klip</th><th>Platform</th><th>Waktu</th><th>Status</th><th></th></tr></thead><tbody id="tb"></tbody></table></div>` })}</div>`;
  const pf = STATE.status?.platforms || {};
  $("#pf").innerHTML = Object.entries(pf).map(([, v]) => UI.panel({ title: v.label, head: v.ready ? UI.pill("Terhubung", "ok") : UI.pill("Belum diset", "warn"),
    body: v.ready ? `<p class="small muted">Siap mengunggah.</p>` : `<p class="small ink2">${esc(v.hint)}</p>${UI.btn("Buka Pengaturan", { size: "sm", href: "#settings", ico: "sliders" })}` })).join("");
  let cursor = new Date(); cursor.setDate(1);
  let items = [];
  const STL = { pending: "Menunggu", uploading: "Mengunggah", done: "Berhasil", failed: "Gagal", paused: "Dijeda" };
  const STK = { pending: "", uploading: "accent run", done: "ok", failed: "bad", paused: "warn" };
  function drawCal() {
    $("#mon").textContent = cursor.toLocaleDateString("id-ID", { month: "long", year: "numeric" });
    const start = new Date(cursor); start.setDate(1 - cursor.getDay());
    const todayKey = new Date().toDateString();
    let html = ["Min", "Sen", "Sel", "Rab", "Kam", "Jum", "Sab"].map((d) => `<div class="dow">${d}</div>`).join("");
    for (let i = 0; i < 42; i++) {
      const d = new Date(start); d.setDate(start.getDate() + i);
      const ev = items.filter((s) => new Date(s.run_at * 1000).toDateString() === d.toDateString());
      html += `<div class="day ${d.getMonth() !== cursor.getMonth() ? "out" : ""} ${d.toDateString() === todayKey ? "today" : ""}"><span class="d">${d.getDate()}</span>
        ${ev.map((s) => `<span class="ev ${s.status}" title="${esc(s.title)}">${new Date(s.run_at * 1000).toLocaleTimeString("id-ID", { hour: "2-digit", minute: "2-digit" })} ${esc(s.title)}</span>`).join("")}</div>`;
    }
    $("#cal").innerHTML = html;
    const c = (st) => items.filter((s) => s.status === st).length;
    $("#counts").innerHTML = UI.pill(`${items.length} total`) + UI.pill(`${c("pending")} menunggu`) + UI.pill(`${c("done")} berhasil`, "ok") + (c("failed") ? UI.pill(`${c("failed")} gagal`, "bad") : "");
  }
  async function load() {
    items = await api("/api/schedules");
    drawCal();
    $("#tb").innerHTML = items.map((s) => `<tr data-id="${s.id}"><td style="max-width:360px"><b class="clip1" style="display:block">${esc(s.title || baseName(s.file_path))}</b>${s.result ? `<span class="tiny muted clip1" style="display:block">${esc(JSON.stringify(s.result))}</span>` : ""}</td>
      <td><div class="row" style="gap:4px">${s.platforms.map((p) => UI.pill(p)).join("")}</div></td><td class="small nowrap">${fmtDate(s.run_at)}</td>
      <td><span class="pill ${STK[s.status] || ""}">${s.status === "uploading" ? '<span class="dot"></span>' : ""}${STL[s.status] || s.status}</span></td>
      <td class="r"><div class="btn-row" style="justify-content:flex-end;flex-wrap:nowrap">${s.status !== "done" ? UI.btn("Upload sekarang", { size: "sm", attrs: 'data-a="now"' }) : ""}
        ${s.status === "pending" ? UI.btn("", { ico: "pause", size: "sm", kind: "ghost", title: "Jeda", attrs: 'data-a="pause"' }) : ["paused", "failed"].includes(s.status) ? UI.btn("", { ico: "play", size: "sm", kind: "ghost", title: "Antrekan lagi", attrs: 'data-a="resume"' }) : ""}
        ${UI.btn("", { ico: "trash", size: "sm", kind: "ghost", title: "Hapus", attrs: 'data-a="del"' })}</div></td></tr>`).join("")
      || `<tr><td colspan="5">${UI.empty("calendar", "Belum ada jadwal", "Klik Jadwalkan klip atau Smart Cadence untuk mulai.")}</td></tr>`;
  }
  $("#tb").onclick = async (e) => {
    const b = e.target.closest("[data-a]"); if (!b) return;
    const id = b.closest("tr").dataset.id;
    await busy(b, async () => {
      try {
        if (b.dataset.a === "now") { const r = await api(`/api/schedules/${id}/run-now`, { body: {} }); toast(r.status === "done" ? "Upload berhasil" : "Upload gagal — lihat detail di tabel", r.status !== "done"); }
        if (b.dataset.a === "pause") await api(`/api/schedules/${id}`, { method: "PATCH", body: { status: "paused" } });
        if (b.dataset.a === "resume") await api(`/api/schedules/${id}`, { method: "PATCH", body: { status: "pending" } });
        if (b.dataset.a === "del") await api(`/api/schedules/${id}`, { method: "DELETE" });
      } catch (err) { fail(err); }
    });
    load();
  };
  $("#prev").onclick = () => { cursor.setMonth(cursor.getMonth() - 1); drawCal(); };
  $("#next").onclick = () => { cursor.setMonth(cursor.getMonth() + 1); drawCal(); };
  $("#today").onclick = () => { cursor = new Date(); cursor.setDate(1); drawCal(); };
  $("#addOne").onclick = () => libraryPicker((a) => scheduleModal({ asset_id: a.id, onSaved: load }), { kinds: "clip,video" });
  $("#addBulk").onclick = () => libraryPicker((rows) => bulkScheduleModal(rows.map((r) => r.id), load), { kinds: "clip,video", multi: true, title: "Pilih klip untuk Smart Cadence" });
  load().catch(fail);
} };

/* ================================================================== BRAND KIT */
PAGES.brand = { render(view) {
  view.innerHTML = `<div class="page">${pageHead("Brand Kit", "Logo merekmu, terpasang otomatis di semua klip: Analisis AI, Atur Manual, Split Cam, Highlight, Editor, dan video AI.")}
    <div class="layout" style="grid-template-columns:minmax(0,1fr) minmax(0,1fr)">
      ${UI.panel({ title: "Pratinjau", body: `<div class="phone" id="ph"><img class="logo" id="lg" alt="Logo"><div class="ph-cap">Ini rahasia kopi murah<br>yang bikin antre</div><div class="ph-safe" id="safe" hidden></div></div>
        <label class="switch" style="justify-content:center"><input type="checkbox" id="sz"><span>Tampilkan area tombol TikTok/Reels</span></label>` })}
      ${UI.panel({ title: "Pengaturan", body: UI.section("Logo", `<div id="up"></div><div class="row" id="logoRow" hidden><span class="small muted">Logo terpasang.</span><span class="spacer"></span>${UI.btn("Hapus logo", { size: "sm", kind: "danger", ico: "trash", id: "rm" })}</div>`) +
        UI.section("Posisi", `<div id="pos"></div><p class="tiny muted">Pojok bawah tertutup tombol & caption platform. Pilih “Atur sendiri” lalu geser logo di pratinjau.</p>`) +
        UI.section("Tampilan", `<div class="fields two">${UI.field("Ukuran", `<input type="range" id="size" min="0.05" max="0.6" step="0.01">`, { val: '<span class="val" id="szv"></span>' })}
          ${UI.field("Transparansi", `<input type="range" id="op" min="0.1" max="1" step="0.05">`, { val: '<span class="val" id="opv"></span>' })}</div>
          <label class="switch"><input type="checkbox" id="auto"><span>Pasang otomatis di semua klip<span class="sub">Bisa dimatikan per proses.</span></span></label>`),
        foot: `<span class="spacer"></span>${UI.btn("Simpan", { ico: "check", kind: "primary", id: "save" })}` })}</div></div>`;
  let kit = {}, logoPath = "";
  const ph = $("#ph"), lg = $("#lg");
  const place = () => {
    lg.style.width = kit.size * 100 + "%"; lg.style.opacity = kit.opacity;
    lg.style.transform = ""; lg.style.left = lg.style.right = lg.style.top = "";
    if (kit.position === "custom") { lg.style.left = (kit.x ?? 0.05) * 100 + "%"; lg.style.top = (kit.y ?? 0.06) * 100 + "%"; }
    else if (kit.position === "center") { lg.style.left = "50%"; lg.style.top = "50%"; lg.style.transform = "translate(-50%,-50%)"; }
    else if (kit.position === "top-right") { lg.style.right = "4%"; lg.style.top = "7%"; }
    else { lg.style.left = "4%"; lg.style.top = "7%"; }
    lg.style.cursor = kit.position === "custom" ? "move" : "default";
    $("#szv").textContent = Math.round(kit.size * 100) + "%"; $("#opv").textContent = Math.round(kit.opacity * 100) + "%";
  };
  const showLogo = (src) => { lg.src = src; lg.style.display = "block"; $("#logoRow").hidden = false; };
  lg.addEventListener("pointerdown", (e) => {
    if (kit.position !== "custom") return;
    e.preventDefault(); const r = ph.getBoundingClientRect(); lg.setPointerCapture(e.pointerId);
    const sx = e.clientX, sy = e.clientY, ox = kit.x ?? 0.05, oy = kit.y ?? 0.06;
    const mv = (ev) => { kit.x = Math.min(Math.max(0, ox + (ev.clientX - sx) / r.width), 1 - kit.size); kit.y = Math.min(Math.max(0, oy + (ev.clientY - sy) / r.height), 0.9); place(); };
    const up = () => { lg.removeEventListener("pointermove", mv); lg.removeEventListener("pointerup", up); };
    lg.addEventListener("pointermove", mv); lg.addEventListener("pointerup", up);
  });
  dropzone($("#up"), { accept: "image/png,image/jpeg,image/webp", title: "Upload logo", sub: "PNG transparan paling rapi · maks 5 MB", ico: "image", onUploaded: (i) => { logoPath = i.path; showLogo(fileUrl(i.path)); place(); } });
  api("/api/brand-kit").then((k) => {
    kit = { position: "top-left", size: 0.22, opacity: 0.9, auto: true, ...k };
    if (k.has_logo) showLogo(fileUrl(k.logo) + "&t=" + Date.now());
    $("#size").value = kit.size; $("#op").value = kit.opacity; $("#auto").checked = kit.auto;
    seg($("#pos"), [{ v: "top-left", l: "Kiri atas" }, { v: "top-right", l: "Kanan atas" }, { v: "center", l: "Tengah" }, { v: "custom", l: "Atur sendiri" }], kit.position, (v) => { kit.position = v; place(); }, { block: true });
    place();
  });
  $("#size").oninput = (e) => { kit.size = +e.target.value; place(); };
  $("#op").oninput = (e) => { kit.opacity = +e.target.value; place(); };
  $("#sz").onchange = (e) => ($("#safe").hidden = !e.target.checked);
  $("#rm").onclick = () => confirmModal("Hapus logo?", "Klip berikutnya tidak akan diberi logo.", "Hapus", async () => { await api("/api/brand-kit", { body: { ...kit, logo: null, remove_logo: true } }).catch(fail); lg.style.display = "none"; $("#logoRow").hidden = true; logoPath = ""; toast("Logo dihapus"); });
  $("#save").onclick = (e) => busy(e.currentTarget, async () => {
    try { await api("/api/brand-kit", { body: { logo: logoPath || null, position: kit.position, size: kit.size, opacity: kit.opacity, auto: $("#auto").checked, x: kit.x ?? null, y: kit.y ?? null } }); toast("Brand Kit tersimpan"); }
    catch (err) { fail(err); }
  });
} };

/* ================================================================== SETTINGS */
const PRETTY = { edge: "Edge", openai: "OpenAI", gemini: "Gemini", elevenlabs: "ElevenLabs", pollinations: "Pollinations", card: "Kartu teks" };
PAGES.settings = { render(view) {
  const st = STATE.status;
  const pill = (ok, l) => UI.pill((ok ? "✓ " : "") + l, ok ? "ok" : "");
  view.innerHTML = `<div class="page">${pageHead("Pengaturan", "Disimpan ke file .env di laptopmu dan langsung aktif tanpa restart. API key tidak pernah ditampilkan utuh.",
      UI.btn("Setel otomatis (terbaik)", { ico: "sparkles", kind: "primary", size: "sm", id: "autoCfg" }))}
    ${UI.banner("info", "Bingung? Klik <b>Setel otomatis</b> — sistem membaca API key yang terpasang lalu memilih provider, model, suara, dan mesin transkripsi terbaik. Setelah itu cukup tempel link di Beranda.")}
    <div class="layout"><div class="stack lg"><div id="conns"></div><div class="stack lg" id="groups">${UI.panel({ body: UI.empty("sliders", "Memuat pengaturan…") })}</div></div>
    <aside>${st ? UI.panel({ title: "Kemampuan aktif", body: `<div class="stack">${[["LLM", st.ai.providers.map((p) => pill(p.configured, p.provider))], ["Suara", Object.entries(st.media.tts).map(([k, v]) => pill(v.ready, PRETTY[k] || k))],
        ["Gambar", Object.entries(st.media.image).map(([k, v]) => pill(v.ready, PRETTY[k] || k))], ["Lainnya", [pill(st.media.elevenlabs, "ElevenLabs"), pill(st.media.stock, "Stok"), pill(st.media.youtube_api, "YouTube API"), pill(st.ffmpeg, "FFmpeg")]]]
        .map(([k, list]) => `<div class="stack" style="gap:6px"><span class="section-title">${k}</span><div class="row" style="gap:6px">${list.join("")}</div></div>`).join("")}</div>` }) : ""}
      ${st ? UI.panel({ title: "Tes provider AI", body: `${UI.field("Provider", `<select id="tp">${UI.options(st.ai.providers.map((p) => p.provider), st.ai.provider)}</select>`)}${UI.field("Model", `<select id="tm">${UI.options(aiModelOptions(st.ai.provider), "")}</select>`)}
        <div class="row">${UI.btn("Tes koneksi", { size: "sm", id: "tbtn" })}${UI.btn("Deteksi model", { ico: "refresh", size: "sm", id: "dbtn" })}</div><div id="aiRes" class="small"></div>` }) : ""}</aside></div>
    <div style="position:sticky;bottom:16px;display:flex;justify-content:flex-end;margin-top:4px">${UI.btn("Simpan pengaturan", { ico: "check", kind: "primary", size: "lg", id: "save" })}</div></div>`;
  if (st) {
    $("#tbtn").onclick = (e) => busy(e.currentTarget, async () => { try { const r = await api("/test-ai", { body: { provider: $("#tp").value, model: $("#tm").value } }); $("#aiRes").innerHTML = `${UI.pill(r.valid ? "OK" : "Gagal", r.valid ? "ok" : "bad")} <span class="ink2">${esc(r.message)}</span>`; } catch (err) { $("#aiRes").textContent = err.message; } });
    $("#tp").onchange = () => { $("#tm").innerHTML = UI.options(aiModelOptions($("#tp").value), ""); $("#aiRes").textContent = ""; };
    $("#dbtn").onclick = (e) => busy(e.currentTarget, async () => {
      try {
        const prov = $("#tp").value, r = await api("/detect-models", { body: { provider: prov } });
        await refreshStatus();
        $("#tm").innerHTML = UI.options(aiModelOptions(prov), r.recommended || "");
        if (schema?.models) { schema.models[prov] = { ...(schema.models[prov] || {}), models: r.models, detected: true };
          const ps = $("[data-key=AI_PROVIDER]"), lm = $("#llmModel");
          if (ps && lm && ps.value === prov) { lm.innerHTML = modelOpts(prov, lm.value); $("#llmHint").textContent = modelHint(prov); } }
        $("#aiRes").innerHTML = `${UI.pill(`${r.total} model`, "ok")} <span class="ink2">sudah masuk ke dropdown Model.</span>`;
      } catch (err) { $("#aiRes").innerHTML = UI.pill("Gagal", "bad") + " " + esc(err.message); }
    });
  }
  $("#autoCfg").onclick = (e) => busy(e.currentTarget, async () => {
    try {
      const rec = await api("/config/recommended");
      const rows = Object.entries(rec.values).map(([k, v]) => `<tr><td class="small">${esc(k)}</td><td class="small"><b>${esc(v === true ? "aktif" : v === false ? "mati" : v === "" ? "(otomatis/kosong)" : String(v))}</b></td></tr>`).join("");
      modal({ title: "Setelan terbaik untuk key kamu", sub: "Ditulis ke .env dan langsung aktif.", lg: true,
        body: `<ul class="stack" style="gap:6px;margin:0 0 14px;padding-left:18px">${rec.notes.map((n) => `<li>${esc(n)}</li>`).join("")}</ul>
          <div class="table-wrap"><table><tbody>${rows}</tbody></table></div>`,
        foot: UI.btn("Batal", { attrs: "data-close" }) + UI.btn("Terapkan", { ico: "check", kind: "primary", attrs: "data-ok" }) }, (m) => {
        $("[data-ok]", m).onclick = (ev) => busy(ev.currentTarget, async () => {
          try { await api("/config/recommended", { body: {} }); closeModal(); toast("Setelan terbaik diterapkan"); await refreshStatus(); route(); }
          catch (err) { fail(err); }
        });
      });
    } catch (err) { fail(err); }
  });
  let schema = null;
  const clear = new Set();
  const modelOpts = (prov, cur) => {
    const info = schema?.models?.[prov] || {};
    const list = info.models || [];
    const opts = [{ v: "", l: `Default (${info.default || "otomatis"})` }, ...list.map((m) => ({ v: m, l: m }))];
    if (cur && !list.includes(cur)) opts.push({ v: cur, l: `${cur} (tersimpan)` });
    return UI.options(opts, cur);
  };
  const imgModelOpts = (prov, cur) => {
    const c = (schema?.connections || []).find((x) => x.name === prov);
    if (!c) return UI.options([{ v: cur || "", l: cur || "Hanya untuk Koneksi API" }], cur || "");
    return UI.options([{ v: "", l: `Default (${c.image_model || "-"})` }, ...(c.image_models || []).map((m) => ({ v: m, l: m }))], cur);
  };
  const modelHint = (prov) => { const info = schema?.models?.[prov] || {};
    return info.detected ? `${(info.models || []).length} model terdeteksi dari key ${prov}.` : "Daftar bawaan. Klik Deteksi untuk mengambil model yang bisa dipakai key-mu."; };
  const detectModels = async (prov, btn) => busy(btn, async () => {
    try {
      const r = await api("/detect-models", { body: { provider: prov } });
      schema.models[prov] = { ...(schema.models[prov] || {}), models: r.models, detected: true };
      toast(`${r.total} model ${prov} masuk ke daftar`);
      return r;
    } catch (err) { fail(err); }
  });
  /* ---------- Koneksi API: beberapa base URL + API key, model dipilih dari dropdown */
  const host = (u) => String(u || "").replace(/^https?:\/\//, "").split("/")[0];
  function renderConns(list) {
    const el = $("#conns"); if (!el) return;
    const cards = list.map((c) => `<div class="conn" data-id="${esc(c.id)}">
        <div class="row" style="gap:10px;flex-wrap:wrap"><b data-name>${esc(c.name)}</b><span class="tiny muted">${esc(host(c.base_url))} · key ${esc(c.api_key_masked)}</span>
          ${UI.pill(c.format === "anthropic" ? "format Anthropic" : "format OpenAI")}${UI.pill(`${(c.models || []).length} model chat`, (c.models || []).length ? "ok" : "")}${(c.image_models || []).length ? UI.pill(`${c.image_models.length} model gambar`, "ok") : ""}
          <span class="spacer"></span>${UI.btn("", { ico: "refresh", size: "sm", kind: "ghost", title: "Deteksi ulang model", attrs: 'data-c="detect"' })}${UI.btn("", { ico: "trash", size: "sm", kind: "ghost", title: "Hapus koneksi", attrs: 'data-c="del"' })}</div>
        <div class="fields two" style="margin-top:12px">
          ${UI.field("Model AI (cari momen, judul, caption)", `<div class="input-row"><select data-cm="default_model">${UI.options((c.models || []).map((m) => ({ v: m, l: m })), c.default_model)}</select>${UI.btn("Pakai", { size: "sm", attrs: 'data-c="use-ai"', title: "Jadikan provider AI utama" })}</div>`)}
          ${(c.image_models || []).length ? UI.field("Model gambar", `<div class="input-row"><select data-cm="image_model">${UI.options(c.image_models.map((m) => ({ v: m, l: m })), c.image_model)}</select>${UI.btn("Pakai", { size: "sm", attrs: 'data-c="use-img"', title: "Jadikan mesin gambar" })}</div>`) : `<div class="hint small muted" style="align-self:end">Tidak ada model gambar di endpoint ini.</div>`}
        </div></div>`).join('<hr style="border:0;border-top:1px solid var(--line);margin:14px 0">');
    el.innerHTML = UI.panel({ title: "Koneksi API", sub: "Simpan beberapa API key sekaligus (gateway Claude/GPT, OpenRouter, server sendiri). Model terdeteksi otomatis dan muncul di dropdown provider, model, dan mesin gambar.",
      head: UI.btn("Tambah koneksi", { ico: "plus", size: "sm", kind: "primary", attrs: 'data-c="add"' }),
      body: cards || UI.empty("link", "Belum ada koneksi", "Tambahkan base URL + API key, misalnya gateway yang melayani Claude dan gpt-image sekaligus.") });
  }
  const loadConns = () => api("/api/connections").then(renderConns).catch(() => {});
  const useAs = async (values, msg) => { await api("/config", { body: { values } }); toast(msg); await refreshStatus(); route(); };
  $("#conns").addEventListener("change", async (e) => {
    const sel = e.target.closest("[data-cm]"); if (!sel) return;
    const id = sel.closest(".conn").dataset.id;
    try { await api(`/api/connections/${id}`, { method: "PATCH", body: { [sel.dataset.cm]: sel.value } }); toast("Model default koneksi disimpan"); } catch (err) { fail(err); }
  });
  $("#conns").addEventListener("click", (e) => {
    const b = e.target.closest("[data-c]"); if (!b) return;
    const card = b.closest(".conn"), id = card?.dataset.id;
    const name = card ? $("[data-name]", card).textContent : "";
    const pick = (k) => $(`[data-cm="${k}"]`, card).value;
    if (b.dataset.c === "add") return connModal();
    if (b.dataset.c === "detect") return busy(b, async () => { try { const c = await api(`/api/connections/${id}/detect`, { body: {} }); toast(`${c.models.length} model chat, ${c.image_models.length} model gambar`); loadConns(); refreshStatus(); } catch (err) { fail(err); } });
    if (b.dataset.c === "del") return confirmModal(`Hapus koneksi ${name}?`, "API key-nya ikut dihapus dari laptop ini. Pengaturan yang memakai koneksi ini dikembalikan ke default.", "Hapus",
      async () => { await api(`/api/connections/${id}`, { method: "DELETE" }).catch(fail); toast("Koneksi dihapus"); await refreshStatus(); route(); });
    if (b.dataset.c === "use-ai") return busy(b, () => useAs({ AI_PROVIDER: name, LLM_MODEL: pick("default_model") }, `AI utama: ${name} / ${pick("default_model")}`).catch(fail));
    if (b.dataset.c === "use-img") return busy(b, () => useAs({ IMAGE_PROVIDER: name, IMAGE_MODEL: pick("image_model") }, `Mesin gambar: ${name} / ${pick("image_model")}`).catch(fail));
  });
  function connModal() {
    modal({ title: "Tambah koneksi API", sub: "Base URL + API key. Model chat & gambar dideteksi otomatis.",
      body: `<div class="fields">${UI.text("name", "Nama", { placeholder: "mis. Gateway Claude", hint: "Bebas, muncul di dropdown provider." })}
        ${UI.text("base_url", "Base URL", { placeholder: "https://host.com atau https://host.com/v1", cls: "span-all" })}
        ${UI.text("api_key", "API key", { type: "password", placeholder: "sk-…", cls: "span-all" })}
        ${UI.select("format", "Format", [{ v: "auto", l: "Otomatis (disarankan)" }, { v: "openai", l: "OpenAI (/v1/chat/completions)" }, { v: "anthropic", l: "Anthropic (/v1/messages)" }], "auto")}</div>`,
      foot: UI.btn("Batal", { attrs: "data-close" }) + UI.btn("Tes & simpan", { ico: "check", kind: "primary", attrs: "data-ok" }) }, (m) => {
      $("[data-ok]", m).onclick = (ev) => busy(ev.currentTarget, async () => {
        const v = formValues(m);
        // tempelan sering membawa spasi/karakter tak terlihat (zero-width, BOM, baris baru) → bersihkan dulu
        const clean = (s) => String(s || "").replace(/[​-‍﻿\s]+/g, "");
        v.base_url = clean(v.base_url);
        v.api_key = clean(v.api_key);
        v.name = String(v.name || "").trim();
        if (v.base_url && !/^https?:\/\//i.test(v.base_url)) v.base_url = "http://" + v.base_url;  // "127.0.0.1:3425/v1"
        if (!v.base_url) return toast("Base URL belum diisi", true);
        if (!/^https?:\/\/[^/\s]+/i.test(v.base_url)) return toast("Base URL tidak valid — contoh: https://host.com/v1", true);
        if (!v.api_key) return toast("API key terbaca kosong. Bila kolom terisi otomatis oleh browser, hapus lalu tempel ulang key-nya.", true);
        try {
          const c = await api("/api/connections", { body: v });
          closeModal();
          toast(`${c.name}: ${c.models.length} model chat, ${c.image_models.length} model gambar`);
          await refreshStatus(); route();
        } catch (err) { fail(err); }
      });
    });
  }
  loadConns();

  api("/config").then((cfg) => {
    schema = cfg;
    $("#groups").innerHTML = cfg.groups.map((g) => UI.panel({ title: g, body: `<div class="fields two">${cfg.schema.filter((s) => s.group === g).map((s) => {
      const v = cfg.values[s.key] ?? "";
      if (s.key === "LLM_MODEL") return UI.field(s.label, `<div class="input-row"><select data-key="LLM_MODEL" id="llmModel">${modelOpts(cfg.values.AI_PROVIDER, v)}</select>
          ${UI.btn("Deteksi", { ico: "refresh", size: "sm", id: "llmDetect", title: "Ambil daftar model yang bisa dipakai key ini" })}</div>`,
        { hint: `<span id="llmHint">${esc(modelHint(cfg.values.AI_PROVIDER))}</span>` });
      if (s.type === "secret") { const sec = cfg.secrets[s.key] || {};
        return UI.field(s.label, `<div class="input-row"><input type="password" data-key="${s.key}" placeholder="${sec.set ? "Tersimpan — kosongkan bila tidak diubah" : "Tempel API key"}" autocomplete="off">${sec.set ? UI.btn("", { ico: "trash", size: "sm", kind: "ghost", title: "Hapus key", attrs: `data-clear="${s.key}"` }) : ""}</div>`,
          { val: `<span class="val">${sec.set ? esc(sec.masked) : "kosong"}</span>`, hint: esc(s.help || "") }); }
      if (s.type === "bool") return `<label class="switch"><input type="checkbox" data-key="${s.key}" ${v ? "checked" : ""}><span>${esc(s.label)}${s.help ? `<span class="sub">${esc(s.help)}</span>` : ""}</span></label>`;
      if (s.key === "IMAGE_MODEL") return UI.field(s.label, `<select data-key="IMAGE_MODEL" id="imgModel">${imgModelOpts(cfg.values.IMAGE_PROVIDER, v)}</select>`, { hint: esc(s.help || "") });
      if (s.key === "AI_PROVIDER") {
        const ready = new Set((st?.ai.providers || []).filter((p) => p.configured).map((p) => p.provider));
        return UI.field(s.label, `<select data-key="${s.key}">${UI.options(s.options.map((o) => ({ v: o, l: (s.labels?.[o] || o) + (ready.has(o) ? "" : " (belum ada key)") })), v)}</select>`);
      }
      if (s.type === "enum") return UI.field(s.label, `<select data-key="${s.key}">${UI.options(s.options.map((o) => ({ v: o, l: s.labels?.[o] || o || "Otomatis" })), v)}</select>`, { hint: esc(s.help || "") });
      if (s.type === "color") return UI.field(s.label, `<input type="color" data-key="${s.key}" value="${esc(v.startsWith("#") ? v : "#" + v)}">`);
      return UI.field(s.label, `<input type="${s.type === "int" || s.type === "float" ? "number" : "text"}" data-key="${s.key}" value="${esc(v)}" ${s.min != null ? `min="${s.min}" max="${s.max}"` : ""} ${s.type === "float" ? 'step="0.05"' : ""}>`, { hint: esc(s.help || "") });
    }).join("")}</div>` })).join("");
    const imgSel = $("[data-key=IMAGE_PROVIDER]", $("#groups"));
    if (imgSel && $("#imgModel")) imgSel.addEventListener("change", () => { $("#imgModel").innerHTML = imgModelOpts(imgSel.value, ""); });
    const provSel = $("[data-key=AI_PROVIDER]", $("#groups"));
    const refreshModels = () => { $("#llmModel").innerHTML = modelOpts(provSel.value, ""); $("#llmHint").textContent = modelHint(provSel.value); };
    if (provSel && $("#llmModel")) {
      provSel.addEventListener("change", refreshModels);
      $("#llmDetect").onclick = async (e) => { const cur = $("#llmModel").value; if (await detectModels(provSel.value, e.currentTarget)) { $("#llmModel").innerHTML = modelOpts(provSel.value, cur); $("#llmHint").textContent = modelHint(provSel.value); } };
    }
  }).catch(fail);
  $("#groups").onclick = (e) => { const b = e.target.closest("[data-clear]"); if (b) { clear.add(b.dataset.clear); b.closest(".field").querySelector(".val").textContent = "akan dihapus"; b.disabled = true; } };
  $("#save").onclick = (e) => busy(e.currentTarget, async () => {
    if (!schema) return;
    const values = {};
    $$("[data-key]", $("#groups")).forEach((el) => { values[el.dataset.key] = el.type === "checkbox" ? el.checked : el.value; });
    try { const r = await api("/config", { body: { values, clear: [...clear] } }); toast(`Tersimpan: ${r.saved.length} pengaturan`); clear.clear(); await refreshStatus(); route(); }
    catch (err) { fail(err); }
  });
} };
