"""
handlers.py
───────────
Two-photo trade logging flow.

BEFORE flow (entry):
  Photo → "Before" → Pair → Direction → Session → Setup →
  Emotion → Grade → Entry price (optional) → logged as Status: Open

AFTER flow (exit):
  Photo → "After" → Pick open trade (buttons) →
  Result → Exit price (optional) → Notion page updated, Status: Closed
"""

import logging
import requests
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ContextTypes,
    ConversationHandler,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
)

import config
import notion_client as notion
from parser import Trade

log = logging.getLogger(__name__)

# ── Conversation states ───────────────────────────────────────────────────────
(
    PHOTO_TYPE,
    # Before flow
    PAIR, DIRECTION, SESSION, SETUP, EMOTION, GRADE, ENTRY_PRICE,
    # After flow
    PICK_TRADE, RESULT, EXIT_PRICE,
) = range(11)

# ── Button definitions ────────────────────────────────────────────────────────
PAIRS = [
    ["XAUUSD", "XAGUSD"],
    ["EURUSD", "GBPUSD", "USDJPY"],
    ["AUDUSD", "USDCAD", "NZDUSD"],
    ["US30",   "NAS100", "SP500"],
    ["BTCUSD", "ETHUSD"],
    ["OTHER"],
]
DIRECTIONS = [["Long", "Short"]]
SESSIONS   = [["Asia", "London"], ["New York", "Overlap"]]
SETUPS     = [
    ["Breakout",  "Retest"],
    ["Reversal",  "Trend Follow"],
    ["Range",     "News"],
    ["Other"],
]
EMOTIONS = [
    ["Calm",   "Confident"],
    ["FOMO",   "Fearful"],
    ["Greedy", "Revenge"],
    ["Bored"],
]
GRADES  = [["A - Perfect", "B - Good", "C - Messy", "Skip"]]
RESULTS = [["Win", "Loss", "Break Even"]]

EMOJIS = {
    "Long": "📈", "Short": "📉",
    "Win": "✅", "Loss": "❌", "Break Even": "➖",
    "Asia": "🌏", "London": "🇬🇧", "New York": "🗽", "Overlap": "⚡",
    "Calm": "😌", "Confident": "💪", "FOMO": "😰", "Fearful": "😨",
    "Greedy": "🤑", "Revenge": "😤", "Bored": "😑",
    "A - Perfect": "💎", "B - Good": "👍", "C - Messy": "😅", "Skip": "⏭️",
}

def lbl(val: str) -> str:
    e = EMOJIS.get(val, "")
    return f"{val} {e}" if e else val

def kb(options: list[list[str]], prefix: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(lbl(v), callback_data=f"{prefix}:{v}") for v in row]
        for row in options
    ])

def val(callback_data: str) -> str:
    return callback_data.split(":", 1)[1]

def summary(d: dict) -> str:
    parts = []
    if d.get("symbol"):  parts.append(f"*{d['symbol']}*")
    if d.get("dir"):     parts.append(d["dir"])
    if d.get("session"): parts.append(d["session"])
    if d.get("setup"):   parts.append(d["setup"])
    return " | ".join(parts)


# ── Auth ──────────────────────────────────────────────────────────────────────

def authorized(update: Update) -> bool:
    if config.ALLOWED_USER_ID == 0:
        return True
    return update.effective_user.id == config.ALLOWED_USER_ID


# ══════════════════════════════════════════════════════════════════════════════
# STEP 0 — User sends a photo → ask Before or After
# ══════════════════════════════════════════════════════════════════════════════

async def receive_photo(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    if not authorized(update):
        return ConversationHandler.END

    # Download immediately before Telegram expires the file
    photo = update.message.photo[-1]
    try:
        tg_file   = await ctx.bot.get_file(photo.file_id)
        img_bytes = await tg_file.download_as_bytearray()
        ctx.user_data["photo_bytes"] = bytes(img_bytes)
    except Exception as e:
        log.warning(f"Photo download failed: {e}")
        ctx.user_data["photo_bytes"] = None

    await update.message.reply_text(
        "📸 Chart received!\n\n*Is this a Before (entry) or After (exit) chart?*",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("📊 Before  —  Entry",  callback_data="type:before"),
            InlineKeyboardButton("🏁 After  —  Exit",   callback_data="type:after"),
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
            "📊 *Before chart — Entry*\n\n*What pair?*",
            parse_mode="Markdown",
            reply_markup=kb(PAIRS, "pair"),
        )
        return PAIR

    # ── After flow: fetch open trades ────────────────────────────────────────
    await q.edit_message_text("⏳ Fetching your open trades…")
    try:
        open_trades = notion.query_open_trades()
    except Exception as e:
        await q.edit_message_text(f"❌ Could not fetch open trades: {e}")
        return ConversationHandler.END

    if not open_trades:
        await q.edit_message_text(
            "📭 No open trades found.\n\n"
            "Log a *Before* chart first to open a trade.",
            parse_mode="Markdown",
        )
        return ConversationHandler.END

    # Store open trades for later lookup
    ctx.user_data["open_trades"] = {t["id"]: t for t in open_trades}

    # Build buttons — one per open trade
    buttons = []
    for t in open_trades:
        label_text = f"{t['symbol']} {t['dir']}  {t['date']}"
        if t.get("entry"):
            label_text += f"  @ {t['entry']}"
        buttons.append([InlineKeyboardButton(label_text, callback_data=f"pick:{t['id']}")])

    await q.edit_message_text(
        "🏁 *After chart — Exit*\n\nWhich open trade is this closing?",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(buttons),
    )
    return PICK_TRADE


# ── B1: Pair ──────────────────────────────────────────────────────────────────

async def step_pair(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    ctx.user_data["symbol"] = val(q.data)

    await q.edit_message_text(
        f"{summary(ctx.user_data)}\n\n*Direction?*",
        parse_mode="Markdown",
        reply_markup=kb(DIRECTIONS, "dir"),
    )
    return DIRECTION


# ── B2: Direction ─────────────────────────────────────────────────────────────

async def step_direction(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    ctx.user_data["dir"] = val(q.data)

    await q.edit_message_text(
        f"{summary(ctx.user_data)}\n\n*Session?*",
        parse_mode="Markdown",
        reply_markup=kb(SESSIONS, "session"),
    )
    return SESSION


# ── B3: Session ───────────────────────────────────────────────────────────────

async def step_session(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    ctx.user_data["session"] = val(q.data)

    await q.edit_message_text(
        f"{summary(ctx.user_data)}\n\n*Setup / Strategy?*",
        parse_mode="Markdown",
        reply_markup=kb(SETUPS, "setup"),
    )
    return SETUP


# ── B4: Setup ─────────────────────────────────────────────────────────────────

async def step_setup(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    ctx.user_data["setup"] = val(q.data)

    await q.edit_message_text(
        f"{summary(ctx.user_data)}\n\n*How are you feeling?*",
        parse_mode="Markdown",
        reply_markup=kb(EMOTIONS, "emotion"),
    )
    return EMOTION


# ── B5: Emotion ───────────────────────────────────────────────────────────────

async def step_emotion(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    ctx.user_data["emotion"] = val(q.data)

    await q.edit_message_text(
        f"Emotion: *{ctx.user_data['emotion']}*\n\n*Grade your entry execution:*",
        parse_mode="Markdown",
        reply_markup=kb(GRADES, "grade"),
    )
    return GRADE


# ── B6: Grade ─────────────────────────────────────────────────────────────────

async def step_grade(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    raw = val(q.data)
    ctx.user_data["grade"] = "" if raw == "Skip" else raw[0]

    await q.edit_message_text(
        f"{summary(ctx.user_data)}\n\n"
        "*Entry price?* (optional)\n\n"
        "Type your entry price e.g. `2340.50`\nor tap Skip:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("Skip ⏭️", callback_data="entry:skip"),
        ]]),
    )
    return ENTRY_PRICE


# ── B7: Entry price ───────────────────────────────────────────────────────────

async def step_entry_price_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip().replace(",", ".")
    try:
        ctx.user_data["entry"] = float(text.split()[0])
    except ValueError:
        await update.message.reply_text(
            "❌ Couldn't read that. Type a number e.g. `2340.50` or tap Skip.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("Skip ⏭️", callback_data="entry:skip"),
            ]]),
        )
        return ENTRY_PRICE

    await update.message.reply_text("⏳ Saving to Notion…")
    await _log_before(update.message, ctx)
    return ConversationHandler.END


async def step_entry_price_skip(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    ctx.user_data["entry"] = None
    await q.edit_message_text("⏳ Saving to Notion…")
    await _log_before(q.message, ctx)
    return ConversationHandler.END


# ── Before → Notion ───────────────────────────────────────────────────────────

async def _log_before(message, ctx: ContextTypes.DEFAULT_TYPE):
    d = ctx.user_data

    # Upload before screenshot
    image_url   = None
    photo_bytes = d.get("photo_bytes")
    if photo_bytes:
        image_url = notion.upload_image_to_imgbb(photo_bytes)

    trade = Trade(
        symbol  = d.get("symbol", ""),
        dir     = d.get("dir", ""),
        entry   = d.get("entry") or 0.0,
        exit    = 0.0,
        setup   = d.get("setup", ""),
        grade   = d.get("grade", ""),
        emotion = d.get("emotion", ""),
        session = d.get("session", ""),
    )

    try:
        page     = notion.create_open_trade(trade, image_url)
        page_url = page.get("url", "")

        entry_str = f"`{trade.entry}`" if trade.entry else "—"
        screenshot_status = "📷 Before chart saved ✅" if image_url else "⚠️ No screenshot — add IMGBB\\_API\\_KEY to .env"

        await message.edit_text(
            f"📊 *{trade.symbol} {trade.dir} — OPEN*\n"
            f"Session: {trade.session}  |  Setup: {trade.setup}\n"
            f"Emotion: {trade.emotion}"
            f"{'  |  Grade: ' + trade.grade if trade.grade else ''}\n"
            f"Entry: {entry_str}\n\n"
            f"{screenshot_status}\n"
            f"✅ [Trade opened in Notion]({page_url})\n\n"
            f"_Send your After chart when the trade closes._",
            parse_mode="Markdown",
            disable_web_page_preview=True,
        )
    except requests.HTTPError as e:
        log.error(f"Notion {e.response.status_code}: {e.response.text[:300]}")
        await message.edit_text(
            f"❌ Notion error `{e.response.status_code}`:\n`{e.response.text[:200]}`",
            parse_mode="Markdown",
        )
    except Exception as e:
        log.exception("Error in _log_before")
        await message.edit_text(f"❌ Error: {e}")
    finally:
        ctx.user_data.clear()


# ══════════════════════════════════════════════════════════════════════════════
# AFTER FLOW
# ══════════════════════════════════════════════════════════════════════════════

# ── A1: Pick open trade ───────────────────────────────────────────────────────

async def step_pick_trade(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    page_id = val(q.data)
    ctx.user_data["page_id"] = page_id

    # Show the selected trade details
    t = ctx.user_data["open_trades"].get(page_id, {})
    ctx.user_data["selected_trade"] = t

    await q.edit_message_text(
        f"*{t.get('symbol','')} {t.get('dir','')}*  opened {t.get('date','')}\n"
        f"Entry: `{t.get('entry', '—')}`\n\n"
        f"*Result?*",
        parse_mode="Markdown",
        reply_markup=kb(RESULTS, "result"),
    )
    return RESULT


# ── A2: Result ────────────────────────────────────────────────────────────────

async def step_result(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    raw = val(q.data)
    ctx.user_data["result"] = "BE" if raw == "Break Even" else raw

    await q.edit_message_text(
        f"Result: *{raw}*\n\n"
        "*Exit price?* (optional)\n\n"
        "Type your exit price e.g. `2358.20`\nor tap Skip:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("Skip ⏭️", callback_data="exit:skip"),
        ]]),
    )
    return EXIT_PRICE


# ── A3: Exit price ────────────────────────────────────────────────────────────

async def step_exit_price_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip().replace(",", ".")
    try:
        ctx.user_data["exit"] = float(text.split()[0])
    except ValueError:
        await update.message.reply_text(
            "❌ Couldn't read that. Type a number e.g. `2358.20` or tap Skip.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("Skip ⏭️", callback_data="exit:skip"),
            ]]),
        )
        return EXIT_PRICE

    await update.message.reply_text("⏳ Updating Notion…")
    await _log_after(update.message, ctx)
    return ConversationHandler.END


async def step_exit_price_skip(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    ctx.user_data["exit"] = None
    await q.edit_message_text("⏳ Updating Notion…")
    await _log_after(q.message, ctx)
    return ConversationHandler.END


# ── After → Notion update ─────────────────────────────────────────────────────

async def _log_after(message, ctx: ContextTypes.DEFAULT_TYPE):
    d       = ctx.user_data
    page_id = d.get("page_id")
    t       = d.get("selected_trade", {})

    # Upload after screenshot
    image_url   = None
    photo_bytes = d.get("photo_bytes")
    if photo_bytes:
        image_url = notion.upload_image_to_imgbb(photo_bytes)

    entry  = t.get("entry") or 0.0
    exit_  = d.get("exit")  or 0.0
    result = d.get("result", "")
    dir_   = t.get("dir", "Long")

    # Calculate P&L + R:R if we have enough data
    pnl = rr = None
    if entry and exit_:
        is_long = dir_.lower() == "long"
        pnl = round(((exit_ - entry) if is_long else (entry - exit_)), 4)
        sl = t.get("sl")
        tp = t.get("tp")
        if sl and tp:
            risk   = (entry - sl) if is_long else (sl - entry)
            reward = (tp - entry) if is_long else (entry - tp)
            if risk > 0:
                rr = round(reward / risk, 2)

    try:
        notion.close_trade(
            page_id   = page_id,
            result    = result,
            exit_     = exit_ or None,
            pnl       = pnl,
            rr        = rr,
            image_url = image_url,
        )

        emoji     = "🟢" if result == "Win" else "🔴" if result == "Loss" else "⚪"
        pnl_line  = f"\nP&L: `{'+' if pnl and pnl>=0 else ''}{pnl:.4f}`" if pnl else ""
        rr_line   = f"  |  R:R: `{rr:.2f}R`" if rr else ""
        screenshot_status = "📷 After chart saved ✅" if image_url else "⚠️ No screenshot — add IMGBB\\_API\\_KEY to .env"

        await message.edit_text(
            f"{emoji} *{t.get('symbol','')} {t.get('dir','')} — {result}*\n"
            f"Entry: `{entry or '—'}`  →  Exit: `{exit_ or '—'}`"
            f"{pnl_line}{rr_line}\n\n"
            f"{screenshot_status}\n"
            f"✅ Trade closed in Notion",
            parse_mode="Markdown",
        )
    except requests.HTTPError as e:
        log.error(f"Notion {e.response.status_code}: {e.response.text[:300]}")
        await message.edit_text(
            f"❌ Notion error `{e.response.status_code}`:\n`{e.response.text[:200]}`",
            parse_mode="Markdown",
        )
    except Exception as e:
        log.exception("Error in _log_after")
        await message.edit_text(f"❌ Error: {e}")
    finally:
        ctx.user_data.clear()


# ── /cancel ───────────────────────────────────────────────────────────────────

async def cmd_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    ctx.user_data.clear()
    await update.message.reply_text("❌ Cancelled. Drop a chart screenshot to start again.")
    return ConversationHandler.END


# ── /start ────────────────────────────────────────────────────────────────────

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not authorized(update): return
    await update.message.reply_text(
        "📒 *Trading Journal Bot*\n\n"
        "Drop a chart screenshot to log a trade.\n\n"
        "📊 *Before chart* — logs entry, setup, emotion\n"
        "🏁 *After chart* — closes the trade with result & exit\n\n"
        "`/stats`  — performance summary\n"
        "`/open`   — list open trades\n"
        "`/cancel` — cancel current entry",
        parse_mode="Markdown",
    )


# ── /stats ────────────────────────────────────────────────────────────────────

async def cmd_stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not authorized(update): return
    msg = await update.message.reply_text("⏳ Fetching stats…")
    try:
        s = notion.get_stats()
        if s["total"] == 0:
            await msg.edit_text("📊 No closed trades yet. Start by dropping a Before chart!")
            return

        pf    = "∞" if s["profit_factor"] == float("inf") else f"{s['profit_factor']:.2f}"
        net   = f"{'+'if s['net_pnl']>=0 else ''}{s['net_pnl']:.4f}"
        best  = f"{s['best'][0]}  `+{s['best'][1]:.4f}`"  if s.get("best")  else "—"
        worst = f"{s['worst'][0]}  `{s['worst'][1]:.4f}`" if s.get("worst") else "—"

        await msg.edit_text(
            f"📊 *Journal — {s['total']} closed trades*\n\n"
            f"Win rate:      `{s['win_rate']}%`  ({s['wins']}W / {s['losses']}L / {s['be']}BE)\n"
            f"Net P&L:       `{net}`\n"
            f"Profit factor: `{pf}`\n"
            f"Avg R:R:       `{s['avg_rr']:.2f}R`\n\n"
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
        open_trades = notion.query_open_trades()
        if not open_trades:
            await msg.edit_text("📭 No open trades. Drop a Before chart to open one.")
            return

        lines = ["📂 *Open trades:*\n"]
        for t in open_trades:
            entry_str = f"@ `{t['entry']}`" if t.get("entry") else ""
            lines.append(f"• *{t['symbol']}* {t['dir']}  {t['date']}  {entry_str}")
        await msg.edit_text("\n".join(lines), parse_mode="Markdown")
    except Exception as e:
        await msg.edit_text(f"❌ Error: {e}")


# ── ConversationHandler ───────────────────────────────────────────────────────

def build_conv_handler() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[MessageHandler(filters.PHOTO, receive_photo)],
        states={
            PHOTO_TYPE: [CallbackQueryHandler(step_photo_type, pattern=r"^type:")],

            # Before flow
            PAIR:        [CallbackQueryHandler(step_pair,              pattern=r"^pair:")],
            DIRECTION:   [CallbackQueryHandler(step_direction,         pattern=r"^dir:")],
            SESSION:     [CallbackQueryHandler(step_session,           pattern=r"^session:")],
            SETUP:       [CallbackQueryHandler(step_setup,             pattern=r"^setup:")],
            EMOTION:     [CallbackQueryHandler(step_emotion,           pattern=r"^emotion:")],
            GRADE:       [CallbackQueryHandler(step_grade,             pattern=r"^grade:")],
            ENTRY_PRICE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, step_entry_price_text),
                CallbackQueryHandler(step_entry_price_skip,            pattern=r"^entry:skip"),
            ],

            # After flow
            PICK_TRADE: [CallbackQueryHandler(step_pick_trade,         pattern=r"^pick:")],
            RESULT:     [CallbackQueryHandler(step_result,             pattern=r"^result:")],
            EXIT_PRICE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, step_exit_price_text),
                CallbackQueryHandler(step_exit_price_skip,             pattern=r"^exit:skip"),
            ],
        },
        fallbacks=[CommandHandler("cancel", cmd_cancel)],
        per_user=True,
        allow_reentry=True,
    )
