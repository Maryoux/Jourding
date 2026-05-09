"""
setup_notion.py
───────────────
Run ONCE to create the Notion trading journal database with correct schema.

    python setup_notion.py

Prints the DATABASE_ID to paste into your .env.
"""

import os
import sys
import requests
from dotenv import load_dotenv

load_dotenv()

NOTION_TOKEN   = os.getenv("NOTION_TOKEN", "")
NOTION_PAGE_ID = os.getenv("NOTION_PARENT_PAGE_ID", "")
API            = "https://api.notion.com/v1"
VERSION        = "2022-06-28"

HEADERS = {
    "Authorization":  f"Bearer {NOTION_TOKEN}",
    "Notion-Version": VERSION,
    "Content-Type":   "application/json",
}


def create_database(parent_page_id: str) -> dict:
    payload = {
        "parent": {"type": "page_id", "page_id": parent_page_id},
        "icon":   {"type": "emoji",   "emoji":   "📒"},
        "title":  [{"type": "text",   "text":    {"content": "Trading Journal"}}],
        "properties": {

            # Title
            "Symbol": {"title": {}},

            # Status — Open / Closed
            "Status": {
                "select": {
                    "options": [
                        {"name": "Open",   "color": "yellow"},
                        {"name": "Closed", "color": "gray"},
                    ]
                }
            },

            # Trade info selects
            "Direction": {
                "select": {
                    "options": [
                        {"name": "Long",  "color": "green"},
                        {"name": "Short", "color": "red"},
                    ]
                }
            },
            "Result": {
                "select": {
                    "options": [
                        {"name": "Win",  "color": "green"},
                        {"name": "Loss", "color": "red"},
                        {"name": "BE",   "color": "gray"},
                    ]
                }
            },
            "Grade": {
                "select": {
                    "options": [
                        {"name": "A", "color": "green"},
                        {"name": "B", "color": "yellow"},
                        {"name": "C", "color": "orange"},
                    ]
                }
            },
            "Setup": {
                "select": {
                    "options": [
                        {"name": "Breakout",    "color": "blue"},
                        {"name": "Retest",      "color": "green"},
                        {"name": "Reversal",    "color": "purple"},
                        {"name": "Trend Follow","color": "yellow"},
                        {"name": "Range",       "color": "pink"},
                        {"name": "News",        "color": "orange"},
                        {"name": "Other",       "color": "gray"},
                    ]
                }
            },
            "Session": {
                "select": {
                    "options": [
                        {"name": "Asia",     "color": "yellow"},
                        {"name": "London",   "color": "blue"},
                        {"name": "New York", "color": "green"},
                        {"name": "Overlap",  "color": "orange"},
                    ]
                }
            },
            "Emotion": {
                "select": {
                    "options": [
                        {"name": "Calm",      "color": "green"},
                        {"name": "Confident", "color": "blue"},
                        {"name": "FOMO",      "color": "orange"},
                        {"name": "Fearful",   "color": "yellow"},
                        {"name": "Greedy",    "color": "red"},
                        {"name": "Revenge",   "color": "red"},
                        {"name": "Bored",     "color": "gray"},
                    ]
                }
            },

            # Numbers
            "Entry":         {"number": {"format": "number"}},
            "Exit":          {"number": {"format": "number"}},
            "Stop Loss":     {"number": {"format": "number"}},
            "Take Profit":   {"number": {"format": "number"}},
            "Position Size": {"number": {"format": "number"}},
            "Pip Value":     {"number": {"format": "number"}},
            "PnL":           {"number": {"format": "number"}},
            "RR":            {"number": {"format": "number"}},

            # Date & text
            "Date":              {"date":       {}},
            "Notes":             {"rich_text":  {}},
            "Lessons Learned":   {"rich_text":  {}},
            "Mistakes":          {"rich_text":  {}},
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
        print("\n⚠️  No pages found. Share a Notion page with your integration first.")
        print("   Notion page → ··· → Connections → add your integration\n")
        raw = input("Paste the parent page URL or ID: ").strip()
        if "notion.so" in raw:
            raw = raw.rstrip("/").split("/")[-1].split("?")[0][-32:]
        return raw

    print("\nAvailable pages:")
    for i, p in enumerate(pages):
        props = p.get("properties", {})
        title = ""
        for v in props.values():
            if v.get("type") == "title":
                title = "".join(t.get("plain_text","") for t in v.get("title",[]))
                break
        print(f"  [{i+1}] {title or '(untitled)'}  —  {p['id']}")

    choice = input("\nEnter number (or paste a different page ID/URL): ").strip()
    if choice.isdigit() and 1 <= int(choice) <= len(pages):
        return pages[int(choice) - 1]["id"]
    if "notion.so" in choice:
        choice = choice.rstrip("/").split("/")[-1].split("?")[0][-32:]
    return choice


def main():
    if not NOTION_TOKEN:
        print("❌ NOTION_TOKEN not set in .env")
        sys.exit(1)

    print("━━━ Notion Trading Journal Setup ━━━\n")

    parent_id = (NOTION_PAGE_ID or get_parent_page_id()).replace("-", "")
    if not parent_id:
        print("❌ No parent page ID provided.")
        sys.exit(1)

    print(f"\n⏳ Creating database inside page {parent_id} …")
    try:
        db = create_database(parent_id)
    except requests.HTTPError as e:
        print(f"❌ Notion API error {e.response.status_code}:\n{e.response.text}")
        sys.exit(1)

    db_id  = db["id"].replace("-", "")
    db_url = db.get("url", "")

    print(f"""
✅ Database created!

URL: {db_url}

━━━ Paste this into your .env ━━━

NOTION_DATABASE_ID={db_id}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
""")


if __name__ == "__main__":
    main()
