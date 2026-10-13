"""Synthetic data only. Exercises the real SQLCipher library and helper protocol."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
from storage_runtime import Runtime, VaultError
from vault import Database

ROOT = Path(__file__).parent
PASSWORD = 'synthetic-master-password'

class VaultTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        spec = importlib.util.spec_from_file_location('isolated_client', ROOT / 'client.py')
        self.c = importlib.util.module_from_spec(spec); spec.loader.exec_module(self.c)
        self.c.CONFIG = Path(self.temp.name) / 'config'
        self.c.STATE = Path(self.temp.name) / 'state'
        self.original_write = self.c.write_json
        self.r = Runtime(self.c)
    def tearDown(self):
        self.r.close(); self.temp.cleanup()
    def migrate(self, **kw):
        return self.r.security({'action': 'create_vault', 'password': PASSWORD, **kw})
    def test_fresh_setup_standard_and_optional_lock(self):
        self.assertTrue(self.r.status()['setup'])
        with self.assertRaises(self.c.ClientError): self.r.require({})
        self.r.security({'action': 'use_standard'})
        self.r.require({})
        self.c.write_json(self.c.CONFIG / 'accounts.json', {'accounts': []})
        result = self.r.security({'action': 'configure_security', 'password': PASSWORD, 'timeout': 5})
        self.assertFalse(result['encrypted'])
        with self.assertRaises(self.c.ClientError): self.r.require({})
        self.r.require({'session': result['session']})
    def test_migration_encrypts_verifies_and_removes_legacy(self):
        values = {
          self.c.CONFIG / 'accounts.json': {'accounts': [{'token': 'SYNTHETIC_SECRET_TOKEN'}]},
          self.c.CONFIG / 'contacts.json': {'name': 'SYNTHETIC_PRIVATE_CONTACT'},
          self.c.STATE / 'send-ledger.json': {'request-synthetic': {'state': 'started'}},
          self.c.STATE / 'read-state.json': {'seen': ['test-message']},
          self.c.STATE / 'numbers-synthetic.json': {'numbers': ['+12025550101']}}
        for p, v in values.items(): self.original_write(p, v)
        result = self.migrate()
        self.r.require({'session': result['session']})
        for p, v in values.items():
            self.assertFalse(p.exists()); self.assertEqual(self.c.read_json(p, {}), v)
        raw = self.r.path.read_bytes()
        self.assertFalse(raw.startswith(b'SQLite format'))
        for marker in (b'SYNTHETIC_SECRET_TOKEN', b'SYNTHETIC_PRIVATE_CONTACT', PASSWORD.encode()):
            for path in Path(self.temp.name).rglob('*'):
                if path.is_file(): self.assertNotIn(marker, path.read_bytes())
        self.r.close()
        with self.assertRaises(self.c.ClientError): self.c.read_json(self.c.CONFIG/'accounts.json', {})
        with self.assertRaises(VaultError): Database(self.r.path, 'wrong-password')
        unlocked = self.r.security({'action': 'unlock', 'password': PASSWORD})
        self.assertTrue(unlocked['encrypted'])
        self.assertEqual(self.c.read_json(self.c.STATE/'send-ledger.json', {}), values[self.c.STATE/'send-ledger.json'])
    def test_old_password_required_before_migration(self):
        salt = '00'*16
        self.original_write(self.c.CONFIG/'incognito.json', {'enabled': True, 'salt': salt, 'hash': self.c.password_hash('old-password', salt), 'timeout': 5})
        with self.assertRaises(self.c.ClientError): self.migrate(current_password='wrong')
        self.assertFalse(self.r.path.exists())
        self.original_write(self.c.STATE/'incognito-attempts.json', {})
        result = self.migrate(current_password='old-password')
        self.assertEqual(result['timeout'], 5)
        self.assertFalse((self.c.CONFIG/'incognito.json').exists())
    def test_failure_preserves_original_files(self):
        p = self.c.CONFIG/'contacts.json'; value = {'secret': 'synthetic'}
        self.original_write(p, value)
        with patch.object(Database, 'put', side_effect=VaultError('synthetic failure')):
            with self.assertRaises(VaultError): self.migrate()
        self.assertEqual(json.loads(p.read_text()), value)
        self.assertFalse(self.r.path.exists())
    def test_cleanup_failure_resumes_after_unlock(self):
        p = self.c.CONFIG/'contacts.json'; self.original_write(p, {'synthetic': 1})
        with patch.object(self.r, 'cleanup', side_effect=OSError('synthetic failure')):
            with self.assertRaises(OSError): self.migrate()
        self.assertTrue(self.r.path.exists()); self.assertTrue(p.exists()); self.assertIsNone(self.r.db)
        self.r.security({'action': 'unlock', 'password': PASSWORD})
        self.assertFalse(p.exists()); self.assertEqual(self.c.read_json(p, {}), {'synthetic': 1})
    def test_timeout_does_not_renew_on_read(self):
        session = self.migrate()['session']
        deadline = self.r.deadline
        self.r.require({'session': session})
        self.c.read_json(self.c.CONFIG/'accounts.json', {})
        self.assertEqual(self.r.deadline, deadline)
        with patch('storage_runtime.time.monotonic', return_value=deadline+1):
            with self.assertRaises(self.c.ClientError): self.r.require({'session': session})
        self.assertIsNone(self.r.db)
    def test_change_password_and_keep_password_timeout_update(self):
        session = self.migrate()['session']
        result = self.r.security({'action': 'configure_security', 'session': session, 'current_password': PASSWORD, 'password': '', 'timeout': 7})
        self.assertEqual(result['timeout'], 7)
        self.r.security({'action': 'configure_security', 'session': result['session'], 'current_password': PASSWORD, 'password': 'replacement-password', 'timeout': 9})
        self.r.close()
        with self.assertRaises(VaultError): Database(self.r.path, PASSWORD)
        self.r.security({'action': 'unlock', 'password': 'replacement-password'})
        self.assertEqual(self.r.timeout, 9)
    def test_no_plaintext_fallback_or_disable_after_encryption(self):
        session = self.migrate()['session']
        with self.assertRaises(self.c.ClientError): self.r.security({'action': 'use_standard'})
        with self.assertRaises(self.c.ClientError): self.r.security({'action': 'configure_security', 'enabled': False, 'session': session})
        self.r.close(); self.r.path.unlink()
        with self.assertRaises(self.c.ClientError): self.c.read_json(self.c.CONFIG/'accounts.json', {})
    def test_encrypted_send_ledger_survives_interruption(self):
        account = self.c.validate_account({'name':'Synthetic','sid':'AC'+'0'*32,'token':'1'*32})
        self.original_write(self.c.CONFIG/'accounts.json', {'accounts':[account]})
        session = self.migrate()['session']
        request = {'action':'send','account':account['id'],'from':'+12025550101','to':'+12025550120',
                   'body':'Synthetic interrupted send','request_id':'synthetic-request-00001','session':session}
        with patch.object(self.c,'numbers',return_value=[{'number':request['from']}]), \
             patch.object(self.c,'api',side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt): self.c.dispatch(request)
        self.r.close()
        request['session'] = self.r.security({'action':'unlock','password':PASSWORD})['session']
        with patch.object(self.c,'api') as api:
            with self.assertRaises(self.c.ClientError) as err: self.c.dispatch(request)
            self.assertTrue(err.exception.uncertain)
            api.assert_not_called()

    def test_symlink_database_rejected(self):
        p=Path(self.temp.name)/'other'; p.write_text('synthetic')
        self.c.private_dir(self.c.STATE); self.r.path.symlink_to(p)
        with self.assertRaises(self.c.ClientError): self.r.security({'action':'unlock','password':PASSWORD})
        self.assertEqual(p.read_text(),'synthetic')

class ProtocolTests(unittest.TestCase):
    def test_helper_exits_when_idle_without_ui(self):
        with tempfile.TemporaryDirectory() as folder:
            env={**os.environ,'XDG_CONFIG_HOME':folder+'/config','XDG_STATE_HOME':folder+'/state'}
            p = subprocess.Popen([sys.executable,'-I',str(ROOT/'client.py'),'--serve'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True,env=env)
            def request(data):
                p.stdin.write(json.dumps(data)+'\n'); p.stdin.flush()
                return json.loads(p.stdout.readline())
            try:
                session=request({'action':'create_vault','password':PASSWORD})['session']
                result=request({'action':'configure_security','session':session,'current_password':PASSWORD,'timeout':5})
                self.assertTrue(result['ok'])
                p.wait(timeout=9)
                self.assertEqual(json.loads(p.stdout.readline()), {'event':'locked'})
                self.assertEqual(p.returncode,0)
            finally:
                if p.poll() is None: p.kill(); p.wait()
                p.stdin.close(); p.stdout.close()

    def test_process_restart_locked_and_demo_isolated(self):
        with tempfile.TemporaryDirectory() as folder:
            env={**os.environ,'XDG_CONFIG_HOME':folder+'/config','XDG_STATE_HOME':folder+'/state'}
            process = subprocess.Popen([sys.executable,'-I',str(ROOT/'client.py'),'--serve'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True,env=env)
            def request(data):
                process.stdin.write(json.dumps(data)+'\n'); process.stdin.flush()
                return json.loads(process.stdout.readline())
            try:
                self.assertTrue(request({'action':'security_status','id':'one'})['setup'])
                result=request({'action':'create_vault','password':PASSWORD,'id':'two'})
                self.assertTrue(result['ok'],result)
                self.assertEqual(request({'action':'snapshot','session':result['session']})['accounts'],[])
            finally:
                process.stdin.close(); process.wait(timeout=10); process.stdout.close()
            result=subprocess.run([sys.executable,'-I',str(ROOT/'client.py')],input=json.dumps({'action':'snapshot','session':result['session']}),capture_output=True,text=True,env=env,check=True)
            self.assertTrue(json.loads(result.stdout)['locked'])
            before={p: p.read_bytes() for p in Path(folder).rglob('*') if p.is_file()}
            demo=subprocess.run([sys.executable,'-I',str(ROOT/'client.py'),'--demo'],input='{}',capture_output=True,text=True,env=env,check=True)
            self.assertTrue(json.loads(demo.stdout)['demo'])
            self.assertEqual(before,{p: p.read_bytes() for p in Path(folder).rglob('*') if p.is_file()})

if __name__ == '__main__': unittest.main()
