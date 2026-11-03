"""SQLite adapter: backwards-friendly with existing app_documents schemas.

Never drops or rewrites existing application tables. Fails closed on unknown columns.
"""
from __future__ import annotations
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
