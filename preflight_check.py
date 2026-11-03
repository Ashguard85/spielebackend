"""Read-only check of a pre-existing Spiele-Zentrale SQLite file/volume."""
import sys,sqlite3
from pathlib import Path


def inspect(path):
    uri='file:'+str(path.resolve())+'?mode=ro'
    with sqlite3.connect(uri,uri=True) as c:
        tables={r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        table=next((x for x in ('app_documents','app_states','app_state') if x in tables),None)
        if not table:
            print('Keine kompatible App-Tabelle; vorhanden:',', '.join(sorted(tables)));return False
        cols={r[1] for r in c.execute('PRAGMA table_info("'+table+'")')}
        appcol=next((x for x in ('app_id','app','game_id','id') if x in cols),None)
        revcol=next((x for x in ('revision','rev','version') if x in cols),None)
        if not appcol or not revcol:
            print('App-Tabelle vorhanden, unbekannte Spalten:',', '.join(cols));return False
        print('SQLite:',path,'Tabelle:',table)
        for row in c.execute(f'SELECT "{appcol}","{revcol}" FROM "{table}"'):
            print('  Spiel:',row[0],'Revision:',row[1])
        if 'players' in tables:
            print('  Gemeinsame Spielerprofile:',c.execute('SELECT count(*) FROM "players"').fetchone()[0])
        return True

if __name__=='__main__':
    if len(sys.argv)!=2:
        print('Aufruf: python preflight_check.py /ABSOLUTER/PFAD/ZU/ALTEN/DATEN');sys.exit(2)
    arg=Path(sys.argv[1]);files=sorted((p for p in arg.iterdir() if p.suffix in ('.db','.sqlite','.sqlite3')) if arg.is_dir() else [arg])
    if not files:
        print('FEHLER: Keine SQLite-Datei gefunden. Host-Bind-Mount kontrollieren.');sys.exit(1)
    ok=0
    for path in files:
        try:ok+=int(inspect(path))
        except (OSError,sqlite3.DatabaseError) as e:print('FEHLER:',path,e)
    if ok!=1:
        print('FEHLER: Genau eine passende bestehende Spiele-Datenbank erforderlich. Gefunden:',ok)
        sys.exit(1)
    print('OK: Bestehende Spieldaten erkannt. Dies prüft nur das Datenbankschema; kein Docker-Update erfolgt.')
