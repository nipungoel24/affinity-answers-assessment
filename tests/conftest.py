import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).resolve().parent / "fixtures"
sys.path.insert(0, str(ROOT))


def pytest_configure(config):
    config.addinivalue_line("markers", "live: hits the network (mdcomputers.in / Rfam / GitHub); run with LIVE=1")


def pytest_collection_modifyitems(config, items):
    if os.environ.get("LIVE") == "1":
        return
    skip_live = pytest.mark.skip(reason="network test; set LIVE=1 to run")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip_live)
