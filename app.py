import streamlit as st
import pandas as pd
import plotly.express as px
from datetime import datetime, timezone, timedelta
import isodate
import re
import json
import secrets as _secrets
from pathlib import Path

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# ── Country / Region config ───────────────────────────────────────────────────
# q: localized search query sent to YouTube — boosts regional relevance
COUNTRIES: dict[str, dict] = {
    "🇺🇸 United States":  {"code": "US", "langs": frozenset(["en"]),                              "rl": "en",  "script": "latin",  "q": "#shorts trending viral"},
    "🇬🇧 United Kingdom": {"code": "GB", "langs": frozenset(["en"]),                              "rl": "en",  "script": "latin",  "q": "#shorts trending viral"},
    "🇪🇸 Spain":          {"code": "ES", "langs": frozenset(["es"]),                              "rl": "es",  "script": "latin",  "q": "#shorts tendencias viral"},
    "🇧🇷 Brazil":         {"code": "BR", "langs": frozenset(["pt"]),                              "rl": "pt",  "script": "latin",  "q": "#shorts tendência viral"},
    "🇩🇪 Germany":        {"code": "DE", "langs": frozenset(["de"]),                              "rl": "de",  "script": "latin",  "q": "#shorts trend viral"},
    "🇫🇷 France":         {"code": "FR", "langs": frozenset(["fr"]),                              "rl": "fr",  "script": "latin",  "q": "#shorts tendance viral"},
    "🇮🇳 India":          {"code": "IN", "langs": frozenset(["hi","en","ta","te","mr","bn","gu"]), "rl": "hi",  "script": "india",  "q": "#shorts trending viral"},
    "🇯🇵 Japan":          {"code": "JP", "langs": frozenset(["ja"]),                              "rl": "ja",  "script": "cjk",    "q": "#shorts おすすめ トレンド"},
}

_LATIN_WORD_RE  = re.compile(r'[a-zA-Z]{3,}')
_HAS_LATIN_RE   = re.compile(r'[a-zA-Z]')
_CLEAN_TITLE_RE = re.compile(r'[#\[\]@|]')

_DEVANAGARI_RE    = re.compile(r'[ऀ-ॿঀ-৿઀-૿஀-௿ఀ-೿ഀ-ൿ]')
_CJK_KANA_RE      = re.compile(r'[぀-ゟ゠-ヿ一-鿿㐀-䶿]')
_CYRILLIC_RE      = re.compile(r'[Ѐ-ӿԀ-ԯ]')  # explicit Cyrillic geo-filter
_HINGLISH_RE      = re.compile(
    r'\b(?:ke|ko|ki|hai|mein|ne|aur|yeh|woh|bhai|desi|kya|hua|banayi)\b',
    re.IGNORECASE,
)

# Content-format detection
_STREAM_RE  = re.compile(
    r'\b(?:twitch|kick|stream(?:er|ing)?|gameplay|gaming|gta|minecraft|fortnite|'
    r'стрим|нарезка|live\s+stream|vod|esport)\b',
    re.IGNORECASE,
)
_PODCAST_RE = re.compile(
    r'\b(?:podcast|interview|ep\.?\s*\d+|episode|hosted\s+by|подкаст|интервью|'
    r'talk\s+show|mic\s+check|sit\s*down\s*with)\b',
    re.IGNORECASE,
)

_HOSTILE_RE = re.compile(
    r'[ऀ-ॿঀ-৿਀-૿଀-௿'
    r'ఀ-೿ഀ-ൿ'
    r'؀-ۿݐ-ݿࢠ-ࣿﭐ-﷿'
    r'Ѐ-ӿԀ-ԯ'
    r'一-鿿㐀-䶿぀-ゟ゠-ヿ'
    r'가-힯฀-๿א-ת]'
)
_INDIA_HOSTILE_RE = re.compile(
    r'[一-鿿぀-ヿ가-힯'
    r'Ѐ-ӿԀ-ԯ'
    r'؀-ۿݐ-ݿࢠ-ࣿ]'
)

# ══════════════════════════════════════════════════════════════════════════════
#  CLIENT KEY DATABASE  (local JSON file)
# ══════════════════════════════════════════════════════════════════════════════

CLIENTS_FILE = Path(__file__).parent / "clients.json"

def _load_clients() -> list[dict]:
    """Return list of client records: [{key, label, created_at}]."""
    if not CLIENTS_FILE.exists():
        CLIENTS_FILE.write_text("[]", encoding="utf-8")
    try:
        return json.loads(CLIENTS_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []

def _save_clients(clients: list[dict]) -> None:
    CLIENTS_FILE.write_text(json.dumps(clients, indent=2, ensure_ascii=False), encoding="utf-8")

def add_client_key(label: str = "") -> str:
    """Generate a TR-XXXX-XXXX key, persist it, return the key string."""
    part = lambda: _secrets.token_hex(2).upper()
    new_key = f"TR-{part()}-{part()}"
    clients = _load_clients()
    clients.append({
        "key": new_key,
        "label": label or f"Client #{len(clients) + 1}",
        "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
    })
    _save_clients(clients)
    return new_key

def revoke_client_key(key: str) -> None:
    """Remove a key from the database."""
    clients = [c for c in _load_clients() if c["key"] != key]
    _save_clients(clients)

def client_keys_set() -> set[str]:
    return {c["key"] for c in _load_clients()}


# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="TrendRadar — YouTube Shorts",
    page_icon="🚀",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── CSS ───────────────────────────────────────────────────────────────────────
st.markdown("""
<style>
  @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700;800&display=swap');
  html, body, [class*="css"] { font-family: 'Inter', sans-serif; }

  .hero {
    background: linear-gradient(135deg,#6c63ff 0%,#e040fb 50%,#ff5252 100%);
    border-radius: 20px; padding: 36px 40px; margin-bottom: 28px;
    box-shadow: 0 8px 32px rgba(108,99,255,.4);
  }
  .hero h1 { color:#fff; font-size:2.4rem; font-weight:800; margin:0; }
  .hero p  { color:rgba(255,255,255,.85); font-size:1.05rem; margin:8px 0 0; }

  .auth-container {
    max-width: 440px; margin: 60px auto; padding: 40px;
    background:#13131f; border:1px solid #2a2a3e; border-radius:20px;
    box-shadow: 0 8px 32px rgba(0,0,0,.5);
  }
  .auth-container h2 { color:#f0f0ff; font-size:1.5rem; margin:0 0 8px; }
  .auth-container p  { color:#9ca3af; font-size:.9rem; margin:0 0 24px; }

  .trend-card {
    background:#13131f; border:1px solid #2a2a3e; border-radius:20px;
    padding:28px 32px; margin-bottom:20px; transition:border-color .2s;
  }
  .trend-card:hover { border-color:#6c63ff; }

  .badge {
    display:inline-block; padding:4px 12px; border-radius:999px;
    font-size:.75rem; font-weight:700; margin-right:8px;
  }
  .badge-fire   { background:#ff5252; color:#fff; }
  .badge-rising { background:#ff9800; color:#fff; }
  .badge-new    { background:#00bcd4; color:#fff; }

  .recipe-block {
    background:#0d1117; border-left:4px solid #e040fb;
    border-radius:0 12px 12px 0; padding:16px 20px; margin-top:16px;
  }
  .recipe-block h4 { color:#e040fb; font-size:.9rem; margin:0 0 10px; letter-spacing:.08em; }

  .hook-box {
    background:#161625; border:1px dashed #6c63ff; border-radius:10px;
    padding:14px 16px; font-size:1rem; color:#f0f0ff;
    font-style:italic; margin:10px 0;
  }
  .sound-pill {
    display:inline-flex; align-items:center; gap:8px;
    background:#1e1e30; border:1px solid #6c63ff; border-radius:999px;
    padding:6px 14px; font-size:.85rem; color:#a78bfa;
  }
  .capcut-step { display:flex; align-items:flex-start; gap:14px; margin:8px 0; }
  .step-num {
    min-width:28px; height:28px;
    background:linear-gradient(135deg,#6c63ff,#e040fb);
    border-radius:50%; display:flex; align-items:center;
    justify-content:center; font-size:.75rem; font-weight:700; color:#fff;
  }
  .step-text { color:#d1d5db; font-size:.88rem; line-height:1.5; }
  .step-text strong { color:#f0f0ff; }

  a { color:#a78bfa !important; text-decoration:none; }
  a:hover { text-decoration:underline; }

  section[data-testid="stSidebar"] {
    background:#0d0d18 !important; border-right:1px solid #1e1e30;
  }
  section[data-testid="stSidebar"] * { color:#d1d5db; }
</style>
""", unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════════════════════
#  1. AUTH GATE — role-based (admin / user)
# ══════════════════════════════════════════════════════════════════════════════

if "role" not in st.session_state:
    st.session_state["role"] = None

if st.session_state["role"] is None:
    st.markdown("""
<div class="hero">
  <h1>🔐 TrendRadar</h1>
  <p>Enter your access key to unlock the trend intelligence platform.</p>
</div>
""", unsafe_allow_html=True)

    _, col, _ = st.columns([1, 2, 1])
    with col:
        key_input = st.text_input(
            "Access Key",
            type="password",
            placeholder="e.g. TR-A1B2-C3D4",
            label_visibility="visible",
        )
        if st.button("🔓 Unlock Access", use_container_width=True, type="primary"):
            k = key_input.strip()
            admin_keys = list(st.secrets.get("ADMIN_KEYS", []))
            if k and k in admin_keys:
                st.session_state["role"] = "admin"
                st.rerun()
            elif k and k in client_keys_set():
                st.session_state["role"] = "user"
                st.rerun()
            else:
                st.error("❌ Invalid access key. Please contact support to get one.")
        st.caption("Access keys are issued per client. Do not share yours.")

    st.stop()


# ══════════════════════════════════════════════════════════════════════════════
#  YOUTUBE API HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def get_youtube():
    api_key = st.secrets.get("YOUTUBE_API_KEY", "")
    return build("youtube", "v3", developerKey=api_key, cache_discovery=False)

def is_short(item):
    duration_sec = 0
    raw = item["contentDetails"].get("duration", "PT0S")
    try:
        duration_sec = int(isodate.parse_duration(raw).total_seconds())
    except Exception:
        pass
    title = item["snippet"].get("title", "").lower()
    tags  = " ".join(item["snippet"].get("tags", [])).lower()
    has_tag = "#shorts" in title or "#shorts" in tags or "#short" in title
    return duration_sec <= 60 or has_tag

def is_target_language(title: str, desc: str, snippet: dict, country_cfg: dict) -> bool:
    """
    Three-layer language filter + hard geo-filter for latin-script countries.

    Layer 0 — Hard geo-filter : reject Cyrillic/Devanagari in title for EN/ES/PT/DE/FR markets
    Layer 1 — YouTube metadata : fastest, most authoritative
    Layer 2 — Script detection : Unicode range regex
    Layer 3 — Minimum content : at least one real word in expected script
    """
    target_langs = country_cfg["langs"]
    script_mode  = country_cfg["script"]
    text = title + " " + desc[:100]

    # ── Layer 0: Hard geo-filter for latin markets ───────────────────────────
    if script_mode == "latin":
        if _CYRILLIC_RE.search(title) or _DEVANAGARI_RE.search(title):
            return False
        if _HINGLISH_RE.search(title):
            return False

    # ── Layer 1: YouTube metadata ─────────────────────────────────────────────
    for field in ("defaultLanguage", "defaultAudioLanguage"):
        lang = (snippet.get(field) or "").split("-")[0].lower()
        if lang:
            return lang in target_langs

    # ── Layer 2: Script presence / absence ───────────────────────────────────
    if script_mode == "cjk":
        return bool(_CJK_KANA_RE.search(title))

    if script_mode == "india":
        if _INDIA_HOSTILE_RE.search(text):
            return False
        return bool(_DEVANAGARI_RE.search(title)) or bool(_LATIN_WORD_RE.search(title))

    # script_mode == "latin"
    if _HOSTILE_RE.search(text):
        return False
    return bool(_LATIN_WORD_RE.search(title))

def hours_since(published_at: str) -> float:
    pub = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
    now = datetime.now(timezone.utc)
    return max((now - pub).total_seconds() / 3600, 0.1)

def velocity_score(views: int, age_hours: float) -> float:
    return round(views / age_hours, 0)

def categorise(title: str, description: str) -> str:
    if not _HAS_LATIN_RE.search(title):
        return "General"
    text = (title + " " + description).lower()
    rules = {
        "Finance / Money":    ["money","income","salary","invest","crypto","earn","revenue","profit","rich","wealth"],
        "Fitness / Health":   ["gym","workout","fitness","diet","calories","muscle","weight","run","exercise","sleep"],
        "Productivity / AI":  ["chatgpt","ai","productivity","notion","hack","workflow","automation","gpt","claude","tool"],
        "Tech / Gadgets":     ["iphone","android","gadget","amazon","tech","review","unboxing","laptop","phone","device"],
        "Creator Tools":      ["capcut","premiere","edit","youtube","tiktok","instagram","content","creator","viral","views"],
        "Food / Recipe":      ["recipe","food","cook","meal","eat","drink","kitchen","chef","taste","bake"],
        "Fashion / Beauty":   ["outfit","fashion","makeup","beauty","skincare","style","clothes","aesthetic","look","vibe"],
        "Motivation":         ["motivat","mindset","success","hustle","grind","discipline","goals","life","change","growth"],
    }
    for niche, keywords in rules.items():
        if any(k in text for k in keywords):
            return niche
    return "General"

def bpm_for_niche(niche: str) -> int:
    return {
        "Finance / Money": 130, "Fitness / Health": 145,
        "Productivity / AI": 128, "Tech / Gadgets": 115,
        "Creator Tools": 155, "Food / Recipe": 100,
        "Fashion / Beauty": 120, "Motivation": 140,
    }.get(niche, 120)

def pace_for_niche(niche: str) -> tuple[str, int, str]:
    fast  = ("Very fast cuts", 28, "Hard cut + beat sync")
    med   = ("Medium cuts",    14, "Smooth fade")
    slow  = ("Slow & cinematic", 7, "Cross-dissolve")
    nfast = ("Fast cuts",      22, "Zoom + glitch")
    return {
        "Finance / Money":   nfast,
        "Fitness / Health":  fast,
        "Productivity / AI": nfast,
        "Tech / Gadgets":    med,
        "Creator Tools":     fast,
        "Food / Recipe":     slow,
        "Fashion / Beauty":  med,
        "Motivation":        fast,
    }.get(niche, med)

def sound_for_niche(niche: str) -> dict:
    library = {
        "Finance / Money":   {"name":"Metamorphosis","artist":"Interworld","vibe":"Epic / Cinematic","search":"Interworld Metamorphosis"},
        "Fitness / Health":  {"name":"Power","artist":"Kanye West (sped up)","vibe":"Hype / Energetic","search":"Power Kanye sped up shorts"},
        "Productivity / AI": {"name":"Gimme More","artist":"Britney Spears (sped up)","vibe":"Trending TikTok","search":"Gimme More sped up trending"},
        "Tech / Gadgets":    {"name":"Aesthetic","artist":"Tollan Kim","vibe":"Chill / Lo-Fi","search":"Tollan Kim Aesthetic lofi"},
        "Creator Tools":     {"name":"Industry Baby","artist":"Lil Nas X (sped up)","vibe":"Hype / Energetic","search":"Industry Baby sped up"},
        "Food / Recipe":     {"name":"Good Days","artist":"SZA (slowed)","vibe":"Smooth / Dreamy","search":"SZA Good Days slowed reverb"},
        "Fashion / Beauty":  {"name":"Levitating","artist":"Dua Lipa (sped up)","vibe":"Upbeat / Trendy","search":"Levitating Dua Lipa sped up"},
        "Motivation":        {"name":"GODS","artist":"NewJeans","vibe":"Cinematic / Building","search":"NewJeans GODS shorts trending"},
        "General":           {"name":"Monkeys Spinning Monkeys","artist":"Kevin MacLeod","vibe":"Fun / Viral","search":"trending shorts sound 2024"},
    }
    s = library.get(niche, library["General"])
    s["bpm"] = bpm_for_niche(niche)
    s["link"] = f"https://www.youtube.com/results?search_query={s['search'].replace(' ', '+')}"
    return s

def generate_hooks(title: str, niche: str) -> list[str]:
    short_title = title[:50]
    return [
        f'"{short_title}" — you need to see this 👀',
        f"Nobody talks about this ({niche.split('/')[0].strip()} secret 🤫)",
        f"I tried this for 7 days… here's what happened 🤯",
    ]

def generate_capcut_steps(niche: str, title: str) -> list[tuple[str, str]]:
    templates = {
        "Finance / Money": [
            ("0:00–0:02", "Income/result screenshot — blur the number slightly for curiosity"),
            ("0:02–0:06", "Reveal the number with zoom-in + sound effect"),
            ("0:06–0:14", "Fast montage: laptop, dashboard, Stripe/PayPal notifications"),
            ("0:14–0:22", "Breakdown text slide — dark background, numbered list"),
            ("0:22–0:27", "Lifestyle reward scene — short & aspirational"),
            ("0:27–0:30", "CTA: 'Free guide in bio 👇' + subscribe button"),
        ],
        "Fitness / Health": [
            ("0:00–0:02", "Before vs After teaser — side by side, text overlay"),
            ("0:02–0:08", "Morning routine montage — alarm, stretching, water"),
            ("0:08–0:18", "3 key exercises — text with sets/reps, fast cuts on beat"),
            ("0:18–0:24", "Results tracking: app screenshot or mirror shot"),
            ("0:24–0:28", "Protein/meal prep ASMR — satisfying close-up"),
            ("0:28–0:30", "CTA: 'Full program pinned 📌'"),
        ],
        "Productivity / AI": [
            ("0:00–0:02", "Hook text on black — bold white font, glitch effect"),
            ("0:02–0:06", "Screen-record of AI tool — zoom in on key output"),
            ("0:06–0:14", "Fast demo: before (manual) → after (AI) comparison"),
            ("0:14–0:20", "Key result highlighted with text overlay + arrow"),
            ("0:20–0:26", "Your face reaction — surprised / nodding"),
            ("0:26–0:30", "CTA: 'Save for later 🔖 + follow for more'"),
        ],
        "Tech / Gadgets": [
            ("0:00–0:02", "Product close-up, mysterious lighting — no context"),
            ("0:02–0:07", "Unboxing in slow motion, satisfying sounds"),
            ("0:07–0:16", "Before / After split screen — problem solved"),
            ("0:16–0:23", "Detailed use-case demo with text callouts"),
            ("0:23–0:27", "Price tag reveal: 'Only $X on Amazon'"),
            ("0:27–0:30", "CTA: 'Link in bio 👇' + thumbs up reaction"),
        ],
        "Creator Tools": [
            ("0:00–0:02", "Screen record of tool — text: 'this is free'"),
            ("0:02–0:06", "Speed demo: raw → finished in 30 seconds"),
            ("0:06–0:15", "Step-by-step walkthrough with numbered text overlays"),
            ("0:15–0:22", "Side-by-side: without tool vs with tool"),
            ("0:22–0:27", "Views/stats growing — social proof"),
            ("0:27–0:30", "CTA: 'Tutorial link in bio — free 🔗'"),
        ],
    }
    default = [
        ("0:00–0:02", f"Hook text: first 3 words of '{title[:30]}…'"),
        ("0:02–0:08", "Establish the problem or situation — fast, visual"),
        ("0:08–0:18", "Core content — 3 key points, text overlays on each"),
        ("0:18–0:24", "Proof / result / reaction shot"),
        ("0:24–0:28", "Key takeaway text slide — bold, centred"),
        ("0:28–0:30", "CTA: 'Follow for more + comment your question'"),
    ]
    return templates.get(niche, default)

def generate_mj_prompt(title: str, niche: str) -> str:
    topic = _CLEAN_TITLE_RE.sub('', title).strip()[:60]
    style_map = {
        "Finance / Money":   "dark luxury penthouse office, gold and black color palette, dramatic rim lighting, cinematic depth of field",
        "Fitness / Health":  "high-end gym interior, neon accent lighting, motivational atmosphere, mist and blue tones",
        "Productivity / AI": "futuristic holographic interface, dark tech room, glowing blue code, cyberpunk aesthetic",
        "Tech / Gadgets":    "sleek product studio, dark gradient background, soft spotlight, minimalist tech vibe",
        "Creator Tools":     "professional video editing studio, dual monitors glowing, moody purple and orange lighting",
        "Food / Recipe":     "cinematic kitchen, warm golden hour light, shallow depth of field, steam rising from dish",
        "Fashion / Beauty":  "high-fashion editorial backdrop, soft studio lighting, pastel tones, luxury aesthetic",
        "Motivation":        "epic mountain sunrise panorama, dramatic clouds, golden light rays, inspirational mood",
        "General":           "cinematic urban environment, dramatic lighting, moody color grading, high contrast",
    }
    style = style_map.get(niche, style_map["General"])
    return (
        f"/imagine prompt: cinematic vertical background for a YouTube Short about {topic}, "
        f"{style}, highly detailed, photorealistic, 8k --ar 9:16 --v 6.1 --style raw"
    )

def format_count(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n // 1_000}K"
    return str(n)

def badge_for_velocity(vel: float, max_vel: float) -> tuple[str, str]:
    ratio = vel / max_vel if max_vel else 0
    if ratio >= 0.7:
        return "🔥 VIRAL NOW", "badge-fire"
    if ratio >= 0.35:
        return "📈 RISING", "badge-rising"
    return "🆕 NEW", "badge-new"

def tag_content_format(title: str, desc: str) -> str:
    """Classify video into a broad creator-format bucket."""
    text = title + " " + desc[:300]
    if _STREAM_RE.search(text):
        return "🎮 Stream / Gaming"
    if _PODCAST_RE.search(text):
        return "🎙️ Podcast / Interview"
    return "🎬 Original / Creator"


# ══════════════════════════════════════════════════════════════════════════════
#  2. FETCH DATA — pagination up to 200 videos (4 × 50)
# ══════════════════════════════════════════════════════════════════════════════

@st.cache_data(ttl=900, show_spinner=False)
def fetch_trending_shorts(target_count: int, region_code: str, country_name: str) -> list[dict]:
    yt = get_youtube()
    country_cfg = COUNTRIES[country_name]

    # ── Step 1: collect video IDs via paginated search.list ──────────────────
    video_ids: list[str] = []
    page_token: str | None = None
    max_pages = max(1, target_count // 50)          # e.g. 200 → 4 pages
    published_after = (
        datetime.now(timezone.utc).replace(microsecond=0) - timedelta(days=3)
    ).isoformat()

    for _ in range(max_pages):
        search_kwargs: dict = dict(
            part="snippet",
            q=country_cfg.get("q", "#shorts"),
            type="video",
            videoDuration="short",
            regionCode=region_code,
            order="viewCount",
            maxResults=50,
            publishedAfter=published_after,
        )
        if country_cfg["rl"]:
            search_kwargs["relevanceLanguage"] = country_cfg["rl"]
        if page_token:
            search_kwargs["pageToken"] = page_token

        search_resp = yt.search().list(**search_kwargs).execute()

        for item in search_resp.get("items", []):
            if item["id"].get("kind") == "youtube#video":
                video_ids.append(item["id"]["videoId"])

        page_token = search_resp.get("nextPageToken")
        if not page_token:
            break

    if not video_ids:
        return []

    # Deduplicate while preserving order
    seen: set[str] = set()
    unique_ids = [vid for vid in video_ids if not (vid in seen or seen.add(vid))]

    # ── Step 2: fetch full details in batches of 50 ───────────────────────────
    all_items: list[dict] = []
    for i in range(0, len(unique_ids), 50):
        batch = unique_ids[i : i + 50]
        stats_resp = yt.videos().list(
            part="snippet,statistics,contentDetails",
            id=",".join(batch),
        ).execute()
        all_items.extend(stats_resp.get("items", []))

    # ── Step 3: filter, score, build result list ──────────────────────────────
    results: list[dict] = []
    for item in all_items:
        if not is_short(item):
            continue

        snippet = item["snippet"]
        title   = snippet.get("title", "Untitled")
        desc    = snippet.get("description", "")

        if not is_target_language(title, desc, snippet, country_cfg):
            continue

        stats    = item.get("statistics", {})
        pub_at   = snippet.get("publishedAt", "")
        vid_id   = item["id"]

        views    = int(stats.get("viewCount", 0))
        likes    = int(stats.get("likeCount", 0))
        comments = int(stats.get("commentCount", 0))
        age_h    = hours_since(pub_at)
        vel      = velocity_score(views, age_h)
        niche          = categorise(title, desc)
        content_format = tag_content_format(title, desc)
        pace_lbl, cpm, transition = pace_for_niche(niche)
        hooks    = generate_hooks(title, niche)
        age_str  = (f"{age_h:.0f}h ago" if age_h < 48 else f"{age_h/24:.0f}d ago")

        results.append({
            "id":             vid_id,
            "title":          title,
            "niche":          niche,
            "content_format": content_format,
            "views":          views,
            "likes":          likes,
            "comments":       comments,
            "age_hours":      round(age_h, 1),
            "age_str":        age_str,
            "velocity":       vel,
            "velocity_score": 0.0,
            "engagement":     round((likes + comments) / max(views, 1) * 100, 2),
            "sound":          sound_for_niche(niche),
            "pace_label":     pace_lbl,
            "cuts_per_min":   cpm,
            "transition":     transition,
            "hooks":          hooks,
            "capcut_steps":   generate_capcut_steps(niche, title),
            "mj_prompt":      generate_mj_prompt(title, niche),
            "thumb":          snippet.get("thumbnails", {}).get("high", {}).get("url", ""),
            "url":            f"https://youtube.com/shorts/{vid_id}",
        })

    if results:
        max_vel = max(r["velocity"] for r in results)
        for r in results:
            r["velocity_score"] = round(r["velocity"] / max_vel * 100, 1)
            r["hot_label"], r["badge"] = badge_for_velocity(r["velocity"], max_vel)

    return results


# ══════════════════════════════════════════════════════════════════════════════
#  SIDEBAR
# ══════════════════════════════════════════════════════════════════════════════

with st.sidebar:
    # ── Role badge ────────────────────────────────────────────────────────────
    role = st.session_state["role"]
    if role == "admin":
        st.markdown(
            '<div style="background:#1e1e30;border:1px solid #6c63ff;border-radius:8px;'
            'padding:6px 12px;font-size:.8rem;color:#a78bfa;margin-bottom:8px;">'
            '🛡️ Logged in as <strong>Admin</strong></div>',
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            '<div style="background:#1e1e30;border:1px solid #2a2a3e;border-radius:8px;'
            'padding:6px 12px;font-size:.8rem;color:#6b7280;margin-bottom:8px;">'
            '👤 Logged in as <strong>Client</strong></div>',
            unsafe_allow_html=True,
        )

    st.markdown("## ⚙️ Filters")
    st.markdown("---")

    country_name = st.selectbox(
        "🌍 Country / Region",
        list(COUNTRIES.keys()),
        index=0,
        help="Filters both the YouTube API region and the language of results",
    )
    selected_country = COUNTRIES[country_name]

    col_refresh, col_logout = st.columns([3, 2])
    with col_refresh:
        if st.button("🔄 Refresh data", use_container_width=True):
            st.cache_data.clear()
            st.rerun()
    with col_logout:
        if st.button("🚪 Logout", use_container_width=True):
            st.session_state["role"] = None
            st.rerun()

    sort_by = st.selectbox(
        "Sort by",
        ["🚀 Velocity Score", "👁️ Total Views", "❤️ Engagement Rate"],
    )
    min_vel = st.slider("Min Velocity Score", 0, 100, 0, 5)

    st.markdown("---")
    st.markdown("### 📖 How to use")
    st.markdown("""
1. Pick your **Country** at the top
2. Sort by **Velocity Score** — top = blowing up *right now*
3. Open **CapCut Recipe** for a ready-made script
4. Use one of the **Hook Texts** as your opening frame
5. Post within **24h** of the trend peak
    """)
    st.markdown("---")
    st.caption("🔄 Live data · refreshes every 15 min\nv0.6 · TrendRadar · Global")

    # ── Admin Dashboard ───────────────────────────────────────────────────────
    if role == "admin":
        st.markdown("---")
        with st.expander("🛠️ Admin Dashboard", expanded=True):
            # Generate new key
            st.markdown("**Generate Client Key**")
            new_label = st.text_input(
                "Client label (optional)",
                placeholder="e.g. John Smith / Agency X",
                key="admin_label",
            )
            if st.button("➕ Generate New Client Key", use_container_width=True, type="primary"):
                new_key = add_client_key(label=new_label)
                st.session_state["last_generated_key"] = new_key

            if "last_generated_key" in st.session_state:
                st.success(f"New key generated — copy it now:")
                st.code(st.session_state["last_generated_key"], language=None)

            st.markdown("---")

            # Clear cache
            if st.button("🗑️ Clear Global Cache", use_container_width=True):
                st.cache_data.clear()
                st.success("Cache cleared.")

            st.markdown("---")

            # Active client keys
            clients = _load_clients()
            st.markdown(f"**Active Client Keys** ({len(clients)})")
            if not clients:
                st.caption("No client keys yet.")
            else:
                for c in clients:
                    col_info, col_btn = st.columns([3, 1])
                    with col_info:
                        st.markdown(
                            f'<div style="font-size:.8rem;color:#a78bfa;font-family:monospace;">'
                            f'{c["key"]}</div>'
                            f'<div style="font-size:.72rem;color:#6b7280;">'
                            f'{c.get("label","")} · {c.get("created_at","")}</div>',
                            unsafe_allow_html=True,
                        )
                    with col_btn:
                        if st.button("✕", key=f"revoke_{c['key']}", help="Revoke access"):
                            revoke_client_key(c["key"])
                            if st.session_state.get("last_generated_key") == c["key"]:
                                del st.session_state["last_generated_key"]
                            st.rerun()


# ══════════════════════════════════════════════════════════════════════════════
#  HEADER
# ══════════════════════════════════════════════════════════════════════════════

st.markdown(f"""
<div class="hero">
  <h1>🚀 TrendRadar</h1>
  <p>Global YouTube Shorts velocity engine — pick a country, find what's going viral <em>right now</em>, get a ready-made CapCut recipe.</p>
</div>
""", unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════════════════════
#  LOAD DATA  (200 videos = 4 pages × 50)
# ══════════════════════════════════════════════════════════════════════════════

with st.spinner(f"Fetching up to 200 live Shorts for {country_name}…"):
    try:
        all_trends = fetch_trending_shorts(200, selected_country["code"], country_name)
    except HttpError as e:
        st.error(f"YouTube API error: {e}")
        st.stop()

if not all_trends:
    st.warning("No Shorts found in current trending list. Try refreshing.")
    st.stop()

# ── Apply filters ─────────────────────────────────────────────────────────────
all_niches   = sorted(set(t["niche"]          for t in all_trends))
all_formats  = sorted(set(t["content_format"] for t in all_trends))
with st.sidebar:
    selected_niches  = st.multiselect("Niche",  all_niches,  default=all_niches)
    selected_formats = st.multiselect(
        "🎯 Content Format",
        all_formats,
        default=all_formats,
        help="Filter by creator format: stream clips, podcasts, or original short-form content",
    )

filtered = [
    t for t in all_trends
    if t["niche"]          in selected_niches
    and t["content_format"] in selected_formats
    and t["velocity_score"] >= min_vel
]

sort_map = {
    "🚀 Velocity Score":  lambda t: t["velocity_score"],
    "👁️ Total Views":     lambda t: t["views"],
    "❤️ Engagement Rate": lambda t: t["engagement"],
}
filtered.sort(key=sort_map[sort_by], reverse=True)


# ══════════════════════════════════════════════════════════════════════════════
#  SUMMARY METRICS
# ══════════════════════════════════════════════════════════════════════════════

total_views = sum(t["views"] for t in filtered)
avg_vel     = round(sum(t["velocity_score"] for t in filtered) / max(len(filtered), 1), 1)
fire_count  = sum(1 for t in filtered if t["badge"] == "badge-fire")
top_niche   = max(filtered, key=lambda t: t["velocity_score"])["niche"].split("/")[0].strip() if filtered else "—"

c1, c2, c3, c4 = st.columns(4)
c1.metric("🔥 Viral Now",    fire_count)
c2.metric("📊 Avg Velocity", f"{avg_vel}/100")
c3.metric("👁️ Total Views",  format_count(total_views))
c4.metric("🏆 Top Niche",    top_niche)

st.markdown("<br>", unsafe_allow_html=True)


# ── Velocity chart ────────────────────────────────────────────────────────────
with st.expander("📈 Velocity Chart — compare all trends", expanded=False):
    df = pd.DataFrame([{
        "Title": t["title"][:42] + "…" if len(t["title"]) > 42 else t["title"],
        "Velocity Score": t["velocity_score"],
        "Niche": t["niche"],
    } for t in filtered[:20]])
    fig = px.bar(
        df, x="Velocity Score", y="Title", color="Niche",
        orientation="h", text="Velocity Score",
        color_discrete_sequence=["#6c63ff","#e040fb","#ff5252","#ff9800","#00bcd4","#4caf50","#f06292","#80cbc4"],
    )
    fig.update_layout(
        paper_bgcolor="#0a0a0f", plot_bgcolor="#13131f",
        font_color="#d1d5db", height=420,
        margin=dict(l=0, r=20, t=10, b=10),
        yaxis=dict(tickfont=dict(size=10)),
        legend=dict(bgcolor="#0a0a0f", bordercolor="#2a2a3e"),
    )
    fig.update_traces(textposition="outside", textfont_color="#f0f0ff")
    st.plotly_chart(fig, use_container_width=True)


st.markdown(f"### Found **{len(filtered)}** trending Shorts")
st.markdown("---")


# ══════════════════════════════════════════════════════════════════════════════
#  TREND CARDS
# ══════════════════════════════════════════════════════════════════════════════

for trend in filtered:
    vel   = trend["velocity_score"]
    bar_w = int(vel)
    age_str = trend["age_str"]

    col_img, col_main = st.columns([1, 5])

    with col_img:
        if trend["thumb"]:
            st.image(trend["thumb"], use_container_width=True)

    with col_main:
        st.markdown(f"""
<div class="trend-card">
  <div style="display:flex;justify-content:space-between;align-items:flex-start;flex-wrap:wrap;gap:8px;">
    <div>
      <span class="badge {trend['badge']}">{trend['hot_label']}</span>
      <span class="badge" style="background:#1e1e30;color:#a78bfa;">{trend['niche']}</span>
      <span class="badge" style="background:#0d2d1a;color:#4ade80;border:1px solid #166534;">{trend['content_format']}</span>
    </div>
    <div style="color:#6b7280;font-size:.82rem;">posted {age_str}</div>
  </div>
  <h3 style="color:#f0f0ff;margin:14px 0 6px;font-size:1.1rem;">
    <a href="{trend['url']}" target="_blank">{trend['title']}</a>
  </h3>
  <div style="color:#9ca3af;font-size:.82rem;margin-bottom:6px;">Velocity Score</div>
  <div style="display:flex;align-items:center;gap:12px;">
    <div style="flex:1;background:#1e1e30;border-radius:999px;height:8px;">
      <div style="width:{bar_w}%;height:8px;border-radius:999px;
                  background:linear-gradient(90deg,#6c63ff,#e040fb);"></div>
    </div>
    <div style="font-size:1.4rem;font-weight:800;color:#a78bfa;min-width:52px;">
      {vel}<span style="font-size:.75rem;color:#6b7280;">/100</span>
    </div>
  </div>
</div>
""", unsafe_allow_html=True)

    s1, s2, s3, s4 = st.columns(4)
    s1.metric("👁️ Views",      format_count(trend["views"]))
    s2.metric("❤️ Likes",      format_count(trend["likes"]))
    s3.metric("💬 Comments",   format_count(trend["comments"]))
    s4.metric("❤️ Engagement", f"{trend['engagement']}%")

    with st.expander(f"🎬 CapCut Recipe — {trend['title'][:40]}…"):
        s = trend["sound"]
        tab1, tab2, tab3, tab4 = st.tabs(["🎵 Sound & Pace", "📝 Hook Texts", "🎬 CapCut Steps", "🎨 Midjourney"])

        with tab1:
            st.markdown(f"""
<div class="recipe-block">
  <h4>🎵 RECOMMENDED SOUND</h4>
  <div class="sound-pill">🎵 {s['name']} — {s['artist']}</div>
  <p style="color:#9ca3af;font-size:.85rem;margin:10px 0 4px;">
    BPM: <strong style="color:#f0f0ff">{s['bpm']}</strong> &nbsp;|&nbsp;
    Vibe: <strong style="color:#f0f0ff">{s['vibe']}</strong>
  </p>
  <a href="{s['link']}" target="_blank">🔍 Find on YouTube →</a>
</div>
<div class="recipe-block" style="margin-top:12px;border-color:#6c63ff;">
  <h4 style="color:#a78bfa;">✂️ EDITING PACE</h4>
  <p style="color:#f0f0ff;font-size:1rem;margin:0;"><strong>{trend['pace_label']}</strong></p>
  <p style="color:#9ca3af;font-size:.85rem;margin:6px 0 0;">
    ~{trend['cuts_per_min']} cuts/min &nbsp;|&nbsp;
    Transition: <strong style="color:#f0f0ff">{trend['transition']}</strong>
  </p>
</div>
""", unsafe_allow_html=True)

        with tab2:
            st.markdown("<div class='recipe-block'><h4>📝 HOOK OPTIONS — paste into CapCut text layer</h4>", unsafe_allow_html=True)
            for hook in trend["hooks"]:
                st.markdown(f'<div class="hook-box">"{hook}"</div>', unsafe_allow_html=True)
            st.markdown("</div>", unsafe_allow_html=True)
            st.info("💡 Use the hook as your FIRST text on screen (0:00–0:02). Big bold white font on dark background.")

        with tab3:
            st.markdown("<div class='recipe-block'><h4>🎬 CAPCUT STEP-BY-STEP SCRIPT</h4>", unsafe_allow_html=True)
            for i, (tc, instr) in enumerate(trend["capcut_steps"], 1):
                st.markdown(f"""
<div class="capcut-step">
  <div class="step-num">{i}</div>
  <div class="step-text"><strong>{tc}</strong><br>{instr}</div>
</div>""", unsafe_allow_html=True)
            st.markdown("</div>", unsafe_allow_html=True)
            st.success("✅ Total: 30 seconds | Optimized for YouTube Shorts algorithm")

        with tab4:
            st.markdown("<div class='recipe-block' style='border-color:#a78bfa;'><h4 style='color:#a78bfa;'>🎨 MIDJOURNEY BACKGROUND PROMPT</h4>", unsafe_allow_html=True)
            st.markdown("<p style='color:#9ca3af;font-size:.85rem;margin:0 0 10px;'>Paste into Midjourney to generate a 9:16 cinematic background for your Short.</p>", unsafe_allow_html=True)
            st.markdown("</div>", unsafe_allow_html=True)
            st.code(trend["mj_prompt"], language=None)
            st.caption("Click the copy icon (top-right of the box above) → paste into Midjourney Discord")

        st.markdown("---")
        st.markdown("##### 📋 Full Recipe — copy everything at once")

        full_recipe = f"""TREND: {trend['title']}
URL: {trend['url']}
Niche: {trend['niche']} | Velocity: {trend['velocity_score']}/100

━━━ HOOK TEXT (use at 0:00–0:02) ━━━
{trend['hooks'][0]}

━━━ SOUND ━━━
{s['name']} — {s['artist']}
BPM: {s['bpm']} | Vibe: {s['vibe']}
Find it: {s['link']}

━━━ EDITING PACE ━━━
{trend['pace_label']} (~{trend['cuts_per_min']} cuts/min)
Transition: {trend['transition']}

━━━ CAPCUT SCRIPT ━━━
{chr(10).join(f"{tc}  {instr}" for tc, instr in trend['capcut_steps'])}

━━━ MIDJOURNEY PROMPT ━━━
{trend['mj_prompt']}
"""
        st.code(full_recipe, language=None)

    st.markdown("---")


# ── Footer ────────────────────────────────────────────────────────────────────
st.markdown("""
<div style="text-align:center;color:#374151;font-size:.8rem;margin-top:40px;padding:20px;">
  TrendRadar v0.6 · Global · Live YouTube Data API · Built with Streamlit
</div>
""", unsafe_allow_html=True)

