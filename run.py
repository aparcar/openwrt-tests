import pytest

if __name__ == "__main__":
    pytest.main(["tests/test_base.py", "-s", "-k", "test_shell"])
