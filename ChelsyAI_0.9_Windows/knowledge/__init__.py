"""Chelsy Autonomous Knowledge (RAG). This package NEVER changes model weights.

Modules: SearchManager, SourceFetcher, ContentExtractor, KnowledgeIndexer,
KnowledgeRetriever, SourceVerifier, ResearchScheduler, KnowledgeDatabase.
"""
from .database import KnowledgeDatabase
from .extractor import ContentExtractor
from .fetcher import FetchError, SourceFetcher
from .indexer import KnowledgeIndexer
from .retriever import KnowledgeRetriever
from .scheduler import ResearchError, ResearchScheduler
from .search_manager import SearchManager
from .verifier import SourceVerifier

__all__ = ['KnowledgeDatabase', 'ContentExtractor', 'FetchError', 'SourceFetcher', 'KnowledgeIndexer',
           'KnowledgeRetriever', 'ResearchError', 'ResearchScheduler', 'SearchManager', 'SourceVerifier',
           'services']

_services = None


def services():
    """Process-wide knowledge services (lazy)."""
    global _services
    if _services is None:
        database = KnowledgeDatabase()
        indexer = KnowledgeIndexer()
        _services = {
            'db': database,
            'indexer': indexer,
            'retriever': KnowledgeRetriever(),
            'scheduler': ResearchScheduler(database=database, indexer=indexer),
        }
    return _services
