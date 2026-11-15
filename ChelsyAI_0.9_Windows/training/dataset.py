"""Export a verified supervised fine-tuning dataset (JSONL, chat format).

Included: only answers that the user explicitly rated 👍, from accounts that
gave training consent (setting ``training_consent``), that pass automatic
quality checks. Third-party personal data is redacted. Deterministic
train/eval split. A manifest with SHA-256 lets the registry tie a model
to the exact data it was trained on.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import core
import response_quality as rq
from knowledge.extractor import redact_personal_data

SYSTEM = 'Ты — Челси (Chelsy), доброжелательная и точная ИИ-помощница Chelsy AI.'


def consenting_users():
    with core.DB_LOCK, core.db() as conn:
        rows = conn.execute("SELECT username, value FROM user_settings WHERE setting='training_consent'").fetchall()
    return [u for u, v in rows if v == 'true']


def collect_pairs(usernames=None):
    users = usernames if usernames is not None else consenting_users()
    pairs = []
    for username in users:
        if core.get_setting(username, 'training_consent', False) is not True:
            continue
        with core.DB_LOCK, core.db() as conn:
            rated = conn.execute('SELECT id, conversation_id, content FROM messages WHERE username=? AND role=? '
                                 'AND rating>0 AND deleted=0', (username, 'assistant')).fetchall()
            for mid, cid, answer in rated:
                prev = conn.execute('SELECT content FROM messages WHERE username=? AND conversation_id=? AND id<? '
                                    'AND role=? AND deleted=0 ORDER BY id DESC LIMIT 1',
                                    (username, cid, mid, 'user')).fetchone()
                if not prev:
                    continue
                question = prev[0]
                if rq.assess(question, answer).serious:
                    continue
                answer = answer.split('\n\n---\n', 1)[0]   # drop the sources footer
                pairs.append((redact_personal_data(question), redact_personal_data(answer)))
    return pairs


def export(folder, usernames=None, eval_share=0.1):
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    pairs = collect_pairs(usernames)
    train, evaluation = [], []
    for question, answer in pairs:
        record = {'messages': [{'role': 'system', 'content': SYSTEM}, {'role': 'user', 'content': question},
                               {'role': 'assistant', 'content': answer}]}
        bucket = int(hashlib.sha256(question.encode('utf-8')).hexdigest(), 16) % 100
        (evaluation if bucket < eval_share * 100 else train).append(record)
    paths = {}
    for name, records in (('train', train), ('eval', evaluation)):
        path = folder / f'{name}.jsonl'
        path.write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in records), encoding='utf-8')
        paths[name] = path
    digest = hashlib.sha256(paths['train'].read_bytes() + paths['eval'].read_bytes()).hexdigest()
    manifest = {'created': datetime.now(timezone.utc).isoformat(), 'train': len(train), 'eval': len(evaluation),
                'sha256': digest, 'source': 'user-rated answers with training consent', 'pii': 'redacted'}
    (folder / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    return manifest
