"""Dedicated local retrieval for grounded SURAKSHAI welfare support."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path


class WelfareRetrievalError(RuntimeError):
    """Raised when the dedicated welfare retrieval collection is unavailable."""


class WelfareRagService:
    COLLECTION_NAME = 'surakshai_welfare_knowledge'

    def __init__(self, knowledge_base_path=None, persist_path=None, embedding_model_name=None, client=None, embedding_model=None):
        base_dir = Path(__file__).resolve().parents[2]
        self.knowledge_base_path = Path(knowledge_base_path or base_dir / 'knowledge_base')
        self.persist_path = Path(persist_path or base_dir / 'chroma_db')
        self.embedding_model_name = embedding_model_name or os.getenv(
            'SURAKSHAI_EMBEDDING_MODEL',
            'sentence-transformers/all-MiniLM-L6-v2',
        )
        self.client = client
        self.embedding_model = embedding_model
        self.collection = None
        self._initialized = False

    @staticmethod
    def build_query(risk_category, contributors, data_mode='OPERATIONAL_ONLY'):
        if risk_category not in {'LOW', 'ELEVATED', 'HIGH'}:
            raise ValueError('Invalid canonical risk category')
        safe_labels = []
        wellness_labels = {'sleep quality', 'fatigue level', 'perceived stress', 'mood and wellbeing'}
        for contributor in contributors or []:
            label = str(contributor.get('label', '')).strip()
            if not label:
                continue
            if data_mode != 'OPERATIONAL_AND_WELLNESS' and any(term in label.lower() for term in wellness_labels):
                continue
            safe_labels.append(label)
        labels = ', '.join(dict.fromkeys(safe_labels[:5])) or 'general operational recovery and welfare support'
        return f'welfare support for {risk_category.lower()} operational patterns involving {labels}'

    def initialize(self):
        try:
            import chromadb
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            raise WelfareRetrievalError('ChromaDB and SentenceTransformers are required for welfare retrieval') from exc
        if self.client is None:
            self.persist_path.mkdir(parents=True, exist_ok=True)
            self.client = chromadb.PersistentClient(path=str(self.persist_path))
        if self.embedding_model is None:
            self.embedding_model = SentenceTransformer(self.embedding_model_name)
        try:
            self.collection = self.client.get_or_create_collection(self.COLLECTION_NAME)
            if self.collection.count() == 0:
                self._seed_collection()
        except Exception as exc:
            raise WelfareRetrievalError('Unable to initialize the SURAKSHAI welfare collection') from exc
        self._initialized = True
        return self

    def _documents(self):
        paths = sorted(self.knowledge_base_path.glob('surakshai_*.md'))
        if not paths:
            raise WelfareRetrievalError('No dedicated SURAKSHAI welfare knowledge documents found')
        documents = []
        for path in paths:
            content = path.read_text(encoding='utf-8')
            sections = self._split_sections(content)
            for index, section in enumerate(sections):
                if section['text'].strip():
                    source_id = f'{path.name}::{index}'
                    documents.append({
                        'id': source_id,
                        'text': section['text'].strip(),
                        'source': path.name,
                        'title': section['title'],
                    })
        if not documents:
            raise WelfareRetrievalError('Dedicated SURAKSHAI welfare knowledge is empty')
        return documents

    @staticmethod
    def _split_sections(content):
        sections = []
        title = ''
        lines = []
        for line in content.splitlines():
            if line.startswith('## '):
                if lines:
                    sections.append({'title': title, 'text': '\n'.join(lines)})
                title = line[3:].strip()
                lines = [line]
            elif line.startswith('# ') and not lines:
                title = line[2:].strip()
                lines = [line]
            else:
                lines.append(line)
        if lines:
            sections.append({'title': title, 'text': '\n'.join(lines)})
        return sections

    def _seed_collection(self):
        documents = self._documents()
        embeddings = self.embedding_model.encode([item['text'] for item in documents])
        embeddings = embeddings.tolist() if hasattr(embeddings, 'tolist') else embeddings
        self.collection.add(
            ids=[item['id'] for item in documents],
            documents=[item['text'] for item in documents],
            metadatas=[{'source': item['source'], 'title': item['title']} for item in documents],
            embeddings=embeddings,
        )

    def retrieve_welfare_context(self, query, top_k=3):
        if not isinstance(query, str) or not query.strip():
            raise ValueError('Welfare retrieval query must not be empty')
        if not isinstance(top_k, int) or top_k < 1:
            raise ValueError('top_k must be a positive integer')
        if not self._initialized:
            self.initialize()
        try:
            query_embedding = self.embedding_model.encode([query])
            query_embedding = query_embedding.tolist() if hasattr(query_embedding, 'tolist') else query_embedding
            result = self.collection.query(
                query_embeddings=query_embedding,
                n_results=top_k,
            )
        except Exception as exc:
            raise WelfareRetrievalError('Welfare knowledge retrieval failed') from exc
        ids = (result.get('ids') or [[]])[0]
        documents = (result.get('documents') or [[]])[0]
        metadatas = (result.get('metadatas') or [[]])[0]
        distances = (result.get('distances') or [[]])[0]
        retrieved = []
        for index, text in enumerate(documents):
            metadata = metadatas[index] if index < len(metadatas) else {}
            retrieved.append({
                'id': ids[index] if index < len(ids) else hashlib.sha256(text.encode()).hexdigest()[:16],
                'text': text,
                'source': metadata.get('source'),
                'title': metadata.get('title'),
                'distance': distances[index] if index < len(distances) else None,
            })
        return retrieved
