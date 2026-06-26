"""xdl — X (Twitter) video downloader engine.

One `core` (config, ledger, extractor, runner) with two thin entrypoints:
`cli` (local one-shot) and `server` (FastAPI + Huey). Same core in both modes.
"""

__version__ = "0.1.0"
