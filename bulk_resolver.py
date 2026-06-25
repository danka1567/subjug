#!/usr/bin/env python3
"""Fast bulk embed resolver - extracts video_id/server_id/quality from a list of IDs"""
import argparse, gzip, json, os, re, urllib.error, urllib.parse, urllib.request, random
from dataclasses import dataclass, field, asdict
from typing import List

TIMEOUT = 15
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"

PROXIES = [
    ("31.59.20.176", 6754),
    ("31.56.127.193", 7684),
    ("45.38.107.97", 6014),
    ("38.154.203.95", 5863),
    ("198.105.121.200", 6462),
    ("64.137.96.74", 6641),
    ("198.23.243.226", 6361),
    ("38.154.185.97", 6370),
    ("142.111.67.146", 5611),
    ("191.96.254.138", 6185),
]
PROXY_USER = "glsbcfvl"
PROXY_PASS = "336gxb0or4n9"

EMBED_ORIGIN = "https://multiembed.mov"
STREAM_ORIGIN = "https://streamingnow.mov"
STREAM_REFERER = "https://streamingnow.mov/response.php"

TOKEN_RE = re.compile(r'[?&]play=([^&"\'<>]+)', re.I)
LOAD_RE = re.compile(r"""load_sources\(['"]([^'"]+)['"]\)""")
LI_RE = re.compile(r'<li\b([^>]*\bdata-id=[^>]*)>', re.I | re.S)
QUAL_RE = re.compile(r"""<span\b[^>]*class=['"][^'"]*\bquality\b[^'"]*['"][^>]*>(.*?)</span>""", re.I | re.S)
TAG_RE = re.compile(r'<(?:script|style)\b.*?</(?:script|style)>|<[^>]+>', re.I | re.S)

proxy_stats = {f"{h}:{pt}": {"attempts": 0, "success": 0, "fail": 0, "last_reason": ""} for h, pt in PROXIES}

@dataclass
class Src:
    video_id: str
    server_id: str
    quality: str = ""

@dataclass
class R:
    input_url: str
    ok: bool = False
    status: str = ""
    sources: List[Src] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    proxy_used: str = ""
    proxy_log: List[str] = field(default_factory=list)

    def j(self):
        return {
            "input_url": self.input_url, 
            "ok": self.ok, 
            "status": self.status,
            "sources": [asdict(s) for s in self.sources], 
            "errors": self.errors,
            "proxy_used": self.proxy_used, 
            "proxy_log": self.proxy_log
        }

def get_origin(url):
    p = urllib.parse.urlsplit(url)
    return f"{p.scheme}://{p.netloc}"

def base_headers(url, referer=None, mode="navigate"):
    origin = get_origin(url)
    h = {
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document" if mode == "navigate" else "empty",
        "Sec-Fetch-Mode": mode,
        "Sec-Fetch-Site": "none" if not referer else "same-origin",
        "Sec-Fetch-User": "?1" if mode == "navigate" else None,
        "DNT": "1",
        "Cache-Control": "max-age=0",
        "Origin": origin,
    }
    if referer: h["Referer"] = referer
    return {k: v for k, v in h.items() if v is not None}

def post_headers(url, referer=None):
    origin = get_origin(url)
    h = {
        "User-Agent": UA,
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate",
        "Content-Type": "application/x-www-form-urlencoded",
        "X-Requested-With": "XMLHttpRequest",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Site": "same-origin",
        "Origin": origin,
        "Connection": "keep-alive",
        "DNT": "1",
    }
    if referer: h["Referer"] = referer
    return h

def decode_body(raw, headers):
    ct = headers.get("Content-Encoding", "")
    if "gzip" in ct:
        try: raw = gzip.decompress(raw)
        except Exception: pass
    return raw.decode("utf-8", "replace")

def make_opener(host, port):
    proxy_url = f"http://{PROXY_USER}:{PROXY_PASS}@{host}:{port}"
    ph = urllib.request.ProxyHandler({"http": proxy_url, "https": proxy_url})
    opener = urllib.request.build_opener(ph, urllib.request.HTTPRedirectHandler())
    opener.addheaders = []
    return opener

def do_request(req_factory, log, label):
    attempts = list(PROXIES); random.shuffle(attempts)
    last_err = None
    for host, port in attempts:
        key = f"{host}:{port}"
        proxy_stats[key]["attempts"] += 1
        opener = make_opener(host, port)
        req = req_factory()
        try:
            with opener.open(req, timeout=TIMEOUT) as resp:
                raw = resp.read()
                body = decode_body(raw, dict(resp.headers.items()))
                proxy_stats[key]["success"] += 1
                proxy_stats[key]["last_reason"] = "ok"
                msg = f"[{label} OK] proxy={key} url={req.full_url}"
                log.append(msg); print(msg)
                return resp.status, resp.geturl(), dict(resp.headers.items()), body, key
        except urllib.error.HTTPError as e:
            reason = f"HTTP{e.code}"
            proxy_stats[key]["fail"] += 1
            proxy_stats[key]["last_reason"] = reason
            msg = f"[{label} FAIL] proxy={key} reason={reason} url={req.full_url}"
            log.append(msg); print(msg)
            body = e.read().decode("utf-8", "replace")
            last_err = (e.code, req.full_url, dict(e.headers.items()), body, key)
            if e.code in (403, 429, 503): continue
            return last_err
        except Exception as e:
            reason = f"{type(e).__name__}:{e}"
            proxy_stats[key]["fail"] += 1
            proxy_stats[key]["last_reason"] = reason
            msg = f"[{label} FAIL] proxy={key} reason={reason}"
            log.append(msg); print(msg)
            last_err = (0, req.full_url, {}, reason, key)
            continue
    return last_err or (0, "", {}, "all proxies failed", "none")

def g(u, referer=None, log=None):
    if log is None: log = []
    hdrs = base_headers(u, referer)
    def factory(): return urllib.request.Request(u, headers=hdrs)
    return do_request(factory, log, "GET")

def p(u, d, referer=None, log=None):
    if log is None: log = []
    hdrs = post_headers(u, referer)
    def factory(): return urllib.request.Request(u, data=urllib.parse.urlencode(d).encode(), headers=hdrs, method="POST")
    return do_request(factory, log, "POST")

def tok(s):
    m = TOKEN_RE.search(s)
    if m: return urllib.parse.unquote(m.group(1))
    m = LOAD_RE.search(s)
    if m: return m.group(1)
    return None

def extract(html_body):
    s = []
    ms = list(LI_RE.finditer(html_body))
    for i, m in enumerate(ms):
        a = m.group(1)
        vi = re.search(r"""data-id\s*=\s*['"](.*?)['"]""", a, re.I)
        si = re.search(r"""data-server\s*=\s*['"](.*?)['"]""", a, re.I)
        if not vi or not si: continue
        e = ms[i+1].start() if i+1 < len(ms) else html_body.find("</ul>", m.end())
        if e < 0: e = min(len(html_body), m.end()+200)
        f = html_body[m.end():e]
        qm = QUAL_RE.search(f)
        q = TAG_RE.sub(" ", qm.group(1)).strip() if qm else ""
        s.append(Src(vi.group(1), si.group(1), q))
    return s

def resolve(u):
    r = R(u)
    log = r.proxy_log
    try:
        s, fu, hd, bd, px = g(u, referer=EMBED_ORIGIN+"/", log=log)
        if s >= 400:
            r.errors.append(f"HTTP{s} body={bd[:200]}")
            r.status = "http_error"
            return r
        r.proxy_used = px

        pu = urllib.parse.urljoin(u, hd.get("Location") or hd.get("location") or fu)
        tk = tok(pu) or tok(bd)
        if not tk:
            _, _, _, pg, px2 = g(pu, referer=EMBED_ORIGIN+"/", log=log)
            tk = tok(pg)
            if px2 != "none": r.proxy_used = px2

        if not tk:
            r.errors.append("no token")
            r.status = "no_token"
            return r

        ru = urllib.parse.urljoin(pu, "/response.php")
        _, _, _, rh, px3 = p(ru, {"token": tk}, referer=STREAM_REFERER, log=log)
        if px3 != "none": r.proxy_used = px3

        r.sources = extract(rh)
        r.ok = bool(r.sources)
        r.status = "ok" if r.ok else "no_sources"
    except Exception as e:
        import traceback
        r.status = "error"
        r.errors.append(f"{type(e).__name__}:{e}\n{traceback.format_exc()}")
    return r

def proxy_status_report():
    rows = []
    for key, st in proxy_stats.items():
        if st["attempts"] == 0: continue
        rows.append({
            "proxy": key, "success": st["success"], "fail": st["fail"],
            "attempts": st["attempts"], "rate": f"{st['success']}/{st['attempts']}",
            "last": st["last_reason"]
        })
    return rows

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default="tmdb movis ids.txt", help="Text file containing TMDB IDs")
    ap.add_argument("--output", default="results.json", help="Output JSON file path")
    a = ap.parse_args()

    if not os.path.exists(a.file):
        print(f"Error: {a.file} not found!")
        return 1

    with open(a.file, "r", encoding="utf-8") as f:
        lines = f.readlines()

    tmdb_ids = []
    for line in lines:
        # Clean up lines like "100" to just "100"
        cleaned = line.split("]")[-1].strip()
        if cleaned.isdigit():
            tmdb_ids.append(cleaned)

    print(f"Found {len(tmdb_ids)} valid TMDB IDs.")
    
    all_results = []
    for tmdb_id in tmdb_ids:
        url = f"https://multiembed.mov/?video_id={tmdb_id}&tmdb=1"
        print(f"\n--- Resolving: {url} ---")
        result = resolve(url)
        all_results.append(result.j())

    final_output = {
        "total_processed": len(tmdb_ids),
        "successful": sum(1 for r in all_results if r["ok"]),
        "results": all_results,
        "proxy_stats": proxy_status_report()
    }

    with open(a.output, "w", encoding="utf-8") as f:
        json.dump(final_output, f, indent=2, ensure_ascii=False)
        
    print(f"\n✅ Processing complete. Saved to {a.output}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
