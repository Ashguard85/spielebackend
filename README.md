# Spiele-Zentrale v2.0.2 – Portainer Remote-Git-Build

Diese Variante ist absichtlich auf denselben Build-Weg wie das bisher funktionierende Backend reduziert. Docker benoetigt aus Git beim Build nur `Dockerfile`, `requirements.txt` und `app.py`. Keine Unterordner, kein `start.sh`, kein `useradd`.

## GitHub Root

Im Branch `main` muessen direkt sichtbar sein: `Dockerfile`, `app.py`, `requirements.txt`. `compose-portainer.yml` ist nur die Vorlage fuer den Portainer Web Editor.

## Portainer Variablen

`SPIELE_API_TOKEN` muss der bisherige API-Token bleiben. Neu ist `SPIELE_ADMIN_TOKEN` mit mindestens 24 Zeichen und ungleich dem API-Token. `REQUIRE_EXISTING_DB=1` schuetzt davor, bei einem falschen Bind-Mount eine leere DB anzulegen.

## Diagnose vor Deploy

Wenn Portainer beim Stack weiterhin bereits vor dem Container mit HTTP 500 abbricht, liegt der Fehler nicht mehr an fehlenden COPY-Unterordnern. Dann im Portainer Stack/Build-Fehler die Build-Ausgabe ansehen. Bei einem sofort beendeten Container pruefen: `docker ps -a --filter name=spiele-zentrale` und `docker logs spiele-zentrale`.

## Empfohlenes Portainer-Vorgehen

1. ZIP-Inhalt direkt ins Root des bestehenden GitHub-Repositories importieren (kein zusätzlicher Unterordner).
2. Auf GitHub im Branch `main` prüfen: `Dockerfile`, `app.py`, `requirements.txt` liegen direkt oben.
3. In Portainer die Variablen `SPIELE_API_TOKEN` (bestehender Wert) und `SPIELE_ADMIN_TOKEN` (neu, >=24 Zeichen und anders als API-Token) setzen.
4. Den bestehenden Bind-Mount `/home/buebi/docker/spiele-zentrale/data:/app/data` unverändert lassen.
5. Das Compose aus `compose-portainer.yml` verwenden.

Die Compose-Datei markiert beide Tokens als Pflichtvariablen. Fehlt eine davon, soll Portainer bereits bei der Konfiguration einen verständlichen Fehler anzeigen, statt einen Container mit leerem Token zu starten.
