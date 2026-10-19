import os
import re
import json
import time
import threading
import urllib.request
import gzip
from flask import Flask, jsonify
from datetime import datetime, timezone

app = Flask(__name__)

# --- KONFİG ---
APIS = {
    "container": "http://194.238.28.236:8000/container",
    "client": "http://62.169.28.71/client.php?token=pbqDNx2Mt1Q2ggnrvBz2tEM-aSsx099a&limit=100",
    "viewstats": "http://147.135.212.197/crapi/st/viewstats?token=RVdWRElBUzRGcW9WeneNcmd2cGV9ZJd8e29PVlyPcFxeamxSgWVXfw==&records=200",
    "xeron_console": "http://xeron.network:8080/XRSDDD5366/@public/api/console",
    "xeron_otp": "http://xeron.network:8080/XRSDDD5366/@public/api/success-otp",
}
CACHE_TTL = 5
TIMEOUT = 5

# Bellek içi cache ve seen (Render'da dosya kalıcı olmadığı için RAM'de tutuyoruz)
_cache = {"data": None, "time": 0}
_seen = set()
_seen_lock = threading.Lock()

def normalize_number(n):
    return re.sub(r'\D', '', str(n or ''))

def extract_otp(sms):
    if not sms: return ""
    for p in [r'\b(\d{3}[-\s]\d{3})\b', r'\b(\d{6,8})\b', r'\b(\d{4,5})\b']:
        m = re.search(p, sms)
        if m: return m.group(1).replace(" ", "").replace("-", "")
    return ""

def format_time(ts):
    try:
        ts = int(ts)
        if ts > 9999999999: ts /= 1000
        return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    except: return str(ts)

def fetch_url(url):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept-Encoding": "gzip"})
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            raw = resp.read()
            if resp.headers.get("Content-Encoding", "").lower() == "gzip":
                raw = gzip.decompress(raw)
        return json.loads(raw.decode("utf-8", errors="ignore"))
    except: return None

def parse_entry(entry, source):
    # Format 1: ViewStats list-of-list
    if isinstance(entry, list) and len(entry) >= 4:
        platform, num_raw, msg, t = str(entry[0]), str(entry[1]), str(entry[2]), str(entry[3])
        num = normalize_number(num_raw)
        if len(num) < 7: return None
        return {"platform": platform, "number": num, "otp": extract_otp(msg), "message": msg, "time": format_time(t), "source": source, "masked": False}
    
    # Format 2: Xeron
    if isinstance(entry, dict) and "message" in entry and "range" in entry:
        num_raw = entry.get("range", "")
        return {"platform": entry.get("sid", ""), "number": normalize_number(num_raw), "otp": extract_otp(entry.get("message", "")), "message": entry.get("message", ""), "time": format_time(entry.get("time", "")), "source": source, "masked": True}
    
    # Format 3: Container/Client
    if isinstance(entry, dict):
        num = entry.get("numara") or entry.get("number") or entry.get("phone", "")
        if not num: return None
        otp = entry.get("otp") or entry.get("code") or extract_otp(entry.get("sms", ""))
        return {"platform": entry.get("platform", ""), "number": normalize_number(num), "otp": otp, "message": entry.get("sms", ""), "time": format_time(entry.get("zaman", "")), "source": source, "masked": False}
    return None

def get_api_data():
    now = time.time()
    if _cache["data"] and (now - _cache["time"]) < CACHE_TTL:
        return _cache["data"]

    all_items = []
    for source, url in APIS.items():
        data = fetch_url(url)
        if not data: continue
        items = []
        if isinstance(data, dict):
            if "data" in data and isinstance(data["data"], dict):
                for k in ("hits", "result", "items", "list"):
                    if k in data["data"] and isinstance(data["data"][k], list):
                        items = data["data"][k]; break
            if not items:
                for k in ("data", "result", "items", "list"):
                    if k in data and isinstance(data[k], list):
                        items = data[k]; break
        elif isinstance(data, list):
            items = data
        
        for entry in items:
            p = parse_entry(entry, source)
            if p: all_items.append(p)

    # Tekilleştirme (Aynı numara + kod)
    unique = []
    with _seen_lock:
        for item in all_items:
            if not item["otp"] or not item["number"]: continue
            key = f"{item['number']}_{item['otp']}"
            if key in _seen: continue
            _seen.add(key)
            unique.append(item)
        # Seen setini sınırla (bellek şişmesin)
        if len(_seen) > 5000: _seen.clear()

    unique.sort(key=lambda x: x["time"], reverse=True)
    _cache["data"] = unique
    _cache["time"] = now
    return unique

@app.route('/')
@app.route('/api/otp')
def api_otp():
    data = get_api_data()
    return jsonify({"meta": {"code": 200, "status": "ok", "total": len(data), "timestamp": datetime.now(timezone.utc).isoformat()}, "data": data})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
