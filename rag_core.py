"""Teacher's RAG approach: topic chunks, embedding API, NumPy cosine search."""

import hashlib
import json
from pathlib import Path
import re
from threading import RLock

import numpy as np


def load_chunks(path):
    """Keep each numbered Chinese topic and its conditions together."""
    path = Path(path)
    text = path.read_text(encoding='utf-8-sig').replace('\r\n', '\n')
    parts = re.split(r'(?m)^((?:\d+-\d+|(?:(?<=\n\n)|\A)\d+)\.[^\n]+)\n', text)
    chunks = []
    for offset in range(1, len(parts), 2):
        title, body = parts[offset].strip(), parts[offset + 1].strip()
        if not body:
            continue
        # Each regional contact is a self-contained paragraph in the user's file.
        paragraphs = re.split(r'\n\s*\n', body) if title.startswith('1-2.') else [body]
        for paragraph in paragraphs:
            label = f'{title}／{paragraph.splitlines()[0]}' if len(paragraphs) > 1 else title
            chunks.append({'title': label, 'text': paragraph.strip(), 'source': path.name})
    if not chunks:
        raise ValueError('no_numbered_sections_in_document')
    return chunks


class BrandRAG:
    def __init__(self, source_path, client, model, min_score=.45, cache_path=None):
        self.source_path = Path(source_path)
        self.client = client
        self.model = model
        self.min_score = min_score
        self.cache_path = Path(cache_path) if cache_path else None
        self.lock = RLock()
        self.fingerprint = None
        self.chunks = []
        self.vectors = None

    def embed(self, texts):
        response = self.client.embeddings.create(model=self.model, input=texts)
        data = sorted(response.data, key=lambda item: item.index)
        if [item.index for item in data] != list(range(len(texts))):
            raise ValueError('invalid_embedding_indices')
        return self.normalize([item.embedding for item in data], len(texts))

    @staticmethod
    def normalize(values, count):
        matrix = np.asarray(values, dtype=np.float32)
        if matrix.ndim != 2 or matrix.shape[0] != count or matrix.shape[1] == 0 or not np.isfinite(matrix).all():
            raise ValueError('invalid_embedding_shape')
        norms = np.linalg.norm(matrix, axis=1, keepdims=True)
        if not np.isfinite(norms).all() or (norms <= 0).any():
            raise ValueError('invalid_embedding_norm')
        return matrix / norms

    def ensure_index(self):
        chunks = load_chunks(self.source_path)
        identity = json.dumps([chunks, self.model, str(self.client.base_url)], ensure_ascii=False)
        fingerprint = hashlib.sha256(identity.encode('utf-8')).hexdigest()
        if fingerprint == self.fingerprint:
            return
        vectors = None
        if self.cache_path and self.cache_path.exists():
            try:
                saved = json.loads(self.cache_path.read_text(encoding='utf-8'))
                if saved['fingerprint'] == fingerprint:
                    vectors = self.normalize(saved['vectors'], len(chunks))
            except (ValueError, KeyError, TypeError, OSError):
                pass
        if vectors is None:
            vectors = self.embed([f"{c['title']}\n{c['text']}" for c in chunks])
            if self.cache_path:
                self.cache_path.parent.mkdir(parents=True, exist_ok=True)
                self.cache_path.write_text(json.dumps({'fingerprint': fingerprint, 'vectors': vectors.tolist()}), encoding='utf-8')
        self.chunks, self.vectors, self.fingerprint = chunks, vectors, fingerprint

    def search(self, question, k=2):
        if not isinstance(question, str) or not question.strip():
            return {'found': False, 'reason': 'invalid_question'}
        if type(k) is not int or not 1 <= k <= 5:
            raise ValueError('invalid_k')
        with self.lock:
            self.ensure_index()
            query = self.embed([question.strip()])[0]
            if self.vectors.shape[1] != query.shape[0]:
                raise ValueError('embedding_dimension_changed')
            scores = self.vectors @ query
            best = [int(i) for i in np.argsort(scores)[::-1][:k] if scores[i] >= self.min_score]
            if not best:
                return {'found': False, 'reason': 'not_in_document', 'min_score': self.min_score}
            return {'found': True, 'chunks': [dict(self.chunks[i], score=round(float(scores[i]), 3)) for i in best]}
