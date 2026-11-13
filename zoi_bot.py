#!/usr/bin/env python3
"""Zoi Network auto-sync bot.

Login email+password -> OTP via Gmail IMAP -> sessionToken.
Sync tiap 4 jam (window resmi Zoi), streak multiplier naik per tier.

Modes:
  --status   tampilkan state akun (balance, streak, next sync)
  --once     sync kalau window udah buka, diam kalau belum (dipakai cron)
  --login    paksa login ulang (buang token cache)

Kredensial: environment variable atau file .env (lihat .env.example).
"""
import argparse
import email
import imaplib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone, timedelta

API = os.environ.get("ZOI_API_BASE", "https://api.zoi.network/v1")

# Kredensial dibaca berlapis: environment variable dulu (cocok buat Docker /
# GitHub Actions / systemd EnvironmentFile), lalu file env.
ENV_FILE_CANDIDATES = [
    os.environ.get("ZOI_ENV_FILE", ""),
    os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"),
    os.path.expanduser("~/.config/zoi-auto-sync/.env"),
    os.path.expanduser("~/.agent/credentials/zoi.env"),
]
REQUIRED = ("ZOI_EMAIL", "ZOI_PASSWORD", "ZOI_DEVICE_ID", "ZOI_IMAP_PASS")


def load_creds():
    """Gabung os.environ + file env. Env var menang atas file."""
    vals = {}
    for path in ENV_FILE_CANDIDATES:
        if not path or not os.path.isfile(path):
            continue
        try:
            with open(path) as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    k, v = line.split("=", 1)
                    vals[k.strip()] = v.strip().strip('"').strip("'")
        except OSError:
            continue
    # env var menimpa file
    for k in list(vals) + list(REQUIRED) + ["ZOI_IMAP_HOST", "ZOI_IMAP_USER"]:
        if os.environ.get(k):
            vals[k] = os.environ[k]
    return vals


_C = load_creds()
_missing = [k for k in REQUIRED if not _C.get(k)]
if _missing:
    raise SystemExit(
        "kredensial kurang: " + ", ".join(_missing) + "\n"
        "Isi lewat environment variable, atau copy .env.example jadi .env "
        "(atau set ZOI_ENV_FILE ke path file env kamu)."
    )

EMAIL = _C["ZOI_EMAIL"]
PASSWORD = _C["ZOI_PASSWORD"]
DEVICE_ID = _C["ZOI_DEVICE_ID"]
IMAP_HOST = _C.get("ZOI_IMAP_HOST") or "imap.gmail.com"
IMAP_USER = _C.get("ZOI_IMAP_USER") or EMAIL
IMAP_PASS = _C["ZOI_IMAP_PASS"]

STATE_DIR = os.path.expanduser(os.environ.get("ZOI_STATE_DIR", "~/.zoi-auto-sync"))
TOKEN_FILE = os.path.join(STATE_DIR, "token.json")
LOG_FILE = os.path.expanduser(
    os.environ.get("ZOI_LOG_FILE", os.path.join(STATE_DIR, "zoi_bot.log"))
)


def log(msg):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    try:
        os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
        with open(LOG_FILE, "a") as f:
            f.write(line + "\n")
    except OSError:
        pass


def req(method, path, token=None, body=None, timeout=30):
    """Return (status, parsed_json_or_text)."""
    url = API + path
    data = None
    headers = {"Accept": "application/json", "User-Agent": "zoi/1.1.1"}
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = "Bearer " + token
    r = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "ignore")
            status = resp.status
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "ignore")
        status = e.code
    except Exception as e:
        return 0, {"error": f"network: {e}"}
    try:
        return status, json.loads(raw)
    except json.JSONDecodeError:
        return status, raw


# ---------- auth ----------

def load_token():
    try:
        with open(TOKEN_FILE) as f:
            return json.load(f).get("sessionToken")
    except (OSError, json.JSONDecodeError):
        return None


def save_token(tok):
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(TOKEN_FILE, "w") as f:
        json.dump({"sessionToken": tok, "saved_at": int(time.time())}, f)
    os.chmod(TOKEN_FILE, 0o600)


def fetch_otp(newest_uid_min=0, wait=150):
    """Poll Gmail IMAP buat kode Zoi sign-in terbaru."""
    deadline = time.time() + wait
    while time.time() < deadline:
        try:
            m = imaplib.IMAP4_SSL(IMAP_HOST)
            m.login(IMAP_USER, IMAP_PASS)
            m.select("INBOX")
            typ, data = m.search(None, 'FROM', '"Zoi Network"')
            if typ == "OK" and data and data[0]:
                uids = data[0].split()
                for uid in reversed(uids[-4:]):
                    if int(uid) < newest_uid_min:
                        continue
                    typ, msg_data = m.fetch(uid, "(RFC822)")
                    if typ != "OK" or not msg_data or not msg_data[0]:
                        continue
                    msg = email.message_from_bytes(msg_data[0][1])
                    subj = msg.get("Subject", "")
                    body = ""
                    if msg.is_multipart():
                        for part in msg.walk():
                            if part.get_content_type() == "text/plain":
                                payload = part.get_payload(decode=True)
                                if payload:
                                    body += payload.decode("utf-8", "ignore")
                    else:
                        payload = msg.get_payload(decode=True)
                        if payload:
                            body = payload.decode("utf-8", "ignore")

                    # Kode bisa ada di subject ATAU body — cek dua-duanya,
                    # jangan skip email cuma karena subject gak memuat kode.
                    code = None
                    for src in (subj, body):
                        mh = re.search(r"\b(\d{6})\b", src)
                        if mh:
                            code = mh.group(1)
                            break
                    if not code:
                        continue
                    m.logout()
                    return code, int(uid)
            m.logout()
        except Exception as e:
            log(f"IMAP error: {e}")
        time.sleep(8)
    return None, 0


def login():
    """Full login: password -> OTP email -> sessionToken."""
    # catat UID terbaru supaya cuma baca email baru
    base_uid = 0
    try:
        m = imaplib.IMAP4_SSL(IMAP_HOST)
        m.login(IMAP_USER, IMAP_PASS)
        m.select("INBOX")
        typ, data = m.search(None, "ALL")
        if typ == "OK" and data and data[0]:
            base_uid = int(data[0].split()[-1])
        m.logout()
    except Exception as e:
        log(f"IMAP precheck error: {e}")

    status, body = req("POST", "/auth/sign-in",
                       body={"email": EMAIL, "password": PASSWORD, "deviceId": DEVICE_ID})
    log(f"sign-in -> {status} {body}")
    if status != 200 or not isinstance(body, dict):
        return None, f"sign-in gagal: {status} {body}"

    # device udah trusted: token langsung dikasih tanpa OTP
    if body.get("sessionToken"):
        save_token(body["sessionToken"])
        return body["sessionToken"], None

    if "challengeId" not in body:
        return None, f"sign-in gagal: {status} {body}"

    cid = body["challengeId"]
    otp, _ = fetch_otp(newest_uid_min=base_uid)
    if not otp:
        return None, "OTP tidak masuk dalam 150s"

    status, body = req("POST", "/auth/sign-in/verify-otp",
                       body={"challengeId": cid, "otp": otp})
    log(f"verify-otp -> {status} {body}")
    if status == 200 and isinstance(body, dict) and body.get("sessionToken"):
        save_token(body["sessionToken"])
        return body["sessionToken"], None
    return None, f"verify-otp gagal: {status} {body}"


def get_token(force=False):
    if not force:
        tok = load_token()
        if tok:
            status, _ = req("GET", "/auth/me", token=tok)
            if status == 200:
                return tok, None
    return login()


# ---------- actions ----------

def do_status(token, verbose=True):
    status, st = req("GET", "/sync/state", token=token)
    if status != 200:
        return status, st
    now = st["now"]
    nxt = st["sync"]["nextSyncAt"]
    left = max(0, (nxt - now) / 1000)
    if verbose:
        def f(ms):
            dt = datetime.fromtimestamp(ms / 1000, timezone.utc)
            return dt.astimezone(timezone(timedelta(hours=7))).strftime("%d %b %H:%M WIB")
        print(f"Balance      : {st['balance']} ZOI")
        print(f"Streak       : {st['streak']['days']} hari (x{st['streak']['multiplier']})")
        print(f"Total sync   : {st['sync']['totalSyncs']}")
        print(f"Sync terakhir: {f(st['sync']['lastSyncAt'])}")
        print(f"Sync berikut : {f(nxt)}  ({left/3600:.1f} jam lagi)")
        print(f"Bisa sync    : {st['sync']['canSync']}")
    return status, st


def do_sync(token, quiet=False):
    """Coba sync. Return (ok, pesan, state)."""
    status, st = req("GET", "/sync/state", token=token)
    if status != 200:
        return False, f"gagal baca state: {status} {st}", None

    sync = st.get("sync") or {}
    if not sync.get("canSync"):
        left = max(0, (sync.get("nextSyncAt", st.get("now", 0)) - st.get("now", 0)) / 1000)
        return False, f"belum waktunya ({left/3600:.1f} jam lagi)", st

    status, body = req("POST", "/sync", token=token, body={})
    if isinstance(body, dict):
        brief = {k: v for k, v in body.items() if k != "state"}
        log(f"POST /sync -> {status} {brief}")
    else:
        log(f"POST /sync -> {status} {body}")
    if not isinstance(body, dict):
        return False, f"sync balas non-JSON: {status} {str(body)[:120]}", st
    if status in (200, 201):
        # Server balas dua bentuk berbeda: 200 {'reward': N} dan
        # 201 {'earned': N, 'newBalance': N, 'state': {...}}. Tangani dua-duanya.
        got = (body.get("earned") or body.get("reward") or body.get("amount")
               or body.get("totalReward"))

        # Ambil state fresh setelah sync — bentuk 200 gak bawa balance sama sekali.
        bal = body.get("newBalance") or body.get("balance")
        streak_days = None
        next_txt = ""
        s2, st_fresh = req("GET", "/sync/state", token=token)
        if s2 == 200 and isinstance(st_fresh, dict):
            bal = st_fresh.get("balance", bal)
            sk = st_fresh.get("streak") or {}
            streak_days = sk.get("days")
            nxt_ms = (st_fresh.get("sync") or {}).get("nextSyncAt")
            if nxt_ms:
                nxt_dt = datetime.fromtimestamp(nxt_ms / 1000, timezone.utc)
                wib = nxt_dt.astimezone(timezone(timedelta(hours=7)))
                next_txt = wib.strftime("%H:%M WIB")
            st = st_fresh
        if streak_days is None:
            st2 = body.get("state") or {}
            if st2.get("streak"):
                streak_days = st2["streak"].get("days")

        msg = f"SYNC OK: +{got} ZOI" if got else "SYNC OK"
        if bal is not None:
            msg += f" | saldo {bal} ZOI"
        if streak_days is not None:
            msg += f" | streak {streak_days} hari"
        if next_txt:
            msg += f" | next {next_txt}"
        return True, msg, st
    if status == 409:
        return False, "window belum ready (409)", st
    return False, f"sync gagal: {status} {body}", st


def do_chests(token):
    """Klaim chest yang ready. Return list pesan.

    Chest cuma dicoba kalau SEMUA task-nya done — kalau belum, jangan
    tembak endpoint claim (server balas 409 terus dan bikin log kotor).
    """
    out = []
    status, body = req("GET", "/chests", token=token)
    if status != 200 or not isinstance(body, dict):
        return out
    for c in body.get("chests", []):
        if c.get("claimedAt"):
            continue
        if c.get("status") not in ("active", "ready", "claimable"):
            continue
        tasks = c.get("tasks") or []
        if tasks and not all(t.get("done") for t in tasks):
            continue  # belum selesai semua task, skip tanpa nembak claim
        cid = c.get("id")
        st2, rb = req("POST", f"/chests/{cid}/claim", token=token, body={})
        log(f"POST /chests/{cid}/claim -> {st2} {rb}")
        if st2 in (200, 201):
            out.append(f"CHEST '{cid}' OK: {rb.get('reward') or rb}")
        elif st2 == 409:
            continue
        else:
            out.append(f"CHEST '{cid}' gagal: {st2} {rb}")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--login", action="store_true")
    args = ap.parse_args()

    token, err = get_token(force=args.login)
    if not token:
        print(f"❌ Login gagal: {err}")
        return 1

    if args.status:
        status, st = do_status(token)
        if status != 200:
            print(f"❌ {status} {st}")
            return 1
        return 0

    # --once: cron mode, sync only. Chest task diurus manual oleh Nono.
    msgs = []
    ok, msg, st = do_sync(token)
    if ok:
        msgs.append(msg)
    elif "belum waktunya" not in msg:
        msgs.append(f"⚠️ {msg}")

    if msgs:
        print("\n".join(msgs))
    return 0


if __name__ == "__main__":
    sys.exit(main())
