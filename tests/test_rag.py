import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import numpy as np

import rag_core
from agent_core import StoreTools, build_database, run_agent
from test_agent_core import FakeClient, model_message, tool_call

ROOT = Path(__file__).resolve().parents[1]


class EmbeddingClient:
    """Hand-written vectors test ranking; these are not app knowledge or evidence."""
    def __init__(self):
        self.embeddings = self
        self.calls = 0
        self.base_url = 'https://unit-test.invalid/v1/'

    def create(self, model, input):
        self.calls += 1
        def vector(text):
            return [1., 0.] if '奶茶' in text else [0., 1.]
        return SimpleNamespace(data=[SimpleNamespace(index=i, embedding=vector(t)) for i, t in enumerate(input)])


class RAGTests(unittest.TestCase):
    def test_user_text_keeps_activity_conditions_and_regional_contacts(self):
        self.assertTrue(hasattr(rag_core, 'load_chunks'))
        chunks = rag_core.load_chunks(ROOT / '關於50嵐.txt')
        gifts = next(c for c in chunks if c['title'].startswith('2-1.'))
        self.assertIn('2026冬季', gifts['title'])
        self.assertIn('外送不適用', gifts['text'])
        self.assertIn('高雄', gifts['text'])
        self.assertTrue(all(c['source'] == '關於50嵐.txt' for c in chunks))
        self.assertTrue(any('0800-885-050' in c['text'] for c in chunks))

    def test_cosine_search_and_cache_invalidation(self):
        self.assertTrue(hasattr(rag_core, 'BrandRAG'))
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / 'rules.txt'
            cache = Path(folder) / 'index.json'
            source.write_text('1.奶茶活動\n買三杯奶茶送好禮\n\n2.歷史\n品牌起源', encoding='utf-8')
            client = EmbeddingClient()
            rag = rag_core.BrandRAG(source, client, 'unit-model', cache_path=cache)
            result = rag.search('奶茶')
            self.assertTrue(result['found'])
            self.assertEqual(result['chunks'][0]['title'], '1.奶茶活動')
            self.assertEqual(len(result['chunks']), 1)
            rag_core.BrandRAG(source, client, 'unit-model', cache_path=cache).search('奶茶')
            self.assertEqual(client.calls, 3)  # one index, two queries
            source.write_text(source.read_text(encoding='utf-8') + '\n\n3.新品\n新品上市', encoding='utf-8')
            rag_core.BrandRAG(source, client, 'unit-model', cache_path=cache).search('奶茶')
            self.assertEqual(client.calls, 5)

    def test_invalid_embedding_is_not_cached(self):
        self.assertTrue(hasattr(rag_core, 'BrandRAG'))
        client = EmbeddingClient()
        client.create = lambda **kw: SimpleNamespace(data=[SimpleNamespace(index=i, embedding=[0., 0.]) for i, _ in enumerate(kw['input'])])
        with tempfile.TemporaryDirectory() as folder:
            cache = Path(folder) / 'index.json'
            with self.assertRaises(ValueError):
                rag_core.BrandRAG(ROOT / '關於50嵐.txt', client, 'unit-model', cache_path=cache).search('問題')
            self.assertFalse(cache.exists())

    def test_rag_tool_is_called_and_source_is_in_final_answer(self):
        self.assertTrue(hasattr(StoreTools, 'search_brand_info'))
        with tempfile.TemporaryDirectory() as folder:
            connection = build_database(':memory:', ROOT / 'my_shop_info.csv', ROOT / 'my_menu.csv')
            rag = SimpleNamespace(search=lambda q: {'found': True, 'chunks': [{'title': '2-1.活動', 'text': '買三杯奶茶送好禮', 'score': .9, 'source': '關於50嵐.txt'}]})
            try:
                client = FakeClient([model_message(calls=[tool_call('search_brand_info', json.dumps({'question':'奶茶活動'}))]), model_message('買三杯奶茶送好禮。')])
                answer, trace = run_agent([{'role':'user','content':'奶茶活動是什麼？'}], client, 'unit-model', StoreTools(connection, rag=rag))
                self.assertEqual(trace[0]['result']['chunks'][0]['score'], .9)
                self.assertIn('2-1.活動', answer)
                self.assertIn('關於50嵐.txt', answer)
            finally:
                connection.close()
