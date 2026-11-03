# Spiele-Zentrale v2.0.3 – Flat Git + lokaler Frontend-Betrieb

Diese Version baut weiterhin direkt aus deinem GitHub-Repository über Portainer. Die Build-Struktur bleibt bewusst flach: `Dockerfile`, `app.py`, `requirements.txt` im Repository-Root.

## Neu in v2.0.3

- Lokaler Betrieb von Frontend und API unter derselben Serveradresse.
- Admin-Seite zeigt Server-/API-Adresse an und bietet Schnellwahl für `yatzy`, `flip7` und `braendi-fifty`.
- Startseite kennt Brändi Fifty.
- Bekannte Yatzy-/Flip-7-Pakete können beim Upload mit dem LAN-Adapter auf `location.origin` angepasst werden.
- Brändi Fifty ist bereits für same-origin/LAN vorbereitet.
- Bestehende SQLite-App-Dokumente bleiben unverändert; Plattformtabellen werden nur ergänzt.
- `REQUIRE_EXISTING_DB=1` verhindert den versehentlichen Start mit einer leeren Ersatzdatenbank.

## Portainer

Verwende `compose-portainer.yml`. In Portainer müssen mindestens diese Variablen gesetzt sein:

- `SPIELE_API_TOKEN`: bestehender Spiele-API-Token, nicht ändern.
- `SPIELE_ADMIN_TOKEN`: separater Admin-Token mit mindestens 24 Zeichen.

Das Datenverzeichnis bleibt:

`/home/buebi/docker/spiele-zentrale/data:/app/data`

## Frontends installieren

Öffne nach dem Deploy:

`http://SERVER-IP:8110/admin/`

Dann installiere mit diesen IDs:

- Yatzy: `yatzy` – LAN-Adapter aktiviert lassen.
- Flip 7: `flip7` – LAN-Adapter aktiviert lassen.
- Brändi Fifty: `braendi-fifty` – same-origin ist bereits eingebaut.

Danach erscheinen die Spiele auf:

`http://SERVER-IP:8110/`

und einzeln unter `/apps/<id>/`.

## GitHub Pages Übergang

`PWA_ALLOWED_ORIGIN` bleibt vorerst auf `https://ashguard85.github.io`, damit bereits installierte alte Pages-Versionen weiter auf die API zugreifen können. Erst wenn alle Geräte auf die lokal gehosteten Frontends umgestellt sind, kann die Variable geleert werden.

## Teststand

Geprüft wurden Python-Syntax, Dockerfile-COPY-Pfade, Compose-YAML, Erkennung eines bestehenden alten `app_documents`-Schemas, Erhalt vorhandener Yatzy-/Flip-7-Revisionen, ZIP-Validierung und Installation von Brändi Fifty sowie dessen relative PWA-Pfade unter `/apps/braendi-fifty/`.

Ein vollständiger Container-/Browserlauf konnte in der Ausführungsumgebung nicht gestartet werden, weil dort keine Docker-Daemon-/Flask-Laufzeit mit Internetzugang verfügbar ist.
