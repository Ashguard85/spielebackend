"""Validate frontend ZIPs and atomically deploy static files under DATA_DIR/apps."""
from __future__ import annotations
import os, zipfile, stat, re, shutil, tempfile, hashlib, json
from pathlib import Path, PurePosixPath
from .store import VALID_ID, utc

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
