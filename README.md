# ⚡ Elektrocar – Fiat 500e App

Eine Web-App fürs Handy, um deinen Fiat 500e zu verwalten:

- 🔋 **Ladezustand** (Ring-Anzeige), Ladestatus und Reichweite
- 🛣️ **Kilometerstand**
- 📍 **Position** auf der Karte, mit Adresse und Route-Link
- ❄️🔥 **Klimaanlage** ein/aus mit Wunschtemperatur
- 🔒 Extras: Verriegeln, Entriegeln, Hupe & Licht, Laden starten

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

## Aufs Handy

- **iPhone:** Seite in Safari öffnen → Teilen → „Zum Home-Bildschirm“
- **Android:** Seite in Chrome öffnen → ⋮ → „App installieren“

Die App startet dann wie eine normale App im Vollbild. Vollständig installierbar ist sie nur über `https://` (siehe [docs/SYNOLOGY.md](docs/SYNOLOGY.md)).

## Hinweise

- Der Aktualisieren-Knopf weckt das Auto auf, damit es frische Daten und die Position sendet. Das dauert etwas.
- Beim 500e läuft die Klimatisierung als **Vorklimatisierung** (ca. 15 Min.). Ob die Wunschtemperatur übernommen wird, hängt vom Modell ab.
- Die Uconnect-API ist nicht offiziell dokumentiert und kann sich ändern.
