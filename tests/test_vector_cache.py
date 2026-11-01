import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from finlens.store import Store


class VectorCacheTests(unittest.TestCase):
    def setUp(self):
        root=Path(__file__).resolve().parents[1]/'data/test-runs';root.mkdir(parents=True,exist_ok=True)
        self.tmp=tempfile.TemporaryDirectory(dir=root);self.addCleanup(self.tmp.cleanup)
        self.store=Store(self.tmp.name)
        self.store.add_document({'id':'a','company':'A','report_year':2024,'scope':'consolidated',
            'filename':'source.pdf','sha256':'source','is_demo':False,'pdf_path':None},['Source row 123'])
        self.cid=self.store.chunks()[0]['id']
        self.store.save_embeddings('m',[(self.cid,[1,0])])

    def read(self):return self.store.retrieval_inputs('A',[2024],'consolidated','real',model='m')

    def test_warm_snapshot_reuses_immutable_vectors(self):
        _,first=self.read()
        _,second=self.read()
        self.assertIs(first,second)
        self.assertEqual(first[self.cid],(1,0))
        with self.assertRaises(TypeError):first[self.cid]=(0,1)
        with self.assertRaises(TypeError):first[self.cid][0]=99

    def test_cross_store_replace_update_clear_and_rollback_invalidate_correctly(self):
        _,first=self.read();other=Store(self.tmp.name)
        other.save_embeddings('m',[(self.cid,[0,1])])
        _,second=self.read();self.assertIsNot(first,second);self.assertEqual(second[self.cid],(0,1))
        with other.connection() as db:db.execute('UPDATE embeddings SET vector=? WHERE chunk_id=?',(json.dumps([2,3]),self.cid))
        self.assertEqual(self.read()[1][self.cid],(2,3))
        with self.assertRaises(RuntimeError):
            with other.connection() as db:
                db.execute('UPDATE embeddings SET vector=? WHERE chunk_id=?',(json.dumps([9,9]),self.cid))
                raise RuntimeError('rollback')
        self.assertEqual(self.read()[1][self.cid],(2,3))
        other.clear_embeddings('m');self.assertEqual(dict(self.read()[1]),{})

    def test_scope_and_reprocessing_do_not_leak_old_vectors(self):
        self.read()
        self.assertEqual(self.store.retrieval_inputs('Other',model='m')[0],[])
        self.store.replace_derived_text('a',['Changed source 456'],[],'new')
        chunks,vectors=self.read()
        self.assertEqual(chunks[0]['text'],'Changed source 456');self.assertEqual(dict(vectors),{})
        self.assertEqual(dict(self.store.retrieval_inputs(model='unindexed')[1]),{})

    def test_writer_between_chunk_and_vector_reads_cannot_mix_snapshots(self):
        self.read();original=self.store._chunks_in_connection
        def interleave(*args,**kwargs):
            chunks=original(*args,**kwargs)
            other=Store(self.tmp.name);other.save_embeddings('m',[(self.cid,[0,1])])
            return chunks
        with patch.object(self.store,'_chunks_in_connection',side_effect=interleave):
            _,snapshot=self.read()
        self.assertEqual(snapshot[self.cid],(1,0))
        self.assertEqual(self.read()[1][self.cid],(0,1))
