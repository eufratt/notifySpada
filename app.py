"""
SPADA UPNVYK attendance notifier.

Endpoint /check (dipanggil cron-job.org tiap 5-10 menit):
  1. login ke SPADA (Moodle)
  2. buka halaman presensi tiap mata kuliah
  3. kalau ada sesi presensi yang terbuka (ada link "Submit attendance"),
     kirim notif ke ntfy. Absennya tetap kamu yang klik.

Environment variables:
  SPADA_USERNAME, SPADA_PASSWORD   akun SPADA (jangan ditulis di kode)
  ATTENDANCE_URLS                  daftar URL halaman presensi, pisah koma
  NTFY_TOPIC                       nama topic ntfy (bikin yang susah ditebak)
  CHECK_KEY                        kunci rahasia untuk memanggil /check
  SPADA_BASE                       opsional, default https://spada.upnyk.ac.id
  NTFY_SERVER                      opsional, default https://ntfy.sh
"""
import json
import os
import re
import threading
from urllib.parse import urljoin, urlparse, parse_qs

import requests
from bs4 import BeautifulSoup
from flask import Flask, abort, jsonify, request

BASE = os.environ.get("SPADA_BASE", "https://spada.upnyk.ac.id").rstrip("/")
NTFY_SERVER = os.environ.get("NTFY_SERVER", "https://ntfy.sh").rstrip("/")
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "")
USERNAME = os.environ.get("SPADA_USERNAME", "")
PASSWORD = os.environ.get("SPADA_PASSWORD", "")
CHECK_KEY = os.environ.get("CHECK_KEY", "")
URLS = [u.strip() for u in os.environ.get("ATTENDANCE_URLS", "").split(",") if u.strip()]

# --- SSL: server SPADA kadang tidak mengirim sertifikat intermediate ---
# Opsi 1 (aman): taruh sertifikat intermediate (PEM) di repo, isi SPADA_EXTRA_CERT=intermediate.pem
# Opsi 2 (kurang aman): SPADA_VERIFY_SSL=false
EXTRA_CERT = os.environ.get("SPADA_EXTRA_CERT", "")
VERIFY_SSL = os.environ.get("SPADA_VERIFY_SSL", "true").strip().lower() != "false"


def build_verify():
    if not VERIFY_SSL:
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        return False
    if EXTRA_CERT:
        import certifi
        combined = "/tmp/ca-combined.pem"
        with open(certifi.where()) as a, open(EXTRA_CERT) as b, open(combined, "w") as out:
            out.write(a.read() + "\n" + b.read() + "\n")
        return combined
    return True


STATE_FILE = "/tmp/notified.json"
UA ="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"

app = Flask(__name__)
_lock = threading.Lock()


# ---------- state (biar nggak spam notif untuk sesi yang sama) ----------
def load_state():
    try:
        with open(STATE_FILE) as f:
            return set(json.load(f))
    except Exception:
        return set()


def save_state(s):
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(sorted(s), f)
    except Exception:
        pass


# ---------- SPADA ----------
def login():
    s = requests.Session()
    s.headers["User-Agent"] = UA
    s.verify = build_verify()
    r = s.get(f"{BASE}/login/index.php", timeout=30)
    soup = BeautifulSoup(r.text, "html.parser")
    token_el = soup.find("input", {"name": "logintoken"})
    data = {"username": USERNAME, "password": PASSWORD}
    if token_el:
        data["logintoken"] = token_el.get("value", "")
    r = s.post(f"{BASE}/login/index.php", data=data, timeout=30)
    if "login/index.php" in r.url or "logout.php" not in r.text:
        raise RuntimeError("Login SPADA gagal (cek username/password atau SPADA lagi bermasalah)")
    return s


def find_open_sessions(s, url):
    """Return (course_title, [ {sessid, link} ]) untuk sesi presensi yang terbuka."""
    r = s.get(url, timeout=30)
    if "login/index.php" in r.url:
        raise RuntimeError("Sesi login kedaluwarsa")
    soup = BeautifulSoup(r.text, "html.parser")

    title_el = soup.select_one("h1") or soup.find("title")
    title = title_el.get_text(" ", strip=True) if title_el else url

    sessions = []
    seen = set()
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "attendance.php" in href and "sessid" in href:
            full = urljoin(url, href)
            sessid = parse_qs(urlparse(full).query).get("sessid", [""])[0]
            if sessid and sessid not in seen:
                seen.add(sessid)
                sessions.append({"sessid": sessid, "link": url})
    return title, sessions


# ---------- ntfy ----------
def notify(title, message, click_url):
    if not NTFY_TOPIC:
        raise RuntimeError("NTFY_TOPIC belum diset")
    requests.post(
        f"{NTFY_SERVER}/{NTFY_TOPIC}",
        data=message.encode("utf-8"),
        headers={
            "Title": title,
            "Priority": "high",
            "Tags": "alarm_clock",
            "Click": click_url,
        },
        timeout=15,
    )


# ---------- routes ----------
@app.get("/")
def health():
    return "ok"


@app.get("/check")
def check():
    if not CHECK_KEY or request.args.get("key") != CHECK_KEY:
        abort(403)
    if not (USERNAME and PASSWORD and URLS):
        return jsonify(error="SPADA_USERNAME / SPADA_PASSWORD / ATTENDANCE_URLS belum diset"), 500

    with _lock:
        notified = load_state()
        results = []
        try:
            s = login()
        except Exception as e:
            return jsonify(error=str(e)), 502

        for url in URLS:
            try:
                title, sessions = find_open_sessions(s, url)
            except Exception as e:
                results.append({"url": url, "error": str(e)})
                continue

            new = [x for x in sessions if f"{url}|{x['sessid']}" not in notified]
            for x in new:
                notify(
                    "Absen SPADA sudah dibuka",
                    f"{title}\nBuka sekarang & klik Submit attendance.",
                    url,
                )
                notified.add(f"{url}|{x['sessid']}")
            results.append({"url": url, "course": title, "open": len(sessions), "notified": len(new)})

        save_state(notified)
    return jsonify(results=results)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))