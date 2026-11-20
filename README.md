# ⚡ Elektrocar – Fiat 500e App

Eine Web-App fürs Handy, um deinen Fiat 500e zu verwalten:

- 🔋 **Ladezustand** (Ring-Anzeige), Ladestatus und Reichweite
- 🛣️ **Kilometerstand**
- 📍 **Position** auf der Karte, mit Adresse und Route-Link
- ❄️🔥 **Klimaanlage** ein/aus mit Wunschtemperatur
- 🔒 Extras: Verriegeln, Entriegeln, Hupe & Licht, Laden starten
- 🔔 **Benachrichtigungen** aufs Handy bei 80 % und bei vollem Akku – auch wenn die App geschlossen ist
- ⏰ **Klima-Timer**: Vorklimatisierung zu festen Zeiten (z.B. Mo–Fr 7:15) oder einmalig, mit Bestätigung über ntfy
- 💶 **Ladekosten und Verbrauch**: kWh, Kosten, kWh/100 km und €/100 km aus dem Verlauf, Kosten pro Ladevorgang
- 🔌 **Ladeerinnerung**: abends eine Meldung, wenn der Akku unter einer Schwelle und das Auto nicht angesteckt ist
- 📈 **Verlauf**: Ladezustand, Kilometer pro Tag und Ladevorgänge der letzten 24 Std. / 7 / 30 Tage

Die App spricht über die Uconnect-Cloud mit dem Auto (wie die offizielle Fiat-App)
und nutzt dafür die inoffizielle Bibliothek [py-uconnect](https://pypi.org/project/py-uconnect/).

## Schnellstart

```bash
cp .env.example .env      # Zugangsdaten eintragen
pip install -r backend/requirements.txt
set -a; . ./.env; set +a
uvicorn --app-dir backend main:app --host 0.0.0.0 --port 8000
```

Dann <http://localhost:8000> öffnen. Ohne Zugangsdaten startet ein **Demo-Modus** mit simulierten Werten.

### Mit Docker

```bash
docker compose up -d --build
```

Die App ist dann unter <http://localhost:8765> erreichbar.

Update auf die neueste Version: `./update.sh` (holt den Code per `git pull` und baut neu).

### Auf einer Synology NAS

Schritt-für-Schritt-Anleitung inklusive Zugriff von unterwegs (Tailscale oder Reverse Proxy):
👉 [docs/SYNOLOGY.md](docs/SYNOLOGY.md)

## Einstellungen (`.env`)

| Variable | Bedeutung |
|---|---|
| `UCONNECT_EMAIL` / `UCONNECT_PASSWORD` | Login deines Fiat-/Uconnect-Kontos |
| `UCONNECT_PIN` | PIN aus der Fiat-App, nötig für Fernbefehle (Klima, Türen) |
| `UCONNECT_BRAND` | `FIAT_EU` für Europa |
| `UCONNECT_VIN` | optional, falls mehrere Autos im Konto sind |
| `APP_PASSWORD` | schützt die App mit einem Passwort – **unbedingt setzen**, wenn sie aus dem Internet erreichbar ist |
| `CACHE_SECONDS` | wie lange Daten zwischengespeichert werden (Standard 60) |
| `POLL_MINUTES` / `POLL_MINUTES_CHARGING` | wie oft das Auto im Hintergrund abgefragt wird (Standard 10 bzw. 5 Minuten beim Laden) |
| `CLIMATE_MINUTES` | Laufzeit der Klimatisierung für die Restzeit-Anzeige (Standard 15), falls das Auto keinen Status meldet |
| `OUTAGE_NOTIFY_MINUTES` | nach so vielen Minuten ohne Verbindung zu Fiat kommt eine Warnung, bei Rückkehr eine Entwarnung (Standard 60) |
| `STROMPREIS`, `AKKU_KWH`, `LADEVERLUSTE` | Standardwerte für die Ladekosten (0,28 €/kWh, 37,3 kWh nutzbar, 10 %), in der App änderbar |
| `LADEERINNERUNG_UM`, `LADEERINNERUNG_UNTER` | Standardwerte der Ladeerinnerung (20:00, 30 %), in der App änderbar |
| `DATA_DIR` | Ordner für Verlauf und Einstellungen (Docker: `/data`, liegt am NAS im Projektordner unter `data/`) |
| `NTFY_URL` | optional: Benachrichtigungen zusätzlich über [ntfy](https://ntfy.sh) |
| `VAPID_SUBJECT` | optional: Kontaktadresse für Web-Push, z.B. `mailto:du@example.com` |

## Aufs Handy

- **iPhone:** Seite in Safari öffnen → Teilen → „Zum Home-Bildschirm“
- **Android:** Seite in Chrome öffnen → ⋮ → „App installieren“

Die App startet dann wie eine normale App im Vollbild. Vollständig installierbar ist sie nur über `https://` (siehe [docs/SYNOLOGY.md](docs/SYNOLOGY.md)).

## Eigenes Foto

Ohne eigenes Foto zeigt die App oben eine schlichte Auto-Silhouette. Ein Foto als
`data/car.jpg`, `data/car.png` oder `data/car.webp` ablegen – auf der Synology also z.B.
`/volume1/docker/elektroauto/data/car.jpg`. Ein **weißer oder heller Hintergrund wird
automatisch entfernt**, das Bild zugeschnitten und verkleinert (Ergebnis in
`data/.car-cutout.png`). Ein bereits freigestelltes Bild mit Transparenz bleibt unverändert.
Gibt es mehrere, wird das neueste verwendet. Das Foto erscheint beim nächsten Laden der
App und bleibt bei Updates erhalten. Der Ordner `data/` ist nicht im Repository: Fotos aus dem Netz werden
so nicht veröffentlicht.

## Benachrichtigungen

1. Die App über **https://** öffnen (z.B. die Tailscale-Adresse, siehe [docs/SYNOLOGY.md](docs/SYNOLOGY.md)).
2. **iPhone:** die App zuerst zum Home-Bildschirm hinzufügen und von dort öffnen (ab iOS 16.4).
3. In der Karte **Benachrichtigungen** auf „Auf diesem Gerät aktivieren“ tippen und erlauben.
4. Mit „Test senden“ prüfen.

Das NAS fragt das Auto im Hintergrund ab (beim Laden alle 5 Minuten). Die Meldung kommt
deshalb mit bis zu ein paar Minuten Verzögerung. Ist am Auto ein Ladelimit unter 100 %
eingestellt, kommt die „Akku voll“-Meldung nicht.

## Verlauf

Der Verlauf beginnt, sobald die App läuft, und wird in `data/elektroauto.db` gespeichert.
Bei Docker liegt der Ordner `data/` im Projektordner und bleibt bei Updates erhalten.

## Hinweise

- Der Aktualisieren-Knopf weckt das Auto auf, damit es frische Daten und die Position sendet. Das dauert etwas.
- Beim 500e läuft die Klimatisierung als **Vorklimatisierung** (ca. 15 Min.). Ob die Wunschtemperatur übernommen wird, hängt vom Modell ab.
- Die Uconnect-API ist nicht offiziell dokumentiert und kann sich ändern.
