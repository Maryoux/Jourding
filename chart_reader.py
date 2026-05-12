"""
chart_reader.py
───────────────
Uses NVIDIA Nemotron Nano 12B 2 VL (free) via OpenRouter to automatically
extract Entry, SL, and TP price levels from a trading chart screenshot.

Key fixes vs previous version:
  1. System prompt set to "/no_think" — suppresses Nemotron's reasoning trace
     so output goes to content instead of the reasoning field (which was None).
  2. All JSON instructions moved into the user message — more reliable than
     system prompt for this model family.
  3. Regex price extractor as last-resort fallback when the model writes prose
     instead of JSON (e.g. "entry at 84500, stop at 82000").
  4. Model order: Nano Omni first (more instruction-following), 12B VL second.

Cost: $0 — both models are free on OpenRouter.
"""

import base64
import json
import logging
import re
import requests

import config

log = logging.getLogger(__name__)

OPENROUTER_API = "https://openrouter.ai/api/v1/chat/completions"

# Nano Omni follows instructions more reliably; 12B VL has better chart OCR
MODEL_ORDER = [
    "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free",
    "nvidia/nemotron-nano-12b-v2-vl:free",
]

# "/no_think" tells Nemotron reasoning models to skip the chain-of-thought trace
# and write the answer directly to `content` (not the reasoning field).
NO_THINK_SYSTEM = "/no_think"

USER_PROMPT = """Look at this trading chart image carefully.

Find the horizontal lines or labels marking:
- Entry price  (labelled Entry, entry, open, or a green/blue line)
- Stop Loss    (labelled SL, Stop, stop loss, or a red line below entry for longs)
- Take Profit  (labelled TP, Target, T1, or a green line above entry for longs)

Read the exact numbers from the Y-axis (price axis) for each level.

Respond with ONLY this JSON — no explanation, no markdown, no backticks:
{"entry": <number or null>, "sl": <number or null>, "tp": <number or null>, "confidence": "<high|medium|low>"}

Examples:
{"entry": 84500.00, "sl": 82000.00, "tp": 88000.00, "confidence": "high"}
{"entry": 1.08540, "sl": 1.08200, "tp": null, "confidence": "medium"}
{"entry": null, "sl": null, "tp": null, "confidence": "low"}"""


# ── Public API ────────────────────────────────────────────────────────────────

def extract_levels(image_bytes: bytes) -> dict:
    """
    Send a chart screenshot to Nemotron via OpenRouter and return
    detected Entry / SL / TP price levels.

    Returns:
        {"entry": float|None, "sl": float|None, "tp": float|None,
         "confidence": "high"|"medium"|"low", "error": str|None}
    """
    if not config.OPENROUTER_API_KEY:
        return _empty("OPENROUTER_API_KEY not configured")
    if not image_bytes:
        return _empty("No image bytes provided")

    media_type = _detect_media_type(image_bytes)
    b64_image  = base64.standard_b64encode(image_bytes).decode()
    data_url   = f"data:{media_type};base64,{b64_image}"

    for model in MODEL_ORDER:
        result = _call_openrouter(model, data_url)
        if result.get("error") and "rate" in result["error"].lower():
            log.warning(f"chart_reader: {model} rate-limited, trying next…")
            continue
        return result

    return _empty("All models unavailable — try again shortly")


# ── Internal ──────────────────────────────────────────────────────────────────

def _call_openrouter(model: str, data_url: str) -> dict:
    headers = {
        "Authorization": f"Bearer {config.OPENROUTER_API_KEY}",
        "Content-Type":  "application/json",
        "HTTP-Referer":  "https://github.com/Maryoux/Jourding",
        "X-Title":       "Jourding Trading Journal Bot",
    }

    payload = {
        "model":       model,
        "max_tokens":  300,
        "temperature": 0,          # deterministic — better for structured output
        "messages": [
            # "/no_think" suppresses the reasoning trace on Nemotron models,
            # forcing the answer into content instead of the reasoning field.
            {"role": "system", "content": NO_THINK_SYSTEM},
            {
                "role": "user",
                "content": [
                    # Image first — Nemotron VL attends better to image when it comes first
                    {
                        "type":      "image_url",
                        "image_url": {"url": data_url},
                    },
                    {
                        "type": "text",
                        "text": USER_PROMPT,
                    },
                ],
            },
        ],
    }

    try:
        r = requests.post(OPENROUTER_API, headers=headers, json=payload, timeout=60)

        if r.status_code == 429:
            log.warning(f"chart_reader: {model} 429 rate-limited")
            return _empty("rate limited")

        r.raise_for_status()
        data = r.json()
        log.debug(f"chart_reader [{model}] raw response keys: {list(data.keys())}")

        if "error" in data:
            msg = data["error"].get("message", str(data["error"]))
            log.error(f"chart_reader OpenRouter error ({model}): {msg}")
            return _empty(f"OpenRouter: {msg}")

        raw_text = _extract_text(data, model)
        if not raw_text:
            return _empty("Empty response from model")

        log.info(f"chart_reader [{model}] text: {raw_text[:300]}")
        return _parse_response(raw_text)

    except requests.HTTPError as e:
        log.error(f"chart_reader HTTP {e.response.status_code} ({model}): {e.response.text[:300]}")
        return _empty(f"HTTP {e.response.status_code}")
    except requests.Timeout:
        log.error(f"chart_reader timeout ({model})")
        return _empty("Request timed out")
    except Exception as e:
        log.error(f"chart_reader exception ({model}): {e}")
        return _empty(str(e))


def _extract_text(data: dict, model: str) -> str | None:
    """
    Safely pull the assistant's reply from an OpenRouter response.

    Priority order:
      1. message.content  (string)      — normal case with /no_think
      2. message.content  (list/blocks) — some multimodal models
      3. message.reasoning               — fallback if /no_think was ignored
      4. reasoning_details[].content    — OpenRouter extended reasoning format
    """
    try:
        choices = data.get("choices") or []
        if not choices:
            log.warning(f"chart_reader [{model}]: no choices in response")
            return None

        message = choices[0].get("message") or {}

        # 1. Normal string content
        content = message.get("content")
        if isinstance(content, str) and content.strip():
            return content.strip()

        # 2. Content as list of blocks
        if isinstance(content, list):
            parts = [
                block.get("text", "")
                for block in content
                if isinstance(block, dict) and block.get("type") == "text"
            ]
            joined = " ".join(parts).strip()
            if joined:
                return joined

        # 3. reasoning field (present when /no_think was partially ignored)
        reasoning = message.get("reasoning")
        if isinstance(reasoning, str) and reasoning.strip():
            log.info(f"chart_reader [{model}]: content was None, using reasoning field")
            return reasoning.strip()

        # 4. reasoning_details array (OpenRouter extended format)
        details = data.get("reasoning_details") or []
        for d in details:
            if isinstance(d, dict):
                text = d.get("content") or d.get("text", "")
                if text.strip():
                    log.info(f"chart_reader [{model}]: using reasoning_details")
                    return text.strip()

        log.warning(
            f"chart_reader [{model}]: no usable text found. "
            f"message keys: {list(message.keys())}"
        )
        return None

    except (IndexError, KeyError, TypeError) as e:
        log.error(f"chart_reader _extract_text error ({model}): {e}")
        return None


def _parse_response(text: str) -> dict:
    """
    Extract Entry/SL/TP from the model's response.

    Strategy:
      1. Find and parse a JSON object  { ... }
      2. If no JSON found, run regex price extraction on prose text
         (e.g. "entry at 84500, stop loss at 82000, take profit 88000")
    """
    # Strip markdown fences
    clean = re.sub(r"```(?:json)?", "", text).strip().rstrip("`").strip()

    # ── Pass 1: JSON ─────────────────────────────────────────────────────────
    match = re.search(r"\{.*\}", clean, re.DOTALL)
    if not match:
        match = re.search(r"\{[^{}]+\}", clean)

    if match:
        try:
            parsed = json.loads(match.group())
            return {
                "entry":      _to_float(parsed.get("entry")),
                "sl":         _to_float(parsed.get("sl")),
                "tp":         _to_float(parsed.get("tp")),
                "confidence": parsed.get("confidence", "medium"),
                "error":      None,
            }
        except json.JSONDecodeError:
            pass  # fall through to regex pass

    # ── Pass 2: Regex price extraction from prose ─────────────────────────────
    log.info("chart_reader: no JSON found, attempting regex price extraction from prose")
    result = _regex_extract(clean)
    if result:
        return result

    log.warning(f"chart_reader: could not extract prices from: {text[:300]}")
    return _empty("No prices found in response")


# price pattern: optional currency/symbol, digits, optional decimal
_PRICE_PAT = r"[\$]?(\d[\d,]*(?:\.\d+)?)"

# keyword groups for each level
_ENTRY_KEYS = r"entr(?:y|ance)|open(?:ed)?|long\s+at|short\s+at|position\s+at|buy\s+at|sell\s+at"
_SL_KEYS    = r"s(?:top[\s_-]?l(?:oss)?|\.?l\.?)|stop|risk|invalidation"
_TP_KEYS    = r"t(?:ake[\s_-]?p(?:rofit)?|\.?p\.?|arget)|profit|reward|objective|t1"


def _regex_extract(text: str) -> dict | None:
    """
    Scan prose for price numbers near entry/sl/tp keywords.
    Returns a result dict or None if nothing useful is found.
    """
    t = text.lower()

    def find_price(keyword_pattern: str) -> float | None:
        # Look for keyword followed by price within ~30 chars
        pat = rf"(?:{keyword_pattern})\D{{0,30}}?{_PRICE_PAT}"
        m = re.search(pat, t)
        if m:
            return _to_float(m.group(1).replace(",", ""))
        return None

    entry = find_price(_ENTRY_KEYS)
    sl    = find_price(_SL_KEYS)
    tp    = find_price(_TP_KEYS)

    found_any = any(v is not None for v in (entry, sl, tp))
    if not found_any:
        return None

    # Confidence based on how many we found
    found_count = sum(1 for v in (entry, sl, tp) if v is not None)
    confidence  = "medium" if found_count >= 2 else "low"

    log.info(
        f"chart_reader regex extracted — "
        f"entry={entry}, sl={sl}, tp={tp}, confidence={confidence}"
    )
    return {
        "entry":      entry,
        "sl":         sl,
        "tp":         tp,
        "confidence": confidence,
        "error":      None,
    }


def _to_float(v) -> float | None:
    try:
        return float(str(v).replace(",", "")) if v is not None else None
    except (TypeError, ValueError):
        return None


def _empty(reason: str) -> dict:
    return {"entry": None, "sl": None, "tp": None, "confidence": "low", "error": reason}


def _detect_media_type(data: bytes) -> str:
    if data[:8] == b"\x89PNG\r\n\x1a\n":     return "image/png"
    if data[:3] == b"\xff\xd8\xff":           return "image/jpeg"
    if data[:6] in (b"GIF87a", b"GIF89a"):   return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP": return "image/webp"
    return "image/jpeg"


# ── Formatting helper (used by handlers.py) ───────────────────────────────────

def format_detected(levels: dict) -> str:
    def fmtv(v):
        if v is None: return "—"
        v = float(v)
        return f"{v:.2f}" if v >= 100 else f"{v:.5f}".rstrip("0").rstrip(".")

    conf_emoji = {"high": "🟢", "medium": "🟡", "low": "🔴"}.get(
        levels.get("confidence", "low"), "🔴"
    )
    return (
        f"Entry: `{fmtv(levels.get('entry'))}`  "
        f"|  SL: `{fmtv(levels.get('sl'))}`  "
        f"|  TP: `{fmtv(levels.get('tp'))}`\n"
        f"{conf_emoji} {levels.get('confidence','low').capitalize()} confidence"
    )
