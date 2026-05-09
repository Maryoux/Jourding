"""
handlers_v3.py
──────────────
Key changes vs v2:
  - Setup is multi-select — tap multiple, tap ✅ Done to confirm
  - Custom setup saved to custom_setups.json, auto-appears as button next time
  - SL / TP properly collected and pushed to Notion
  - After flow: NO exit price input — auto-filled from result (Win=TP, Loss=SL, BE=Entry)
  - Stats show RR instead of PnL
  - "Message can't be edited" fully fixed — bot always edits its OWN messages only
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
    ENTRY_PRICE, SL_PRICE, TP_PRICE,
    PICK_TRADE, RESULT,
) = range(13)

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
BASE_SETUPS = ["SMT", "IRL-ERL", "ERL-IRL", "Breakout", "Retest", "Reversal", "Trend", "Range", "News"]

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
    return InlineKeyboardMarkup([[InlineKeyboardButton("Skip ⏭️", callback_data=f"{prefix}:skip")]])

def val(cb: str) -> str:
    return cb.split(":", 1)[1]

def setup_kb(selected: list[str]) -> InlineKeyboardMarkup:
    """
    Multi-select setup keyboard.
    Selected items show a ✓ prefix.
    Last row: ✏️ Type my own  |  ✅ Done
    """
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
# BEFORE FLOW
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

    # After: fetch open trades
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
    q   = update.callback_query
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

    # Toggle selection
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

    msg = await update.message.reply_text(
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

# B6 ── Grade ──────────────────────────────────────────────────────────────────
async def step_grade(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    raw = val(q.data)
    ctx.user_data["grade"] = "" if raw == "Skip" else raw[0]
    await q.edit_message_text(
        f"{summary(ctx.user_data)}\n\n"
        "*Entry price?* (optional)\n\nType e.g. `2340.50` or skip:",
        parse_mode="Markdown", reply_markup=skip_kb("entry"),
    )
    return ENTRY_PRICE

# B7 ── Entry price ────────────────────────────────────────────────────────────
async def step_entry_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        ctx.user_data["entry"] = float(update.message.text.strip().replace(",", ".").split()[0])
    except ValueError:
        await update.message.reply_text(
            "❌ Not a valid number. Try `2340.50` or tap Skip.",
            parse_mode="Markdown", reply_markup=skip_kb("entry"),
        )
        return ENTRY_PRICE
    bot_msg = await update.message.reply_text(
        f"Entry: `{ctx.user_data['entry']}`\n\n*Stop Loss (SL)?* (optional)\n\nType e.g. `2320.00` or skip:",
        parse_mode="Markdown", reply_markup=skip_kb("sl"),
    )
    ctx.user_data["_bot_msg"] = bot_msg
    return SL_PRICE

async def step_entry_skip(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    ctx.user_data["entry"] = None
    await q.edit_message_text(
        "*Stop Loss (SL)?* (optional)\n\nType e.g. `2320.00` or skip:",
        parse_mode="Markdown", reply_markup=skip_kb("sl"),
    )
    return SL_PRICE

# B8 ── SL ─────────────────────────────────────────────────────────────────────
async def step_sl_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        ctx.user_data["sl"] = float(update.message.text.strip().replace(",", ".").split()[0])
    except ValueError:
        await update.message.reply_text(
            "❌ Not valid. Try `2320.00` or tap Skip.",
            parse_mode="Markdown", reply_markup=skip_kb("sl"),
        )
        return SL_PRICE
    await update.message.reply_text(
        f"SL: `{ctx.user_data['sl']}`\n\n*Take Profit (TP)?* (optional)\n\nType e.g. `2380.00` or skip:",
        parse_mode="Markdown", reply_markup=skip_kb("tp"),
    )
    return TP_PRICE

async def step_sl_skip(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    ctx.user_data["sl"] = None
    await q.edit_message_text(
        "*Take Profit (TP)?* (optional)\n\nType e.g. `2380.00` or skip:",
        parse_mode="Markdown", reply_markup=skip_kb("tp"),
    )
    return TP_PRICE

# B9 ── TP → log to Notion ─────────────────────────────────────────────────────
async def step_tp_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        ctx.user_data["tp"] = float(update.message.text.strip().replace(",", ".").split()[0])
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
    ctx.user_data["tp"] = None
    await q.edit_message_text("⏳ Saving to Notion…")
    await _log_before(q.message, ctx)
    return ConversationHandler.END

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

    try:
        page     = notion.create_open_trade(
            symbol    = d.get("symbol", ""),
            direction = d.get("dir", ""),
            session   = d.get("session", ""),
            setups    = d.get("setups", []),
            emotion   = d.get("emotion", ""),
            grade     = d.get("grade", ""),
            entry     = entry,
            sl        = sl,
            tp        = tp,
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
            f"Entry: `{entry or '—'}`  |  SL: `{sl or '—'}`  |  TP: `{tp or '—'}`\n"
            f"Planned R:R: `{rr_str}`\n\n"
            f"{screenshot}\n"
            f"✅ [Trade opened in Notion]({page_url})\n\n"
            f"_Send After chart when trade closes._",
            parse_mode="Markdown", disable_web_page_preview=True,
        )
    except Exception as e:
        log.exception("_log_before error")
        try: await bot_msg.edit_text(f"❌ Error: {e}")
        except Exception: pass
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
        f"Entry: `{t.get('entry','—')}`  |  SL: `{t.get('sl','—')}`  |  TP: `{t.get('tp','—')}`\n\n"
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

    # Auto-filled exit
    exit_price = notion.resolve_exit(result, entry, sl, tp)

    # Actual R:R preview
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
            f"Entry: `{entry or '—'}`  →  Exit ({exit_label}): `{exit_price or '—'}`\n"
            f"Actual R:R: `{rr_str}`\n\n"
            f"{screenshot}\n"
            f"✅ Trade closed in Notion",
            parse_mode="Markdown",
        )
    except Exception as e:
        log.exception("_log_after error")
        try: await bot_msg.edit_text(f"❌ Error: {e}")
        except Exception: pass
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
    await update.message.reply_text(
        "📒 *Trading Journal Bot*\n\n"
        "Drop a chart screenshot to log a trade.\n\n"
        "📊 *Before* — logs entry, setup, SL/TP\n"
        "🏁 *After*  — pick trade, select result → done\n"
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
            e = f"@ `{t['entry']}`" if t.get("entry") else ""
            lines.append(
                f"• *{t['symbol']}* {t['dir']}  {t['date']}  {e}\n"
                f"  Setup: {setup_str}  |  SL: `{t.get('sl','—')}`  TP: `{t.get('tp','—')}`"
            )
        await msg.edit_text("\n".join(lines), parse_mode="Markdown")
    except Exception as e:
        await msg.edit_text(f"❌ Error: {e}")

# ── ConversationHandler ───────────────────────────────────────────────────────
def build_conv_handler() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[MessageHandler(filters.PHOTO, receive_photo)],
        states={
            PHOTO_TYPE:   [CallbackQueryHandler(step_photo_type,   pattern=r"^type:")],
            PAIR:         [CallbackQueryHandler(step_pair,          pattern=r"^pair:")],
            DIRECTION:    [CallbackQueryHandler(step_direction,     pattern=r"^dir:")],
            SESSION:      [CallbackQueryHandler(step_session,       pattern=r"^session:")],
            SETUP_PICK:   [CallbackQueryHandler(step_setup_pick,    pattern=r"^setup:")],
            CUSTOM_SETUP: [MessageHandler(filters.TEXT & ~filters.COMMAND, step_custom_setup)],
            EMOTION:      [CallbackQueryHandler(step_emotion,       pattern=r"^emotion:")],
            GRADE:        [CallbackQueryHandler(step_grade,         pattern=r"^grade:")],
            ENTRY_PRICE:  [
                MessageHandler(filters.TEXT & ~filters.COMMAND, step_entry_text),
                CallbackQueryHandler(step_entry_skip, pattern=r"^entry:skip"),
            ],
            SL_PRICE:     [
                MessageHandler(filters.TEXT & ~filters.COMMAND, step_sl_text),
                CallbackQueryHandler(step_sl_skip,    pattern=r"^sl:skip"),
            ],
            TP_PRICE:     [
                MessageHandler(filters.TEXT & ~filters.COMMAND, step_tp_text),
                CallbackQueryHandler(step_tp_skip,    pattern=r"^tp:skip"),
            ],
            PICK_TRADE:   [CallbackQueryHandler(step_pick_trade,   pattern=r"^pick:")],
            RESULT:       [CallbackQueryHandler(step_result,       pattern=r"^result:")],
        },
        fallbacks=[CommandHandler("cancel", cmd_cancel)],
        per_user=True,
        allow_reentry=True,
    )
