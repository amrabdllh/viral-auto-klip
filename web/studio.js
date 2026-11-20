/* Viral Auto Klip Studio — Content Creator / Story Studio / Motion Graphic (wizard 3 langkah) */
"use strict";

const STYLE_LABEL = {
  sinematik: "Sinematik", "3d_render": "3D render", claymation: "Claymation", anime: "Anime", kartun: "Kartun ceria", komik: "Komik",
  cat_air: "Cat air", dokumenter: "Foto dokumenter", arsip: "Arsip sejarah", found_footage: "Found footage", pixel_art: "Pixel art",
  low_poly: "Low-poly", fantasi: "Fantasi epik", stickman: "Stickman", whiteboard: "Whiteboard doodle", kolase: "Kolase kertas",
  diorama: "Diorama", noir: "Komik noir",
};
const TONE_LABEL = { naratif: "Naratif & emosional", edukatif: "Edukatif step-by-step", inspiratif: "Inspiratif", fakta_viral: "Fakta viral",
  profesional: "Profesional", misteri: "Menegangkan / misteri", ceria: "Ceria & lucu", kritis: "Kritis", savage: "Savage (tetap sopan)" };
const VISUAL_MODES = [{ v: "ai_image", l: "Semua gambar AI" }, { v: "mixed", l: "Campur AI + footage stok" }, { v: "stock", l: "Footage stok" }, { v: "card", l: "Kartu teks (tanpa AI gambar)" }];
const MOTION_LABEL = { zoom_in: "Zoom masuk", zoom_out: "Zoom keluar", pan_left: "Geser kiri", pan_right: "Geser kanan", pan_up: "Geser atas", pan_down: "Geser bawah", still: "Diam" };
const SOURCE_TYPES = [{ v: "topik", l: "Topik", ico: "pen" }, { v: "youtube", l: "YouTube", ico: "play" }, { v: "artikel", l: "Artikel / PDF", ico: "doc" }, { v: "naskah", l: "Naskah sendiri", ico: "book" }];
const SOURCE_PH = { topik: "Tulis topik video… mis. “Misteri hilangnya pesawat MH370”", youtube: "Tempel link YouTube — AI membaca transkrip & metadata",
  artikel: "Tempel link artikel berita/blog, atau upload PDF/TXT di bawah", naskah: "Tempel naskahmu — dipakai apa adanya sebagai narasi (tanpa AI pun bisa)" };

const MODES = {
  creator: { desc: "Dari topik, link YouTube, artikel, atau naskahmu — AI menulis cerita per adegan, membuat gambar, narasi, subtitle, dan musik jadi satu video faceless.", tone: "naratif", style: "sinematik" },
  story: { desc: "Video bercerita bergambar: pilih gaya ilustrasi, AI menyusun adegan, gambar bergerak dengan efek kamera, lengkap dengan narator.", tone: "naratif", style: "kartun" },
  motion: { desc: "Explainer animasi dari ide atau naskah: tiap adegan punya visual preset dan teks besar yang muncul tepat saat narasi diucapkan.", tone: "edukatif", style: "" },
};

function creatorPage(mode) {
  return { render(view) {
    const M = MODES[mode], cat = STATE.catalog || {}, label = navItem(mode).label;
    const draftKey = `draft:${mode}`;
    const fresh = () => ({ step: 1, story: null, video: null, params: { mode, source_type: "topik", source: "", category: "", tone: M.tone, focus: "topik", duration: 60,
      language: "Bahasa Indonesia", ending: "kesimpulan kuat", visual_style: M.style, visual_mode: "ai_image", character: "", character_images: [], image_provider: "",
      preset: mode === "motion" ? "jurnalisme" : "", aspect: "9:16" } });
    const st = store.get(draftKey, null) || fresh();
    const pre = STATE.prefill[mode];
    if (pre) { Object.assign(st.params, pre); st.step = 1; st.story = null; delete STATE.prefill[mode]; }
    const save = () => store.set(draftKey, st);

    view.innerHTML = `<div class="page">${pageHead(label, M.desc, UI.btn("Mulai baru", { ico: "plus", size: "sm", id: "reset" }))}${keyBanner(["llm", "ffmpeg"])}
      <nav class="stepper" id="steps" aria-label="Langkah"></nav><div id="body"></div><div id="job" hidden></div></div>`;
    $("#reset").onclick = () => confirmModal("Mulai cerita baru?", "Draf yang sedang dikerjakan akan dihapus.", "Mulai baru", async () => { store.set(draftKey, null); route(); });

    function drawSteps() {
      $("#steps").innerHTML = ["Konten", "Cerita", "Video"].map((l, i) => `${i ? '<span class="sep"></span>' : ""}<button type="button" class="${st.step === i + 1 ? "on" : st.step > i + 1 ? "done" : ""}" data-s="${i + 1}"><span class="n">${st.step > i + 1 ? "✓" : i + 1}</span><span class="lbl-t">${l}</span></button>`).join("");
      $("#steps").onclick = (e) => { const b = e.target.closest("[data-s]"); if (!b) return; const s = +b.dataset.s; if (s >= 2 && !st.story) return toast("Buat cerita dulu di langkah 1", true); st.step = s; save(); draw(); };
    }
    function draw() { drawSteps(); [null, step1, step2, step3][st.step](); hydrate($("#body")); }

    /* ---------------- 1 · konten */
    function step1() {
      const p = st.params;
      const imgCaps = STATE.status?.media?.image || {};
      const styles = mode === "story" ? cat.story_styles || [] : cat.visual_styles || [];
      $("#body").innerHTML = `<div class="layout"><div class="stack lg">${UI.panel({ id: "f1", title: "Apa konten yang ingin dibuat?",
        body: (mode === "motion" ? UI.section("Preset motion graphic", `<div id="pre"></div>`) : "") +
          UI.section("Bahan", `<div id="srcType"></div><textarea name="source" rows="5">${esc(p.source)}</textarea><div id="docUp" hidden></div>`) +
          UI.section("Arah cerita", `<div class="fields">${UI.select("category", "Kategori", [{ v: "", l: "Semua kategori" }, ...(cat.categories || [])], p.category)}
            ${UI.select("tone", "Gaya penyampaian", Object.keys(TONE_LABEL).map((k) => ({ v: k, l: TONE_LABEL[k] })), p.tone)}
            ${UI.select("focus", "Fokus cerita", [{ v: "topik", l: "Topik / peristiwa" }, { v: "tokoh", l: "Satu tokoh utama" }], p.focus)}
            ${UI.select("duration", "Durasi target", [30, 45, 60, 90, 120, 180].map((v) => ({ v, l: v + " detik" })), p.duration, { numeric: true })}
            ${UI.select("language", "Bahasa narasi", ["Bahasa Indonesia", "English", "Bahasa Melayu", "Bahasa Jawa", "Bahasa Sunda"], p.language)}
            ${UI.select("ending", "Akhir cerita", cat.endings || ["kesimpulan kuat"], p.ending)}</div>`) +
          (mode !== "motion" ? UI.section("Gaya visual", `<div id="sty"></div>`) : "") +
          UI.more("Visual & tokoh", `<div class="fields">${UI.select("visual_mode", "Sumber visual", VISUAL_MODES, p.visual_mode)}
            ${UI.select("image_provider", "Generator gambar", [{ v: "", l: "Otomatis" }, ...Object.entries(imgCaps).map(([k, v]) => ({ v: k, l: v.label + (v.ready ? "" : " · butuh key"), disabled: !v.ready }))], p.image_provider)}
            ${UI.select("aspect", "Rasio", ["9:16", "1:1", "16:9"], p.aspect)}</div>
            ${UI.text("character", "Tokoh utama", { value: p.character, placeholder: "mis. pria 30-an, rambut ikal, kemeja batik biru", hint: "Opsional — deskripsi agar wajah konsisten di tiap adegan." })}
            <div class="row"><div id="charUp"></div><span class="small muted" id="charList">${p.character_images.length ? p.character_images.length + " foto tokoh" : "Foto tokoh dipakai Gemini sebagai referensi wajah."}</span></div>
            ${STATE.status?.media?.stock ? "" : UI.banner("info", "Footage stok butuh PEXELS_API_KEY atau PIXABAY_API_KEY di <a href='#settings'>Pengaturan</a>.")}`),
        foot: `${UI.btn("Riwayat", { ico: "clock", kind: "ghost", id: "hist" })}<span class="spacer"></span>${UI.btn("Buat cerita", { ico: "right", kind: "primary", size: "lg", id: "next" })}` })}</div>
        <aside>${UI.aside("Alur", ["<b>Konten</b> — tentukan bahan & arah cerita.", "<b>Cerita</b> — AI menulis adegan; edit narasi, gambar, urutan.", "<b>Video</b> — pilih narator, subtitle, musik, lalu render."],
          UI.banner("info", "Naskah sendiri tetap bisa diproses tanpa API key LLM."))}</aside></div>`;
      const ta = $("[name=source]");
      const setType = (v) => { p.source_type = v; ta.placeholder = SOURCE_PH[v]; ta.rows = v === "naskah" ? 10 : v === "topik" ? 4 : 2; $("#docUp").hidden = v !== "artikel"; };
      seg($("#srcType"), SOURCE_TYPES, p.source_type, setType);
      setType(p.source_type);
      if (mode === "motion") choiceGrid($("#pre"), Object.entries(cat.motion_presets || {}).map(([k, v]) => ({ v: k, l: v.label, d: v.use })), p.preset, (v) => { p.preset = v; save(); });
      if (mode !== "motion") choiceGrid($("#sty"), styles.map((s) => ({ v: s, l: STYLE_LABEL[s] || s })), p.visual_style, (v) => { p.visual_style = v; save(); });
      dropzone($("#docUp"), { accept: ".pdf,.txt,.srt", title: "Upload PDF / TXT", sub: "Teks diekstrak otomatis", ico: "doc", onUploaded: async (i) => {
        if (/\.(txt|srt)$/i.test(i.name)) { ta.value = (await (await fetch(fileUrl(i.path))).text()).slice(0, 20000); p.doc_path = ""; }
        else { p.doc_path = i.path; ta.value = `Dokumen: ${i.name}`; toast("PDF dipilih — teksnya dibaca saat membuat cerita"); }
      } });
      uploadButton($("#charUp"), { accept: "image/*", label: "Foto tokoh", multiple: true, onUploaded: (i) => { p.character_images.push(i.path); $("#charList").textContent = p.character_images.length + " foto tokoh"; save(); } });
      $("#hist").onclick = () => {
        const hist = store.get(`hist:${mode}`, []);
        modal({ title: "Riwayat cerita", body: hist.length ? `<div class="list">${hist.map((h, i) => `<div class="list-item"><div class="li-main"><b>${esc(h.story.title)}</b><span>${h.story.scenes.length} adegan · ${new Date(h.ts).toLocaleString("id-ID")}</span></div>${UI.btn("Buka", { size: "sm", attrs: `data-i="${i}"` })}</div>`).join("")}</div>` : UI.empty("clock", "Belum ada riwayat") }, (m) => {
          m.onclick = (e) => { const b = e.target.closest("[data-i]"); if (!b) return; const h = hist[+b.dataset.i]; st.params = { ...st.params, ...h.params }; st.story = h.story; st.step = 2; save(); closeModal(); draw(); };
        });
      };
      $("#next").onclick = (e) => {
        Object.assign(p, formValues($("#f1")));
        if (!p.source.trim()) return toast("Isi topik, link, atau naskah dulu", true);
        save();
        busy(e.currentTarget, async () => {
          try {
            let params = { ...p };
            if (p.doc_path && p.source_type === "artikel") {
              const job = await api("/api/jobs", { body: { kind: "transcript", title: "Baca dokumen", params: { path: p.doc_path } } });
              params = { ...params, source: (await waitJob(job.id)).result.text.slice(0, 20000) };
            }
            st.story = await api("/api/creator/script", { body: { params } });
            const hist = store.get(`hist:${mode}`, []); hist.unshift({ ts: Date.now(), params: p, story: st.story }); store.set(`hist:${mode}`, hist.slice(0, 15));
            st.step = 2; save(); draw();
          } catch (err) { fail(err); }
        });
      };
    }

    /* ---------------- 2 · storyboard */
    function step2() {
      const s = st.story;
      const words = s.scenes.reduce((n, x) => n + String(x.narration || "").split(/\s+/).filter(Boolean).length, 0);
      $("#body").innerHTML = `<div class="stack lg">${UI.panel({ title: "Judul & pembuka", sub: esc(s.source_label || ""), head: UI.pill(`${s.scenes.length} adegan`, "teal") + UI.pill(`±${Math.round(words / 2.5)} dtk narasi`),
          body: `<div class="fields two">${UI.field("Judul video", `<input type="text" id="ttl" value="${esc(s.title)}">`)}${UI.field("Hook pembuka", `<input type="text" id="hook" value="${esc(s.hook)}">`)}</div>` })}
        ${UI.panel({ title: "Storyboard", sub: "Gambar yang belum dibuat akan dibuat saat render.", head: UI.btn("Buat semua gambar", { ico: "sparkles", size: "sm", id: "allImg" }) + UI.btn("Tulis ulang", { ico: "refresh", size: "sm", kind: "ghost", id: "regen" }),
          bodyCls: "flush", body: `<div id="scenes"></div>`, foot: `${UI.btn("Konten", { ico: "left", id: "back" })}<span class="spacer"></span>${UI.btn("Lanjut ke video", { ico: "right", kind: "primary", size: "lg", id: "next" })}` })}</div>`;
      function drawScenes() {
        $("#scenes").innerHTML = s.scenes.map((sc, i) => `<div class="scene" data-i="${i}">
          <div class="s-pic"><div class="s-img">${sc.image_path ? `<img src="${fileUrl(sc.image_path)}" alt="Gambar adegan ${i + 1}">` : "Belum ada gambar"}</div>
            <div class="stack" style="gap:6px">${UI.btn(sc.image_path ? "Ganti" : "Buat gambar", { ico: "sparkles", size: "sm", attrs: 'data-a="img"' })}<label class="btn sm">${icon("upload")}<span>Upload</span><input type="file" accept="image/*" hidden data-up></label></div></div>
          <div class="stack"><div class="s-head"><span class="s-no">ADEGAN ${String(i + 1).padStart(2, "0")}</span><div class="s-tools">
            ${UI.btn("", { ico: "up", size: "sm", kind: "ghost", title: "Naik", attrs: 'data-a="up"' })}${UI.btn("", { ico: "down", size: "sm", kind: "ghost", title: "Turun", attrs: 'data-a="down"' })}
            ${UI.btn("", { ico: "dup", size: "sm", kind: "ghost", title: "Duplikat", attrs: 'data-a="dup"' })}${UI.btn("", { ico: "plus", size: "sm", kind: "ghost", title: "Sisipkan adegan setelahnya", attrs: 'data-a="ins"' })}
            ${UI.btn("", { ico: "trash", size: "sm", kind: "ghost", title: "Hapus adegan", attrs: 'data-a="del"' })}</div></div>
            ${UI.field("Narasi", `<textarea data-f="narration" rows="3">${esc(sc.narration)}</textarea>`)}
            <div class="fields two">${UI.field("Teks di layar", `<input type="text" data-f="on_screen_text" value="${esc(sc.on_screen_text)}">`)}
              ${UI.field("Gerak kamera", `<select data-f="motion">${UI.options(Object.keys(MOTION_LABEL).map((m) => ({ v: m, l: MOTION_LABEL[m] })), sc.motion)}</select>`)}</div>
            ${UI.more("Prompt gambar & kata kunci stok", `${UI.field("Prompt gambar", `<textarea data-f="image_prompt" rows="3">${esc(sc.image_prompt)}</textarea>`)}${UI.field("Kata kunci footage stok", `<input type="text" data-f="stock_query" value="${esc(sc.stock_query || "")}" placeholder="bahasa Inggris">`)}`)}</div></div>`).join("");
      }
      drawScenes();
      $("#ttl").oninput = (e) => { s.title = e.target.value; save(); };
      $("#hook").oninput = (e) => { s.hook = e.target.value; save(); };
      $("#scenes").oninput = (e) => { const f = e.target.dataset.f; if (f) { s.scenes[+e.target.closest(".scene").dataset.i][f] = e.target.value; save(); } };
      $("#scenes").onchange = async (e) => {
        if (!e.target.matches("[data-up]")) return;
        const i = +e.target.closest(".scene").dataset.i, file = e.target.files[0]; if (!file) return;
        try { s.scenes[i].image_path = (await uploadFile(file)).path; save(); drawScenes(); } catch (err) { fail(err); }
      };
      const genImage = async (i) => { const r = await api("/api/creator/scene-image", { body: { params: st.params, scene: s.scenes[i] } }); s.scenes[i].image_path = r.path; save(); drawScenes(); };
      $("#scenes").onclick = (e) => {
        const b = e.target.closest("[data-a]"); if (!b) return;
        const i = +b.closest(".scene").dataset.i, a = b.dataset.a, arr = s.scenes;
        if (a === "img") return busy(b, () => genImage(i).catch(fail));
        if (a === "up" && i > 0) [arr[i - 1], arr[i]] = [arr[i], arr[i - 1]];
        if (a === "down" && i < arr.length - 1) [arr[i + 1], arr[i]] = [arr[i], arr[i + 1]];
        if (a === "dup") arr.splice(i + 1, 0, { ...arr[i], id: Math.random().toString(36).slice(2) });
        if (a === "ins") arr.splice(i + 1, 0, { id: Math.random().toString(36).slice(2), narration: "", on_screen_text: "", image_prompt: "", stock_query: "", motion: "zoom_in" });
        if (a === "del") { if (arr.length <= 1) return toast("Minimal satu adegan", true); arr.splice(i, 1); }
        save(); drawScenes();
      };
      $("#allImg").onclick = (e) => busy(e.currentTarget, async () => {
        const todo = s.scenes.map((x, i) => (x.image_path ? -1 : i)).filter((i) => i >= 0);
        for (const [n, i] of todo.entries()) { toast(`Membuat gambar ${n + 1}/${todo.length}…`, false, 2500); try { await genImage(i); } catch (err) { fail(err); break; } }
      });
      $("#regen").onclick = () => confirmModal("Tulis ulang cerita?", "Storyboard sekarang (termasuk editanmu) akan diganti.", "Tulis ulang", async () => { st.step = 1; st.story = null; save(); draw(); setTimeout(() => $("#next")?.click(), 50); });
      $("#back").onclick = () => { st.step = 1; save(); draw(); };
      $("#next").onclick = () => {
        const empty = s.scenes.findIndex((x) => !String(x.narration || "").trim());
        if (empty >= 0) return toast(`Adegan ${empty + 1} belum punya narasi`, true);
        st.step = 3; save(); draw();
      };
    }

    /* ---------------- 3 · video */
    function step3() {
      const eleven = STATE.status?.media?.elevenlabs;
      const v = st.video || { caption_style: "viral", subtitles: true, kinetic_text: mode === "motion", show_title: true, transition: mode === "motion" ? "slideleft" : "fade", music_volume: 0.12, brand_kit: true };
      $("#body").innerHTML = `<div class="layout"><div class="stack lg">${UI.panel({ id: "f3", title: "Narator", body: `<div id="vp"></div>${UI.text("voice_instructions", "Instruksi gaya bicara", { value: v.voice_instructions || "", placeholder: "mis. bercerita pelan, penuh ketegangan", hint: "Hanya OpenAI & Gemini." })}` +
          `<div class="section"><div class="section-title">Tampilan</div><div class="fields two">${UI.select("caption_style", "Gaya subtitle", CAPTION_STYLES(), v.caption_style)}${UI.select("transition", "Transisi antar adegan", cat.transitions || ["fade"], v.transition)}</div>
          <div class="switches">${UI.toggle("subtitles", "Subtitle karaoke", { checked: v.subtitles })}${UI.toggle("kinetic_text", "Teks besar di layar", { checked: v.kinetic_text })}${UI.toggle("show_title", "Judul di awal", { checked: v.show_title })}${UI.toggle("brand_kit", "Logo Brand Kit", { checked: v.brand_kit })}</div></div>` +
          `<div class="section"><div class="section-title">Musik latar</div><div id="mp"></div>${eleven ? UI.text("music_prompt", "Atau buat musik AI", { value: v.music_prompt || "", placeholder: "cinematic dark ambient, slow build", hint: "ElevenLabs · kosong = tidak." }) : ""}
          <div class="fields two">${UI.range("music_volume", "Volume musik", { value: v.music_volume, min: 0.03, max: 0.4, step: 0.01, fmt: "%" })}</div></div>`,
          foot: `${UI.btn("Cerita", { ico: "left", id: "back" })}<span class="spacer"></span>${UI.btn("Render video", { ico: "play", kind: "primary", size: "lg", id: "go" })}` })}</div>
        <aside>${UI.panel({ title: st.story.title, sub: `${st.story.scenes.length} adegan · ${st.params.aspect}`, body: `<div class="thumb-strip">${st.story.scenes.map((sc) => `<div>${sc.image_path ? `<img src="${fileUrl(sc.image_path)}" alt="">` : ""}</div>`).join("")}</div>
          ${UI.banner("info", "Render ±20–60 detik per adegan. Boleh pindah halaman — progres ada di <a href='#jobs'>Antrean Job</a>.")}` })}</aside></div>`;
      let vp, mp;
      voicePicker($("#vp"), { provider: v.tts_provider, voice: v.voice }).then((x) => (vp = x));
      musicPicker($("#mp")).then((x) => (mp = x));
      const collect = () => { st.video = { ...formValues($("#f3")), ...(vp ? vp.value() : {}) }; save(); return st.video; };
      $("#back").onclick = () => { collect(); st.step = 2; save(); draw(); };
      $("#go").onclick = (e) => runJob($("#job"), "creator_render", { ...st.params, ...collect(), music_path: mp ? mp.value() : "", storyboard: st.story },
        { title: `${label}: ${st.story.title}`.slice(0, 120), btn: e.currentTarget });
    }

    draw();
  } };
}

PAGES.creator = creatorPage("creator");
PAGES.story = creatorPage("story");
PAGES.motion = creatorPage("motion");

boot();
