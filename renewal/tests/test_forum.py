import json
from pathlib import Path
import sys,tempfile,unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from custos_forum import Forum,poll
from custos_square import APIError,Store
from custos_observe import Observer
from test_observations import NativeFixture

class FakeForum(Forum):
    def __init__(self,store):
        super().__init__(store,config={'expected_name':'custos','publish_enabled':True})
        self._key='test-secret';self.name='custos';self.posts=0;self.fail=False;self.read_fail=False;self.pages={};self.method='house'
    def request(self,path,query=None,method='GET',body=None):
        if path=='/api/renew':return {'name':self.name,'active':True,'membership':{'method':self.method}}
        if method=='POST':
            self.posts+=1
            if self.fail:raise APIError('forum_transport_uncertain')
            self.body=body
            return {'message':{'id':'m1'},'thread':{'id':'t1'}}
        if path.startswith('/api/messages/'):
            if self.read_fail:raise APIError('forum_transport_uncertain')
            return {'message':{'id':'m1','authorName':self.name,'body':self.body['body'],'threadId':'t1','parentId':self.body.get('parentId')},'thread':{'board':'research','title':self.body.get('title')}}
        return self.pages[path]

class ForumTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.store=Store(self.temp.name);self.addCleanup(self.store.db.close)
        self.forum=FakeForum(self.store)
        self.payload={'board':'research','title':'A test','body':'A bounded observation.'}
    def test_wrong_identity_and_disabled_writes_never_post(self):
        self.forum.name='kevin-s-bot'
        with self.assertRaisesRegex(APIError,'identity_mismatch'):self.forum.write('request-1','post',self.payload)
        self.forum.name='custos';self.forum.config['publish_enabled']=False
        with self.assertRaisesRegex(APIError,'publishing_disabled'):self.forum.write('request-1','post',self.payload)
        self.assertEqual(self.forum.posts,0)
    def test_paid_post_never_signs_or_pays(self):
        self.forum.method='x402'
        with self.assertRaisesRegex(APIError,'not_authorized'):self.forum.write('request-1','post',self.payload)
        self.assertEqual(self.forum.posts,0)
    def test_success_readback_and_replay(self):
        receipt=self.forum.write('request-1','post',self.payload)
        self.assertEqual(receipt['status'],'delivered')
        self.assertEqual(self.forum.write('request-1','post',self.payload),receipt)
        self.assertEqual(self.forum.posts,1)
    def test_uncertain_write_never_retried_even_under_new_id(self):
        self.forum.fail=True
        for identity in ['request-1','request-1','request-2']:
            with self.assertRaises(APIError):self.forum.write(identity,'post',self.payload)
        self.assertEqual(self.forum.posts,1)
    def test_readback_outage_recovers_without_reposting(self):
        self.forum.read_fail=True
        with self.assertRaises(APIError):self.forum.write('request-1','post',self.payload)
        self.forum.read_fail=False
        self.assertEqual(self.forum.receipt('request-1')['status'],'delivered')
        self.assertEqual(self.forum.posts,1)
    def test_path_and_secret_refused(self):
        for path in ['/api/renew/x402','https://evil.example/','/api/bounties','/api/signup']:
            with self.assertRaises(APIError):Forum.request(self.forum,path)
        with self.assertRaisesRegex(APIError,'secret'):self.forum.write('request-1','post',dict(self.payload,body='test-secret'))
    def test_capture_before_cursor_and_replay(self):
        row={'id':'m2','threadId':'t1','authorName':'neighbor','body':'Please check the method.'}
        self.forum.pages['/api/inbox']={'inbox':[row],'nextCursor':100,'cursorParam':'since','hasMore':False}
        native=NativeFixture();native.fail_capture=True;observer=Observer({},self.store,None,native)
        with self.assertRaises(APIError):poll(observer,self.forum,'inbox')
        self.assertIsNone(self.store.get('forum:inbox'))
        self.assertIsNotNone(self.store.get('forum:inbox:pending'))
        native.fail_capture=False;poll(observer,self.forum,'inbox');poll(observer,self.forum,'inbox')
        self.assertEqual(len(native.goals),1)
        self.assertEqual(self.store.get('forum:inbox')['query'],{'since':100})
    def test_mixed_thread_and_message_feed_page_advances(self):
        thread={'type':'thread','id':'th_1','authorName':'house','title':'Research','board':'research'}
        message={'type':'message','id':'m2','threadId':'th_1','authorName':'neighbor','body':'Actual content','board':'research'}
        self.forum.pages['/api/feed']={'feed':[thread,message],'nextCursor':100,'cursorParam':'since','hasMore':False}
        observer=Observer({'forum':{'boards':['research']}},self.store,None,NativeFixture())
        poll(observer,self.forum,'feed')
        self.assertEqual(self.store.get('forum:feed')['query'],{'since':100})
        self.assertTrue(self.store.seen('forum:thread:th_1'))
        self.assertTrue(self.store.seen('forum:feed:m2'))
        self.assertIsNone(self.store.get('forum:feed:pending'))

    def test_feed_baseline_does_not_suppress_directed_inbox(self):
        row={'id':'m2','threadId':'t1','authorName':'neighbor','body':'Please check the method.','board':'research'}
        for kind in ['feed','inbox']:self.forum.pages['/api/'+kind]={kind:[row],'nextCursor':100,'cursorParam':'since','hasMore':False}
        native=NativeFixture();observer=Observer({'forum':{'boards':['research']}},self.store,None,native)
        poll(observer,self.forum,'feed');self.assertFalse(native.goals)
        poll(observer,self.forum,'inbox');self.assertEqual(len(native.goals),1)

if __name__=='__main__':unittest.main()
