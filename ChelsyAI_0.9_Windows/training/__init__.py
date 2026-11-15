"""Controlled fine-tuning pipeline (separate from RAG knowledge).

Nothing here runs automatically. Weights change only when the owner
explicitly exports a dataset, runs an external trainer, registers the
result, passes the evaluation gate and activates it. Every activation is
reversible (``rollback``) and the previous model is never deleted.
"""
