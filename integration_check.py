"""Run inside Compose: real PostgreSQL persistence and outage-replay checks."""

import json
import os
from pathlib import Path
import tempfile
import uuid
from unittest.mock import patch

import psycopg
from PIL import Image

from supervisor import config, cycle


def main():
    cfg = config()
    cfg["stream"] = "integration:" + str(uuid.uuid4())
    # Fixtures are deliberately isolated from the real stream's learned state.
    with tempfile.TemporaryDirectory() as temporary:
        cfg["data"] = Path(temporary) / "evidence"
        first = Path("/fixtures/live-frame.jpg")
        second = Path(temporary) / "second.png"
        changed = Image.open(first).convert("RGB")
        # A changed background area makes a fresh frame without altering OCR.
        from PIL import ImageDraw
        ImageDraw.Draw(changed).rectangle((1800, 0, 1919, 40), fill="black")
        changed.save(second)
        a = cycle(cfg, first)
        assert a["raw_counter"] == 58857 and a["counter_status"] == "provisional", a
        b = cycle(cfg, second)
        assert b["accepted_counter"] == 58857 and b["counter_status"] == "accepted_rebased", b
        c = cycle(cfg, Path("/fixtures/latest/live-frame.jpg"))
        assert c["raw_counter"] == 59062 and c["counter_status"] == "rejected", c
        # Models are reloaded from PostgreSQL on every cycle, including after
        # a fresh process starts. A rejected spike must leave the anchor intact.
        with psycopg.connect() as conn:
            saved = conn.execute("SELECT state FROM monitor_state WHERE stream_url=%s", (cfg["stream"],)).fetchone()[0]
            assert saved["counter"]["anchor"]["value"] == 58857
            assert saved["clothing"]["views"]["presenter_main"]["baseline_observation"] == a["id"]
        with patch.dict(os.environ, {"DATABASE_URL": "host=127.0.0.1 port=1 dbname=unavailable"}):
            offline = cycle(cfg, first)
        assert offline["status"] == "database_unavailable"
        assert len(list((cfg["data"] / "outbox").glob("*.json"))) == 1
        cycle(cfg, first)
        assert not list((cfg["data"] / "outbox").glob("*.json"))
        with psycopg.connect() as conn:
            assert conn.execute("SELECT count(*) FROM observations WHERE id=%s", (offline["id"],)).fetchone()[0] == 1
            rows = conn.execute("SELECT count(*) FROM observations WHERE stream_url=%s", (cfg["stream"],)).fetchone()[0]
        print(json.dumps({"integration": "passed", "stream": cfg["stream"], "observations": rows}))


if __name__ == "__main__":
    main()
