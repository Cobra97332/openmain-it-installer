"""TLS continuity receipts and sanitized JSON-RPC diagnostics; offline fixtures."""
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'netbird-router/common'))
import zabbix_registration as registration
import zabbix_sync
from runtime import Invalid


class ReceiptTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name);self.path=self.root/'receipt.json'
        self.key=self.root/'key.psk';self.key.write_text('ab'*32);self.key.chmod(0o600)
        self.c=dict(url='https://api.invalid/api_jsonrpc.php',customer='Example',proxy='proxy-example-primary',identity='test-only')
        uid=patch.object(registration,'ROOT_UID',os.getuid());uid.start();self.addCleanup(uid.stop)

    def store(self):
        return registration.TLSStore(self.path,self.c,'ab'*32)

    def prepare(self):
        store=self.store();marker=store.prepare('proxy',self.c['proxy'],'201')
        return dict(proxyid='101',name=self.c['proxy'],description=marker,proxy_groupid='201',operating_mode='0',tls_accept='2')

    def test_private_intent_confirm_repeat_and_no_plain_psk(self):
        obj=self.prepare();before=self.path.read_bytes()
        self.store().verify('proxy',obj,'201')
        self.assertEqual(self.path.read_bytes(),before)
        self.store().confirm('proxy',obj,'201')
        self.store().verify('proxy',obj,'201')
        self.assertEqual(self.path.stat().st_mode & 0o777,0o600)
        self.assertEqual(self.root.stat().st_mode & 0o777,0o700)
        self.assertNotIn('ab'*32,self.path.read_text())
        with self.assertRaises(Invalid):self.store().prepare('proxy',self.c['proxy'],'201')

    def test_missing_malformed_or_mismatched_ownership_never_adopted(self):
        obj=self.prepare()
        cases=[dict(obj,description=None),dict(obj,description='OpenMain managed foreign'),dict(obj,name='other'),
               dict(obj,proxy_groupid='202'),dict(obj,operating_mode='1'),dict(obj,tls_accept='3')]
        for case in cases:
            with self.subTest(case=case),self.assertRaises(Invalid):self.store().verify('proxy',case,'201')
        self.store().confirm('proxy',obj,'201')
        with self.assertRaises(Invalid):self.store().verify('proxy',dict(obj,proxyid='102'),'201')
        data=json.loads(self.path.read_text());del data['objects']['proxy']['marker'];self.path.write_text(json.dumps(data))
        with self.assertRaises(Invalid):self.store().verify('proxy',dict(obj,description=None),'201')
        self.path.unlink()
        with self.assertRaises(Invalid):self.store().verify('proxy',obj,'201')

    def test_binding_changes_never_replace_key_or_receipt(self):
        self.prepare();before=self.path.read_bytes()
        for key in ('identity','customer','proxy','url'):
            c=dict(self.c);c[key]='other'
            with self.subTest(key=key),self.assertRaises(Invalid):registration.TLSStore(self.path,c,'ab'*32)
        with self.assertRaises(Invalid):registration.TLSStore(self.path,self.c,'cd'*32)
        self.assertEqual(self.path.read_bytes(),before);self.assertEqual(self.key.read_text(),'ab'*32)

    def test_unsafe_receipt_links_modes_and_parent_refused(self):
        self.prepare();self.path.chmod(0o644)
        with self.assertRaises(Invalid):self.store()
        self.path.chmod(0o600);self.path.rename(self.root/'original');self.path.symlink_to(self.root/'original')
        with self.assertRaises(OSError):self.store()
        self.path.unlink();os.link(self.root/'original',self.path)
        with self.assertRaises(Invalid):self.store()
        self.path.unlink();self.root.chmod(0o755)
        with self.assertRaises(Invalid):self.store()
        self.root.chmod(0o700)

    def test_key_symlink_hardlink_or_insecure_permissions_rejected(self):
        self.assertEqual(registration.key_bytes(self.key),bytes.fromhex('ab'*32))
        self.key.chmod(0o640);self.assertEqual(registration.key_bytes(self.key),bytes.fromhex('ab'*32))
        self.key.chmod(0o644)
        with self.assertRaises(Invalid):registration.key_bytes(self.key)
        self.key.chmod(0o600);link=self.root/'link';link.symlink_to(self.key)
        with self.assertRaises(OSError):registration.key_bytes(link)
        link.unlink();os.link(self.key,link)
        with self.assertRaises(Invalid):registration.key_bytes(self.key)

    def test_host_marker_requires_exactly_one_binding_and_no_write_only_read(self):
        store=self.store();marker=store.prepare('host','router-example','201')
        host=dict(hostid='301',host='router-example',proxy_groupid='201',monitored_by=2,tls_connect=2,tls_accept=2,
                  tags=[dict(tag='OpenMain registration',value=marker)])
        store.confirm('host',host,'201')
        self.store().verify('host',host,'201')
        for tags in ([],host['tags']*2,[dict(tag='OpenMain registration',value='foreign')]):
            with self.subTest(tags=tags),self.assertRaises(Invalid):self.store().verify('host',dict(host,tags=tags),'201')

    def test_public_version_method_uses_documented_params_without_authentication(self):
        with patch.object(zabbix_sync.urllib.request,'build_opener') as build, patch.object(zabbix_sync, 'require_access') as guard:
            transport = build.return_value.open
            transport.return_value.__enter__.return_value.status = 200
            transport.return_value.__enter__.return_value.read.return_value=json.dumps(dict(jsonrpc='2.0',id=1,result='7.4.15')).encode()
            api=zabbix_sync.API('https://api.invalid','synthetic-token')
            self.assertEqual(api('apiinfo.version',[]),'7.4.15')
            request=transport.call_args.args[0]
            self.assertIsNone(request.get_header('Authorization'))
            self.assertEqual(json.loads(request.data)['params'],[])
            with self.assertRaises(registration.RPCError):api('apiinfo.version',{})
            self.assertEqual(transport.call_count,1)
            guard.assert_not_called()

    def test_rpc_codes_are_classified_without_echoing_any_server_diagnostics(self):
        for code in (-32602,-32500,-32603,'ab'*32,{},True):
            response=dict(jsonrpc='2.0',id=1,error=dict(code=code,message='synthetic-token',data='ab'*32))
            with self.subTest(code=code),self.assertRaises(registration.RPCError) as error:registration.rpc_result(response)
            self.assertNotIn('synthetic-token',str(error.exception));self.assertNotIn('ab'*32,str(error.exception))
            if type(code) is int:self.assertIn(str(code),str(error.exception))
        with patch.object(zabbix_sync.urllib.request,'build_opener') as build, patch.object(zabbix_sync, 'require_access'):
            transport = build.return_value.open
            transport.return_value.__enter__.return_value.status = 200
            transport.return_value.__enter__.return_value.read.return_value=json.dumps(dict(jsonrpc='2.0',id=1,error=dict(code=-32602,message='synthetic-token'))).encode()
            with self.assertRaisesRegex(registration.RPCError,'-32602'):zabbix_sync.API('https://api.invalid','synthetic-token')('proxy.get',{})
