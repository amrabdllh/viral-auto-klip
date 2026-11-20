"""
Viral Auto Klip - Streamlit Web UI (Beautiful & Powerful)
Run with: streamlit run app.py
"""
import streamlit as st
import os
import json
from pathlib import Path
from datetime import datetime
import tempfile
import shutil
from loguru import logger

# Core imports
from core.utils import setup_logging, download_video, get_video_info
from core.analyzer import analyze_viral_potential, configure_llm_clients
from core.clipper import process_clips
from core.transcriber import transcribe_video
from core.clipping_software import submit_clip_job, wait_for_clip_job
from core import ai_providers as ai
import config

setup_logging()

st.set_page_config(
    page_title="🚀 Viral Auto Klip",
    page_icon="🔥",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Custom CSS for beautiful UI
st.markdown("""
<style>
    .main-header {
        font-size: 3rem;
        background: linear-gradient(90deg, #FF6B6B, #4ECDC4);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        text-align: center;
        margin-bottom: 0.5rem;
    }
    .sub-header {
        text-align: center;
        color: #666;
        font-size: 1.2rem;
        margin-bottom: 2rem;
    }
    .metric-card {
        background: #f8f9fa;
        border-radius: 12px;
        padding: 1rem;
        text-align: center;
        border: 1px solid #e9ecef;
    }
    .clip-card {
        background: white;
        border-radius: 16px;
        padding: 1.5rem;
        margin-bottom: 1.5rem;
        box-shadow: 0 4px 15px rgba(0,0,0,0.1);
        border-left: 6px solid #4ECDC4;
    }
    .viral-score {
        font-size: 2.5rem;
        font-weight: bold;
        color: #FF6B6B;
    }
    .stButton>button {
        background: linear-gradient(90deg, #FF6B6B, #4ECDC4);
        color: white;
        border: none;
        padding: 0.75rem 2rem;
        font-size: 1.1rem;
        border-radius: 50px;
        font-weight: bold;
    }
</style>
""", unsafe_allow_html=True)

def main():
    st.markdown('<h1 class="main-header">🚀 Viral Auto Klip</h1>', unsafe_allow_html=True)
    st.markdown('<p class="sub-header">AI yang bikin video panjang jadi ratusan klip viral otomatis • Face Detection • Auto Caption • Scoring Pintar</p>', unsafe_allow_html=True)
    
    # Sidebar controls
    with st.sidebar:
        st.header("⚙️ Pengaturan")
        st.caption(
            f"Preset laptop: Ryzen 7 5800H | RAM 24GB | RTX 3060 6GB | "
            f"Whisper {config.WHISPER_MODEL}/{config.WHISPER_DEVICE}/{config.WHISPER_COMPUTE_TYPE}"
        )
        
        num_clips = st.slider("Jumlah Klip Maksimal", 3, 20, 8)
        min_score = st.slider("Min Viral Score", 40, 90, 60)
        min_duration = st.slider("Min Durasi Klip (detik)", 10, 30, 15)
        max_duration = st.slider("Max Durasi Klip (detik)", 30, 90, 60)
        clip_engine = st.selectbox(
            "Engine Clip",
            ["Local Advanced Engine", "Clipping.software API"],
            index=0,
            help="Local = proses di laptop kamu. Clipping.software API = kirim URL publik ke API eksternal jika punya API key."
        )
        clipping_api_key = ""
        if clip_engine == "Clipping.software API":
            clipping_api_key = st.text_input(
                "Clipping.software API Key",
                value="",
                type="password",
                help="Bisa juga disimpan di .env sebagai CLIPPING_SOFTWARE_API_KEY=..."
            )
            if config.CLIPPING_SOFTWARE_API_KEY:
                st.caption("API key Clipping.software terdeteksi dari .env. Kolom ini boleh dikosongkan.")
        
        st.divider()
        st.subheader("🎨 Style & Fitur")
        enable_captions = st.checkbox("Caption otomatis (burn-in)", value=True)
        caption_style = st.selectbox(
            "Caption Style",
            ["modern", "tiktok_bold", "gradient", "neon", "minimal", "karaoke", "emoji"],
            index=0,
        )
        caption_font_size = st.slider("Caption Font Size", 30, 120, 72, help="Untuk video 1080x1920: 64–80 nyaman dibaca di HP.")
        caption_vertical_position = st.slider("Caption Vertical Position", 10, 900, 420,
                                              help="Jarak dari bawah (px). ±420 = di atas zona tombol & caption TikTok/Reels.")
        caption_border_thickness = st.slider("Caption Border Thickness", 0, 10, 6)
        enable_face = st.checkbox("Face Reframing (Ikuti Wajah)", value=True)
        
        st.subheader("🎥 Mode Video")
        video_mode = st.selectbox(
            "Tipe Video",
            [
                "Single Speaker (Talking Head)",
                "Conversation / Podcast (2 Orang)",
                "Group / Multi Person (3+ Orang)",
                "Auto Detect",
            ],
            index=0,
            help="Pilih Group/Multi Person untuk video berisi 3 orang atau lebih agar framing menjaga semua wajah tetap masuk."
        )
        
        output_type = st.selectbox(
            "Output Type",
            ["Vertical", "Wide", "Fit"],
            index=0,
            help="Vertical=9:16, Wide=16:9, Fit=pertahankan semua frame dalam format vertical dengan background blur."
        )
        
        st.subheader("✨ Efek Lanjutan")
        col1, col2, col3 = st.columns(3)
        with col1:
            auto_zoom = st.checkbox("Auto Zoom on Speaker", value=True, help="Otomatis zoom ke wajah orang yang bicara (smooth animation)")
        with col2:
            split_screen = st.checkbox("Split Screen Mode", value=False, help="Tampilkan 2 orang berdampingan (cocok untuk conversation/podcast)")
        with col3:
            bg_blur = st.checkbox("Background Blur", value=False, help="Blur background seperti Zoom (hanya wajah yang tajam)")
        klipers_reframe = st.checkbox(
            "Klipers-style Reframe Mode",
            value=True,
            help="Dynamic crop: closeup, two-person, group context, reaction, plus caption-safe framing."
        )
        eye_contact = st.checkbox("Eye Contact Correction", value=False, help="Bantu wajah terlihat lebih frontal dan mata lebih jelas ke kamera")
        virtual_bg_file = st.file_uploader("Virtual Background (gambar)", type=["jpg", "jpeg", "png"])
        
        transcribe_mode = st.selectbox(
            "Mode Transkripsi",
            [
                "Auto (rekomendasi — deteksi GPU otomatis)",
                "GPU NVIDIA (paksa cuda, fallback CPU)",
                "Groq Whisper Cloud (tidak pakai GPU laptop)",
                "ElevenLabs Scribe Cloud (akurasi tinggi, timestamp per kata)",
                "CPU aman (paling stabil)",
                "🛡️ Super Stable CPU Mode (100% stabil, tidak pakai GPU sama sekali)"
            ],
            index=0,
            help="Groq Whisper & ElevenLabs Scribe memakai API cloud (audio dikirim ke provider). Auto/GPU tetap bisa fallback ke cloud bila lokal gagal dan API key tersedia."
        )
        elevenlabs_api_key = ""
        if "ElevenLabs" in transcribe_mode:
            elevenlabs_api_key = st.text_input("ElevenLabs API Key", value="", type="password",
                                               help="Bisa juga di .env sebagai ELEVENLABS_API_KEY=...")
            if config.ELEVENLABS_API_KEY:
                st.caption("ElevenLabs API key terdeteksi dari .env.")
        language_label = st.selectbox(
            "Bahasa Video",
            ["Otomatis", "Indonesia (id)", "English (en)", "Melayu (ms)", "Jawa (jw)"],
            index={"": 0, "id": 1, "en": 2, "ms": 3, "jw": 4}.get(config.WHISPER_LANGUAGE, 0),
            help="Menentukan bahasa mempercepat & mempertajam transkripsi. Indonesia juga mengaktifkan hint tanda baca.",
        )
        whisper_language = {"Otomatis": "", "Indonesia (id)": "id", "English (en)": "en", "Melayu (ms)": "ms", "Jawa (jw)": "jw"}[language_label]
        whisper_model = st.selectbox(
            "Model Whisper",
            ["tiny", "base", "small", "medium", "large-v3-turbo", "large-v3"],
            index=["tiny", "base", "small", "medium", "large-v3-turbo", "large-v3"].index(config.WHISPER_MODEL)
            if config.WHISPER_MODEL in ["tiny", "base", "small", "medium", "large-v3-turbo", "large-v3"] else 2,
            help="large-v3-turbo lebih ringan/cepat dari large-v3 dengan akurasi tinggi. Medium paling stabil."
        )
        allow_large_gpu = st.checkbox(
            "Paksa izinkan large-v3 di GPU",
            value=config.WHISPER_ALLOW_LARGE_ON_GPU,
            help="Untuk large/large-v3 penuh. large-v3-turbo biasanya lebih aman di RTX 3060 6GB."
        )
        st.subheader("🧠 AI (Moment Finder + Rerank + Packaging)")
        enable_llm = st.checkbox("Gunakan AI untuk memilih & menulis klip", value=config.USE_LLM_SCORING)
        providers = list(config.ALL_AI_PROVIDERS)
        ai_provider = st.selectbox(
            "Provider AI",
            providers,
            index=providers.index(config.AI_PROVIDER) if config.AI_PROVIDER in providers else 0,
            format_func=lambda name: ai.PROVIDER_INFO.get(name, {}).get("label", name),
            help="Claude/GPT/Gemini = kualitas top-tier. Groq = tercepat & gratis. Ollama = lokal. "
                 "Custom = endpoint OpenAI-compatible apa pun (base URL + API key sendiri)."
        )
        provider_models = ai.provider_models(ai_provider)
        default_model = ai.resolve_model(ai_provider)
        model_options = provider_models + ["✏️ Model lain (ketik sendiri)"]
        model_choice = st.selectbox(
            "Model AI",
            model_options,
            index=provider_models.index(default_model) if default_model in provider_models else len(model_options) - 1,
            help="Model untuk mencari momen, menilai, dan menulis hook/judul/caption."
        )
        ai_model = model_choice
        if model_choice.startswith("✏️"):
            ai_model = st.text_input("Nama model", value="" if default_model in provider_models else default_model,
                                     placeholder="mis. gpt-5.2 / claude-opus-5-5 / qwen2.5:14b")
        ai_api_key, ai_base_url = "", ""
        key_attr = ai.PROVIDER_INFO.get(ai_provider, {}).get("key_attr")
        if key_attr:
            ai_api_key = st.text_input(f"{ai_provider} API Key", value="", type="password",
                                       help=f"Bisa juga disimpan di .env sebagai {key_attr}=...")
            if getattr(config, key_attr, ""):
                st.caption(f"API key {ai_provider} terdeteksi dari .env — kolom ini boleh dikosongkan.")
        if ai_provider == "Custom":
            ai_base_url = st.text_input("Base URL (OpenAI-compatible)", value=config.CUSTOM_AI_BASE_URL,
                                        placeholder="https://api.provider-kamu.com/v1")
        elif ai_provider == "Ollama":
            ai_base_url = st.text_input("Ollama Base URL", value=config.OLLAMA_BASE_URL)
        elif ai_provider == "Antigravity":
            status = ai.google_login_status()
            if status["logged_in"]:
                st.success(f"✅ Login sebagai {status['email'] or 'akun Google'}")
                if st.button("Logout Google"):
                    ai.google_logout()
                    st.rerun()
            else:
                if not status["has_client_secret"]:
                    st.info(
                        "Google mewajibkan tiap aplikasi punya OAuth client sendiri (login bawaan Antigravity hanya "
                        "untuk Antigravity). Setup sekali (±5 menit):\n"
                        "1. console.cloud.google.com → buat project\n"
                        "2. Enable **Generative Language API**\n"
                        "3. Google Auth Platform → Get started (Audience External) → tambah email kamu di Test users\n"
                        "4. Clients → Create client → **Desktop app** → Download JSON\n"
                        "5. Upload file JSON di bawah\n\n"
                        "Lebih cepat tanpa OAuth: pilih provider **Gemini** + API key gratis dari aistudio.google.com/apikey"
                    )
                secret_file = st.file_uploader("client_secret.json (OAuth Desktop app)", type=["json"], key="g_secret")
                if secret_file is not None:
                    try:
                        ai.save_client_secret(secret_file.getvalue().decode("utf-8"))
                        st.success("client_secret.json tersimpan. Klik Login di bawah.")
                    except Exception as exc:
                        st.error(str(exc))
                if st.button("🔐 Login dengan Google", disabled=not ai.google_login_status()["has_client_secret"]):
                    try:
                        with st.spinner("Selesaikan login di tab browser yang terbuka…"):
                            ai.google_login()
                        st.rerun()
                    except Exception as exc:
                        st.error(f"Login gagal: {exc}")
        if st.button("🔌 Tes koneksi AI"):
            ai.set_credentials(ai_provider, api_key=ai_api_key, base_url=ai_base_url, model=ai_model)
            ok, message = ai.test_connection(ai_provider, ai.resolve_model(ai_provider, ai_model))
            (st.success if ok else st.error)(f"{ai_provider}: {message}")
        
        st.divider()
        st.subheader("📤 Output")
        add_watermark = st.checkbox("Tambah Watermark", value=False)
        use_nvenc = st.checkbox("Render pakai NVIDIA NVENC", value=config.USE_NVENC, help="Lebih cepat di RTX 3060. Jika gagal, sistem fallback ke CPU encoder.")
        keep_source_video = st.checkbox("Simpan source video", value=config.KEEP_SOURCE_VIDEO, help="Disarankan aktif supaya bisa proses ulang tanpa download lagi.")
        target_platform = st.selectbox("Target Platform", ["TikTok", "YouTube Shorts", "Instagram Reels", "Semua"])
        make_publish = st.checkbox(
            "Buat Paket Upload (publish/) + Rencana Posting",
            value=config.ENABLE_PUBLISH_PACKAGE,
            help="Folder publish/ berisi video siap upload, caption per platform, jam upload terbaik, dan varian caption A/B test.",
        )
        
        st.divider()
        st.subheader("🎵 Klipers.pro Audio & Style")
        enable_audio = st.checkbox("Audio Enhancer (Noise Reduction + Bass Boost)", value=True)
        enable_music = st.checkbox("Background Music + Auto Duck", value=False)
        music_file = st.file_uploader("Upload Music (mp3/wav) untuk ducking", type=["mp3", "wav"]) if enable_music else None
        
        if st.button("💾 Simpan Pengaturan"):
            config.MIN_VIRAL_SCORE = min_score
            config.MIN_CLIP_DURATION = min_duration
            config.MAX_CLIP_DURATION = max_duration
            config.MAX_CLIPS_PER_VIDEO = num_clips
            st.success("Pengaturan disimpan!")
    
    # Main input
    st.subheader("📥 Masukkan Video")
    url = st.text_input(
        "URL Video (YouTube, TikTok, Instagram, Facebook, dll)",
        placeholder="https://www.youtube.com/watch?v=...",
        help="Paste link video panjang apa saja"
    )
    with st.expander("YouTube login/cookies", expanded=False):
        cookie_browser = st.selectbox(
            "Ambil cookies dari browser",
            ["Tidak pakai", "chrome", "edge", "firefox", "brave", "opera"],
            help="Pakai ini kalau YouTube minta login atau bot check. Tutup browser dulu kalau cookies terkunci."
        )
        cookies_file = st.file_uploader("Atau upload cookies.txt", type=["txt"])
    
    col1, col2 = st.columns([1, 1])
    with col1:
        uploaded_file = st.file_uploader("Atau upload file video langsung", type=["mp4", "mov", "avi", "mkv"])
    
    with col2:
        st.info("💡 **Tips**: Video 10-60 menit paling optimal. Podcast, vlog, tutorial, gaming, motivasi = hasil terbaik!")
    
    if st.button("🔥 MULAI PROSES - BUAT KLIP VIRAL!", type="primary", use_container_width=True):
        if not url and not uploaded_file:
            st.error("Masukkan URL atau upload file video!")
            return
        
        process_video(
            url, uploaded_file, num_clips, min_score, min_duration, max_duration,
            caption_style, enable_face, transcribe_mode, enable_llm, add_watermark,
            target_platform, ai_provider, ai_model,
            ai_api_key=ai_api_key, ai_base_url=ai_base_url,
            video_mode=video_mode, output_type=output_type,
            enable_audio=enable_audio, enable_music=enable_music, music_file=music_file,
            auto_zoom=auto_zoom, split_screen=split_screen, bg_blur=bg_blur,
            klipers_reframe=klipers_reframe, whisper_model=whisper_model, allow_large_gpu=allow_large_gpu,
            eye_contact=eye_contact, virtual_bg_file=virtual_bg_file, cookie_browser=cookie_browser,
            cookies_file=cookies_file, use_nvenc=use_nvenc, keep_source_video=keep_source_video,
            caption_font_size=caption_font_size, caption_vertical_position=caption_vertical_position,
            caption_border_thickness=caption_border_thickness, clip_engine=clip_engine,
            clipping_api_key=clipping_api_key, make_publish=make_publish,
            elevenlabs_api_key=elevenlabs_api_key, whisper_language=whisper_language,
            enable_captions=enable_captions,
        )
    elif st.session_state.get("last_result"):
        # FIX: tombol download memicu rerun Streamlit -> dulu hasil hilang dari layar.
        show_results(**st.session_state["last_result"], celebrate=False)

def process_video(
    url,
    uploaded_file,
    num_clips,
    min_score,
    min_duration,
    max_duration,
    caption_style,
    enable_face,
    transcribe_mode,
    enable_llm,
    add_watermark,
    target_platform,
    ai_provider="Groq",
    ai_model="",
    ai_api_key="",
    ai_base_url="",
    video_mode="Single Speaker (Talking Head)",
    output_type="Vertical",
    enable_audio=True,
    enable_music=False,
    music_file=None,
    auto_zoom=True,
    split_screen=False,
    bg_blur=False,
    klipers_reframe=True,
    whisper_model=None,
    allow_large_gpu=False,
    eye_contact=False,
    virtual_bg_file=None,
    cookie_browser="Tidak pakai",
    cookies_file=None,
    use_nvenc=True,
    keep_source_video=True,
    caption_font_size=48,
    caption_vertical_position=95,
    caption_border_thickness=3,
    clip_engine="Local Advanced Engine",
    clipping_api_key="",
    make_publish=True,
    elevenlabs_api_key="",
    whisper_language="",
    enable_captions=True,
):
    progress_bar = st.progress(0)
    status_text = st.empty()
    st.session_state.pop("last_result", None)

    try:
        config.WHISPER_LANGUAGE = whisper_language
        if elevenlabs_api_key:
            config.ELEVENLABS_API_KEY = elevenlabs_api_key.strip()
        config.MIN_VIRAL_SCORE = min_score
        config.MIN_CLIP_DURATION = min_duration
        config.MAX_CLIP_DURATION = max_duration
        config.MAX_CLIPS_PER_VIDEO = num_clips
        if whisper_model:
            config.WHISPER_MODEL = whisper_model
        config.WHISPER_ALLOW_LARGE_ON_GPU = bool(allow_large_gpu)
        config.USE_NVENC = bool(use_nvenc)
        config.KEEP_SOURCE_VIDEO = bool(keep_source_video)
        if clip_engine == "Clipping.software API":
            if uploaded_file:
                st.warning("Clipping.software API butuh URL publik. Karena kamu upload file lokal, proses dialihkan ke Local Advanced Engine.")
            elif not url:
                st.error("Masukkan URL untuk memakai Clipping.software API.")
                return
            else:
                api_key = clipping_api_key.strip() or config.CLIPPING_SOFTWARE_API_KEY
                if not api_key:
                    st.error("Isi Clipping.software API Key atau tambahkan CLIPPING_SOFTWARE_API_KEY di .env.")
                    return
                status_text.info("Mengirim job ke Clipping.software API...")
                progress_bar.progress(15)
                caption_style_api = {
                    "font_size": min(max(int(caption_font_size), 5), 30),
                    "vertical_position": min(max(int(caption_vertical_position), 0), 260),
                    "border_thickness": min(max(int(caption_border_thickness), 0), 6),
                }
                task = submit_clip_job(
                    api_key=api_key,
                    output_type=output_type,
                    number_of_clips=num_clips,
                    captions=True,
                    caption_style=caption_style_api,
                    yt_link=url if "youtu" in url.lower() else "",
                    video_url="" if "youtu" in url.lower() else url,
                    ai_clipping=True,
                )
                task_id = task.get("taskID") or task.get("taskId") or task.get("id")
                if not task_id:
                    st.json(task)
                    st.error("API tidak mengembalikan taskID.")
                    return
                status_text.info(f"Job API diterima: {task_id}. Menunggu hasil...")
                progress_bar.progress(45)
                result = wait_for_clip_job(api_key, task_id)
                progress_bar.progress(100)
                st.success("Clipping.software API selesai.")
                st.json(result)
                return
        if "Groq Whisper" in transcribe_mode:
            whisper_device = "groq"
            whisper_compute = "cloud"
            st.info("Groq Whisper Cloud aktif — transkripsi diproses di Groq, GPU/CPU laptop tidak terbebani.")
        elif "ElevenLabs" in transcribe_mode:
            whisper_device = "elevenlabs"
            whisper_compute = "cloud"
            st.info("ElevenLabs Scribe aktif — audio dikirim ke ElevenLabs untuk transkripsi.")
        elif "Super Stable" in transcribe_mode:
            whisper_device = "cpu"
            whisper_compute = "int8"
            st.info("🛡️ Super Stable CPU Mode aktif — proses lebih lambat tapi 100% stabil (tidak pakai GPU sama sekali)")
        elif "Auto" in transcribe_mode:
            whisper_device = "auto"
            whisper_compute = config.WHISPER_COMPUTE_TYPE
            st.info(f"Auto GPU aktif: aplikasi akan coba CUDA dengan compute {whisper_compute}, lalu fallback CPU otomatis.")
        elif "GPU" in transcribe_mode:
            whisper_device = "cuda"
            whisper_compute = config.WHISPER_COMPUTE_TYPE
            st.info(f"Mode GPU aktif: aplikasi akan coba CUDA dengan compute {whisper_compute}. Kalau gagal, fallback CPU otomatis.")
        else:
            whisper_device = "cpu"
            whisper_compute = "int8"
        if enable_llm:
            if configure_llm_clients(provider=ai_provider, model=ai_model, api_key=ai_api_key, base_url=ai_base_url):
                st.info(f"🧠 AI aktif: {ai_provider} / {config.LLM_MODEL}")
            else:
                st.warning(
                    f"AI aktif, tapi {ai_provider} belum dikonfigurasi (API key / base URL / login). "
                    "Proses lanjut dengan scoring lokal."
                )
                enable_llm = False
        
        # Step 1: Download or use uploaded
        status_text.info("📥 Mengunduh / memproses video...")
        progress_bar.progress(10)
        
        temp_dir = Path(tempfile.mkdtemp())  # still used for uploaded files
        video_path = ""
        
        if uploaded_file:
            video_path = str(temp_dir / uploaded_file.name)
            with open(video_path, "wb") as f:
                f.write(uploaded_file.getbuffer())
        else:
            # Download to fixed 'mentahan/' folder (easy to find, not in %TEMP%)
            cookies_path = ""
            if cookies_file:
                cookies_path = str(temp_dir / "cookies.txt")
                with open(cookies_path, "wb") as f:
                    f.write(cookies_file.getbuffer())
            browser = "" if cookie_browser == "Tidak pakai" else cookie_browser
            video_path = download_video(url, cookies_from_browser=browser, cookies_file=cookies_path)
            status_text.info(f"✅ Video tersimpan di folder 'mentahan/' → {Path(video_path).name}")
        
        downloaded_video_info = get_video_info(video_path)
        source_height = downloaded_video_info.get("height", 0)
        source_width = downloaded_video_info.get("width", 0)
        if source_height and source_height < 720:
            st.warning(
                f"Video sumber hanya {source_width}x{source_height}. "
                "Aplikasi tetap lanjut memakai kualitas terbaik yang tersedia dari yt-dlp."
            )

        progress_bar.progress(25)
        status_text.success(f"✅ Video siap: {Path(video_path).name}")
        
        # Step 2: Transcribe
        status_text.info("🎙️ Transcribing dengan Whisper AI...")
        progress_bar.progress(35)
        
        transcript_segments, info = transcribe_video(
            video_path,
            model_name=config.WHISPER_MODEL,
            word_timestamps=True,
            device=whisper_device,
            compute_type=whisper_compute,
        )
        
        progress_bar.progress(55)
        actual_device = info.get("device", "unknown")
        status_text.success(f"✅ Transkrip selesai ({len(transcript_segments)} segmen) — pakai {actual_device.upper()}")
        
        # Step 3: Viral Analysis
        status_text.info("🧠 AI sedang menganalisis momen viral...")
        progress_bar.progress(65)
        
        video_info = downloaded_video_info
        clips_data = analyze_viral_potential(
            transcript_segments, 
            video_info, 
            face_data=None,  # will detect per clip
            use_llm=enable_llm,
            video_path=video_path,
        )
        
        if not clips_data:
            st.warning("Tidak menemukan klip dengan score tinggi. Coba turunkan min score atau pilih video lain.")
            return
        
        progress_bar.progress(75)
        status_text.success(f"✅ Ditemukan {len(clips_data)} klip potensial viral!")
        
        # Step 4: Process clips
        status_text.info("🎬 Memproses klip: extract + face reframe + caption + encode...")
        progress_bar.progress(80)
        
        output_dir = config.OUTPUT_DIR / f"klip_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        output_dir.mkdir(parents=True, exist_ok=True)
        
        # Save music if uploaded
        music_path = ""
        if enable_music and music_file:
            music_path = str(temp_dir / music_file.name)
            with open(music_path, "wb") as f:
                f.write(music_file.getbuffer())

        virtual_bg_path = ""
        if virtual_bg_file:
            virtual_bg_path = str(temp_dir / virtual_bg_file.name)
            with open(virtual_bg_path, "wb") as f:
                f.write(virtual_bg_file.getbuffer())
        
        # Determine output type (matches clipping.software: Vertical, Wide, Fit)
        if output_type == "Wide":
            target_aspect = "landscape"
            fit_output = False
        elif output_type == "Fit":
            target_aspect = "portrait"
            fit_output = True
        else:
            target_aspect = "portrait"
            fit_output = False
        is_conversation = "Conversation" in video_mode or "Auto Detect" in video_mode
        group_framing = fit_output or "Group" in video_mode
        caption_overrides = {
            "font_size": caption_font_size,
            "vertical_position": caption_vertical_position,
            "stroke_width": caption_border_thickness,
            "border_thickness": caption_border_thickness,
        }
        
        processed = process_clips(
            video_path,
            clips_data[:num_clips],
            output_dir,
            style_name=caption_style,
            caption_overrides=caption_overrides,
            enable_face_reframe=enable_face,
            add_watermark=add_watermark,
            target_platform=target_platform,
            enable_audio_enhance=enable_audio,
            background_music_path=music_path,
            auto_duck=True,
            conversation_mode=is_conversation,
            target_aspect=target_aspect,
            auto_zoom=auto_zoom,
            split_screen=split_screen,
            bg_blur=bg_blur,
            group_framing=group_framing,
            klipers_reframe=klipers_reframe,
            eye_contact_correction=eye_contact,
            virtual_background_path=virtual_bg_path,
            enable_captions=enable_captions,
        )
        
        publish_dir = None
        if make_publish:
            try:
                from core.publisher import build_publish_package
                platforms_list = [target_platform] if target_platform != "Semua" else None
                publish_dir = build_publish_package(processed, output_dir, platforms=platforms_list)
                status_text.info(f"📤 Paket upload siap: {publish_dir}")
            except Exception as exc:
                st.warning(f"Gagal membuat paket publish: {exc}")

        progress_bar.progress(95)
        
        # Step 5: Show results
        status_text.success("🎉 SEMUA KLIP SIAP!")
        progress_bar.progress(100)
        
        st.session_state["last_result"] = {
            "processed_clips": processed,
            "output_dir": output_dir,
            "video_info": video_info,
            "target_platform": target_platform,
            "publish_dir": publish_dir,
        }
        show_results(processed, output_dir, video_info, target_platform, publish_dir)
        
    except Exception as e:
        st.error(f"❌ Error: {str(e)}")
        logger.exception("Processing failed")
    finally:
        # Cleanup temp (for uploaded files)
        if 'temp_dir' in locals():
            shutil.rmtree(temp_dir, ignore_errors=True)
        
        # Auto delete source video from 'mentahan/' after successful process (save storage)
        if 'video_path' in locals() and video_path and not uploaded_file and not config.KEEP_SOURCE_VIDEO:
            try:
                if Path(video_path).exists():
                    Path(video_path).unlink()
                    logger.info(f"🗑️ Source video dihapus otomatis: {Path(video_path).name}")
            except Exception as e:
                logger.warning(f"Gagal hapus source video: {e}")

def show_results(processed_clips: list, output_dir: Path, video_info: dict, target_platform: str = "Semua",
                 publish_dir=None, celebrate: bool = True):
    if celebrate:
        st.balloons()
    
    st.header("🏆 Hasil Klip Viral Kamu")
    if not processed_clips:
        st.warning("Analisis menemukan kandidat, tapi render klip gagal. Cek log FFmpeg/Whisper untuk detail error.")
        return
    
    # Summary metrics
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("Total Klip", len(processed_clips))
    with col2:
        avg_score = sum(c["score"] for c in processed_clips) / len(processed_clips)
        st.metric("Rata-rata Score", f"{avg_score:.1f}/100")
    with col3:
        total_duration = sum(c["duration"] for c in processed_clips)
        st.metric("Total Durasi Klip", f"{total_duration:.0f} detik")
    with col4:
        st.metric("Kategori Terpopuler", max(set(c["category"] for c in processed_clips), key=lambda x: sum(1 for c in processed_clips if c["category"]==x)))
    
    st.divider()
    
    # Individual clips
    for i, clip in enumerate(processed_clips):
        with st.container():
            st.markdown(f"""
            <div class="clip-card">
                <div style="display: flex; justify-content: space-between; align-items: center;">
                    <div>
                        <h3 style="margin:0;">Clip #{i+1} — <span style="color:#FF6B6B;">{clip['category'].upper()}</span></h3>
                        <p style="margin:0.2rem 0; color:#666;">{clip['text'][:120]}...</p>
                    </div>
                    <div style="text-align:right;">
                        <div class="viral-score">{clip['score']}</div>
                        <div style="color:#4ECDC4; font-weight:bold;">VIRAL SCORE</div>
                    </div>
                </div>
            </div>
            """, unsafe_allow_html=True)
            
            cols = st.columns([3, 2, 1])
            
            with cols[0]:
                if os.path.exists(clip["file_path"]):
                    st.video(clip["file_path"])
                else:
                    st.warning("File tidak ditemukan")
            
            with cols[1]:
                st.write(f"**Durasi:** {clip['duration']:.1f} detik")
                st.write(f"**Alasan Viral:** {clip['reason']}")
                st.write(f"**Hook:** {clip.get('hook_text', '—')}")
                if clip.get("ai_title"):
                    st.write(f"**Judul (AI):** {clip['ai_title']}")
                if clip.get("hook_alternatives"):
                    st.write(f"**Hook A/B:** {' · '.join(clip['hook_alternatives'])}")
                source_label = "🧠 AI Moment Finder" if clip.get("source") == "ai_moment_finder" else "📐 Rule-based"
                st.write(f"**Sumber momen:** {source_label} | skor lokal {clip.get('local_score', '—')} · skor AI {clip.get('llm_score') or '—'}")
                st.write(f"**Caption Upload:** {clip.get('upload_caption', '—')}")
                st.write(f"**Hashtag:** {' '.join(clip.get('hashtags', [])) or '—'}")
                st.write(f"**FYP Grade:** {clip.get('fyp_grade', '—')}")
                platform_copy = clip.get("platform_copy", {}).get(target_platform)
                if not platform_copy and clip.get("platform_copy"):
                    platform_copy = next(iter(clip["platform_copy"].values()))
                if platform_copy:
                    st.write(f"**Copy Platform:** {platform_copy.get('caption', '—')}")
                st.write(f"**Kata Kunci:** {', '.join(clip.get('keywords_matched', [])) or '—'}")
                if clip.get("sentiment"):
                    st.write(f"**Sentiment:** {clip['sentiment']:+.2f}")
            
            with cols[2]:
                if clip.get("thumbnail") and os.path.exists(clip["thumbnail"]):
                    st.image(clip["thumbnail"], width="stretch")

                # Download button (FIX: dulu open() tanpa cek file -> crash seluruh halaman hasil)
                if os.path.exists(clip["file_path"]):
                    with open(clip["file_path"], "rb") as f:
                        st.download_button(
                            label="⬇️ Download MP4",
                            data=f,
                            file_name=Path(clip["file_path"]).name,
                            mime="video/mp4",
                            key=f"dl_{i}"
                        )
    
    # Metadata download
    metadata_path = output_dir / "metadata.json"
    if metadata_path.exists():
        with open(metadata_path, "rb") as f:
            st.download_button(
                "📋 Download Metadata Lengkap (JSON)",
                data=f,
                file_name="klip_metadata.json",
                mime="application/json"
            )
    
    if publish_dir and Path(publish_dir).exists():
        import zipfile
        import io
        zip_buf = io.BytesIO()
        with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for file in Path(publish_dir).rglob("*"):
                if file.is_file():
                    zf.write(file, file.relative_to(Path(publish_dir)))
        st.download_button(
            "📤 Download Paket Upload (ZIP)",
            data=zip_buf.getvalue(),
            file_name=f"{Path(publish_dir).name}.zip",
            mime="application/zip",
        )
        st.info("📤 Paket publish berisi caption per platform, jam upload terbaik & varian A/B test.")

    with st.expander("⬆️ Auto-upload ke YouTube Shorts / TikTok"):
        st.caption("YouTube butuh client_secret.json OAuth (YOUTUBE_CLIENT_SECRET_FILE). "
                   "TikTok butuh `pip install tiktok-uploader` + cookies (TIKTOK_COOKIES_FILE).")
        up_col1, up_col2, up_col3 = st.columns(3)
        with up_col1:
            do_yt = st.checkbox("YouTube Shorts", key="up_yt")
        with up_col2:
            do_tt = st.checkbox("TikTok", key="up_tt")
        with up_col3:
            up_limit = st.number_input("Jumlah klip teratas (0 = semua)", 0, 20, 3, key="up_limit")
        if st.button("🚀 Upload sekarang", key="up_go", disabled=not (do_yt or do_tt)):
            from core.publisher import upload_clips
            with st.spinner("Mengupload..."):
                report = upload_clips(processed_clips, youtube=do_yt, tiktok=do_tt, limit=int(up_limit))
            st.json(report)

    st.success(f"✅ Semua file tersimpan di: `{output_dir}`")
    st.info("💡 **Tips Selanjutnya**: Upload ke TikTok/Shorts/Reels dengan caption menarik + hashtag #ViralAutoKlip #Shorts")

if __name__ == "__main__":
    main()
