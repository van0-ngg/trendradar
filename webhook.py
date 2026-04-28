"""
TrendRadar — Gumroad webhook
Receives purchase pings, generates a Pro key, saves to Supabase, emails the buyer.

Deploy on Render (free tier):
  Build command : pip install flask supabase
  Start command : python webhook.py
  Root dir      : / (repo root)

Env vars required:
  SUPABASE_URL     — from Supabase project settings
  SUPABASE_KEY     — anon/service key
  SMTP_HOST / SMTP_PORT / SMTP_USER / SMTP_PASS / SMTP_FROM
  WEBHOOK_SECRET   — any random string (add as ?secret=... in Gumroad URL)
  APP_URL          — your Streamlit app URL
"""

from flask import Flask, request, jsonify
import os
import secrets as _secrets
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timezone

app = Flask(__name__)

WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "")
SUPABASE_URL   = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY   = os.environ.get("SUPABASE_KEY", "")
APP_URL        = os.environ.get("APP_URL", "https://trendradar-d5xzalqvywsbdv39nroyjo.streamlit.app")
SMTP_HOST      = os.environ.get("SMTP_HOST", "")
SMTP_PORT      = int(os.environ.get("SMTP_PORT", 587))
SMTP_USER      = os.environ.get("SMTP_USER", "")
SMTP_PASS      = os.environ.get("SMTP_PASS", "")
SMTP_FROM      = os.environ.get("SMTP_FROM", "")


def _supabase():
    from supabase import create_client
    return create_client(SUPABASE_URL, SUPABASE_KEY)


def _generate_key() -> str:
    part = lambda: _secrets.token_hex(2).upper()
    return f"TR-{part()}-{part()}"


def _send_email(to_email: str, key: str) -> bool:
    smtp_from = SMTP_FROM or SMTP_USER
    if not (SMTP_HOST and SMTP_USER and SMTP_PASS and to_email):
        return False

    body = f"""\
Your TrendRadar Pro access key:

    {key}

Open the app and enter your key on the start screen:
{APP_URL}

This unlocks all 9 markets and CSV export.
Key is valid as long as your subscription is active.

— TrendRadar · YouTube Trends Intelligence
"""
    msg = MIMEMultipart()
    msg["From"]    = smtp_from
    msg["To"]      = to_email
    msg["Subject"] = f"Your TrendRadar Pro Key — {key}"
    msg.attach(MIMEText(body, "plain"))

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as srv:
            srv.ehlo()
            srv.starttls()
            srv.login(SMTP_USER, SMTP_PASS)
            srv.sendmail(smtp_from, to_email, msg.as_string())
        return True
    except Exception as exc:
        print(f"[webhook] email failed: {exc!r}")
        return False


@app.route("/webhook/gumroad", methods=["POST"])
def gumroad():
    # Secret check
    if WEBHOOK_SECRET and request.args.get("secret") != WEBHOOK_SECRET:
        return jsonify({"error": "unauthorized"}), 401

    data    = request.form
    email   = (data.get("email") or "").strip()
    sale_id = (data.get("sale_id") or "").strip()
    is_test = data.get("test", "false").lower() == "true"

    if not email:
        return jsonify({"error": "no email"}), 400

    if is_test:
        print(f"[webhook] test purchase — ignored ({email})")
        return jsonify({"status": "test_ignored"}), 200

    # Deduplicate: skip if sale_id already stored
    if SUPABASE_URL and sale_id:
        try:
            hit = _supabase().table("clients").select("key").eq("label", f"gumroad-{sale_id}").execute()
            if hit.data:
                print(f"[webhook] duplicate sale_id {sale_id} — skipped")
                return jsonify({"status": "duplicate"}), 200
        except Exception:
            pass

    key    = _generate_key()
    now    = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    label  = f"gumroad-{sale_id}" if sale_id else f"gumroad-{email}"
    record = {
        "key":        key,
        "label":      label,
        "created_at": now,
        "expires_at": None,
        "email":      email,
    }

    if SUPABASE_URL:
        try:
            _supabase().table("clients").insert(record).execute()
        except Exception as exc:
            print(f"[webhook] supabase insert failed: {exc!r}")
            # retry without email column (in case it doesn't exist yet)
            try:
                _supabase().table("clients").insert({k: v for k, v in record.items() if k != "email"}).execute()
            except Exception as exc2:
                print(f"[webhook] supabase fallback failed: {exc2!r}")
                return jsonify({"error": "db_error"}), 500

    sent = _send_email(email, key)
    print(f"[webhook] sale={sale_id} key={key} email={'ok' if sent else 'FAILED'} to={email}")
    return jsonify({"status": "ok", "email_sent": sent}), 200


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"}), 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    app.run(host="0.0.0.0", port=port)
