"""Cloud sync regression tests with fake PostgREST, no real credentials/network."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import uuid
import core
import cloud_sync

class CloudSyncTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.previous_data,self.previous_db=core.DATA,core.DB_PATH
        core.DATA=Path(self.tmp.name)
        core.DB_PATH=core.DATA/'test.db'
        self.a=str(uuid.uuid4())
        self.b=str(uuid.uuid4())
        self.cloud={}

    def tearDown(self):
        core.DATA,self.previous_data=self.previous_data,core.DATA
        core.DB_PATH=self.previous_db
        self.tmp.cleanup()

    def api(self,table,token,method='GET',params=None,rows=None,prefer=None):
        if table=='chelsy_ai_messages':
            uid=token  # fake JWT is also fake UUID in isolated mock
            target=self.cloud.setdefault(uid,[])
            if method=='GET': return target[:]
            if method=='POST':
                for row in rows:
                    if row['user_id'] != uid: raise AssertionError('cross-account write')
                    if any(r['legacy_id']==row['legacy_id'] for r in target):continue
                    target.append({'id':str(uuid.uuid4()),**row})
                return []
            raise AssertionError(method)
        if table=='chelsy_ai_memories':return []
        raise AssertionError(table)

    def test_idempotent_upload_two_accounts(self):
        with patch.object(cloud_sync,'_call',side_effect=self.api):
            core.save_message('sb:'+self.a,'user','Hello')
            core.save_message('sb:'+self.a,'assistant','Hi')
            core.save_message('sb:'+self.b,'user','Secret B')
            cloud_sync.sync(self.a,self.a)
            cloud_sync.sync(self.a,self.a)
            cloud_sync.sync(self.b,self.b)
        self.assertEqual([r['content'] for r in self.cloud[self.a]],['Hello','Hi'])
        self.assertEqual([r['content'] for r in self.cloud[self.b]],['Secret B'])

    def test_cloud_import_idempotent_and_no_echo(self):
        remote={'id':str(uuid.uuid4()),'legacy_id':'web:11','role':'user',
                'content':'From website','created_at':'2026-11-11T12:00:00+00:00'}
        self.cloud[self.a]=[remote]
        with patch.object(cloud_sync,'_call',side_effect=self.api):
            self.assertEqual(cloud_sync.pull(self.a,self.a),1)
            self.assertEqual(cloud_sync.pull(self.a,self.a),0)
            self.assertEqual(cloud_sync.push(self.a,self.a),0)
        self.assertEqual(len(core.history('sb:'+self.a)),1)
        self.assertEqual(len(self.cloud[self.a]),1)

    def test_host_origin_not_reimported(self):
        core.save_message('sb:'+self.a,'user','Local')
        with patch.object(cloud_sync,'_call',side_effect=self.api):
            cloud_sync.push(self.a,self.a)
            self.assertEqual(cloud_sync.pull(self.a,self.a),0)
        self.assertEqual(len(core.history('sb:'+self.a)),1)

    def test_memory_offline_edit_has_priority(self):
        user='sb:'+self.a
        core.store_personal_fact(user,'color','violet')
        def remote(table,token,method='GET',params=None,rows=None,prefer=None):
            if method=='GET': return [{'memory_key':'color','value':'old cloud value'}]
            return []
        with patch.object(cloud_sync,'_call',side_effect=remote):
            cloud_sync.pull_memories(self.a,self.a)
            self.assertEqual(core.personal_facts(user)['color'],'violet')
            cloud_sync.push_memories(self.a,self.a)
        with core.db() as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM pending_memory WHERE username=?',(user,)).fetchone()[0],0)

    def test_memory_cloud_update_reaches_new_host(self):
        user='sb:'+self.a
        def remote(table,token,method='GET',params=None,rows=None,prefer=None):
            return [{'memory_key':'language','value':'русский'}]
        with patch.object(cloud_sync,'_call',side_effect=remote):
            cloud_sync.pull_memories(self.a,self.a)
        self.assertEqual(core.personal_facts(user)['language'],'русский')
        with core.db() as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM pending_memory WHERE username=?',(user,)).fetchone()[0],0)

    def test_memory_validates_limits(self):
        with self.assertRaises(ValueError):
            cloud_sync.save_memory(self.a,self.a,'','x')
        with self.assertRaises(ValueError):
            cloud_sync.save_memory(self.a,self.a,'fact','x'*2001)

if __name__=='__main__':unittest.main()
