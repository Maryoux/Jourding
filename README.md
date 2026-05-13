# Jourding

A Telegram bot that logs your trades into Notion using a screenshot-first, button-driven flow. No typing required — drop a chart, tap buttons, done.

> 🤖 **Auto-detects Entry, SL & TP** from your chart screenshot using NVIDIA Nemotron vision (free via OpenRouter) — zero manual price input needed.

---

## How it works

### Before chart (opening a trade)

Drop your entry chart → bot reads prices automatically → confirm or edit → trade saved to Notion as **Open**

```
📸 Screenshot
    ↓
Before / After?
    ↓ Before
Pair → Direction → Session → Setup (multi-select) → Emotion → Grade
    ↓
🤖 Auto-detect Entry / SL / TP from chart
    ↓
✅ Use detected  |  ✏️ Edit  |  ⏭️ Skip
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
Jourding/
├── main.py                # Entry point — starts the bot
├── handlers.py            # All Telegram conversation handlers (14-state machine)
├── chart_reader.py        # Vision AI — extracts Entry/SL/TP from chart screenshots
├── notion_client.py       # Notion API wrapper
├── setup_notion.py        # One-time database creation script
├── config.py              # Env loader + validation
├── parser.py              # Trade data parser and calculator
├── custom_setups.json     # Auto-created — saves your custom setups
├── requirements.txt
├── Jourding.service       # systemd service file for VPS deployment
└── .env                   # Your tokens (copy from .env.example)
```

---

## Quick start

### 1. Clone and create a virtual environment

```bash
git clone https://github.com/Maryoux/Jourding.git
cd Jourding
python3 -m venv venv
source venv/bin/activate
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

`requirements.txt`:
```
python-telegram-bot==20.7
requests==2.31.0
python-dotenv==1.0.0
```

### 3. Set up your `.env`

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

# Auto chart reading — FREE via OpenRouter (optional but recommended)
OPENROUTER_API_KEY=              # Get free key at openrouter.ai
```

### 4. Get your tokens

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

**OpenRouter API Key** *(for auto price detection — free)*
- Sign up at https://openrouter.ai → Keys → Create key → copy it
- No credit card required — the Nemotron models used are completely free
- Without this, Entry/SL/TP must be entered manually

### 5. Create the Notion database

```bash
python setup_notion.py
```

The script will:
- List pages your integration can access → pick one as the parent
- Create the full database with all properties and formulas
- Print the `NOTION_DATABASE_ID` → paste it into `.env`

### 6. Run the bot

```bash
python main.py
```

---

## Auto price detection

When `OPENROUTER_API_KEY` is set, after you select a grade the bot automatically analyses the chart screenshot and detects Entry, SL, and TP price levels.

You'll see a confirmation screen with three options:

```
🤖 Detected from chart:

Entry: `84500.00`
SL:    `82000.00`
TP:    `88000.00`
🟢 High confidence

[ ✅ Use these ]  [ ✏️ Edit ]
      [ ⏭️ Skip prices ]
```

| Option | Behaviour |
|---|---|
| ✅ Use these | Accept detected values → saves to Notion immediately, no typing |
| ✏️ Edit | Opens manual entry flow with detected values pre-filled as hints |
| ⏭️ Skip prices | Saves the trade with no price data |

If detection fails or `OPENROUTER_API_KEY` is not set, the bot silently falls back to the original manual entry flow.

**Models used (both free, $0/M tokens):**
| Model | Role |
|---|---|
| `nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free` | Primary — better instruction following |
| `nvidia/nemotron-nano-12b-v2-vl:free` | Fallback — ChartQA and OCR specialist |

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
| Entry | Number | Bot / AI |
| SL | Number | Bot / AI |
| TP | Number | Bot / AI |
| Exit | Number | Bot (auto from result) |
| Planned R:R | **Formula** | Notion (Entry/SL/TP) |
| Actual R:R | **Formula** | Notion (Entry/Exit/SL) |
| Notes | Text | Bot / manual |
| Lessons Learned | Text | Bot / manual |

**Notion formulas used:**

*Planned R:R*
```
if(prop("SL") != 0 and prop("Entry") != 0 and prop("TP") != 0,
  if(prop("Direction") == "Long",
    (prop("TP") - prop("Entry")) / (prop("Entry") - prop("SL")),
    (prop("Entry") - prop("TP")) / (prop("SL") - prop("Entry"))),
  0)
```

*Actual R:R*
```
if(prop("SL") != 0 and prop("Entry") != 0 and prop("Exit") != 0,
  if(prop("Direction") == "Long",
    (prop("Exit") - prop("Entry")) / (prop("Entry") - prop("SL")),
    (prop("Entry") - prop("Exit")) / (prop("SL") - prop("Entry"))),
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

## Deploying 24/7 on a VPS

A $4–6/mo VPS (Hetzner, DigitalOcean, Vultr) running Ubuntu is more than enough — the bot uses almost no RAM or CPU.

### 1. Upload your files

```bash
scp -r ./Jourding root@your-vps-ip:/home/Jourding
```

### 2. Set up the environment on the VPS

```bash
cd /home/Jourding
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 3. Configure your .env

```bash
cp .env.example .env
nano .env    # fill in all your tokens
```

### 4. Install the systemd service

```bash
sudo cp Jourding.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable Jourding
sudo systemctl start Jourding
```

**`Jourding.service`:**
```ini
[Unit]
Description=Jourding Trading Journal Bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=root
WorkingDirectory=/home/Jourding
ExecStartPre=/bin/sleep 10
ExecStart=/home/Jourding/venv/bin/python3 /home/Jourding/main.py
Restart=always
RestartSec=10
EnvironmentFile=/home/Jourding/.env
StandardOutput=journal
StandardError=journal
SyslogIdentifier=jourding

[Install]
WantedBy=multi-user.target
```

> `ExecStartPre=/bin/sleep 10` gives the network a moment to fully stabilise after boot before the bot tries to connect to Telegram — prevents `NetworkError` on startup.

### 5. Useful commands

```bash
sudo systemctl status Jourding       # check running status
sudo journalctl -u Jourding -f       # live log stream
sudo systemctl restart Jourding      # restart after code changes
sudo systemctl stop Jourding         # stop the bot
```

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

**Auto price detection not working / falls back to manual**
- Make sure `OPENROUTER_API_KEY` is set in `.env`
- Check the logs: `journalctl -u Jourding -f` — look for `chart_reader` lines
- Free models can occasionally be rate-limited; the bot automatically retries with the fallback model
- If both models are unavailable, the bot falls back to manual entry gracefully

**`NetworkError: httpx.ReadError` on startup (systemd)**
- This happens when the service starts before the network is fully ready
- The `ExecStartPre=/bin/sleep 10` in the service file fixes this
- If it persists after reboots, increase the sleep value to `20`

**Open trades not showing in After flow**
- Make sure the trade was logged with `Status: Open` — only trades created by this bot appear
- If you moved trades manually in Notion, check their Status field

**Custom setups not appearing**
- Check that `custom_setups.json` exists in the same folder as `main.py`
- If you deleted it, your custom setups are gone — re-add them via `✏️ Type my own`
