"""Shim so older pip/setuptools can do `pip install -e .`.
Real metadata lives in pyproject.toml."""
from setuptools import setup

setup()
