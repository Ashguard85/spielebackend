# Spiele-Zentrale v2 – Portainer Remote-Git-Build

Dieses Paket ist für das bestehende Repository `Ashguard85/spielebackend` gedacht. Die Dateien gehören direkt ins Repository-Root, damit `build.context: https://github.com/Ashguard85/spielebackend.git#main` den Dockerfile findet.

## Portainer-Variablen

- `SPIELE_API_TOKEN`: exakt der bisherige API-Token (nicht ändern, sonst funktionieren vorhandene PWAs nicht mehr).
- `SPIELE_ADMIN_TOKEN`: neuer separater Token, mindestens 24 Zeichen, darf nicht mit `SPIELE_API_TOKEN` identisch sein.

`DB_PATH` wird absichtlich **nicht** gesetzt. Mit `DATA_DIR=/app/data` und `REQUIRE_EXISTING_DB=1` sucht v2 nach genau einer bestehenden kompatiblen `.db`, `.sqlite` oder `.sqlite3` im gemounteten Ordner. Bei keiner oder mehreren passenden DBs wird der Start verweigert.

Der Stack setzt vorerst `user: "0:0"`, damit eine bestehende root-eigene SQLite-Datei im Bind-Mount weiter beschreibbar ist. Später kann auf einen unprivilegierten UID/GID umgestellt werden, nachdem die Besitzrechte auf dem Host bewusst angepasst wurden.
