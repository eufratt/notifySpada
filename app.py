"""
SPADA UPNVYK attendance notifier.

Endpoint /check (dipanggil cron-job.org tiap 5-10 menit):
  1. login ke SPADA (Moodle)
  2. kumpulkan halaman presensi:
       - yang kamu tulis langsung di ATTENDANCE_URLS, dan/atau
       - semua modul presensi yang ada di halaman mata kuliah (COURSE_URLS),
         jadi link presensi yang berubah tiap minggu ikut terdeteksi otomatis
  3. kalau ada sesi presensi yang terbuka (ada link "Submit attendance"),
     kirim notif ke ntfy. Absennya tetap kamu yang klik.

Environment variables:
  SPADA_USERNAME, SPADA_PASSWORD   akun SPADA (jangan ditulis di kode)
  COURSE_URLS                      URL halaman utama mata kuliah (course/view.php?id=...), pisah koma
  ATTENDANCE_URLS                  URL halaman presensi tertentu, pisah koma (opsional jika COURSE_URLS diisi)
  NTFY_TOPIC                       nama topic ntfy (bikin yang susah ditebak)
  CHECK_KEY                        kunci rahasia untuk memanggil /check
  SPADA_BASE                       opsional, default https://spada.upnyk.ac.id
  NTFY_SERVER                      opsional, default https://ntfy.sh
  SPADA_VERIFY_SSL / SPADA_EXTRA_CERT   lihat README (masalah sertifikat SPADA)
"""
import json
import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urljoin, urlparse, parse_qs

import requests
from bs4 import BeautifulSoup
from flask import Flask, abort, jsonify, request


def _split(name):
    return [u.strip() for u in os.environ.get(name, "").split(",") if u.strip()]


BASE = os.environ.get("SPADA_BASE", "https://spada.upnyk.ac.id").rstrip("/")
NTFY_SERVER = os.environ.get("NTFY_SERVER", "https://ntfy.sh").rstrip("/")
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "")
USERNAME = os.environ.get("SPADA_USERNAME", "")
PASSWORD = os.environ.get("SPADA_PASSWORD", "")
CHECK_KEY = os.environ.get("CHECK_KEY", "")
URLS = _split("ATTENDANCE_URLS")
COURSE_URLS = _split("COURSE_URLS")

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
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"

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


def module_id(url):
    return parse_qs(urlparse(url).query).get("id", [""])[0]


def find_course_modules(s, course_url):
    """Return [ {id, url, name} ] untuk semua modul presensi di halaman mata kuliah."""
    r = s.get(course_url, timeout=30)
    if "login/index.php" in r.url:
        raise RuntimeError("Sesi login kedaluwarsa")
    soup = BeautifulSoup(r.text, "html.parser")

    mods = {}
    for a in soup.find_all("a", href=True):
        if "/mod/attendance/view.php" not in a["href"]:
            continue
        mid = module_id(urljoin(course_url, a["href"]))
        if not mid:
            continue
        name = re.sub(r"\s*Attendance$", "", a.get_text(" ", strip=True)).strip()
        if mid not in mods:
            mods[mid] = {"id": mid, "url": f"{BASE}/mod/attendance/view.php?id={mid}", "name": name}
        elif not mods[mid]["name"] and name:
            mods[mid]["name"] = name
    for m in mods.values():
        if not m["name"]:
            m["name"] = f"Presensi {m['id']}"
    return list(mods.values())


def find_open_sessions(s, url):
    """Return (course_title, activity_title, [ {sessid} ]) untuk sesi presensi yang terbuka."""
    r = s.get(url, timeout=30)
    if "login/index.php" in r.url:
        raise RuntimeError("Sesi login kedaluwarsa")
    soup = BeautifulSoup(r.text, "html.parser")

    title_el = soup.select_one("h1") or soup.find("title")
    course = title_el.get_text(" ", strip=True) if title_el else url

    crumbs = [li.get_text(" ", strip=True) for li in soup.select(".breadcrumb li, li.breadcrumb-item")]
    activity = crumbs[-2] if len(crumbs) >= 2 else None

    sessions = []
    seen = set()
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "attendance.php" in href and "sessid" in href:
            full = urljoin(url, href)
            sessid = parse_qs(urlparse(full).query).get("sessid", [""])[0]
            if sessid and sessid not in seen:
                seen.add(sessid)
                sessions.append({"sessid": sessid})
    return course, activity, sessions


# ---------- ntfy ----------
def notify(title, message, click_url):
    if not NTFY_TOPIC:
        raise RuntimeError("NTFY_TOPIC belum diset")
    r = requests.post(
        f"{NTFY_SERVER}/{NTFY_TOPIC}",
        data=message.encode("utf-8"),
        headers={
            "Title": title,  # header harus ASCII, nama matkul masuk ke body
            "Priority": "high",
            "Tags": "alarm_clock",
            "Click": click_url,
        },
        timeout=15,
    )
    r.raise_for_status()


# ---------- routes ----------
@app.get("/")
def health():
    return "ok"


@app.get("/check")
def check():
    if not CHECK_KEY or request.args.get("key") != CHECK_KEY:
        abort(403)
    if not (USERNAME and PASSWORD and (URLS or COURSE_URLS)):
        return jsonify(error="SPADA_USERNAME / SPADA_PASSWORD dan ATTENDANCE_URLS atau COURSE_URLS belum diset"), 500
    verbose = request.args.get("verbose") == "1"

    with _lock:
        notified = load_state()
        try:
            s = login()
        except Exception as e:
            return jsonify(error=str(e)), 502

        errors = []
        course_info = []
        targets = {}

        for u in URLS:
            key = module_id(u) or u
            targets.setdefault(key, {"id": key, "url": u, "name": None})

        for cu in COURSE_URLS:
            try:
                mods = find_course_modules(s, cu)
            except Exception as e:
                errors.append({"url": cu, "error": str(e)})
                continue
            course_info.append({"course_url": cu, "modules_found": len(mods)})
            for m in mods:
                cur = targets.get(m["id"])
                if cur is None:
                    targets[m["id"]] = m
                elif not cur.get("name"):
                    cur["name"] = m["name"]

        def work(t):
            try:
                course, activity, sessions = find_open_sessions(s, t["url"])
                return {"t": t, "course": course, "activity": t.get("name") or activity, "sessions": sessions}
            except Exception as e:
                return {"t": t, "error": str(e)}

        with ThreadPoolExecutor(max_workers=4) as ex:
            outcomes = list(ex.map(work, list(targets.values())))

        results = []
        for o in outcomes:
            t = o["t"]
            if "error" in o:
                errors.append({"url": t["url"], "error": o["error"]})
                continue

            sent = 0
            for x in o["sessions"]:
                key = f"{t['id']}|{x['sessid']}"
                if key in notified:
                    continue
                label = f"{o['course']}\n{o['activity']}" if o["activity"] else o["course"]
                try:
                    notify("Absen SPADA sudah dibuka", f"{label}\nBuka sekarang & klik Submit attendance.", t["url"])
                except Exception as e:
                    errors.append({"url": t["url"], "error": f"notif gagal: {e}"})
                    continue
                notified.add(key)
                sent += 1

            if verbose or o["sessions"]:
                results.append({
                    "course": o["course"],
                    "activity": o["activity"],
                    "url": t["url"],
                    "open": len(o["sessions"]),
                    "notified": sent,
                })

        save_state(notified)

    return jsonify(checked=len(targets), courses=course_info, results=results, errors=errors)


@app.get("/test")
def test_notif():
    """Kirim notif percobaan ke ntfy (untuk memastikan Render -> ntfy -> HP jalan)."""
    if not CHECK_KEY or request.args.get("key") != CHECK_KEY:
        abort(403)
    try:
        notify("Tes notif SPADA", "Kalau kamu baca ini, notif dari bot ke HP sudah jalan.", BASE)
    except Exception as e:
        return jsonify(ok=False, error=str(e)), 502
    return jsonify(ok=True)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))