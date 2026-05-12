"""
handlers.py
───────────
Key additions vs previous version:
  - chart_reader integration: after a Before photo is received, Claude vision
    automatically detects Entry, SL, and TP from the chart.
  - New CONFIRM_PRICES state: shows detected levels with three options:
      ✅ Use these  →  skips manual entry, saves immediately
      ✏️ Edit       →  proceeds to the normal manual entry flow (pre-filled)
      ⏭️ Skip all   →  saves with no prices (original "skip everything" path)
  - If ANTHROPIC_API_KEY is not set, chart_reader is bypassed and the
    original manual flow runs unchanged.
  - All existing behaviour (multi-select setups, custom setups, After flow,
    /stats, /open, /cancel) is preserved exactly.
"""

import json
import logging
import os
import requests
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ContextTypes, ConversationHandler, CommandHandler,
    MessageHandler, CallbackQueryHandler, filters,
)

import config
import notion_client as notion

# Import chart_reader only if the API key is available
try:
    import chart_reader as cr
    _CHART_READER_AVAILABLE = bool(config.OPENROUTER_API_KEY)
except ImportError:
    _CHART_READER_AVAILABLE = False

log = logging.getLogger(__name__)

# ── Custom setups persistence ─────────────────────────────────────────────────
CUSTOM_FILE = "custom_setups.json"

def load_custom() -> list[str]:
    if os.path.exists(CUSTOM_FILE):
        try:
            with open(CUSTOM_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return []

def save_custom(name: str):
    data = load_custom()
    if name not in data:
        data.append(name)
        with open(CUSTOM_FILE, "w") as f:
            json.dump(data, f)

# ── States ────────────────────────────────────────────────────────────────────
(
    PHOTO_TYPE,
    PAIR, DIRECTION, SESSION,
    SETUP_PICK, CUSTOM_SETUP,
    EMOTION, GRADE,
    CONFIRM_PRICES,             # ← NEW: auto-detected price confirmation
    ENTRY_PRICE, SL_PRICE, TP_PRICE,
    PICK_TRADE, RESULT,
) = range(14)

# ── Options ───────────────────────────────────────────────────────────────────
PAIRS = [
    ["XAUUSD", "XAGUSD"],
    ["EURUSD", "GBPUSD", "USDJPY"],
    ["AUDUSD", "USDCAD", "NZDUSD"],
    ["US30", "NAS100", "SP500"],
    ["BTCUSD", "ETHUSD"],
    ["OTHER"],
]
DIRECTIONS = [["Long", "Short"]]
SESSIONS   = [["Asia", "London"], ["New York", "Overlap"]]
EMOTIONS   = [
    ["Calm", "Confident"],
    ["FOMO", "Fearful"],
    ["Greedy", "Revenge"],
    ["Bored"],
]
GRADES  = [["A - Perfect", "B - Good", "C - Messy", "Skip"]]
RESULTS = [["Win", "Loss", "Break Even"]]
BASE_SETUPS = ["SMT", "IRL-ERL", "ERL-IRL", "Breakout", "Retest",
               "Reversal", "Trend", "Range", "News"]

EMOJIS = {
    "Long": "📈", "Short": "📉",
    "Win": "✅", "Loss": "❌", "Break Even": "➖",
    "Asia": "🌏", "London": "🇬🇧", "New York": "🗽", "Overlap": "⚡",
    "Calm": "😌", "Confident": "💪", "FOMO": "😰", "Fearful": "😨",
    "Greedy": "🤑", "Revenge": "😤", "Bored": "😑",
    "A - Perfect": "💎", "B - Good": "👍", "C - Messy": "😅", "Skip": "⏭️",
    "SMT": "🔄", "IRL-ERL": "📐", "ERL-IRL": "📐",
}

def lbl(v: str) -> str:
    e = EMOJIS.get(v, "")
    return f"{v} {e}" if e else v

def kb(options: list[list[str]], prefix: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(lbl(v), callback_data=f"{prefix}:{v}") for v in row]
        for row in options
    ])

def skip_kb(prefix: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("Skip ⏭️", callback_data=f"{prefix}:skip")
    ]])

def val(cb: str) -> str:
    return cb.split(":", 1)[1]

def setup_kb(selected: list[str]) -> InlineKeyboardMarkup:
    all_setups = BASE_SETUPS + [s for s in load_custom() if s not in BASE_SETUPS]
    rows = []
    pair = []
    for s in all_setups:
        tick  = "✓ " if s in selected else ""
        label = f"{tick}{lbl(s)}"
        pair.append(InlineKeyboardButton(label, callback_data=f"setup:{s}"))
        if len(pair) == 2:
            rows.append(pair); pair = []
    if pair:
        rows.append(pair)
    rows.append([
        InlineKeyboardButton("✏️ Type my own", callback_data="setup:__custom__"),
        InlineKeyboardButton("✅ Done",         callback_data="setup:__done__"),
    ])
    return InlineKeyboardMarkup(rows)

def summary(d: dict) -> str:
    parts = []
    if d.get("symbol"):  parts.append(f"*{d['symbol']}*")
    if d.get("dir"):     parts.append(d["dir"])
    if d.get("session"): parts.append(d["session"])
    setups = d.get("setups", [])
    if setups: parts.append("+".join(setups))
    return " | ".join(parts)

# ── Auth ──────────────────────────────────────────────────────────────────────
def authorized(update: Update) -> bool:
    if config.ALLOWED_USER_ID == 0: return True
    return update.effective_user.id == config.ALLOWED_USER_ID

# ── Price formatting helper ───────────────────────────────────────────────────
def _fmt_price(v) -> str:
    if v is None:
        return "—"
    v = float(v)
    return f"{v:.2f}" if v >= 100 else f"{v:.5f}".rstrip("0").rstrip(".")

# ══════════════════════════════════════════════════════════════════════════════
# STEP 0 — Photo received
# ══════════════════════════════════════════════════════════════════════════════
async def receive_photo(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    if not authorized(update): return ConversationHandler.END

    photo = update.message.photo[-1]
    try:
        tg_file   = await ctx.bot.get_file(photo.file_id)
        img_bytes = await tg_file.download_as_bytearray()
        ctx.user_data["photo_bytes"] = bytes(img_bytes)
    except Exception as e:
        log.warning(f"Photo download failed: {e}")
        ctx.user_data["photo_bytes"] = None

    await update.message.reply_text(
        "📸 Chart received!\n\n*Before (entry) or After (exit)?*",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("📊 Before — Entry", callback_data="type:before"),
            InlineKeyboardButton("🏁 After — Exit",   callback_data="type:after"),
        ]]),
    )
    return PHOTO_TYPE

# ══════════════════════════════════════════════════════════════════════════════
# PHOTO TYPE BRANCH
# ══════════════════════════════════════════════════════════════════════════════
async def step_photo_type(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    flow = val(q.data)
    ctx.user_data["flow"] = flow

    if flow == "before":
        await q.edit_message_text(
            "📊 *Before — Entry chart*\n\n*What pair?*",
            parse_mode="Markdown", reply_markup=kb(PAIRS, "pair"),
        )
        return PAIR

    # ── After flow ────────────────────────────────────────────────────────────
    await q.edit_message_text("⏳ Fetching open trades…")
    try:
        open_trades = notion.query_open_trades()
    except Exception as e:
        await q.message.edit_text(f"❌ Could not fetch open trades: {e}")
        return ConversationHandler.END

    if not open_trades:
        await q.message.edit_text(
            "📭 No open trades found.\nLog a *Before* chart first.",
            parse_mode="Markdown",
        )
        return ConversationHandler.END

    ctx.user_data["open_trades"] = {t["id"]: t for t in open_trades}
    buttons = []
    for t in open_trades:
        setup_str = "+".join(t.get("setups", [])) if t.get("setups") else ""
        row_label = f"{t['symbol']} {t['dir']}  {t['date']}"
        if t.get("entry"): row_label += f"  @ {t['entry']}"
        if setup_str:      row_label += f"  [{setup_str}]"
        buttons.append([InlineKeyboardButton(row_label, callback_data=f"pick:{t['id']}")])

    await q.message.edit_text(
        "🏁 *After — Exit chart*\n\nWhich open trade is this closing?",
        parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(buttons),
    )
    return PICK_TRADE

# ══════════════════════════════════════════════════════════════════════════════
# BEFORE FLOW  (B1 → B8)
# ══════════════════════════════════════════════════════════════════════════════

# B1 ── Pair ───────────────────────────────────────────────────────────────────
async def step_pair(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    ctx.user_data["symbol"] = val(q.data)
    await q.edit_message_text(
        f"{summary(ctx.user_data)}\n\n*Direction?*",
        parse_mode="Markdown", reply_markup=kb(DIRECTIONS, "dir"),
    )
    return DIRECTION

# B2 ── Direction ──────────────────────────────────────────────────────────────
async def step_direction(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    ctx.user_data["dir"] = val(q.data)
    await q.edit_message_text(
        f"{summary(ctx.user_data)}\n\n*Session?*",
        parse_mode="Markdown", reply_markup=kb(SESSIONS, "session"),
    )
    return SESSION

# B3 ── Session ────────────────────────────────────────────────────────────────
async def step_session(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    ctx.user_data["session"] = val(q.data)
    ctx.user_data.setdefault("setups", [])
    await q.edit_message_text(
        f"{summary(ctx.user_data)}\n\n"
        "*Setup / Strategy?*\n_Tap all that apply, then ✅ Done_",
        parse_mode="Markdown", reply_markup=setup_kb(ctx.user_data["setups"]),
    )
    return SETUP_PICK

# B4 ── Setup multi-select ─────────────────────────────────────────────────────
async def step_setup_pick(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    q      = update.callback_query
    await q.answer()
    chosen = val(q.data)

    if chosen == "__custom__":
        await q.edit_message_text(
            "✏️ *Type your setup name:*\n\n"
            "_It'll be saved as a button for next time._",
            parse_mode="Markdown",
        )
        return CUSTOM_SETUP

    if chosen == "__done__":
        setups = ctx.user_data.get("setups", [])
        if not setups:
            await q.answer("Pick at least one setup!", show_alert=True)
            return SETUP_PICK
        await q.edit_message_text(
            f"{summary(ctx.user_data)}\n\n*How are you feeling?*",
            parse_mode="Markdown", reply_markup=kb(EMOTIONS, "emotion"),
        )
        return EMOTION

    setups = ctx.user_data.setdefault("setups", [])
    if chosen in setups:
        setups.remove(chosen)
    else:
        setups.append(chosen)

    selected_str = "  ".join(f"✓{s}" for s in setups) if setups else "_none yet_"
    await q.edit_message_text(
        f"{summary(ctx.user_data)}\n\n"
        f"*Setup / Strategy?*\n_Tap all that apply, then ✅ Done_\n\n"
        f"Selected: {selected_str}",
        parse_mode="Markdown", reply_markup=setup_kb(setups),
    )
    return SETUP_PICK

# B4b ── Custom setup typed ────────────────────────────────────────────────────
async def step_custom_setup(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    name = update.message.text.strip()
    if not name:
        await update.message.reply_text("Please type a setup name.")
        return CUSTOM_SETUP

    save_custom(name)
    setups = ctx.user_data.setdefault("setups", [])
    if name not in setups:
        setups.append(name)

    await update.message.reply_text(
        f"✅ *{name}* saved — appears as button next time!\n\n"
        f"{summary(ctx.user_data)}\n\n"
        f"*Setup / Strategy?*\n_Tap all that apply, then ✅ Done_",
        parse_mode="Markdown", reply_markup=setup_kb(setups),
    )
    return SETUP_PICK

# B5 ── Emotion ────────────────────────────────────────────────────────────────
async def step_emotion(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    ctx.user_data["emotion"] = val(q.data)
    await q.edit_message_text(
        f"Emotion: *{ctx.user_data['emotion']}*\n\n*Grade your entry:*",
        parse_mode="Markdown", reply_markup=kb(GRADES, "grade"),
    )
    return GRADE

# B6 ── Grade → trigger chart reading ─────────────────────────────────────────
async def step_grade(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    raw = val(q.data)
    ctx.user_data["grade"] = "" if raw == "Skip" else raw[0]

    # ── Auto-detect prices if Anthropic key is configured ─────────────────────
    if _CHART_READER_AVAILABLE and ctx.user_data.get("photo_bytes"):
        await q.edit_message_text(
            f"{summary(ctx.user_data)}\n\n"
            "🔍 *Analysing chart for Entry / SL / TP…*",
            parse_mode="Markdown",
        )
        levels = cr.extract_levels(ctx.user_data["photo_bytes"])
        ctx.user_data["detected"] = levels
        return await _show_confirm_prices(q.message, ctx)

    # ── Fallback: manual entry flow ───────────────────────────────────────────
    await q.edit_message_text(
        f"{summary(ctx.user_data)}\n\n"
        "*Entry price?* (optional)\n\nType e.g. `2340.50` or skip:",
        parse_mode="Markdown", reply_markup=skip_kb("entry"),
    )
    return ENTRY_PRICE


# ══════════════════════════════════════════════════════════════════════════════
# CONFIRM_PRICES  (new state — only reached when chart_reader is active)
# ══════════════════════════════════════════════════════════════════════════════

async def _show_confirm_prices(bot_msg, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Render the auto-detected price confirmation screen.
    Three options:
      ✅ Use these  — accept and save immediately
      ✏️ Edit       — enter manual flow (pre-filled with detected values)
      ⏭️ Skip all   — save with no prices
    """
    levels = ctx.user_data.get("detected", {})
    d      = ctx.user_data

    entry = levels.get("entry")
    sl    = levels.get("sl")
    tp    = levels.get("tp")
    conf  = levels.get("confidence", "low")
    err   = levels.get("error")

    if err or (entry is None and sl is None and tp is None):
        # Detection failed — fall back to manual entry silently
        await bot_msg.edit_text(
            f"{summary(d)}\n\n"
            "⚠️ _Could not read prices from chart — please enter manually._\n\n"
            "*Entry price?* (optional)\n\nType e.g. `2340.50` or skip:",
            parse_mode="Markdown", reply_markup=skip_kb("entry"),
        )
        return ENTRY_PRICE

    conf_emoji = {"high": "🟢", "medium": "🟡", "low": "🔴"}.get(conf, "🔴")
    conf_label = conf.capitalize()

    detected_lines = (
        f"Entry: `{_fmt_price(entry)}`\n"
        f"SL:    `{_fmt_price(sl)}`\n"
        f"TP:    `{_fmt_price(tp)}`\n"
        f"{conf_emoji} {conf_label} confidence"
    )

    kb_confirm = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Use these",  callback_data="prices:use"),
            InlineKeyboardButton("✏️ Edit",        callback_data="prices:edit"),
        ],
        [
            InlineKeyboardButton("⏭️ Skip prices", callback_data="prices:skip"),
        ],
    ])

    await bot_msg.edit_text(
        f"{summary(d)}\n\n"
        f"🤖 *Detected from chart:*\n\n"
        f"{detected_lines}\n\n"
        f"_Use these prices, edit them, or skip?_",
        parse_mode="Markdown",
        reply_markup=kb_confirm,
    )
    return CONFIRM_PRICES


async def step_confirm_prices(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle the ✅ / ✏️ / ⏭️ choice on the confirm-prices screen."""
    q = update.callback_query
    await q.answer()
    choice = val(q.data)           # "use" | "edit" | "skip"

    levels = ctx.user_data.get("detected", {})

    if choice == "use":
        # Accept detected values, save to Notion immediately
        ctx.user_data["entry"] = levels.get("entry")
        ctx.user_data["sl"]    = levels.get("sl")
        ctx.user_data["tp"]    = levels.get("tp")
        await q.edit_message_text("⏳ Saving to Notion…")
        await _log_before(q.message, ctx)
        return ConversationHandler.END

    if choice == "skip":
        # No prices at all
        ctx.user_data["entry"] = None
        ctx.user_data["sl"]    = None
        ctx.user_data["tp"]    = None
        await q.edit_message_text("⏳ Saving to Notion…")
        await _log_before(q.message, ctx)
        return ConversationHandler.END

    # "edit" — pre-fill user_data with detected values and enter manual flow
    ctx.user_data["entry"] = levels.get("entry")
    ctx.user_data["sl"]    = levels.get("sl")
    ctx.user_data["tp"]    = levels.get("tp")

    entry_display = _fmt_price(ctx.user_data["entry"])
    hint = f"Detected: `{entry_display}`  — type to override or skip:" if ctx.user_data["entry"] else "Type e.g. `2340.50` or skip:"

    await q.edit_message_text(
        f"{summary(ctx.user_data)}\n\n"
        f"*Entry price?* (optional)\n\n{hint}",
        parse_mode="Markdown", reply_markup=skip_kb("entry"),
    )
    return ENTRY_PRICE


# ══════════════════════════════════════════════════════════════════════════════
# MANUAL PRICE ENTRY  (B7 / B8 / B9)
# ══════════════════════════════════════════════════════════════════════════════

# B7 ── Entry price ────────────────────────────────────────────────────────────
async def step_entry_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        ctx.user_data["entry"] = float(
            update.message.text.strip().replace(",", ".").split()[0]
        )
    except ValueError:
        await update.message.reply_text(
            "❌ Not a valid number. Try `2340.50` or tap Skip.",
            parse_mode="Markdown", reply_markup=skip_kb("entry"),
        )
        return ENTRY_PRICE

    sl_hint = _pre_filled_hint("sl", ctx)
    await update.message.reply_text(
        f"Entry: `{_fmt_price(ctx.user_data['entry'])}`\n\n"
        f"*Stop Loss (SL)?* (optional)\n\n{sl_hint}",
        parse_mode="Markdown", reply_markup=skip_kb("sl"),
    )
    return SL_PRICE

async def step_entry_skip(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    # Keep any pre-filled value or set None
    if "entry" not in ctx.user_data:
        ctx.user_data["entry"] = None

    sl_hint = _pre_filled_hint("sl", ctx)
    await q.edit_message_text(
        f"*Stop Loss (SL)?* (optional)\n\n{sl_hint}",
        parse_mode="Markdown", reply_markup=skip_kb("sl"),
    )
    return SL_PRICE

# B8 ── SL ─────────────────────────────────────────────────────────────────────
async def step_sl_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        ctx.user_data["sl"] = float(
            update.message.text.strip().replace(",", ".").split()[0]
        )
    except ValueError:
        await update.message.reply_text(
            "❌ Not valid. Try `2320.00` or tap Skip.",
            parse_mode="Markdown", reply_markup=skip_kb("sl"),
        )
        return SL_PRICE

    tp_hint = _pre_filled_hint("tp", ctx)
    await update.message.reply_text(
        f"SL: `{_fmt_price(ctx.user_data['sl'])}`\n\n"
        f"*Take Profit (TP)?* (optional)\n\n{tp_hint}",
        parse_mode="Markdown", reply_markup=skip_kb("tp"),
    )
    return TP_PRICE

async def step_sl_skip(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    if "sl" not in ctx.user_data:
        ctx.user_data["sl"] = None

    tp_hint = _pre_filled_hint("tp", ctx)
    await q.edit_message_text(
        f"*Take Profit (TP)?* (optional)\n\n{tp_hint}",
        parse_mode="Markdown", reply_markup=skip_kb("tp"),
    )
    return TP_PRICE

# B9 ── TP → log to Notion ─────────────────────────────────────────────────────
async def step_tp_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        ctx.user_data["tp"] = float(
            update.message.text.strip().replace(",", ".").split()[0]
        )
    except ValueError:
        await update.message.reply_text(
            "❌ Not valid. Try `2380.00` or tap Skip.",
            parse_mode="Markdown", reply_markup=skip_kb("tp"),
        )
        return TP_PRICE
    status_msg = await update.message.reply_text("⏳ Saving to Notion…")
    await _log_before(status_msg, ctx)
    return ConversationHandler.END

async def step_tp_skip(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    if "tp" not in ctx.user_data:
        ctx.user_data["tp"] = None
    await q.edit_message_text("⏳ Saving to Notion…")
    await _log_before(q.message, ctx)
    return ConversationHandler.END


def _pre_filled_hint(field: str, ctx: ContextTypes.DEFAULT_TYPE) -> str:
    """
    When the user chose ✏️ Edit, show the detected value as a hint
    so they know what was pre-detected and can just tap Skip to accept it.
    """
    v = ctx.user_data.get(field)
    if v is not None:
        label = field.upper()
        return f"Detected {label}: `{_fmt_price(v)}` — type to override or skip to accept:"
    return f"Type e.g. `{'2320.00' if field == 'sl' else '2380.00'}` or skip:"


# ── Before → Notion ───────────────────────────────────────────────────────────
async def _log_before(bot_msg, ctx: ContextTypes.DEFAULT_TYPE):
    d = ctx.user_data
    image_url = None
    if d.get("photo_bytes"):
        image_url = notion.upload_image_to_imgbb(d["photo_bytes"])

    entry = d.get("entry")
    sl    = d.get("sl")
    tp    = d.get("tp")

    # Planned R:R preview
    rr_str = "—"
    if entry and sl and tp:
        is_long = d.get("dir", "Long").lower() == "long"
        risk    = (entry - sl) if is_long else (sl - entry)
        reward  = (tp - entry) if is_long else (entry - tp)
        if risk > 0:
            rr_str = f"{reward / risk:.2f}R"

    # Source tag for prices
    auto_tag = ""
    if d.get("detected") and (entry or sl or tp):
        conf = d["detected"].get("confidence", "")
        auto_tag = f"\n_🤖 Prices auto-detected ({conf} confidence)_"

    try:
        page = notion.create_open_trade(
            symbol           = d.get("symbol", ""),
            direction        = d.get("dir", ""),
            session          = d.get("session", ""),
            setups           = d.get("setups", []),
            emotion          = d.get("emotion", ""),
            grade            = d.get("grade", ""),
            entry            = entry,
            sl               = sl,
            tp               = tp,
            before_image_url = image_url,
        )
        page_url   = page.get("url", "")
        setups_str = " + ".join(d.get("setups", [])) or "—"
        screenshot = "📷 Before chart saved ✅" if image_url else "⚠️ No screenshot — set IMGBB\\_API\\_KEY in .env"

        await bot_msg.edit_text(
            f"📊 *{d.get('symbol','')} {d.get('dir','')} — OPEN*\n"
            f"Session: {d.get('session','')}  |  Setup: {setups_str}\n"
            f"Emotion: {d.get('emotion','')}"
            f"{'  |  Grade: ' + d.get('grade','') if d.get('grade') else ''}\n\n"
            f"Entry: `{_fmt_price(entry)}`  |  SL: `{_fmt_price(sl)}`  |  TP: `{_fmt_price(tp)}`\n"
            f"Planned R:R: `{rr_str}`"
            f"{auto_tag}\n\n"
            f"{screenshot}\n"
            f"✅ [Trade opened in Notion]({page_url})\n\n"
            f"_Send After chart when trade closes._",
            parse_mode="Markdown", disable_web_page_preview=True,
        )
    except Exception as e:
        log.exception("_log_before error")
        try:
            await bot_msg.edit_text(f"❌ Error: {e}")
        except Exception:
            pass
    finally:
        ctx.user_data.clear()


# ══════════════════════════════════════════════════════════════════════════════
# AFTER FLOW
# ══════════════════════════════════════════════════════════════════════════════

# A1 ── Pick open trade ────────────────────────────────────────────────────────
async def step_pick_trade(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    page_id = val(q.data)
    ctx.user_data["page_id"] = page_id
    t = ctx.user_data["open_trades"].get(page_id, {})
    ctx.user_data["selected_trade"] = t

    setup_str = " + ".join(t.get("setups", [])) or "—"
    await q.edit_message_text(
        f"*{t.get('symbol','')} {t.get('dir','')}*  opened {t.get('date','')}\n"
        f"Setup: {setup_str}\n"
        f"Entry: `{_fmt_price(t.get('entry'))}`  "
        f"|  SL: `{_fmt_price(t.get('sl'))}`  "
        f"|  TP: `{_fmt_price(t.get('tp'))}`\n\n"
        f"*Result?*\n\n"
        f"_Exit will be auto-filled:_\n"
        f"Win → TP  |  Loss → SL  |  BE → Entry",
        parse_mode="Markdown",
        reply_markup=kb(RESULTS, "result"),
    )
    return RESULT

# A2 ── Result → auto-fill exit → update Notion ────────────────────────────────
async def step_result(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    raw    = val(q.data)
    result = "BE" if raw == "Break Even" else raw
    ctx.user_data["result"] = result

    await q.edit_message_text("⏳ Updating Notion…")
    await _log_after(q.message, ctx)
    return ConversationHandler.END

# ── After → Notion update ─────────────────────────────────────────────────────
async def _log_after(bot_msg, ctx: ContextTypes.DEFAULT_TYPE):
    d       = ctx.user_data
    page_id = d.get("page_id")
    t       = d.get("selected_trade", {})
    result  = d.get("result", "")

    image_url = None
    if d.get("photo_bytes"):
        image_url = notion.upload_image_to_imgbb(d["photo_bytes"])

    entry = t.get("entry")
    sl    = t.get("sl")
    tp    = t.get("tp")

    exit_price = notion.resolve_exit(result, entry, sl, tp)

    rr_str = "—"
    if entry and sl and exit_price:
        is_long = t.get("dir", "Long").lower() == "long"
        risk    = (entry - sl) if is_long else (sl - entry)
        gain    = (exit_price - entry) if is_long else (entry - exit_price)
        if risk > 0:
            rr_str = f"{gain / risk:.2f}R"

    try:
        notion.close_trade(
            page_id=page_id, result=result,
            entry=entry, sl=sl, tp=tp,
            after_image_url=image_url,
        )

        emoji      = "🟢" if result == "Win" else "🔴" if result == "Loss" else "⚪"
        exit_label = {"Win": "TP", "Loss": "SL", "BE": "Entry"}.get(result, "")
        screenshot = "📷 After chart saved ✅" if image_url else "⚠️ No screenshot — set IMGBB\\_API\\_KEY in .env"

        await bot_msg.edit_text(
            f"{emoji} *{t.get('symbol','')} {t.get('dir','')} — {result}*\n\n"
            f"Entry: `{_fmt_price(entry)}`  →  Exit ({exit_label}): `{_fmt_price(exit_price)}`\n"
            f"Actual R:R: `{rr_str}`\n\n"
            f"{screenshot}\n"
            f"✅ Trade closed in Notion",
            parse_mode="Markdown",
        )
    except Exception as e:
        log.exception("_log_after error")
        try:
            await bot_msg.edit_text(f"❌ Error: {e}")
        except Exception:
            pass
    finally:
        ctx.user_data.clear()


# ── /cancel ───────────────────────────────────────────────────────────────────
async def cmd_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    ctx.user_data.clear()
    await update.message.reply_text("❌ Cancelled. Drop a chart to start again.")
    return ConversationHandler.END

# ── /start ────────────────────────────────────────────────────────────────────
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not authorized(update): return
    auto_tag = "🤖 _Entry/SL/TP auto-detected from chart_\n" if _CHART_READER_AVAILABLE else ""
    await update.message.reply_text(
        "📒 *Trading Journal Bot*\n\n"
        "Drop a chart screenshot to log a trade.\n\n"
        "📊 *Before* — logs entry, setup, SL/TP\n"
        "🏁 *After*  — pick trade, select result → done\n"
        f"{auto_tag}"
        "_(Exit auto-filled: Win=TP, Loss=SL, BE=Entry)_\n\n"
        "`/stats`   — performance summary\n"
        "`/open`    — list open trades\n"
        "`/cancel`  — cancel current entry",
        parse_mode="Markdown",
    )

# ── /stats ────────────────────────────────────────────────────────────────────
async def cmd_stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not authorized(update): return
    msg = await update.message.reply_text("⏳ Fetching stats…")
    try:
        s = notion.get_stats()
        if s["total"] == 0:
            await msg.edit_text("📊 No closed trades yet. Drop a Before chart to start!")
            return
        best  = f"{s['best'][0]}  `+{s['best'][1]:.2f}R`"  if s.get("best")  else "—"
        worst = f"{s['worst'][0]}  `{s['worst'][1]:.2f}R`" if s.get("worst") else "—"
        await msg.edit_text(
            f"📊 *Journal — {s['total']} closed trades*\n\n"
            f"Win rate: `{s['win_rate']}%`  ({s['wins']}W / {s['losses']}L / {s['be']}BE)\n"
            f"Avg R:R:  `{s['avg_rr']:.2f}R`\n\n"
            f"🏆 Best:  {best}\n"
            f"💀 Worst: {worst}",
            parse_mode="Markdown",
        )
    except Exception as e:
        log.exception("Stats error")
        await msg.edit_text(f"❌ Could not fetch stats: {e}")

# ── /open ─────────────────────────────────────────────────────────────────────
async def cmd_open(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not authorized(update): return
    msg = await update.message.reply_text("⏳ Fetching open trades…")
    try:
        trades = notion.query_open_trades()
        if not trades:
            await msg.edit_text("📭 No open trades. Drop a Before chart to open one.")
            return
        lines = ["📂 *Open trades:*\n"]
        for t in trades:
            setup_str = " + ".join(t.get("setups", [])) or "—"
            e = f"@ `{_fmt_price(t.get('entry'))}`" if t.get("entry") else ""
            lines.append(
                f"• *{t['symbol']}* {t['dir']}  {t['date']}  {e}\n"
                f"  Setup: {setup_str}  "
                f"|  SL: `{_fmt_price(t.get('sl'))}`  "
                f"TP: `{_fmt_price(t.get('tp'))}`"
            )
        await msg.edit_text("\n".join(lines), parse_mode="Markdown")
    except Exception as e:
        await msg.edit_text(f"❌ Error: {e}")


# ══════════════════════════════════════════════════════════════════════════════
# ConversationHandler
# ══════════════════════════════════════════════════════════════════════════════
def build_conv_handler() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[MessageHandler(filters.PHOTO, receive_photo)],
        states={
            PHOTO_TYPE: [
                CallbackQueryHandler(step_photo_type, pattern=r"^type:")
            ],
            PAIR: [
                CallbackQueryHandler(step_pair, pattern=r"^pair:")
            ],
            DIRECTION: [
                CallbackQueryHandler(step_direction, pattern=r"^dir:")
            ],
            SESSION: [
                CallbackQueryHandler(step_session, pattern=r"^session:")
            ],
            SETUP_PICK: [
                CallbackQueryHandler(step_setup_pick, pattern=r"^setup:")
            ],
            CUSTOM_SETUP: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, step_custom_setup)
            ],
            EMOTION: [
                CallbackQueryHandler(step_emotion, pattern=r"^emotion:")
            ],
            GRADE: [
                CallbackQueryHandler(step_grade, pattern=r"^grade:")
            ],
            CONFIRM_PRICES: [                               # ← NEW
                CallbackQueryHandler(step_confirm_prices, pattern=r"^prices:")
            ],
            ENTRY_PRICE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, step_entry_text),
                CallbackQueryHandler(step_entry_skip, pattern=r"^entry:skip"),
            ],
            SL_PRICE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, step_sl_text),
                CallbackQueryHandler(step_sl_skip,   pattern=r"^sl:skip"),
            ],
            TP_PRICE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, step_tp_text),
                CallbackQueryHandler(step_tp_skip,   pattern=r"^tp:skip"),
            ],
            PICK_TRADE: [
                CallbackQueryHandler(step_pick_trade, pattern=r"^pick:")
            ],
            RESULT: [
                CallbackQueryHandler(step_result, pattern=r"^result:")
            ],
        },
        fallbacks=[CommandHandler("cancel", cmd_cancel)],
        per_user=True,
        allow_reentry=True,
    )
