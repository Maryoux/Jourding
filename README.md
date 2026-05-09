# Jourding

A Telegram bot that logs your trades into Notion using a screenshot-first, button-driven flow. No typing required — just drop a chart and tap buttons.

---

## How it works

### Before chart (opening a trade)
Drop your entry chart → bot asks questions via buttons → trade saved to Notion as **Open**

```
📸 Screenshot
    ↓
Before / After?
    ↓ Before
Pair → Direction → Session → Setup (multi-select) → Emotion → Grade
    ↓
Entry price (optional) → SL (optional) → TP (optional)
    ↓
✅ Saved to Notion — Status: Open
   Planned R:R calculated automatically by Notion formula
```

### After chart (closing a trade)
Drop your exit chart → pick the open trade → select result → done

```
📸 Screenshot
    ↓
Before / After?
    ↓ After
Pick open trade (buttons)
    ↓
Result: Win / Loss / Break Even
    ↓
✅ Notion page updated — Status: Closed
   Exit auto-filled (Win=TP, Loss=SL, BE=Entry)
   Actual R:R calculated automatically by Notion formula
   After chart appended below Before chart on same page
```

---

## Project structure

```
trading_journal_bot/
├── main.py                # Entry point — starts the bot
├── handlers.py            # All Telegram conversation handlers
├── notion_client.py       # Notion API wrapper
├── setup_notion.py        # One-time database creation script
├── config.py              # Env loader + validation
├── custom_setups.json     # Auto-created — saves your custom setups
├── requirements.txt
└── .env                   # Your tokens (copy from .env.example)
```

---

## Quick start

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

`requirements.txt`:
```
python-telegram-bot>=21.0
requests>=2.31.0
python-dotenv>=1.0.0
```

### 2. Set up your `.env`
```bash
cp .env.example .env

nano .env
```


```env
# Telegram
TELEGRAM_TOKEN=your_bot_token_here
ALLOWED_USER_ID=123456789        # Your Telegram user ID (0 = allow anyone)

# Notion
NOTION_TOKEN=secret_xxxxxxxxxxxxxxxxxxxx
NOTION_DATABASE_ID=              # Filled after running setup_notion.py
NOTION_PARENT_PAGE_ID=           # Optional — page to create the DB inside

# Screenshot hosting (free)
IMGBB_API_KEY=                   # Get free key at imgbb.com
```

### 3. Get your tokens

**Telegram Bot Token**
- Open Telegram → search `@BotFather` → `/newbot` → copy token

**Your Telegram User ID**
- Open `@userinfobot` in Telegram → copy your ID → paste into `ALLOWED_USER_ID`

**Notion Integration Token**
- Go to https://www.notion.so/my-integrations → New integration → copy secret
- Open your Notion workspace page → `···` menu → Connections → add your integration

**imgbb API Key** *(for screenshots)*
- Register free at https://imgbb.com → API section → copy key
- Without this, screenshots will **not** be saved to Notion

### 4. Create the Notion database

```bash
python setup_notion.py
```

The script will:
- List pages your integration can access → pick one as the parent
- Create the full database with all properties and formulas
- Print the `NOTION_DATABASE_ID` → paste it into `.env`

### 5. Run the bot

```bash
python main.py
```

---

## Notion database schema

| Property | Type | Filled by |
|---|---|---|
| Symbol | Title | Bot |
| Date | Date | Bot (today) |
| Status | Select | Bot (Open → Closed) |
| Direction | Select | Bot |
| Result | Select | Bot |
| Setup | **Multi-select** | Bot |
| Session | Select | Bot |
| Emotion | Select | Bot |
| Grade | Select | Bot |
| Entry | Number | Bot |
| SL | Number | Bot |
| TP | Number | Bot |
| Exit | Number | Bot (auto from result) |
| Planned R:R | **Formula** | Notion (Entry/SL/TP) |
| Actual R:R | **Formula** | Notion (Entry/Exit/SL) |
| Notes | Text | Bot / manual |
| Lessons Learned | Text | Bot / manual |

**Notion formulas used:**

*Planned R:R*
```
if(SL != 0 and Entry != 0 and TP != 0,
  if(Direction == "Long",
    (TP - Entry) / (Entry - SL),
    (Entry - TP) / (SL - Entry)),
  0)
```

*Actual R:R*
```
if(SL != 0 and Entry != 0 and Exit != 0,
  if(Direction == "Long",
    (Exit - Entry) / (Entry - SL),
    (Entry - Exit) / (SL - Entry)),
  0)
```

> **Tip:** Set the Planned R:R and Actual R:R formula columns to number format `0.00` in Notion for clean display.

---

## Bot commands

| Command | What it does |
|---|---|
| *(drop a photo)* | Start logging a trade |
| `/stats` | Win rate, avg R:R, best/worst trade |
| `/open` | List all currently open trades |
| `/cancel` | Cancel the current conversation |
| `/start` | Show help message |

---

## Setup options (multi-select)

Default setups available as buttons:

| Setup | Description |
|---|---|
| SMT | Smart Money Technique |
| IRL-ERL | Internal Range Liquidity → External |
| ERL-IRL | External Range Liquidity → Internal |
| Breakout | Price breaks structure |
| Retest | Return to broken level |
| Reversal | Counter-trend move |
| Trend | With-trend continuation |
| Range | Consolidation play |
| News | Fundamental catalyst |

**Adding custom setups:** tap `✏️ Type my own` during the flow → type any name → it's saved to `custom_setups.json` and automatically appears as a button next time.

You can select **multiple setups** per trade to capture confluence.

---

## Running 24/7

### Option A — screen (simplest)
```bash
screen -S tradebot
python main.py
# Detach: Ctrl+A then D
# Reattach: screen -r tradebot
```

### Option B — systemd service (Linux/VPS)
```ini
# /etc/systemd/system/tradebot.service
[Unit]
Description=Trading Journal Bot
After=network.target

[Service]
WorkingDirectory=/path/to/trading_journal_bot
ExecStart=/usr/bin/python3 main.py
Restart=always
EnvironmentFile=/path/to/trading_journal_bot/.env

[Install]
WantedBy=multi-user.target
```
```bash
sudo systemctl enable tradebot
sudo systemctl start tradebot
sudo journalctl -u tradebot -f    # watch logs
```

A $4–6/mo VPS (Hetzner, DigitalOcean, Vultr) running Ubuntu is enough — the bot uses almost no RAM or CPU.

---

## Troubleshooting

**Bot doesn't respond**
- Check `TELEGRAM_TOKEN` is correct
- Send `/start` to the bot first so it knows your chat ID
- Verify `ALLOWED_USER_ID` matches your actual ID from `@userinfobot`

**Notion 400 validation error**
- Make sure `NOTION_DATABASE_ID` is the correct 32-character ID (no dashes)
- Property names in Notion must match exactly — delete and recreate the DB with `setup_notion.py` if unsure

**Screenshots not appearing in Notion**
- Set `IMGBB_API_KEY` in `.env` — without it screenshots are skipped
- Free key at https://imgbb.com, takes 1 minute to get

**Open trades not showing in After flow**
- Make sure the trade was logged with `Status: Open` — only trades created by this bot appear
- If you moved trades manually in Notion, check their Status field

**Custom setups not appearing**
- Check that `custom_setups.json` exists in the same folder as `main.py`
- If you deleted it, your custom setups are gone — re-add them via `✏️ Type my own`
