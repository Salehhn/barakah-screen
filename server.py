#!/usr/bin/env python3
"""Halal Stock Screener — self-hosted US equity screen using SEC EDGAR + Yahoo prices."""

from __future__ import annotations

import json
import os
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"
CACHE = ROOT / "cache"
CACHE.mkdir(exist_ok=True)
UNIVERSE = STATIC / "universe.json"

def _drive_token():
    raw = os.environ.get("GDRIVE_SA_JSON") or ""
    if not raw or not os.environ.get("GDRIVE_FOLDER_ID"):
        return None
    try:
        from google.oauth2 import service_account
        from google.auth.transport.requests import Request
        info = json.loads(raw)
        creds = service_account.Credentials.from_service_account_info(
            info, scopes=["https://www.googleapis.com/auth/drive"]
        )
        creds.refresh(Request())
        return creds.token
    except Exception as e:
        print("Drive auth failed:", e)
        return None


def _drive_call(url, token, data=None, method=None, content_type=None):
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", "Bearer " + token)
    if content_type:
        req.add_header("Content-Type", content_type)
    with urllib.request.urlopen(req, timeout=30) as res:
        return res.read()


def drive_load(name):
    token = _drive_token()
    folder = os.environ.get("GDRIVE_FOLDER_ID")
    if not token:
        return None
    q = urllib.parse.quote(f"name='{name}' and '{folder}' in parents and trashed=false")
    listed = json.loads(_drive_call(
        f"https://www.googleapis.com/drive/v3/files?q={q}&fields=files(id,name)", token
    ))
    files = listed.get("files") or []
    if not files:
        return None
    body = _drive_call(
        f"https://www.googleapis.com/drive/v3/files/{files[0]['id']}?alt=media", token
    )
    return json.loads(body.decode())


def drive_save(name, obj):
    token = _drive_token()
    folder = os.environ.get("GDRIVE_FOLDER_ID")
    if not token:
        return False
    payload = json.dumps(obj).encode()
    q = urllib.parse.quote(f"name='{name}' and '{folder}' in parents and trashed=false")
    listed = json.loads(_drive_call(
        f"https://www.googleapis.com/drive/v3/files?q={q}&fields=files(id,name)", token
    ))
    files = listed.get("files") or []
    if files:
        _drive_call(
            f"https://www.googleapis.com/upload/drive/v3/files/{files[0]['id']}?uploadType=media",
            token, data=payload, method="PATCH", content_type="application/json",
        )
    else:
        meta = json.dumps({"name": name, "parents": [folder]}).encode()
        boundary = "barakahbound"
        body = (
            f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n".encode()
            + meta + b"\r\n"
            + f"--{boundary}\r\nContent-Type: application/json\r\n\r\n".encode()
            + payload + b"\r\n"
            + f"--{boundary}--".encode()
        )
        _drive_call(
            "https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart",
            token, data=body, method="POST",
            content_type="multipart/related; boundary=" + boundary,
        )
    return True
PAPER = CACHE / "paper.json"
FX_PAPER = CACHE / "fx-paper.json"
SHARES = 100  # paper size per theoretical open

FX_BOOK = [
    {"symbol": "XAUUSD=X", "tv": "XAUUSD", "name": "Gold", "group": "Metal", "pip": 0.01},
    {"symbol": "XAGUSD=X", "tv": "XAGUSD", "name": "Silver", "group": "Metal", "pip": 0.01},
    {"symbol": "EURJPY=X", "tv": "EURJPY", "name": "EUR/JPY", "group": "Forex", "pip": 0.01},
    {"symbol": "PL=F", "tv": "XPTUSD", "name": "Platinum", "group": "Metal", "pip": 0.10},
    {"symbol": "CL=F", "tv": "USOIL", "name": "WTI Oil", "group": "Oil", "pip": 0.01},
    {"symbol": "BZ=F", "tv": "UKOIL", "name": "Brent Oil", "group": "Oil", "pip": 0.01},
    {"symbol": "EURUSD=X", "tv": "EURUSD", "name": "EUR/USD", "group": "Forex", "pip": 0.0001},
    {"symbol": "GBPUSD=X", "tv": "GBPUSD", "name": "GBP/USD", "group": "Forex", "pip": 0.0001},
    {"symbol": "USDJPY=X", "tv": "USDJPY", "name": "USD/JPY", "group": "Forex", "pip": 0.01},
    {"symbol": "AUDUSD=X", "tv": "AUDUSD", "name": "AUD/USD", "group": "Forex", "pip": 0.0001},
    {"symbol": "USDCAD=X", "tv": "USDCAD", "name": "USD/CAD", "group": "Forex", "pip": 0.0001},
    {"symbol": "USDCHF=X", "tv": "USDCHF", "name": "USD/CHF", "group": "Forex", "pip": 0.0001},
    {"symbol": "BTC-USD", "tv": "BTCUSD", "name": "Bitcoin", "group": "Coin", "pip": 1.0},
    {"symbol": "ETH-USD", "tv": "ETHUSD", "name": "Ethereum", "group": "Coin", "pip": 0.1},
    {"symbol": "SOL-USD", "tv": "SOLUSD", "name": "Solana", "group": "Coin", "pip": 0.01},
    {"symbol": "XRP-USD", "tv": "XRPUSD", "name": "XRP", "group": "Coin", "pip": 0.0001},
]
SPUS_CSV = "https://www.sp-funds.com/wp-content/uploads/data/TidalFG_Holdings_SPUS.csv"

UA = "BarakahScreen/1.0 (self-hosted educational screener; research@example.com)"
SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SEC_FACTS = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
SEC_SUBS = "https://data.sec.gov/submissions/CIK{cik}.json"
YAHOO_CHART = "https://query2.finance.yahoo.com/v8/finance/chart/{sym}?interval=1d&range=5d"

_lock = threading.Lock()
_tickers = None  # ticker -> {cik, title}
_etf_refreshing = False

WATCHLIST = [
    "AAPL", "MSFT", "NVDA", "GOOGL", "META", "AMZN", "TSLA", "AVGO", "AMD", "NFLX",
    "ORCL", "ADBE", "CRM", "INTU", "NOW", "QCOM", "TXN", "AMAT", "KLAC", "LRCX",
    "COST", "WMT", "HD", "NKE", "SBUX", "KO", "PEP", "PG", "JNJ", "UNH",
    "ABT", "TMO", "LLY", "MRK", "PFE", "ISRG", "XOM", "CVX", "CAT", "DE",
    "HON", "GE", "BA", "LMT", "RTX", "V", "MA", "JPM", "BAC", "GS",
    "BRK-B", "PLTR", "SMCI", "PANW", "CRWD", "UBER", "ABNB", "DIS", "CMCSA", "T",
    "INTC", "F", "GM", "SNAP", "SOFI", "NU", "VALE", "GOLD", "NEM", "FCX",
    "NIO", "RIVN", "LCID", "WBD", "PARA", "SIRI", "NOK", "ERIC", "CSCO", "IBM",
    "MU", "HPQ", "HPE", "DAL", "UAL", "AAL", "CCL", "NCLH", "RCL", "MRO",
]

# SIC prefixes / exact codes commonly treated as non-permissible primary businesses
HARAM_SIC = {
    "2082", "2084", "2085",  # malt / wine / distilled
    "2111", "2121", "2131", "2141",  # tobacco
    "6021", "6022", "6029", "6035", "6036", "6061", "6062", "6099",
    "6111", "6141", "6153", "6159", "6162", "6163", "6211", "6221", "6282",
    "6311", "6321", "6324", "6331", "6351", "6361", "6371", "6399", "6411",
    "7011",  # hotels (MSCI often excludes)
    "7993", "7999",  # coin-op amuse / amusement & recreation
    "7812", "7832", "7833",  # motion pictures / theaters (some standards)
    "3482", "3483", "3484", "3489", "3761", "3764", "3769",  # ordnance / missiles
}

HARAM_KEYWORDS = [
    "bank", "bancorp", "banking", "savings", "thrift", "insurance", "life insurance",
    "property-casualty", "reinsurance", "broker-dealer", "investment banking",
    "tobacco", "cigarette", "cigar", "alcohol", "brewery", "brewer", "distiller",
    "winery", "wine", "beer", "spirits", "casino", "gambling", "gaming",
    "lottery", "adult entertainment", "pornograph", "weapons", "ammunition",
    "ordnance", "missile", "defense contractor", "firearm",
]

QUESTIONABLE_KEYWORDS = [
    "hotel", "motel", "resort", "cinema", "movie", "music", "entertainment",
    "restaurant", "media", "broadcast", "streaming", "airline", "credit card",
    "payments", "fintech", "mortgage",
]

STANDARDS = {
    "AAOIFI": {
        "label": "AAOIFI (Std. 21 style)",
        "debt_max": 0.30, "cash_max": 0.30, "recv_max": None,
        "impure_max": 0.05, "denom": "market_cap",
    },
    "SP": {
        "label": "S&P Shariah style",
        "debt_max": 0.33, "cash_max": 0.33, "recv_max": 0.49,
        "impure_max": 0.05, "denom": "market_cap", "recv_denom": "market_cap",
    },
    "DJIM": {
        "label": "Dow Jones Islamic style",
        "debt_max": 0.33, "cash_max": 0.33, "recv_max": 0.33,
        "impure_max": 0.05, "denom": "market_cap",
    },
    "FTSE": {
        "label": "FTSE Shariah style",
        "debt_max": 0.3333, "cash_max": 0.3333, "recv_max": 0.50,
        "impure_max": 0.05, "denom": "assets",
    },
    "MSCI": {
        "label": "MSCI Islamic style",
        "debt_max": 0.3333, "cash_max": 0.3333, "recv_max": 0.3333,
        "impure_max": 0.05, "denom": "assets",
    },
    "RAJHI": {
        "label": "Al Rajhi style (AAOIFI-like)",
        "debt_max": 0.30, "cash_max": 0.30, "recv_max": None,
        "impure_max": 0.05, "denom": "market_cap",
    },
    "ALINMA": {
        "label": "Alinma style",
        "debt_max": 0.33, "cash_max": 0.33, "recv_max": None,
        "impure_max": 0.05, "denom": "market_cap",
    },
    "BILAD": {
        "label": "Bank Albilad style",
        "debt_max": 0.30, "cash_max": 0.30, "recv_max": 0.49,
        "impure_max": 0.05, "denom": "market_cap", "recv_denom": "market_cap",
    },
}


def http_get(url: str, timeout: int = 25) -> bytes:
    last = None
    for attempt in range(4):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Encoding": "identity"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except Exception as e:
            last = e
            if "429" in str(e) or "403" in str(e):
                time.sleep(1.5 * (attempt + 1))
                continue
            raise
    raise last


def cache_path(name: str) -> Path:
    safe = "".join(c if c.isalnum() or c in "._-" else "_" for c in name)
    return CACHE / safe


def cached_json(name: str, fetcher, ttl: int):
    path = cache_path(name)
    if path.exists() and time.time() - path.stat().st_mtime < ttl:
        return json.loads(path.read_text())
    data = fetcher()
    path.write_text(json.dumps(data))
    return data


def load_tickers():
    global _tickers
    with _lock:
        if _tickers is not None:
            return _tickers

        def fetch():
            raw = json.loads(http_get(SEC_TICKERS_URL))
            out = {}
            for row in raw.values():
                t = str(row.get("ticker", "")).upper().replace(".", "-")
                if t:
                    out[t] = {"cik": str(row["cik_str"]).zfill(10), "title": row.get("title", "")}
            return out

        _tickers = cached_json("company_tickers.json", fetch, ttl=24 * 3600)
        return _tickers


def latest_instant(facts_gaap: dict, tags: list[str], prefer_forms=("10-Q", "10-K", "20-F", "40-F")):
    best = None
    for tag in tags:
        node = facts_gaap.get(tag)
        if not node:
            continue
        units = node.get("units", {})
        series = units.get("USD") or units.get("USD/shares")
        if not series:
            continue
        for item in series:
            if "start" in item:
                continue  # duration, not instant
            form = item.get("form", "")
            if form not in prefer_forms and form not in ("10-Q/A", "10-K/A"):
                continue
            end = item.get("end")
            val = item.get("val")
            if end is None or val is None:
                continue
            rec = {"tag": tag, "end": end, "val": float(val), "form": form, "fy": item.get("fy"), "fp": item.get("fp")}
            if best is None or rec["end"] > best["end"] or (rec["end"] == best["end"] and rec["form"] == "10-K"):
                best = rec
    return best


def latest_duration(facts_gaap: dict, tags: list[str], prefer_fy=True):
    """Prefer latest full-year, else latest quarterly duration."""
    fy_best = None
    q_best = None
    for tag in tags:
        node = facts_gaap.get(tag)
        if not node:
            continue
        series = node.get("units", {}).get("USD")
        if not series:
            continue
        for item in series:
            if "start" not in item:
                continue
            form = item.get("form", "")
            if form not in ("10-Q", "10-K", "20-F", "40-F", "10-Q/A", "10-K/A"):
                continue
            rec = {
                "tag": tag,
                "start": item.get("start"),
                "end": item.get("end"),
                "val": float(item["val"]),
                "form": form,
                "fy": item.get("fy"),
                "fp": item.get("fp"),
            }
            fp = item.get("fp")
            if fp == "FY" or form in ("10-K", "20-F"):
                if fy_best is None or rec["end"] > fy_best["end"]:
                    fy_best = rec
            else:
                if q_best is None or rec["end"] > q_best["end"]:
                    q_best = rec
    if prefer_fy and fy_best:
        return fy_best
    return q_best or fy_best


def sum_debt(gaap: dict):
    """Best-effort interest-bearing debt from common US-GAAP tags."""
    long_t = latest_instant(gaap, [
        "LongTermDebt",
        "LongTermDebtNoncurrent",
        "LongTermDebtAndCapitalLeaseObligations",
        "LongTermDebtNoncurrentAndCapitalLeaseObligations",
        "DebtInstrumentCarryingAmount",
    ])
    current = latest_instant(gaap, [
        "LongTermDebtCurrent",
        "DebtCurrent",
        "ShortTermBorrowings",
        "CommercialPaper",
        "CurrentPortionOfLongTermDebt",
        "LongTermDebtAndCapitalLeaseObligationsCurrent",
    ])
    total_tag = latest_instant(gaap, [
        "LongTermDebtAndCapitalLeaseObligations",
        "DebtAndCapitalLeaseObligations",
    ])
    pieces = []
    asof = None
    total = 0.0
    used = []
    if long_t:
        total += long_t["val"]
        used.append(long_t["tag"])
        asof = long_t["end"]
    if current:
        # avoid double count if same tag family already included
        if current["tag"] not in used:
            total += current["val"]
            used.append(current["tag"])
            asof = max(filter(None, [asof, current["end"]]))
    if total == 0 and total_tag:
        total = total_tag["val"]
        used = [total_tag["tag"]]
        asof = total_tag["end"]
    return {"val": total if used else None, "asof": asof, "tags": used}


def classify_business(sic: str | None, sic_desc: str, title: str):
    text = f"{sic_desc} {title}".lower()
    sic = (sic or "").strip()
    reasons = []
    status = "pass"

    if sic and (sic in HARAM_SIC or sic[:2] in {"60", "61", "62", "63", "64"}):
        status = "fail"
        reasons.append(f"SIC {sic} ({sic_desc or 'financials'}) is typically a prohibited primary activity.")
    for kw in HARAM_KEYWORDS:
        if kw in text:
            status = "fail"
            reasons.append(f"Name/industry contains “{kw}”.")
            break
    if status == "pass":
        for kw in QUESTIONABLE_KEYWORDS:
            if kw in text:
                status = "review"
                reasons.append(f"Mixed/questionable activity keyword: “{kw}”. Scholars differ — check revenue mix.")
                break
    if not reasons:
        reasons.append("Primary SIC/industry is not on the automated prohibited list.")
    return {"status": status, "sic": sic, "sic_description": sic_desc, "reasons": reasons}


def http_get_ua(url: str, ua: str, timeout: int = 25) -> bytes:
    last = None
    for attempt in range(4):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": ua, "Accept-Encoding": "identity"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except Exception as e:
            last = e
            if "429" in str(e):
                time.sleep(1.2 * (attempt + 1))
                continue
            raise
    raise last


def yahoo_price(symbol: str, ttl: int = 900):
    def fetch():
        # Yahoo is picky about UA + rate limits; try a browser UA then Twelve Data demo.
        last_err = None
        try:
            raw = json.loads(http_get_ua(
                YAHOO_CHART.format(sym=urllib.parse.quote(symbol)),
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            ))
            res = raw.get("chart", {}).get("result") or [None]
            if res[0]:
                meta = res[0]["meta"]
                return {
                    "price": meta.get("regularMarketPrice"),
                    "name": meta.get("longName") or meta.get("shortName") or symbol,
                    "currency": meta.get("currency"),
                    "exchange": meta.get("fullExchangeName") or meta.get("exchangeName"),
                    "change_pct": meta.get("regularMarketChangePercent"),
                }
        except Exception as e:
            last_err = e
        try:
            raw = json.loads(http_get_ua(
                f"https://api.twelvedata.com/quote?symbol={urllib.parse.quote(symbol)}&apikey=demo",
                UA,
            ))
            if raw.get("close") or raw.get("price"):
                px = float(raw.get("close") or raw.get("price"))
                prev = float(raw["previous_close"]) if raw.get("previous_close") else None
                chg = ((px - prev) / prev * 100) if prev else None
                return {
                    "price": px,
                    "name": raw.get("name") or symbol,
                    "currency": raw.get("currency") or "USD",
                    "exchange": raw.get("exchange"),
                    "change_pct": chg,
                }
        except Exception as e:
            last_err = e
        raise RuntimeError(f"price feed failed: {last_err}")

    try:
        return cached_json(f"yahoo_{symbol}.json", fetch, ttl=ttl)
    except Exception as e:
        return {"error": str(e), "price": None, "name": symbol}


def sma(vals, n):
    vals = [v for v in vals if v is not None]
    if len(vals) < n:
        return None
    return sum(vals[-n:]) / n


def rsi(closes, n=14):
    c = [v for v in closes if v is not None]
    if len(c) < n + 1:
        return None
    gains = losses = 0.0
    for i in range(-n, 0):
        d = c[i] - c[i - 1]
        if d >= 0:
            gains += d
        else:
            losses -= d
    if losses == 0:
        return 100.0
    rs = (gains / n) / (losses / n)
    return 100 - (100 / (1 + rs))


TF_MAP = {
    "1d": ("1d", "6mo", 280),
    "5m": ("5m", "5d", 45),
    "1m": ("1m", "1d", 40),
}


def fetch_ohlc(symbol: str, tf: str = "1d"):
    interval, span, ttl = TF_MAP.get(tf, TF_MAP["1d"])

    def fetch():
        url = (
            "https://query2.finance.yahoo.com/v8/finance/chart/"
            + urllib.parse.quote(symbol)
            + f"?interval={interval}&range={span}"
        )
        raw = json.loads(http_get_ua(
            url,
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        ))
        res = (raw.get("chart") or {}).get("result") or [None]
        if not res[0]:
            raise RuntimeError("no chart")
        q = (res[0].get("indicators") or {}).get("quote") or [{}]
        q = q[0]
        return {
            "close": q.get("close") or [],
            "high": q.get("high") or [],
            "low": q.get("low") or [],
            "volume": q.get("volume") or [],
            "open": q.get("open") or [],
        }

    try:
        return cached_json(f"ohlc_{tf}_{symbol}.json", fetch, ttl=ttl)
    except Exception as e:
        return {"error": str(e)}


def load_paper():
    remote = drive_load("stock-trades.json")
    if isinstance(remote, dict):
        return remote
    path = PAPER if PAPER.exists() else (STATIC / "paper.json")
    if not path.exists():
        return {"trades": [], "open": None}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"trades": [], "open": None}


def save_paper(data):
    text = json.dumps(data, indent=2)
    PAPER.write_text(text, encoding="utf-8")
    try:
        (STATIC / "paper.json").write_text(text, encoding="utf-8")
    except Exception:
        pass
    try:
        drive_save("stock-trades.json", data)
    except Exception as e:
        print("Drive stock save failed:", e)


def et_now():
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("America/New_York"))
    except Exception:
        return datetime.now(timezone.utc) - __import__("datetime").timedelta(hours=4)


def paper_cum(trades):
    return round(sum(t.get("pnl") or 0 for t in trades), 2)


def update_paper_with_top(top, market):
    """If tape healthy near the US open and no trade today, paper-buy #1. Else mark TP/EOD."""
    book = load_paper()
    now = et_now()
    today = now.strftime("%Y-%m-%d")
    hour, minute = now.hour, now.minute
    buyers = (market or {}).get("buyers") or 0
    pos = book.get("open")

    if pos:
        q = yahoo_price(pos["symbol"])
        last = q.get("price")
        if last is not None:
            pos["last"] = last
            tp = pos.get("tp1")
            sl = pos.get("stop")
            hit = None
            if tp and last >= tp:
                hit = "TP1"
            elif sl and last <= sl:
                hit = "SL"
            elif hour >= 16:
                hit = "EOD"
            if hit:
                pnl = round((last - pos["open_price"]) * pos.get("shares", SHARES), 2)
                book["trades"].append({
                    "symbol": pos["symbol"],
                    "date": pos["date"],
                    "open_price": pos["open_price"],
                    "close_price": last,
                    "exit": hit,
                    "pnl": pnl,
                    "shares": pos.get("shares", SHARES),
                })
                book["open"] = None
            else:
                book["open"] = pos
        save_paper(book)
        book["cumulative"] = paper_cum(book["trades"])
        return book

    already = any(t.get("date") == today for t in book.get("trades") or [])
    weekday = now.weekday() < 5
    mins = hour * 60 + minute
    in_open_window = weekday and (9 * 60 + 28) <= mins <= (15 * 60 + 55)
    book["server_et"] = now.strftime("%Y-%m-%d %H:%M ET")
    if already or not in_open_window or buyers < 60 or not top:
        book["cumulative"] = paper_cum(book.get("trades") or [])
        if already:
            why = "already paper-traded today"
        elif not weekday:
            why = "US market closed (weekend)"
        elif not in_open_window:
            why = f"outside cash hours — server clock {book['server_et']} (need 09:28–15:55 ET)"
        elif buyers < 60:
            why = f"tape not healthy (buyers {buyers} < 60)"
        else:
            why = "no top name"
        book["skipped"] = why
        return book

    idea = top[0]
    px = idea.get("price") or ((idea.get("levels") or {}).get("entry"))
    if not px:
        book["cumulative"] = paper_cum(book.get("trades") or [])
        book["skipped"] = "no price on top name"
        return book
    lv = idea.get("levels") or {}
    book["open"] = {
        "symbol": idea.get("symbol"),
        "date": today,
        "open_price": float(px),
        "tp1": lv.get("tp1"),
        "stop": lv.get("stop"),
        "shares": SHARES,
        "last": float(px),
        "opened_at": now.strftime("%H:%M ET"),
    }
    save_paper(book)
    book["cumulative"] = paper_cum(book.get("trades") or [])
    book["skipped"] = None
    return book


def market_tape():
    """Buyers vs sellers snapshot from SPY + QQQ. Not a timing oracle."""
    spy_d = fetch_ohlc("SPY", "1d")
    spy_i = fetch_ohlc("SPY", "5m")
    qqq_d = fetch_ohlc("QQQ", "1d")
    def last_chg(ohlc):
        c = [x for x in (ohlc.get("close") or []) if x is not None]
        if len(c) < 2:
            return None
        return (c[-1] - c[-2]) / c[-2] * 100
    def last_px(ohlc):
        c = [x for x in (ohlc.get("close") or []) if x is not None]
        return c[-1] if c else None
    spy_sma = sma([x for x in (spy_d.get("close") or []) if x is not None], 20)
    spy_px = last_px(spy_d)
    spy_day = last_chg(spy_d)
    qqq_day = last_chg(qqq_d)
    buyers = 50
    notes = []
    if spy_px and spy_sma:
        if spy_px > spy_sma:
            buyers += 18
            notes.append("SPY above 20-day average")
        else:
            buyers -= 18
            notes.append("SPY below 20-day average")
    if spy_day is not None:
        buyers += max(-15, min(15, spy_day * 4))
        notes.append(f"SPY day {spy_day:+.2f}%")
    if qqq_day is not None:
        buyers += max(-10, min(10, qqq_day * 3))
        notes.append(f"QQQ day {qqq_day:+.2f}%")
    rsi_spy = rsi([x for x in (spy_d.get("close") or []) if x is not None])
    if rsi_spy is not None:
        if rsi_spy > 70:
            buyers -= 8
            notes.append(f"SPY RSI {rsi_spy:.0f} hot")
        elif rsi_spy < 35:
            buyers += 4
            notes.append(f"SPY RSI {rsi_spy:.0f} washed")
    buyers = int(max(5, min(95, buyers)))
    if buyers >= 62:
        label, stance = "Buyers in control", "Market health: OK to look for longs"
    elif buyers <= 38:
        label, stance = "Sellers in control", "Stay away from fresh longs"
    else:
        label, stance = "Mixed tape", "Be picky — no clear buy-the-market bid"
    return {
        "buyers": buyers,
        "sellers": 100 - buyers,
        "label": label,
        "stance": stance,
        "spy": spy_px,
        "notes": notes,
    }


def idea_score(symbol: str, screen: dict, tf: str = "5m") -> dict:
    ohlc = fetch_ohlc(symbol, tf)
    closes_try = [c for c in (ohlc.get("close") or []) if c is not None]
    if len(closes_try) < 15:
        daily = fetch_ohlc(symbol, "1d")
        if len([c for c in (daily.get("close") or []) if c is not None]) >= 15:
            ohlc = daily
            tf = tf + "→1d"
    if ohlc.get("error") and not (ohlc.get("close") or []):
        return {
            "symbol": symbol,
            "name": screen.get("name"),
            "price": screen.get("price"),
            "overall": screen.get("overall"),
            "score": 0,
            "setup": "No live bars",
            "reasons": [str(ohlc.get("error"))],
            "timeframe": tf,
            "levels": None,
        }
    closes = [c for c in (ohlc.get("close") or []) if c is not None]
    vols = [v for v in (ohlc.get("volume") or []) if v is not None]
    highs = [h for h in (ohlc.get("high") or []) if h is not None]
    price = (closes[-1] if closes else None) or screen.get("price")
    reasons = []
    score = 40
    setup = "Watch"
    daily = fetch_ohlc(symbol, "1d")
    d_closes = [c for c in (daily.get("close") or []) if c is not None]
    d_sma = sma(d_closes, 20)
    d_px = d_closes[-1] if d_closes else price
    if d_sma and d_px:
        if d_px > d_sma:
            score += 10
            reasons.append("Daily trend up (close > 20-day).")
        else:
            score -= 16
            reasons.append("Daily trend down — poor day-trade long.")
            setup = "Stay away (daily down)"

    sma20 = sma(closes, 20)
    sma50 = sma(closes, 50)
    r = rsi(closes)
    rel_vol = None
    if len(vols) >= 21:
        avg = sum(vols[-21:-1]) / 20
        if avg:
            rel_vol = vols[-1] / avg
    day_chg = screen.get("change_pct")
    if day_chg is None and len(closes) >= 2:
        day_chg = (closes[-1] - closes[-2]) / closes[-2] * 100
    dist_20h = None
    if highs and price:
        h20 = max(highs[-20:]) if len(highs) >= 20 else max(highs)
        if h20:
            dist_20h = (price / h20 - 1) * 100

    if sma20 and price and price > sma20:
        score += 12
        reasons.append("Price above 20-day average (short-term trend up).")
    elif sma20 and price:
        score -= 8
        reasons.append("Price below 20-day average.")

    if sma50 and price and price > sma50:
        score += 12
        reasons.append("Price above 50-day average (medium-term trend up).")
    elif sma50 and price:
        score -= 6
        reasons.append("Price below 50-day average.")

    if r is not None:
        if 45 <= r <= 68:
            score += 14
            reasons.append(f"RSI {r:.0f} is in a constructive zone (not overbought).")
        elif r > 75:
            score -= 10
            reasons.append(f"RSI {r:.0f} is stretched / overbought.")
        elif r < 35:
            score += 6
            reasons.append(f"RSI {r:.0f} looks washed out (possible bounce, not a trend buy).")
        else:
            reasons.append(f"RSI {r:.0f}.")

    if rel_vol is not None:
        if rel_vol >= 1.8:
            score += 16
            reasons.append(f"Relative volume {rel_vol:.1f}x — unusual activity.")
        elif rel_vol >= 1.2:
            score += 8
            reasons.append(f"Volume above average ({rel_vol:.1f}x).")
        else:
            reasons.append(f"Volume quiet ({rel_vol:.1f}x).")

    if day_chg is not None:
        if day_chg >= 2:
            score += 10
            reasons.append(f"Up {day_chg:.1f}% today.")
        elif day_chg <= -3:
            score -= 6
            reasons.append(f"Down {day_chg:.1f}% today.")

    if dist_20h is not None and dist_20h >= -1.5:
        score += 10
        reasons.append("Holding near the 20-day high (breakout-style).")
    elif sma20 and sma50 and price and sma50 < price < sma20 * 1.01:
        score += 6
        reasons.append("Pullback toward the 20-day average inside a larger uptrend.")

    score = max(0, min(99, score))
    if score >= 72 and rel_vol and rel_vol >= 1.5 and day_chg and day_chg > 0:
        setup = "Momentum"
    elif score >= 68 and dist_20h is not None and dist_20h >= -1.5:
        setup = "Breakout"
    elif score >= 62 and sma50 and price and price > sma50:
        setup = "Trend pullback"
    elif r is not None and r < 35:
        setup = "Oversold bounce"
    elif score < 45:
        setup = "Avoid / weak"

    lows = [x for x in (ohlc.get("low") or []) if x is not None]
    atr = None
    if len(closes) >= 16 and len(highs) >= 15 and len(lows) >= 15:
        trs = []
        for i in range(-14, 0):
            h = highs[i] if i < len(highs) else highs[-1]
            l = lows[i] if i < len(lows) else lows[-1]
            prev = closes[i - 1]
            trs.append(max(h - l, abs(h - prev), abs(l - prev)))
        atr = sum(trs) / len(trs)

    levels = None
    if price:
        buf = atr if atr and atr > 0 else price * 0.02
        low10 = min(lows[-10:]) if len(lows) >= 10 else (min(lows) if lows else price - buf)
        h20 = max(highs[-20:]) if len(highs) >= 20 else (max(highs) if highs else price + buf)
        if setup == "Trend pullback" and sma20:
            entry = round(min(price, sma20), 4)
        elif setup == "Oversold bounce":
            entry = round(price, 4)
        else:
            entry = round(price, 4)
        stop = round(min(low10, price - 1.2 * buf), 4)
        if stop >= entry:
            stop = round(entry - 1.2 * buf, 4)
        risk = max(entry - stop, price * 0.008)
        tp1 = round(max(h20, entry + 1.5 * risk), 4)
        tp2 = round(entry + 2.5 * risk, 4)
        rr1 = (tp1 - entry) / risk if risk else None
        levels = {
            "entry": entry,
            "entry_note": "Suggested buy zone around last price (pullback uses the 20-day average when lower).",
            "stop": stop,
            "tp1": tp1,
            "tp2": tp2,
            "atr": atr,
            "rr_tp1": rr1,
        }

    return {
        "symbol": symbol,
        "name": screen.get("name"),
        "price": price,
        "change_pct": day_chg,
        "overall": screen.get("overall"),
        "aaoifi": (screen.get("standards") or {}).get("AAOIFI", {}).get("verdict"),
        "msci": (screen.get("standards") or {}).get("MSCI", {}).get("verdict"),
        "score": score,
        "setup": setup,
        "rsi": r,
        "rel_vol": rel_vol,
        "sma20": sma20,
        "sma50": sma50,
        "levels": levels,
        "reasons": reasons[:5],
        "timeframe": tf,
        "bias": "Buy candidate" if score >= 65 and screen.get("overall") != "FAIL" else (
            "Watch" if score >= 50 else "Weak"
        ),
    }


def _parse_csv_tickers(text: str):
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not lines:
        return []
    header = [h.strip().strip('"') for h in lines[0].split(",")]
    idx = None
    for name in ("StockTicker", "Ticker", "ticker", "Symbol"):
        if name in header:
            idx = header.index(name)
            break
    out = []
    for ln in lines[1:]:
        cols = [c.strip().strip('"') for c in ln.split(",")]
        t = ""
        if idx is not None and idx < len(cols):
            t = cols[idx].upper().replace(".", "-")
        if t and all(ch.isalnum() or ch in "-" for ch in t) and 1 < len(t) <= 6:
            if t not in ("SPUS", "HLAL", "UMMA", "CASH", "USD"):
                out.append(t)
    return out


def fetch_etf_holdings():
    found = {}
    try:
        raw = http_get_ua(SPUS_CSV, UA, timeout=30).decode("utf-8", "replace")
        for t in _parse_csv_tickers(raw):
            found.setdefault(t, set()).add("SPUS")
    except Exception as e:
        print("SPUS holdings fetch failed:", e)
    extra = STATIC / "etf-extra.txt"
    if extra.exists():
        for line in extra.read_text(encoding="utf-8").splitlines():
            t = line.split("#")[0].strip().upper().replace(".", "-")
            if t:
                found.setdefault(t, set()).add("LOCAL")
    rows = []
    for t, srcs in sorted(found.items()):
        rows.append({
            "ok": True,
            "symbol": t,
            "name": t,
            "overall": "PASS",
            "aaoifi": "PASS" if "SPUS" in srcs else "REVIEW",
            "sp": "PASS" if "SPUS" in srcs else "REVIEW",
            "etfs": sorted(srcs),
            "source": "etf-holdings",
            "price": None,
        })
    return {
        "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "count": len(rows),
        "rows": rows,
        "note": "Deduped ETF holdings. SPUS from issuer CSV. Add HLAL/UMMA tickers in static/etf-extra.txt.",
    }


def export_from_etfs():
    payload = fetch_etf_holdings()
    payload["rows"] = enrich_prices(payload.get("rows") or [])
    UNIVERSE.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print("Wrote", UNIVERSE, "rows", payload["count"])
    return payload


def yahoo_quotes_batch(symbols):
    out = {}
    ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    symbols = [s for s in symbols if s]
    for i in range(0, len(symbols), 40):
        chunk = symbols[i:i + 40]
        url = "https://query1.finance.yahoo.com/v7/finance/quote?symbols=" + urllib.parse.quote(",".join(chunk))
        try:
            raw = json.loads(http_get_ua(url, ua, timeout=20))
            for q in (raw.get("quoteResponse") or {}).get("result") or []:
                sym = (q.get("symbol") or "").upper().replace(".", "-")
                out[sym] = {
                    "price": q.get("regularMarketPrice"),
                    "change_pct": q.get("regularMarketChangePercent"),
                    "name": q.get("shortName") or q.get("longName") or sym,
                }
        except Exception:
            for s in chunk:
                try:
                    one = yahoo_price(s)
                    if one.get("price") is not None:
                        out[s] = one
                except Exception:
                    pass
                time.sleep(0.03)
    return out


def enrich_prices(rows, limit=400):
    missing = [r.get("symbol") for r in rows[:limit] if r.get("price") is None and r.get("symbol")]
    quotes = yahoo_quotes_batch(missing) if missing else {}
    for row in rows:
        q = quotes.get((row.get("symbol") or "").upper())
        if not q:
            continue
        if q.get("price") is not None:
            row["price"] = q.get("price")
            row["change_pct"] = q.get("change_pct")
            if q.get("name"):
                row["name"] = q.get("name")
    return rows


def universe_age_days():
    snap = load_universe()
    if not snap or not snap.get("created_at"):
        return 999
    try:
        created = datetime.strptime(snap["created_at"].replace(" UTC", ""), "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - created).total_seconds() / 86400
    except Exception:
        return 999


def refresh_etf_universe_async(force=False):
    global _etf_refreshing
    if not force and universe_age_days() < 7:
        return
    with _lock:
        if _etf_refreshing:
            return
        _etf_refreshing = True

    def work():
        global _etf_refreshing
        try:
            print("Refreshing ETF universe on server…")
            export_from_etfs()
        except Exception as e:
            print("ETF refresh failed:", e)
        finally:
            _etf_refreshing = False

    threading.Thread(target=work, daemon=True).start()


def load_universe():
    if not UNIVERSE.exists():
        return None
    try:
        data = json.loads(UNIVERSE.read_text(encoding="utf-8"))
        return data
    except Exception:
        return None


def export_universe(symbols=None):
    symbols = symbols or WATCHLIST
    rows = []
    for i, s in enumerate(symbols, 1):
        print(f"[{i}/{len(symbols)}] {s}")
        try:
            sc = screen_symbol(s)
            rows.append({
                "ok": sc.get("ok"),
                "symbol": sc.get("symbol", s),
                "name": sc.get("name"),
                "overall": sc.get("overall"),
                "aaoifi": (sc.get("standards") or {}).get("AAOIFI", {}).get("verdict"),
                "msci": (sc.get("standards") or {}).get("MSCI", {}).get("verdict"),
                "sp": (sc.get("standards") or {}).get("SP", {}).get("verdict"),
                "djim": (sc.get("standards") or {}).get("DJIM", {}).get("verdict"),
                "ftse": (sc.get("standards") or {}).get("FTSE", {}).get("verdict"),
                "rajhi": (sc.get("standards") or {}).get("RAJHI", {}).get("verdict"),
                "alinma": (sc.get("standards") or {}).get("ALINMA", {}).get("verdict"),
                "bilad": (sc.get("standards") or {}).get("BILAD", {}).get("verdict"),
                "price": sc.get("price"),
                "change_pct": sc.get("change_pct"),
                "market_cap": sc.get("market_cap"),
                "business": (sc.get("business") or {}).get("status"),
                "error": sc.get("error"),
            })
        except Exception as e:
            rows.append({"ok": False, "symbol": s, "error": str(e)})
        time.sleep(0.12)
    payload = {
        "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "count": len(rows),
        "rows": rows,
        "note": "Weekly PC export. Public site should filter this file and not re-hit SEC.",
    }
    UNIVERSE.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    (CACHE / "universe.json").write_text(json.dumps(payload), encoding="utf-8")
    print("Wrote", UNIVERSE)
    return payload


def filter_universe_rows(rows, max_price, min_price, allow):
    out = []
    for sc in rows:
        if not sc.get("ok") and sc.get("overall") is None:
            continue
        v = sc.get("overall")
        if allow and "ALL" not in allow and v not in allow:
            continue
        px = sc.get("price")
        if min_price not in (None, 0) or max_price is not None:
            if px is None:
                continue
            if min_price is not None and px < min_price:
                continue
            if max_price is not None and px > max_price:
                continue
        out.append(sc)
    return out


def daily_eligible(max_price, min_price, allow):
    """Halal + price universe. Prefer weekly static/universe.json."""
    snap = load_universe()
    if snap and snap.get("rows"):
        rows = enrich_prices(list(snap["rows"]))
        return filter_universe_rows(rows, max_price, min_price, allow)

    def fetch():
        rows = []
        for s in WATCHLIST[:80]:
            try:
                sc = screen_symbol(s)
                if not sc.get("ok"):
                    continue
                v = sc.get("overall")
                if allow and "ALL" not in allow and v not in allow:
                    continue
                px = sc.get("price")
                if px is not None:
                    if min_price is not None and px < min_price:
                        continue
                    if max_price is not None and px > max_price:
                        continue
                rows.append({
                    "symbol": sc["symbol"],
                    "name": sc.get("name"),
                    "overall": sc.get("overall"),
                    "aaoifi": (sc.get("standards") or {}).get("AAOIFI", {}).get("verdict"),
                    "msci": (sc.get("standards") or {}).get("MSCI", {}).get("verdict"),
                    "price": sc.get("price"),
                    "change_pct": sc.get("change_pct"),
                    "standards": sc.get("standards"),
                })
            except Exception:
                pass
            time.sleep(0.08)
        return {"rows": rows}

    key = f"eligible_{min_price}_{max_price}_{'-'.join(sorted(allow))}.json"
    return cached_json(key, fetch, ttl=20 * 3600)["rows"]


def screen_symbol(symbol: str) -> dict:
    symbol = symbol.upper().replace(".", "-")
    tickers = load_tickers()
    info = tickers.get(symbol)
    if not info and symbol.endswith("-B"):
        info = tickers.get(symbol.replace("-B", ""))
    # BRK-B special
    if not info and symbol == "BRK-B":
        info = tickers.get("BRK-B") or next((v for k, v in tickers.items() if "BERKSHIRE" in v["title"].upper() and k.startswith("BRK")), None)
        if info:
            pass
    if not info:
        # try without dash
        alt = symbol.replace("-", ".")
        for k, v in tickers.items():
            if k.replace("-", ".") == alt or k == symbol.replace("-", ""):
                info = v
                break
    if not info:
        return {"ok": False, "symbol": symbol, "error": "Ticker not found in SEC company list (US filers only)."}

    cik = info["cik"]

    def fetch_facts():
        return json.loads(http_get(SEC_FACTS.format(cik=cik), timeout=40))

    def fetch_subs():
        return json.loads(http_get(SEC_SUBS.format(cik=cik), timeout=30))

    try:
        facts = cached_json(f"facts_{cik}.json", fetch_facts, ttl=7 * 24 * 3600)
    except Exception as e:
        return {"ok": False, "symbol": symbol, "error": f"SEC facts failed: {e}"}
    try:
        subs = cached_json(f"subs_{cik}.json", fetch_subs, ttl=24 * 3600)
    except Exception:
        subs = {}

    gaap = facts.get("facts", {}).get("us-gaap", {})
    dei = facts.get("facts", {}).get("dei", {})

    assets = latest_instant(gaap, ["Assets"])
    cash = latest_instant(gaap, [
        "CashAndCashEquivalentsAtCarryingValue",
        "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
        "Cash",
    ])
    securities = latest_instant(gaap, [
        "MarketableSecuritiesCurrent",
        "AvailableForSaleSecuritiesCurrent",
        "ShortTermInvestments",
        "AvailableForSaleSecuritiesDebtSecuritiesCurrent",
        "DebtSecuritiesAvailableForSaleCurrent",
    ])
    recv = latest_instant(gaap, [
        "AccountsReceivableNetCurrent",
        "AccountsReceivableNet",
        "ReceivablesNetCurrent",
    ])
    debt = sum_debt(gaap)
    revenue = latest_duration(gaap, [
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "Revenues",
        "SalesRevenueNet",
        "RevenueFromContractWithCustomerIncludingAssessedTax",
    ])
    interest_inc = latest_duration(gaap, [
        "InterestIncomeOperating",
        "InterestAndDividendIncomeOperating",
        "InvestmentIncomeInterest",
        "InterestIncome",
        "InterestAndOtherIncome",
    ])

    shares = None
    sh_node = dei.get("EntityCommonStockSharesOutstanding") or gaap.get("CommonStockSharesOutstanding")
    if sh_node:
        series = sh_node.get("units", {}).get("shares") or []
        inst = [x for x in series if "start" not in x]
        if inst:
            inst.sort(key=lambda x: x.get("end") or "")
            shares = float(inst[-1]["val"])

    quote = yahoo_price(symbol)
    price = quote.get("price")
    market_cap = price * shares if price and shares else None
    if market_cap is None:
        # 10-K public float is stale but keeps AAOIFI-style ratios usable if the price feed is down
        pf = None
        node = dei.get("EntityPublicFloat")
        if node:
            series = node.get("units", {}).get("USD") or []
            inst = [x for x in series if "start" not in x and x.get("val") is not None]
            if inst:
                inst.sort(key=lambda x: x.get("end") or "")
                pf = float(inst[-1]["val"])
                market_cap = pf

    cash_val = (cash["val"] if cash else 0.0) + (securities["val"] if securities else 0.0)
    cash_asof = max(filter(None, [cash["end"] if cash else None, securities["end"] if securities else None]), default=None)

    sic = str(subs.get("sic") or "")
    sic_desc = subs.get("sicDescription") or ""
    biz = classify_business(sic, sic_desc, facts.get("entityName") or info["title"])

    impure = None
    impure_note = "Interest income tag not found in XBRL — review the latest 10-K notes for non-operating / interest income."
    if interest_inc and revenue and revenue["val"]:
        impure = interest_inc["val"] / revenue["val"]
        impure_note = f"Proxy = {interest_inc['tag']} / {revenue['tag']} ({interest_inc.get('fp') or interest_inc.get('form')}). This is interest-like income only, not a full segment review."

    def ratio(num, den):
        if num is None or den in (None, 0):
            return None
        return num / den

    debt_val = debt["val"]
    assets_val = assets["val"] if assets else None
    recv_val = recv["val"] if recv else None

    def eval_standard(std):
        denom = market_cap if std["denom"] == "market_cap" else assets_val
        debt_r = ratio(debt_val, denom)
        cash_r = ratio(cash_val if cash or securities else None, denom)
        if std.get("recv_max") is None:
            recv_r = None
        elif std.get("recv_denom") == "market_cap" or (std["denom"] == "market_cap" and std.get("recv_denom") != "assets"):
            recv_r = ratio(recv_val, market_cap)
        else:
            recv_r = ratio((recv_val or 0) + (cash["val"] if cash else 0), assets_val)
        checks = []

        def add(name, val, cap, ok_if_missing=False):
            if val is None:
                checks.append({"name": name, "value": None, "max": cap, "pass": None, "missing": True})
                return
            checks.append({"name": name, "value": val, "max": cap, "pass": val < cap, "missing": False})

        add("Debt ratio", debt_r, std["debt_max"])
        add("Cash + interest-bearing securities", cash_r, std["cash_max"])
        if std["recv_max"] is not None:
            add("Receivables + cash / assets", recv_r, std["recv_max"])
        add("Impure income proxy", impure, std["impure_max"])

        known = [c for c in checks if c["pass"] is not None]
        failed = [c for c in known if c["pass"] is False]
        missing = [c for c in checks if c["missing"]]
        if biz["status"] == "fail":
            verdict = "FAIL"
        elif failed:
            verdict = "FAIL"
        elif missing or biz["status"] == "review":
            verdict = "REVIEW"
        else:
            verdict = "PASS"
        return {
            "id": None,
            "label": std["label"],
            "denom": std["denom"],
            "checks": checks,
            "verdict": verdict,
        }

    standards = {k: eval_standard(v) for k, v in STANDARDS.items()}
    for k, v in standards.items():
        v["id"] = k

    overall = "PASS"
    if any(s["verdict"] == "FAIL" for s in standards.values()) or biz["status"] == "fail":
        # overall uses AAOIFI as primary
        overall = standards["AAOIFI"]["verdict"]
        if biz["status"] == "fail":
            overall = "FAIL"
    elif any(s["verdict"] == "REVIEW" for s in standards.values()) or biz["status"] == "review":
        overall = "REVIEW"

    purify = None
    if impure is not None:
        purify = max(impure, 0.0)

    return {
        "ok": True,
        "symbol": symbol,
        "name": quote.get("name") or facts.get("entityName") or info["title"],
        "cik": cik,
        "price": price,
        "change_pct": quote.get("change_pct"),
        "currency": quote.get("currency") or "USD",
        "exchange": quote.get("exchange"),
        "shares": shares,
        "market_cap": market_cap,
        "business": biz,
        "financials": {
            "assets": assets,
            "cash": cash,
            "securities": securities,
            "receivables": recv,
            "debt": debt,
            "revenue": revenue,
            "interest_income": interest_inc,
            "cash_plus_securities": {"val": cash_val if (cash or securities) else None, "asof": cash_asof},
        },
        "impure_income_proxy": impure,
        "impure_note": impure_note,
        "purification_rate": purify,
        "standards": standards,
        "overall": overall,
        "screened_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "disclaimer": "Educational screen from public SEC XBRL + last price. Not a fatwa. Scholars and index providers differ on hotels, media, weapons, payments, and mixed-revenue firms.",
    }


def user_now():
    """User daytime is UTC+4."""
    return datetime.now(timezone.utc) - __import__("datetime").timedelta(hours=-4) if False else (
        datetime.now(timezone.utc) + __import__("datetime").timedelta(hours=4)
    )


def fx_atr(highs, lows, closes, n=14):
    if min(len(highs), len(lows), len(closes)) < n + 1:
        return None
    trs = []
    for i in range(-n, 0):
        h, l, pc = highs[i], lows[i], closes[i - 1]
        if None in (h, l, pc):
            continue
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    if not trs:
        return None
    return sum(trs) / len(trs)


def score_side(closes, highs, vols, price, side):
    score = 40
    reasons = []
    sma20 = sma(closes, 20)
    sma50 = sma(closes, 50)
    r = rsi(closes)
    day_chg = None
    if len(closes) >= 2 and closes[-2]:
        day_chg = (closes[-1] - closes[-2]) / closes[-2] * 100
    rel_vol = None
    if len(vols) >= 21 and vols[-1] and sum(vols[-21:-1]):
        rel_vol = vols[-1] / (sum(vols[-21:-1]) / 20)

    up = side == "BUY"
    if sma20 and price:
        if (price > sma20) == up:
            score += 14
            reasons.append("Price vs SMA20 favors " + side)
        else:
            score -= 10
    if sma50 and price:
        if (price > sma50) == up:
            score += 10
        else:
            score -= 8
    if r is not None:
        if up and 45 <= r <= 68:
            score += 12
        elif up and r < 35:
            score += 6
            reasons.append("Washed RSI — bounce long")
        elif up and r > 75:
            score -= 12
        elif (not up) and 32 <= r <= 55:
            score += 12
        elif (not up) and r > 70:
            score += 8
            reasons.append("Stretched RSI — fade short")
        elif (not up) and r < 30:
            score -= 12
    if day_chg is not None:
        if (day_chg > 0) == up:
            score += min(12, abs(day_chg) * 2)
        else:
            score -= min(10, abs(day_chg) * 2)
    if rel_vol and rel_vol >= 1.3:
        score += 8
        reasons.append("Volume expanding")
    return score, reasons, r, day_chg


def session_vwap(closes, highs, lows, vols):
    n = min(len(closes), 48)
    if n < 5:
        return None
    c, h, l = closes[-n:], (highs or closes)[-n:], (lows or closes)[-n:]
    v = (vols or [])[-n:]
    if v and any(x and x > 0 for x in v) and len(v) == n:
        num = den = 0.0
        for i in range(n):
            tp = ((h[i] or c[i]) + (l[i] or c[i]) + c[i]) / 3
            vv = v[i] or 0
            num += tp * vv
            den += vv
        return num / den if den else sum(c) / n
    return sum(c) / n


def load_fx_sides():
    p = CACHE / "fx-sides.json"
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_fx_sides(data):
    (CACHE / "fx-sides.json").write_text(json.dumps(data), encoding="utf-8")
    try:
        (STATIC / "fx-sides.json").write_text(json.dumps(data), encoding="utf-8")
    except Exception:
        pass


def fx_score_one(item, tf="1h"):
    # 1h not in TF_MAP — map hourly to 5m/1d mix: use 1d for stability + 5m impulse
    daily = fetch_ohlc(item["symbol"], "1d")
    intra = fetch_ohlc(item["symbol"], "5m")
    d_closes = [c for c in (daily.get("close") or []) if c is not None]
    closes = [c for c in (intra.get("close") or []) if c is not None] or d_closes
    highs = [h for h in (intra.get("high") or daily.get("high") or []) if h is not None]
    lows = [x for x in (intra.get("low") or daily.get("low") or []) if x is not None]
    vols = [v for v in (intra.get("volume") or daily.get("volume") or []) if v is not None]
    q = yahoo_price(item["symbol"], ttl=40)
    price = q.get("price") or (closes[-1] if closes else None)
    if not price or len(closes) < 10:
        return {
            "symbol": item["symbol"], "tv": item["tv"], "name": item["name"], "group": item["group"],
            "price": price, "score": 0, "side": "NONE", "setup": "No data", "levels": None,
        }
    buy, br, rsi_b, chg = score_side(closes, highs, vols, price, "BUY")
    sell, sr, rsi_s, _ = score_side(closes, highs, vols, price, "SELL")
    vw = session_vwap(closes, highs, lows, vols)
    if vw and price:
        if price >= vw:
            buy += 8
            sell -= 8
            br.append("Above session VWAP")
        else:
            sell += 8
            buy -= 8
            sr.append("Below session VWAP")
    buy = int(max(0, min(99, buy)))
    sell = int(max(0, min(99, sell)))
    prev = (load_fx_sides().get(item["symbol"]) or {}).get("side")
    if abs(buy - sell) < 8 and max(buy, sell) < 72:
        side, score, reasons = "NONE", max(buy, sell), ["No-trade band — BUY and SELL too close"]
    elif buy >= sell + 10 or (buy >= sell and prev != "SELL"):
        side, score, reasons = "BUY", buy, br
    elif sell >= buy + 10 or (sell >= buy and prev != "BUY"):
        side, score, reasons = "SELL", sell, sr
    elif prev == "BUY" and sell < buy + 10:
        side, score, reasons = "BUY", buy, br + ["Held BUY (need +10 to flip)"]
    elif prev == "SELL" and buy < sell + 10:
        side, score, reasons = "SELL", sell, sr + ["Held SELL (need +10 to flip)"]
    elif buy >= sell:
        side, score, reasons = "BUY", buy, br
    else:
        side, score, reasons = "SELL", sell, sr
    a = fx_atr(highs[-30:] if highs else closes, lows[-30:] if lows else closes, closes[-30:] if closes else [])
    if not a:
        a = price * 0.004
    a = min(a, price * 0.012)
    if side == "SELL":
        entry, stop, tp1, tp2 = price, price + 1.0 * a, price - 1.2 * a, price - 2.0 * a
    else:
        entry, stop, tp1, tp2 = price, price - 1.0 * a, price + 1.2 * a, price + 2.0 * a
    if side == "NONE":
        setup = "No trade"
    elif score >= 70:
        setup = "Strong " + side
    elif score >= 55:
        setup = "Watch " + side
    else:
        setup = "Weak"
    return {
        "symbol": item["symbol"],
        "tv": item["tv"],
        "name": item["name"],
        "group": item["group"],
        "price": price,
        "vwap": round(vw, 6) if vw else None,
        "buy_score": buy,
        "sell_score": sell,
        "change_pct": q.get("change_pct") if q.get("change_pct") is not None else chg,
        "rsi": rsi_b,
        "score": score,
        "side": side,
        "setup": setup,
        "reasons": reasons[:4],
        "levels": {
            "entry": round(entry, 6),
            "stop": round(stop, 6),
            "tp1": round(tp1, 6),
            "tp2": round(tp2, 6),
        },
    }


def fx_tape():
    rows = []
    for sym in ("XAUUSD=X", "CL=F", "BTC-USD", "EURUSD=X"):
        q = yahoo_price(sym)
        rows.append((sym, q.get("change_pct")))
    chgs = [c for _, c in rows if c is not None]
    risk_on = 50
    notes = []
    for sym, c in rows:
        if c is None:
            continue
        notes.append(f"{sym} {c:+.2f}%")
        if sym == "BTC-USD":
            risk_on += max(-12, min(12, c))
        elif sym == "XAUUSD=X":
            risk_on += max(-8, min(8, -c * 0.4))
        elif sym == "CL=F":
            risk_on += max(-8, min(8, c * 0.5))
        elif sym == "EURUSD=X":
            risk_on += max(-6, min(6, c * 2))
    risk_on = int(max(8, min(92, risk_on)))
    if risk_on >= 60:
        label, stance = "Risk-on / buyers", "OK to take strongest BUY if score high"
    elif risk_on <= 40:
        label, stance = "Risk-off / sellers", "Prefer strongest SELL or stay flat"
    else:
        label, stance = "Mixed metals/FX tape", "Only take a clear 70+ setup"
    return {"buyers": risk_on, "sellers": 100 - risk_on, "label": label, "stance": stance, "notes": notes}


def load_fx_paper():
    remote = drive_load("fx-trades.json")
    if isinstance(remote, dict):
        return remote
    path = FX_PAPER if FX_PAPER.exists() else (STATIC / "fx-paper.json")
    if not path.exists():
        return {"trades": [], "open": None}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"trades": [], "open": None}


def save_fx_paper(data):
    text = json.dumps(data, indent=2)
    FX_PAPER.write_text(text, encoding="utf-8")
    try:
        (STATIC / "fx-paper.json").write_text(text, encoding="utf-8")
        days = STATIC / "fx-days"
        days.mkdir(exist_ok=True)
        by = {}
        for t in data.get("trades") or []:
            by.setdefault(t.get("date") or "unknown", []).append(t)
        index = []
        for day, rows in by.items():
            pips = round(sum((r.get("pips") or 0) for r in rows), 1)
            (days / f"{day}.json").write_text(json.dumps({"date": day, "trades": rows, "pips": pips}, indent=2), encoding="utf-8")
            index.append({"date": day, "trades": len(rows), "pips": pips})
        index.sort(key=lambda x: x["date"], reverse=True)
        (days / "index.json").write_text(json.dumps(index, indent=2), encoding="utf-8")
    except Exception:
        pass
    try:
        drive_save("fx-trades.json", data)
    except Exception as e:
        print("Drive fx save failed:", e)


def fx_slot_now(now):
    daytime = 5 <= now.hour < 15
    qmin = (now.minute // 15) * 15
    slot_id = f"{now.strftime('%Y-%m-%d')}-{now.hour:02d}{qmin:02d}"
    slot_label = f"{now.hour:02d}:{qmin:02d}"
    return daytime, slot_id, slot_label


def pip_size_of(symbol: str, item=None):
    if item and item.get("pip"):
        return float(item["pip"])
    s = (symbol or "").upper()
    for row in FX_BOOK:
        if row["symbol"] == symbol or row.get("tv") == symbol:
            return float(row.get("pip") or 0.0001)
    if "JPY" in s:
        return 0.01
    if "XAU" in s or s == "GC=F":
        return 0.01
    if "XAG" in s or s == "SI=F":
        return 0.01
    if s in ("CL=F", "BZ=F") or "OIL" in s:
        return 0.01
    if "BTC" in s:
        return 1.0
    if "ETH" in s:
        return 0.1
    if s.endswith("=X"):
        return 0.0001
    return 0.0001


def to_pips(symbol, move, item=None):
    ps = pip_size_of(symbol, item)
    if not ps:
        return 0.0
    return round(move / ps, 1)


def fx_mark(pos, last):
    if not pos or last is None or not pos.get("open_price"):
        return 0.0
    signed = 1 if pos.get("side") == "BUY" else -1
    return to_pips(pos.get("symbol"), signed * (last - pos["open_price"]))


def fx_totals(book, today=None):
    if today is None:
        today = user_now().strftime("%Y-%m-%d")
    trades = book.get("trades") or []
    total = sum((t.get("pips") if t.get("pips") is not None else 0) for t in trades)
    daily = sum((t.get("pips") if t.get("pips") is not None else 0) for t in trades if t.get("date") == today)
    op = book.get("open") or {}
    live = op.get("live_pips") if op.get("live_pips") is not None else (op.get("live_pct") or 0)
    if op:
        total += live
        if op.get("date") == today:
            daily += live
    book["cumulative"] = round(total, 1)
    book["daily_cumulative"] = round(daily, 1)
    book["closed_count"] = len(trades)
    book["today"] = today
    return book


def fx_update_paper(top, tape):
    """Paper while /fx.html is open, 05:00–15:00 Muscat (+04). Hold to SL/TP."""
    book = load_fx_paper()
    now = user_now()
    today = now.strftime("%Y-%m-%d")
    hour = now.hour
    daytime, slot_id, slot_label = fx_slot_now(now)
    pos = book.get("open")

    def close_pos(pos, last, why):
        signed = 1 if pos.get("side") == "BUY" else -1
        move = signed * (last - pos["open_price"])
        pips = to_pips(pos.get("symbol"), move)
        pct = move / pos["open_price"] * 100 if pos.get("open_price") else 0
        book["trades"].append({
            "symbol": pos["symbol"],
            "name": pos.get("name"),
            "group": pos.get("group"),
            "side": pos.get("side"),
            "date": pos.get("date"),
            "slot": pos.get("slot"),
            "slot_id": pos.get("slot_id"),
            "open_price": pos["open_price"],
            "close_price": last,
            "exit": why,
            "points": pips,
            "pips": pips,
            "pct": round(pct, 3),
            "pnl": pips,
        })
        book["open"] = None

    if pos:
        q = yahoo_price(pos["symbol"])
        last = q.get("price")
        if last is not None:
            pos["last"] = last
            tp, sl, side = pos.get("tp1"), pos.get("stop"), pos.get("side")
            hit = None
            if side == "BUY":
                if tp and last >= tp:
                    hit = "TP1"
                elif sl and last <= sl:
                    hit = "SL"
            else:
                if tp and last <= tp:
                    hit = "TP1"
                elif sl and last >= sl:
                    hit = "SL"
            # Hold until TP/SL, a +10 side flip on this symbol, or end of day — not every 15 min.
            if top:
                idea = top[0]
                if idea.get("symbol") == pos.get("symbol") and idea.get("side") in ("BUY", "SELL"):
                    if idea.get("side") != pos.get("side"):
                        other = idea.get("sell_score") if pos.get("side") == "BUY" else idea.get("buy_score")
                        mine = idea.get("buy_score") if pos.get("side") == "BUY" else idea.get("sell_score")
                        if other is not None and mine is not None and other >= (mine or 0) + 10:
                            hit = "FLIP"
                # different #1: keep holding current until SL/TP
            if (not daytime) or hour >= 15:
                hit = hit or "EOD"
            if hit:
                close_pos(pos, last, hit)
            else:
                pos["live_pct"] = fx_mark(pos, last)
                pos["live_pips"] = pos["live_pct"]
                book["open"] = pos
        save_fx_paper(book)
        fx_totals(book, today)
        book["server_local"] = now.strftime("%Y-%m-%d %H:%M +04")
        return book

    done = any(t.get("slot_id") == slot_id for t in book.get("trades") or [])
    buyers = (tape or {}).get("buyers") or 50
    if not daytime:
        book["skipped"] = f"outside 05:00–15:00 Muscat — clock {now.strftime('%H:%M +04')}"
        fx_totals(book, today)
        book["server_local"] = now.strftime("%Y-%m-%d %H:%M +04")
        return book
    if done:
        book["skipped"] = f"already logged slot {slot_label} +04"
        fx_totals(book, today)
        book["server_local"] = now.strftime("%Y-%m-%d %H:%M +04")
        return book
    if not top:
        book["skipped"] = "no ranked market"
        fx_totals(book, today)
        book["server_local"] = now.strftime("%Y-%m-%d %H:%M +04")
        return book
    idea = next((x for x in top if x.get("side") in ("BUY", "SELL") and (x.get("score") or 0) >= 58), None)
    if not idea:
        book["skipped"] = "no clear side (no-trade band or score < 58)"
        fx_totals(book, today)
        book["server_local"] = now.strftime("%Y-%m-%d %H:%M +04")
        return book
    lv = idea.get("levels") or {}
    px = idea.get("price") or lv.get("entry")
    book["open"] = {
        "symbol": idea["symbol"],
        "tv": idea.get("tv"),
        "name": idea.get("name"),
        "group": idea.get("group"),
        "side": idea.get("side"),
        "date": today,
        "slot": slot_label,
        "slot_id": slot_id,
        "open_price": float(px),
        "tp1": lv.get("tp1"),
        "stop": lv.get("stop"),
        "last": float(px),
        "live_pct": 0,
        "live_pips": 0,
        "opened_at": now.strftime("%H:%M +04"),
        "tape": buyers,
    }
    save_fx_paper(book)
    book["skipped"] = None
    fx_totals(book, today)
    book["server_local"] = now.strftime("%Y-%m-%d %H:%M +04")
    return book


def rank_fx():
    ranked = []
    for item in FX_BOOK:
        try:
            ranked.append(fx_score_one(item))
        except Exception as e:
            ranked.append({
                "symbol": item["symbol"], "tv": item["tv"], "name": item["name"],
                "group": item["group"], "score": 0, "side": "NONE", "setup": str(e),
            })
        time.sleep(0.04)
    ranked.sort(key=lambda x: (0 if x.get("side") == "NONE" else 1, x.get("score") or 0), reverse=True)
    sides = load_fx_sides()
    for i, row in enumerate(ranked, 1):
        row["rank"] = i
        sides[row["symbol"]] = {"side": row.get("side"), "score": row.get("score")}
    save_fx_sides(sides)
    return ranked


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(STATIC), **kwargs)

    def log_message(self, fmt, *args):
        print("[http]", self.address_string(), fmt % args)

    def _json(self, obj, code=200):
        body = json.dumps(obj, default=str).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        q = urllib.parse.parse_qs(parsed.query)

        if path in ("/api/ideas", "/api/batch", "/api/universe", "/"):
            refresh_etf_universe_async(False)

        if path == "/api/tape":
            try:
                return self._json({"ok": True, **market_tape()})
            except Exception as e:
                return self._json({"ok": False, "buyers": 50, "sellers": 50, "label": "Tape unavailable", "stance": str(e)})

        if path == "/api/health":
            snap = load_universe()
            return self._json({
                "ok": True,
                "watchlist": len(WATCHLIST),
                "universe": bool(snap),
                "universe_at": (snap or {}).get("created_at"),
                "universe_count": (snap or {}).get("count"),
            })

        if path == "/api/universe":
            snap = load_universe()
            if not snap:
                return self._json({"ok": False, "error": "No static/universe.json. Run weekly export on your PC."}, 404)
            max_price = q.get("max_price", [""])[0]
            min_price = q.get("min_price", [""])[0]
            try:
                max_price = float(max_price) if max_price else None
            except ValueError:
                max_price = None
            try:
                min_price = float(min_price) if min_price else 0.0
            except ValueError:
                min_price = 0.0
            allowed = (q.get("verdict") or ["ALL"])[0].upper()
            allow = {x.strip() for x in allowed.split(",") if x.strip()}
            raw_rows = enrich_prices(list(snap.get("rows") or []))
            rows = filter_universe_rows(raw_rows, max_price, min_price, allow)
            return self._json({"ok": True, "created_at": snap.get("created_at"), "results": rows, "count": len(rows), "source": "weekly-universe"})

        if path == "/api/watchlist":
            return self._json({"symbols": WATCHLIST})

        if path == "/api/search":
            qstr = (q.get("q") or [""])[0].strip().upper()
            tickers = load_tickers()
            hits = []
            if qstr:
                for t, meta in tickers.items():
                    if qstr in t or qstr.lower() in meta["title"].lower():
                        hits.append({"symbol": t, "name": meta["title"], "cik": meta["cik"]})
                    if len(hits) >= 20:
                        break
            return self._json({"results": hits})

        if path == "/api/screen":
            sym = (q.get("symbol") or [""])[0].strip()
            if not sym:
                return self._json({"ok": False, "error": "symbol required"}, 400)
            try:
                return self._json(screen_symbol(sym))
            except Exception as e:
                return self._json({"ok": False, "symbol": sym.upper(), "error": str(e)}, 500)

        if path == "/api/batch":
            snap = load_universe()
            if snap and snap.get("rows") and not (q.get("symbols") or [""])[0].strip():
                max_price = q.get("max_price", [""])[0]
                min_price = q.get("min_price", [""])[0]
                try:
                    max_price = float(max_price) if max_price else None
                except ValueError:
                    max_price = None
                try:
                    min_price = float(min_price) if min_price else None
                except ValueError:
                    min_price = None
                allowed = (q.get("verdict") or ["ALL"])[0].upper()
                allow = {x.strip() for x in allowed.split(",") if x.strip()}
                rows = filter_universe_rows(snap["rows"], max_price, min_price, allow)
                return self._json({
                    "results": rows,
                    "count": len(rows),
                    "source": "weekly-universe",
                    "created_at": snap.get("created_at"),
                })
            syms = [s.strip().upper() for s in (q.get("symbols") or [","])[0].split(",") if s.strip()]
            if not syms:
                syms = WATCHLIST
            out = []
            for s in syms[:80]:
                try:
                    r = screen_symbol(s)
                    out.append({
                        "ok": r.get("ok"),
                        "symbol": r.get("symbol", s),
                        "name": r.get("name"),
                        "overall": r.get("overall"),
                        "price": r.get("price"),
                        "change_pct": r.get("change_pct"),
                        "market_cap": r.get("market_cap"),
                        "business": (r.get("business") or {}).get("status"),
                        "aaoifi": (r.get("standards") or {}).get("AAOIFI", {}).get("verdict"),
                        "msci": (r.get("standards") or {}).get("MSCI", {}).get("verdict"),
                        "error": r.get("error"),
                    })
                except Exception as e:
                    out.append({"ok": False, "symbol": s, "error": str(e)})
                time.sleep(0.12)
            return self._json({"results": out, "count": len(out)})

        if path == "/api/ideas":
            syms = [s.strip().upper() for s in (q.get("symbols") or [""])[0].split(",") if s.strip()]
            if not syms:
                syms = WATCHLIST
            max_price = q.get("max_price", [""])[0]
            min_price = q.get("min_price", [""])[0]
            try:
                max_price = float(max_price) if max_price else None
            except ValueError:
                max_price = None
            try:
                min_price = float(min_price) if min_price else 0.0
            except ValueError:
                min_price = 0.0
            allowed = (q.get("verdict") or ["PASS,REVIEW"])[0].upper()
            allow = {x.strip() for x in allowed.split(",") if x.strip()}
            tf = (q.get("tf") or ["5m"])[0]
            if tf not in TF_MAP:
                tf = "5m"
            ideas = []
            skipped = 0
            if not [s.strip().upper() for s in (q.get("symbols") or [""])[0].split(",") if s.strip()]:
                universe = daily_eligible(max_price, min_price, allow)
            else:
                universe = [{"symbol": s, "name": s, "overall": "REVIEW"} for s in
                            [x.strip().upper() for x in (q.get("symbols") or [""])[0].split(",") if x.strip()][:40]]
            skipped = max(0, 80 - len(universe))
            for row in universe[:40]:
                s = row["symbol"]
                try:
                    ideas.append(idea_score(s, row, tf))
                except Exception as e:
                    ideas.append({"symbol": s, "error": str(e), "score": 0, "setup": "Error", "timeframe": tf})
                time.sleep(0.05)
            ideas.sort(key=lambda x: x.get("score") or 0, reverse=True)
            for i, row in enumerate(ideas, 1):
                row["rank"] = i
            top = ideas[:10]
            tape = market_tape()
            paper = update_paper_with_top(top, tape)
            return self._json({
                "results": top,
                "count": len(top),
                "scored": len(ideas),
                "skipped": skipped,
                "market": tape,
                "paper": paper,
                "note": "Paper log: 100 shares of #1 if tape healthy in the 9:28–11:30 ET window. Not a live order.",
            })

        if path == "/api/paper":
            book = load_paper()
            book["cumulative"] = paper_cum(book.get("trades") or [])
            return self._json(book)

        if path == "/api/fx/tape":
            try:
                return self._json({"ok": True, **fx_tape()})
            except Exception as e:
                return self._json({"ok": False, "buyers": 50, "sellers": 50, "label": str(e)})

        if path == "/api/fx/ideas":
            ranked = rank_fx()
            tape = fx_tape()
            paper = fx_update_paper(ranked, tape)
            return self._json({
                "results": ranked,
                "count": len(ranked),
                "market": tape,
                "paper": paper,
                "note": "Paper 05:00–15:00 Muscat while /fx.html is open. Hold to SL/TP.",
            })

        if path == "/api/fx/days":
            idx = STATIC / "fx-days" / "index.json"
            days = []
            if idx.exists():
                try:
                    days = json.loads(idx.read_text(encoding="utf-8"))
                except Exception:
                    days = []
            return self._json({"ok": True, "days": days})

        if path == "/api/fx/paper":
            book = load_fx_paper()
            op = book.get("open")
            if op and op.get("symbol"):
                q = yahoo_price(op["symbol"], ttl=20)
                last = q.get("price")
                if last is not None:
                    op["last"] = last
                    op["live_pct"] = fx_mark(op, last)
                    op["live_pips"] = op["live_pct"]
                    book["open"] = op
                    save_fx_paper(book)
            fx_totals(book)
            book["server_local"] = user_now().strftime("%Y-%m-%d %H:%M +04")
            return self._json(book)

        if path == "/ideas" or path == "/ideas.html":
            self.path = "/ideas.html"
            return super().do_GET()

        if path == "/history" or path == "/history.html":
            self.path = "/history.html"
            return super().do_GET()
        if path in ("/fx", "/fx.html"):
            self.path = "/fx.html"
            return super().do_GET()
        if path in ("/fx-history", "/fx-history.html"):
            self.path = "/fx-history.html"
            return super().do_GET()

        if path == "/" or path == "/intro.html":
            self.path = "/intro.html"
            return super().do_GET()
        if path == "/index.html" or path == "/screen":
            self.path = "/index.html"
            return super().do_GET()


def main():
    import sys
    if len(sys.argv) > 1 and sys.argv[1] in ("--export", "export"):
        extra = [s.strip().upper() for s in sys.argv[2:] if s.strip()]
        export_universe(extra or WATCHLIST)
        return
    if len(sys.argv) > 1 and sys.argv[1] in ("--from-etfs", "etfs"):
        export_from_etfs()
        return
    port = int(os.environ.get("PORT", "8787"))
    print(f"Halal screener → http://127.0.0.1:{port}")
    snap = load_universe()
    if snap:
        print("Weekly table:", snap.get("created_at"), "rows", snap.get("count"))
    refresh_etf_universe_async(force=not bool(snap))
    def loop():
        while True:
            time.sleep(24 * 3600)
            refresh_etf_universe_async(False)
    threading.Thread(target=loop, daemon=True).start()
    print("Warming SEC ticker map…")
    try:
        n = len(load_tickers())
        print(f"Loaded {n} SEC tickers.")
    except Exception as e:
        print("Ticker map warm failed (will retry on first request):", e)
    httpd = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    httpd.serve_forever()


if __name__ == "__main__":
    main()
