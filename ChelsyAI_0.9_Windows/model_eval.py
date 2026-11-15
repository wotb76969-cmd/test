"""Evaluate installed Ollama models on a fixed RU/EN quality suite.

Prompt engineering alone cannot guarantee quality: this tool measures it.
Each answer is checked automatically (language, mixing, coherence,
repetition, length vs. question complexity) and timed. Run on the host:

    python model_eval.py                 # all installed chat models
    python model_eval.py qwen3:8b gemma3:4b

The report is written to <data>/model_eval/report-<time>.md/.json.
Automatic scores are a screening signal; read the answers yourself before
switching the default model.
"""
from __future__ import annotations

import json
import sys
import time
from datetime import datetime

import ollama_client
import response_quality as rq

SUITE = [
    {'id': 'smalltalk-ru', 'lang': 'ru', 'prompt': 'Как дела, Челси?', 'length': 'short'},
    {'id': 'science-ru', 'lang': 'ru', 'prompt': 'Почему небо голубое?', 'length': 'medium',
     'expect_any': ['рассе', 'волн']},
    {'id': 'explain-ru', 'lang': 'ru', 'prompt': 'Объясни простыми словами, что такое рекурсия, и приведи пример на Python.',
     'length': 'medium', 'expect_any': ['def ', 'рекурс']},
    {'id': 'structured-ru', 'lang': 'ru', 'prompt': 'Составь пошаговый план подготовки к собеседованию на Python-разработчика.',
     'length': 'long', 'expect_any': ['1.', '- ', '* ']},
    {'id': 'uncertainty-ru', 'lang': 'ru', 'prompt': 'Сколько точно жителей будет в Казани в 2087 году?', 'length': 'short',
     'expect_any': ['невозможно', 'нельзя', 'точно', 'прогноз', 'не могу']},
    {'id': 'smalltalk-en', 'lang': 'en', 'prompt': 'Hi Chelsy, how is your day going?', 'length': 'short'},
    {'id': 'explain-en', 'lang': 'en', 'prompt': 'Explain what an HTTP cookie is in simple terms.', 'length': 'medium',
     'expect_any': ['browser', 'server']},
    {'id': 'code-en', 'lang': 'en', 'prompt': 'Write a Python function that checks whether a string is a palindrome.',
     'length': 'medium', 'expect_any': ['def ']},
]
LENGTH_RANGE = {'short': (5, 90), 'medium': (25, 450), 'long': (80, 1400)}


def score_answer(case, answer):
    assessment = rq.assess(case['prompt'], answer, case['lang'], searched=False)
    points, notes = 0.0, []
    if not assessment.serious:
        points += 0.5
    else:
        notes += assessment.issues
    words = len(answer.split())
    low, high = LENGTH_RANGE[case['length']]
    if low <= words <= high:
        points += 0.2
    else:
        notes.append(f'length {words} words')
    if case.get('expect_any'):
        if any(token.lower() in answer.lower() for token in case['expect_any']):
            points += 0.3
        else:
            notes.append('expected content missing')
    else:
        points += 0.3
    return round(points, 2), notes


def evaluate(models, llm=ollama_client, system=None):
    from intelligence import PERSONA_RU, PERSONA_EN, LENGTH
    results = []
    for model in models:
        rows = []
        for case in SUITE:
            persona = (PERSONA_RU if case['lang'] == 'ru' else PERSONA_EN).format(
                length=LENGTH[case['lang']][case['length']], language='русском' if case['lang'] == 'ru' else 'English')
            messages = [{'role': 'system', 'content': system or persona}, {'role': 'user', 'content': case['prompt']}]
            started = time.monotonic()
            try:
                answer = llm.chat(model, messages, {'temperature': 0.3, 'num_ctx': 4096, 'num_predict': 700})
                error = ''
            except Exception as exc:
                answer, error = '', type(exc).__name__
            elapsed = round(time.monotonic() - started, 2)
            points, notes = score_answer(case, answer) if not error else (0.0, [error])
            rows.append({'case': case['id'], 'score': points, 'seconds': elapsed, 'notes': notes, 'answer': answer})
        total = round(sum(r['score'] for r in rows) / len(rows), 3)
        results.append({'model': model, 'score': total, 'avg_seconds': round(sum(r['seconds'] for r in rows) / len(rows), 2),
                        'cases': rows})
    results.sort(key=lambda r: -r['score'])
    return results


def write_report(results, folder):
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
    (folder / f'report-{stamp}.json').write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
    lines = ['# Оценка моделей Chelsy AI', '', '| Модель | Оценка (0–1) | Среднее время, с |', '|---|---|---|']
    lines += [f'| {r["model"]} | {r["score"]} | {r["avg_seconds"]} |' for r in results]
    for r in results:
        lines += ['', f'## {r["model"]}']
        for c in r['cases']:
            lines += [f'### {c["case"]} — {c["score"]} ({c["seconds"]} с) {"; ".join(c["notes"])}', '', c['answer'][:1500], '']
    path = folder / f'report-{stamp}.md'
    path.write_text('\n'.join(lines), encoding='utf-8')
    return path


def main(argv=None):
    import core
    models = (argv if argv is not None else sys.argv[1:]) or ollama_client.list_models()
    if not models:
        print('Ollama недоступна или модели не установлены.')
        return 1
    results = evaluate(models)
    path = write_report(results, core.DATA / 'model_eval')
    for r in results:
        print(f'{r["model"]:30} score={r["score"]:.3f} avg={r["avg_seconds"]}s')
    print('Отчёт:', path)
    return 0


if __name__ == '__main__':
    sys.exit(main())
