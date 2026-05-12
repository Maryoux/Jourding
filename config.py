import os
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_TOKEN      = os.getenv("TELEGRAM_TOKEN", "")
ALLOWED_USER_ID     = int(os.getenv("ALLOWED_USER_ID", "0"))
NOTION_TOKEN        = os.getenv("NOTION_TOKEN", "")
NOTION_DATABASE_ID  = os.getenv("NOTION_DATABASE_ID", "")
IMGBB_API_KEY       = os.getenv("IMGBB_API_KEY", "")        # optional — screenshot hosting
OPENROUTER_API_KEY  = os.getenv("OPENROUTER_API_KEY", "")  # optional — auto chart-reading (free)

def validate():
    missing = []
    if not TELEGRAM_TOKEN:     missing.append("TELEGRAM_TOKEN")
    if not NOTION_TOKEN:       missing.append("NOTION_TOKEN")
    if not NOTION_DATABASE_ID: missing.append("NOTION_DATABASE_ID")
    if missing:
        raise EnvironmentError(f"Missing required env vars: {', '.join(missing)}")
