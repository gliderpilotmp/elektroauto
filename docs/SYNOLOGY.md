# Elektroauto-App auf der Synology NAS

Diese Anleitung bringt die App auf deine Synology und macht sie am Handy erreichbar,
zu Hause und unterwegs.

## Voraussetzungen

- DSM 7.2 oder neuer
- Paket **Container Manager** (Paket-Zentrum). Wird es dort nicht angeboten,
  unterstützt dein Modell leider keine Container.

## 1. Dateien auf das NAS kopieren

**Empfohlen – mit Git** (dann geht jedes Update mit einem Befehl, siehe „Aktualisieren“):

1. Paket-Zentrum → **Git Server** installieren (bringt den Befehl `git` mit).
2. SSH aktivieren (Systemsteuerung → Terminal & SNMP) und per SSH anmelden.
3. Repository holen:

   ```bash
   git clone https://github.com/gliderpilotmp/elektroauto.git /volume1/docker/elektroauto
   ```

**Oder ohne Git:**

1. Auf GitHub: **Code → Download ZIP** und entpacken.
2. In der **File Station** den Ordner `docker/elektroauto` anlegen
   (der Freigabeordner `docker` entsteht mit dem Container Manager).
3. Den Inhalt des Repositorys hineinladen. Danach liegen dort u.a.
   `Dockerfile`, `docker-compose.yml`, `backend/`, `frontend/`.

## 2. Zugangsdaten eintragen

1. `.env.example` herunterladen, mit einem Texteditor öffnen und ausfüllen:

   ```ini
   UCONNECT_EMAIL=deine@email.at
   UCONNECT_PASSWORD=deinPasswort
   UCONNECT_PIN=1234
   UCONNECT_BRAND=FIAT_EU
   APP_PASSWORD=ein-langes-eigenes-passwort
   ```

2. Als **`.env`** (mit Punkt vorne, ohne Endung) wieder in `docker/elektroauto` hochladen.

> ⚠️ **`APP_PASSWORD` immer setzen.** Wer die Adresse der App kennt,
> kann sonst dein Auto entriegeln.

## 3. Container starten

1. **Container Manager → Projekt → Erstellen**
2. Projektname: `elektroauto`
3. Pfad: `docker/elektroauto` auswählen. Die vorhandene `docker-compose.yml`
   wird erkannt, „Vorhandene docker-compose.yml verwenden“ wählen.
4. **Weiter → Fertig**. Das Bauen dauert beim ersten Mal ein paar Minuten.

Die App läuft jetzt dauerhaft und startet nach einem Neustart des NAS von selbst.
Im Heimnetz ist sie erreichbar unter:

```
http://<IP-deines-NAS>:8765
```

Die IP findest du unter **Systemsteuerung → Netzwerk → Netzwerkschnittstelle**.

> Port 8765 wurde gewählt, weil 8000 oft schon belegt ist (z.B. von Portainer).
> Für einen anderen Port in `docker-compose.yml` nur die **linke** Zahl ändern, z.B. `"9123:8000"`.

## 4. Von unterwegs erreichen

QuickConnect funktioniert dafür nicht, es leitet nur Synology-eigene Dienste weiter.
Wähle **eine** der beiden Varianten:

### Variante A: Tailscale (empfohlen)

Privates Netz zwischen Handy und NAS: kein offener Port, nichts ist öffentlich im Internet.

1. Kostenloses Konto auf <https://tailscale.com> anlegen.
2. Paket-Zentrum → **Tailscale** installieren, öffnen und mit dem Konto anmelden.
3. Am Handy die **Tailscale-App** installieren und mit demselben Konto anmelden.
4. Fertig: Am Handy ist die App unter `http://<NAS-Name>:8765` erreichbar,
   den Namen zeigt die Tailscale-App.

**Mit HTTPS** (nötig für die volle App-Installation unter Android):

1. In der Tailscale-Verwaltung (<https://login.tailscale.com/admin/dns>)
   **MagicDNS** und **HTTPS Certificates** aktivieren.
2. Per SSH auf der Synology einmalig ausführen
   (SSH aktivieren: Systemsteuerung → Terminal & SNMP):

   ```bash
   sudo /var/packages/Tailscale/target/bin/tailscale serve --bg 8765
   ```

3. Die App ist dann unter `https://<NAS-Name>.<dein-tailnet>.ts.net` erreichbar.
   Die genaue Adresse gibt der Befehl aus.

### Variante B: Eigene Adresse mit Reverse Proxy

Die App ist dann öffentlich im Internet erreichbar und nur durch `APP_PASSWORD` geschützt.

1. **Systemsteuerung → Externer Zugriff → DDNS**: eine Adresse anlegen,
   z.B. `meinname.synology.me`.
2. **Systemsteuerung → Sicherheit → Zertifikat**: Let's-Encrypt-Zertifikat für
   `auto.meinname.synology.me` hinzufügen.
3. **Systemsteuerung → Anmeldeportal → Erweitert → Reverse Proxy → Erstellen**:
   - Quelle: `HTTPS`, Hostname `auto.meinname.synology.me`, Port `443`
   - Ziel: `HTTP`, Hostname `localhost`, Port `8765`
4. Im Router Port **443** auf das NAS weiterleiten.
5. Die App ist unter `https://auto.meinname.synology.me` erreichbar.

## 5. App am Handy installieren

Adresse im Browser öffnen, mit `APP_PASSWORD` anmelden, dann:

- **iPhone:** in **Safari** → Teilen-Symbol → **„Zum Home-Bildschirm“**
- **Android:** in **Chrome** → ⋮ → **„App installieren“**
  (oder „Zum Startbildschirm hinzufügen“)

Die App startet dann im Vollbild wie eine normale App.
Über `https://` wird sie vollständig installiert. Über `http://` im Heimnetz
entsteht meist nur ein Lesezeichen am Startbildschirm, das aber genauso funktioniert.

## 6. Benachrichtigungen einschalten

1. Die App über die **https://…ts.net**-Adresse öffnen (Schritt 4, Variante A mit HTTPS).
   Über `http://` funktionieren Benachrichtigungen nicht.
2. **iPhone:** von dem Symbol am Home-Bildschirm aus öffnen, nicht in Safari.
3. Karte **Benachrichtigungen** → „Auf diesem Gerät aktivieren“ → erlauben → „Test senden“.

## Aktualisieren

**Mit Git** (per SSH):

```bash
/volume1/docker/elektroauto/update.sh
```

Das Skript holt den neuesten Code, zeigt was neu ist, baut den Container neu und räumt alte
Images auf. Gibt es nichts Neues, tut es nichts (`--force` baut trotzdem neu).
`.env` und `data/` bleiben unverändert.

Automatisch jede Nacht: **Systemsteuerung → Aufgabenplanung → Erstellen → Geplante Aufgabe →
Benutzerdefiniertes Script**, Benutzer `root`, Zeitplan z.B. täglich 3:00, Befehl:

```bash
/volume1/docker/elektroauto/update.sh
```

Hast du den Ordner ursprünglich ohne Git angelegt, verknüpfst du ihn einmalig so
(`.env` und `data/` bleiben erhalten):

```bash
cd /volume1/docker/elektroauto
git init -b main && git remote add origin https://github.com/gliderpilotmp/elektroauto.git
git fetch origin && git checkout -f -B main origin/main
```

**Ohne Git:**

1. Neue Dateien von GitHub in `docker/elektroauto` kopieren (`.env` und den Ordner `data/` behalten:
   darin liegen Verlauf und Benachrichtigungs-Einstellungen).
2. **Container Manager → Projekt → elektroauto → Aktion → Erstellen**:
   baut die App neu und startet sie.

## Probleme?

- **Container Manager → Projekt → elektroauto → Protokoll** zeigt Fehlermeldungen.
- „driver failed programming external connectivity“ beim Start: Der Port ist schon belegt.
  Per SSH mit `sudo docker ps` nachsehen, wer ihn nutzt, und in `docker-compose.yml` einen anderen wählen.
- „Uconnect nicht erreichbar“: E-Mail, Passwort und `UCONNECT_BRAND` prüfen.
- Klima oder Türen gehen nicht: `UCONNECT_PIN` prüfen. Das ist die PIN aus der Fiat-App.
