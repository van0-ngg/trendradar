import streamlit as st
import pandas as pd
import plotly.express as px
from datetime import datetime, timezone, timedelta
import html
import isodate
import re
import json
import secrets as _secrets
from pathlib import Path

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# ── Country / Region config ───────────────────────────────────────────────────
COUNTRIES: dict[str, dict] = {
    "🇺🇸 United States":  {"code": "US", "langs": frozenset(["en"]),                               "rl": "en",  "script": "latin"},
    "🇬🇧 United Kingdom": {"code": "GB", "langs": frozenset(["en"]),                               "rl": "en",  "script": "latin"},
    "🇪🇸 Spain":          {"code": "ES", "langs": frozenset(["es"]),                               "rl": "es",  "script": "latin"},
    "🇧🇷 Brazil":         {"code": "BR", "langs": frozenset(["pt"]),                               "rl": "pt",  "script": "latin"},
    "🇩🇪 Germany":        {"code": "DE", "langs": frozenset(["de"]),                               "rl": "de",  "script": "latin"},
    "🇫🇷 France":         {"code": "FR", "langs": frozenset(["fr"]),                               "rl": "fr",  "script": "latin"},
    "🇮🇳 India":          {"code": "IN", "langs": frozenset(["hi","en","ta","te","mr","bn","gu"]),  "rl": "hi",  "script": "india"},
    "🇯🇵 Japan":          {"code": "JP", "langs": frozenset(["ja"]),                               "rl": "ja",  "script": "cjk"},
    "🇷🇺 Russia":         {"code": "RU", "langs": frozenset(["ru"]),                               "rl": "ru",  "script": "cyrillic"},
}

BANNED_COUNTRIES: frozenset[str] = frozenset({"IN", "PK", "BD", "ID", "VN", "TH"})
EN_MARKET_CODES:  frozenset[str] = frozenset({"US", "GB", "CA", "AU"})

# Localized search queries — boosts local-language results at the API level
LOCAL_QUERIES: dict[str, dict] = {
    "US": {"q": "#shorts",                          "lang": "en"},
    "GB": {"q": "#shorts uk",                       "lang": "en"},
    "CA": {"q": "#shorts",                          "lang": "en"},
    "AU": {"q": "#shorts",                          "lang": "en"},
    "BR": {"q": "#shorts brasil OR dança OR trend", "lang": "pt"},
    "DE": {"q": "#shorts deutsch OR trend",         "lang": "de"},
    "ES": {"q": "#shorts tendencia OR viral",       "lang": "es"},
    "FR": {"q": "#shorts tendance OR viral",        "lang": "fr"},
    "IN": {"q": "#shorts india OR trending",                              "lang": "hi"},
    "JP": {"q": "#shorts トレンド",                                        "lang": "ja"},
    "RU": {"q": "#shorts тренд | топ | шортс | тикток | рекомендации",   "lang": "ru"},
}
_LOCAL_QUERY_DEFAULT = {"q": "#shorts", "lang": "en"}

# Per-market anti-bot engagement threshold (EN strict, CIS moderate, others lenient)
_BOT_THRESHOLDS: dict[str, float] = {
    "US": 2.0, "GB": 2.0, "CA": 2.0, "AU": 2.0,  # English-speaking — strict
    "RU": 1.5,                                      # CIS — moderate
}

# Long-video thresholds are lower — viewers watch but like less often
_BOT_THRESHOLDS_LONG: dict[str, float] = {
    "US": 0.8, "GB": 0.8, "CA": 0.8, "AU": 0.8,
    "RU": 0.6,
}
_BOT_THRESHOLD_LONG_DEFAULT = 0.5

_LATIN_WORD_RE  = re.compile(r'[a-zA-Z]{3,}')
_HAS_LATIN_RE   = re.compile(r'[a-zA-Z]')
_CLEAN_TITLE_RE = re.compile(r'[#\[\]@|]')

_DEVANAGARI_RE    = re.compile(r'[ऀ-ॿঀ-৿઀-૿஀-௿ఀ-೿ഀ-ൿ]')
_CJK_KANA_RE      = re.compile(r'[぀-ゟ゠-ヿ一-鿿㐀-䶿]')
_CYRILLIC_RE      = re.compile(r'[Ѐ-ӿԀ-ԯ]')
_HINGLISH_RE      = re.compile(
    r'\b(?:ke|ko|ki|hai|mein|ne|aur|yeh|woh|bhai|desi|kya|hua|banayi)\b',
    re.IGNORECASE,
)

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
#  CLIENT KEY DATABASE
# ══════════════════════════════════════════════════════════════════════════════

CLIENTS_FILE = Path(__file__).parent / "clients.json"

def _load_clients() -> list[dict]:
    if not CLIENTS_FILE.exists():
        CLIENTS_FILE.write_text("[]", encoding="utf-8")
    try:
        return json.loads(CLIENTS_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []

def _save_clients(clients: list[dict]) -> None:
    CLIENTS_FILE.write_text(json.dumps(clients, indent=2, ensure_ascii=False), encoding="utf-8")

def add_client_key(label: str = "") -> str:
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
  @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800;900&display=swap');

  html, body, [class*="css"] {
    font-family: 'Inter', sans-serif;
    background: #08080f;
  }

  /* ── Hero ── */
  .hero {
    background: linear-gradient(135deg, #6c63ff 0%, #e040fb 45%, #ff5252 100%);
    border-radius: 24px; padding: 40px 44px; margin-bottom: 32px;
    box-shadow: 0 12px 48px rgba(108,99,255,.45);
    position: relative; overflow: hidden;
  }
  .hero::before {
    content: ""; position: absolute; inset: 0;
    background: radial-gradient(ellipse at 70% 50%, rgba(255,255,255,.08) 0%, transparent 70%);
  }
  .hero h1 { color:#fff; font-size:2.6rem; font-weight:900; margin:0; letter-spacing:-.03em; }
  .hero p  { color:rgba(255,255,255,.88); font-size:1.05rem; margin:10px 0 0; }
  .hero-meta { color:rgba(255,255,255,.6); font-size:.8rem; margin:14px 0 0; display:flex; gap:16px; flex-wrap:wrap; }
  .hero-meta span { background:rgba(0,0,0,.2); padding:3px 10px; border-radius:999px; }

  /* ── Metric cards ── */
  .metric-row { display:grid; grid-template-columns:repeat(4,1fr); gap:16px; margin-bottom:28px; }
  .metric-card {
    background: #13131f; border: 1px solid #2a2a3e;
    border-radius: 18px; padding: 22px 24px;
    transition: border-color .2s, transform .15s;
  }
  .metric-card:hover { border-color: #6c63ff; transform: translateY(-2px); }
  .metric-card .label { color:#6b7280; font-size:.78rem; font-weight:600; letter-spacing:.06em; text-transform:uppercase; margin-bottom:6px; }
  .metric-card .value { color:#f0f0ff; font-size:1.9rem; font-weight:800; line-height:1; }
  .metric-card .sub   { color:#6b7280; font-size:.78rem; margin-top:4px; }
  .metric-card.fire   { border-color:#ff5252; box-shadow:0 0 18px rgba(255,82,82,.12); }
  .metric-card.fire .value { color:#ff5252; }
  .metric-card.purple .value { color:#a78bfa; }
  .metric-card.green  .value { color:#4ade80; }

  /* ── Trend card ── */
  .trend-card {
    background: linear-gradient(145deg, #13131f 0%, #0f0f1a 100%);
    border: 1px solid #2a2a3e; border-radius: 22px;
    padding: 26px 30px; margin-bottom: 4px;
    transition: border-color .2s, box-shadow .2s;
  }
  .trend-card:hover {
    border-color: #6c63ff;
    box-shadow: 0 4px 24px rgba(108,99,255,.15);
  }

  /* ── Badges ── */
  .badge {
    display: inline-block; padding: 4px 12px; border-radius: 999px;
    font-size: .72rem; font-weight: 700; margin-right: 6px; letter-spacing:.02em;
  }
  .badge-fire   { background: linear-gradient(90deg,#ff5252,#ff9800); color:#fff; }
  .badge-rising { background: linear-gradient(90deg,#ff9800,#ffd600); color:#000; }
  .badge-new    { background: linear-gradient(90deg,#00bcd4,#00e5ff); color:#000; }

  /* ── Recipe blocks ── */
  .recipe-block {
    background: #0d1117; border-left: 4px solid #e040fb;
    border-radius: 0 14px 14px 0; padding: 18px 22px; margin-top: 14px;
  }
  .recipe-block h4 {
    color: #e040fb; font-size: .82rem; margin: 0 0 12px;
    letter-spacing: .1em; text-transform: uppercase;
  }
  .hook-box {
    background: #161625; border: 1px dashed #6c63ff; border-radius: 12px;
    padding: 14px 18px; font-size: .95rem; color: #f0f0ff;
    font-style: italic; margin: 10px 0; line-height: 1.5;
  }
  .sound-pill {
    display: inline-flex; align-items: center; gap: 8px;
    background: #1e1e30; border: 1px solid #6c63ff; border-radius: 999px;
    padding: 6px 16px; font-size: .85rem; color: #a78bfa;
  }
  .capcut-step { display: flex; align-items: flex-start; gap: 14px; margin: 10px 0; }
  .step-num {
    min-width: 28px; height: 28px;
    background: linear-gradient(135deg, #6c63ff, #e040fb);
    border-radius: 50%; display: flex; align-items: center;
    justify-content: center; font-size: .72rem; font-weight: 800; color: #fff;
    flex-shrink: 0;
  }
  .step-text { color: #d1d5db; font-size: .88rem; line-height: 1.55; }
  .step-text strong { color: #f0f0ff; }

  /* ── Auth ── */
  .auth-wrap {
    max-width: 420px; margin: 80px auto; padding: 44px;
    background: #13131f; border: 1px solid #2a2a3e; border-radius: 24px;
    box-shadow: 0 12px 48px rgba(0,0,0,.6);
  }

  /* ── Divider ── */
  .section-divider {
    height: 1px; background: linear-gradient(90deg,transparent,#2a2a3e,transparent);
    margin: 28px 0;
  }

  /* ── Links ── */
  a { color: #a78bfa !important; text-decoration: none; }
  a:hover { text-decoration: underline; }

  /* ── Sidebar ── */
  section[data-testid="stSidebar"] {
    background: linear-gradient(180deg,#0a0a18 0%,#0d0d1f 100%) !important;
    border-right: 1px solid #1e1e30;
  }
  section[data-testid="stSidebar"] * { color: #d1d5db; }
  section[data-testid="stSidebar"] .stSelectbox label,
  section[data-testid="stSidebar"] .stSlider label { color: #9ca3af !important; font-size:.85rem; }

  /* ── Hide Streamlit chrome ── */
  #MainMenu { visibility: hidden; }
  footer    { visibility: hidden; }
  header    { visibility: hidden; }

  /* ── Stats grid inside trend card ── */
  .stats-grid {
    display: grid; grid-template-columns: repeat(4, 1fr);
    gap: 12px; margin: 18px 0 0;
  }
  .stat-item {
    background: #0a0a14; border: 1px solid #1e1e30;
    border-radius: 12px; padding: 12px 14px;
    text-align: center; transition: border-color .2s;
  }
  .stat-item:hover { border-color: #6c63ff; }
  .stat-icon  { font-size: 1rem; margin-bottom: 4px; display: block; }
  .stat-value {
    font-size: 1.2rem; font-weight: 800; color: #10b981;
    line-height: 1.1; margin-bottom: 2px;
  }
  .stat-value.purple { color: #a78bfa; }
  .stat-value.orange { color: #fb923c; }
  .stat-value.blue   { color: #38bdf8; }
  .stat-label { font-size: .68rem; color: #4b5563; font-weight: 600; letter-spacing:.04em; text-transform: uppercase; }

  /* ── Streamlit overrides ── */
  div[data-testid="stExpander"] { background: #0f0f1a; border: 1px solid #2a2a3e; border-radius: 14px; }
  button[kind="primary"] { background: linear-gradient(90deg,#6c63ff,#e040fb) !important; border:none !important; }
</style>
""", unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════════════════════
#  AUTH GATE
# ══════════════════════════════════════════════════════════════════════════════

if "role" not in st.session_state:
    st.session_state["role"] = None

if st.session_state["role"] is None:
    st.markdown("""
<div class="hero" style="max-width:480px;margin:60px auto 0;">
  <h1 style="font-size:2rem;">🔐 TrendRadar</h1>
  <p>Enter your access key to unlock the trend intelligence platform.</p>
</div>
""", unsafe_allow_html=True)

    _, col, _ = st.columns([1, 2, 1])
    with col:
        st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)
        key_input = st.text_input(
            "Access Key",
            type="password",
            placeholder="TR-XXXX-XXXX",
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
                st.error("❌ Invalid access key. Contact support to get one.")
        st.caption("Access keys are issued per client. Do not share yours.")

    st.stop()


# ══════════════════════════════════════════════════════════════════════════════
#  YOUTUBE API HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def _get_api_keys() -> list[str]:
    # Supports YOUTUBE_API_KEYS = "key1,key2,key3"  OR legacy YOUTUBE_API_KEY = "key1"
    raw = st.secrets.get("YOUTUBE_API_KEYS") or st.secrets.get("YOUTUBE_API_KEY", "")
    if isinstance(raw, (list, tuple)):
        return [str(k).strip() for k in raw if str(k).strip()]
    return [k.strip() for k in str(raw).split(",") if k.strip()]

def get_youtube(key_index: int = 0):
    keys = _get_api_keys()
    if not keys:
        raise RuntimeError("No YouTube API key configured in secrets.")
    api_key = keys[min(key_index, len(keys) - 1)]
    return build("youtube", "v3", developerKey=api_key, cache_discovery=False)

def is_short(item: dict) -> bool:
    """True if video is ≤ 60 s OR explicitly tagged #shorts."""
    raw = item["contentDetails"].get("duration", "PT0S")
    try:
        duration_sec = int(isodate.parse_duration(raw).total_seconds())
    except Exception:
        duration_sec = 0
    title = item["snippet"].get("title", "").lower()
    tags  = " ".join(item["snippet"].get("tags", [])).lower()
    return duration_sec <= 60 or "#shorts" in title or "#short" in title or "#shorts" in tags

def is_long_video(item: dict) -> bool:
    """True if video is strictly > 5 minutes (300 s)."""
    raw = item["contentDetails"].get("duration", "PT0S")
    try:
        duration_sec = int(isodate.parse_duration(raw).total_seconds())
    except Exception:
        duration_sec = 0
    return duration_sec > 300

def is_target_language(title: str, desc: str, snippet: dict, country_cfg: dict) -> bool:
    target_langs = country_cfg["langs"]
    script_mode  = country_cfg["script"]
    text = title + " " + desc[:100]

    if script_mode == "latin":
        if _CYRILLIC_RE.search(title) or _DEVANAGARI_RE.search(title):
            return False
        if _HINGLISH_RE.search(title):
            return False

    for field in ("defaultLanguage", "defaultAudioLanguage"):
        lang = (snippet.get(field) or "").split("-")[0].lower()
        if lang:
            return lang in target_langs

    if script_mode == "cjk":
        return bool(_CJK_KANA_RE.search(title))

    if script_mode == "india":
        if _INDIA_HOSTILE_RE.search(text):
            return False
        return bool(_DEVANAGARI_RE.search(title)) or bool(_LATIN_WORD_RE.search(title))

    if script_mode == "cyrillic":
        if _CJK_KANA_RE.search(title) or _DEVANAGARI_RE.search(title):
            return False
        return bool(_CYRILLIC_RE.search(title))

    if _HOSTILE_RE.search(text):
        return False
    return bool(_LATIN_WORD_RE.search(title))

def hours_since(published_at: str) -> float:
    pub = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
    return max((datetime.now(timezone.utc) - pub).total_seconds() / 3600, 0.1)

def velocity_score(views: int, age_hours: float) -> float:
    return round(views / age_hours, 0)

def categorise(title: str, description: str) -> str:
    text = (title + " " + description).lower()
    _AI_KEYWORDS = [
        "ai ", "chatgpt", "midjourney", "elevenlabs", "gpt", "нейросеть",
        "ии ", "heygen", "artificial intelligence", "openai",
    ]
    if any(k in text for k in _AI_KEYWORDS):
        return "🤖 AI & Tech"
    if not _HAS_LATIN_RE.search(title):
        return "General"
    rules = {
        "Finance / Money":   ["money","income","salary","invest","crypto","earn","revenue","profit","rich","wealth"],
        "Fitness / Health":  ["gym","workout","fitness","diet","calories","muscle","weight","run","exercise","sleep"],
        "Productivity / AI": ["productivity","notion","hack","workflow","automation","claude","tool"],
        "Tech / Gadgets":    ["iphone","android","gadget","amazon","tech","review","unboxing","laptop","phone","device"],
        "Creator Tools":     ["capcut","premiere","edit","youtube","tiktok","instagram","content","creator","viral","views"],
        "Food / Recipe":     ["recipe","food","cook","meal","eat","drink","kitchen","chef","taste","bake"],
        "Fashion / Beauty":  ["outfit","fashion","makeup","beauty","skincare","style","clothes","aesthetic","look","vibe"],
        "Motivation":        ["motivat","mindset","success","hustle","grind","discipline","goals","life","change","growth"],
    }
    for niche, keywords in rules.items():
        if any(k in text for k in keywords):
            return niche
    return "General"

def bpm_for_niche(niche: str) -> int:
    return {
        "🤖 AI & Tech": 135, "Finance / Money": 130, "Fitness / Health": 145,
        "Productivity / AI": 128, "Tech / Gadgets": 115, "Creator Tools": 155,
        "Food / Recipe": 100, "Fashion / Beauty": 120, "Motivation": 140,
    }.get(niche, 120)

def pace_for_niche(niche: str) -> tuple[str, int, str]:
    fast  = ("Very fast cuts", 28, "Hard cut + beat sync")
    med   = ("Medium cuts",    14, "Smooth fade")
    slow  = ("Slow & cinematic", 7, "Cross-dissolve")
    nfast = ("Fast cuts",      22, "Zoom + glitch")
    return {
        "🤖 AI & Tech": nfast, "Finance / Money": nfast, "Fitness / Health": fast,
        "Productivity / AI": nfast, "Tech / Gadgets": med, "Creator Tools": fast,
        "Food / Recipe": slow, "Fashion / Beauty": med, "Motivation": fast,
    }.get(niche, med)

def sound_for_niche(niche: str) -> dict:
    library = {
        "🤖 AI & Tech":      {"name":"GODS","artist":"NewJeans","vibe":"Cinematic / Futuristic","search":"NewJeans GODS AI tech shorts trending"},
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
    s = dict(library.get(niche, library["General"]))
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
        "🤖 AI & Tech": [
            ("0:00–0:02", "Hook text on black: 'This AI can…' — glitch/digital reveal effect"),
            ("0:02–0:06", "Screen-record of AI tool output — zoom + highlight the result"),
            ("0:06–0:14", "Fast demo: before (manual) → after (AI) — hard cut on beat"),
            ("0:14–0:22", "Key capability shown with text callouts + zoomed UI"),
            ("0:22–0:27", "Real-world use case or output montage — 3 rapid cuts"),
            ("0:27–0:30", "CTA: 'Free link in bio 🔗 + follow for more AI tools'"),
        ],
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
        "🤖 AI & Tech":      "neural network visualization, deep blue and electric purple data streams, futuristic AI interface, cyberpunk neon glow, 9:16 vertical",
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
    text = title + " " + desc[:300]
    if _STREAM_RE.search(text):
        return "🎮 Stream / Gaming"
    if _PODCAST_RE.search(text):
        return "🎙️ Podcast / Interview"
    return "🎬 Original / Creator"


# ══════════════════════════════════════════════════════════════════════════════
#  FETCH DATA  — search.list · fmt="shorts" or fmt="long"
#  Shorts:  videoDuration="short" · 5 pages · ~510 quota units/refresh
#  Long:    no duration filter · 5 pages · filter locally > 300 s
# ══════════════════════════════════════════════════════════════════════════════

@st.cache_data(ttl=7200, show_spinner=False)
def fetch_trending_videos(region_code: str, country_name: str, key_index: int = 0, fmt: str = "shorts") -> list[dict]:
    yt = get_youtube(key_index)
    country_cfg = COUNTRIES[country_name]

    # ── Step 1: search.list ──────────────────────────────────────────────────
    # Shorts: last 7 days — fresh viral content
    # Long:   last 30 days — big creators post less often, videos stay relevant longer
    days = 7 if fmt == "shorts" else 30
    published_after = (
        datetime.now(timezone.utc).replace(microsecond=0) - timedelta(days=days)
    ).isoformat()

    seen_ids: set[str] = set()
    raw_ids:  list[str] = []
    page_token: str | None = None

    for _ in range(5):          # hard cap: 5 pages = up to 250 raw candidates
        if fmt == "shorts":
            local = LOCAL_QUERIES.get(country_cfg["code"], _LOCAL_QUERY_DEFAULT)
            kwargs: dict = dict(
                part="snippet",
                q=local["q"],
                type="video",
                videoDuration="short",
                regionCode=region_code,
                order="viewCount",
                maxResults=50,
                publishedAfter=published_after,
                relevanceLanguage=local["lang"],
            )
        else:
            # Empty q = purely region + view-count ranked; no keyword bias
            # No videoDuration — we filter locally for > 300 s
            # No relevanceLanguage — lets MrBeast-style global creators through
            kwargs = dict(
                part="snippet",
                q="",
                type="video",
                regionCode=region_code,
                order="viewCount",
                maxResults=50,
                publishedAfter=published_after,
            )
        if page_token:
            kwargs["pageToken"] = page_token

        resp = yt.search().list(**kwargs).execute()

        for item in resp.get("items", []):
            if item["id"].get("kind") == "youtube#video":
                vid = item["id"]["videoId"]
                if vid not in seen_ids:
                    seen_ids.add(vid)
                    raw_ids.append(vid)

        page_token = resp.get("nextPageToken")
        if not page_token:
            break

    if not raw_ids:
        return []

    # ── Step 2: fetch full details in batches of 50 ───────────────────────────
    all_items: list[dict] = []
    for i in range(0, len(raw_ids), 50):
        batch = raw_ids[i : i + 50]
        stats_resp = yt.videos().list(
            part="snippet,statistics,contentDetails",
            id=",".join(batch),
        ).execute()
        all_items.extend(stats_resp.get("items", []))

    if not all_items:
        return []

    # ── Step 3: channel → country lookup for EN markets (50 IDs/request) ────
    channel_countries: dict[str, str | None] = {}
    apply_channel_filter = country_cfg["code"] in EN_MARKET_CODES
    if apply_channel_filter:
        channel_ids = list({item["snippet"]["channelId"] for item in all_items})
        for i in range(0, len(channel_ids), 50):
            batch = channel_ids[i : i + 50]
            ch_resp = yt.channels().list(
                part="snippet",
                id=",".join(batch),
                maxResults=50,
            ).execute()
            for ch in ch_resp.get("items", []):
                channel_countries[ch["id"]] = ch["snippet"].get("country")

    # ── Step 4: filter, score, build result list ──────────────────────────────
    results: list[dict] = []
    for item in all_items:
        if fmt == "shorts":
            if not is_short(item):
                continue
        else:
            if not is_long_video(item):
                continue

        snippet = item["snippet"]
        title   = snippet.get("title", "Untitled")
        desc    = snippet.get("description", "")

        if apply_channel_filter:
            ch_country = channel_countries.get(snippet.get("channelId", ""))
            if ch_country in BANNED_COUNTRIES:
                continue

        if not is_target_language(title, desc, snippet, country_cfg):
            continue

        stats  = item.get("statistics", {})
        pub_at = snippet.get("publishedAt", "")
        vid_id = item["id"]

        views    = int(stats.get("viewCount", 0))
        likes    = int(stats.get("likeCount", 0))
        comments = int(stats.get("commentCount", 0))
        eng_rate = round((likes + comments) / max(views, 1) * 100, 2)

        if fmt == "shorts":
            bot_threshold    = _BOT_THRESHOLDS.get(country_cfg["code"], 1.0)
            suspect_threshold = 3.5
        else:
            bot_threshold    = _BOT_THRESHOLDS_LONG.get(country_cfg["code"], _BOT_THRESHOLD_LONG_DEFAULT)
            suspect_threshold = 1.0

        if views > 10_000 and eng_rate < bot_threshold:
            continue

        suspect_engagement = views > 1_000 and eng_rate < suspect_threshold

        age_h          = hours_since(pub_at)
        vel            = velocity_score(views, age_h)
        niche          = categorise(title, desc)
        content_format = tag_content_format(title, desc)
        pace_lbl, cpm, transition = pace_for_niche(niche)
        age_str        = f"{age_h:.0f}h ago" if age_h < 48 else f"{age_h/24:.0f}d ago"

        if fmt == "shorts":
            video_url = f"https://youtube.com/shorts/{vid_id}"
        else:
            video_url = f"https://youtube.com/watch?v={vid_id}"

        results.append({
            "id":               vid_id,
            "title":            title,
            "niche":            niche,
            "content_format":   content_format,
            "views":            views,
            "likes":            likes,
            "comments":         comments,
            "age_hours":        round(age_h, 1),
            "age_str":          age_str,
            "velocity":         vel,
            "velocity_score":   0.0,
            "engagement":       eng_rate,
            "suspect_engagement": suspect_engagement,
            "sound":            sound_for_niche(niche),
            "pace_label":       pace_lbl,
            "cuts_per_min":     cpm,
            "transition":       transition,
            "hooks":            generate_hooks(title, niche),
            "capcut_steps":     generate_capcut_steps(niche, title),
            "mj_prompt":        generate_mj_prompt(title, niche),
            "thumb":            snippet.get("thumbnails", {}).get("high", {}).get("url", ""),
            "url":              video_url,
        })

    # Top-50 cap: if more than 50 survived filters, keep the most-viewed ones
    if len(results) > 50:
        results.sort(key=lambda r: r["views"], reverse=True)
        results = results[:50]

    if results:
        max_vel = max(r["velocity"] for r in results)
        for r in results:
            r["velocity_score"] = round(r["velocity"] / max_vel * 100, 1)
            r["hot_label"], r["badge"] = badge_for_velocity(r["velocity"], max_vel)

    return results


# ── Key-rotation wrapper (not cached — manages session state) ────────────────
def load_trending_videos(region_code: str, country_name: str, fmt: str = "shorts") -> list[dict] | None:
    if "current_key_index" not in st.session_state:
        st.session_state.current_key_index = 0

    keys = _get_api_keys()
    if not keys:
        st.error("No YouTube API keys configured. Add YOUTUBE_API_KEYS to Streamlit secrets.")
        return None

    while st.session_state.current_key_index < len(keys):
        try:
            return fetch_trending_videos(region_code, country_name, st.session_state.current_key_index, fmt)
        except HttpError as e:
            if e.resp.status == 403 and "quotaExceeded" in str(e):
                st.session_state.current_key_index += 1
                print(f"[TrendRadar] Key exhausted. Switching to key index {st.session_state.current_key_index}")
            else:
                raise

    return None  # all keys exhausted


# ══════════════════════════════════════════════════════════════════════════════
#  SIDEBAR
# ══════════════════════════════════════════════════════════════════════════════

with st.sidebar:
    role = st.session_state["role"]

    if role == "admin":
        st.markdown(
            '<div style="background:linear-gradient(90deg,#1a1a35,#1e1530);border:1px solid #6c63ff;'
            'border-radius:10px;padding:8px 14px;font-size:.8rem;color:#a78bfa;margin-bottom:12px;">'
            '🛡️ Logged in as <strong>Admin</strong></div>',
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            '<div style="background:#13131f;border:1px solid #2a2a3e;border-radius:10px;'
            'padding:8px 14px;font-size:.8rem;color:#6b7280;margin-bottom:12px;">'
            '👤 Logged in as <strong>Client</strong></div>',
            unsafe_allow_html=True,
        )

    st.markdown("### ⚙️ Controls")

    content_fmt = st.radio(
        "📺 Content Format",
        ["Shorts (≤ 60s)", "Long Videos (> 5m)"],
        horizontal=True,
    )
    fmt_key = "shorts" if content_fmt == "Shorts (≤ 60s)" else "long"

    country_name = st.selectbox(
        "🌍 Market",
        list(COUNTRIES.keys()),
        index=0,
        help="Filters YouTube trending chart by region",
    )
    selected_country = COUNTRIES[country_name]

    sort_by = st.selectbox(
        "📊 Sort by",
        ["🚀 Velocity Score", "👁️ Total Views", "❤️ Engagement Rate"],
    )
    min_vel = st.slider("Min Velocity Score", 0, 100, 0, 5)

    col_refresh, col_logout = st.columns([3, 2])
    with col_refresh:
        if st.button("🔄 Refresh", use_container_width=True, type="primary"):
            st.cache_data.clear()
            st.session_state.current_key_index = 0
            st.rerun()
    with col_logout:
        if st.button("🚪 Logout", use_container_width=True):
            st.session_state["role"] = None
            st.rerun()

    st.markdown("---")
    st.markdown("### 📖 How to use")
    st.markdown("""
1. Pick your **Market** above
2. Sort by **Velocity Score** — top = blowing up *right now*
3. Open **CapCut Recipe** for a ready-made script
4. Use one of the **Hook Texts** as your opening frame
5. Post within **24 h** of the trend peak
    """)
    st.markdown("---")
    st.caption("🔄 Cache: 1 h · ~8 API quota units/refresh\nv0.7 · TrendRadar · Global")

    if role == "admin":
        st.markdown("---")
        with st.expander("🛠️ Admin Dashboard", expanded=True):
            st.markdown("**Generate Client Key**")
            new_label = st.text_input(
                "Client label (optional)",
                placeholder="e.g. John Smith / Agency X",
                key="admin_label",
            )
            if st.button("➕ Generate New Key", use_container_width=True, type="primary"):
                st.session_state["last_generated_key"] = add_client_key(label=new_label)

            if "last_generated_key" in st.session_state:
                st.success("New key generated — copy it now:")
                st.code(st.session_state["last_generated_key"], language=None)

            st.markdown("---")
            if st.button("🗑️ Clear Global Cache", use_container_width=True):
                st.cache_data.clear()
                st.success("Cache cleared.")

            st.markdown("---")
            clients = _load_clients()
            st.markdown(f"**Active Keys** ({len(clients)})")
            if not clients:
                st.caption("No client keys yet.")
            else:
                for c in clients:
                    col_info, col_btn = st.columns([3, 1])
                    with col_info:
                        st.markdown(
                            f'<div style="font-size:.78rem;color:#a78bfa;font-family:monospace;">{c["key"]}</div>'
                            f'<div style="font-size:.7rem;color:#6b7280;">{c.get("label","")} · {c.get("created_at","")}</div>',
                            unsafe_allow_html=True,
                        )
                    with col_btn:
                        if st.button("✕", key=f"revoke_{c['key']}", help="Revoke"):
                            revoke_client_key(c["key"])
                            if st.session_state.get("last_generated_key") == c["key"]:
                                del st.session_state["last_generated_key"]
                            st.rerun()


# ══════════════════════════════════════════════════════════════════════════════
#  HEADER
# ══════════════════════════════════════════════════════════════════════════════

now_str = datetime.now(timezone.utc).strftime("%H:%M UTC")
st.markdown(
    f'<div class="hero">'
    f'<h1>🚀 TrendRadar</h1>'
    f'<p>Official YouTube trending chart · {country_name} · real engagement only · CapCut recipe included.</p>'
    f'<div class="hero-meta">'
    f'<span>📡 Live data</span>'
    f'<span>🕒 Refreshed at {now_str}</span>'
    f'<span>💡 ~306 quota units/refresh</span>'
    f'<span>🔒 2-hour cache</span>'
    f'</div></div>',
    unsafe_allow_html=True,
)


# ══════════════════════════════════════════════════════════════════════════════
#  LOAD DATA
# ══════════════════════════════════════════════════════════════════════════════

with st.spinner(f"Loading {content_fmt} for {country_name}…"):
    try:
        all_trends = load_trending_videos(selected_country["code"], country_name, fmt_key)
    except HttpError as e:
        st.error(f"YouTube API error: {e}")
        st.stop()

if all_trends is None:
    st.warning(
        "We are experiencing unusually high traffic. "
        "Fetching the latest cached trends. "
        "Please try refreshing in a few hours."
    )
    st.stop()

if not all_trends:
    st.warning("No Shorts found in the current trending chart. Try another market or refresh.")
    st.stop()

# ── Sidebar filters (need data first) ────────────────────────────────────────
all_niches  = sorted(set(t["niche"]          for t in all_trends))
all_formats = sorted(set(t["content_format"] for t in all_trends))
with st.sidebar:
    st.markdown("---")
    st.markdown("### 🔎 Filters")
    selected_niches  = st.multiselect("Niche",  all_niches,  default=all_niches)
    selected_formats = st.multiselect(
        "Content Format", all_formats, default=all_formats,
        help="Original creator content, stream clips, or podcast snippets",
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
#  METRICS
# ══════════════════════════════════════════════════════════════════════════════

total_views = sum(t["views"] for t in filtered)
avg_vel     = round(sum(t["velocity_score"] for t in filtered) / max(len(filtered), 1), 1)
fire_count  = sum(1 for t in filtered if t["badge"] == "badge-fire")
top_niche   = max(filtered, key=lambda t: t["velocity_score"])["niche"].split("/")[0].strip() if filtered else "—"
avg_eng     = round(sum(t["engagement"] for t in filtered) / max(len(filtered), 1), 1)

st.markdown(
    f'<div class="metric-row">'
    f'<div class="metric-card fire"><div class="label">🔥 Viral Now</div><div class="value">{fire_count}</div><div class="sub">badge-fire videos</div></div>'
    f'<div class="metric-card purple"><div class="label">🚀 Avg Velocity</div><div class="value">{avg_vel}<span style="font-size:1rem;font-weight:600">/100</span></div><div class="sub">normalised score</div></div>'
    f'<div class="metric-card green"><div class="label">👁️ Total Views</div><div class="value">{format_count(total_views)}</div><div class="sub">combined reach</div></div>'
    f'<div class="metric-card"><div class="label">❤️ Avg Engagement</div><div class="value">{avg_eng}<span style="font-size:1rem;font-weight:600">%</span></div><div class="sub">top niche: {top_niche}</div></div>'
    f'</div>',
    unsafe_allow_html=True,
)


# ══════════════════════════════════════════════════════════════════════════════
#  VELOCITY CHART + CSV EXPORT
# ══════════════════════════════════════════════════════════════════════════════

col_chart, col_export = st.columns([5, 1])

with col_chart:
    with st.expander("📈 Velocity Chart — top 20", expanded=False):
        df_chart = pd.DataFrame([{
            "Title": (t["title"][:40] + "…") if len(t["title"]) > 40 else t["title"],
            "Velocity Score": t["velocity_score"],
            "Niche": t["niche"],
        } for t in filtered[:20]])
        fig = px.bar(
            df_chart, x="Velocity Score", y="Title", color="Niche",
            orientation="h", text="Velocity Score",
            color_discrete_sequence=["#6c63ff","#e040fb","#ff5252","#ff9800","#00bcd4","#4caf50","#f06292","#80cbc4"],
        )
        fig.update_layout(
            paper_bgcolor="#08080f", plot_bgcolor="#13131f",
            font_color="#d1d5db", height=440,
            margin=dict(l=0, r=24, t=10, b=10),
            yaxis=dict(tickfont=dict(size=10)),
            legend=dict(bgcolor="#08080f", bordercolor="#2a2a3e"),
        )
        fig.update_traces(textposition="outside", textfont_color="#f0f0ff")
        st.plotly_chart(fig, use_container_width=True)

with col_export:
    st.markdown("<div style='height:12px'></div>", unsafe_allow_html=True)
    if filtered:
        csv_df = pd.DataFrame([{
            "Title":          t["title"],
            "URL":            t["url"],
            "Niche":          t["niche"],
            "Format":         t["content_format"],
            "Views":          t["views"],
            "Likes":          t["likes"],
            "Comments":       t["comments"],
            "Engagement %":   t["engagement"],
            "Velocity Score": t["velocity_score"],
            "Age (hours)":    t["age_hours"],
            "Hot Label":      t["hot_label"],
        } for t in filtered])
        st.download_button(
            label="⬇️ Export CSV",
            data=csv_df.to_csv(index=False).encode("utf-8"),
            file_name=f"trendradar_{selected_country['code']}_{datetime.now().strftime('%Y%m%d_%H%M')}.csv",
            mime="text/csv",
            use_container_width=True,
        )


st.markdown(f"### Found **{len(filtered)}** Shorts in the official trending chart")
st.markdown('<div class="section-divider"></div>', unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════════════════════
#  TREND CARDS
# ══════════════════════════════════════════════════════════════════════════════

for trend in filtered:
    vel     = trend["velocity_score"]
    bar_w   = int(vel)
    age_str = trend["age_str"]

    col_img, col_main = st.columns([1, 5])

    with col_img:
        if trend["thumb"]:
            st.image(trend["thumb"], use_container_width=True)

    with col_main:
        safe_title = html.escape(trend["title"]).replace("\n", " ").replace("\r", "").strip()
        safe_niche = html.escape(trend["niche"]).replace("\n", " ").replace("\r", "").strip()
        suspect_badge = (
            '<span class="badge" style="background:#2d1a0d;color:#fb923c;border:1px solid #9a3412;">⚠️ Suspect Engagement</span>'
            if trend.get("suspect_engagement") else ""
        )
        card_html = (
            f'<div class="trend-card">'
            # ── top row: badges + age ──
            f'<div style="display:flex;justify-content:space-between;align-items:flex-start;flex-wrap:wrap;gap:8px;">'
            f'<div>'
            f'<span class="badge {trend["badge"]}">{trend["hot_label"]}</span>'
            f'<span class="badge" style="background:#1a1a35;color:#a78bfa;border:1px solid #3a3a6e;">{safe_niche}</span>'
            f'<span class="badge" style="background:#0d2d1a;color:#4ade80;border:1px solid #166534;">{trend["content_format"]}</span>'
            f'{suspect_badge}'
            f'</div>'
            f'<div style="color:#4b5563;font-size:.78rem;background:#0a0a14;padding:3px 10px;border-radius:999px;border:1px solid #1e1e30;">🕒 {age_str}</div>'
            f'</div>'
            # ── title ──
            f'<h3 style="color:#f0f0ff;margin:16px 0 4px;font-size:1.12rem;font-weight:800;line-height:1.4;letter-spacing:-.01em;">'
            f'<a href="{trend["url"]}" target="_blank" style="color:#f0f0ff!important;">{safe_title}</a>'
            f'</h3>'
            # ── velocity bar ──
            f'<div style="color:#6b7280;font-size:.7rem;margin:12px 0 5px;letter-spacing:.06em;text-transform:uppercase;">🚀 Velocity Score</div>'
            f'<div style="display:flex;align-items:center;gap:14px;margin-bottom:2px;">'
            f'<div style="flex:1;background:#1e1e30;border-radius:999px;height:7px;">'
            f'<div style="width:{bar_w}%;height:7px;border-radius:999px;background:linear-gradient(90deg,#6c63ff,#e040fb);box-shadow:0 0 10px rgba(108,99,255,.6);"></div>'
            f'</div>'
            f'<div style="font-size:1.55rem;font-weight:900;color:#a78bfa;min-width:60px;text-align:right;line-height:1;">'
            f'{vel}<span style="font-size:.68rem;color:#4b5563;font-weight:400;">/100</span>'
            f'</div>'
            f'</div>'
            # ── stats grid ──
            f'<div class="stats-grid">'
            f'<div class="stat-item"><span class="stat-icon">👁️</span><div class="stat-value">{format_count(trend["views"])}</div><div class="stat-label">Views</div></div>'
            f'<div class="stat-item"><span class="stat-icon">❤️</span><div class="stat-value blue">{format_count(trend["likes"])}</div><div class="stat-label">Likes</div></div>'
            f'<div class="stat-item"><span class="stat-icon">💬</span><div class="stat-value purple">{format_count(trend["comments"])}</div><div class="stat-label">Comments</div></div>'
            f'<div class="stat-item"><span class="stat-icon">📊</span><div class="stat-value orange">{trend["engagement"]}%</div><div class="stat-label">Engagement</div></div>'
            f'</div>'
            f'</div>'
        )
        st.markdown(card_html, unsafe_allow_html=True)

    with st.expander(f"🎬 CapCut Recipe — {trend['title'][:40]}…"):
        s = trend["sound"]
        tab1, tab2, tab3, tab4 = st.tabs(["🎵 Sound & Pace", "📝 Hook Texts", "🎬 CapCut Steps", "🎨 Midjourney"])

        with tab1:
            st.markdown(
                f'<div class="recipe-block">'
                f'<h4>🎵 Recommended Sound</h4>'
                f'<div class="sound-pill">🎵 {s["name"]} — {s["artist"]}</div>'
                f'<p style="color:#9ca3af;font-size:.85rem;margin:12px 0 4px;">'
                f'BPM: <strong style="color:#f0f0ff">{s["bpm"]}</strong> &nbsp;|&nbsp; Vibe: <strong style="color:#f0f0ff">{s["vibe"]}</strong>'
                f'</p><a href="{s["link"]}" target="_blank">🔍 Find on YouTube →</a></div>'
                f'<div class="recipe-block" style="margin-top:12px;border-color:#6c63ff;">'
                f'<h4 style="color:#a78bfa;">✂️ Editing Pace</h4>'
                f'<p style="color:#f0f0ff;font-size:1rem;margin:0;"><strong>{trend["pace_label"]}</strong></p>'
                f'<p style="color:#9ca3af;font-size:.85rem;margin:6px 0 0;">'
                f'~{trend["cuts_per_min"]} cuts/min &nbsp;|&nbsp; Transition: <strong style="color:#f0f0ff">{trend["transition"]}</strong>'
                f'</p></div>',
                unsafe_allow_html=True,
            )

        with tab2:
            st.markdown('<div class="recipe-block"><h4>📝 Hook Options — paste into CapCut text layer</h4>', unsafe_allow_html=True)
            for hook in trend["hooks"]:
                st.markdown(f'<div class="hook-box">"{html.escape(hook)}"</div>', unsafe_allow_html=True)
            st.markdown("</div>", unsafe_allow_html=True)
            st.info("💡 Use the hook as your FIRST text on screen (0:00–0:02). Big bold white font on dark background.")

        with tab3:
            st.markdown('<div class="recipe-block"><h4>🎬 CapCut Step-by-Step Script</h4>', unsafe_allow_html=True)
            for i, (tc, instr) in enumerate(trend["capcut_steps"], 1):
                st.markdown(
                    f'<div class="capcut-step">'
                    f'<div class="step-num">{i}</div>'
                    f'<div class="step-text"><strong>{tc}</strong><br>{instr}</div>'
                    f'</div>',
                    unsafe_allow_html=True,
                )
            st.markdown("</div>", unsafe_allow_html=True)
            st.success("✅ Total: 30 seconds | Optimized for YouTube Shorts algorithm")

        with tab4:
            st.markdown(
                '<div class="recipe-block" style="border-color:#a78bfa;">'
                '<h4 style="color:#a78bfa;">🎨 Midjourney Background Prompt</h4>'
                '<p style="color:#9ca3af;font-size:.85rem;margin:0 0 10px;">Paste into Midjourney to generate a 9:16 cinematic background.</p>'
                '</div>',
                unsafe_allow_html=True,
            )
            st.code(trend["mj_prompt"], language=None)
            st.caption("Copy icon (top-right) → paste into Midjourney Discord")

        st.markdown("---")
        st.markdown("##### 📋 Full Recipe — copy everything at once")
        full_recipe = (
            f"TREND: {trend['title']}\n"
            f"URL: {trend['url']}\n"
            f"Niche: {trend['niche']} | Velocity: {trend['velocity_score']}/100\n\n"
            f"━━━ HOOK TEXT (0:00–0:02) ━━━\n{trend['hooks'][0]}\n\n"
            f"━━━ SOUND ━━━\n{s['name']} — {s['artist']}\nBPM: {s['bpm']} | Vibe: {s['vibe']}\nFind it: {s['link']}\n\n"
            f"━━━ EDITING PACE ━━━\n{trend['pace_label']} (~{trend['cuts_per_min']} cuts/min)\nTransition: {trend['transition']}\n\n"
            f"━━━ CAPCUT SCRIPT ━━━\n"
            + "\n".join(f"{tc}  {instr}" for tc, instr in trend["capcut_steps"])
            + f"\n\n━━━ MIDJOURNEY PROMPT ━━━\n{trend['mj_prompt']}\n"
        )
        st.code(full_recipe, language=None)

    st.markdown('<div class="section-divider"></div>', unsafe_allow_html=True)


# ── Footer ────────────────────────────────────────────────────────────────────
st.markdown(
    '<div style="text-align:center;color:#374151;font-size:.78rem;margin-top:40px;padding:24px;">'
    'TrendRadar v0.7 · Official YouTube Trending · Built with Streamlit'
    '</div>',
    unsafe_allow_html=True,
)
    "BR": {"q": "#shorts brasil OR dança OR trend", "lang": "pt"},
    "DE": {"q": "#shorts deutsch OR trend",         "lang": "de"},
    "ES": {"q": "#shorts tendencia OR viral",       "lang": "es"},
    "FR": {"q": "#shorts tendance OR viral",        "lang": "fr"},
    "IN": {"q": "#shorts india OR trending",                              "lang": "hi"},
    "JP": {"q": "#shorts トレンド",                                        "lang": "ja"},
    "RU": {"q": "#shorts тренд | топ | шортс | тикток | рекомендации",   "lang": "ru"},
}
_LOCAL_QUERY_DEFAULT = {"q": "#shorts", "lang": "en"}

# Localized queries for Long Videos — no #shorts tag, keyword-based
LOCAL_QUERIES_LONG: dict[str, dict] = {
    "US": {"q": "trending viral",                        "lang": "en"},
    "GB": {"q": "trending viral uk",                     "lang": "en"},
    "CA": {"q": "trending viral",                        "lang": "en"},
    "AU": {"q": "trending viral",                        "lang": "en"},
    "BR": {"q": "brasil viral tendência",                "lang": "pt"},
    "DE": {"q": "viral deutschland trend",               "lang": "de"},
    "ES": {"q": "viral tendencia españa",                "lang": "es"},
    "FR": {"q": "viral tendance france",                 "lang": "fr"},
    "IN": {"q": "india trending viral",                  "lang": "hi"},
    "JP": {"q": "トレンド 人気 動画",                      "lang": "ja"},
    "RU": {"q": "тренд топ вирусное видео",              "lang": "ru"},
}
_LOCAL_QUERY_LONG_DEFAULT = {"q": "trending viral", "lang": "en"}

# Per-market anti-bot engagement threshold (EN strict, CIS moderate, others lenient)
_BOT_THRESHOLDS: dict[str, float] = {
    "US": 2.0, "GB": 2.0, "CA": 2.0, "AU": 2.0,  # English-speaking — strict
    "RU": 1.5,                                      # CIS — moderate
}

# Long-video thresholds are lower — viewers watch but like less often
_BOT_THRESHOLDS_LONG: dict[str, float] = {
    "US": 0.8, "GB": 0.8, "CA": 0.8, "AU": 0.8,
    "RU": 0.6,
}
_BOT_THRESHOLD_LONG_DEFAULT = 0.5

_LATIN_WORD_RE  = re.compile(r'[a-zA-Z]{3,}')
_HAS_LATIN_RE   = re.compile(r'[a-zA-Z]')
_CLEAN_TITLE_RE = re.compile(r'[#\[\]@|]')

_DEVANAGARI_RE    = re.compile(r'[ऀ-ॿঀ-৿઀-૿஀-௿ఀ-೿ഀ-ൿ]')
_CJK_KANA_RE      = re.compile(r'[぀-ゟ゠-ヿ一-鿿㐀-䶿]')
_CYRILLIC_RE      = re.compile(r'[Ѐ-ӿԀ-ԯ]')
_HINGLISH_RE      = re.compile(
    r'\b(?:ke|ko|ki|hai|mein|ne|aur|yeh|woh|bhai|desi|kya|hua|banayi)\b',
    re.IGNORECASE,
)

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
#  CLIENT KEY DATABASE
# ══════════════════════════════════════════════════════════════════════════════

CLIENTS_FILE = Path(__file__).parent / "clients.json"

def _load_clients() -> list[dict]:
    if not CLIENTS_FILE.exists():
        CLIENTS_FILE.write_text("[]", encoding="utf-8")
    try:
        return json.loads(CLIENTS_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []

def _save_clients(clients: list[dict]) -> None:
    CLIENTS_FILE.write_text(json.dumps(clients, indent=2, ensure_ascii=False), encoding="utf-8")

def add_client_key(label: str = "") -> str:
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
  @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800;900&display=swap');

  html, body, [class*="css"] {
    font-family: 'Inter', sans-serif;
    background: #08080f;
  }

  /* ── Hero ── */
  .hero {
    background: linear-gradient(135deg, #6c63ff 0%, #e040fb 45%, #ff5252 100%);
    border-radius: 24px; padding: 40px 44px; margin-bottom: 32px;
    box-shadow: 0 12px 48px rgba(108,99,255,.45);
    position: relative; overflow: hidden;
  }
  .hero::before {
    content: ""; position: absolute; inset: 0;
    background: radial-gradient(ellipse at 70% 50%, rgba(255,255,255,.08) 0%, transparent 70%);
  }
  .hero h1 { color:#fff; font-size:2.6rem; font-weight:900; margin:0; letter-spacing:-.03em; }
  .hero p  { color:rgba(255,255,255,.88); font-size:1.05rem; margin:10px 0 0; }
  .hero-meta { color:rgba(255,255,255,.6); font-size:.8rem; margin:14px 0 0; display:flex; gap:16px; flex-wrap:wrap; }
  .hero-meta span { background:rgba(0,0,0,.2); padding:3px 10px; border-radius:999px; }

  /* ── Metric cards ── */
  .metric-row { display:grid; grid-template-columns:repeat(4,1fr); gap:16px; margin-bottom:28px; }
  .metric-card {
    background: #13131f; border: 1px solid #2a2a3e;
    border-radius: 18px; padding: 22px 24px;
    transition: border-color .2s, transform .15s;
  }
  .metric-card:hover { border-color: #6c63ff; transform: translateY(-2px); }
  .metric-card .label { color:#6b7280; font-size:.78rem; font-weight:600; letter-spacing:.06em; text-transform:uppercase; margin-bottom:6px; }
  .metric-card .value { color:#f0f0ff; font-size:1.9rem; font-weight:800; line-height:1; }
  .metric-card .sub   { color:#6b7280; font-size:.78rem; margin-top:4px; }
  .metric-card.fire   { border-color:#ff5252; box-shadow:0 0 18px rgba(255,82,82,.12); }
  .metric-card.fire .value { color:#ff5252; }
  .metric-card.purple .value { color:#a78bfa; }
  .metric-card.green  .value { color:#4ade80; }

  /* ── Trend card ── */
  .trend-card {
    background: linear-gradient(145deg, #13131f 0%, #0f0f1a 100%);
    border: 1px solid #2a2a3e; border-radius: 22px;
    padding: 26px 30px; margin-bottom: 4px;
    transition: border-color .2s, box-shadow .2s;
  }
  .trend-card:hover {
    border-color: #6c63ff;
    box-shadow: 0 4px 24px rgba(108,99,255,.15);
  }

  /* ── Badges ── */
  .badge {
    display: inline-block; padding: 4px 12px; border-radius: 999px;
    font-size: .72rem; font-weight: 700; margin-right: 6px; letter-spacing:.02em;
  }
  .badge-fire   { background: linear-gradient(90deg,#ff5252,#ff9800); color:#fff; }
  .badge-rising { background: linear-gradient(90deg,#ff9800,#ffd600); color:#000; }
  .badge-new    { background: linear-gradient(90deg,#00bcd4,#00e5ff); color:#000; }

  /* ── Recipe blocks ── */
  .recipe-block {
    background: #0d1117; border-left: 4px solid #e040fb;
    border-radius: 0 14px 14px 0; padding: 18px 22px; margin-top: 14px;
  }
  .recipe-block h4 {
    color: #e040fb; font-size: .82rem; margin: 0 0 12px;
    letter-spacing: .1em; text-transform: uppercase;
  }
  .hook-box {
    background: #161625; border: 1px dashed #6c63ff; border-radius: 12px;
    padding: 14px 18px; font-size: .95rem; color: #f0f0ff;
    font-style: italic; margin: 10px 0; line-height: 1.5;
  }
  .sound-pill {
    display: inline-flex; align-items: center; gap: 8px;
    background: #1e1e30; border: 1px solid #6c63ff; border-radius: 999px;
    padding: 6px 16px; font-size: .85rem; color: #a78bfa;
  }
  .capcut-step { display: flex; align-items: flex-start; gap: 14px; margin: 10px 0; }
  .step-num {
    min-width: 28px; height: 28px;
    background: linear-gradient(135deg, #6c63ff, #e040fb);
    border-radius: 50%; display: flex; align-items: center;
    justify-content: center; font-size: .72rem; font-weight: 800; color: #fff;
    flex-shrink: 0;
  }
  .step-text { color: #d1d5db; font-size: .88rem; line-height: 1.55; }
  .step-text strong { color: #f0f0ff; }

  /* ── Auth ── */
  .auth-wrap {
    max-width: 420px; margin: 80px auto; padding: 44px;
    background: #13131f; border: 1px solid #2a2a3e; border-radius: 24px;
    box-shadow: 0 12px 48px rgba(0,0,0,.6);
  }

  /* ── Divider ── */
  .section-divider {
    height: 1px; background: linear-gradient(90deg,transparent,#2a2a3e,transparent);
    margin: 28px 0;
  }

  /* ── Links ── */
  a { color: #a78bfa !important; text-decoration: none; }
  a:hover { text-decoration: underline; }

  /* ── Sidebar ── */
  section[data-testid="stSidebar"] {
    background: linear-gradient(180deg,#0a0a18 0%,#0d0d1f 100%) !important;
    border-right: 1px solid #1e1e30;
  }
  section[data-testid="stSidebar"] * { color: #d1d5db; }
  section[data-testid="stSidebar"] .stSelectbox label,
  section[data-testid="stSidebar"] .stSlider label { color: #9ca3af !important; font-size:.85rem; }

  /* ── Hide Streamlit chrome ── */
  #MainMenu { visibility: hidden; }
  footer    { visibility: hidden; }
  header    { visibility: hidden; }

  /* ── Stats grid inside trend card ── */
  .stats-grid {
    display: grid; grid-template-columns: repeat(4, 1fr);
    gap: 12px; margin: 18px 0 0;
  }
  .stat-item {
    background: #0a0a14; border: 1px solid #1e1e30;
    border-radius: 12px; padding: 12px 14px;
    text-align: center; transition: border-color .2s;
  }
  .stat-item:hover { border-color: #6c63ff; }
  .stat-icon  { font-size: 1rem; margin-bottom: 4px; display: block; }
  .stat-value {
    font-size: 1.2rem; font-weight: 800; color: #10b981;
    line-height: 1.1; margin-bottom: 2px;
  }
  .stat-value.purple { color: #a78bfa; }
  .stat-value.orange { color: #fb923c; }
  .stat-value.blue   { color: #38bdf8; }
  .stat-label { font-size: .68rem; color: #4b5563; font-weight: 600; letter-spacing:.04em; text-transform: uppercase; }

  /* ── Streamlit overrides ── */
  div[data-testid="stExpander"] { background: #0f0f1a; border: 1px solid #2a2a3e; border-radius: 14px; }
  button[kind="primary"] { background: linear-gradient(90deg,#6c63ff,#e040fb) !important; border:none !important; }
</style>
""", unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════════════════════
#  AUTH GATE
# ══════════════════════════════════════════════════════════════════════════════

if "role" not in st.session_state:
    st.session_state["role"] = None

if st.session_state["role"] is None:
    st.markdown("""
<div class="hero" style="max-width:480px;margin:60px auto 0;">
  <h1 style="font-size:2rem;">🔐 TrendRadar</h1>
  <p>Enter your access key to unlock the trend intelligence platform.</p>
</div>
""", unsafe_allow_html=True)

    _, col, _ = st.columns([1, 2, 1])
    with col:
        st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)
        key_input = st.text_input(
            "Access Key",
            type="password",
            placeholder="TR-XXXX-XXXX",
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
                st.error("❌ Invalid access key. Contact support to get one.")
        st.caption("Access keys are issued per client. Do not share yours.")

    st.stop()


# ══════════════════════════════════════════════════════════════════════════════
#  YOUTUBE API HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def _get_api_keys() -> list[str]:
    # Supports YOUTUBE_API_KEYS = "key1,key2,key3"  OR legacy YOUTUBE_API_KEY = "key1"
    raw = st.secrets.get("YOUTUBE_API_KEYS") or st.secrets.get("YOUTUBE_API_KEY", "")
    if isinstance(raw, (list, tuple)):
        return [str(k).strip() for k in raw if str(k).strip()]
    return [k.strip() for k in str(raw).split(",") if k.strip()]

def get_youtube(key_index: int = 0):
    keys = _get_api_keys()
    if not keys:
        raise RuntimeError("No YouTube API key configured in secrets.")
    api_key = keys[min(key_index, len(keys) - 1)]
    return build("youtube", "v3", developerKey=api_key, cache_discovery=False)

def is_short(item: dict) -> bool:
    """True if video is ≤ 60 s OR explicitly tagged #shorts."""
    raw = item["contentDetails"].get("duration", "PT0S")
    try:
        duration_sec = int(isodate.parse_duration(raw).total_seconds())
    except Exception:
        duration_sec = 0
    title = item["snippet"].get("title", "").lower()
    tags  = " ".join(item["snippet"].get("tags", [])).lower()
    return duration_sec <= 60 or "#shorts" in title or "#short" in title or "#shorts" in tags

def is_long_video(item: dict) -> bool:
    """True if video is strictly > 5 minutes (300 s)."""
    raw = item["contentDetails"].get("duration", "PT0S")
    try:
        duration_sec = int(isodate.parse_duration(raw).total_seconds())
    except Exception:
        duration_sec = 0
    return duration_sec > 300

def is_target_language(title: str, desc: str, snippet: dict, country_cfg: dict) -> bool:
    target_langs = country_cfg["langs"]
    script_mode  = country_cfg["script"]
    text = title + " " + desc[:100]

    if script_mode == "latin":
        if _CYRILLIC_RE.search(title) or _DEVANAGARI_RE.search(title):
            return False
        if _HINGLISH_RE.search(title):
            return False

    for field in ("defaultLanguage", "defaultAudioLanguage"):
        lang = (snippet.get(field) or "").split("-")[0].lower()
        if lang:
            return lang in target_langs

    if script_mode == "cjk":
        return bool(_CJK_KANA_RE.search(title))

    if script_mode == "india":
        if _INDIA_HOSTILE_RE.search(text):
            return False
        return bool(_DEVANAGARI_RE.search(title)) or bool(_LATIN_WORD_RE.search(title))

    if script_mode == "cyrillic":
        if _CJK_KANA_RE.search(title) or _DEVANAGARI_RE.search(title):
            return False
        return bool(_CYRILLIC_RE.search(title))

    if _HOSTILE_RE.search(text):
        return False
    return bool(_LATIN_WORD_RE.search(title))

def hours_since(published_at: str) -> float:
    pub = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
    return max((datetime.now(timezone.utc) - pub).total_seconds() / 3600, 0.1)

def velocity_score(views: int, age_hours: float) -> float:
    return round(views / age_hours, 0)

def categorise(title: str, description: str) -> str:
    text = (title + " " + description).lower()
    _AI_KEYWORDS = [
        "ai ", "chatgpt", "midjourney", "elevenlabs", "gpt", "нейросеть",
        "ии ", "heygen", "artificial intelligence", "openai",
    ]
    if any(k in text for k in _AI_KEYWORDS):
        return "🤖 AI & Tech"
    if not _HAS_LATIN_RE.search(title):
        return "General"
    rules = {
        "Finance / Money":   ["money","income","salary","invest","crypto","earn","revenue","profit","rich","wealth"],
        "Fitness / Health":  ["gym","workout","fitness","diet","calories","muscle","weight","run","exercise","sleep"],
        "Productivity / AI": ["productivity","notion","hack","workflow","automation","claude","tool"],
        "Tech / Gadgets":    ["iphone","android","gadget","amazon","tech","review","unboxing","laptop","phone","device"],
        "Creator Tools":     ["capcut","premiere","edit","youtube","tiktok","instagram","content","creator","viral","views"],
        "Food / Recipe":     ["recipe","food","cook","meal","eat","drink","kitchen","chef","taste","bake"],
        "Fashion / Beauty":  ["outfit","fashion","makeup","beauty","skincare","style","clothes","aesthetic","look","vibe"],
        "Motivation":        ["motivat","mindset","success","hustle","grind","discipline","goals","life","change","growth"],
    }
    for niche, keywords in rules.items():
        if any(k in text for k in keywords):
            return niche
    return "General"

def bpm_for_niche(niche: str) -> int:
    return {
        "🤖 AI & Tech": 135, "Finance / Money": 130, "Fitness / Health": 145,
        "Productivity / AI": 128, "Tech / Gadgets": 115, "Creator Tools": 155,
        "Food / Recipe": 100, "Fashion / Beauty": 120, "Motivation": 140,
    }.get(niche, 120)

def pace_for_niche(niche: str) -> tuple[str, int, str]:
    fast  = ("Very fast cuts", 28, "Hard cut + beat sync")
    med   = ("Medium cuts",    14, "Smooth fade")
    slow  = ("Slow & cinematic", 7, "Cross-dissolve")
    nfast = ("Fast cuts",      22, "Zoom + glitch")
    return {
        "🤖 AI & Tech": nfast, "Finance / Money": nfast, "Fitness / Health": fast,
        "Productivity / AI": nfast, "Tech / Gadgets": med, "Creator Tools": fast,
        "Food / Recipe": slow, "Fashion / Beauty": med, "Motivation": fast,
    }.get(niche, med)

def sound_for_niche(niche: str) -> dict:
    library = {
        "🤖 AI & Tech":      {"name":"GODS","artist":"NewJeans","vibe":"Cinematic / Futuristic","search":"NewJeans GODS AI tech shorts trending"},
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
    s = dict(library.get(niche, library["General"]))
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
        "🤖 AI & Tech": [
            ("0:00–0:02", "Hook text on black: 'This AI can…' — glitch/digital reveal effect"),
            ("0:02–0:06", "Screen-record of AI tool output — zoom + highlight the result"),
            ("0:06–0:14", "Fast demo: before (manual) → after (AI) — hard cut on beat"),
            ("0:14–0:22", "Key capability shown with text callouts + zoomed UI"),
            ("0:22–0:27", "Real-world use case or output montage — 3 rapid cuts"),
            ("0:27–0:30", "CTA: 'Free link in bio 🔗 + follow for more AI tools'"),
        ],
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
        "🤖 AI & Tech":      "neural network visualization, deep blue and electric purple data streams, futuristic AI interface, cyberpunk neon glow, 9:16 vertical",
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
    text = title + " " + desc[:300]
    if _STREAM_RE.search(text):
        return "🎮 Stream / Gaming"
    if _PODCAST_RE.search(text):
        return "🎙️ Podcast / Interview"
    return "🎬 Original / Creator"


# ══════════════════════════════════════════════════════════════════════════════
#  FETCH DATA  — search.list · fmt="shorts" or fmt="long"
#  Shorts:  videoDuration="short" · 5 pages · ~510 quota units/refresh
#  Long:    no duration filter · 5 pages · filter locally > 300 s
# ══════════════════════════════════════════════════════════════════════════════

@st.cache_data(ttl=7200, show_spinner=False)
def fetch_trending_videos(region_code: str, country_name: str, key_index: int = 0, fmt: str = "shorts") -> list[dict]:
    yt = get_youtube(key_index)
    country_cfg = COUNTRIES[country_name]

    # ── Step 1: search.list — last 7 days ────────────────────────────────────
    published_after = (
        datetime.now(timezone.utc).replace(microsecond=0) - timedelta(days=7)
    ).isoformat()

    seen_ids: set[str] = set()
    raw_ids:  list[str] = []
    page_token: str | None = None

    if fmt == "shorts":
        local = LOCAL_QUERIES.get(country_cfg["code"], _LOCAL_QUERY_DEFAULT)
    else:
        local = LOCAL_QUERIES_LONG.get(country_cfg["code"], _LOCAL_QUERY_LONG_DEFAULT)

    for _ in range(5):          # hard cap: 5 pages = up to 250 raw candidates
        kwargs: dict = dict(
            part="snippet",
            q=local["q"],
            type="video",
            regionCode=region_code,
            order="viewCount",
            maxResults=50,
            publishedAfter=published_after,
            relevanceLanguage=local["lang"],
        )
        if fmt == "shorts":
            kwargs["videoDuration"] = "short"
        if page_token:
            kwargs["pageToken"] = page_token

        resp = yt.search().list(**kwargs).execute()

        for item in resp.get("items", []):
            if item["id"].get("kind") == "youtube#video":
                vid = item["id"]["videoId"]
                if vid not in seen_ids:
                    seen_ids.add(vid)
                    raw_ids.append(vid)

        page_token = resp.get("nextPageToken")
        if not page_token:
            break

    if not raw_ids:
        return []

    # ── Step 2: fetch full details in batches of 50 ───────────────────────────
    all_items: list[dict] = []
    for i in range(0, len(raw_ids), 50):
        batch = raw_ids[i : i + 50]
        stats_resp = yt.videos().list(
            part="snippet,statistics,contentDetails",
            id=",".join(batch),
        ).execute()
        all_items.extend(stats_resp.get("items", []))

    if not all_items:
        return []

    # ── Step 3: channel → country lookup for EN markets (50 IDs/request) ────
    channel_countries: dict[str, str | None] = {}
    apply_channel_filter = country_cfg["code"] in EN_MARKET_CODES
    if apply_channel_filter:
        channel_ids = list({item["snippet"]["channelId"] for item in all_items})
        for i in range(0, len(channel_ids), 50):
            batch = channel_ids[i : i + 50]
            ch_resp = yt.channels().list(
                part="snippet",
                id=",".join(batch),
                maxResults=50,
            ).execute()
            for ch in ch_resp.get("items", []):
                channel_countries[ch["id"]] = ch["snippet"].get("country")

    # ── Step 4: filter, score, build result list ──────────────────────────────
    results: list[dict] = []
    for item in all_items:
        if fmt == "shorts":
            if not is_short(item):
                continue
        else:
            if not is_long_video(item):
                continue

        snippet = item["snippet"]
        title   = snippet.get("title", "Untitled")
        desc    = snippet.get("description", "")

        if apply_channel_filter:
            ch_country = channel_countries.get(snippet.get("channelId", ""))
            if ch_country in BANNED_COUNTRIES:
                continue

        if not is_target_language(title, desc, snippet, country_cfg):
            continue

        stats  = item.get("statistics", {})
        pub_at = snippet.get("publishedAt", "")
        vid_id = item["id"]

        views    = int(stats.get("viewCount", 0))
        likes    = int(stats.get("likeCount", 0))
        comments = int(stats.get("commentCount", 0))
        eng_rate = round((likes + comments) / max(views, 1) * 100, 2)

        if fmt == "shorts":
            bot_threshold    = _BOT_THRESHOLDS.get(country_cfg["code"], 1.0)
            suspect_threshold = 3.5
        else:
            bot_threshold    = _BOT_THRESHOLDS_LONG.get(country_cfg["code"], _BOT_THRESHOLD_LONG_DEFAULT)
            suspect_threshold = 1.0

        if views > 10_000 and eng_rate < bot_threshold:
            continue

        suspect_engagement = views > 1_000 and eng_rate < suspect_threshold

        age_h          = hours_since(pub_at)
        vel            = velocity_score(views, age_h)
        niche          = categorise(title, desc)
        content_format = tag_content_format(title, desc)
        pace_lbl, cpm, transition = pace_for_niche(niche)
        age_str        = f"{age_h:.0f}h ago" if age_h < 48 else f"{age_h/24:.0f}d ago"

        if fmt == "shorts":
            video_url = f"https://youtube.com/shorts/{vid_id}"
        else:
            video_url = f"https://youtube.com/watch?v={vid_id}"

        results.append({
            "id":               vid_id,
            "title":            title,
            "niche":            niche,
            "content_format":   content_format,
            "views":            views,
            "likes":            likes,
            "comments":         comments,
            "age_hours":        round(age_h, 1),
            "age_str":          age_str,
            "velocity":         vel,
            "velocity_score":   0.0,
            "engagement":       eng_rate,
            "suspect_engagement": suspect_engagement,
            "sound":            sound_for_niche(niche),
            "pace_label":       pace_lbl,
            "cuts_per_min":     cpm,
            "transition":       transition,
            "hooks":            generate_hooks(title, niche),
            "capcut_steps":     generate_capcut_steps(niche, title),
            "mj_prompt":        generate_mj_prompt(title, niche),
            "thumb":            snippet.get("thumbnails", {}).get("high", {}).get("url", ""),
            "url":              video_url,
        })

    # Top-50 cap: if more than 50 survived filters, keep the most-viewed ones
    if len(results) > 50:
        results.sort(key=lambda r: r["views"], reverse=True)
        results = results[:50]

    if results:
        max_vel = max(r["velocity"] for r in results)
        for r in results:
            r["velocity_score"] = round(r["velocity"] / max_vel * 100, 1)
            r["hot_label"], r["badge"] = badge_for_velocity(r["velocity"], max_vel)

    return results


# ── Key-rotation wrapper (not cached — manages session state) ────────────────
def load_trending_videos(region_code: str, country_name: str, fmt: str = "shorts") -> list[dict] | None:
    if "current_key_index" not in st.session_state:
        st.session_state.current_key_index = 0

    keys = _get_api_keys()
    if not keys:
        st.error("No YouTube API keys configured. Add YOUTUBE_API_KEYS to Streamlit secrets.")
        return None

    while st.session_state.current_key_index < len(keys):
        try:
            return fetch_trending_videos(region_code, country_name, st.session_state.current_key_index, fmt)
        except HttpError as e:
            if e.resp.status == 403 and "quotaExceeded" in str(e):
                st.session_state.current_key_index += 1
                print(f"[TrendRadar] Key exhausted. Switching to key index {st.session_state.current_key_index}")
            else:
                raise

    return None  # all keys exhausted


# ══════════════════════════════════════════════════════════════════════════════
#  SIDEBAR
# ══════════════════════════════════════════════════════════════════════════════

with st.sidebar:
    role = st.session_state["role"]

    if role == "admin":
        st.markdown(
            '<div style="background:linear-gradient(90deg,#1a1a35,#1e1530);border:1px solid #6c63ff;'
            'border-radius:10px;padding:8px 14px;font-size:.8rem;color:#a78bfa;margin-bottom:12px;">'
            '🛡️ Logged in as <strong>Admin</strong></div>',
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            '<div style="background:#13131f;border:1px solid #2a2a3e;border-radius:10px;'
            'padding:8px 14px;font-size:.8rem;color:#6b7280;margin-bottom:12px;">'
            '👤 Logged in as <strong>Client</strong></div>',
            unsafe_allow_html=True,
        )

    st.markdown("### ⚙️ Controls")

    content_fmt = st.radio(
        "📺 Content Format",
        ["Shorts (≤ 60s)", "Long Videos (> 5m)"],
        horizontal=True,
    )
    fmt_key = "shorts" if content_fmt == "Shorts (≤ 60s)" else "long"

    country_name = st.selectbox(
        "🌍 Market",
        list(COUNTRIES.keys()),
        index=0,
        help="Filters YouTube trending chart by region",
    )
    selected_country = COUNTRIES[country_name]

    sort_by = st.selectbox(
        "📊 Sort by",
        ["🚀 Velocity Score", "👁️ Total Views", "❤️ Engagement Rate"],
    )
    min_vel = st.slider("Min Velocity Score", 0, 100, 0, 5)

    col_refresh, col_logout = st.columns([3, 2])
    with col_refresh:
        if st.button("🔄 Refresh", use_container_width=True, type="primary"):
            st.cache_data.clear()
            st.session_state.current_key_index = 0
            st.rerun()
    with col_logout:
        if st.button("🚪 Logout", use_container_width=True):
            st.session_state["role"] = None
            st.rerun()

    st.markdown("---")
    st.markdown("### 📖 How to use")
    st.markdown("""
1. Pick your **Market** above
2. Sort by **Velocity Score** — top = blowing up *right now*
3. Open **CapCut Recipe** for a ready-made script
4. Use one of the **Hook Texts** as your opening frame
5. Post within **24 h** of the trend peak
    """)
    st.markdown("---")
    st.caption("🔄 Cache: 1 h · ~8 API quota units/refresh\nv0.7 · TrendRadar · Global")

    if role == "admin":
        st.markdown("---")
        with st.expander("🛠️ Admin Dashboard", expanded=True):
            st.markdown("**Generate Client Key**")
            new_label = st.text_input(
                "Client label (optional)",
                placeholder="e.g. John Smith / Agency X",
                key="admin_label",
            )
            if st.button("➕ Generate New Key", use_container_width=True, type="primary"):
                st.session_state["last_generated_key"] = add_client_key(label=new_label)

            if "last_generated_key" in st.session_state:
                st.success("New key generated — copy it now:")
                st.code(st.session_state["last_generated_key"], language=None)

            st.markdown("---")
            if st.button("🗑️ Clear Global Cache", use_container_width=True):
                st.cache_data.clear()
                st.success("Cache cleared.")

            st.markdown("---")
            clients = _load_clients()
            st.markdown(f"**Active Keys** ({len(clients)})")
            if not clients:
                st.caption("No client keys yet.")
            else:
                for c in clients:
                    col_info, col_btn = st.columns([3, 1])
                    with col_info:
                        st.markdown(
                            f'<div style="font-size:.78rem;color:#a78bfa;font-family:monospace;">{c["key"]}</div>'
                            f'<div style="font-size:.7rem;color:#6b7280;">{c.get("label","")} · {c.get("created_at","")}</div>',
                            unsafe_allow_html=True,
                        )
                    with col_btn:
                        if st.button("✕", key=f"revoke_{c['key']}", help="Revoke"):
                            revoke_client_key(c["key"])
                            if st.session_state.get("last_generated_key") == c["key"]:
                                del st.session_state["last_generated_key"]
                            st.rerun()


# ══════════════════════════════════════════════════════════════════════════════
#  HEADER
# ══════════════════════════════════════════════════════════════════════════════

now_str = datetime.now(timezone.utc).strftime("%H:%M UTC")
st.markdown(
    f'<div class="hero">'
    f'<h1>🚀 TrendRadar</h1>'
    f'<p>Official YouTube trending chart · {country_name} · real engagement only · CapCut recipe included.</p>'
    f'<div class="hero-meta">'
    f'<span>📡 Live data</span>'
    f'<span>🕒 Refreshed at {now_str}</span>'
    f'<span>💡 ~306 quota units/refresh</span>'
    f'<span>🔒 2-hour cache</span>'
    f'</div></div>',
    unsafe_allow_html=True,
)


# ══════════════════════════════════════════════════════════════════════════════
#  LOAD DATA
# ══════════════════════════════════════════════════════════════════════════════

with st.spinner(f"Loading {content_fmt} for {country_name}…"):
    try:
        all_trends = load_trending_videos(selected_country["code"], country_name, fmt_key)
    except HttpError as e:
        st.error(f"YouTube API error: {e}")
        st.stop()

if all_trends is None:
    st.warning(
        "We are experiencing unusually high traffic. "
        "Fetching the latest cached trends. "
        "Please try refreshing in a few hours."
    )
    st.stop()

if not all_trends:
    st.warning("No Shorts found in the current trending chart. Try another market or refresh.")
    st.stop()

# ── Sidebar filters (need data first) ────────────────────────────────────────
all_niches  = sorted(set(t["niche"]          for t in all_trends))
all_formats = sorted(set(t["content_format"] for t in all_trends))
with st.sidebar:
    st.markdown("---")
    st.markdown("### 🔎 Filters")
    selected_niches  = st.multiselect("Niche",  all_niches,  default=all_niches)
    selected_formats = st.multiselect(
        "Content Format", all_formats, default=all_formats,
        help="Original creator content, stream clips, or podcast snippets",
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
#  METRICS
# ══════════════════════════════════════════════════════════════════════════════

total_views = sum(t["views"] for t in filtered)
avg_vel     = round(sum(t["velocity_score"] for t in filtered) / max(len(filtered), 1), 1)
fire_count  = sum(1 for t in filtered if t["badge"] == "badge-fire")
top_niche   = max(filtered, key=lambda t: t["velocity_score"])["niche"].split("/")[0].strip() if filtered else "—"
avg_eng     = round(sum(t["engagement"] for t in filtered) / max(len(filtered), 1), 1)

st.markdown(
    f'<div class="metric-row">'
    f'<div class="metric-card fire"><div class="label">🔥 Viral Now</div><div class="value">{fire_count}</div><div class="sub">badge-fire videos</div></div>'
    f'<div class="metric-card purple"><div class="label">🚀 Avg Velocity</div><div class="value">{avg_vel}<span style="font-size:1rem;font-weight:600">/100</span></div><div class="sub">normalised score</div></div>'
    f'<div class="metric-card green"><div class="label">👁️ Total Views</div><div class="value">{format_count(total_views)}</div><div class="sub">combined reach</div></div>'
    f'<div class="metric-card"><div class="label">❤️ Avg Engagement</div><div class="value">{avg_eng}<span style="font-size:1rem;font-weight:600">%</span></div><div class="sub">top niche: {top_niche}</div></div>'
    f'</div>',
    unsafe_allow_html=True,
)


# ══════════════════════════════════════════════════════════════════════════════
#  VELOCITY CHART + CSV EXPORT
# ══════════════════════════════════════════════════════════════════════════════

col_chart, col_export = st.columns([5, 1])

with col_chart:
    with st.expander("📈 Velocity Chart — top 20", expanded=False):
        df_chart = pd.DataFrame([{
            "Title": (t["title"][:40] + "…") if len(t["title"]) > 40 else t["title"],
            "Velocity Score": t["velocity_score"],
            "Niche": t["niche"],
        } for t in filtered[:20]])
        fig = px.bar(
            df_chart, x="Velocity Score", y="Title", color="Niche",
            orientation="h", text="Velocity Score",
            color_discrete_sequence=["#6c63ff","#e040fb","#ff5252","#ff9800","#00bcd4","#4caf50","#f06292","#80cbc4"],
        )
        fig.update_layout(
            paper_bgcolor="#08080f", plot_bgcolor="#13131f",
            font_color="#d1d5db", height=440,
            margin=dict(l=0, r=24, t=10, b=10),
            yaxis=dict(tickfont=dict(size=10)),
            legend=dict(bgcolor="#08080f", bordercolor="#2a2a3e"),
        )
        fig.update_traces(textposition="outside", textfont_color="#f0f0ff")
        st.plotly_chart(fig, use_container_width=True)

with col_export:
    st.markdown("<div style='height:12px'></div>", unsafe_allow_html=True)
    if filtered:
        csv_df = pd.DataFrame([{
            "Title":          t["title"],
            "URL":            t["url"],
            "Niche":          t["niche"],
            "Format":         t["content_format"],
            "Views":          t["views"],
            "Likes":          t["likes"],
            "Comments":       t["comments"],
            "Engagement %":   t["engagement"],
            "Velocity Score": t["velocity_score"],
            "Age (hours)":    t["age_hours"],
            "Hot Label":      t["hot_label"],
        } for t in filtered])
        st.download_button(
            label="⬇️ Export CSV",
            data=csv_df.to_csv(index=False).encode("utf-8"),
            file_name=f"trendradar_{selected_country['code']}_{datetime.now().strftime('%Y%m%d_%H%M')}.csv",
            mime="text/csv",
            use_container_width=True,
        )


st.markdown(f"### Found **{len(filtered)}** Shorts in the official trending chart")
st.markdown('<div class="section-divider"></div>', unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════════════════════
#  TREND CARDS
# ══════════════════════════════════════════════════════════════════════════════

for trend in filtered:
    vel     = trend["velocity_score"]
    bar_w   = int(vel)
    age_str = trend["age_str"]

    col_img, col_main = st.columns([1, 5])

    with col_img:
        if trend["thumb"]:
            st.image(trend["thumb"], use_container_width=True)

    with col_main:
        safe_title = html.escape(trend["title"]).replace("\n", " ").replace("\r", "").strip()
        safe_niche = html.escape(trend["niche"]).replace("\n", " ").replace("\r", "").strip()
        suspect_badge = (
            '<span class="badge" style="background:#2d1a0d;color:#fb923c;border:1px solid #9a3412;">⚠️ Suspect Engagement</span>'
            if trend.get("suspect_engagement") else ""
        )
        card_html = (
            f'<div class="trend-card">'
            # ── top row: badges + age ──
            f'<div style="display:flex;justify-content:space-between;align-items:flex-start;flex-wrap:wrap;gap:8px;">'
            f'<div>'
            f'<span class="badge {trend["badge"]}">{trend["hot_label"]}</span>'
            f'<span class="badge" style="background:#1a1a35;color:#a78bfa;border:1px solid #3a3a6e;">{safe_niche}</span>'
            f'<span class="badge" style="background:#0d2d1a;color:#4ade80;border:1px solid #166534;">{trend["content_format"]}</span>'
            f'{suspect_badge}'
            f'</div>'
            f'<div style="color:#4b5563;font-size:.78rem;background:#0a0a14;padding:3px 10px;border-radius:999px;border:1px solid #1e1e30;">🕒 {age_str}</div>'
            f'</div>'
            # ── title ──
            f'<h3 style="color:#f0f0ff;margin:16px 0 4px;font-size:1.12rem;font-weight:800;line-height:1.4;letter-spacing:-.01em;">'
            f'<a href="{trend["url"]}" target="_blank" style="color:#f0f0ff!important;">{safe_title}</a>'
            f'</h3>'
            # ── velocity bar ──
            f'<div style="color:#6b7280;font-size:.7rem;margin:12px 0 5px;letter-spacing:.06em;text-transform:uppercase;">🚀 Velocity Score</div>'
            f'<div style="display:flex;align-items:center;gap:14px;margin-bottom:2px;">'
            f'<div style="flex:1;background:#1e1e30;border-radius:999px;height:7px;">'
            f'<div style="width:{bar_w}%;height:7px;border-radius:999px;background:linear-gradient(90deg,#6c63ff,#e040fb);box-shadow:0 0 10px rgba(108,99,255,.6);"></div>'
            f'</div>'
            f'<div style="font-size:1.55rem;font-weight:900;color:#a78bfa;min-width:60px;text-align:right;line-height:1;">'
            f'{vel}<span style="font-size:.68rem;color:#4b5563;font-weight:400;">/100</span>'
            f'</div>'
            f'</div>'
            # ── stats grid ──
            f'<div class="stats-grid">'
            f'<div class="stat-item"><span class="stat-icon">👁️</span><div class="stat-value">{format_count(trend["views"])}</div><div class="stat-label">Views</div></div>'
            f'<div class="stat-item"><span class="stat-icon">❤️</span><div class="stat-value blue">{format_count(trend["likes"])}</div><div class="stat-label">Likes</div></div>'
            f'<div class="stat-item"><span class="stat-icon">💬</span><div class="stat-value purple">{format_count(trend["comments"])}</div><div class="stat-label">Comments</div></div>'
            f'<div class="stat-item"><span class="stat-icon">📊</span><div class="stat-value orange">{trend["engagement"]}%</div><div class="stat-label">Engagement</div></div>'
            f'</div>'
            f'</div>'
        )
        st.markdown(card_html, unsafe_allow_html=True)

    with st.expander(f"🎬 CapCut Recipe — {trend['title'][:40]}…"):
        s = trend["sound"]
        tab1, tab2, tab3, tab4 = st.tabs(["🎵 Sound & Pace", "📝 Hook Texts", "🎬 CapCut Steps", "🎨 Midjourney"])

        with tab1:
            st.markdown(
                f'<div class="recipe-block">'
                f'<h4>🎵 Recommended Sound</h4>'
                f'<div class="sound-pill">🎵 {s["name"]} — {s["artist"]}</div>'
                f'<p style="color:#9ca3af;font-size:.85rem;margin:12px 0 4px;">'
                f'BPM: <strong style="color:#f0f0ff">{s["bpm"]}</strong> &nbsp;|&nbsp; Vibe: <strong style="color:#f0f0ff">{s["vibe"]}</strong>'
                f'</p><a href="{s["link"]}" target="_blank">🔍 Find on YouTube →</a></div>'
                f'<div class="recipe-block" style="margin-top:12px;border-color:#6c63ff;">'
                f'<h4 style="color:#a78bfa;">✂️ Editing Pace</h4>'
                f'<p style="color:#f0f0ff;font-size:1rem;margin:0;"><strong>{trend["pace_label"]}</strong></p>'
                f'<p style="color:#9ca3af;font-size:.85rem;margin:6px 0 0;">'
                f'~{trend["cuts_per_min"]} cuts/min &nbsp;|&nbsp; Transition: <strong style="color:#f0f0ff">{trend["transition"]}</strong>'
                f'</p></div>',
                unsafe_allow_html=True,
            )

        with tab2:
            st.markdown('<div class="recipe-block"><h4>📝 Hook Options — paste into CapCut text layer</h4>', unsafe_allow_html=True)
            for hook in trend["hooks"]:
                st.markdown(f'<div class="hook-box">"{html.escape(hook)}"</div>', unsafe_allow_html=True)
            st.markdown("</div>", unsafe_allow_html=True)
            st.info("💡 Use the hook as your FIRST text on screen (0:00–0:02). Big bold white font on dark background.")

        with tab3:
            st.markdown('<div class="recipe-block"><h4>🎬 CapCut Step-by-Step Script</h4>', unsafe_allow_html=True)
            for i, (tc, instr) in enumerate(trend["capcut_steps"], 1):
                st.markdown(
                    f'<div class="capcut-step">'
                    f'<div class="step-num">{i}</div>'
                    f'<div class="step-text"><strong>{tc}</strong><br>{instr}</div>'
                    f'</div>',
                    unsafe_allow_html=True,
                )
            st.markdown("</div>", unsafe_allow_html=True)
            st.success("✅ Total: 30 seconds | Optimized for YouTube Shorts algorithm")

        with tab4:
            st.markdown(
                '<div class="recipe-block" style="border-color:#a78bfa;">'
                '<h4 style="color:#a78bfa;">🎨 Midjourney Background Prompt</h4>'
                '<p style="color:#9ca3af;font-size:.85rem;margin:0 0 10px;">Paste into Midjourney to generate a 9:16 cinematic background.</p>'
                '</div>',
                unsafe_allow_html=True,
            )
            st.code(trend["mj_prompt"], language=None)
            st.caption("Copy icon (top-right) → paste into Midjourney Discord")

        st.markdown("---")
        st.markdown("##### 📋 Full Recipe — copy everything at once")
        full_recipe = (
            f"TREND: {trend['title']}\n"
            f"URL: {trend['url']}\n"
            f"Niche: {trend['niche']} | Velocity: {trend['velocity_score']}/100\n\n"
            f"━━━ HOOK TEXT (0:00–0:02) ━━━\n{trend['hooks'][0]}\n\n"
            f"━━━ SOUND ━━━\n{s['name']} — {s['artist']}\nBPM: {s['bpm']} | Vibe: {s['vibe']}\nFind it: {s['link']}\n\n"
            f"━━━ EDITING PACE ━━━\n{trend['pace_label']} (~{trend['cuts_per_min']} cuts/min)\nTransition: {trend['transition']}\n\n"
            f"━━━ CAPCUT SCRIPT ━━━\n"
            + "\n".join(f"{tc}  {instr}" for tc, instr in trend["capcut_steps"])
            + f"\n\n━━━ MIDJOURNEY PROMPT ━━━\n{trend['mj_prompt']}\n"
        )
        st.code(full_recipe, language=None)

    st.markdown('<div class="section-divider"></div>', unsafe_allow_html=True)


# ── Footer ────────────────────────────────────────────────────────────────────
st.markdown(
    '<div style="text-align:center;color:#374151;font-size:.78rem;margin-top:40px;padding:24px;">'
    'TrendRadar v0.7 · Official YouTube Trending · Built with Streamlit'
    '</div>',
    unsafe_allow_html=True,
)
