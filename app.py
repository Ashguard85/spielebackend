"""Spiele-Zentrale v2.0.2 – single-file Git build for Portainer.

All platform code and admin assets are intentionally kept in app.py so Docker's
remote Git build only requires root-level files. Existing game data remains in
the mounted SQLite database.
"""
from __future__ import annotations
import os, json, re, hmac, tempfile, zipfile, sqlite3, traceback, uuid, stat, shutil, hashlib
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit
from datetime import datetime, timezone
from flask import Flask, request, jsonify, Response, redirect, send_file, send_from_directory, abort

"""SQLite adapter: backwards-friendly with existing app_documents schemas.

Never drops or rewrites existing application tables. Fails closed on unknown columns.
"""
import json, os, sqlite3, re, uuid
from pathlib import Path
from datetime import datetime, timezone

VALID_ID = re.compile(r'^[a-z][a-z0-9_-]{1,39}$')


def utc():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def db_location():
    folder = Path(os.getenv('DATA_DIR', '/app/data')).resolve()
    folder.mkdir(parents=True, exist_ok=True)
    explicit = os.getenv('DB_PATH', '').strip()
    if explicit:
        path = Path(explicit).resolve()
        if path.parent != folder and os.getenv('ALLOW_EXTERNAL_DB_PATH') != '1':
            raise RuntimeError('DB_PATH muss im DATA_DIR liegen (oder ALLOW_EXTERNAL_DB_PATH=1 setzen)')
        if os.getenv('REQUIRE_EXISTING_DB', '0') == '1' and not path.is_file():
            raise RuntimeError(f'Vorhandene SQLite-Datei fehlt: {path}. Volume-Pfad prüfen!')
        return path
    candidates = sorted(p for p in folder.iterdir() if p.is_file() and p.suffix in ('.db', '.sqlite', '.sqlite3'))
    known = []
    for p in candidates:
        try:
            uri = f'file:{p}?mode=ro'
            with sqlite3.connect(uri, uri=True) as db:
                tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if tables.intersection({'app_documents','app_states','app_state'}): known.append(p)
        except sqlite3.DatabaseError: continue
    if len(known) > 1:
        raise RuntimeError('Mehrere mögliche alte Spiele-Datenbanken: DB_PATH explizit setzen.')
    if len(known) == 1: return known[0]
    if candidates and os.getenv('REQUIRE_EXISTING_DB', '0') == '1':
        raise RuntimeError('Keine kompatible Spiele-Datenbank gefunden. DB_PATH oder altes Volume prüfen.')
    if os.getenv('REQUIRE_EXISTING_DB', '0') == '1':
        raise RuntimeError('REQUIRE_EXISTING_DB=1, aber keine bestehende SQLite-Datenbank gefunden. Volume prüfen.')
    if candidates: raise RuntimeError('SQLite-Dateien vorhanden, aber Spiele-Schema unbekannt. DB_PATH prüfen, kein neues DB erstellen.')
    return folder / 'spiele.sqlite'

DB_FILE = None

def db_path():
    global DB_FILE
    if DB_FILE is None: DB_FILE = db_location()
    return DB_FILE


class ManagedConnection(sqlite3.Connection):
    def __exit__(self, exc_type, exc, tb):
        try: return super().__exit__(exc_type, exc, tb)
        finally: self.close()


def connect():
    c = sqlite3.connect(str(db_path()), timeout=15, isolation_level=None, factory=ManagedConnection)
    c.row_factory = sqlite3.Row
    c.execute('PRAGMA busy_timeout=15000')
    c.execute('PRAGMA journal_mode=WAL')
    c.execute('PRAGMA foreign_keys=ON')
    return c


def table_exists(conn, table):
    return bool(conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(table,)).fetchone())


def columns(conn,table):
    if table not in ('app_documents','app_states','app_state'): raise ValueError('unknown table')
    return {r['name']:r for r in conn.execute('PRAGMA table_info("'+table+'")')}


def document_adapter(conn):
    tables = [t for t in ('app_documents','app_states','app_state') if table_exists(conn,t)]
    if not tables:
        conn.execute('''CREATE TABLE app_documents (
          app_id TEXT PRIMARY KEY, data_json TEXT, revision INTEGER NOT NULL DEFAULT 0,
          updated_at TEXT NOT NULL DEFAULT '')''')
        tables=['app_documents']
    if len(tables)>1:
        raise RuntimeError('Mehrere App-Zustandstabellen vorhanden; automatische Auswahl verweigert')
    table=tables[0]; cols=columns(conn,table)
    idcol=next((x for x in ('app_id','app','game_id','id') if x in cols),None)
    datacol=next((x for x in ('data_json','state_json','document_json','payload_json','data','payload','state','json') if x in cols),None)
    revcol=next((x for x in ('revision','rev','version') if x in cols),None)
    updated=next((x for x in ('updated_at','modified_at') if x in cols),None)
    if not (idcol and datacol and revcol):
        raise RuntimeError(f'Unbekanntes {table}-Schema: {list(cols)}. Vor Update prüfen; keine Daten verändert.')
    created=next((x for x in ('created_at','created_on') if x in cols),None)
    for key, meta in cols.items():
        if key not in (idcol,datacol,revcol,updated,created) and meta['notnull'] and meta['dflt_value'] is None and not meta['pk']:
            raise RuntimeError(f'Datenschema verlangt Zusatzspalte {key}; schreibender Zugriff verweigert')
    return table,idcol,datacol,revcol,updated


def init():
    with connect() as c:
        document_adapter(c)
        c.execute('''CREATE TABLE IF NOT EXISTS platform_registry (
          app_id TEXT PRIMARY KEY, display_name TEXT, frontend_installed INTEGER NOT NULL DEFAULT 0,
          state TEXT NOT NULL DEFAULT 'active' CHECK(state IN ('active','disabled','deleted')),
          created_at TEXT NOT NULL, updated_at TEXT NOT NULL)''')
        c.execute('''CREATE TABLE IF NOT EXISTS platform_app_backups (
          id TEXT PRIMARY KEY, app_id TEXT NOT NULL, revision INTEGER, data_json TEXT,
          created_at TEXT NOT NULL, reason TEXT NOT NULL)''')
        c.execute('''CREATE TABLE IF NOT EXISTS platform_audit (
          id INTEGER PRIMARY KEY AUTOINCREMENT, event TEXT NOT NULL, app_id TEXT,
          created_at TEXT NOT NULL, detail TEXT)''')
        c.execute('''CREATE TABLE IF NOT EXISTS platform_player_profiles (
          id TEXT PRIMARY KEY, name TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)''')


def as_dict(row): return dict(row) if row else None


def registry(conn,app):
    r=conn.execute('SELECT * FROM platform_registry WHERE app_id=?',(app,)).fetchone()
    return as_dict(r)


def state_allowed(conn,app):
    r=registry(conn,app)
    return r is None or r['state']=='active'


def get_doc(conn,app):
    t,i,d,r,u=document_adapter(conn)
    row=conn.execute(f'SELECT "{d}" AS body,"{r}" AS rev '+(f',"{u}" AS modified' if u else '')+f' FROM "{t}" WHERE "{i}"=?',(app,)).fetchone()
    if row is None:return {'revision':0,'data':None,'updatedAt':None}
    text=row['body']
    try: body=json.loads(text) if text is not None else None
    except (ValueError,TypeError):raise RuntimeError('Vorhandene App-Daten sind kein gültiges JSON, kein Überschreiben')
    return {'revision':int(row['rev']),'data':body,'updatedAt':row['modified'] if u else None}


def set_doc(conn,app,body,revision):
    t,i,d,r,u=document_adapter(conn)
    old=get_doc(conn,app)
    if revision!=old['revision']: return None
    payload=json.dumps(body,ensure_ascii=False,separators=(',',':'))
    if old['data'] is not None:
        conn.execute('INSERT INTO platform_app_backups VALUES(?,?,?,?,?,?)',
            (str(uuid.uuid4()),app,old['revision'],json.dumps(old['data'],ensure_ascii=False),utc(),'before_update'))
        keep=max(1,min(500,int(os.getenv('BACKUP_KEEP','50'))))
        conn.execute('DELETE FROM platform_app_backups WHERE app_id=? AND id NOT IN '+
          '(SELECT id FROM platform_app_backups WHERE app_id=? ORDER BY rowid DESC LIMIT ?)',(app,app,keep))
    if old['revision']==0 and old['data'] is None:
        created=next((col for col in ('created_at','created_on') if col in columns(conn,t)),None)
        names=[i,d,r]+([u] if u else [])+([created] if created else [])
        vals=[app,payload,1]+([utc()] if u else [])+([utc()] if created else [])
        placeholders=','.join('?' for _ in names)
        conn.execute(f'INSERT INTO "{t}" ('+','.join('"'+n+'"' for n in names)+f') VALUES({placeholders})',vals)
        return 1
    names=[f'"{d}"=?',f'"{r}"=?']+([f'"{u}"=?'] if u else [])
    vals=[payload,old['revision']+1]+([utc()] if u else [])+[app,revision]
    result=conn.execute(f'UPDATE "{t}" SET '+','.join(names)+f' WHERE "{i}"=? AND "{r}"=?',vals)
    if result.rowcount!=1:raise RuntimeError('Paralleländerung während Transaktion')
    return old['revision']+1


def player_adapter(conn):
    """Prefer the existing shared players table instead of making a second player database.

    Never change or drop the legacy table; if it is incompatible, we only read
    the keys we understand and refuse unsafe writes.
    """
    if table_exists(conn,'players'):
        cols={r['name']:r for r in conn.execute('PRAGMA table_info("players")')}
        identifier=next((x for x in ('id','player_id') if x in cols),None)
        name=next((x for x in ('name','display_name') if x in cols),None)
        if identifier and name:return 'players',identifier,name,cols
        raise RuntimeError('Bestehende players-Tabelle hat ein unbekanntes Schema. Profile nicht überschreiben.')
    cols={r['name']:r for r in conn.execute('PRAGMA table_info("platform_player_profiles")')}
    return 'platform_player_profiles','id','name',cols


def all_players(conn):
    table,key,name,cols=player_adapter(conn)
    rows=[]
    for row in conn.execute(f'SELECT * FROM "{table}"'):
        p={'id':row[key],'name':row[name]}
        for field in ('created_at','updated_at'):
            if field in cols:p[field]=row[field]
        rows.append(p)
    return sorted(rows,key=lambda p:str(p['name']).lower())


def add_player(conn,identifier,name):
    table,key,name_col,cols=player_adapter(conn)
    for field,meta in cols.items():
        if field not in (key,name_col,'created_at','updated_at') and meta['notnull'] and meta['dflt_value'] is None and not meta['pk']:
            raise RuntimeError('Players-Schema benötigt weiteres Pflichtfeld: '+field)
    fields=[key,name_col]+[k for k in ('created_at','updated_at') if k in cols]
    values=[identifier,name]+[utc() for k in fields[2:]]
    conn.execute(f'INSERT INTO "{table}" ('+','.join('"'+x+'"' for x in fields)+') VALUES ('+','.join('?' for x in fields)+')',values)


def all_docs(conn):
    t,i,d,r,u=document_adapter(conn)
    return {row['app']: {'revision':row['rev'], 'has_data':bool(row['body'])} for row in conn.execute(
      f'SELECT "{i}" AS app,"{r}" AS rev,"{d}" AS body FROM "{t}"')}


def remove_doc(conn,app):
    t,i,d,r,u=document_adapter(conn)
    old=get_doc(conn,app)
    if old['data'] is not None:
        conn.execute('INSERT INTO platform_app_backups VALUES(?,?,?,?,?,?)',(
            str(uuid.uuid4()),app,old['revision'],json.dumps(old['data'],ensure_ascii=False),utc(),'before_delete'))
    conn.execute(f'DELETE FROM "{t}" WHERE "{i}"=?',(app,))
    return old


def set_registry(conn,app,name=None,frontend=None,state=None):
    old=registry(conn,app); now=utc()
    if old:
        conn.execute('UPDATE platform_registry SET display_name=?,frontend_installed=?,state=?,updated_at=? WHERE app_id=?',(
            name if name is not None else old['display_name'],
            int(frontend) if frontend is not None else old['frontend_installed'],
            state if state is not None else old['state'],now,app))
    else:
        conn.execute('INSERT INTO platform_registry VALUES(?,?,?,?,?,?)',(
            app,name or app,int(bool(frontend)),state or 'active',now,now))


def audit(conn,event,app=None,detail=None):
    conn.execute('INSERT INTO platform_audit (event,app_id,created_at,detail) VALUES(?,?,?,?)',(
        event,app,utc(),str(detail or '')[:300]))


def list_apps(conn):
    rows={r['app_id']:dict(r) for r in conn.execute('SELECT * FROM platform_registry')}
    docs=all_docs(conn)
    for app,d in docs.items():
        if app not in rows:rows[app]={'app_id':app,'display_name':app,'state':'active','frontend_installed':0}
    for app,info in rows.items():
        info.update(docs.get(app,{'revision':0,'has_data':False}))
    return sorted(rows.values(),key=lambda a:a['app_id'])


def full_backup(label):
    folder=Path(os.getenv('DATA_DIR','/app/data')).resolve()/'backups'
    folder.mkdir(parents=True,exist_ok=True)
    stem=re.sub(r'[^a-z0-9_-]','-',str(label).lower())[:36]
    target=folder/(datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+stem+'-'+uuid.uuid4().hex[:8]+'.sqlite')
    conn=connect()
    try:
        out=sqlite3.connect(str(target))
        try: conn.backup(out)
        finally:out.close()
    finally:conn.close()
    return target


def transaction():
    class Tx:
        def __enter__(self):
            self.c=connect();self.c.execute('BEGIN IMMEDIATE');return self.c
        def __exit__(self,typ,val,tb):
            try:self.c.execute('ROLLBACK' if typ else 'COMMIT')
            finally:self.c.close()
    return Tx()


"""Validate frontend ZIPs and atomically deploy static files under DATA_DIR/apps."""
import os, zipfile, stat, re, shutil, tempfile, hashlib, json
from pathlib import Path, PurePosixPath

VALID_TYPES={'.html','.js','.mjs','.css','.json','.webmanifest','.svg','.png','.jpg','.jpeg','.gif','.webp','.ico','.txt','.woff','.woff2','.ttf','.wasm','.map','.avif'}
VALID_BASENAMES={'LICENSE','LICENSE.md','VERSION','README.md'}
MAX_FILES=600
MAX_UNPACKED=70*1024*1024
MAX_FILE=30*1024*1024


def apps_root():
    root=Path(os.getenv('DATA_DIR','/app/data')).resolve()/'apps'
    root.mkdir(parents=True,exist_ok=True)
    return root


def app_folder(app_id):
    if not VALID_ID.fullmatch(app_id):raise ValueError('Ungültige Spielkennung')
    return apps_root()/app_id


def _safe_entry(info):
    name=info.filename
    if (not name or len(name)>240 or '\\' in name or name.startswith('/') or '\x00' in name
        or ':' in name or name.startswith('//')):
        raise ValueError('Unsicherer ZIP-Dateipfad')
    path=PurePosixPath(name)
    if any(seg in ('.','..') or seg.startswith('.') for seg in path.parts):
        raise ValueError('Versteckte Verzeichnisse oder ../ im ZIP nicht erlaubt')
    if not path.parts or len(path.parts)>8:raise ValueError('ZIP-Verzeichnistiefe zu gross')
    for seg in path.parts:
        if not re.fullmatch(r'[A-Za-z0-9_\-.@ +()]+',seg):
            raise ValueError('Ungewöhnlicher ZIP-Dateiname: '+name[:80])
    if path.parts[0].lower() in ('api','admin','static','app','platform'):
        raise ValueError('Verbotener ZIP-Root-Pfad')
    mode=(info.external_attr >> 16) & 0xffff
    if stat.S_ISLNK(mode):raise ValueError('ZIP darf keine Symlinks enthalten')
    if mode and stat.S_IFMT(mode) not in (0,stat.S_IFREG,stat.S_IFDIR):
        raise ValueError('Spezialdateien im ZIP verboten')
    if info.is_dir():return name
    if (path.suffix.lower() not in VALID_TYPES and path.name not in VALID_BASENAMES):
        raise ValueError('Nicht erlaubter Dateityp: '+path.name)
    return name


def validated_files(zip_path):
    files={}; seen=set(); size=0
    with zipfile.ZipFile(zip_path) as z:
        infos=z.infolist()
        if len(infos)>MAX_FILES:raise ValueError('Zu viele Dateien im ZIP')
        for info in infos:
            name=_safe_entry(info)
            canon=name.rstrip('/').casefold()
            if canon in seen:raise ValueError('Doppelter ZIP-Pfad: '+name)
            seen.add(canon)
            if info.is_dir():continue
            if info.file_size>MAX_FILE:raise ValueError('Datei zu gross: '+name)
            size+=info.file_size
            if size>MAX_UNPACKED:raise ValueError('ZIP insgesamt zu gross')
            if info.compress_size and info.file_size/info.compress_size>500:
                raise ValueError('Verdächtig hohe ZIP-Kompression')
            data=z.read(info)
            if len(data)!=info.file_size:raise ValueError('ZIP-Lesefehler')
            files[name]=data
    if 'index.html' not in files:
        raise ValueError('index.html muss direkt im ZIP-Root liegen. Kein zusätzlicher Projektordner.')
    if b'<html' not in files['index.html'].lower() and b'<!doctype html' not in files['index.html'].lower():
        raise ValueError('index.html scheint keine HTML-App zu sein')
    return files


def lan_adapter(files,app):
    """Deliberate, narrow compatibility patch for previously shipped Pages apps.

    Source is changed only if exact historic strings match; never patch other apps.
    The API token must still be entered by the user in the app settings.
    """
    changes=[]
    if app=='yatzy' and 'app.js' in files:
        src=files['app.js'].decode('utf-8')
        old="const url=String($('centralUrl').value||deviceSettings.centralUrl||'').trim().replace(/\\/+$/,'');"
        new="const url=String($('centralUrl').value||deviceSettings.centralUrl||location.origin).trim().replace(/\\/+$/,'');"
        if old in src:src=src.replace(old,new,1);changes.append('Yatzy: gleiche Serveradresse vorausfüllen')
        old="parsed.protocol==='http:' && ['localhost','127.0.0.1'].includes(parsed.hostname)"
        new="parsed.protocol==='http:' && (['localhost','127.0.0.1'].includes(parsed.hostname)||parsed.origin===location.origin)"
        if old in src:src=src.replace(old,new,1);changes.append('Yatzy: HTTP nur auf identischem LAN-Origin zulassen')
        files['app.js']=src.encode('utf-8')
    if app=='flip7' and 'providers.js' in files:
        src=files['providers.js'].decode('utf-8')
        old="if(!config.docker&&!/^https:\\/\\/[^\\s/]+/i.test(base))"
        new="if(!config.docker&&!(/^https:\\/\\/[^\\s/]+/i.test(base)||(base===location.origin&&location.protocol==='http:')))"
        if old in src:src=src.replace(old,new,1);changes.append('Flip7: HTTP nur auf identischem LAN-Origin zulassen')
        files['providers.js']=src.encode('utf-8')
    return changes


def install(app_id,zip_path,patch_lan=False):
    # Call only after admin authentication and a full external DB backup for upgrades.
    files=validated_files(zip_path)
    changes=lan_adapter(files,app_id) if patch_lan else []
    # Change the SW bytes on a new package install; existing PWAs still decide
    # themselves when a waiting SW may be activated (no forced reload).
    fingerprint=hashlib.sha256(Path(zip_path).read_bytes()+str(bool(patch_lan)).encode()).hexdigest()[:20]
    for worker in ('sw.js','service-worker.js'):
        if worker in files:
            files[worker]+=f"\n/* spiele-zentrale-install:{fingerprint} */\n".encode()
    root=apps_root()
    staging=Path(tempfile.mkdtemp(prefix='.upload-',dir=str(root)))
    target=app_folder(app_id)
    old=None
    try:
        for name,payload in files.items():
            destination=staging/name
            destination.parent.mkdir(parents=True,exist_ok=True)
            destination.write_bytes(payload)
        metadata={'app_id':app_id,'installed_at':utc(),'sha256':hashlib.sha256(Path(zip_path).read_bytes()).hexdigest(),
                  'file_count':len(files),'lan_adapter_changes':changes}
        # Internal hidden metadata is not served as static content.
        (staging/'.platform.json').write_text(json.dumps(metadata,ensure_ascii=False))
        if target.exists():
            old=root/('.previous-'+app_id+'-'+os.urandom(5).hex())
            target.rename(old)
        try:staging.rename(target)
        except Exception:
            if old:old.rename(target)
            raise
        if old:
            try:shutil.rmtree(old)
            except OSError:pass # New version is already live; preserve rollback directory for admin inspection.
        return metadata
    finally:
        if staging.exists():shutil.rmtree(staging)


def backup_frontend(app_id,label='before-uninstall'):
    folder=app_folder(app_id)
    if not folder.is_dir():return None
    import datetime
    target=Path(os.getenv('DATA_DIR','/app/data')).resolve()/'backups'/'frontends'
    target.mkdir(parents=True,exist_ok=True)
    name=(datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+label+'-'+app_id+'-'+os.urandom(4).hex()+'.zip')
    destination=target/name
    with zipfile.ZipFile(destination,'w',zipfile.ZIP_DEFLATED,compresslevel=7) as z:
        for p in sorted(folder.rglob('*')):
            if p.is_file() and not p.is_symlink() and not p.name.startswith('.'):
                z.write(p,p.relative_to(folder).as_posix())
    return name


def remove_frontend(app_id):
    folder=app_folder(app_id)
    if folder.exists():shutil.rmtree(folder)


def manifest(app_id):
    metadata=app_folder(app_id)/'.platform.json'
    if metadata.is_file():
        try:return json.loads(metadata.read_text())
        except (OSError,ValueError):return None
    return None


STATIC_ASSETS={'index.html': '<!doctype html><html lang="de-CH"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover"><meta name="color-scheme" content="light dark"><meta name="theme-color" content="#177f83"><title>Spiele-Zentrale</title><link rel="stylesheet" href="/static/site.css"></head><body>\n<header class="top"><div class="shell row-between"><div class="brand"><div class="brand-mark">▦</div><div><strong>Spiele-Zentrale</strong><small>Deine Spiele auf dem Ubuntu-Server</small></div></div><a class="subtle-link" href="/admin/">Verwaltung</a></div></header>\n<main class="shell"><div class="intro"><span class="eyebrow">Deine Spiele</span><h1>Was spielen wir heute?</h1><p>Alle hier installierten Spiele laufen direkt auf deinem Server. Die Daten liegen in der zentralen SQLite-Datenbank.</p></div><section class="tiles" id="apps" aria-live="polite"><div class="empty">Installierte Spiele werden geladen …</div></section><section class="hint"><h2>Noch ein Spiel hinzufügen?</h2><p>In der <a href="/admin/">Verwaltung</a> kannst du die ZIP-Pakete deiner bestehenden Yatzy- und Flip-7-PWAs importieren und aktualisieren.</p></section></main><footer class="shell foot">Nur im Heimnetz? Ein Cloudflare Tunnel ist für den Zugriff über die LAN-IP nicht nötig. Für volle PWA-/Offline-Funktionen auf dem iPhone ist vertrauenswürdiges HTTPS erforderlich.</footer><script src="/static/home.js" defer></script></body></html>\n', 'admin.html': '<!doctype html><html lang="de-CH"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover"><meta name="color-scheme" content="light dark"><meta name="theme-color" content="#177f83"><title>Verwaltung · Spiele-Zentrale</title><link rel="stylesheet" href="/static/site.css"></head><body>\n<header class="top"><div class="shell row-between"><div class="brand"><div class="brand-mark">▦</div><div><strong>Spiele-Zentrale</strong><small>Administration</small></div></div><a class="subtle-link" href="/">Zur Startseite</a></div></header>\n<main class="shell admin">\n<section class="intro"><span class="eyebrow">Server-Verwaltung</span><h1>Spiele verwalten</h1><p>Die Daten bleiben in einer gemeinsamen SQLite-Datenbank. Installationen und Löschungen erfordern einen separaten Administrator-Token.</p></section>\n<div class="notice warning">Nur eigene, vertrauenswürdige Frontend-ZIPs installieren: Enthaltenes JavaScript wird unter deiner Serveradresse ausgeführt. Im Heimnetz ist HTTP für normale Seiten möglich; API-/Admin-Tokens sind über HTTP jedoch nicht verschlüsselt. Für den produktiven iPhone-Betrieb empfehlen wir lokales HTTPS.</div>\n<section class="panel" aria-labelledby="accessHead"><h2 id="accessHead">Administratorzugang</h2><label class="field">Administrator-Token <input type="password" id="adminToken" autocomplete="off" spellcheck="false" placeholder="SPIELE_ADMIN_TOKEN aus deiner Portainer-Umgebung"></label><div class="actions"><button id="connect" class="button primary">Verbinden</button><button id="disconnect" class="button secondary">Trennen</button></div><p id="status" role="status" class="status">Nicht angemeldet. Der Token wird nur im Arbeitsspeicher dieser Seite gehalten.</p></section>\n<section class="panel" aria-labelledby="installedHead"><div class="row-between"><h2 id="installedHead">Installierte und gespeicherte Spiele</h2><button id="refresh" class="button secondary" disabled>Aktualisieren</button></div><div id="apps" class="app-list"><div class="empty">Mit Administrator-Token verbinden.</div></div></section>\n<section class="panel" aria-labelledby="installHead"><h2 id="installHead">Spiel-ZIP installieren oder aktualisieren</h2><form id="installForm"><div class="form-grid"><label class="field">Spielkennung (muss zur API passen)<input id="appId" name="app_id" pattern="[a-z][a-z0-9_-]{1,39}" required placeholder="yatzy oder flip7" autocomplete="off"></label><label class="field">Anzeigename<input id="displayName" name="display_name" maxlength="80" placeholder="Yatzy Duell"></label></div><label class="field">Pages-ZIP aus deinen bestehenden Releases<input type="file" id="zip" name="zip" accept=".zip,application/zip" required></label><label class="check"><input type="checkbox" id="overwrite" name="overwrite" value="yes"> Bestehendes Frontend bewusst aktualisieren</label><label class="check"><input type="checkbox" id="lanPatch" name="lan_patch" value="yes" checked> LAN-Adapter für bekannte Yatzy-/Flip-7-Versionen anwenden (lokales HTTP auf gleicher Serveradresse zulassen)</label><label class="check"><input type="checkbox" id="reactivate" name="reactivate" value="yes"> Zuvor vollständig gelöschtes Spiel ausdrücklich neu aktivieren</label><button type="submit" id="installButton" class="button primary">ZIP prüfen und installieren</button></form><p class="help">Es werden nur statische Dateien unter <code>/apps/spiel-id/</code> abgelegt. Kein Python- oder Node-Code aus dem ZIP wird ausgeführt. Vor jedem Update wird ein SQLite-Backup erstellt. Der Browser muss bei Yatzy und Flip 7 weiterhin einmalig den Spiele-API-Token erhalten.</p></section>\n<section class="panel" aria-labelledby="backupHead"><div class="row-between"><h2 id="backupHead">SQLite-Sicherungen</h2><button id="newBackup" class="button primary" disabled>Jetzt sichern</button></div><p>Vor Datenlöschungen wird eine vollständige SQLite-Kopie erstellt. Lade regelmässig eine Kopie ausserhalb des Ubuntu-Servers herunter.</p><div id="backups" class="backup-list"><div class="empty">Noch keine Liste geladen.</div></div></section>\n</main><footer class="shell foot">Backup-Dateien enthalten weiterhin gelöschte Spielstände. Für eine vollständige Datenentfernung müssen alte Backups separat und bewusst nach deinen Aufbewahrungsregeln gelöscht werden.</footer><script src="/static/admin.js" defer></script></body></html>\n', 'site.css': ":root{color-scheme:light dark;--bg:#f2f6f7;--paper:#fff;--text:#193039;--muted:#586e76;--border:#d9e5e7;--accent:#147f85;--accent2:#0b6066;--bad:#bb3f3f;--line:#dbe4e8;font-family:system-ui,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif}\n@media(prefers-color-scheme:dark){:root{--bg:#0d171b;--paper:#18272b;--text:#e4f2f3;--muted:#b0c3c8;--border:#334b51;--accent:#51b9ba;--accent2:#3aa9a9;--bad:#ed8888;--line:#334b51}}\n*{box-sizing:border-box}html{background:var(--bg);-webkit-text-size-adjust:100%}body{margin:0;background:var(--bg);color:var(--text);min-height:100vh}a{color:var(--accent2)}button,input{font:inherit}input:not([type=checkbox]):not([type=file]){display:block;width:100%;margin-top:7px;padding:11px 12px;border:1px solid var(--border);border-radius:10px;background:var(--paper);color:var(--text)}input[type=file]{display:block;width:100%;margin-top:9px;color:var(--text)}.shell{width:min(930px,calc(100% - 32px));margin:auto}.top{background:var(--paper);border-bottom:1px solid var(--border);padding:18px 0}.row-between{display:flex;align-items:center;justify-content:space-between;gap:12px}.brand{display:flex;align-items:center;gap:11px}.brand strong{display:block;font-size:18px}.brand small{display:block;color:var(--muted);font-size:12px;margin-top:3px}.brand-mark{border-radius:12px;background:var(--accent);color:#fff;font-size:27px;min-width:46px;min-height:46px;display:grid;place-items:center}.subtle-link{font-size:14px;text-decoration:none;color:var(--muted);font-weight:650}.intro{padding:35px 0 28px}.intro h1{font-size:clamp(29px,5.2vw,46px);letter-spacing:-.035em;margin:10px 0}.intro p{max-width:650px;color:var(--muted);line-height:1.6}.eyebrow{font-size:12px;text-transform:uppercase;letter-spacing:.15em;color:var(--accent2);font-weight:750}.tiles{display:grid;grid-template-columns:repeat(auto-fill,minmax(235px,1fr));gap:15px}.game-tile{background:var(--paper);border:1px solid var(--border);border-radius:18px;padding:20px;display:flex;align-items:center;gap:16px;text-decoration:none;color:var(--text);min-height:118px;box-shadow:0 8px 22px #00000005}.game-tile:hover,.game-tile:focus-visible{outline:2px solid var(--accent)}.game-icon{font-size:34px}.game-tile h2{font-size:18px;margin:0}.game-tile p{font-size:13px;color:var(--muted);margin:6px 0 0}.arrow{margin-left:auto;font-size:27px;color:var(--accent)}.empty{background:var(--paper);border:1px dashed var(--border);padding:25px;color:var(--muted);border-radius:14px;line-height:1.5;grid-column:1/-1}.hint{background:var(--paper);border:1px solid var(--border);border-radius:16px;margin-top:36px;padding:24px}.hint h2{margin:0 0 8px;font-size:18px}.hint p{margin:0;color:var(--muted);line-height:1.5}.foot{color:var(--muted);padding:32px 0 44px;font-size:13px;line-height:1.6}.admin{display:grid;gap:20px}.admin .intro{padding-bottom:2px}.panel{background:var(--paper);border:1px solid var(--border);border-radius:17px;padding:22px}.panel h2{font-size:20px;margin:0 0 16px}.field{font-weight:600;font-size:14px;display:block;margin-bottom:16px}.actions{display:flex;flex-wrap:wrap;gap:8px;margin-top:14px}.button{display:inline-flex;align-items:center;justify-content:center;padding:9px 13px;min-height:40px;border:1px solid transparent;border-radius:10px;cursor:pointer;text-decoration:none;font-weight:650;font-size:14px}.button.primary{background:var(--accent2);color:white}.button.secondary{border-color:var(--border);background:transparent;color:var(--text)}.button.danger{border-color:var(--bad);background:transparent;color:var(--bad)}.button:disabled{opacity:.45;cursor:default}.status{color:var(--muted);font-size:14px;margin-bottom:0;line-height:1.5}.status.error{color:var(--bad)}.small,.help{color:var(--muted);font-size:13px;line-height:1.5}.help{margin-top:18px}.form-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:15px}.notice{border-radius:14px;border:1px solid var(--border);padding:16px;line-height:1.5;font-size:14px}.notice.warning{border-color:#aa8153;background:color-mix(in srgb,var(--paper),#f3a31b 9%)}.check{display:flex;align-items:flex-start;gap:9px;font-size:14px;margin:15px 0;line-height:1.5}.check input{margin-top:4px;accent-color:var(--accent)}.app-list,.backup-list{display:grid;gap:12px}.app-row{padding:16px;border:1px solid var(--border);border-radius:12px}.app-row h3{margin:0 0 6px}.app-row p{margin:0}.pill{border-radius:20px;border:1px solid var(--border);font-size:12px;padding:5px 9px;color:var(--muted);white-space:nowrap}.pill.active{color:#177c5b}.pill.deleted{color:var(--bad)}.backup-row{display:flex;justify-content:space-between;align-items:center;gap:12px;border-bottom:1px solid var(--border);padding:12px 0;font-size:12px;word-break:break-word}code{font-size:12px;word-break:break-word}@media(max-width:600px){.top{padding:13px 0}.panel{padding:17px}.intro{padding:24px 0}.row-between{align-items:flex-start}.backup-row{flex-direction:column;align-items:flex-start}}\n", 'home.js': "'use strict';\nconst container=document.getElementById('apps');\nconst styles={yatzy:'🎲',flip7:'🃏'};\nfetch('/api/apps',{cache:'no-store'}).then(async r=>{if(!r.ok)throw Error('HTTP '+r.status);return r.json()}).then(data=>{\n container.replaceChildren();\n const apps=data.apps||[];\n if(!apps.length){let p=document.createElement('div');p.className='empty';p.textContent='Noch keine Frontends installiert. Importiere Yatzy oder Flip 7 über Verwaltung → Spiel-ZIP installieren.';container.append(p);return;}\n for(const app of apps){const a=document.createElement('a');a.className='game-tile';a.href=app.url;\n const icon=document.createElement('span');icon.className='game-icon';icon.textContent=styles[app.id]||'🎮';\n const label=document.createElement('div');const h=document.createElement('h2');h.textContent=app.name||app.id;\n const p=document.createElement('p');p.textContent='Spiel öffnen';label.append(h,p);\n const arrow=document.createElement('span');arrow.className='arrow';arrow.textContent='›';a.append(icon,label,arrow);container.append(a)}\n}).catch(error=>{container.textContent='Spiele konnten nicht geladen werden: '+error.message});\n", 'admin.js': "'use strict';\n// Admin token never enters localStorage, IndexedDB, cookies, URL or any backup.\n(()=>{\n let token='';const $=id=>document.getElementById(id);\n function setStatus(msg,isError=false){$('status').textContent=msg;$('status').className='status'+(isError?' error':'');}\n async function call(path,opts={}){\n  const headers={'X-Admin-Token':token,...(opts.body instanceof FormData?{}:{'Content-Type':'application/json'})};\n  const response=await fetch('/admin/api'+path,{...opts,headers,cache:'no-store',credentials:'omit'});\n  const ct=response.headers.get('content-type')||'';\n  const data=ct.includes('json')?await response.json():{};\n  if(!response.ok)throw Error(data.error||'HTTP '+response.status);\n  return data;\n }\n function button(label,callback,style='secondary'){\n  const b=document.createElement('button');b.type='button';b.className='button '+style;b.textContent=label;b.addEventListener('click',callback);return b;\n }\n async function guarded(operation){try{await operation()}catch(error){setStatus(error.message,true);alert(error.message)}}\n async function refresh(){\n  const result=await call('/apps');$('apps').replaceChildren();\n  if(!result.apps.length){const e=document.createElement('div');e.className='empty';e.textContent='Keine Spiele registriert.';$('apps').append(e)}\n  for(const app of result.apps){\n   const card=document.createElement('article');card.className='app-row';\n   const head=document.createElement('div');head.className='row-between';\n   const title=document.createElement('div');const h=document.createElement('h3');h.textContent=app.display_name||app.app_id;\n   const detail=document.createElement('p');detail.className='small';detail.textContent=app.app_id+' · '+(app.frontend_installed?'Frontend installiert':'Kein Frontend')+' · '+(app.has_data?'Spieldaten vorhanden':'Keine Spieldaten')+' · Revision '+app.revision;\n   const pill=document.createElement('span');pill.className='pill '+app.state;pill.textContent=app.state==='active'?'Aktiv':app.state==='disabled'?'Deaktiviert':'Gelöscht';\n   title.append(h,detail);head.append(title,pill);card.append(head);\n   const actions=document.createElement('div');actions.className='actions';\n   if(app.frontend_installed&&app.state==='active'){\n    const link=document.createElement('a');link.href='/apps/'+encodeURIComponent(app.app_id)+'/';link.className='button secondary';link.textContent='Öffnen';actions.append(link);\n   }\n   if(app.state!=='deleted'){\n    actions.append(button(app.state==='disabled'?'Aktivieren':'Deaktivieren',()=>guarded(async()=>{\n     const next=app.state==='disabled'?'active':'disabled';\n     if(!confirm('Spiel '+app.app_id+' '+(next==='active'?'aktivieren':'deaktivieren')+'? Alte PWAs erhalten bei Deaktivierung HTTP 410.'))return;\n     await call('/apps/'+app.app_id+'/toggle',{method:'POST',body:JSON.stringify({state:next})});await refresh();\n    })));\n   }\n   if(app.frontend_installed){actions.append(button('Frontend-ZIP sichern',()=>guarded(async()=>{await download('/apps/'+encodeURIComponent(app.app_id)+'/download',app.app_id+'-frontend.zip')}),'secondary'));actions.append(button('Nur Frontend entfernen',()=>guarded(async()=>{\n    const phrase='ENTFERNEN '+app.app_id;\n    if(prompt('Frontend entfernen und Spieldaten behalten? Das Spiel wird deaktiviert.\\nZum Bestätigen eingeben: '+phrase)!==phrase)return;\n    const r=await call('/apps/'+app.app_id+'/uninstall',{method:'POST',body:JSON.stringify({confirm:phrase})});\n    setStatus('Frontend entfernt. Vollständiges SQLite-Backup: '+r.backup);await refresh();await loadBackups();\n   }),'secondary'))}\n   if(app.has_data||app.frontend_installed||app.state==='disabled'){\n    actions.append(button('Spiel und Daten löschen',()=>guarded(async()=>{\n     const phrase='LÖSCHEN '+app.app_id;\n     if(prompt('ACHTUNG: App-Dokument und Frontend werden entfernt, API dauerhaft gesperrt. Vorher SQLite-Backup.\\nZum Bestätigen eingeben: '+phrase)!==phrase)return;\n     if(!confirm('Wirklich die Daten von '+app.app_id+' löschen? Andere Spiele und gemeinsame Profile bleiben erhalten.'))return;\n     const r=await call('/apps/'+app.app_id+'/delete',{method:'POST',body:JSON.stringify({confirm:phrase})});\n     setStatus('Spiel gelöscht und gesperrt. Backup: '+r.backup);await refresh();await loadBackups();\n    }),'danger'))\n   }\n   card.append(actions);$('apps').append(card);\n  }\n }\n async function download(path,name){\n  const response=await fetch('/admin/api'+path,{headers:{'X-Admin-Token':token},cache:'no-store',credentials:'omit'});\n  if(!response.ok)throw Error('Download: HTTP '+response.status);\n  const blob=await response.blob(),url=URL.createObjectURL(blob),a=document.createElement('a');\n  a.href=url;a.download=name;document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),30000);\n }\n async function loadBackups(){\n  const result=await call('/backups');$('backups').replaceChildren();\n  if(!result.backups.length){$('backups').textContent='Noch keine Backups.';return}\n  for(const item of result.backups){\n   const row=document.createElement('div');row.className='backup-row';\n   const label=document.createElement('span');label.textContent=item.name+' · '+(item.sizeBytes/1024).toFixed(1)+' KB';\n   row.append(label,button('Herunterladen',()=>guarded(async()=>{\n    await download(item.kind==='frontend'?'/frontends/backups/'+encodeURIComponent(item.name):'/backups/'+encodeURIComponent(item.name),item.name);\n   })));\n   $('backups').append(row);\n  }\n }\n $('connect').addEventListener('click',()=>guarded(async()=>{\n  token=$('adminToken').value.trim();$('adminToken').value='';\n  if(!token)throw Error('Administrator-Token fehlt');\n  await refresh();await loadBackups();$('refresh').disabled=false;$('newBackup').disabled=false;\n  setStatus('Angemeldet. Admin-Token ist nur in dieser geöffneten Seite im Arbeitsspeicher.');\n }));\n $('disconnect').addEventListener('click',()=>{token='';$('refresh').disabled=true;$('newBackup').disabled=true;setStatus('Getrennt. Browserseite neu laden, um die Sitzung zu verlassen.');$('apps').textContent='Nicht angemeldet.';$('backups').textContent='Nicht angemeldet.'});\n $('refresh').addEventListener('click',()=>guarded(async()=>{await refresh();await loadBackups();setStatus('Aktualisiert')}));\n $('newBackup').addEventListener('click',()=>guarded(async()=>{const r=await call('/backup',{method:'POST',body:'{}'});setStatus('SQLite-Sicherung: '+r.backup);await loadBackups()}));\n $('installForm').addEventListener('submit',e=>{e.preventDefault();guarded(async()=>{\n  if(!token)throw Error('Bitte zuerst Administrator-Token eingeben');\n  const appId=$('appId').value.trim(),file=$('zip').files[0];if(!file)throw Error('ZIP-Datei fehlt');\n  if(!confirm('Frontend-Paket '+file.name+' als '+appId+' installieren? Nur vertrauenswürdige Dateien verwenden.'))return;\n  const body=new FormData($('installForm'));\n  const r=await call('/apps/install',{method:'POST',body});\n  setStatus('Installiert: '+appId+' · DB-Backup '+r.backup+' · LAN-Anpassungen: '+(r.lan_adapter_changes||[]).join('; '));\n  await refresh();await loadBackups();\n })});\n})();\n"}
STATIC_TYPES={".css":"text/css; charset=utf-8",".js":"application/javascript; charset=utf-8",".html":"text/html; charset=utf-8"}

VERSION='v2.0.2'
AUTH=os.getenv('AUTH_ENABLED','true').lower() in ('true','1','yes')
APP_TOKEN=os.getenv('APP_API_TOKEN','')
ADMIN_TOKEN=os.getenv('ADMIN_TOKEN','')
ORIGINS={x.strip().rstrip('/') for x in os.getenv('PWA_ALLOWED_ORIGIN','').split(',') if x.strip()}
MAX_DOC=max(300000,min(25000000,int(os.getenv('MAX_APP_DOC_BYTES','3000000'))))
MAX_UPLOAD=min(60, max(5,int(os.getenv('MAX_FRONTEND_ZIP_MB','30'))))*1024*1024

if AUTH and (not APP_TOKEN or APP_TOKEN.startswith('HIER_') or APP_TOKEN.lower() in {'changeme','password','test'}):
    raise RuntimeError('APP_API_TOKEN fehlt oder ist noch ein Beispielwert; Server nicht gestartet.')
if not ADMIN_TOKEN or ADMIN_TOKEN.startswith('HIER_') or ADMIN_TOKEN.lower() in {'changeme','password','test'} or ADMIN_TOKEN == APP_TOKEN or len(ADMIN_TOKEN)<24:
    raise RuntimeError('ADMIN_TOKEN muss unabhängig und mindestens 24 Zeichen lang sein.')

app=Flask(__name__,static_folder=None)
app.config['MAX_CONTENT_LENGTH']=max(MAX_UPLOAD+4*1024*1024,MAX_DOC+1024*1024)
init()


def err(message,status=400,**extra):return jsonify(error=message,**extra),status

def token_ok(received,actual):return bool(actual) and hmac.compare_digest(received or '',actual)

def same_origin():
    origin=request.headers.get('Origin','')
    if not origin:return True # Non-browser clients must still supply the auth token
    u=urlsplit(origin)
    return u.scheme in ('http','https') and u.netloc.lower()==request.host.lower()

@app.before_request
def access():
    origin=request.headers.get('Origin','')
    if origin and not (same_origin() or origin.rstrip('/') in ORIGINS):
        return err('Origin not allowed',403)
    if request.method=='OPTIONS':return Response(status=204)
    if request.path.startswith('/admin/api/'):
        if not token_ok(request.headers.get('X-Admin-Token',''),ADMIN_TOKEN):return err('Admin-Token fehlt oder ungültig',401)
        if request.method not in ('GET','HEAD') and not same_origin():return err('Admin-Aktion nur von gleicher Origin',403)
    if request.path.startswith('/api/') and request.path not in ('/api/health','/api/config','/api/apps'):
        auth=request.headers.get('Authorization','')
        if AUTH and (not auth.startswith('Bearer ') or not token_ok(auth[7:],APP_TOKEN)):
            return err('Unauthorized',401)
    if request.path.startswith('/api/v1/apps/'):
        parts=request.path.split('/')
        if len(parts)>4:
            app_id=parts[4]
            if not VALID_ID.fullmatch(app_id):return err('Ungültige App-ID',404)
            with connect() as c:
                if not state_allowed(c,app_id):return err('Dieses Spiel ist gesperrt oder entfernt',410,app_id=app_id)

@app.after_request
def secure_headers(resp):
    origin=request.headers.get('Origin','')
    if origin and (same_origin() or origin.rstrip('/') in ORIGINS):
        resp.headers['Access-Control-Allow-Origin']=origin
        resp.headers['Vary']='Origin'
        resp.headers['Access-Control-Allow-Headers']='Content-Type,Authorization,CF-Access-Client-Id,CF-Access-Client-Secret'
        resp.headers['Access-Control-Allow-Methods']='GET,POST,PUT,PATCH,DELETE,OPTIONS'
        resp.headers['Access-Control-Max-Age']='600'
    resp.headers['X-Content-Type-Options']='nosniff'
    resp.headers['Referrer-Policy']='no-referrer'
    resp.headers['X-Frame-Options']='DENY'
    if request.path=='/' or request.path.startswith(('/admin','/static/')):
        resp.headers['Content-Security-Policy']="default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; object-src 'none'; base-uri 'self'; frame-ancestors 'none'; connect-src 'self'; form-action 'self'"
    resp.headers['Cache-Control']='no-store' if request.path.startswith(('/api/','/admin/')) else 'no-cache'
    return resp

@app.errorhandler(413)
def too_big(e):return err('Anfrage zu gross',413)

@app.errorhandler(404)
def page_not_found(e):return err('Nicht gefunden',404) if request.path.startswith(('/api/','/admin/api/')) else ('Nicht gefunden',404)

@app.get('/api/health')
@app.get('/health')
def health():return jsonify(status='ok',version=VERSION,schema=1)

@app.get('/api/config')
def config():return jsonify(version=VERSION,service='Spiele-Zentrale',auth_enabled=AUTH,storage='server',api='v1',maxAppDocumentBytes=MAX_DOC)

@app.get('/api/apps')
def installed_list():
    with connect() as c:
        rows=list_apps(c)
    return jsonify(apps=[{'id':r['app_id'],'name':r['display_name'],'url':'/apps/'+r['app_id']+'/',
                           'available':r['state']=='active' and bool(r['frontend_installed'])} for r in rows
                         if r['state']=='active' and r['frontend_installed']])

@app.route('/api/v1/apps/<app_id>/state',methods=['GET','PUT'])
def state(app_id):
    if not VALID_ID.fullmatch(app_id):return err('Ungültige App-ID',404)
    if request.method=='GET':
        with connect() as c:
            if not state_allowed(c,app_id):return err('App deaktiviert',410)
            doc=get_doc(c,app_id)
        return jsonify(**doc,app_id=app_id)
    if request.content_length and request.content_length>MAX_DOC+1024*1024: return err('App-Dokument zu gross',413)
    body=request.get_json(silent=True)
    if not isinstance(body,dict) or 'data' not in body or type(body.get('revision')) is not int or body['revision']<0:
        return err('Erwartet JSON: data und revision (nichtnegative Ganzzahl)')
    payload=json.dumps(body['data'],ensure_ascii=False,separators=(',',':')).encode('utf-8')
    if len(payload)>MAX_DOC:return err('App-Dokument zu gross',413,maxAppDocumentBytes=MAX_DOC)
    try:
        with transaction() as c:
            if not state_allowed(c,app_id):return err('App deaktiviert oder entfernt',410)
            rev=set_doc(c,app_id,body['data'],body['revision'])
            if rev is None:return err('Konflikt: neuere Serverrevision vorhanden',409)
            audit(c,'app_state_saved',app_id,str(rev))
        return jsonify(status='ok',revision=rev,updatedAt=utc())
    except sqlite3.IntegrityError:return err('Paralleländerung; bitte Serverstand neu laden',409)

@app.get('/api/v1/apps/<app_id>/snapshots')
def snapshots(app_id):
    if not VALID_ID.fullmatch(app_id):return err('Ungültige App-ID',404)
    with connect() as c:
        rows=c.execute('SELECT id,app_id,revision,created_at,reason FROM platform_app_backups WHERE app_id=? ORDER BY rowid DESC LIMIT 100',(app_id,)).fetchall()
    return jsonify(snapshots=[dict(r) for r in rows])

@app.post('/api/v1/apps/<app_id>/snapshots')
def create_snapshot(app_id):
    if not VALID_ID.fullmatch(app_id):return err('Ungültige App-ID',404)
    with transaction() as c:
        if not state_allowed(c,app_id):return err('App gesperrt',410)
        current=get_doc(c,app_id)
        if current['data'] is None:return err('Kein Spielstand vorhanden',404)
        sid=str(uuid.uuid4())
        c.execute('INSERT INTO platform_app_backups VALUES(?,?,?,?,?,?)',(
            sid,app_id,current['revision'],json.dumps(current['data'],ensure_ascii=False),utc(),'manual_snapshot'))
    return jsonify(status='ok',snapshot_id=sid,revision=current['revision']),201

@app.post('/api/v1/apps/<app_id>/restore')
def restore_snapshot(app_id):
    if not VALID_ID.fullmatch(app_id):return err('Ungültige App-ID',404)
    body=request.get_json(silent=True) or {}
    sid=str(body.get('snapshot_id',''))
    rev=body.get('revision')
    if not re.fullmatch(r'[a-f0-9-]{36}',sid) or type(rev) is not int or rev<0:
        return err('snapshot_id und aktuelle revision benötigt')
    with transaction() as c:
        if not state_allowed(c,app_id):return err('App gesperrt',410)
        row=c.execute('SELECT data_json FROM platform_app_backups WHERE id=? AND app_id=?',(sid,app_id)).fetchone()
        if not row:return err('Snapshot nicht gefunden',404)
        data=json.loads(row['data_json'])
        if len(row['data_json'].encode('utf-8'))>MAX_DOC:return err('Snapshot überschreitet aktuelles Datenlimit',413)
        next_rev=set_doc(c,app_id,data,rev)
        if next_rev is None:return err('Konflikt: neuere Serverrevision vorhanden',409)
        audit(c,'snapshot_restored',app_id,sid)
    return jsonify(status='ok',revision=next_rev)

# Generic players API (existing shared-profile tables remain untouched).
@app.get('/api/v1/players')
def players_list():
    with connect() as c:rows=all_players(c)
    return jsonify(players=rows,items=rows)

@app.post('/api/v1/players')
def players_add():
    import uuid
    body=request.get_json(silent=True) or {}
    name=str(body.get('name','')).strip()
    if not name or len(name)>80:return err('Name fehlt oder ist zu lang')
    identifier=body.get('id') or str(uuid.uuid4());identifier=str(identifier)
    if not re.fullmatch(r'[a-zA-Z0-9_-]{5,80}',identifier):return err('Ungültige Spieler-ID')
    with transaction() as c:
        try:add_player(c,identifier,name)
        except sqlite3.IntegrityError:return err('Spieler-ID existiert bereits',409)
    return jsonify(id=identifier,name=name),201

@app.get('/')
def home():return Response(STATIC_ASSETS['index.html'],mimetype='text/html')
@app.get('/admin/')
@app.get('/admin')
def admin_page():return Response(STATIC_ASSETS['admin.html'],mimetype='text/html')
@app.get('/static/<path:name>')
def static(name):
    if name not in {'site.css','home.js','admin.js'}:abort(404)
    suffix=Path(name).suffix
    return Response(STATIC_ASSETS[name],content_type=STATIC_TYPES.get(suffix,'text/plain; charset=utf-8'))
@app.get('/apps/<app_id>')
def game_redirect(app_id):return redirect('/apps/'+app_id+'/',code=308)
@app.get('/apps/<app_id>/')
@app.get('/apps/<app_id>/<path:filepath>')
def serve_game(app_id,filepath='index.html'):
    if not VALID_ID.fullmatch(app_id):abort(404)
    with connect() as c:
        r=registry(c,app_id)
        if r and r['state']!='active':return 'Spiel ist deaktiviert oder entfernt',410
    folder=app_folder(app_id)
    if not folder.exists() or not (folder/'index.html').is_file():abort(404)
    if filepath.startswith('.') or '/.' in filepath or '\\' in filepath:abort(404)
    response=send_from_directory(folder,filepath)
    if filepath.endswith(('.html','.webmanifest','.js','.css','.json')):
        response.headers['Cache-Control']='no-cache'
    return response


def admin_app_info(c,app_id):
    infos=list_apps(c)
    return next((r for r in infos if r['app_id']==app_id),None)

@app.get('/admin/api/apps')
def admin_apps():
    with connect() as c:apps=list_apps(c)
    for item in apps:item['installed_info']=manifest(item['app_id'])
    return jsonify(apps=apps,dbFile=db_path().name,maxZipMb=MAX_UPLOAD//1024//1024)

@app.post('/admin/api/apps/install')
def admin_install():
    app_id=str(request.form.get('app_id','')).strip()
    name=str(request.form.get('display_name','')).strip() or app_id
    if not VALID_ID.fullmatch(app_id):return err('Spielkennung: 2–40 Zeichen, Kleinbuchstaben/Ziffern/Bindestrich/_')
    if len(name)>80:return err('Spielname zu lang')
    source=request.files.get('zip')
    if not source:return err('ZIP-Datei fehlt')
    if not source.filename.lower().endswith('.zip'):return err('Nur ZIP-Dateien')
    # refuse replacing a previously deleted app unless explicit reactivation is chosen
    with connect() as c:
        current=registry(c,app_id)
    if current and current['state']=='deleted' and request.form.get('reactivate')!='yes':
        return err('App wurde endgültig gelöscht: bewusste Reaktivierung erforderlich',409)
    if current and current['frontend_installed'] and request.form.get('overwrite')!='yes':
        return err('Frontend vorhanden: Update ausdrücklich bestätigen',409)
    with tempfile.TemporaryDirectory(prefix='upload-') as tmp:
        path=Path(tmp)/'upload.zip'
        source.save(path)
        if path.stat().st_size>MAX_UPLOAD:return err('ZIP überschreitet Upload-Limit',413)
        try:validated_files(path)
        except (ValueError,zipfile.BadZipFile,RuntimeError,OSError,UnicodeDecodeError) as exc:
            return err('ZIP nicht installiert: '+str(exc),400)
        backup=full_backup('before-install-'+app_id)
        frontend_backup=backup_frontend(app_id,'before-update')
        try:info=install(app_id,path,patch_lan=request.form.get('lan_patch')=='yes')
        except (ValueError,zipfile.BadZipFile,RuntimeError,OSError,UnicodeDecodeError) as exc:
            return err('ZIP nicht installiert: '+str(exc),400)
        with transaction() as c:
            set_registry(c,app_id,name=name,frontend=True,state='active')
            audit(c,'frontend_installed',app_id,'DB backup '+backup.name)
        return jsonify(status='ok',backup=backup.name,frontend_backup=frontend_backup,**info)

@app.get('/admin/api/apps/<app_id>/download')
def admin_download_frontend(app_id):
    if not VALID_ID.fullmatch(app_id):return err('Ungültige ID',404)
    archive=backup_frontend(app_id,'download')
    if not archive:return err('Kein Frontend installiert',404)
    folder=Path(os.getenv('DATA_DIR','/app/data')).resolve()/'backups'/'frontends'
    return send_from_directory(folder,archive,as_attachment=True,mimetype='application/zip')

@app.post('/admin/api/apps/<app_id>/toggle')
def admin_toggle(app_id):
    if not VALID_ID.fullmatch(app_id):return err('Ungültige ID',404)
    req=request.get_json(silent=True) or {}
    state=req.get('state')
    if state not in ('active','disabled'):return err('Nur active/disabled zulässig')
    with transaction() as c:
        current=registry(c,app_id)
        if current and current['state']=='deleted':return err('Gelöschte App muss neu installiert werden',409)
        set_registry(c,app_id,state=state)
        audit(c,'app_'+state,app_id)
    return jsonify(status='ok',state=state)

@app.post('/admin/api/apps/<app_id>/uninstall')
def admin_uninstall(app_id):
    if not VALID_ID.fullmatch(app_id):return err('Ungültige ID',404)
    body=request.get_json(silent=True) or {}
    if body.get('confirm') != 'ENTFERNEN '+app_id:return err('Bestätigung muss ENTFERNEN '+app_id+' lauten')
    backup=full_backup('before-uninstall-'+app_id)
    frontend_backup=backup_frontend(app_id)
    with transaction() as c:
        set_registry(c,app_id,frontend=False,state='disabled')
        audit(c,'frontend_removed',app_id,backup.name)
    remove_frontend(app_id)
    return jsonify(status='ok',state='disabled',data_kept=True,backup=backup.name,frontend_backup=frontend_backup)

@app.post('/admin/api/apps/<app_id>/delete')
def admin_delete(app_id):
    if not VALID_ID.fullmatch(app_id):return err('Ungültige ID',404)
    body=request.get_json(silent=True) or {}
    if body.get('confirm')!='LÖSCHEN '+app_id:
        return err('Bestätigung muss LÖSCHEN '+app_id+' lauten')
    backup=full_backup('before-delete-'+app_id)
    frontend_backup=backup_frontend(app_id,'before-delete')
    with transaction() as c:
        original=remove_doc(c,app_id)
        c.execute('DELETE FROM platform_app_backups WHERE app_id=?',(app_id,))  # Archive only in external SQLite backup
        set_registry(c,app_id,frontend=False,state='deleted')
        audit(c,'app_deleted',app_id,backup.name)
    remove_frontend(app_id)
    return jsonify(status='ok',deleted=True,backup=backup.name,frontend_backup=frontend_backup,previousRevision=original['revision'])

@app.post('/admin/api/backup')
def admin_backup():
    backup=full_backup('manual')
    with transaction() as c:audit(c,'backup_created',detail=backup.name)
    return jsonify(status='ok',backup=backup.name)

@app.get('/admin/api/backups')
def admin_backups():
    folder=Path(os.getenv('DATA_DIR','/app/data')).resolve()/'backups'
    items=sorted(folder.glob('*.sqlite'),reverse=True)[:100] if folder.exists() else []
    fronts=folder/'frontends'
    front_items=sorted(fronts.glob('*.zip'),reverse=True)[:100] if fronts.exists() else []
    return jsonify(backups=[{'name':f.name,'sizeBytes':f.stat().st_size,'createdAt':datetime.fromtimestamp(f.stat().st_mtime,timezone.utc).isoformat(),'kind':'sqlite'} for f in items]+
                   [{'name':f.name,'sizeBytes':f.stat().st_size,'createdAt':datetime.fromtimestamp(f.stat().st_mtime,timezone.utc).isoformat(),'kind':'frontend'} for f in front_items])

@app.get('/admin/api/frontends/backups/<name>')
def admin_download_frontend_backup(name):
    if not re.fullmatch(r'[A-Za-z0-9_.-]{1,170}\.zip',name):abort(404)
    folder=Path(os.getenv('DATA_DIR','/app/data')).resolve()/'backups'/'frontends'
    if not (folder/name).is_file():abort(404)
    return send_from_directory(folder,name,as_attachment=True,mimetype='application/zip')

@app.get('/admin/api/backups/<name>')
def admin_download(name):
    if not re.fullmatch(r'[a-zA-Z0-9_.-]{1,150}\.sqlite',name):abort(404)
    folder=Path(os.getenv('DATA_DIR','/app/data')).resolve()/'backups'
    if not (folder/name).is_file():abort(404)
    return send_from_directory(folder,name,as_attachment=True,mimetype='application/vnd.sqlite3')

if __name__=='__main__':app.run(host='0.0.0.0',port=8080,debug=False)
