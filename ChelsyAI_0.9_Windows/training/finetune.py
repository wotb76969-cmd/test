"""Owner-only CLI for the controlled fine-tuning process.

    python -m training.finetune export  --out data/training/run1
    python -m training.finetune train   --data data/training/run1 --confirm
    python -m training.finetune register --name chelsy-ft:v1 --gguf out.gguf --base qwen3:8b --data data/training/run1
    python -m training.finetune evaluate --name chelsy-ft:v1 --baseline qwen3:8b
    python -m training.finetune activate --name chelsy-ft:v1
    python -m training.finetune rollback

``train`` runs an EXTERNAL trainer command (e.g. an Unsloth/axolotl script
that produces a GGUF file) taken from the CHELSY_TRAIN_COMMAND environment
variable; Chelsy itself does not contain a GPU trainer. Training requires
``--confirm`` and is never triggered by conversations.
"""
from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path


def main(argv=None):
    os.environ.setdefault('CHELSY_APPDATA', '1')
    import core
    from training.dataset import export
    from training.registry import ModelRegistry, RegistryError

    parser = argparse.ArgumentParser(description='Chelsy AI controlled fine-tuning')
    sub = parser.add_subparsers(dest='cmd', required=True)
    p = sub.add_parser('export')
    p.add_argument('--out', default=str(core.DATA / 'training' / 'latest'))
    p = sub.add_parser('train')
    p.add_argument('--data', required=True)
    p.add_argument('--confirm', action='store_true')
    p = sub.add_parser('register')
    p.add_argument('--name', required=True)
    p.add_argument('--gguf', required=True)
    p.add_argument('--base', required=True)
    p.add_argument('--data')
    p = sub.add_parser('evaluate')
    p.add_argument('--name', required=True)
    p.add_argument('--baseline', required=True)
    p = sub.add_parser('activate')
    p.add_argument('--name', required=True)
    p.add_argument('--force', action='store_true')
    sub.add_parser('rollback')
    args = parser.parse_args(argv)
    registry = ModelRegistry()
    try:
        if args.cmd == 'export':
            print(json.dumps(export(args.out), ensure_ascii=False, indent=2))
        elif args.cmd == 'train':
            command = os.environ.get('CHELSY_TRAIN_COMMAND', '').strip()
            if not command:
                print('Задай CHELSY_TRAIN_COMMAND (внешний тренер, например скрипт Unsloth → GGUF).')
                return 2
            if not args.confirm:
                print('Обучение меняет веса модели. Повтори с --confirm после проверки датасета.')
                return 2
            manifest = json.loads((Path(args.data) / 'manifest.json').read_text(encoding='utf-8'))
            if manifest.get('train', 0) < 50:
                print('Слишком маленький датасет (< 50 примеров) — обучение не запускается.')
                return 2
            return subprocess.run(shlex.split(command) + [str(Path(args.data).resolve())]).returncode
        elif args.cmd == 'register':
            manifest = None
            if args.data:
                manifest = json.loads((Path(args.data) / 'manifest.json').read_text(encoding='utf-8'))
            print(registry.register(args.name, args.gguf, args.base, manifest))
        elif args.cmd == 'evaluate':
            import model_eval
            results = {r['model']: r['score'] for r in model_eval.evaluate([args.name, args.baseline])}
            registry.record_score(args.name, results[args.name])
            data = registry.load()
            data['baseline'] = {'model': args.baseline, 'score': results[args.baseline]}
            registry.save(data)
            print(json.dumps(results, indent=2))
        elif args.cmd == 'activate':
            data = registry.load()
            baseline = (data.get('baseline') or {}).get('score')
            if baseline is None:
                print('Сначала выполни evaluate с --baseline.')
                return 2
            print('Активна модель:', registry.activate(args.name, baseline, force=args.force))
        elif args.cmd == 'rollback':
            print('Возврат к модели:', registry.rollback() or 'по умолчанию')
    except RegistryError as exc:
        print('Ошибка:', exc)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
