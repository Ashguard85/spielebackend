# Spiele-Zentrale v1.1 – Ubuntu / Portainer / Cloudflare

Zentraler, spielunabhängiger REST-Datenspeicher für eigenständige GitHub-Pages-PWAs. **Kein automatisches Synchronisieren.** Die PWA spricht die API explizit im Server-Modus an. Die Flip-7-PWA v2 ist kompatibel. Yatzy Pages v2.6.0 unterstützt nun die zentrale API; das alte Node-Backend wird ausschliesslich für die sichere Übernahme bestehender Daten weiter benötigt.

## Architektur

Ein Flask/Gunicorn-Container, eine SQLite-Datenbank mit WAL / FULL / Foreign Keys, Backup-Dateien. Trennung per `app_id`: `flip7`, `yatzy`, weitere. Ein Zustand ist ein JSON-Objekt mit monoton steigender Revision. Schreibvorgänge benötigen die zuvor gelesene Revision, um veraltete Überschreibungen mit HTTP 409 abzulehnen. Je App existiert vorerst ein vollständiger Snapshot, nicht ein Multiplayer-Realtime-System.

Gemeinsame Spieler werden unter `/api/v1/players` gespeichert. **Wichtig:** Die App-spezifischen Spielerprofile von Flip 7 werden nicht automatisch mit der zentralen Spieler-Tabelle abgeglichen; dies erfordert spätere explizite Migrations- und Zuordnungslogik. Es gibt keine spielübergreifende Statistik in v1.

## Installation

Das ZIP direkt als Git-Repository-Root einspielen; keine .github-Dateien enthalten. In Portainer Git-Repository als Build-Kontext verwenden oder das `compose.yml` mit lokalem Git-Checkout bauen. Exemplarischer Portainer Webeditor:

```yaml
services:
  spiele-zentrale:
    build:
      context: https://github.com/DEIN-USER/spiele-backend.git#main
    container_name: spiele-zentrale
    restart: unless-stopped
    ports:
      - "8096:8080"
    volumes:
      - /opt/docker/spiele-zentrale/data:/app/data
    environment:
      DATA_DIR: /app/data
      AUTH_ENABLED: "true"
      APP_API_TOKEN: "BITTE-MIT-ZUFAELLIGEM-SECRET-MINDESTENS-32-ZEICHEN-ERSETZEN"
      PWA_ALLOWED_ORIGIN: "https://DEIN-USER.github.io"
      BACKUP_KEEP: "50"
```

**Vor dem Deployment** Datenordner auf Ubuntu anlegen und Eigentümer ändern:

```bash
sudo mkdir -p /opt/docker/spiele-zentrale/data
sudo chown -R 10001:10001 /opt/docker/spiele-zentrale/data
```

Beispiel für ein sicheres Token: `python3 -c 'import secrets; print(secrets.token_urlsafe(48))'`. Kein Token in öffentlichen Git-Repositories speichern! Besser Portainer Stack-Variablen oder Environment-Management nutzen. `AUTH_ENABLED=false` nur bei anderweitig streng abgesichertem Backend. Reverse Proxy / Cloudflare Tunnel auf `http://localhost:8096` konfigurieren (Tunnel-Agent je nach Netzstandort). Öffentlichen Port 8096 per Firewall nicht für das Internet freigeben.

### Cloudflare Pages-PWA

Cloudflare Tunnel HTTPS-Domain z. B. `https://spiele-api.example.com`. `PWA_ALLOWED_ORIGIN` enthält **Origins** (ohne Pfad), kommasepariert. Beispiel `https://name.github.io,https://anderes.example.com`. Bei Cloudflare Access müssen Service-Token-Header sowie CORS-Preflights (`OPTIONS`) passend freigegeben sein. Für Browsergespeicherte Tokens je Gerät eine eigene widerrufbare Berechtigung verwenden. Keine Zugangsdaten in Release, Backups oder Logs.

### REST API

- `GET /health`, `GET /api/health` – Status, öffentlich
- `GET /api/config` – Version/Authentifizierungsmodus, öffentlich
- `GET /api/v1/apps` – Spielkennungen und Revisionen
- `GET /api/v1/apps/flip7/state` – `{revision, data, updatedAt}` (bei neuem Spiel `data:null`)
- `PUT /api/v1/apps/flip7/state` – `{revision:0, data:{...}}` (bei Revisionenkonflikt HTTP 409)
- `GET /api/v1/players` – zentrale Profile
- `PUT /api/v1/players/<id>` – `{name:"..."}`
- `GET /api/v1/export` – gesamtes JSON-Backup
- `POST /api/v1/restore/preview` – das Backupobjekt als JSON-Body
- `POST /api/v1/restore` – `{backup:{...}, confirm:"REPLACE_ALL"}`; alle Daten werden ersetzt.

Für authentifizierte Endpunkte: `Authorization: Bearer <APP_API_TOKEN>`. Der Zustand jeder App wird serverseitig auf JSON-Typ, Größe, und Revision geprüft, **nicht auf die Spielregeln**. Client-Datenmodelle müssen weiterhin validiert werden. Keine Uploads/Datei-BLOBs oder gemeinsame Bilder in v1.

## Backup / Restore und Migration

Die Anwendung erstellt **vor jeder Änderung** einen JSON-Snapshot unter `/app/data/backups`; mit `BACKUP_KEEP=50` rotiert. Große Mengen an kleinen Spielzügen führen damit zu häufigen Backups; eine spätere Version kann periodische Backups ergänzen. Restore erfordert getrennte Vorschau und explizites `REPLACE_ALL` und erstellt zuvor ein Sicherheitsbackup. Das Schema wird über `schema_migrations` versioniert, bestehende Daten werden nicht gelöscht. Persistenz `/app/data/spiele.sqlite`.

## Flip 7 Umstellung (Pages v2)

1. Bestehenden Flip-7-Docker-Container **nicht entfernen**; dortige Datensicherung erstellen.
2. Zentrale API deployen und `/health` testen.
3. Flip-7-Pages-v2 veröffentlichen. In Einstellungen den neuen API-Endpunkt als **Basis-URL** (`https://spiele-api.example.com`) und Token eintragen.
4. Bisherige lokale Daten im lokalen Modus per Backup exportieren. Falls die Daten nur auf dem alten Flip-7-Server liegen, **zuerst in der bisherigen v1-PWA** über „Serverdaten lokal übernehmen“ auf das Gerät übertragen. Danach v2 installieren/aktualisieren. Lokale IndexedDB-Daten werden durch das Update nicht gelöscht.
5. In v2 über „Lokale Daten auf Server“ mit Vorschau und Bestätigung übertragen. Bestehende andere `app_id`-Bereiche bleiben erhalten.
6. Vor dem Abschalten des alten Backends prüfen, ob alle Partien und Profile übernommen wurden. Ein direkter automatischer Import der alten SQLite-Datei ist **nicht** umgesetzt.

Yatzy Pages v2.6.0 enthält die für den Umstieg nötige Adapter- und Importoberfläche. Alte Yatzy-Spielbereiche werden nach Vorschau und Bestätigung eingelesen; den alten Container erst nach Datenvergleich und externer Sicherung abschalten.

## Offline / Update

Die zentrale API selbst ist nicht offline. Die Pages-PWA nutzt IndexedDB lokal und einen separaten Service-Worker-App-Shell-Cache, wobei API-Aufrufe nie gecacht werden. Keine Hintergrund-Synchronisation. Update-Mechanismus der Pages-PWA konservativ: manuelle Aktivierung; laufende Eingaben schützen. Bei GitHub-Pages-Ausfall startet die installierte App weiterhin aus dem Cache, wenn die App-Shell zuvor vollständig installiert wurde.

## Bekannte Grenzen und Tests

- Keine Weboberfläche im zentralen Backend (es ist ein reiner API-Dienst; die vorhandenen Spiel-PWAs bleiben unabhängig). Die alte Vorgabe einer vollständigen Docker-Fullstack-App wurde **für diesen ausdrücklich gewünschten zentralen Server** bewusst aufgehoben.
- Kein automatischer Profile-Merge zwischen Spielen, keine universelle Punkte-Berechnung, keine Live-Synchronisation.
- Python-Syntax/SQLite/Backup-Logik sowie ZIP-Inhalte wurden statisch bzw. isoliert geprüft. Flask/Gunicorn HTTP-Laufzeit und realer Cloudflare-, Portainer- und iPhone-PWA-Betrieb müssen noch auf deinem Server getestet werden.
- Bei Server-Modus kann jede App ihren eigenen JSON-Bereich verändern; ein kompromittiertes gemeinsames Token kann alle Spielbereiche betreffen. Tokens und Netzwerkzugriff deshalb absichern; zukünftig per-App-Berechtigungen sinnvoll.
- Portainer: Nach Git-Import ist ein erneutes Build/Redeploy notwendig.

## v1.1 – optional grössere Yatzy-Sicherungen

**Abwärtskompatibles Update des bestehenden Containers**, kein neuer Portainer-Stack und keine Datenbankmigration. Version `v1.1` ergänzt nur `MAX_APP_DOC_BYTES` (Standard weiterhin 3_000_000 Bytes, maximal 25_000_000). Wenn eine vollständige Yatzy-Datenübernahme aufgrund vieler Bilder beim PUT mit HTTP 413 scheitert, kann im bestehenden Portainer-Stack `MAX_APP_DOC_BYTES: "20000000"` gesetzt und die Spiele-Zentrale aus dem neuen Git-Release neu gebaut werden. Das Limit gilt je App-Dokument, nicht je Partie. `/api/config` zeigt `maxAppDocumentBytes`. Flip 7 bleibt unter seinem bisherigen eigenen App-Schlüssel unverändert.

**Achtung:** Bei grossen eingebetteten Bildern erzeugt jedes UPDATE eines Yatzy-Dokuments ein vollständiges JSON-Backup; das kann viel Speicherplatz benötigen. Lange Frist: getrennte Binärdateien statt Data-URLs im SQLite-JSON. Bestehendes `/app/data` unbedingt als Volume erhalten und vor dem Update ein externes Backup erzeugen. Neue Konfigurationsvariable nicht notwendig, solange das alte 3-MB-Limit genügt.
