"""Shim so ``pip install -e .`` works when the build env has an old setuptools.

Project metadata lives in ``pyproject.toml``; setuptools loads it automatically.
"""

from setuptools import setup

setup()
