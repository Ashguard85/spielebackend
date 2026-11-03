"""Spiele-Zentrale v1: generic versioned JSON documents; no game rules on server."""
import hmac, json, os, re, sqlite3, sys, threading, tempfile, zipfile, io
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit
from flask import Flask, request, jsonify, Response

VERSION='v1.1'; SCHEMA=1
ROOT=Path(os.getenv('DATA_DIR','/app/data')); DB=ROOT/'spiele.sqlite'; BACKUPS=ROOT/'backups'
ORIGINS={s.strip().rstrip('/') for s in os.getenv('PWA_ALLOWED_ORIGIN','').split(',') if s.strip()}
API_TOKEN=os.getenv('APP_API_TOKEN',''); AUTH=os.getenv('AUTH_ENABLED','true').lower() in ('true','1','yes')
KEEP=max(1,min(500,int(os.getenv('BACKUP_KEEP','50'))))
LIMIT=max(300_000,min(25_000_000,int(os.getenv('MAX_APP_DOC_BYTES','3000000'))))
lock=threading.RLock(); app=Flask(__name__);app.json.ensure_ascii=False;app.config['MAX_CONTENT_LENGTH']=LIMIT+100_000

def now():return datetime.now(timezone.utc).isoformat()
def db():
 c=sqlite3.connect(DB,timeout=20,isolation_level=None);c.row_factory=sqlite3.Row
 c.execute('PRAGMA foreign_keys=ON');c.execute('PRAGMA journal_mode=WAL');c.execute('PRAGMA synchronous=FULL');c.execute('PRAGMA busy_timeout=20000');return c

def init():
 try:
  ROOT.mkdir(parents=True,exist_ok=True);BACKUPS.mkdir(parents=True,exist_ok=True)
  with tempfile.TemporaryFile(dir=ROOT) as f:f.write(b'ok')
  c=db()
  try:
   c.execute('BEGIN IMMEDIATE')
   c.execute('CREATE TABLE IF NOT EXISTS schema_migrations(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)')
   versions=[r[0] for r in c.execute('SELECT version FROM schema_migrations')]
   if any(v>SCHEMA for v in versions):raise RuntimeError('Database schema unsupported')
   c.execute('CREATE TABLE IF NOT EXISTS app_documents(app_id TEXT PRIMARY KEY, revision INTEGER NOT NULL, payload TEXT NOT NULL, updated_at TEXT NOT NULL)')
   c.execute('CREATE TABLE IF NOT EXISTS players(id TEXT PRIMARY KEY, name TEXT NOT NULL, updated_at TEXT NOT NULL)')
   c.execute('CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL)')
   c.execute('INSERT OR IGNORE INTO schema_migrations VALUES(?,?)',(1,now()))
   c.execute('COMMIT')
  except:
   if c.in_transaction:c.execute('ROLLBACK')
   raise
  finally:c.close()
  if AUTH and len(API_TOKEN)<32:raise RuntimeError('AUTH_ENABLED=true requires APP_API_TOKEN (min. 32 chars)')
 except Exception as e:
  print('FATAL: Database migration failed or cannot write to /app/data: '+str(e),file=sys.stderr,flush=True);raise

def error(msg,status=400):return jsonify({'error':msg}),status

def valid_app(s):return isinstance(s,str) and bool(re.fullmatch('[a-z][a-z0-9_-]{1,39}',s))
def valid_id(s):return isinstance(s,str) and bool(re.fullmatch('[A-Za-z0-9_-]{8,80}',s))
def validate_doc(payload):
 if not isinstance(payload,dict):raise ValueError('Dokument muss ein JSON-Objekt sein')
 blob=json.dumps(payload,ensure_ascii=False,allow_nan=False,separators=(',',':'))
 if len(blob.encode())>LIMIT:raise ValueError('Dokument zu gross')
 return blob

def snapshot(c):
 return {'format':'spiele-zentrale-backup','version':1,'createdAt':now(),
         'apps':[{'id':r['app_id'],'revision':r['revision'],'data':json.loads(r['payload'])} for r in c.execute('SELECT * FROM app_documents ORDER BY app_id')],
         'players':[dict(r) for r in c.execute('SELECT * FROM players ORDER BY id')]}

def backup(c,tag):
 data=snapshot(c)
 name=f'{tag}-{datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")}.json'
 with tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',dir=BACKUPS,delete=False) as t:
  json.dump(data,t,ensure_ascii=False);t.flush();os.fsync(t.fileno());tmp=t.name
 os.replace(tmp,BACKUPS/name)
 for p in sorted(BACKUPS.glob('*.json'),key=lambda x:x.stat().st_mtime,reverse=True)[KEEP:]:p.unlink()
 return name

@app.before_request
def auth_guard():
 origin=request.headers.get('Origin')
 if origin:
  parsed=urlsplit(origin);same=parsed.scheme in ('http','https') and parsed.netloc.lower()==request.host.lower()
  if not same and origin not in ORIGINS:return error('Origin not allowed',403)
 if request.method=='OPTIONS':return Response(status=204)
 if AUTH and request.path.startswith('/api/') and request.path not in ('/api/health','/api/config'):
  if not hmac.compare_digest(request.headers.get('Authorization',''),'Bearer '+API_TOKEN):return error('Unauthorized',401)

@app.after_request
def secure_headers(resp):
 origin=request.headers.get('Origin')
 if origin in ORIGINS:
  resp.headers['Access-Control-Allow-Origin']=origin;resp.headers['Vary']='Origin'
  resp.headers['Access-Control-Allow-Headers']='Content-Type,Authorization,CF-Access-Client-Id,CF-Access-Client-Secret'
  resp.headers['Access-Control-Allow-Methods']='GET,POST,PUT,DELETE,OPTIONS';resp.headers['Access-Control-Max-Age']='600'
 resp.headers['X-Content-Type-Options']='nosniff';resp.headers['Referrer-Policy']='no-referrer'
 resp.headers['Cache-Control']='no-store';resp.headers['X-Frame-Options']='DENY'
 return resp

@app.errorhandler(ValueError)
def invalid(e):return error(str(e))
@app.errorhandler(413)
def oversize(e):return error('Anfrage zu gross',413)
@app.get('/health')
@app.get('/api/health')
def health():return jsonify(status='ok',version=VERSION,schema=SCHEMA)
@app.get('/api/config')
def config():return jsonify(version=VERSION,service='Spiele-Zentrale',auth_enabled=AUTH,storage='server',api='v1',maxAppDocumentBytes=LIMIT)
@app.get('/')
def home():return jsonify(service='Spiele-Zentrale',version=VERSION,docs='/api/config')

@app.get('/api/v1/apps')
def apps():
 c=db()
 try:return jsonify(items=[{'id':r['app_id'],'revision':r['revision'],'updatedAt':r['updated_at']} for r in c.execute('SELECT app_id,revision,updated_at FROM app_documents ORDER BY app_id')])
 finally:c.close()

@app.get('/api/v1/apps/<app_id>/state')
def get_state(app_id):
 if not valid_app(app_id):return error('Ungültige Spielkennung')
 c=db()
 try:
  r=c.execute('SELECT revision,payload,updated_at FROM app_documents WHERE app_id=?',(app_id,)).fetchone()
  return jsonify({'revision':r['revision'],'data':json.loads(r['payload']),'updatedAt':r['updated_at']}) if r else jsonify({'revision':0,'data':None,'updatedAt':None})
 finally:c.close()

@app.put('/api/v1/apps/<app_id>/state')
def put_state(app_id):
 if not valid_app(app_id):return error('Ungültige Spielkennung')
 body=request.get_json(silent=True)
 if not isinstance(body,dict) or type(body.get('revision'))!=int or body['revision']<0:return error('Revision fehlt')
 blob=validate_doc(body.get('data'))
 with lock:
  c=db()
  try:
   c.execute('BEGIN IMMEDIATE')
   r=c.execute('SELECT revision FROM app_documents WHERE app_id=?',(app_id,)).fetchone()
   current=r[0] if r else 0
   if body['revision']!=current:
    c.execute('ROLLBACK');return error('Revision conflict',409)
   backup(c,'before-write')
   new=current+1
   c.execute('INSERT INTO app_documents(app_id,revision,payload,updated_at) VALUES(?,?,?,?) ON CONFLICT(app_id) DO UPDATE SET revision=excluded.revision,payload=excluded.payload,updated_at=excluded.updated_at',(app_id,new,blob,now()))
   c.execute('COMMIT');return jsonify(revision=new)
  except:
   if c.in_transaction:c.execute('ROLLBACK')
   raise
  finally:c.close()

@app.get('/api/v1/players')
def players():
 c=db()
 try:return jsonify(items=[dict(r) for r in c.execute('SELECT * FROM players ORDER BY name')])
 finally:c.close()
@app.put('/api/v1/players/<player_id>')
def put_player(player_id):
 if not valid_id(player_id):return error('Ungültige Spieler-ID')
 obj=request.get_json(silent=True)
 if not isinstance(obj,dict) or not isinstance(obj.get('name'),str) or not 1<=len(obj['name'].strip())<=60:return error('Ungültiger Name')
 with lock:
  c=db()
  try:
   c.execute('BEGIN IMMEDIATE');backup(c,'before-player')
   c.execute('INSERT INTO players(id,name,updated_at) VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name,updated_at=excluded.updated_at',(player_id,obj['name'].strip(),now()))
   c.execute('COMMIT');return jsonify(id=player_id,name=obj['name'].strip())
  except:
   if c.in_transaction:c.execute('ROLLBACK')
   raise
  finally:c.close()

@app.get('/api/v1/export')
def export():
 c=db()
 try:data=snapshot(c)
 finally:c.close()
 return Response(json.dumps(data,ensure_ascii=False,indent=2),mimetype='application/json',headers={'Content-Disposition':'attachment; filename="spiele-backup.json"'})

@app.post('/api/v1/restore/preview')
def preview():
 body=request.get_json(silent=True)
 apps,players=validate_backup(body)
 c=db()
 try:existing={r[0] for r in c.execute('SELECT app_id FROM app_documents')}
 finally:c.close()
 return jsonify(apps=len(apps),players=len(players),conflicts=sorted(existing.intersection(x['id'] for x in apps)),mode='replace',message='Restore ersetzt sämtliche Serverdaten; Sicherheitsbackup wird automatisch erstellt')

def validate_backup(body):
 if not isinstance(body,dict) or body.get('format')!='spiele-zentrale-backup' or body.get('version')!=1:raise ValueError('Unbekanntes Backupformat')
 apps=body.get('apps');players=body.get('players')
 if not isinstance(apps,list) or not isinstance(players,list) or len(apps)>100 or len(players)>10000:raise ValueError('Ungültiger Backup-Inhalt')
 seen=set();ids=set()
 for a in apps:
  if not isinstance(a,dict) or not valid_app(a.get('id')) or a['id'] in seen or type(a.get('revision'))!=int or a['revision']<0:raise ValueError('Ungültige Apps')
  validate_doc(a.get('data'));seen.add(a['id'])
 for p in players:
  if not isinstance(p,dict) or not valid_id(p.get('id')) or p['id'] in ids or not isinstance(p.get('name'),str) or not 1<=len(p['name'].strip())<=60:raise ValueError('Ungültige Spieler')
  ids.add(p['id'])
 return apps,players

@app.post('/api/v1/restore')
def restore():
 body=request.get_json(silent=True)
 if not isinstance(body,dict) or body.get('confirm')!='REPLACE_ALL':return error('Bestätigung REPLACE_ALL fehlt')
 apps,players=validate_backup(body.get('backup'))
 with lock:
  c=db()
  try:
   c.execute('BEGIN IMMEDIATE');backup(c,'before-restore')
   c.execute('DELETE FROM app_documents');c.execute('DELETE FROM players')
   for a in apps:c.execute('INSERT INTO app_documents VALUES(?,?,?,?)',(a['id'],a['revision'],validate_doc(a['data']),now()))
   for p in players:c.execute('INSERT INTO players VALUES(?,?,?)',(p['id'],p['name'].strip(),now()))
   c.execute('COMMIT');return jsonify(status='ok',apps=len(apps),players=len(players))
  except:
   if c.in_transaction:c.execute('ROLLBACK')
   raise
  finally:c.close()

init()
