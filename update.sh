#!/bin/sh
# Holt den neuesten Code von GitHub und baut den Container neu.
#
#   ./update.sh          nur neu bauen, wenn es Änderungen gibt
#   ./update.sh --force  immer neu bauen
#
# .env und der Ordner data/ (Verlauf, Einstellungen) bleiben unverändert.
set -eu

cd "$(dirname "$0")"

FORCE=0
[ "${1:-}" = "--force" ] && FORCE=1

# docker braucht auf der Synology root-Rechte
SUDO=""
[ "$(id -u)" -ne 0 ] && SUDO="sudo"

# "docker compose" (neu) oder "docker-compose" (alt)
if $SUDO docker compose version >/dev/null 2>&1; then
  COMPOSE="$SUDO docker compose"
elif command -v docker-compose >/dev/null 2>&1; then
  COMPOSE="$SUDO docker-compose"
else
  echo "Fehler: docker compose nicht gefunden (Container Manager installiert?)" >&2
  exit 1
fi

# Projektname des laufenden Containers übernehmen, damit Container Manager
# dasselbe Projekt aktualisiert statt ein zweites anzulegen
PROJECT="${COMPOSE_PROJECT_NAME:-}"
if [ -z "$PROJECT" ]; then
  PROJECT="$($SUDO docker inspect elektroauto \
    --format '{{ index .Config.Labels "com.docker.compose.project" }}' 2>/dev/null || true)"
fi
[ -z "$PROJECT" ] && PROJECT="$(basename "$(pwd)")"

echo "→ Hole neuesten Code von GitHub …"
OLD="$(git rev-parse HEAD)"
git pull --ff-only
NEW="$(git rev-parse HEAD)"

if [ "$OLD" = "$NEW" ] && [ "$FORCE" -eq 0 ]; then
  echo "✓ Bereits aktuell ($(git log -1 --format='%h %s'))."
  echo "  Neu bauen erzwingen mit: ./update.sh --force"
  exit 0
fi

if [ "$OLD" != "$NEW" ]; then
  echo "→ Neu seit dem letzten Update:"
  git log --format='   • %s' "$OLD..$NEW"
fi

# Docker auf der Synology legt Ordner für Volumes nicht selbst an
mkdir -p data

echo "→ Baue und starte Container (Projekt: $PROJECT) …"
$COMPOSE -p "$PROJECT" up -d --build

# alte, nicht mehr benutzte Images aufräumen (spart Speicherplatz)
$SUDO docker image prune -f >/dev/null 2>&1 || true

echo "✓ Fertig: $(git log -1 --format='%h %s')"
