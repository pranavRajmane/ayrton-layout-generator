import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.registry import REGISTRY  # noqa: E402

REGISTRY.discover("elements")
REGISTRY.discover("tests.fixture_elements")
