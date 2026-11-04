# Spiele-Zentrale 2.4.0 – Endzustand nach Migration

Die Plattform erwartet Frontend und API auf derselben Origin, z. B. lokal `http://SERVER:8110` oder extern `https://play.haasenheim.com`.

Die Compose-Datei benötigt keine Variable `PWA_ALLOWED_ORIGIN` mehr. Bestehendes Volume, `APP_API_TOKEN`, `ADMIN_TOKEN`, `REQUIRE_EXISTING_DB=1` und die übrigen Grenzwerte bleiben erhalten.

Die SQLite-Kompatibilitätsadapter bleiben absichtlich im Code. Sie rufen keine alten Server auf; sie verhindern nur, dass die migrierte Datenbank wegen eines historischen Tabellennamens oder Spaltennamens nicht mehr gelesen werden kann.
