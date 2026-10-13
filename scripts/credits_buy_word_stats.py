from __future__ import annotations

import datetime as dt
import re
from collections import Counter
from pathlib import Path

LOG_DIR = Path(
    r"C:\Users\borkedcube\AppData\Roaming\Chatterino2\Logs\Twitch"
    r"\Channels\abrokecube"
)
OUTPUT_DIR = Path("output")
CUTOFF_DATE = dt.date(2026, 10, 7)  # logs from the last 3 months
PREFIX = "!credits buy"

# [00:00:13] user: message
LINE_RE = re.compile(r"^\[(\d{2}:\d{2}:\d{2})\]\s+(\S+?):\s+(.*)$")
# Trailing purchase count, e.g. "gold nugget 1" -> "gold nugget"
TRAILING_COUNT_RE = re.compile(r"\s+\d+\s*$")
BOT_USERS = {"cubedhelperbot"}


def normalize_phrase(text: str) -> str:
    return " ".join(text.split()).lower()


def main() -> None:
    counts: Counter[str] = Counter()
    matches = 0
    files_processed = 0
    cutoff = CUTOFF_DATE - dt.timedelta(days=90)

    for log_file in sorted(LOG_DIR.glob("*.log")):
        stem = log_file.stem  # e.g. abrokecube-2026-10-07
        try:
            y, m, d = tuple(int(p) for p in stem.split("-")[-3:])
        except ValueError:
            continue
        if dt.date(y, m, d) < cutoff:
            continue
        files_processed += 1
        with log_file.open(encoding="utf-8", errors="replace") as fh:
            for line in fh:
                match = LINE_RE.match(line)
                if not match:
                    continue
                if match.group(2).lower() in BOT_USERS:
                    continue
                msg = match.group(3)
                if not msg.lower().startswith(PREFIX):
                    continue
                matches += 1
                rest = msg[len(PREFIX) :].strip()
                if not rest:
                    continue
                phrase = TRAILING_COUNT_RE.sub("", rest)
                counts[normalize_phrase(phrase)] += 1

    OUTPUT_DIR.mkdir(exist_ok=True)
    out_file = OUTPUT_DIR / "credits_buy_words.txt"
    with out_file.open("w", encoding="utf-8") as fh:
        fh.write(
            f"Most used phrases after '!credits buy' "
            f"({cutoff.isoformat()} to {CUTOFF_DATE.isoformat()})\n"
        )
        fh.write(f"Files processed: {files_processed}, matches: {matches}\n\n")
        for phrase, count in counts.most_common():
            fh.write(f"{count:5d}  {phrase}\n")
    print(f"Wrote {out_file} ({matches} matches, {len(counts)} unique phrases)")


if __name__ == "__main__":
    main()
