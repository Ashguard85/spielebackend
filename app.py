"""Spiele-Zentrale v2: generic SQLite game API + local static game hosting.

Important: app data remains keyed under its old app_id. ZIPs only contain static sites.
"""
from __future__ import annotations
import os, json, re, hmac, tempfile, zipfile, sqlite3, traceback, uuid
from pathlib import Path
from urllib.parse import urlsplit
from datetime import datetime, timezone

from flask import Flask, request, jsonify, Response, redirect, send_file, send_from_directory, abort
from games_platform.store import (VALID_ID, init, connect, transaction, get_doc, set_doc, remove_doc,
                            registry, state_allowed, list_apps, set_registry, audit, full_backup,
                            db_path, all_docs, utc, all_players, add_player)
from games_platform.packages import app_folder, install, remove_frontend, manifest, apps_root, backup_frontend, validated_files

VERSION='v2.0.0'
AUTH=os.getenv('AUTH_ENABLED','true').lower() in ('true','1','yes')
APP_TOKEN=os.getenv('APP_API_TOKEN','')
ADMIN_TOKEN=os.getenv('ADMIN_TOKEN','')
ORIGINS={x.strip().rstrip('/') for x in os.getenv('PWA_ALLOWED_ORIGIN','').split(',') if x.strip()}
MAX_DOC=max(300000,min(25000000,int(os.getenv('MAX_APP_DOC_BYTES','3000000'))))
MAX_UPLOAD=min(60, max(5,int(os.getenv('MAX_FRONTEND_ZIP_MB','30'))))*1024*1024
BASE=Path(__file__).resolve().parent

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
def home():return send_from_directory(BASE/'static','index.html')
@app.get('/admin/')
@app.get('/admin')
def admin_page():return send_from_directory(BASE/'static','admin.html')
@app.get('/static/<path:name>')
def static(name):
    if name not in {'site.css','home.js','admin.js'}:abort(404)
    return send_from_directory(BASE/'static',name)
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
