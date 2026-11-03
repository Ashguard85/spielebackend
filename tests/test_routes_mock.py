"""Offline route logic tests with a minimal Flask stub.
Actual Flask/Gunicorn integration still requires building the Docker image.
"""
import sys,os,tempfile,unittest,types,importlib,json,zipfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from games_platform import store

class FakeRequest:
    def __init__(self):
        self.path='/api/health';self.method='GET';self.headers={};self.content_length=0
        self._json=None;self.form={};self.files={};self.host='127.0.0.1:8096'
    def get_json(self,silent=True):return self._json
class FakeResult(dict):
    def __init__(self,*a,**k):super().__init__(*a,**k);self.headers={}

class FakeApp:
    def __init__(self,*a,**k):self.config={}
    def get(self,*a,**k):return lambda f:f
    def post(self,*a,**k):return lambda f:f
    def route(self,*a,**k):return lambda f:f
    def before_request(self,f):return f
    def after_request(self,f):return f
    def errorhandler(self,*a,**k):return lambda f:f
    def run(self,*a,**k):pass

class RouteTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
    cls.tmp=tempfile.TemporaryDirectory()
    os.environ['DATA_DIR']=cls.tmp.name
    os.environ['REQUIRE_EXISTING_DB']='0'
    os.environ['APP_API_TOKEN']='test-api-token-not-secret-32'
    os.environ['ADMIN_TOKEN']='test-admin-token-not-secret-64-characters'
    store.DB_FILE=None
    stub=types.ModuleType('flask')
    stub.request=FakeRequest();stub.Flask=FakeApp
    stub.jsonify=lambda *a,**kw: a[0] if a else kw
    stub.Response=lambda *a,**kw:{'status':kw.get('status',200)}
    stub.redirect=lambda *a,**kw:{'redirect':a[0]}
    stub.send_file=lambda *a,**kw:{'file':a[0]}
    stub.send_from_directory=lambda folder,file,**k:FakeResult({'file':str(Path(folder)/file),'download':k.get('as_attachment',False)})
    stub.abort=lambda code:(_ for _ in ()).throw(Exception('abort '+str(code)))
    cls.old_flask=sys.modules.get('flask')
    sys.modules['flask']=stub
    spec=importlib.util.spec_from_file_location('_test_spiele_app',ROOT/'app.py')
    cls.mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(cls.mod)
    cls.req=stub.request
 @classmethod
 def tearDownClass(cls):
    if cls.old_flask:sys.modules['flask']=cls.old_flask
    else:sys.modules.pop('flask',None)
    cls.tmp.cleanup();store.DB_FILE=None
 def setUp(self):
    # Each test starts with an independent logical state.
    with store.transaction() as c:
        c.execute('DELETE FROM app_documents')
        c.execute('DELETE FROM platform_registry')
        c.execute('DELETE FROM platform_app_backups')
 def configure(self,path,method='GET',token=None,admin=None,body=None,origin=None):
    self.req.path=path;self.req.method=method;self.req._json=body;self.req.content_length=0
    self.req.headers={}
    if token:self.req.headers['Authorization']='Bearer '+token
    if admin:self.req.headers['X-Admin-Token']=admin
    if origin:self.req.headers['Origin']=origin
    self.req.host='127.0.0.1:8096'
 def test_authorization_revision_delete_and_tombstone(self):
    m=self.mod
    self.configure('/api/v1/apps/yatzy/state')
    denied=m.access();self.assertEqual(denied[1],401)
    self.configure('/api/v1/apps/yatzy/state','PUT',token='test-api-token-not-secret-32',body={'revision':0,'data':{'history':[1,2,3]}})
    self.assertIsNone(m.access())
    saved=m.state('yatzy');self.assertEqual(saved['revision'],1)
    self.configure('/api/v1/apps/yatzy/state','PUT',token='test-api-token-not-secret-32',body={'revision':0,'data':{'history':[]}})
    self.assertEqual(m.state('yatzy')[1],409)
    self.configure('/api/v1/apps/yatzy/state','GET',token='test-api-token-not-secret-32')
    self.assertEqual(m.state('yatzy')['data']['history'],[1,2,3])
    self.configure('/admin/api/apps/yatzy/delete','POST',admin='incorrect',body={'confirm':'LÖSCHEN yatzy'})
    self.assertEqual(m.access()[1],401)
    self.configure('/admin/api/apps/yatzy/delete','POST',admin='test-admin-token-not-secret-64-characters',body={'confirm':'LÖSCHEN yatzy'})
    self.assertIsNone(m.access())
    r=m.admin_delete('yatzy');self.assertEqual(r['deleted'],True)
    self.configure('/api/v1/apps/yatzy/state','PUT',token='test-api-token-not-secret-32',body={'revision':0,'data':{'new':True}})
    self.assertEqual(m.access()[1],410)
    self.configure('/api/v1/apps/flip7/state','PUT',token='test-api-token-not-secret-32',body={'revision':0,'data':{'games':[1]}})
    self.assertIsNone(m.access());self.assertEqual(m.state('flip7')['revision'],1)
    with store.connect() as c:self.assertFalse(store.state_allowed(c,'yatzy'))
 def test_admin_install_and_frontend_serving(self):
    m=self.mod;z=Path(self.tmp.name)/'flip7-test.zip'
    with store.transaction() as c:store.set_doc(c,'flip7',{'games':[1]},0)
    with zipfile.ZipFile(z,'w') as f:
       f.writestr('index.html','<!doctype html><html><body>Flip 7</body></html>')
       f.writestr('sw.js','const CACHE="test";')
    class File:
        filename='flip7-test.zip'
        def save(self,dst):dst.write_bytes(z.read_bytes())
    self.configure('/admin/api/apps/install','POST',admin='test-admin-token-not-secret-64-characters')
    self.req.form={'app_id':'flip7','display_name':'Flip 7','overwrite':'no','lan_patch':'yes'}
    self.req.files={'zip':File()}
    self.assertIsNone(m.access());result=m.admin_install()
    self.assertEqual(result['status'],'ok')
    self.assertEqual(m.serve_game('flip7','index.html')['file'].split('/')[-1],'index.html')
    self.configure('/admin/api/apps/flip7/uninstall','POST',admin='test-admin-token-not-secret-64-characters',body={'confirm':'ENTFERNEN flip7'})
    self.assertIsNone(m.access());result=m.admin_uninstall('flip7')
    self.assertTrue(result['data_kept'])
    self.configure('/api/v1/apps/flip7/state','GET',token='test-api-token-not-secret-32')
    self.assertEqual(m.access()[1],410)
    with store.connect() as c:self.assertEqual(store.get_doc(c,'flip7')['data']['games'],[1])
 def test_snapshot_restoration_requires_revision(self):
    m=self.mod
    self.configure('/api/v1/apps/yatzy/state','PUT',token='test-api-token-not-secret-32',body={'revision':0,'data':{'rounds':[1]}})
    self.assertEqual(m.state('yatzy')['revision'],1)
    self.configure('/api/v1/apps/yatzy/snapshots','POST',token='test-api-token-not-secret-32')
    sid=m.create_snapshot('yatzy')[0]['snapshot_id']
    self.configure('/api/v1/apps/yatzy/state','PUT',token='test-api-token-not-secret-32',body={'revision':1,'data':{'rounds':[1,2]}})
    self.assertEqual(m.state('yatzy')['revision'],2)
    self.configure('/api/v1/apps/yatzy/restore','POST',token='test-api-token-not-secret-32',body={'snapshot_id':sid,'revision':1})
    self.assertEqual(m.restore_snapshot('yatzy')[1],409)
    self.req._json={'snapshot_id':sid,'revision':2}
    self.assertEqual(m.restore_snapshot('yatzy')['revision'],3)
    self.configure('/api/v1/apps/yatzy/state','GET',token='test-api-token-not-secret-32')
    self.assertEqual(m.state('yatzy')['data']['rounds'],[1])
 def test_origin_and_preflight(self):
    m=self.mod
    self.configure('/api/v1/apps/kniffel/state','OPTIONS',origin='http://evil.example')
    self.assertEqual(m.access()[1],403)
    self.configure('/api/v1/apps/kniffel/state','OPTIONS',origin='http://127.0.0.1:8096')
    self.assertEqual(m.access()['status'],204)

if __name__=='__main__':unittest.main()
