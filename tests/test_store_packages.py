import sys,os,unittest,tempfile,json,zipfile,io,sqlite3,stat
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from games_platform import store,packages


def create_zip(out,names):
 with zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as z:
  for name,value in names.items():z.writestr(name,value)

class StoreTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();os.environ['DATA_DIR']=self.tmp.name
  os.environ.pop('DB_PATH',None);os.environ.pop('REQUIRE_EXISTING_DB',None)
  store.DB_FILE=None
 def tearDown(self):self.tmp.cleanup();store.DB_FILE=None
 def test_state_revision_and_tombstone(self):
  store.init()
  with store.connect() as c:
   self.assertEqual(store.get_doc(c,'yatzy')['revision'],0)
  with store.transaction() as c:
   self.assertEqual(store.set_doc(c,'yatzy',{'history':[1]},0),1)
  with store.transaction() as c:
   self.assertIsNone(store.set_doc(c,'yatzy',{'history':[2]},0))
   self.assertEqual(store.set_doc(c,'yatzy',{'history':[1,2]},1),2)
   store.set_doc(c,'flip7',{'games':[]},0)
  with store.connect() as c:
   self.assertEqual(store.get_doc(c,'yatzy')['data']['history'],[1,2])
   self.assertEqual(store.get_doc(c,'flip7')['revision'],1)
  backup=store.full_backup('before-delete');self.assertTrue(backup.is_file())
  with store.transaction() as c:
   store.remove_doc(c,'yatzy');c.execute("DELETE FROM platform_app_backups WHERE app_id='yatzy'")
   store.set_registry(c,'yatzy',state='deleted',frontend=False)
  with store.connect() as c:
   self.assertEqual(store.get_doc(c,'yatzy')['revision'],0)
   self.assertFalse(store.state_allowed(c,'yatzy'))
   self.assertEqual(store.get_doc(c,'flip7')['data'],{'games':[]})
  with sqlite3.connect(backup) as c:
   self.assertEqual(json.loads(c.execute("SELECT data_json FROM app_documents WHERE app_id='yatzy'").fetchone()[0])['history'],[1,2])
 def test_detect_existing_legacy_schema_and_preserve(self):
  old=Path(self.tmp.name)/'games.sqlite3'
  with sqlite3.connect(old) as c:
   c.execute('CREATE TABLE app_documents (app_id TEXT PRIMARY KEY,revision INTEGER NOT NULL,data TEXT NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL)')
   c.execute("INSERT INTO app_documents VALUES ('yatzy',42,'{\"old\":true}','2024-01-01','2024-01-01')")
   c.execute("INSERT INTO app_documents VALUES ('flip7',12,'{\"score\":23}','2024-01-01','2024-01-01')")
   c.execute('CREATE TABLE players (id TEXT PRIMARY KEY, name TEXT NOT NULL)')
   c.execute("INSERT INTO players VALUES ('old01','Original-Spielerin')")
  store.init();self.assertEqual(store.db_path(),old)
  with store.connect() as c:
   self.assertEqual(store.all_players(c)[0]['name'],'Original-Spielerin')
  with store.transaction() as c:store.add_player(c,'new002','Neue Person')
  with sqlite3.connect(old) as c:
   self.assertEqual(c.execute("SELECT name FROM players WHERE id='new002'").fetchone()[0],'Neue Person')
  with store.connect() as c:
   self.assertEqual(store.get_doc(c,'yatzy')['revision'],42)
   self.assertEqual(store.set_doc(c,'yatzy',{},3),None)
  with store.transaction() as c:
   self.assertEqual(store.set_doc(c,'yatzy',{'old':True,'new':True},42),43)
   store.set_doc(c,'kniffel',{'new_game':True},0)
  with store.connect() as c:
   self.assertEqual(store.get_doc(c,'flip7')['data'],{'score':23})
   self.assertEqual(store.get_doc(c,'yatzy')['revision'],43)
 def test_upgrade_must_not_start_blank_db(self):
  os.environ['REQUIRE_EXISTING_DB']='1'
  with self.assertRaises(RuntimeError):store.init()
  self.assertFalse((Path(self.tmp.name)/'spiele.sqlite').exists())
 def test_schema_incompatibility_fails_closed(self):
  p=Path(self.tmp.name)/'custom.db'
  with sqlite3.connect(p) as c:
   c.execute('CREATE TABLE app_documents(app_id TEXT PRIMARY KEY, revision INT, ciphertext BLOB NOT NULL)')
  with self.assertRaisesRegex(RuntimeError,'Schema'):store.init()

class ZipTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();os.environ['DATA_DIR']=self.tmp.name
 def tearDown(self):self.tmp.cleanup()
 def test_install_update_and_reject_paths(self):
  p=Path(self.tmp.name)/'valid.zip';create_zip(p,{'index.html':'<!doctype html><html><body>Yatzy</body></html>','app.js':'console.log(1)','icons/favicon.png':b'png'})
  r=packages.install('yatzy',p);self.assertEqual(r['file_count'],3)
  self.assertTrue((packages.app_folder('yatzy')/'index.html').exists())
  create_zip(p,{'index.html':'<html>Updated</html>','app.js':'console.log(2)'})
  packages.install('yatzy',p)
  self.assertIn('Updated',(packages.app_folder('yatzy')/'index.html').read_text())
  for bad in ('../evil.html','/evil.html','.github/workflow.yml','dir\\evil.js'):
   create_zip(p,{'index.html':'<html>ok</html>',bad:'boom'})
   with self.assertRaises(ValueError,msg=bad):packages.validated_files(p)
  create_zip(p,{'folder/index.html':'<html>No root</html>'})
  with self.assertRaisesRegex(ValueError,'index.html'):packages.validated_files(p)
  self.assertIn('Updated',(packages.app_folder('yatzy')/'index.html').read_text())
 def test_symlink_zip_refused(self):
  p=Path(self.tmp.name)/'bad.zip'
  with zipfile.ZipFile(p,'w') as z:
   z.writestr('index.html','<html>ok</html>')
   info=zipfile.ZipInfo('link.js');info.create_system=3;info.external_attr=(stat.S_IFLNK|0o777)<<16
   z.writestr(info,'/etc/passwd')
  with self.assertRaises(ValueError):packages.validated_files(p)
 def test_service_worker_changes_per_install(self):
  p=Path(self.tmp.name)/'app.zip'
  create_zip(p,{'index.html':'<html>One</html>','sw.js':'const VERSION=1;'})
  packages.install('flip7',p)
  one=(packages.app_folder('flip7')/'sw.js').read_text()
  create_zip(p,{'index.html':'<html>Two</html>','sw.js':'const VERSION=1;'})
  packages.install('flip7',p)
  two=(packages.app_folder('flip7')/'sw.js').read_text()
  self.assertNotEqual(one,two)
  self.assertIn('spiele-zentrale-install:',two)
  archive=packages.backup_frontend('flip7')
  self.assertTrue((Path(self.tmp.name)/'backups'/'frontends'/archive).is_file())
 def test_lan_patch_known_versions(self):
  files={'app.js':b"const url=String($('centralUrl').value||deviceSettings.centralUrl||'').trim().replace(/\\/+$/,'');"}
  patched=packages.lan_adapter(files,'yatzy')
  self.assertEqual(len(patched),1);self.assertIn(b'location.origin',files['app.js'])

if __name__=='__main__':unittest.main()
