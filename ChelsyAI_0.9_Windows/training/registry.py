"""Model registry with evaluation gate, backup of the active choice and rollback.

The registry stores which Ollama model is the host default. Activating a
fine-tuned model requires that its evaluation score is not worse than the
current model by more than ``tolerance``. Ollama models are never deleted,
so a rollback is always possible.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import core

NAME = re.compile(r'^[a-z0-9][a-z0-9._-]{0,60}(:[a-z0-9._-]{1,40})?$')


class RegistryError(RuntimeError):
    pass


class ModelRegistry:
    def __init__(self, folder=None, runner=subprocess.run):
        self.folder = Path(folder or core.DATA / 'models')
        self.path = self.folder / 'registry.json'
        self.runner = runner

    def load(self):
        try:
            return json.loads(self.path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return {'active': None, 'history': [], 'models': {}}

    def save(self, data):
        self.folder.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            backups = self.folder / 'backups'
            backups.mkdir(exist_ok=True)
            shutil.copy2(self.path, backups / f'registry-{datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")}.json')
        tmp = self.path.with_suffix('.tmp')
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
        tmp.replace(self.path)

    def register(self, name, gguf_path, base, manifest=None, system_prompt=None):
        """Create an Ollama model from a GGUF/adapter produced by an external trainer."""
        if not NAME.match(name):
            raise RegistryError('Недопустимое имя модели')
        gguf = Path(gguf_path)
        if not gguf.is_file():
            raise RegistryError(f'Файл модели не найден: {gguf}')
        self.folder.mkdir(parents=True, exist_ok=True)
        modelfile = self.folder / f'{name.replace(":", "_")}.Modelfile'
        lines = [f'FROM {gguf.resolve()}', 'PARAMETER temperature 0.6']
        if system_prompt:
            lines.append('SYSTEM """' + system_prompt.replace('"""', '') + '"""')
        modelfile.write_text('\n'.join(lines) + '\n', encoding='utf-8')
        result = self.runner(['ollama', 'create', name, '-f', str(modelfile)], capture_output=True, text=True)
        if result.returncode != 0:
            raise RegistryError('ollama create завершилась с ошибкой: ' + (result.stderr or '')[-400:])
        data = self.load()
        data['models'][name] = {'base': base, 'created': datetime.now(timezone.utc).isoformat(),
                                'dataset': manifest or {}, 'score': None, 'status': 'candidate'}
        self.save(data)
        return name

    def record_score(self, name, score):
        data = self.load()
        data['models'].setdefault(name, {'base': '', 'created': '', 'dataset': {}, 'status': 'external'})['score'] = score
        self.save(data)

    def activate(self, name, baseline_score, tolerance=0.02, force=False):
        data = self.load()
        entry = data['models'].get(name)
        if not entry:
            raise RegistryError('Модель не зарегистрирована')
        if entry.get('score') is None:
            raise RegistryError('Сначала оцени модель (model_eval)')
        if not force and entry['score'] + tolerance < baseline_score:
            raise RegistryError(f'Оценка {entry["score"]} хуже текущей {baseline_score}: активация отклонена')
        data['history'].append({'from': data.get('active'), 'to': name, 'at': datetime.now(timezone.utc).isoformat()})
        data['active'] = name
        entry['status'] = 'active'
        self.save(data)
        from intelligence import save_host_settings
        save_host_settings({'model': name})
        return name

    def rollback(self):
        data = self.load()
        if not data['history']:
            raise RegistryError('Нет предыдущей модели для отката')
        last = data['history'].pop()
        previous = last['from']
        if data.get('active') in data['models']:
            data['models'][data['active']]['status'] = 'rolled-back'
        data['active'] = previous
        self.save(data)
        from intelligence import save_host_settings
        save_host_settings({'model': previous or ''})
        return previous
