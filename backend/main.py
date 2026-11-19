"""Elektrocar – Backend für die Fiat 500e Verwaltung.

Spricht über die (inoffizielle) Uconnect-Cloud-API mit dem Auto
(Bibliothek ``py-uconnect``) und stellt eine kleine REST-API sowie die
Web-Oberfläche bereit. Ohne Zugangsdaten läuft ein Demo-Modus.
"""

from __future__ import annotations

import asyncio
import logging
import os
import random
import secrets
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

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

# Befehle, die die App senden darf (Name -> Beschreibung)
ALLOWED_COMMANDS = {
    "RDL": "Türen verriegeln",
    "RDU": "Türen entriegeln",
    "HBLF": "Hupe & Licht",
    "ROLIGHTS": "Licht",
    "CNOW": "Laden starten",
}


# --------------------------------------------------------------------------
# Datenquellen
# --------------------------------------------------------------------------


def _summary(v: dict[str, Any]) -> dict[str, Any]:
    """Reduziert die Fahrzeugdaten auf das, was die App anzeigt."""

    loc = v.get("location") or {}
    climate_on = bool(v.get("hvac_on") or v.get("ev_cabin_cond"))
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
        "climate_on": climate_on,
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
        self.lock = asyncio.Lock()
        self.cache: dict[str, Any] | None = None
        self.cache_time = 0.0
        self.vin: str | None = VIN

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

    async def wake(self) -> None:
        """Bittet das Auto, frische Daten und Position hochzuladen."""

        from py_uconnect.command import COMMAND_DEEP_REFRESH, COMMAND_REFRESH_LOCATION

        v = await self._vehicle()
        for cmd in (COMMAND_DEEP_REFRESH, COMMAND_REFRESH_LOCATION):
            if v.supports(cmd):
                try:
                    await self.client.command(v.vin, cmd)
                except Exception as err:  # noqa: BLE001
                    log.warning("%s fehlgeschlagen: %s", cmd, err)

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
            "hvac_on": False,
            "hvac_temperature": 21.0,
            "door_driver_locked": True,
            "door_passenger_locked": True,
            "location": {"latitude": 48.20849, "longitude": 16.37208},
            "supported_commands": ["RDL", "RDU", "HBLF", "ROLIGHTS", "ROPRECOND",
                                   "ROPRECOND_OFF", "CNOW", "VF", "DEEPREFRESH"],
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

    async def wake(self) -> None:
        loc = self.state["location"]
        loc["latitude"] += random.uniform(-0.0005, 0.0005)
        loc["longitude"] += random.uniform(-0.0005, 0.0005)

    async def climate(self, on: bool, temp: float | None) -> bool:
        await asyncio.sleep(2.5)
        self.state["hvac_on"] = on
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

# Laufende Fernbefehle: id -> {"state": "pending"|"ok"|"failed", ...}
jobs: dict[str, dict[str, Any]] = {}


async def _run_job(job_id: str, coro) -> None:
    try:
        ok = await coro
        jobs[job_id].update(state="ok" if ok else "failed")
    except Exception as err:  # noqa: BLE001
        log.exception("Befehl fehlgeschlagen")
        jobs[job_id].update(state="failed", error=str(err))


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

app = FastAPI(title="Elektrocar", docs_url=None, redoc_url=None)


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
    return {**data, "demo": DEMO}


@app.post("/api/wake", dependencies=[Depends(auth)])
async def wake() -> dict[str, Any]:
    await source.wake()
    return {"ok": True}


@app.post("/api/climate", dependencies=[Depends(auth)])
async def climate(req: ClimateRequest) -> dict[str, Any]:
    label = "Klima ein" if req.on else "Klima aus"
    return _start_job(label, source.climate(req.on, req.temperature))


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


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(FRONTEND / "index.html")


app.mount("/", StaticFiles(directory=FRONTEND), name="static")

