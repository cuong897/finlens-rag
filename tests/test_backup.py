import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from finlens.backup import BackupError,create_backup,restore_backup,verify_backup
from finlens.store import Store


class BackupTests(unittest.TestCase):
    def setUp(self):
        root=Path(__file__).resolve().parents[1]/'data/test-runs';root.mkdir(parents=True,exist_ok=True)
        self.tmp=tempfile.TemporaryDirectory(dir=root);self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.store=Store(self.root/'source')
        original=b'%PDF-original-test';path=self.store.root/'uploads/a.pdf';path.write_bytes(original)
        self.store.add_document({'id':'a','company':'A','report_year':2024,'scope':'consolidated','is_demo':False,
            'filename':'a.pdf','pdf_path':'uploads/a.pdf','sha256':hashlib.sha256(original).hexdigest()},['Source 100'])
        self.store.add_fact({'document_id':'a','page':1,'metric':'revenue','year':2024,'value':'100','unit':'one','currency':'VND','verified':True,'note':'Reviewed source'})
        self.store.save_embeddings('m',[(self.store.chunks()[0]['id'],[1,0])])
        (self.store.root/'.env').write_text('private-key')

    def test_roundtrip_preserves_sources_facts_vectors_and_excludes_secrets(self):
        backup=self.root/'backup';restored=self.root/'restored'
        create_backup(self.store.root,backup);restore_backup(backup,restored)
        other=Store(restored)
        self.assertEqual(other.facts(),self.store.facts())
        self.assertEqual(other.chunks(),self.store.chunks())
        self.assertEqual(other.embeddings('m'),self.store.embeddings('m'))
        self.assertEqual(other.pdf_path('a').read_bytes(),self.store.pdf_path('a').read_bytes())
        self.assertFalse((backup/'.env').exists());self.assertFalse((restored/'.env').exists())

    def test_corruption_and_missing_original_refuse_without_creating_target(self):
        backup=self.root/'backup';create_backup(self.store.root,backup)
        (backup/'uploads/a.pdf').write_bytes(b'tampered')
        with self.assertRaises(BackupError):restore_backup(backup,self.root/'restore')
        self.assertFalse((self.root/'restore').exists())
        (self.store.root/'uploads/a.pdf').unlink()
        with self.assertRaises(OSError):create_backup(self.store.root,self.root/'bad-backup')
        self.assertFalse((self.root/'bad-backup').exists())

    def test_overwrite_nested_paths_and_manifest_traversal_are_rejected(self):
        backup=self.root/'backup';create_backup(self.store.root,backup)
        for destination in [self.store.root,backup/'nested',self.root]:
            with self.assertRaises(BackupError):restore_backup(backup,destination)
        manifest=json.loads((backup/'manifest.json').read_text())
        manifest['files']['uploads/../../outside']={'bytes':0,'sha256':'bad'}
        (backup/'manifest.json').write_text(json.dumps(manifest))
        with self.assertRaises(BackupError):verify_backup(backup)

    def test_table_configuration_hash_is_not_confused_with_file_hash(self):
        content=b'label,2024\nrevenue,100';path=self.store.root/'uploads/b.csv';path.write_bytes(content)
        self.store.add_document({'id':'b','company':'B','report_year':2024,'scope':'consolidated','is_demo':False,
            'filename':'b.csv','pdf_path':'uploads/b.csv','sha256':'configuration-hash'},['revenue 100'])
        with self.store.connection() as db:
            db.execute('INSERT INTO table_sources VALUES (?,?,?)',('b',hashlib.sha256(content).hexdigest(),'{}'))
        backup=self.root/'backup';create_backup(self.store.root,backup)
        restore_backup(backup,self.root/'restored')
        self.assertEqual((self.root/'restored/uploads/b.csv').read_bytes(),content)

    def test_copy_failure_does_not_publish_a_partial_restore(self):
        backup=self.root/'backup';create_backup(self.store.root,backup)
        with patch('finlens.backup.shutil.copyfile',side_effect=OSError('disk write failure')):
            with self.assertRaises(OSError):restore_backup(backup,self.root/'restored')
        self.assertFalse((self.root/'restored').exists())
