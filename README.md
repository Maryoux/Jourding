# Jourding 🐍

Pure Python Telegram bot that logs trades directly to Notion.
No n8n. No Zapier. No third-party automation layers.

## Project structure

```
Jourding/
├── main.py           # Entry point — starts the bot
├── handlers.py       # All Telegram command handlers
├── parser.py         # Message parser + Trade dataclass + calculations
├── notion_client.py  # Notion API wrapper (create pages, query stats)
├── config.py         # Env variable loader + validation
├── requirements.txt
└── .env.example      # Copy to .env and fill in your tokens
```

## Quick start

```bash
# 1. Clone / copy the folder
cd Jourding 

# 2. Install dependencies
pip install -r requirements.txt

# 3. Set up environment
cp .env.example .env
# Edit .env with your tokens (see below)

# 4. Run
python main.py
```

## Tokens you need

### Telegram Bot Token
1. Open Telegram → search `@BotFather`
2. Send `/newbot` → follow prompts
3. Copy the token into `TELEGRAM_TOKEN`

### Your Telegram User ID
1. Open `@userinfobot` in Telegram
2. It replies with your user ID
3. Paste into `ALLOWED_USER_ID`

### Notion Integration Token
1. Go to https://www.notion.so/my-integrations
2. New integration → give it a name → copy the secret
3. Paste into `NOTION_TOKEN`
4. In Notion: open your trading database → ⋯ menu → Connections → add your integration

### Notion Database ID
Your database URL looks like:
`https://notion.so/yourworkspace/XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX?v=...`
Copy the 32-character ID and paste into `NOTION_DATABASE_ID`

---

## Notion database schema

Create a database with these exact property names:

| Name | Type |
|---|---|
| Symbol | Title |
| Direction | Select (Long, Short) |
| Entry | Number |
| Exit | Number |
| Stop Loss | Number |
| Take Profit | Number |
| Position Size | Number |
| Pip Value | Number |
| P&L | Number |
| R:R | Number |
| Result | Select (Win, Loss, BE) |
| Setup | Select |
| Grade | Select (A, B, C) |
| Emotion | Select |
| Session | Select |
| Date | Date |
| Notes | Text |
| Lessons Learned | Text |
| Mistakes | Text |

---

## Bot commands

| Command | What it does |
|---|---|
| `/trade` | Log a new trade with full details |
| `/stats` | Win rate, P&L, profit factor, best/worst trade |
| `/calc` | Quick P&L calculator (no Notion required) |
| `/help` | Show the trade message format |
| `/start` | Welcome + command list |

---

## Trade message format

Send this to the bot (order doesn't matter, case-insensitive):

```
/trade
SYMBOL: XAUUSD
DIR: Long
ENTRY: 2340.50
EXIT: 2358.20
SL: 2330.00
TP: 2360.00
SIZE: 0.5
PIPVAL: 10
SETUP: Breakout
GRADE: A
EMOTION: Calm
SESSION: London
NOTE: Clean break above resistance, held entry zone well
LESSON: Wait for candle close before entering
MISTAKE: Moved SL too early out of fear
```

Attach a chart screenshot to the same message — the bot forwards it to Notion.

**Shorthand aliases:** `SYM` `PAIR` `SIDE` `CLOSE` `STOP` `TARGET` `LOT` `PATTERN` `MOOD`

---

## Quick calc (no Notion)

```
/calc long 2340.50 2358.20 sl=2330 tp=2360 size=0.5 pipval=10
```

Bot replies instantly with P&L, R:R, and result — no Notion page created.

---

## Running 24/7

### With screen (simplest)
```bash
screen -S tradebot
python main.py
# Detach: Ctrl+A then D
# Reattach: screen -r tradebot
```

### As a systemd service (Linux)
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
sudo systemctl enable tradebot && sudo systemctl start tradebot
sudo journalctl -u tradebot -f   # watch logs
```

### On a VPS (cheapest option)
Any $4/mo VPS (DigitalOcean, Hetzner, Vultr) running Ubuntu works fine.
The bot uses virtually no RAM or CPU.

---

## Troubleshooting

**Bot doesn't respond**
- Check `TELEGRAM_TOKEN` is correct
- Make sure you sent `/start` to the bot first
- Verify `ALLOWED_USER_ID` matches your real Telegram ID (use `@userinfobot`)

**Notion page not created**
- Check `NOTION_TOKEN` and `NOTION_DATABASE_ID`
- Did you share the database with the integration in Notion?
- Property names are case-sensitive — they must match the schema exactly

**Screenshot not in Notion**
- Telegram file URLs expire after ~1 hour — get a free `IMGBB_API_KEY` for permanent links
- Without imgbb, the cover image works for about an hour after logging

**Parse error on a valid message**
- Make sure each field is on its own line
- Format is `FIELDNAME: value` — colon and space required
- Run `/help` to double-check the format
