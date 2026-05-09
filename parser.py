"""
parser.py
─────────
Parses /trade messages and calculates P&L, R:R, Result.
"""

from dataclasses import dataclass, field
from typing import Optional


# ── Field aliases (all uppercase) ────────────────────────────────────────────

ALIASES: dict[str, str] = {
    "SYMBOL": "symbol",  "SYM":   "symbol",  "PAIR":  "symbol",
    "DIR":    "dir",     "DIRECTION": "dir",  "SIDE":  "dir",
    "ENTRY":  "entry",   "ENTRY PRICE": "entry",
    "EXIT":   "exit",    "EXIT PRICE": "exit","CLOSE": "exit",
    "SL":     "sl",      "STOP":  "sl",       "STOP LOSS": "sl",
    "TP":     "tp",      "TARGET":"tp",        "TAKE PROFIT": "tp",
    "SIZE":   "size",    "LOT":   "size",      "LOTS": "size",
    "PIPVAL": "pipval",  "PIP VALUE": "pipval",
    "SETUP":  "setup",   "PATTERN":"setup",    "STRATEGY": "setup",
    "GRADE":  "grade",   "SCORE": "grade",
    "EMOTION":"emotion", "MOOD":  "emotion",   "PSYCH": "emotion",
    "SESSION":"session", "SES":   "session",
    "NOTE":   "note",    "NOTES": "note",      "COMMENT": "note",
    "LESSON": "lesson",  "LESSONS":"lesson",   "LEARNING": "lesson",
    "MISTAKE":"mistake", "ERROR": "mistake",
}


# ── Data model ────────────────────────────────────────────────────────────────

@dataclass
class Trade:
    # Required
    symbol: str = ""
    dir:    str = ""      # Long | Short
    entry:  float = 0.0
    exit:   float = 0.0

    # Optional inputs
    sl:       Optional[float] = None
    tp:       Optional[float] = None
    size:     float = 1.0
    pipval:   float = 1.0
    setup:    str = ""
    grade:    str = ""    # A | B | C
    emotion:  str = ""
    session:  str = ""    # London | NY | Asia | etc.
    note:     str = ""
    lesson:   str = ""
    mistake:  str = ""

    # Auto-calculated
    pnl:    float = 0.0
    rr:     Optional[float] = None
    result: str = ""      # Win | Loss | BE

    def calculate(self):
        is_long = self.dir.lower() == "long"
        self.pnl = round(
            ((self.exit - self.entry) if is_long else (self.entry - self.exit))
            * self.size * self.pipval,
            2
        )
        if self.sl and self.tp and self.entry:
            risk   = (self.entry - self.sl) if is_long else (self.sl - self.entry)
            reward = (self.tp - self.entry) if is_long else (self.entry - self.tp)
            self.rr = round(reward / risk, 2) if risk > 0 else None

        self.result = (
            "Win"  if self.pnl >  0.01 else
            "Loss" if self.pnl < -0.01 else
            "BE"
        )
        return self

    def summary(self) -> str:
        emoji = "🟢" if self.result == "Win" else "🔴" if self.result == "Loss" else "⚪"
        dir_cap = self.dir.capitalize()
        pnl_str = f"{'+'if self.pnl >= 0 else ''}{self.pnl:.2f}"
        rr_str  = f"{self.rr:.2f}R" if self.rr is not None else "—"
        lines   = [
            f"{emoji} *{self.symbol} {dir_cap} — {self.result}*",
            f"",
            f"Entry: `{self.entry}` → Exit: `{self.exit}`",
            f"P&L: `${pnl_str}` | R:R: `{rr_str}`",
        ]
        extras = []
        if self.setup:   extras.append(f"Setup: {self.setup}")
        if self.grade:   extras.append(f"Grade: {self.grade}")
        if self.session: extras.append(f"Session: {self.session}")
        if self.emotion: extras.append(f"Emotion: {self.emotion}")
        if extras:
            lines.append("  ".join(extras))
        if self.note:
            lines += ["", f"📝 _{self.note}_"]
        return "\n".join(lines)


# ── Parser ────────────────────────────────────────────────────────────────────

class ParseError(Exception):
    pass


def parse(text: str) -> Trade:
    """
    Parse a /trade message into a Trade object.
    Raises ParseError with a human-readable message on failure.
    """
    raw: dict[str, str] = {}
    lines = [l.strip() for l in text.strip().splitlines() if l.strip()]

    # Skip the /trade command line itself
    lines = [l for l in lines if not l.lower().startswith("/trade")]

    for line in lines:
        if ":" not in line:
            continue
        raw_key, _, raw_val = line.partition(":")
        key   = raw_key.strip().upper()
        value = raw_val.strip()
        field = ALIASES.get(key)
        if field:
            raw[field] = value

    # ── Validate required fields ──────────────────────────────────────────────
    errors = []
    for req in ("symbol", "dir", "entry", "exit"):
        if req not in raw or not raw[req]:
            errors.append(f"Missing *{req.upper()}*")

    if "dir" in raw and raw["dir"].lower() not in ("long", "short"):
        errors.append("DIR must be *Long* or *Short*")

    for numeric in ("entry", "exit", "sl", "tp", "size", "pipval"):
        if numeric in raw:
            try:
                float(raw[numeric])
            except ValueError:
                errors.append(f"{numeric.upper()} must be a number, got: `{raw[numeric]}`")

    if "grade" in raw and raw["grade"].upper() not in ("", "A", "B", "C"):
        errors.append("GRADE must be A, B, or C")

    if errors:
        raise ParseError("\n".join(f"• {e}" for e in errors))

    # ── Build Trade object ────────────────────────────────────────────────────
    def f(k):  return float(raw[k]) if k in raw and raw[k] else None
    def s(k):  return raw.get(k, "").strip()

    t = Trade(
        symbol  = s("symbol").upper(),
        dir     = s("dir").capitalize(),
        entry   = float(raw["entry"]),
        exit    = float(raw["exit"]),
        sl      = f("sl"),
        tp      = f("tp"),
        size    = float(raw.get("size", 1)),
        pipval  = float(raw.get("pipval", 1)),
        setup   = s("setup"),
        grade   = s("grade").upper(),
        emotion = s("emotion").capitalize(),
        session = s("session"),
        note    = s("note"),
        lesson  = s("lesson"),
        mistake = s("mistake"),
    )
    t.calculate()
    return t
