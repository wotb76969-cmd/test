"""Offline/source smoke checks for dependencies and important app APIs."""
import inspect
import sys
from pathlib import Path

import gradio as gr
import core
import app

assert sys.version_info[:2] == (3, 11), 'Build with Python 3.11'
assert int(gr.__version__.split('.')[0]) == 6, 'Expected Gradio 6.x'
assert callable(app.build)
assert core.normalized_url('https://example.org/')
assert not core.public_url('http://127.0.0.1/')

ui = app.build()  # Catch Chatbot/Gradio API incompatibilities before a 10-minute build.
assert ui is not None
print('PREFLIGHT OK: Python, Gradio', gr.__version__, 'and Chelsy UI')
