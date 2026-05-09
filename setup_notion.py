"""
setup_notion_v3.py
──────────────────
Run ONCE to create the optimised Notion database.

Schema changes vs v2:
  - Setup        → multi_select  (multiple strategies per trade)
  - Planned RR   → Notion formula  (auto-calc from Entry/SL/TP)
  - Actual RR    → Notion formula  (auto-calc from Entry/Exit/SL)
  - Exit         → auto-filled by bot (Win=TP, Loss=SL, BE=Entry)
  - Removed:     Position Size, Pip Value, PnL ($), Mistakes
"""

import os, sys, requests
from dotenv import load_dotenv

load_dotenv()

NOTION_TOKEN   = os.getenv("NOTION_TOKEN", "")
NOTION_PAGE_ID = os.getenv("NOTION_PARENT_PAGE_ID", "")
API            = "https://api.notion.com/v1"
VERSION        = "2022-06-28"
HEADERS        = {
    "Authorization":  f"Bearer {NOTION_TOKEN}",
    "Notion-Version": VERSION,
    "Content-Type":   "application/json",
}

# ── Notion formulas ───────────────────────────────────────────────────────────
# Returns 0 safely when prices are missing
PLANNED_RR_FORMULA = (
    'if(prop("SL") != 0 and prop("Entry") != 0 and prop("TP") != 0, '
    'if(prop("Direction") == "Long", '
    '   (prop("TP") - prop("Entry")) / (prop("Entry") - prop("SL")), '
    '   (prop("Entry") - prop("TP")) / (prop("SL") - prop("Entry"))), '
    '0)'
)
ACTUAL_RR_FORMULA = (
    'if(prop("SL") != 0 and prop("Entry") != 0 and prop("Exit") != 0, '
    'if(prop("Direction") == "Long", '
    '   (prop("Exit") - prop("Entry")) / (prop("Entry") - prop("SL")), '
    '   (prop("Entry") - prop("Exit")) / (prop("SL") - prop("Entry"))), '
    '0)'
)


def create_database(parent_page_id: str) -> dict:
    payload = {
        "parent": {"type": "page_id", "page_id": parent_page_id},
        "icon":   {"type": "emoji", "emoji": "📒"},
        "title":  [{"type": "text", "text": {"content": "Trading Journal"}}],
        "properties": {

            # ── Identity ──────────────────────────────────────────────────────
            "Symbol":    {"title": {}},
            "Date":      {"date":  {}},

            "Status": {
                "select": {"options": [
                    {"name": "Open",   "color": "yellow"},
                    {"name": "Closed", "color": "default"},
                ]}
            },
            "Direction": {
                "select": {"options": [
                    {"name": "Long",  "color": "green"},
                    {"name": "Short", "color": "red"},
                ]}
            },
            "Result": {
                "select": {"options": [
                    {"name": "Win",  "color": "green"},
                    {"name": "Loss", "color": "red"},
                    {"name": "BE",   "color": "gray"},
                ]}
            },

            # ── Setup (multi-select — pick as many as needed) ─────────────────
            "Setup": {
                "multi_select": {"options": [
                    {"name": "SMT",      "color": "blue"},
                    {"name": "IRL-ERL",  "color": "purple"},
                    {"name": "ERL-IRL",  "color": "pink"},
                    {"name": "Breakout", "color": "green"},
                    {"name": "Retest",   "color": "yellow"},
                    {"name": "Reversal", "color": "orange"},
                    {"name": "Trend",    "color": "brown"},
                    {"name": "Range",    "color": "gray"},
                    {"name": "News",     "color": "red"},
                ]}
            },

            # ── Context ───────────────────────────────────────────────────────
            "Session": {
                "select": {"options": [
                    {"name": "Asia",     "color": "yellow"},
                    {"name": "London",   "color": "blue"},
                    {"name": "New York", "color": "green"},
                    {"name": "Overlap",  "color": "orange"},
                ]}
            },
            "Emotion": {
                "select": {"options": [
                    {"name": "Calm",      "color": "green"},
                    {"name": "Confident", "color": "blue"},
                    {"name": "FOMO",      "color": "orange"},
                    {"name": "Fearful",   "color": "yellow"},
                    {"name": "Greedy",    "color": "red"},
                    {"name": "Revenge",   "color": "red"},
                    {"name": "Bored",     "color": "gray"},
                ]}
            },
            "Grade": {
                "select": {"options": [
                    {"name": "A", "color": "green"},
                    {"name": "B", "color": "yellow"},
                    {"name": "C", "color": "orange"},
                ]}
            },

            # ── Prices (bot fills Entry/SL/TP on open, Exit on close) ─────────
            "Entry": {"number": {"format": "number"}},
            "SL":    {"number": {"format": "number"}},
            "TP":    {"number": {"format": "number"}},
            "Exit":  {"number": {"format": "number"}},

            # ── Auto-calculated by Notion formulas ────────────────────────────
            "Planned RR": {"formula": {"expression": PLANNED_RR_FORMULA}},
            "Actual RR":  {"formula": {"expression": ACTUAL_RR_FORMULA}},

            # ── Notes ─────────────────────────────────────────────────────────
            "Notes":           {"rich_text": {}},
            "Lessons Learned": {"rich_text": {}},
        },
    }
    r = requests.post(f"{API}/databases", headers=HEADERS, json=payload, timeout=15)
    r.raise_for_status()
    return r.json()


def get_parent_page_id() -> str:
    r = requests.post(
        f"{API}/search",
        headers=HEADERS,
        json={"filter": {"value": "page", "property": "object"}, "page_size": 10},
        timeout=15,
    )
    r.raise_for_status()
    pages = [p for p in r.json().get("results", []) if p["object"] == "page"]

    if not pages:
        print("\n⚠️  No pages found — share a Notion page with your integration first.")
        raw = input("Paste the parent page URL or ID: ").strip()
        if "notion.so" in raw:
            raw = raw.rstrip("/").split("/")[-1].split("?")[0][-32:]
        return raw

    print("\nAvailable pages:")
    for i, p in enumerate(pages):
        title = ""
        for v in p.get("properties", {}).values():
            if v.get("type") == "title":
                title = "".join(t.get("plain_text", "") for t in v.get("title", []))
                break
        print(f"  [{i+1}] {title or '(untitled)'}  —  {p['id']}")

    choice = input("\nEnter number (or paste page URL/ID): ").strip()
    if choice.isdigit() and 1 <= int(choice) <= len(pages):
        return pages[int(choice) - 1]["id"]
    if "notion.so" in choice:
        choice = choice.rstrip("/").split("/")[-1].split("?")[0][-32:]
    return choice


def main():
    if not NOTION_TOKEN:
        print("❌ NOTION_TOKEN not set in .env")
        sys.exit(1)

    print("━━━ Notion Trading Journal Setup v3 ━━━\n")
    parent_id = (NOTION_PAGE_ID or get_parent_page_id()).replace("-", "")
    if not parent_id:
        print("❌ No parent page ID provided.")
        sys.exit(1)

    print("⏳ Creating database…")
    try:
        db = create_database(parent_id)
    except requests.HTTPError as e:
        print(f"❌ Notion error {e.response.status_code}:\n{e.response.text}")
        sys.exit(1)

    db_id  = db["id"].replace("-", "")
    db_url = db.get("url", "")
    print(f"""
✅ Done!

URL: {db_url}

━━━ Paste into your .env ━━━

NOTION_DATABASE_ID={db_id}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━
""")

if __name__ == "__main__":
    main()
