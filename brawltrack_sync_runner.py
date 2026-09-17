"""One-shot BrawlTrack -> Supabase sync entrypoint.

Designed for Render Cron.  Keeps scheduling outside the Telegram web process so
restarts cannot create duplicate sync loops.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone


def main() -> int:
    try:
        from brawltrack_meta_sync import sync_brawltrack_meta
        result = sync_brawltrack_meta()
        print(json.dumps({
            "ok": True,
            "source": "brawltrack",
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "result": result,
        }, ensure_ascii=False, default=str))
        return 0
    except Exception as exc:
        print(json.dumps({
            "ok": False,
            "source": "brawltrack",
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "error": f"{type(exc).__name__}: {exc}",
        }, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
