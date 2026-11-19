"""Benachrichtigungen: Web-Push direkt in die App, optional zusätzlich über ntfy."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives import serialization
from py_vapid import Vapid02, b64urlencode
from pywebpush import WebPushException, webpush

from store import Store

log = logging.getLogger("elektrocar.notify")

VAPID_SUBJECT = os.environ.get("VAPID_SUBJECT") or "mailto:elektroauto@example.org"
NTFY_URL = os.environ.get("NTFY_URL", "")

# Schwellen, bei denen benachrichtigt wird, und Abstand, ab dem wieder "scharf"
THRESHOLDS = {"80": 80.0, "100": 100.0}
REARM_BELOW = 5.0


class Notifier:
    def __init__(self, store: Store, data_dir: Path) -> None:
        self.store = store
        key_file = data_dir / "vapid_private.pem"
        if key_file.exists():
            self.vapid = Vapid02.from_file(str(key_file))
        else:
            self.vapid = Vapid02()
            self.vapid.generate_keys()
            self.vapid.save_key(str(key_file))
        raw = self.vapid.public_key.public_bytes(
            serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
        )
        self.public_key = b64urlencode(raw)

    # ---- Einstellungen ------------------------------------------------------

    def settings(self) -> dict[str, bool]:
        return {k: bool(self.store.get(f"notify_{k}", True)) for k in THRESHOLDS}

    def set_settings(self, values: dict[str, bool]) -> None:
        for k, v in values.items():
            if k in THRESHOLDS:
                self.store.set(f"notify_{k}", bool(v))

    # ---- Senden -------------------------------------------------------------

    has_ntfy = bool(NTFY_URL)

    async def send(self, title: str, body: str, tag: str = "elektroauto") -> dict[str, Any]:
        """Sendet an alle Geräte. Gibt Anzahl und Fehlermeldungen zurück."""

        payload = json.dumps({"title": title, "body": body, "tag": tag})
        sent, errors = 0, []
        for sub in self.store.subscriptions():
            try:
                await asyncio.to_thread(
                    webpush,
                    sub,
                    payload,
                    vapid_private_key=self.vapid,
                    vapid_claims={"sub": VAPID_SUBJECT},
                    ttl=6 * 3600,
                    timeout=15,
                )
                sent += 1
            except WebPushException as err:
                resp = err.response
                status = resp.status_code if resp is not None else None
                detail = (resp.text or "").strip()[:200] if resp is not None else str(err)
                service = _service(sub["endpoint"])
                if status in (404, 410):
                    log.info("Push-Abo bei %s abgelaufen, wird entfernt", service)
                    self.store.remove_subscription(sub["endpoint"])
                    errors.append(f"{service}: Abo abgelaufen, bitte neu aktivieren")
                else:
                    log.warning("Push an %s fehlgeschlagen (%s): %s", service, status, detail)
                    errors.append(f"{service} {status}: {detail or err}")
            except Exception as err:  # noqa: BLE001
                log.warning("Push fehlgeschlagen: %s", err)
                errors.append(f"{_service(sub['endpoint'])}: {err}")

        if NTFY_URL:
            try:
                await asyncio.to_thread(_ntfy, title, body)
                sent += 1
            except Exception as err:  # noqa: BLE001
                log.warning("ntfy fehlgeschlagen: %s", err)
                errors.append(f"ntfy: {err}")
        log.info("Benachrichtigung '%s': %d gesendet, %d Fehler", title, sent, len(errors))
        return {"sent": sent, "errors": errors}

    # ---- Ladeschwellen ------------------------------------------------------

    async def check(self, status: dict[str, Any]) -> None:
        """Benachrichtigt einmal, wenn der Akku beim Laden 80 % bzw. 100 % erreicht."""

        soc = status.get("soc")
        if soc is None:
            return
        enabled = self.settings()
        for key, level in THRESHOLDS.items():
            armed_key = f"armed_{key}"
            armed = self.store.get(armed_key)
            if armed is None:
                # erster Wert: nur merken, ob die Schwelle noch vor uns liegt
                self.store.set(armed_key, soc < level)
            elif soc < level - REARM_BELOW:
                if not armed:
                    self.store.set(armed_key, True)
            elif soc >= level and armed:
                self.store.set(armed_key, False)
                if enabled[key] and (status.get("plugged_in") or status.get("charging")):
                    await self.send(*_message(key, status), tag=f"soc-{key}")


def _message(key: str, s: dict[str, Any]) -> tuple[str, str]:
    name = s.get("name") or "Dein Auto"
    rng = s.get("range_km")
    rng_txt = f" Reichweite: {rng:.0f} km." if rng else ""
    if key == "100":
        return "🔋 Akku voll", f"{name} ist voll geladen (100 %).{rng_txt}"
    return "⚡ Akku bei 80 %", f"{name} hat 80 % erreicht.{rng_txt}"


def _service(endpoint: str) -> str:
    host = urllib.parse.urlparse(endpoint).hostname or "?"
    if "apple" in host:
        return "Apple"
    if "google" in host:
        return "Google"
    if "mozilla" in host:
        return "Firefox"
    return host


def _ntfy(title: str, body: str) -> None:
    # Titel als Parameter, weil HTTP-Header keine Emojis/Umlaute erlauben
    sep = "&" if "?" in NTFY_URL else "?"
    url = NTFY_URL + sep + urllib.parse.urlencode({"title": title, "tags": "car"})
    req = urllib.request.Request(url, data=body.encode(), method="POST")
    urllib.request.urlopen(req, timeout=15).close()
