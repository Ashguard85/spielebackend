# Spiele-Zentrale 2.4.1 – bereinigter Endstand

Für den Betrieb nach der Migration: lokale URL oder `https://play.haasenheim.com`, Frontends unter `/apps/<id>/`, API und Admin auf derselben Origin.

## Entfernt

- `PWA_ALLOWED_ORIGIN` / GitHub-Pages-CORS;
- Cloudflare-Service-Token-Header in CORS;
- automatischer LAN-Adapter beim Frontend-ZIP-Upload;
- Pages-spezifische Installationshinweise.

## Beibehalten

- bestehende SQLite-Schema-Adapter und alte Tabellen **nur zur sicheren Datenkompatibilität**;
- `APP_API_TOKEN` für einmalige Geräteanmeldung und Bearer-Fallback;
- `ADMIN_TOKEN`;
- HttpOnly-Gerätesitzungen;
- gemeinsame Spieler und Bereiche;
- App-Dokumente mit Revisionen, Snapshots und SQLite-Backups;
- Frontend-Hosting und Admin-Installation.

Die Kompatibilitätsadapter sollten nicht entfernt werden, solange dieselbe migrierte SQLite-Datenbank weiterverwendet wird. Sie erzeugen keinen externen Sync und kontaktieren keinen alten Server.


Neu in v2.4.0: frei sortierbare Startseite und zentral verwaltete PNG/JPG/WebP-Spielicons, die Startseite und Punkteblöcke gemeinsam verwenden.


## Fix in v2.4.1

- Zentral hochgeladene Spielicons erhalten eine versionsabhängige URL.
- Beim Ersetzen eines Icons können Browser oder Cloudflare dadurch nicht mehr das alte Bild unter derselben URL weiterverwenden.
- Der Icon-Endpunkt liefert zusätzlich strikte No-Cache-Header.
- Kein Frontend-Update erforderlich; bestehende Apps übernehmen die neue `iconUrl` automatisch über `/api/apps`.


## Backup-Aufbewahrung

- `BACKUP_KEEP` begrenzt ausschliesslich die internen `platform_app_backups`-Snapshots pro App. Die Bereinigung passiert beim nächsten erfolgreichen Speichern der jeweiligen App.
- Vollständige SQLite-Dateien unter `DATA_DIR/backups/*.sqlite` werden nicht automatisch gelöscht.
- Frontend-Sicherungen unter `DATA_DIR/backups/frontends/*.zip` werden ebenfalls nicht automatisch gelöscht.
- Ab v2.4.2 können beide Backup-Typen im Adminbereich einzeln heruntergeladen und nach Bestätigung gelöscht werden.


## v2.5.0 – ZIP-Vorschau / Debug

Im Adminbereich können Frontend-ZIPs temporär geprüft werden, ohne sie zu installieren. Vorschauen laufen unter `/apps/preview-<id>/` mit eigener Test-API, eigenem Browser-Datenpräfix und ohne Service Worker. Produktive App-Dokumente werden nicht verändert. Standard-Aufbewahrung: 24 Stunden (`PREVIEW_KEEP_HOURS`), maximal 10 Vorschauen (`PREVIEW_MAX`).
