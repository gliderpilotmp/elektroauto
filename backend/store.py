"""Speichert den Verlauf (Ladezustand, Kilometer, Position) und Einstellungen in SQLite."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

# Ein neuer Messpunkt wird gespeichert, wenn sich etwas geändert hat
# oder spätestens nach dieser Zeit (Sekunden)
KEEPALIVE_SECONDS = 3600


class Store:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.Lock()
        with self.lock, self.db:
            self.db.executescript(
                """
                CREATE TABLE IF NOT EXISTS snapshots (
                    ts INTEGER NOT NULL,
                    soc REAL, range_km REAL, odometer REAL,
                    charging INTEGER, plugged_in INTEGER,
                    lat REAL, lon REAL
                );
                CREATE INDEX IF NOT EXISTS snapshots_ts ON snapshots (ts);
                CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
                CREATE TABLE IF NOT EXISTS push_subscriptions (
                    endpoint TEXT PRIMARY KEY, data TEXT NOT NULL, created INTEGER
                );
                """
            )

    # ---- Einstellungen ------------------------------------------------------

    def get(self, key: str, default: Any = None) -> Any:
        with self.lock:
            row = self.db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return json.loads(row["value"]) if row else default

    def set(self, key: str, value: Any) -> None:
        with self.lock, self.db:
            self.db.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, json.dumps(value)),
            )

    # ---- Push-Abos ----------------------------------------------------------

    def add_subscription(self, sub: dict[str, Any]) -> None:
        with self.lock, self.db:
            self.db.execute(
                "INSERT OR REPLACE INTO push_subscriptions VALUES (?, ?, ?)",
                (sub["endpoint"], json.dumps(sub), int(time.time())),
            )

    def remove_subscription(self, endpoint: str) -> None:
        with self.lock, self.db:
            self.db.execute("DELETE FROM push_subscriptions WHERE endpoint=?", (endpoint,))

    def subscriptions(self) -> list[dict[str, Any]]:
        with self.lock:
            rows = self.db.execute("SELECT data FROM push_subscriptions").fetchall()
        return [json.loads(r["data"]) for r in rows]

    def has_subscription(self, endpoint: str) -> bool:
        with self.lock:
            return bool(
                self.db.execute(
                    "SELECT 1 FROM push_subscriptions WHERE endpoint=?", (endpoint,)
                ).fetchone()
            )

    # ---- Verlauf ------------------------------------------------------------

    def record(self, s: dict[str, Any]) -> bool:
        """Speichert einen Messpunkt, wenn er neu ist. Gibt True zurück, wenn gespeichert."""

        loc = s.get("location") or {}
        row = (
            s.get("soc"),
            s.get("range_km"),
            s.get("odometer"),
            _flag(s.get("charging")),
            _flag(s.get("plugged_in")),
            _round(loc.get("lat"), 5),
            _round(loc.get("lon"), 5),
        )
        if all(v is None for v in row[:3]):
            return False

        now = int(time.time())
        with self.lock, self.db:
            last = self.db.execute(
                "SELECT ts, soc, range_km, odometer, charging, plugged_in, lat, lon "
                "FROM snapshots ORDER BY ts DESC LIMIT 1"
            ).fetchone()
            if last and tuple(last)[1:] == row and now - last["ts"] < KEEPALIVE_SECONDS:
                return False
            self.db.execute("INSERT INTO snapshots VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (now, *row))
        return True

    def history(self, days: int) -> dict[str, Any]:
        since = int(time.time()) - days * 86400
        with self.lock:
            rows = [
                dict(r)
                for r in self.db.execute(
                    "SELECT * FROM snapshots WHERE ts >= ? ORDER BY ts", (since,)
                )
            ]
            # letzter Punkt vor dem Zeitraum: Startwert für Kilometer und Diagramm
            before = self.db.execute(
                "SELECT * FROM snapshots WHERE ts < ? ORDER BY ts DESC LIMIT 1", (since,)
            ).fetchone()

        all_rows = ([dict(before)] if before else []) + rows
        points = [
            {"ts": r["ts"], "soc": r["soc"], "charging": bool(r["charging"])}
            for r in rows
            if r["soc"] is not None
        ]
        return {
            "points": _downsample(points, 600),
            "daily_km": _daily_km(all_rows, since),
            "sessions": _sessions(all_rows),
            "driven_km": _driven(rows, before),
        }


def _flag(v: Any) -> int | None:
    return None if v is None else int(bool(v))


def _round(v: Any, n: int) -> float | None:
    return None if v is None else round(float(v), n)


def _downsample(points: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    if len(points) <= limit:
        return points
    step = len(points) / limit
    out = [points[int(i * step)] for i in range(limit)]
    out[-1] = points[-1]
    return out


def _driven(rows: list[dict[str, Any]], before: sqlite3.Row | None) -> float | None:
    odos = [r["odometer"] for r in rows if r["odometer"] is not None]
    if before is not None and before["odometer"] is not None:
        odos.insert(0, before["odometer"])
    return round(max(odos) - min(odos), 1) if len(odos) >= 2 else None


def _daily_km(rows: list[dict[str, Any]], since: int) -> list[dict[str, Any]]:
    """Gefahrene Kilometer pro Tag (Ortszeit): letzter Stand des Tages minus
    letzter Stand davor."""

    last_per_day: dict[str, float] = {}
    for r in rows:
        if r["odometer"] is None:
            continue
        day = datetime.fromtimestamp(r["ts"]).strftime("%Y-%m-%d")
        last_per_day[day] = max(last_per_day.get(day, 0), r["odometer"])

    first_day = datetime.fromtimestamp(since).strftime("%Y-%m-%d")
    out, prev = [], None
    for day in sorted(last_per_day):
        odo = last_per_day[day]
        if prev is not None and day >= first_day:
            out.append({"day": day, "km": round(max(0.0, odo - prev), 1)})
        prev = odo
    return out


def _sessions(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Ladevorgänge aus den Wechseln des Lade-Status."""

    sessions: list[dict[str, Any]] = []
    cur: dict[str, Any] | None = None
    for r in rows:
        if r["charging"] and cur is None:
            cur = {"start": r["ts"], "end": r["ts"], "soc_start": r["soc"], "soc_end": r["soc"]}
        elif cur is not None:
            cur["end"] = r["ts"]
            if r["soc"] is not None:
                cur["soc_end"] = r["soc"]
            if not r["charging"]:
                sessions.append(cur)
                cur = None
    if cur is not None:
        cur["active"] = True
        sessions.append(cur)
    return list(reversed(sessions))[:20]
