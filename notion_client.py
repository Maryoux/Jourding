"""
notion_client_v3.py
────────────────────
Notion API wrapper — v3 schema.

Key changes:
  - Setup → multi_select
  - SL / TP properly pushed on create
  - Exit auto-filled from Result (Win=TP, Loss=SL, BE=Entry)
  - No PnL ($) — RR is calculated by Notion formulas
  - Removed: Position Size, Pip Value, Mistakes
"""

import base64
import logging
import requests
from datetime import date
from typing import Optional

import config

log = logging.getLogger(__name__)

API     = "https://api.notion.com/v1"
VERSION = "2022-06-28"


def _headers():
    return {
        "Authorization":  f"Bearer {config.NOTION_TOKEN}",
        "Notion-Version": VERSION,
        "Content-Type":   "application/json",
    }


# ── Screenshot ────────────────────────────────────────────────────────────────

def upload_image_to_imgbb(image_bytes: bytes) -> Optional[str]:
    if not config.IMGBB_API_KEY:
        log.warning("IMGBB_API_KEY not set — screenshot will not be saved to Notion.")
        return None
    try:
        b64 = base64.b64encode(image_bytes).decode()
        r   = requests.post(
            "https://api.imgbb.com/1/upload",
            data={"key": config.IMGBB_API_KEY, "image": b64},
            timeout=30,
        )
        r.raise_for_status()
        url = r.json()["data"]["url"]
        log.info(f"Screenshot → {url}")
        return url
    except requests.HTTPError as e:
        log.error(f"imgbb {e.response.status_code}: {e.response.text[:200]}")
    except Exception as e:
        log.error(f"imgbb upload failed: {e}")
    return None


# ── Property helpers ──────────────────────────────────────────────────────────

def _title(v: str)  -> dict: return {"title":     [{"text": {"content": v}}]}
def _text(v: str)   -> dict: return {"rich_text":  [{"text": {"content": v}}]} if v else {"rich_text": []}
def _select(v: str) -> dict: return {"select":     {"name": v}} if v else {"select": None}
def _date(v: str)   -> dict: return {"date":       {"start": v}}

def _number(v) -> dict:
    return {"number": float(v)} if v is not None else {"number": None}

def _multi_select(values: list[str]) -> dict:
    """Accepts a list of setup names — creates options on the fly in Notion."""
    return {"multi_select": [{"name": v} for v in values if v]}

def _image_block(url: str) -> dict:
    return {"object": "block", "type": "image",
            "image": {"type": "external", "external": {"url": url}}}

def _h3(text: str) -> dict:
    return {"object": "block", "type": "heading_3",
            "heading_3": {"rich_text": [{"text": {"content": text}}]}}


# ── Auto-fill exit from result ────────────────────────────────────────────────

def resolve_exit(result: str, entry: Optional[float],
                 sl: Optional[float], tp: Optional[float]) -> Optional[float]:
    """
    Win  → Exit = TP
    Loss → Exit = SL
    BE   → Exit = Entry
    """
    if result == "Win"  and tp    is not None: return tp
    if result == "Loss" and sl    is not None: return sl
    if result == "BE"   and entry is not None: return entry
    return None


# ── Create open trade (Before photo) ─────────────────────────────────────────

def create_open_trade(
    symbol:    str,
    direction: str,
    session:   str,
    setups:    list[str],
    emotion:   str,
    grade:     str,
    entry:     Optional[float],
    sl:        Optional[float],
    tp:        Optional[float],
    notes:     str = "",
    before_image_url: Optional[str] = None,
) -> dict:
    today = date.today().isoformat()

    props = {
        "Symbol":    _title(symbol),
        "Date":      _date(today),
        "Status":    _select("Open"),
        "Direction": _select(direction),
        "Session":   _select(session),
        "Setup":     _multi_select(setups),
        "Emotion":   _select(emotion),
        "Grade":     _select(grade),
        "Entry":     _number(entry),
        "SL":        _number(sl),
        "TP":        _number(tp),
    }
    if notes:
        props["Notes"] = _text(notes)

    payload: dict = {
        "parent":     {"database_id": config.NOTION_DATABASE_ID},
        "properties": props,
    }

    blocks = []
    if before_image_url:
        payload["cover"] = {"type": "external", "external": {"url": before_image_url}}
        blocks += [_h3("📊 Before — Entry Chart"), _image_block(before_image_url)]
    if blocks:
        payload["children"] = blocks

    r = requests.post(f"{API}/pages", headers=_headers(), json=payload, timeout=15)
    r.raise_for_status()
    return r.json()


# ── Close trade (After photo) ─────────────────────────────────────────────────

def close_trade(
    page_id:   str,
    result:    str,
    entry:     Optional[float] = None,
    sl:        Optional[float] = None,
    tp:        Optional[float] = None,
    after_image_url: Optional[str] = None,
) -> dict:
    """
    Closes a trade:
      - Sets Status → Closed, Result
      - Auto-fills Exit (Win=TP, Loss=SL, BE=Entry)
      - Appends After chart screenshot block
    """
    exit_price = resolve_exit(result, entry, sl, tp)

    props: dict = {
        "Status": _select("Closed"),
        "Result": _select(result),
        "Exit":   _number(exit_price),
    }

    r = requests.patch(
        f"{API}/pages/{page_id}",
        headers=_headers(),
        json={"properties": props},
        timeout=15,
    )
    r.raise_for_status()

    # Append After screenshot block to the page body
    if after_image_url:
        blocks = [_h3("🏁 After — Exit Chart"), _image_block(after_image_url)]
        requests.patch(
            f"{API}/blocks/{page_id}/children",
            headers=_headers(),
            json={"children": blocks},
            timeout=15,
        ).raise_for_status()

    return r.json()


# ── Query open trades ─────────────────────────────────────────────────────────

def query_open_trades() -> list[dict]:
    body = {
        "filter": {"property": "Status", "select": {"equals": "Open"}},
        "sorts":  [{"property": "Date", "direction": "descending"}],
        "page_size": 20,
    }
    r = requests.post(
        f"{API}/databases/{config.NOTION_DATABASE_ID}/query",
        headers=_headers(), json=body, timeout=15,
    )
    r.raise_for_status()

    results = []
    for page in r.json().get("results", []):
        props = page.get("properties", {})

        title  = props.get("Symbol", {}).get("title", [])
        symbol = title[0].get("plain_text", "?") if title else "?"
        dir_   = (props.get("Direction", {}).get("select") or {}).get("name", "")
        entry  = props.get("Entry", {}).get("number")
        sl     = props.get("SL",    {}).get("number")
        tp     = props.get("TP",    {}).get("number")
        dt     = (props.get("Date", {}).get("date") or {}).get("start", "")

        # Multi-select setups
        setups = [s["name"] for s in props.get("Setup", {}).get("multi_select", [])]

        results.append({
            "id": page["id"], "symbol": symbol, "dir": dir_,
            "entry": entry, "sl": sl, "tp": tp,
            "date": dt, "setups": setups,
        })

    return results


# ── Stats ─────────────────────────────────────────────────────────────────────

def query_closed_trades(page_size: int = 100) -> list[dict]:
    trades, cursor = [], None
    while True:
        body: dict = {
            "filter":    {"property": "Status", "select": {"equals": "Closed"}},
            "page_size": page_size,
        }
        if cursor:
            body["start_cursor"] = cursor
        r = requests.post(
            f"{API}/databases/{config.NOTION_DATABASE_ID}/query",
            headers=_headers(), json=body, timeout=15,
        )
        r.raise_for_status()
        data    = r.json()
        trades += data.get("results", [])
        if not data.get("has_more"):
            break
        cursor = data.get("next_cursor")
    return trades


def get_stats() -> dict:
    pages = query_closed_trades()
    total = len(pages)
    if total == 0:
        return {"total": 0, "wins": 0, "losses": 0, "be": 0,
                "win_rate": 0, "avg_rr": 0, "best": None, "worst": None}

    wins = losses = be = 0
    rr_values, trade_rrs = [], []

    for p in pages:
        props  = p.get("properties", {})
        result = (props.get("Result", {}).get("select") or {}).get("name", "")
        # Actual RR comes from the Notion formula — read as formula result
        actual_rr_prop = props.get("Actual RR", {})
        rr = actual_rr_prop.get("formula", {}).get("number")

        title = props.get("Symbol", {}).get("title", [])
        sym   = title[0].get("plain_text", "?") if title else "?"

        if result == "Win":   wins   += 1
        elif result == "Loss": losses += 1
        else:                  be     += 1

        if rr is not None:
            rr_values.append(rr)
            trade_rrs.append((sym, rr))

    win_rate = wins / total * 100 if total else 0
    avg_rr   = sum(rr_values) / len(rr_values) if rr_values else 0

    return {
        "total":    total,
        "wins":     wins,
        "losses":   losses,
        "be":       be,
        "win_rate": round(win_rate, 1),
        "avg_rr":   round(avg_rr, 2),
        "best":     max(trade_rrs, key=lambda x: x[1]) if trade_rrs else None,
        "worst":    min(trade_rrs, key=lambda x: x[1]) if trade_rrs else None,
    }
