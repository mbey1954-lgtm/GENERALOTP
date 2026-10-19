import os
import re
import json
import time
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
TIMEOUT = 5

# --- BELLEK İÇİ HAFIZA (Render ücretsiz planda kalıcı değil) ---
_seen_keys = set()
_last_data = None
_last_fetch_time = 0
CACHE_TTL = 3

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
        num = normalize_number(str(entry[1]))
        if len(num) < 7: return None
        return {"platform": str(entry[0]), "number": num, "otp": extract_otp(str(entry[2])), "message": str(entry[2])[:100], "time": format_time(entry[3]), "source": source, "masked": False}
    
    # Format 2: Xeron
    if isinstance(entry, dict) and "message" in entry and "range" in entry:
        return {"platform": str(entry.get("sid", "")), "number": normalize_number(str(entry.get("range", ""))), "otp": extract_otp(entry.get("message", "")), "message": str(entry.get("message", ""))[:100], "time": format_time(entry.get("time", "")), "source": source, "masked": True}
    
    # Format 3: Container/Client
    if isinstance(entry, dict):
        num = entry.get("numara") or entry.get("number") or entry.get("phone", "")
        if not num: return None
        otp = entry.get("otp") or entry.get("code") or extract_otp(entry.get("sms", ""))
        return {"platform": str(entry.get("platform", "")), "number": normalize_number(str(num)), "otp": otp, "message": str(entry.get("sms", ""))[:100], "time": format_time(entry.get("zaman", "")), "source": source, "masked": False}
    return None

def get_data():
    global _seen_keys, _last_data, _last_fetch_time
    
    now = time.time()
    if _last_data and (now - _last_fetch_time) < CACHE_TTL:
        return _last_data
    
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
            if p and p["otp"] and p["number"]:
                all_items.append(p)
    
    # Aynı numara+kodu tekrar etme
    new_items = []
    for item in all_items:
        key = f"{item['number']}_{item['otp']}"
        if key in _seen_keys: continue
        _seen_keys.add(key)
        new_items.append(item)
    
    # Hafıza sınırı
    if len(_seen_keys) > 5000:
        _seen_keys = set(list(_seen_keys)[-2500:])
    
    new_items.sort(key=lambda x: x["time"], reverse=True)
    _last_data = new_items
    _last_fetch_time = now
    return new_items

@app.route('/')
@app.route('/api/otp')
def api_otp():
    data = get_data()
    return jsonify({
        "meta": {"code": 200, "status": "ok", "total": len(data), "timestamp": datetime.now(timezone.utc).isoformat()},
        "data": data
    })

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
