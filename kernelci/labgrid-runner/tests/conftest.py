"""Pytest configuration for labgrid-runner tests."""

import sys
from pathlib import Path

import pytest

# Add the package to sys.path for imports
package_dir = Path(__file__).parent.parent
if str(package_dir) not in sys.path:
    sys.path.insert(0, str(package_dir))


@pytest.fixture(autouse=True)
def reset_settings():
    """Reset settings between tests."""
    # This ensures each test starts with fresh settings
    yield
