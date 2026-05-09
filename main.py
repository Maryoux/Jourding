"""
main.py
───────
Entry point. Registers all handlers and starts polling.
"""

import logging
from telegram.ext import ApplicationBuilder, CommandHandler

import config
from handlers import build_conv_handler, cmd_start, cmd_stats, cmd_cancel, cmd_open

logging.basicConfig(
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%H:%M:%S",
    level=logging.INFO,
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.WARNING)

log = logging.getLogger(__name__)


def main():
    config.validate()

    app = ApplicationBuilder().token(config.TELEGRAM_TOKEN).build()

    app.add_handler(build_conv_handler())
    app.add_handler(CommandHandler("start",  cmd_start))
    app.add_handler(CommandHandler("stats",  cmd_stats))
    app.add_handler(CommandHandler("open",   cmd_open))
    app.add_handler(CommandHandler("cancel", cmd_cancel))

    log.info("━━━ Trading Journal Bot ready ━━━")
    app.run_polling(allowed_updates=["message", "callback_query"])


if __name__ == "__main__":
    main()
