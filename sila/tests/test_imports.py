"""
Import smoke tests — one test per module.

Each test does nothing except import the module. A NameError, ImportError,
or any other exception raised at module load time (missing import, typo in
a class body, bad default value, etc.) will fail exactly the test for that
module, making the root cause immediately obvious.
"""

import importlib

import pytest


MODULES = [
    "sila.security",
    "sila.models.step",
    "sila.models.project",
    "sila.engine.audio_loader",
    "sila.engine.sequencer",
    "sila.engine.sampler",
    "sila.engine.lfo",
    "sila.engine.fx",
    "sila.engine.audio",
    "sila.engine.clock",
    "sila.export.digitakt",
    "sila.storage.project_store",
    "sila.api.routes",
    "sila.main",
]


@pytest.mark.parametrize("module", MODULES)
def test_module_imports(module):
    importlib.import_module(module)
