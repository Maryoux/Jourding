"""
notion_client.py
────────────────
Notion API: create open trades, close them with exit data, query stats.
"""

import base64
import logging
import requests
from datetime import date
from typing import Optional

import config
from parser import Trade

log = logging.getLogger(__name__)

API     = "https://api.notion.com/v1"
VERSION = "2022-06-28"

def _headers():
    return {
        "Authorization":  f"Bearer {config.NOTION_TOKEN}",
        "Notion-Version": VERSION,
        "Content-Type":   "application/json",
    }


# ── Screenshot upload ─────────────────────────────────────────────────────────

def upload_image_to_imgbb(image_bytes: bytes) -> Optional[str]:
    if not config.IMGBB_API_KEY:
        log.warning("IMGBB_API_KEY not set — screenshot will not be saved.")
        return None
    try:
        b64 = base64.b64encode(image_bytes).decode()
        r = requests.post(
            "https://api.imgbb.com/1/upload",
            data={"key": config.IMGBB_API_KEY, "image": b64},
            timeout=30,
        )
        r.raise_for_status()
        url = r.json()["data"]["url"]
        log.info(f"Screenshot uploaded → {url}")
        return url
    except requests.HTTPError as e:
        log.error(f"imgbb {e.response.status_code}: {e.response.text[:200]}")
        return None
    except Exception as e:
        log.error(f"imgbb upload failed: {e}")
        return None


# ── Property helpers ──────────────────────────────────────────────────────────

def _select(val: str) -> dict:
    return {"select": {"name": val}} if val else {"select": None}

def _number(val) -> dict:
    return {"number": float(val)} if val is not None else {"number": None}

def _text(val: str) -> dict:
    return {"rich_text": [{"text": {"content": val}}]} if val else {"rich_text": []}

def _title(val: str) -> dict:
    return {"title": [{"text": {"content": val}}]}

def _date(val: str) -> dict:
    return {"date": {"start": val}}


# ── Image blocks helper ───────────────────────────────────────────────────────

def _image_block(url: str) -> dict:
    return {
        "object": "block",
        "type":   "image",
        "image":  {"type": "external", "external": {"url": url}},
    }

def _heading_block(text: str) -> dict:
    return {
        "object":    "block",
        "type":      "heading_3",
        "heading_3": {"rich_text": [{"text": {"content": text}}]},
    }


# ── Create open trade (Before photo) ─────────────────────────────────────────

def create_open_trade(trade: Trade, before_image_url: Optional[str] = None) -> dict:
    """
    Creates a Notion page with Status = Open.
    Called when the user logs a Before (entry) chart.
    """
    today = date.today().isoformat()

    props = {
        "Symbol":        _title(trade.symbol),
        "Direction":     _select(trade.dir),
        "Session":       _select(trade.session),
        "Setup":         _select(trade.setup),
        "Grade":         _select(trade.grade),
        "Emotion":       _select(trade.emotion),
        "Date":          _date(today),
        "Status":        _select("Open"),
        "Notes":         _text(trade.note),
        "Lessons Learned": _text(trade.lesson),
    }

    if trade.entry:
        props["Entry"] = _number(trade.entry)

    payload: dict = {
        "parent":     {"database_id": config.NOTION_DATABASE_ID},
        "properties": props,
    }

    blocks = []
    if before_image_url:
        payload["cover"] = {"type": "external", "external": {"url": before_image_url}}
        blocks.append(_heading_block("📊 Before — Entry Chart"))
        blocks.append(_image_block(before_image_url))

    if blocks:
        payload["children"] = blocks

    r = requests.post(f"{API}/pages", headers=_headers(), json=payload, timeout=15)
    r.raise_for_status()
    return r.json()


# ── Close trade (After photo) ─────────────────────────────────────────────────

def close_trade(
    page_id:   str,
    result:    str,
    exit_:     Optional[float] = None,
    pnl:       Optional[float] = None,
    rr:        Optional[float] = None,
    image_url: Optional[str]   = None,
) -> dict:
    """
    Updates an existing Notion page:
    - Sets Status → Closed
    - Adds Result, Exit, PnL, RR
    - Appends the After chart screenshot as a new block
    """
    # ── Update properties ─────────────────────────────────────────────────────
    props: dict = {
        "Status": _select("Closed"),
        "Result": _select(result),
    }
    if exit_ is not None: props["Exit"] = _number(exit_)
    if pnl   is not None: props["PnL"]  = _number(pnl)
    if rr    is not None: props["RR"]   = _number(rr)

    r = requests.patch(
        f"{API}/pages/{page_id}",
        headers=_headers(),
        json={"properties": props},
        timeout=15,
    )
    r.raise_for_status()

    # ── Append After screenshot block ─────────────────────────────────────────
    if image_url:
        blocks = [
            _heading_block("🏁 After — Exit Chart"),
            _image_block(image_url),
        ]
        requests.patch(
            f"{API}/blocks/{page_id}/children",
            headers=_headers(),
            json={"children": blocks},
            timeout=15,
        ).raise_for_status()

    return r.json()


# ── Query open trades ─────────────────────────────────────────────────────────

def query_open_trades() -> list[dict]:
    """
    Returns a list of open trades from Notion, simplified for display.
    """
    body = {
        "filter": {
            "property": "Status",
            "select":   {"equals": "Open"},
        },
        "sorts": [{"property": "Date", "direction": "descending"}],
        "page_size": 20,
    }
    r = requests.post(
        f"{API}/databases/{config.NOTION_DATABASE_ID}/query",
        headers=_headers(),
        json=body,
        timeout=15,
    )
    r.raise_for_status()

    results = []
    for page in r.json().get("results", []):
        props  = page.get("properties", {})
        title  = props.get("Symbol", {}).get("title", [])
        symbol = title[0].get("plain_text", "?") if title else "?"
        dir_   = (props.get("Direction", {}).get("select") or {}).get("name", "")
        entry  = (props.get("Entry",     {}).get("number"))
        dt     = (props.get("Date",      {}).get("date")  or {}).get("start", "")
        sl     = (props.get("Stop Loss", {}).get("number"))
        tp     = (props.get("Take Profit",{}).get("number"))

        results.append({
            "id":     page["id"],
            "symbol": symbol,
            "dir":    dir_,
            "entry":  entry,
            "sl":     sl,
            "tp":     tp,
            "date":   dt,
        })

    return results


# ── Query all closed trades + stats ──────────────────────────────────────────

def query_all_trades(page_size: int = 100) -> list[dict]:
    trades = []
    cursor = None
    while True:
        body: dict = {
            "filter":    {"property": "Status", "select": {"equals": "Closed"}},
            "page_size": page_size,
        }
        if cursor:
            body["start_cursor"] = cursor
        r = requests.post(
            f"{API}/databases/{config.NOTION_DATABASE_ID}/query",
            headers=_headers(),
            json=body,
            timeout=15,
        )
        r.raise_for_status()
        data    = r.json()
        trades += data.get("results", [])
        if not data.get("has_more"):
            break
        cursor = data.get("next_cursor")
    return trades


def _prop_num(props: dict, key: str) -> Optional[float]:
    return props.get(key, {}).get("number")

def _prop_sel(props: dict, key: str) -> str:
    s = props.get(key, {}).get("select")
    return s["name"] if s else ""


def get_stats() -> dict:
    pages = query_all_trades()
    total = len(pages)
    if total == 0:
        return {
            "total": 0, "wins": 0, "losses": 0, "be": 0,
            "win_rate": 0, "net_pnl": 0, "profit_factor": 0,
            "avg_rr": 0, "best": None, "worst": None,
        }

    wins = losses = be = 0
    gross_win = gross_loss = pnl_total = 0.0
    rr_values = []
    trade_pnls = []

    for p in pages:
        props  = p.get("properties", {})
        result = _prop_sel(props, "Result")
        pnl    = _prop_num(props, "PnL") or 0
        rr     = _prop_num(props, "RR")
        title  = props.get("Symbol", {}).get("title", [])
        sym    = title[0].get("plain_text", "?") if title else "?"

        if result == "Win":
            wins += 1;      gross_win  += pnl
        elif result == "Loss":
            losses += 1;    gross_loss += abs(pnl)
        else:
            be += 1

        pnl_total += pnl
        trade_pnls.append((sym, pnl))
        if rr is not None:
            rr_values.append(rr)

    win_rate      = wins / total * 100 if total else 0
    profit_factor = (gross_win / gross_loss) if gross_loss > 0 else (float("inf") if gross_win > 0 else 0)
    avg_rr        = sum(rr_values) / len(rr_values) if rr_values else 0

    return {
        "total":         total,
        "wins":          wins,
        "losses":        losses,
        "be":            be,
        "win_rate":      round(win_rate, 1),
        "net_pnl":       round(pnl_total, 4),
        "profit_factor": round(profit_factor, 2),
        "avg_rr":        round(avg_rr, 2),
        "best":          max(trade_pnls, key=lambda x: x[1]),
        "worst":         min(trade_pnls, key=lambda x: x[1]),
    }


# ── Legacy (kept for backward compat) ────────────────────────────────────────

def create_trade_page(trade: Trade, image_url: Optional[str] = None) -> dict:
    return create_open_trade(trade, image_url)
