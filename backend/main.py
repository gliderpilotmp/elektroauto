"""Elektrocar – Backend für die Fiat 500e Verwaltung.

Spricht über die (inoffizielle) Uconnect-Cloud-API mit dem Auto
(Bibliothek ``py-uconnect``) und stellt eine kleine REST-API sowie die
Web-Oberfläche bereit. Ohne Zugangsdaten läuft ein Demo-Modus.

Ein Hintergrunddienst fragt das Auto regelmäßig ab, speichert den Verlauf
und schickt Benachrichtigungen bei 80 % und 100 % Ladezustand.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import random
import secrets
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import carimage
from notify import Notifier
from store import Store

log = logging.getLogger("elektrocar")
logging.basicConfig(level=logging.INFO)

FRONTEND = Path(__file__).resolve().parent.parent / "frontend"

EMAIL = os.environ.get("UCONNECT_EMAIL", "")
PASSWORD = os.environ.get("UCONNECT_PASSWORD", "")
PIN = os.environ.get("UCONNECT_PIN") or None
BRAND = os.environ.get("UCONNECT_BRAND", "FIAT_EU")
VIN = os.environ.get("UCONNECT_VIN") or None
APP_PASSWORD = os.environ.get("APP_PASSWORD", "")
CACHE_SECONDS = int(os.environ.get("CACHE_SECONDS", "60"))
DEMO = os.environ.get("DEMO", "").lower() in ("1", "true", "yes") or not (
    EMAIL and PASSWORD
)
DATA_DIR = Path(os.environ.get("DATA_DIR") or Path(__file__).resolve().parent.parent / "data")
# Wie oft das Auto im Hintergrund abgefragt wird (Minuten); beim Laden öfter
POLL_MINUTES = float(os.environ.get("POLL_MINUTES") or (1 if DEMO else 10))
# Wie lange die Klimatisierung nach dem Einschalten läuft (Minuten), wenn das
# Auto keinen Status meldet
CLIMATE_MINUTES = float(os.environ.get("CLIMATE_MINUTES") or 15)
POLL_MINUTES_CHARGING = float(os.environ.get("POLL_MINUTES_CHARGING") or (1 if DEMO else 5))

# Aktualisieren-Knopf: nach so vielen Ablehnungen in Folge eine Pause einlegen
WAKE_MAX_FAILS = 3
WAKE_PAUSE_HOURS = 6

# Nach so vielen Minuten ohne Verbindung zu Fiat wird einmal benachrichtigt
OUTAGE_NOTIFY_MINUTES = float(os.environ.get("OUTAGE_NOTIFY_MINUTES") or 60)

# Nach einem abgelehnten Login so lange keine neue Anmeldung versuchen (Minuten);
# verdoppelt sich bei jedem weiteren Fehlschlag bis LOGIN_PAUSE_MAX_HOURS.
# Fiat sperrt das Konto sonst wegen zu vieler Fehlversuche.
LOGIN_PAUSE_MINUTES = float(os.environ.get("LOGIN_PAUSE_MINUTES") or 60)
LOGIN_PAUSE_MAX_HOURS = float(os.environ.get("LOGIN_PAUSE_MAX_HOURS") or 24)

# Befehle, die die App senden darf (Name -> Beschreibung)
ALLOWED_COMMANDS = {
    "RDL": "Türen verriegeln",
    "RDU": "Türen entriegeln",
    "HBLF": "Hupe & Licht",
    "ROLIGHTS": "Licht",
    "ROTRUNKUNLOCK": "Kofferraum öffnen",
    "CNOW": "Laden starten",
}


# --------------------------------------------------------------------------
# Datenquellen
# --------------------------------------------------------------------------


def error_text(err: Exception) -> str:
    """Lesbare Fehlermeldung, inkl. Begründung aus der Antwort von Fiat."""

    status = getattr(err, "status", None)
    body = getattr(err, "body", None)
    reason = ""
    if isinstance(body, dict):
        parts = [str(body[k]) for k in ("message", "errorMessage", "description", "name", "code", "error")
                 if body.get(k) not in (None, "")]
        reason = " · ".join(dict.fromkeys(parts))
    elif body:
        reason = str(body).strip()
    reason = reason[:200]
    if status and reason:
        return f"HTTP {status}: {reason}"
    if status:
        return f"HTTP {status} (ohne Begründung)"
    return reason or str(err)


def _is_rejection(err: Exception) -> bool:
    """True, wenn Fiat einen Befehl abgelehnt hat (HTTP 4xx) – nicht bei
    Anmelde- oder Verbindungsfehlern."""

    from py_uconnect import APIError, AuthenticationError

    status = getattr(err, "status", None)
    return (isinstance(err, APIError) and not isinstance(err, AuthenticationError)
            and status is not None and 400 <= status < 500)


class LoginPaused(Exception):
    """Login wird absichtlich nicht versucht (Pause nach Fehlschlägen)."""


def _cred_key() -> str:
    # Ändern sich E-Mail oder Passwort in .env, gilt eine alte Pause nicht mehr
    return hashlib.sha256(f"{EMAIL}\0{PASSWORD}".encode()).hexdigest()[:16]


def _login_paused_text() -> str | None:
    block = store.get("login_block") or {}
    if block.get("key") != _cred_key() or time.time() >= block.get("until", 0):
        return None
    return (f"Anmeldung bei Fiat pausiert bis {_hm(block['until'])} "
            f"nach {block['count']} Fehlschlag/Fehlschlägen ({block.get('reason', '')}). "
            "Ein geändertes Passwort in .env hebt die Pause sofort auf.")


async def _login_failed(reason: str) -> None:
    block = store.get("login_block") or {}
    count = (block.get("count", 0) if block.get("key") == _cred_key() else 0) + 1
    minutes = min(LOGIN_PAUSE_MINUTES * 2 ** (count - 1), LOGIN_PAUSE_MAX_HOURS * 60)
    until = time.time() + minutes * 60
    store.set("login_block", {"key": _cred_key(), "count": count, "until": until, "reason": reason})
    log.warning("Login abgelehnt (%d. Mal): %s – Pause bis %s", count, reason, _hm(until))
    if count == 1:
        await notifier.send(
            "🔒 Fiat-Anmeldung abgelehnt",
            f"{reason}. Die App versucht es erst ab {_hm(until)} wieder, damit Fiat "
            "das Konto nicht sperrt. Prüfe Passwort bzw. die offizielle Fiat-App.",
            tag="login",
        )


def _login_ok() -> None:
    if store.get("login_block"):
        store.set("login_block", {})


def _hm(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%H:%M")


def _summary(v: dict[str, Any]) -> dict[str, Any]:
    """Reduziert die Fahrzeugdaten auf das, was die App anzeigt."""

    loc = v.get("location") or {}
    return {
        "vin": v.get("vin"),
        "name": v.get("nickname") or "Fiat 500e",
        "model": v.get("model") or "500e",
        "year": v.get("year"),
        "image_url": v.get("image_url"),
        "soc": v.get("state_of_charge"),
        "range_km": v.get("distance_to_empty") or v.get("range_total"),
        "range_unit": v.get("distance_to_empty_unit") or "km",
        "odometer": v.get("odometer"),
        "odometer_unit": v.get("odometer_unit") or "km",
        "plugged_in": v.get("plugged_in"),
        "charging": v.get("charging"),
        "charging_level": v.get("charging_level"),
        # hvac_on fehlt beim 500e oft; ergänzt durch _with_climate()
        "climate_on": v.get("hvac_on"),
        "climate_temp": v.get("hvac_temperature"),
        "doors_locked": all(
            x is not False
            for x in (v.get("door_driver_locked"), v.get("door_passenger_locked"))
        )
        if v.get("door_driver_locked") is not None
        else None,
        "location": {
            "lat": loc.get("latitude"),
            "lon": loc.get("longitude"),
            "updated": loc.get("updated"),
            "approximate": loc.get("is_approximate"),
        }
        if loc.get("latitude") is not None
        else None,
        "supported_commands": v.get("supported_commands", []),
        "updated": v.get("timestamp_status") or v.get("timestamp_info"),
    }


class UconnectSource:
    """Echtes Auto über die Uconnect-Cloud."""

    def __init__(self) -> None:
        from py_uconnect import BRANDS, Client

        if BRAND not in BRANDS:
            raise RuntimeError(f"Unbekannte Marke {BRAND!r}, z.B. FIAT_EU")
        self.client = Client(EMAIL, PASSWORD, PIN, BRANDS[BRAND])
        self._wrap_login()
        self.lock = asyncio.Lock()
        self.cache: dict[str, Any] | None = None
        self.cache_time = 0.0
        self.vin: str | None = VIN

    def _wrap_login(self) -> None:
        """Jede neue Anmeldung (Abfrage, Befehle, PIN) läuft hier durch: nach
        abgelehnten Logins wird pausiert, statt Fiat mit Versuchen zu fluten."""

        from py_uconnect import APIError, AuthenticationError

        client = self.client.api
        original = client._ensure_credentials

        async def guarded():
            creds = client._credentials
            if creds is not None and not creds.expires_soon():
                return creds
            paused = _login_paused_text()
            if paused:
                raise LoginPaused(paused)
            try:
                creds = await original()
            except (AuthenticationError, APIError) as err:
                await _login_failed(error_text(err))
                raise
            _login_ok()
            return creds

        client._ensure_credentials = guarded

    async def status(self, force: bool = False) -> dict[str, Any]:
        async with self.lock:
            if force or not self.cache or time.time() - self.cache_time > CACHE_SECONDS:
                vehicles = await self.client.refresh()
                if not vehicles:
                    raise HTTPException(404, "Kein Fahrzeug im Konto gefunden")
                if not self.vin or self.vin not in vehicles:
                    self.vin = next(iter(vehicles))
                self.cache = _summary(vehicles[self.vin].to_dict())
                self.cache_time = time.time()
            return self.cache

    async def _vehicle(self):
        if not self.vin:
            await self.status()
        return self.client.vehicles[self.vin]

    async def wake(self) -> list[str]:
        """Bittet das Auto, frische Daten und Position hochzuladen.
        Gibt Hinweise für die App zurück (z.B. Begründung einer Ablehnung)."""

        from dataclasses import replace

        from py_uconnect.command import COMMAND_DEEP_REFRESH, COMMAND_REFRESH_LOCATION

        v = await self._vehicle()
        # Nur die freigeschaltete Variante senden (DEEPREFRESH bzw. DEEPREFRESH2),
        # ohne Ersatzversuche: sonst geht Fiats Begründung der Ablehnung verloren
        deep = COMMAND_DEEP_REFRESH if "DEEPREFRESH2" in v.enabled_services \
            else replace(COMMAND_DEEP_REFRESH, fallback=None)
        notes: list[str] = []
        for cmd, label in ((deep, "Daten"), (COMMAND_REFRESH_LOCATION, "Position")):
            if not v.supports(cmd):
                continue
            key = f"wake_fail_{cmd.name}"
            fail = store.get(key) or {"count": 0, "until": 0}
            if time.time() < fail["until"]:
                notes.append(f"{label} aktualisieren pausiert bis {_hm(fail['until'])} "
                             "(Fiat hat zuletzt mehrmals abgelehnt)")
                continue
            try:
                await self.client.command(v.vin, cmd)
                store.set(key, {"count": 0, "until": 0})
            except Exception as err:  # noqa: BLE001
                reason = error_text(err)
                if not _is_rejection(err):
                    # Anmelde- oder Verbindungsproblem: zählt nicht als Ablehnung
                    log.warning("%s nicht gesendet: %s", cmd, reason)
                    notes.append(f"{label} aktualisieren: Anmeldung/Verbindung zu Fiat "
                                 f"fehlgeschlagen ({reason})")
                    continue
                count = fail["count"] + 1
                until = time.time() + WAKE_PAUSE_HOURS * 3600 if count >= WAKE_MAX_FAILS else 0
                store.set(key, {"count": count, "until": until, "reason": reason})
                log.warning("%s abgelehnt (%d. Mal): %s", cmd, count, reason)
                msg = f"{label} aktualisieren abgelehnt: {reason}"
                if until:
                    msg += f" – nächster Versuch ab {_hm(until)}"
                notes.append(msg)
        return notes

    async def climate(self, on: bool, temp: float | None) -> bool:
        from py_uconnect.command import (
            COMMAND_HVAC_OFF,
            COMMAND_HVAC_ON,
            COMMAND_PRECOND_OFF,
            COMMAND_PRECOND_ON,
            hvac_params,
        )

        v = await self._vehicle()
        if on and v.supports(COMMAND_HVAC_ON):
            params = hvac_params(temp) if temp is not None else None
            ok = await self.client.command_verify(v.vin, COMMAND_HVAC_ON, params, 90)
        elif not on and v.supports(COMMAND_HVAC_OFF):
            ok = await self.client.command_verify(v.vin, COMMAND_HVAC_OFF, None, 90)
        else:
            # Der 500e kennt meist nur die Vorklimatisierung (ROPRECOND)
            cmd = COMMAND_PRECOND_ON if on else COMMAND_PRECOND_OFF
            ok = await self.client.command_verify(v.vin, cmd, None, 90)
        self.cache_time = 0
        return ok

    async def command(self, name: str) -> bool:
        from py_uconnect import COMMANDS_BY_NAME

        v = await self._vehicle()
        ok = await self.client.command_verify(v.vin, COMMANDS_BY_NAME[name], None, 90)
        self.cache_time = 0
        return ok


class DemoSource:
    """Simulierter 500e – zum Ausprobieren ohne Zugangsdaten."""

    def __init__(self) -> None:
        self.state = {
            "vin": "ZFAEFAC39MX000500",
            "nickname": "Mein 500e",
            "model": "500e La Prima",
            "year": 2023,
            "state_of_charge": 78.0,
            "distance_to_empty": 241.0,
            "odometer": 18342.0,
            "plugged_in": True,
            "charging": True,
            "charging_level": 2,
            # wie beim echten 500e: kein Klima-Status vom Auto
            "hvac_on": None,
            "hvac_temperature": 21.0,
            "door_driver_locked": True,
            "door_passenger_locked": True,
            "location": {"latitude": 48.20849, "longitude": 16.37208},
            # wie beim echten 500e (kein HBLF, dafür ROLIGHTS und Kofferraum)
            "supported_commands": ["RDL", "RDU", "ROLIGHTS", "ROPRECOND", "ROPRECOND_OFF",
                                   "ROTRUNKUNLOCK", "CNOW", "VF", "DEEPREFRESH"],
        }
        self.last = time.time()

    def _tick(self) -> None:
        dt = time.time() - self.last
        self.last = time.time()
        s = self.state
        if s["charging"]:
            s["state_of_charge"] = min(100.0, s["state_of_charge"] + dt / 60 * 0.5)
            if s["state_of_charge"] >= 100:
                s["charging"] = False
        s["distance_to_empty"] = round(s["state_of_charge"] * 3.1)
        now = datetime.now(timezone.utc).isoformat()
        s["timestamp_status"] = now
        s["location"]["updated"] = now

    async def status(self, force: bool = False) -> dict[str, Any]:
        self._tick()
        return _summary(self.state)

    async def wake(self) -> list[str]:
        loc = self.state["location"]
        loc["latitude"] += random.uniform(-0.0005, 0.0005)
        loc["longitude"] += random.uniform(-0.0005, 0.0005)
        return []

    async def climate(self, on: bool, temp: float | None) -> bool:
        await asyncio.sleep(2.5)
        if temp is not None:
            self.state["hvac_temperature"] = temp
        return True

    async def command(self, name: str) -> bool:
        await asyncio.sleep(2)
        s = self.state
        if name in ("RDL", "RDU"):
            s["door_driver_locked"] = s["door_passenger_locked"] = name == "RDL"
        elif name == "CNOW" and s["plugged_in"]:
            s["charging"] = True
        return True


source: UconnectSource | DemoSource = DemoSource() if DEMO else UconnectSource()
store = Store(DATA_DIR / ("demo.db" if DEMO else "elektroauto.db"))
notifier = Notifier(store, DATA_DIR)


def _seed_demo_history() -> None:
    """Erzeugt 30 Tage Beispiel-Verlauf für den Demo-Modus."""

    with store.lock:
        if store.db.execute("SELECT 1 FROM snapshots LIMIT 1").fetchone():
            return
    rng = random.Random(500)
    start = datetime.now() - timedelta(days=30)
    soc, odo, rows, charging_tonight = 64.0, 17_510.0, [], False
    t = start.replace(minute=0, second=0, microsecond=0)
    while t < datetime.now() - timedelta(minutes=30):
        h, weekday = t.hour, t.weekday() < 5
        charging = 0
        if t.minute == 0 and ((weekday and h in (7, 17)) or (not weekday and h == 11 and rng.random() < 0.6)):
            km = rng.uniform(8, 16) if weekday else rng.uniform(15, 45)
            odo += km
            soc = max(10.0, soc - km / 2.6)
        elif (h >= 22 or h < 6) and soc < 100 and (soc < 55 or charging_tonight):
            charging = 1
            charging_tonight = True
            soc = min(100.0, soc + 4.5)
        if h == 12:
            charging_tonight = False
        rows.append((int(t.timestamp()), round(soc, 1), round(soc * 3.1), round(odo, 1),
                     charging, 1 if charging or h >= 20 or h < 7 else 0,
                     48.20849, 16.37208))
        t += timedelta(minutes=30)
    with store.lock, store.db:
        store.db.executemany("INSERT INTO snapshots VALUES (?, ?, ?, ?, ?, ?, ?, ?)", rows)
    source.state["state_of_charge"] = soc
    source.state["odometer"] = odo


if DEMO:
    _seed_demo_history()


def _with_climate(data: dict[str, Any]) -> dict[str, Any]:
    """Klima-Status: Meldet das Auto keinen (oder einen älteren) Status, wird er
    aus dem letzten erfolgreichen Befehl abgeleitet."""

    out = {**data, "climate_until": None, "climate_estimated": False}
    cmd = store.get("climate_cmd")
    if not cmd:
        out["climate_on"] = bool(data.get("climate_on"))
        return out
    until = cmd["ts"] + CLIMATE_MINUTES * 60
    reported_at = _ts(data.get("updated"))
    car_is_newer = data.get("climate_on") is not None and reported_at and reported_at > cmd["ts"]
    if car_is_newer:
        out["climate_on"] = bool(data["climate_on"])
    elif cmd["on"] and time.time() < until:
        out.update(climate_on=True, climate_until=int(until), climate_estimated=True)
    else:
        out["climate_on"] = False
    return out


def _ts(value: Any) -> float | None:
    try:
        return datetime.fromisoformat(str(value)).timestamp() if value else None
    except ValueError:
        return None


async def _climate_job(on: bool, temp: float | None) -> bool:
    ok = await source.climate(on, temp)
    if ok:
        store.set("climate_cmd", {"on": on, "ts": time.time()})
    return ok


async def _observe(data: dict[str, Any]) -> None:
    """Neue Fahrzeugdaten: Verlauf speichern und ggf. benachrichtigen."""

    try:
        store.record(data)
        await notifier.check(data)
    except Exception:  # noqa: BLE001
        log.exception("Verlauf/Benachrichtigung fehlgeschlagen")


async def _outage_failed(reason: str) -> None:
    """Merkt sich, seit wann Fiat nicht erreichbar ist, und benachrichtigt einmal,
    wenn das länger als OUTAGE_NOTIFY_MINUTES dauert."""

    out = store.get("outage") or {}
    now = time.time()
    if not out.get("since"):
        out = {"since": now, "notified": False}
    out["reason"] = reason
    if not out["notified"] and now - out["since"] >= OUTAGE_NOTIFY_MINUTES * 60:
        await notifier.send(
            "⚠️ Keine Verbindung zu Fiat",
            f"Seit {_hm(out['since'])} schlägt die Abfrage fehl: {reason}. "
            "Bis das wieder geht, kommen keine Lade-Benachrichtigungen.",
            tag="outage",
        )
        out["notified"] = True
    store.set("outage", out)


async def _outage_ok() -> None:
    out = store.get("outage") or {}
    if not out.get("since"):
        return
    if out.get("notified"):
        await notifier.send("✅ Verbindung zu Fiat wieder da",
                            f"Ausfall von {_hm(out['since'])} bis {_hm(time.time())}.", tag="outage")
    store.set("outage", {})


async def _poller() -> None:
    """Fragt das Auto im Hintergrund ab, auch wenn die App nicht offen ist."""

    while True:
        charging = False
        try:
            data = await source.status(force=True)
            charging = bool(data.get("charging"))
            await _observe(data)
            await _outage_ok()
        except Exception as err:  # noqa: BLE001
            reason = error_text(err)
            log.warning("Hintergrund-Abfrage fehlgeschlagen: %s", reason)
            await _outage_failed(reason)
        minutes = POLL_MINUTES_CHARGING if charging else POLL_MINUTES
        await asyncio.sleep(max(0.5, minutes) * 60)

# Laufende Fernbefehle: id -> {"state": "pending"|"ok"|"failed", ...}
jobs: dict[str, dict[str, Any]] = {}


async def _run_job(job_id: str, coro) -> None:
    try:
        ok = await coro
        jobs[job_id].update(state="ok" if ok else "failed")
    except Exception as err:  # noqa: BLE001
        reason = error_text(err)
        log.warning("Befehl '%s' fehlgeschlagen: %s", jobs[job_id]["label"], reason)
        jobs[job_id].update(state="failed", error=reason)


def _start_job(label: str, coro) -> dict[str, Any]:
    job_id = uuid.uuid4().hex[:12]
    jobs[job_id] = {"id": job_id, "label": label, "state": "pending", "started": time.time()}
    asyncio.create_task(_run_job(job_id, coro))
    # alte Jobs aufräumen
    for k in [k for k, j in jobs.items() if time.time() - j["started"] > 3600]:
        jobs.pop(k, None)
    return jobs[job_id]


# --------------------------------------------------------------------------
# API
# --------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(_: FastAPI):
    task = asyncio.create_task(_poller())
    yield
    task.cancel()


app = FastAPI(title="Elektrocar", docs_url=None, redoc_url=None, lifespan=lifespan)


def auth(x_app_password: str | None = Header(default=None)) -> None:
    if APP_PASSWORD and not secrets.compare_digest(x_app_password or "", APP_PASSWORD):
        raise HTTPException(401, "Passwort erforderlich")


class ClimateRequest(BaseModel):
    on: bool
    temperature: float | None = Field(default=None, ge=16, le=28)


@app.get("/api/config")
async def config() -> dict[str, Any]:
    return {"demo": DEMO, "password_required": bool(APP_PASSWORD)}


@app.get("/api/status", dependencies=[Depends(auth)])
async def status(force: bool = False) -> dict[str, Any]:
    try:
        data = await source.status(force)
    except HTTPException:
        raise
    except Exception as err:  # noqa: BLE001
        log.exception("Status fehlgeschlagen")
        raise HTTPException(502, f"Uconnect nicht erreichbar: {err}") from err
    await _observe(data)
    eta = store.charge_eta(data.get("soc")) if data.get("charging") else None
    return {**_with_climate(data), "charge_eta": eta, "demo": DEMO}


@app.post("/api/wake", dependencies=[Depends(auth)])
async def wake() -> dict[str, Any]:
    try:
        notes = await source.wake()
    except Exception as err:  # noqa: BLE001
        notes = [f"Aktualisieren fehlgeschlagen: {error_text(err)}"]
    return {"ok": not notes, "notes": notes}


@app.post("/api/climate", dependencies=[Depends(auth)])
async def climate(req: ClimateRequest) -> dict[str, Any]:
    label = "Klima ein" if req.on else "Klima aus"
    return _start_job(label, _climate_job(req.on, req.temperature))


@app.post("/api/command/{name}", dependencies=[Depends(auth)])
async def command(name: str) -> dict[str, Any]:
    if name not in ALLOWED_COMMANDS:
        raise HTTPException(400, "Befehl nicht erlaubt")
    return _start_job(ALLOWED_COMMANDS[name], source.command(name))


@app.get("/api/jobs/{job_id}", dependencies=[Depends(auth)])
async def job(job_id: str) -> dict[str, Any]:
    if job_id not in jobs:
        raise HTTPException(404, "Unbekannter Auftrag")
    return jobs[job_id]


@app.get("/api/history", dependencies=[Depends(auth)])
async def history(days: int = 7) -> dict[str, Any]:
    return store.history(min(max(days, 1), 365))


class PushSubscription(BaseModel):
    endpoint: str
    keys: dict[str, str]
    expirationTime: float | None = None


class NotifySettings(BaseModel):
    soc80: bool | None = Field(default=None, alias="80")
    soc100: bool | None = Field(default=None, alias="100")


@app.get("/api/notify", dependencies=[Depends(auth)])
async def notify_info(endpoint: str | None = None) -> dict[str, Any]:
    return {
        "public_key": notifier.public_key,
        "settings": notifier.settings(),
        "subscribed": store.has_subscription(endpoint) if endpoint else False,
        "devices": len(store.subscriptions()),
        "ntfy": notifier.has_ntfy,
    }


@app.post("/api/notify/settings", dependencies=[Depends(auth)])
async def notify_settings(req: NotifySettings) -> dict[str, bool]:
    notifier.set_settings(
        {k: v for k, v in (("80", req.soc80), ("100", req.soc100)) if v is not None}
    )
    return notifier.settings()


@app.post("/api/notify/subscribe", dependencies=[Depends(auth)])
async def subscribe(sub: PushSubscription) -> dict[str, Any]:
    store.add_subscription(sub.model_dump())
    return {"ok": True}


@app.post("/api/notify/unsubscribe", dependencies=[Depends(auth)])
async def unsubscribe(sub: PushSubscription) -> dict[str, Any]:
    store.remove_subscription(sub.endpoint)
    return {"ok": True}


@app.post("/api/notify/test", dependencies=[Depends(auth)])
async def notify_test() -> dict[str, Any]:
    if not store.subscriptions() and not notifier.has_ntfy:
        raise HTTPException(400, "Kein Gerät für Benachrichtigungen angemeldet")
    result = await notifier.send("🔔 Test", "Benachrichtigungen funktionieren!", tag="test")
    if not result["sent"]:
        raise HTTPException(502, "Senden fehlgeschlagen: " + "; ".join(result["errors"]))
    return result


@app.get("/car-image")
async def car_image() -> FileResponse:
    """Foto des Autos aus data/car.webp|png|jpg, automatisch freigestellt;
    sonst ein Platzhalter. Das Foto liegt bewusst nicht im Repository."""

    path = await asyncio.to_thread(carimage.cutout, DATA_DIR)
    return FileResponse(path or FRONTEND / "car-placeholder.svg", headers={"Cache-Control": "no-cache"})


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(FRONTEND / "index.html")


app.mount("/", StaticFiles(directory=FRONTEND), name="static")

