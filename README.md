# Spiele-Zentrale 2.4.0 – bereinigter Endstand

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
