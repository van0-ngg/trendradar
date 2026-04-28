import streamlit as st
import streamlit.components.v1 as components
import pandas as pd
import plotly.express as px
from datetime import datetime, timezone, timedelta
import html
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


_HAS_LATIN_RE   = re.compile(r'[a-zA-Z]')
_CLEAN_TITLE_RE = re.compile(r'[#\[\]@|]')
_PT_RE          = re.compile(r'PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?')

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

_AI_KEYWORDS = frozenset([
    "ai ", "chatgpt", "midjourney", "elevenlabs", "gpt", "нейросеть",
    "ии ", "heygen", "artificial intelligence", "openai",
])

# ══════════════════════════════════════════════════════════════════════════════
#  CLIENT KEY DATABASE
# ══════════════════════════════════════════════════════════════════════════════

CLIENTS_FILE = Path(__file__).parent / "clients.json"

# ── Supabase: used when SUPABASE_URL + SUPABASE_KEY are set in secrets. ───────
# Falls back to local clients.json for local dev / non-Supabase deploys.
def _use_supabase() -> bool:
    return bool(st.secrets.get("SUPABASE_URL") and st.secrets.get("SUPABASE_KEY"))

@st.cache_resource
def _supabase_client():
    from supabase import create_client  # noqa: PLC0415
    return create_client(st.secrets["SUPABASE_URL"], st.secrets["SUPABASE_KEY"])

def _load_clients() -> list[dict]:
    if _use_supabase():
        try:
            return _supabase_client().table("clients").select("*").execute().data or []
        except Exception:
            return []
    if not CLIENTS_FILE.exists():
        CLIENTS_FILE.write_text("[]", encoding="utf-8")
    try:
        return json.loads(CLIENTS_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []

def _save_clients(clients: list[dict]) -> None:
    CLIENTS_FILE.write_text(json.dumps(clients, indent=2, ensure_ascii=False), encoding="utf-8")

def _key_expired(c: dict) -> bool:
    exp = c.get("expires_at")
    if not exp:
        return False
    try:
        exp_dt = datetime.strptime(exp, "%Y-%m-%d %H:%M UTC").replace(tzinfo=timezone.utc)
        return exp_dt < datetime.now(timezone.utc)
    except ValueError:
        return False

def _send_key_email(to_email: str, key: str, label: str, expires_at: str | None) -> bool:
    smtp_host = str(st.secrets.get("SMTP_HOST", "")).strip()
    smtp_user = str(st.secrets.get("SMTP_USER", "")).strip()
    smtp_pass = str(st.secrets.get("SMTP_PASS", "")).strip()
    smtp_from = str(st.secrets.get("SMTP_FROM", smtp_user)).strip()
    smtp_port = int(st.secrets.get("SMTP_PORT", 587))
    if not (smtp_host and smtp_user and smtp_pass and to_email):
        return False
    app_url   = str(st.secrets.get("APP_URL", "https://trendradar-d5xzalqvywsbdv39nroyjo.streamlit.app")).strip()
    expiry_ln = f"Key expires: {expires_at[:10]}" if expires_at else "Key does not expire."
    body = (
        f"Hi {label},\n\n"
        f"Your TrendRadar access key is ready:\n\n"
        f"    {key}\n\n"
        f"{expiry_ln}\n\n"
        f"Dashboard: {app_url}\n"
        f"Enter your key on the login screen to unlock the dashboard.\n\n"
        f"—\nTrendRadar · YouTube Trends Intelligence\n"
    )
    import smtplib
    from email.mime.text import MIMEText
    from email.mime.multipart import MIMEMultipart
    msg = MIMEMultipart()
    msg["From"]    = smtp_from
    msg["To"]      = to_email
    msg["Subject"] = f"Your TrendRadar Access Key — {key}"
    msg.attach(MIMEText(body, "plain"))
    try:
        with smtplib.SMTP(smtp_host, smtp_port) as srv:
            srv.ehlo(); srv.starttls()
            srv.login(smtp_user, smtp_pass)
            srv.sendmail(smtp_from, to_email, msg.as_string())
        return True
    except Exception as _e:
        print(f"[TrendRadar] Email send failed: {_e!r}")
        return False


def add_client_key(label: str = "", expires_days: int | None = 30, email: str = "") -> str:
    part = lambda: _secrets.token_hex(2).upper()
    new_key = f"TR-{part()}-{part()}"
    expiry = (
        (datetime.now(timezone.utc) + timedelta(days=expires_days)).strftime("%Y-%m-%d %H:%M UTC")
        if expires_days else None
    )
    record = {
        "key":        new_key,
        "label":      label or f"Client #{len(_load_clients()) + 1}",
        "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "expires_at": expiry,
        "email":      email or None,
    }
    if _use_supabase():
        try:
            _supabase_client().table("clients").insert(record).execute()
        except Exception:
            rec_fb = {k: v for k, v in record.items() if k != "email"}
            _supabase_client().table("clients").insert(rec_fb).execute()
    else:
        clients = _load_clients()
        clients.append(record)
        _save_clients(clients)
    if email:
        sent = _send_key_email(email, new_key, record["label"], expiry)
        print(f"[TrendRadar] Key email {'sent' if sent else 'failed'} → {email}")
    return new_key

def revoke_client_key(key: str) -> None:
    if _use_supabase():
        _supabase_client().table("clients").delete().eq("key", key).execute()
    else:
        clients = [c for c in _load_clients() if c["key"] != key]
        _save_clients(clients)

def client_keys_set() -> set[str]:
    return {c["key"] for c in _load_clients() if not _key_expired(c)}


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
  @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800;900&display=swap');

  html, body, [class*="css"] {
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
    background: #000000;
  }

  /* ── Hero ── */
  .hero {
    background: #000000;
    border: 1px solid rgba(255,255,255,.08);
    border-radius: 28px; padding: 56px 60px; margin-bottom: 36px;
    position: relative; overflow: hidden;
  }
  .hero::before {
    content: ""; position: absolute;
    top: -40%; right: -5%; width: 55%; height: 200%;
    background: radial-gradient(ellipse at center, rgba(99,102,241,.1) 0%, transparent 65%);
    pointer-events: none;
  }
  .hero h1 {
    color: #ffffff; font-size: 2.8rem; font-weight: 700; margin: 0;
    letter-spacing: -.04em; line-height: 1.1;
  }
  .hero p  { color: rgba(255,255,255,.45); font-size: 1rem; margin: 14px 0 0; font-weight: 400; }
  .hero-meta {
    color: rgba(255,255,255,.3); font-size: .73rem; margin: 20px 0 0;
    display: flex; gap: 8px; flex-wrap: wrap;
  }
  .hero-meta span {
    background: rgba(255,255,255,.05); border: 1px solid rgba(255,255,255,.08);
    padding: 4px 12px; border-radius: 999px;
  }

  /* ── Metric cards ── */
  .metric-row { display: grid; grid-template-columns: repeat(4,1fr); gap: 12px; margin-bottom: 32px; }
  .metric-card {
    background: #111111; border: 1px solid rgba(255,255,255,.07);
    border-radius: 20px; padding: 24px 26px;
    transition: border-color .2s, transform .15s;
  }
  .metric-card:hover { border-color: rgba(99,102,241,.3); transform: translateY(-2px); }
  .metric-card .label {
    color: rgba(255,255,255,.3); font-size: .68rem; font-weight: 600;
    letter-spacing: .1em; text-transform: uppercase; margin-bottom: 8px;
  }
  .metric-card .value { color: #ffffff; font-size: 2rem; font-weight: 700; line-height: 1; }
  .metric-card .sub   { color: rgba(255,255,255,.22); font-size: .7rem; margin-top: 6px; }
  .metric-card.fire   { border-color: rgba(239,68,68,.2); }
  .metric-card.fire .value { color: #f87171; }
  .metric-card.purple .value { color: #a5b4fc; }
  .metric-card.green  .value { color: #6ee7b7; }

  /* ── Trend card ── */
  .trend-card {
    background: #0d0d0d;
    border: 1px solid rgba(255,255,255,.07); border-radius: 20px;
    padding: 28px 32px; margin-bottom: 4px;
    transition: border-color .25s, box-shadow .25s;
  }
  .trend-card:hover {
    border-color: rgba(99,102,241,.25);
    box-shadow: 0 4px 40px rgba(99,102,241,.06);
  }

  /* ── Badges ── */
  .badge {
    display: inline-block; padding: 3px 10px; border-radius: 999px;
    font-size: .66rem; font-weight: 700; margin-right: 6px; letter-spacing: .04em;
  }
  .badge-fire   { background: rgba(239,68,68,.1); color: #f87171; border: 1px solid rgba(239,68,68,.2); }
  .badge-rising { background: rgba(251,146,60,.1); color: #fba060; border: 1px solid rgba(251,146,60,.2); }
  .badge-new    { background: rgba(56,189,248,.1); color: #7dd3fc; border: 1px solid rgba(56,189,248,.2); }

  /* ── Recipe blocks ── */
  .recipe-block {
    background: #080808; border-left: 2px solid rgba(129,140,248,.35);
    border-radius: 0 14px 14px 0; padding: 20px 24px; margin-top: 14px;
  }
  .recipe-block h4 {
    color: #a5b4fc; font-size: .68rem; margin: 0 0 14px;
    letter-spacing: .12em; text-transform: uppercase;
  }
  .hook-box {
    background: #0f0f0f; border: 1px solid rgba(99,102,241,.18); border-radius: 12px;
    padding: 16px 20px; font-size: .92rem; color: rgba(255,255,255,.75);
    font-style: italic; margin: 10px 0; line-height: 1.6;
  }
  .sound-pill {
    display: inline-flex; align-items: center; gap: 8px;
    background: rgba(99,102,241,.08); border: 1px solid rgba(99,102,241,.2);
    border-radius: 999px; padding: 6px 16px; font-size: .82rem; color: #a5b4fc;
  }
  .capcut-step { display: flex; align-items: flex-start; gap: 14px; margin: 10px 0; }
  .step-num {
    min-width: 26px; height: 26px;
    background: rgba(99,102,241,.12); border: 1px solid rgba(99,102,241,.25);
    border-radius: 50%; display: flex; align-items: center;
    justify-content: center; font-size: .66rem; font-weight: 800; color: #818cf8;
    flex-shrink: 0;
  }
  .step-text { color: rgba(255,255,255,.55); font-size: .85rem; line-height: 1.6; }
  .step-text strong { color: rgba(255,255,255,.85); }

  /* ── Auth / Landing ── */
  .auth-wrap { max-width: 440px; margin: 0 auto; }

  /* ── Divider ── */
  .section-divider {
    height: 1px; background: rgba(255,255,255,.06);
    margin: 28px 0;
  }

  /* ── Links ── */
  a { color: #a5b4fc !important; text-decoration: none; }
  a:hover { color: #c7d2fe !important; }

  /* ── Sidebar ── */
  section[data-testid="stSidebar"] {
    background: #030303 !important;
    border-right: 1px solid rgba(255,255,255,.06) !important;
  }
  section[data-testid="stSidebar"] * { color: rgba(255,255,255,.65); }
  section[data-testid="stSidebar"] .stSelectbox label,
  section[data-testid="stSidebar"] .stSlider label {
    color: rgba(255,255,255,.35) !important; font-size: .82rem;
  }

  /* ── Hide Streamlit chrome ── */
  #MainMenu { visibility: hidden; }
  footer    { visibility: hidden; }
  header    { visibility: hidden; }

  /* ── Stats grid inside trend card ── */
  .stats-grid {
    display: grid; grid-template-columns: repeat(4, 1fr);
    gap: 10px; margin: 18px 0 0;
  }
  .stat-item {
    background: #080808; border: 1px solid rgba(255,255,255,.06);
    border-radius: 12px; padding: 12px 14px;
    text-align: center; transition: border-color .2s;
  }
  .stat-item:hover { border-color: rgba(99,102,241,.22); }
  .stat-icon  { font-size: .9rem; margin-bottom: 4px; display: block; }
  .stat-value {
    font-size: 1.1rem; font-weight: 700; color: #6ee7b7;
    line-height: 1.1; margin-bottom: 2px;
  }
  .stat-value.purple { color: #a5b4fc; }
  .stat-value.orange { color: #fba060; }
  .stat-value.blue   { color: #7dd3fc; }
  .stat-label {
    font-size: .62rem; color: rgba(255,255,255,.2);
    font-weight: 600; letter-spacing: .06em; text-transform: uppercase;
  }

  /* ── Streamlit overrides ── */
  div[data-testid="stExpander"] {
    background: #080808 !important;
    border: 1px solid rgba(255,255,255,.07) !important;
    border-radius: 16px !important;
  }
  button[kind="primary"] {
    background: #6366f1 !important; border: none !important;
    border-radius: 10px !important; font-weight: 600 !important;
  }
  button[kind="primary"]:hover { background: #4f46e5 !important; }

  /* ── Upgrade pill ── */
  .upgrade-pill {
    display: inline-block; background: rgba(99,102,241,.08);
    border: 1px solid rgba(99,102,241,.2); border-radius: 999px;
    padding: 4px 14px; font-size: .72rem; color: #a5b4fc;
  }

  /* ══ KEYFRAMES ═══════════════════════════════════════════════════════════ */
  @keyframes fadeUp {
    from { opacity: 0; transform: translateY(30px); }
    to   { opacity: 1; transform: translateY(0); }
  }
  @keyframes fadeIn {
    from { opacity: 0; }
    to   { opacity: 1; }
  }
  @keyframes pulse-ring {
    0%   { box-shadow: 0 0 0 0 rgba(248,113,113,.3); }
    70%  { box-shadow: 0 0 0 14px rgba(248,113,113,0); }
    100% { box-shadow: 0 0 0 0 rgba(248,113,113,0); }
  }
  @keyframes orb-drift {
    0%,100% { transform: translate(0,0) scale(1); }
    33%     { transform: translate(44px,-34px) scale(1.05); }
    66%     { transform: translate(-28px,22px) scale(0.96); }
  }
  @keyframes orb-drift-r {
    0%,100% { transform: translate(0,0) scale(1); }
    33%     { transform: translate(-36px,28px) scale(1.04); }
    66%     { transform: translate(30px,-18px) scale(0.97); }
  }
  @keyframes fill-bar {
    from { transform: scaleX(0); }
    to   { transform: scaleX(1); }
  }
  @keyframes gradient-shift {
    0%,100% { background-position: 0% 50%; }
    50%     { background-position: 100% 50%; }
  }
  @keyframes badge-breathe {
    0%,100% { opacity: 1; }
    50%     { opacity: .65; }
  }

  /* ══ PAGE-LOAD ENTRANCES ═════════════════════════════════════════════════ */
  .hero { animation: fadeUp .85s cubic-bezier(.16,1,.3,1) both; }

  .metric-card { animation: fadeUp .7s cubic-bezier(.16,1,.3,1) both; }
  .metric-card:nth-child(1) { animation-delay: .05s; }
  .metric-card:nth-child(2) { animation-delay: .12s; }
  .metric-card:nth-child(3) { animation-delay: .19s; }
  .metric-card:nth-child(4) { animation-delay: .26s; }
  .metric-card.fire {
    animation: fadeUp .7s cubic-bezier(.16,1,.3,1) .05s both,
               pulse-ring 2.8s ease-in-out 1.8s infinite;
  }

  /* ══ SCROLL REVEAL (class added by JS) ═══════════════════════════════════ */
  .trend-card {
    opacity: 0;
    transform: translateY(24px);
    transition: opacity .6s cubic-bezier(.16,1,.3,1),
                transform .6s cubic-bezier(.16,1,.3,1);
  }
  .trend-card.is-visible { opacity: 1; transform: translateY(0); }

  /* ══ VELOCITY BAR FILL ═══════════════════════════════════════════════════ */
  .vel-bar-fill {
    transform-origin: left center;
    animation: fill-bar 1s cubic-bezier(.16,1,.3,1) .4s both;
  }

  /* ══ ANIMATED GRADIENT TEXT ══════════════════════════════════════════════ */
  .gradient-text {
    background: linear-gradient(120deg, #818cf8, #c084fc, #60a5fa, #818cf8);
    background-size: 250% 250%;
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    background-clip: text;
    animation: gradient-shift 5s ease infinite;
  }

  /* ══ FIRE BADGE BREATH ═══════════════════════════════════════════════════ */
  .badge-fire { animation: badge-breathe 2.2s ease-in-out infinite; }

  /* ══ BACKGROUND ORBS ═════════════════════════════════════════════════════ */
  .bg-orb {
    position: fixed; border-radius: 50%;
    pointer-events: none; z-index: 0;
  }
  .bg-orb-1 {
    width: 720px; height: 720px;
    background: radial-gradient(circle, rgba(99,102,241,.09) 0%, transparent 65%);
    top: -260px; right: -200px;
    animation: orb-drift 18s ease-in-out infinite;
  }
  .bg-orb-2 {
    width: 520px; height: 520px;
    background: radial-gradient(circle, rgba(192,132,252,.07) 0%, transparent 65%);
    bottom: -180px; left: -160px;
    animation: orb-drift-r 22s ease-in-out infinite;
  }
  .bg-orb-3 {
    width: 350px; height: 350px;
    background: radial-gradient(circle, rgba(96,165,250,.06) 0%, transparent 65%);
    top: 40%; left: 38%;
    animation: orb-drift 28s ease-in-out infinite 4s;
  }
</style>
""", unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════════════════════
#  AUTH GATE
# ══════════════════════════════════════════════════════════════════════════════

if "role" not in st.session_state:
    st.session_state["role"] = None

if st.session_state["role"] is None:
    # ── Landing page ──────────────────────────────────────────────────────────
    st.markdown("""
<div class="bg-orb bg-orb-1"></div>
<div class="bg-orb bg-orb-2"></div>
<div class="bg-orb bg-orb-3"></div>

<div style="text-align:center;padding:72px 0 0;position:relative;z-index:1;">
  <p style="color:rgba(255,255,255,.28);font-size:.72rem;letter-spacing:.16em;
     text-transform:uppercase;margin:0 0 22px;
     animation:fadeIn .8s ease both .05s;">
    YouTube Trends Intelligence
  </p>
  <h1 style="color:#ffffff;font-size:3.4rem;font-weight:700;
     letter-spacing:-.05em;line-height:1.06;margin:0;
     animation:fadeUp .9s cubic-bezier(.16,1,.3,1) both .15s;">
    What's trending<br>
    <span class="gradient-text">right now</span>
  </h1>
  <p style="color:rgba(255,255,255,.38);font-size:1rem;margin:22px auto 0;
     max-width:400px;line-height:1.7;font-weight:400;
     animation:fadeUp .9s cubic-bezier(.16,1,.3,1) both .28s;">
    Discover viral Shorts before they peak. CapCut recipes,
    hook texts, and real engagement data — all in one dashboard.
  </p>
</div>
""", unsafe_allow_html=True)

    st.markdown("<div style='height:44px;animation:fadeIn .6s ease both .38s;'></div>", unsafe_allow_html=True)
    _, col, _ = st.columns([1, 2, 1])
    with col:
        if st.button("🎯  Start for Free", use_container_width=True, type="primary"):
            st.session_state["role"] = "guest"
            st.rerun()

        st.markdown("""
<div style="text-align:center;margin:10px 0 28px;">
  <span style="color:rgba(255,255,255,.2);font-size:.75rem;">
    US market · No CSV export · No credit card required
  </span>
</div>
""", unsafe_allow_html=True)

        stripe_link = str(st.secrets.get("STRIPE_LINK", "")).strip()
        if stripe_link:
            st.markdown(
                f'<div style="text-align:center;padding:16px;'
                f'background:rgba(99,102,241,.05);border:1px solid rgba(99,102,241,.14);'
                f'border-radius:16px;margin-bottom:24px;">'
                f'<div style="color:rgba(255,255,255,.35);font-size:.75rem;margin-bottom:12px;">'
                f'All 9 markets + CSV export</div>'
                f'<a href="{html.escape(stripe_link)}" target="_blank" style="'
                f'display:inline-block;background:#6366f1;color:#fff!important;'
                f'font-weight:600;padding:10px 28px;border-radius:10px;'
                f'font-size:.88rem;text-decoration:none!important;">'
                f'Upgrade to Pro — $15/mo</a>'
                f'<div style="color:rgba(255,255,255,.2);font-size:.7rem;margin-top:10px;">'
                f'Key delivered by email</div></div>',
                unsafe_allow_html=True,
            )

        st.markdown("""
<div style="text-align:center;color:rgba(255,255,255,.18);font-size:.72rem;
   letter-spacing:.08em;text-transform:uppercase;margin-bottom:10px;">
  Already have a key?
</div>
""", unsafe_allow_html=True)

        key_input = st.text_input(
            "Access Key",
            type="password",
            placeholder="TR-XXXX-XXXX",
            label_visibility="collapsed",
        )
        if st.button("🔓 Unlock Pro Access", use_container_width=True):
            k = key_input.strip()
            admin_keys = list(st.secrets.get("ADMIN_KEYS", []))
            demo_key   = str(st.secrets.get("DEMO_KEY", "")).strip()
            if k and k in admin_keys:
                st.session_state["role"] = "admin"
                st.rerun()
            elif k and demo_key and k == demo_key:
                st.session_state["role"] = "demo"
                st.rerun()
            elif k and k in client_keys_set():
                st.session_state["role"] = "user"
                st.rerun()
            else:
                st.error("❌ Invalid key.")

    st.stop()


# ── Scroll-reveal: IntersectionObserver adds .is-visible to .trend-card ──────
components.html("""
<script>
(function(){
  function init(){
    var doc = window.parent.document;
    var cards = doc.querySelectorAll('.trend-card');
    if(!cards.length){ setTimeout(init, 400); return; }
    var obs = new IntersectionObserver(function(entries){
      entries.forEach(function(e){
        if(e.isIntersecting){
          e.target.classList.add('is-visible');
          obs.unobserve(e.target);
        }
      });
    }, { threshold: 0.06 });
    cards.forEach(function(c){ obs.observe(c); });
  }
  setTimeout(init, 700);
})();
</script>
""", height=0)


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

def _duration_seconds(duration_str: str) -> int:
    """Parse ISO 8601 PT#H#M#S to total seconds."""
    if not duration_str:
        return 0
    match = _PT_RE.match(duration_str)
    if not match:
        return 0
    hours   = int(match.group(1)) if match.group(1) else 0
    minutes = int(match.group(2)) if match.group(2) else 0
    seconds = int(match.group(3)) if match.group(3) else 0
    return hours * 3600 + minutes * 60 + seconds

def is_short(item: dict) -> bool:
    """True if video is ≤ 60 s OR explicitly tagged #shorts."""
    raw   = item.get("contentDetails", {}).get("duration", "PT0S")
    title = item.get("snippet", {}).get("title", "").lower()
    tags  = " ".join(item.get("snippet", {}).get("tags", [])).lower()
    return _duration_seconds(raw) <= 60 or "#shorts" in title or "#short" in title or "#shorts" in tags

def is_long_video(item: dict) -> bool:
    """True if video is strictly > 2 minutes (120 s)."""
    return _duration_seconds(item.get("contentDetails", {}).get("duration", "PT0S")) > 120

def hours_since(published_at: str) -> float:
    pub = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
    return max((datetime.now(timezone.utc) - pub).total_seconds() / 3600, 0.1)

def velocity_score(views: int, age_hours: float) -> float:
    return round(views / age_hours, 0)

def categorise(title: str, description: str) -> str:
    text = (title + " " + description).lower()
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
#  FETCH DATA — videos.list(chart="mostPopular") · up to 4 pages · ~4 quota units
#  Format split done locally: Shorts ≤ 60 s, Long > 2 m. No server-side filters.
# ══════════════════════════════════════════════════════════════════════════════

@st.cache_data(ttl=7200, show_spinner=False)
def fetch_trending_videos(region_code: str, country_name: str, key_index: int = 0, fmt: str = "shorts") -> list[dict]:
    yt = get_youtube(key_index)

    # ── Pull mostPopular chart (4 pages × 50 = up to 200 videos) ─────────────
    all_items: list[dict] = []
    page_token: str | None = None

    for _ in range(4):
        kwargs: dict = dict(
            part="snippet,statistics,contentDetails",
            chart="mostPopular",
            regionCode=region_code,
            maxResults=50,
        )
        if page_token:
            kwargs["pageToken"] = page_token

        resp = yt.videos().list(**kwargs).execute()
        all_items.extend(resp.get("items", []))

        page_token = resp.get("nextPageToken")
        if not page_token:
            break

    if not all_items:
        return []

    # ── Split by format, score, build result list (no hard server-side filters) ─
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
        stats   = item.get("statistics", {})
        pub_at  = snippet.get("publishedAt", "")
        vid_id  = item["id"]

        views    = int(stats.get("viewCount",  0))
        likes    = int(stats.get("likeCount",  0))
        comments = int(stats.get("commentCount", 0))
        eng_rate = round((likes + comments) / max(views, 1) * 100, 2)

        suspect_engagement = views > 100_000 and eng_rate < 1.0

        age_h          = hours_since(pub_at) if pub_at else 0.1
        vel            = velocity_score(views, age_h)
        niche          = categorise(title, desc)
        content_format = tag_content_format(title, desc)
        pace_lbl, cpm, transition = pace_for_niche(niche)
        age_str        = f"{age_h:.0f}h ago" if age_h < 48 else f"{age_h/24:.0f}d ago"
        video_url      = f"https://youtube.com/shorts/{vid_id}" if fmt == "shorts" else f"https://youtube.com/watch?v={vid_id}"

        results.append({
            "id":                 vid_id,
            "title":              title,
            "niche":              niche,
            "content_format":     content_format,
            "views":              views,
            "likes":              likes,
            "comments":           comments,
            "age_hours":          round(age_h, 1),
            "age_str":            age_str,
            "velocity":           vel,
            "velocity_score":     0.0,
            "engagement":         eng_rate,
            "suspect_engagement": suspect_engagement,
            "sound":              sound_for_niche(niche),
            "pace_label":         pace_lbl,
            "cuts_per_min":       cpm,
            "transition":         transition,
            "hooks":              generate_hooks(title, niche),
            "capcut_steps":       generate_capcut_steps(niche, title),
            "mj_prompt":          generate_mj_prompt(title, niche),
            "thumb":              snippet.get("thumbnails", {}).get("high", {}).get("url", ""),
            "url":                video_url,
        })

    print(f"[TrendRadar] fmt={fmt} region={region_code} | Total fetched: {len(all_items)}, After filter: {len(results)}")

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


def _log_usage(role: str, region_code: str, fmt: str) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    print(f"[USAGE] {ts} | role={role} | region={region_code} | fmt={fmt}")
    if _use_supabase():
        try:
            _supabase_client().table("usage_logs").insert({
                "logged_at": ts,
                "role":      role,
                "region":    region_code,
                "fmt":       fmt,
            }).execute()
        except Exception as _e:
            print(f"[TrendRadar] Usage log failed: {_e!r}")


# ══════════════════════════════════════════════════════════════════════════════
#  SIDEBAR
# ══════════════════════════════════════════════════════════════════════════════

with st.sidebar:
    role = st.session_state["role"]

    if role == "admin":
        st.markdown(
            '<div style="background:rgba(99,102,241,.08);border:1px solid rgba(99,102,241,.25);'
            'border-radius:10px;padding:8px 14px;font-size:.78rem;color:#a5b4fc;margin-bottom:12px;">'
            '🛡️ Logged in as <strong>Admin</strong></div>',
            unsafe_allow_html=True,
        )
    elif role == "demo":
        st.markdown(
            '<div style="background:rgba(110,231,183,.05);border:1px solid rgba(110,231,183,.2);'
            'border-radius:10px;padding:8px 14px;font-size:.78rem;color:#6ee7b7;margin-bottom:12px;">'
            '🎯 <strong>Demo Mode</strong> — US only · no CSV export</div>',
            unsafe_allow_html=True,
        )
    elif role == "guest":
        stripe_link_sb = str(st.secrets.get("STRIPE_LINK", "")).strip()
        upgrade_link = f' <a href="{html.escape(stripe_link_sb)}" target="_blank" style="color:#818cf8!important;">Upgrade →</a>' if stripe_link_sb else ""
        st.markdown(
            f'<div style="background:rgba(255,255,255,.03);border:1px solid rgba(255,255,255,.08);'
            f'border-radius:10px;padding:8px 14px;font-size:.78rem;color:rgba(255,255,255,.45);margin-bottom:12px;">'
            f'🆓 <strong>Free</strong> — US only · no CSV{upgrade_link}</div>',
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            '<div style="background:rgba(255,255,255,.03);border:1px solid rgba(255,255,255,.07);'
            'border-radius:10px;padding:8px 14px;font-size:.78rem;color:rgba(255,255,255,.4);margin-bottom:12px;">'
            '👤 Logged in as <strong>Client</strong></div>',
            unsafe_allow_html=True,
        )

    st.markdown("### ⚙️ Controls")

    content_fmt = st.radio(
        "📺 Content Format",
        ["Shorts (≤ 60s)", "Long Videos (> 2m)"],
        horizontal=True,
    )
    fmt_key = "shorts" if content_fmt == "Shorts (≤ 60s)" else "long"

    if role in ("demo", "guest"):
        country_name = "🇺🇸 United States"
        lock_note = "demo locked" if role == "demo" else "free plan"
        st.markdown(
            f'<div style="font-size:.82rem;color:rgba(255,255,255,.4);margin:4px 0 12px;">🌍 Market — '
            f'<strong style="color:rgba(255,255,255,.8);">🇺🇸 United States</strong> '
            f'<span style="color:rgba(255,255,255,.25);font-size:.73rem;">({lock_note})</span></div>',
            unsafe_allow_html=True,
        )
    else:
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
    min_vel     = st.slider("Min Velocity Score", 0, 100, 0, 5)
    min_views_k = st.slider("Min Views (×1K)", 0, 5_000, 0, 100)
    min_eng     = st.slider("Min Engagement %", 0.0, 10.0, 0.0, 0.1, format="%.1f%%")

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
    st.caption("TrendRadar · YouTube Trends Intelligence · v0.7")

    if role == "admin":
        st.markdown("---")
        with st.expander("🛠️ Admin Dashboard", expanded=True):
            st.markdown("**Generate Client Key**")
            new_label = st.text_input(
                "Client name / label",
                placeholder="e.g. John Smith / Agency X",
                key="admin_label",
            )
            new_email = st.text_input(
                "Client email (auto-sends key if SMTP configured)",
                placeholder="client@example.com",
                key="admin_email",
            )
            expires_opt = st.selectbox(
                "Expires in",
                [30, 60, 90, 180, 365, None],
                format_func=lambda x: f"{x} days" if x else "Never",
                key="admin_expires",
            )
            if st.button("➕ Generate & Send Key", use_container_width=True, type="primary"):
                st.session_state["last_generated_key"] = add_client_key(
                    label=new_label, expires_days=expires_opt, email=new_email.strip()
                )
                if new_email.strip():
                    st.session_state["last_key_email"] = new_email.strip()

            if "last_generated_key" in st.session_state:
                last_email = st.session_state.get("last_key_email", "")
                sent_note  = f" · emailed to {last_email}" if last_email else " · no email sent (SMTP not set)"
                st.success(f"Key generated{sent_note}:")
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
                        expired      = _key_expired(c)
                        exp_raw      = c.get("expires_at")
                        exp_display  = f"expires {exp_raw[:10]}" if exp_raw else "no expiry"
                        expired_tag  = ' <span style="color:#ef4444;font-weight:700;">EXPIRED</span>' if expired else ""
                        key_color    = "#6b7280" if expired else "#a78bfa"
                        email_display = f' · ✉️ {c["email"]}' if c.get("email") else ""
                        st.markdown(
                            f'<div style="font-size:.78rem;color:{key_color};font-family:monospace;">'
                            f'{c["key"]}{expired_tag}</div>'
                            f'<div style="font-size:.7rem;color:#6b7280;">'
                            f'{c.get("label","")} · {c.get("created_at","")} · {exp_display}{email_display}</div>',
                            unsafe_allow_html=True,
                        )
                    with col_btn:
                        if st.button("✕", key=f"revoke_{c['key']}", help="Revoke"):
                            revoke_client_key(c["key"])
                            if st.session_state.get("last_generated_key") == c["key"]:
                                del st.session_state["last_generated_key"]
                            st.rerun()

            st.markdown("---")
            st.markdown("**Usage Analytics**")
            if _use_supabase():
                try:
                    logs = _supabase_client().table("usage_logs").select("*").order("logged_at", desc=True).limit(50).execute().data or []
                    if logs:
                        total = len(logs)
                        by_region = {}
                        by_role   = {}
                        for lg in logs:
                            by_region[lg.get("region","?")] = by_region.get(lg.get("region","?"), 0) + 1
                            by_role[lg.get("role","?")]     = by_role.get(lg.get("role","?"), 0) + 1
                        st.caption(f"Last 50 sessions — total tracked: {total}")
                        top_regions = sorted(by_region.items(), key=lambda x: -x[1])[:5]
                        st.caption("Top regions: " + " · ".join(f"{r}({n})" for r, n in top_regions))
                        st.caption("By role: " + " · ".join(f"{r}({n})" for r, n in by_role.items()))
                        st.caption(f"Latest: {logs[0]['logged_at']} — {logs[0].get('role','?')} / {logs[0].get('region','?')}")
                    else:
                        st.caption("No usage logs yet.")
                except Exception:
                    st.caption("Usage log table not found. Run SQL to create it.")
            else:
                st.caption("Supabase not connected — usage not tracked.")


# ══════════════════════════════════════════════════════════════════════════════
#  HEADER
# ══════════════════════════════════════════════════════════════════════════════

now_str = datetime.now(timezone.utc).strftime("%H:%M UTC")
st.markdown(
    f'<div class="hero">'
    f'<h1>TrendRadar</h1>'
    f'<p>What\'s trending on YouTube {country_name} right now — CapCut recipe included.</p>'
    f'<div class="hero-meta">'
    f'<span>📡 Live data</span>'
    f'<span>🕒 {now_str}</span>'
    f'<span>🔄 Updated every 2 h</span>'
    f'<span>✅ Real engagement only</span>'
    f'</div></div>',
    unsafe_allow_html=True,
)


# ══════════════════════════════════════════════════════════════════════════════
#  LOAD DATA
# ══════════════════════════════════════════════════════════════════════════════

with st.spinner(f"Loading {content_fmt} for {country_name}…"):
    try:
        all_trends = load_trending_videos(selected_country["code"], country_name, fmt_key)
        if all_trends is not None:
            _log_usage(role, selected_country["code"], fmt_key)
    except HttpError as e:
        st.error(f"YouTube API error: {e}")
        st.stop()

if all_trends is None:
    now_utc   = datetime.now(timezone.utc)
    reset_utc = now_utc.replace(hour=8, minute=0, second=0, microsecond=0)
    if reset_utc <= now_utc:
        reset_utc += timedelta(days=1)
    hours_left = int((reset_utc - now_utc).total_seconds() // 3600)
    mins_left  = int(((reset_utc - now_utc).total_seconds() % 3600) // 60)
    st.warning(
        f"⚠️ **API quota exhausted for today.**\n\n"
        f"YouTube resets quotas daily at **08:00 UTC** "
        f"(in ~**{hours_left}h {mins_left}m**). "
        f"Try refreshing after that or contact support."
    )
    st.stop()

if not all_trends:
    label = "Shorts" if fmt_key == "shorts" else "Long Videos"
    st.warning(f"No {label} found matching the criteria. Try lowering filters or changing the market.")
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

min_views = min_views_k * 1_000
filtered = [
    t for t in all_trends
    if t["niche"]           in selected_niches
    and t["content_format"] in selected_formats
    and t["velocity_score"] >= min_vel
    and t["views"]          >= min_views
    and t["engagement"]     >= min_eng
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
    f'<div class="metric-card fire"><div class="label">🔥 Viral Now</div><div class="value">{fire_count}</div><div class="sub">videos trending hard</div></div>'
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
            paper_bgcolor="#000000", plot_bgcolor="#0d0d0d",
            font_color="rgba(255,255,255,.5)", height=440,
            margin=dict(l=0, r=24, t=10, b=10),
            yaxis=dict(tickfont=dict(size=10)),
            legend=dict(bgcolor="#000000", bordercolor="rgba(255,255,255,.08)"),
        )
        fig.update_traces(textposition="outside", textfont_color="#ffffff")
        st.plotly_chart(fig, use_container_width=True)

with col_export:
    st.markdown("<div style='height:12px'></div>", unsafe_allow_html=True)
    if filtered and role not in ("demo", "guest"):
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
    elif role in ("demo", "guest"):
        stripe_link_csv = str(st.secrets.get("STRIPE_LINK", "")).strip()
        upgrade_txt = f'<a href="{html.escape(stripe_link_csv)}" target="_blank" style="color:#818cf8!important;font-size:.7rem;">Upgrade →</a>' if stripe_link_csv else "upgrade to unlock"
        st.markdown(
            f'<div style="font-size:.72rem;color:rgba(255,255,255,.25);text-align:center;padding:8px 0;line-height:1.6;">'
            f'⬇️ CSV Export<br>{upgrade_txt}</div>',
            unsafe_allow_html=True,
        )


fmt_label = "Shorts" if fmt_key == "shorts" else "Long Videos"
st.markdown(f"### Found **{len(filtered)}** {fmt_label} in the official trending chart")
st.markdown('<div class="section-divider"></div>', unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════════════════════
#  TREND CARDS
# ══════════════════════════════════════════════════════════════════════════════

for trend in filtered:
    try:
        vel     = trend.get("velocity_score", 0.0)
        bar_w   = int(vel)
        age_str = trend.get("age_str", "?")

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
                f'<div style="display:flex;justify-content:space-between;align-items:flex-start;flex-wrap:wrap;gap:8px;">'
                f'<div>'
                f'<span class="badge {trend["badge"]}">{trend["hot_label"]}</span>'
                f'<span class="badge" style="background:rgba(99,102,241,.08);color:#a5b4fc;border:1px solid rgba(99,102,241,.2);">{safe_niche}</span>'
                f'<span class="badge" style="background:rgba(56,189,248,.07);color:#7dd3fc;border:1px solid rgba(56,189,248,.18);">{trend["content_format"]}</span>'
                f'{suspect_badge}'
                f'</div>'
                f'<div style="color:rgba(255,255,255,.25);font-size:.75rem;background:rgba(255,255,255,.04);padding:3px 10px;border-radius:999px;border:1px solid rgba(255,255,255,.07);">🕒 {age_str}</div>'
                f'</div>'
                f'<h3 style="color:#ffffff;margin:16px 0 4px;font-size:1.1rem;font-weight:700;line-height:1.45;letter-spacing:-.02em;">'
                f'<a href="{trend["url"]}" target="_blank" style="color:#ffffff!important;">{safe_title}</a>'
                f'</h3>'
                f'<div style="color:rgba(255,255,255,.22);font-size:.65rem;margin:14px 0 6px;letter-spacing:.1em;text-transform:uppercase;">Velocity Score</div>'
                f'<div style="display:flex;align-items:center;gap:14px;margin-bottom:2px;">'
                f'<div style="flex:1;background:rgba(255,255,255,.06);border-radius:999px;height:6px;overflow:hidden;">'
                f'<div class="vel-bar-fill" style="width:{bar_w}%;height:6px;border-radius:999px;background:linear-gradient(90deg,#6366f1,#818cf8);"></div>'
                f'</div>'
                f'<div style="font-size:1.45rem;font-weight:700;color:#a5b4fc;min-width:56px;text-align:right;line-height:1;">'
                f'{vel}<span style="font-size:.62rem;color:rgba(255,255,255,.2);font-weight:400;">/100</span>'
                f'</div>'
                f'</div>'
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

    except Exception as _card_err:
        print(f"[TrendRadar] Card render error for '{trend.get('title', '?')[:50]}': {_card_err!r}")
        st.warning(f"Could not render card: {_card_err}")


# ── Footer ────────────────────────────────────────────────────────────────────
st.markdown(
    '<div style="text-align:center;color:#374151;font-size:.78rem;margin-top:40px;padding:24px;">'
    'TrendRadar v0.7 · Official YouTube Trending · Built with Streamlit'
    '</div>',
    unsafe_allow_html=True,
)
