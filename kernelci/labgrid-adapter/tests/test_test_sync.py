"""Tests for test repository synchronization."""

import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from labgrid_kci_adapter.test_sync import _run_git, ensure_tests


@pytest.fixture
def temp_dir():
    """Create a temporary directory for tests."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


class TestRunGit:
    """Tests for _run_git helper."""

    @pytest.mark.asyncio
    async def test_run_git_success(self, temp_dir):
        """Test successful git command."""
        # Initialize a git repo
        returncode, output = await _run_git("init", cwd=temp_dir)
        assert returncode == 0
        assert (temp_dir / ".git").exists()

    @pytest.mark.asyncio
    async def test_run_git_failure(self, temp_dir):
        """Test failed git command."""
        returncode, output = await _run_git("clone", "nonexistent-repo", cwd=temp_dir)
        assert returncode != 0


class TestEnsureTests:
    """Tests for ensure_tests function."""

    @pytest.mark.asyncio
    async def test_ensure_tests_local_dir_exists(self, temp_dir):
        """Test using existing local directory when no repo URL."""
        # Create a local tests directory
        tests_dir = temp_dir / "tests"
        tests_dir.mkdir()
        (tests_dir / "test_example.py").write_text("def test_pass(): pass")

        with patch("labgrid_kci_adapter.test_sync.settings") as mock_settings:
            mock_settings.tests_repo_url = ""
            mock_settings.tests_repo_branch = "main"
            mock_settings.tests_repo_subdir = ""
            mock_settings.tests_dir = tests_dir

            result = await ensure_tests()
            assert result == tests_dir

    @pytest.mark.asyncio
    async def test_ensure_tests_local_dir_not_exists(self, temp_dir):
        """Test error when local directory doesn't exist and no repo URL."""
        tests_dir = temp_dir / "nonexistent"

        with patch("labgrid_kci_adapter.test_sync.settings") as mock_settings:
            mock_settings.tests_repo_url = ""
            mock_settings.tests_repo_branch = "main"
            mock_settings.tests_repo_subdir = ""
            mock_settings.tests_dir = tests_dir

            with pytest.raises(RuntimeError, match="does not exist"):
                await ensure_tests()

    @pytest.mark.asyncio
    async def test_ensure_tests_clone_repo(self, temp_dir):
        """Test cloning a new repository."""
        tests_dir = temp_dir / "tests"

        # Create a "remote" repo to clone from
        remote_dir = temp_dir / "remote"
        remote_dir.mkdir()
        await _run_git("init", "--bare", cwd=remote_dir)

        # Create a source repo with content
        source_dir = temp_dir / "source"
        source_dir.mkdir()
        await _run_git("init", cwd=source_dir)
        await _run_git("config", "user.email", "test@test.com", cwd=source_dir)
        await _run_git("config", "user.name", "Test", cwd=source_dir)
        (source_dir / "test.py").write_text("# test")
        await _run_git("add", ".", cwd=source_dir)
        await _run_git("commit", "-m", "initial", cwd=source_dir)
        await _run_git("remote", "add", "origin", str(remote_dir), cwd=source_dir)
        await _run_git("push", "-u", "origin", "master", cwd=source_dir)

        with patch("labgrid_kci_adapter.test_sync.settings") as mock_settings:
            mock_settings.tests_repo_url = str(remote_dir)
            mock_settings.tests_repo_branch = "master"
            mock_settings.tests_repo_subdir = ""
            mock_settings.tests_dir = tests_dir

            result = await ensure_tests()
            assert result == tests_dir
            assert (tests_dir / ".git").exists()
            assert (tests_dir / "test.py").exists()

    @pytest.mark.asyncio
    async def test_ensure_tests_update_existing(self, temp_dir):
        """Test updating an existing cloned repository."""
        tests_dir = temp_dir / "tests"

        # Create a "remote" repo
        remote_dir = temp_dir / "remote"
        remote_dir.mkdir()
        await _run_git("init", "--bare", cwd=remote_dir)

        # Create source and push
        source_dir = temp_dir / "source"
        source_dir.mkdir()
        await _run_git("init", cwd=source_dir)
        await _run_git("config", "user.email", "test@test.com", cwd=source_dir)
        await _run_git("config", "user.name", "Test", cwd=source_dir)
        (source_dir / "test.py").write_text("# v1")
        await _run_git("add", ".", cwd=source_dir)
        await _run_git("commit", "-m", "v1", cwd=source_dir)
        await _run_git("remote", "add", "origin", str(remote_dir), cwd=source_dir)
        await _run_git("push", "-u", "origin", "master", cwd=source_dir)

        # Clone to tests_dir
        await _run_git("clone", str(remote_dir), str(tests_dir))

        # Update source and push
        (source_dir / "test.py").write_text("# v2")
        await _run_git("add", ".", cwd=source_dir)
        await _run_git("commit", "-m", "v2", cwd=source_dir)
        await _run_git("push", cwd=source_dir)

        # Now ensure_tests should update
        with patch("labgrid_kci_adapter.test_sync.settings") as mock_settings:
            mock_settings.tests_repo_url = str(remote_dir)
            mock_settings.tests_repo_branch = "master"
            mock_settings.tests_repo_subdir = ""
            mock_settings.tests_dir = tests_dir

            result = await ensure_tests()
            assert result == tests_dir
            assert (tests_dir / "test.py").read_text() == "# v2"

    @pytest.mark.asyncio
    async def test_ensure_tests_override_params(self, temp_dir):
        """Test that parameters override settings."""
        tests_dir = temp_dir / "custom"
        tests_dir.mkdir()
        (tests_dir / "test.py").write_text("# test")

        with patch("labgrid_kci_adapter.test_sync.settings") as mock_settings:
            mock_settings.tests_repo_url = ""
            mock_settings.tests_repo_branch = "main"
            mock_settings.tests_repo_subdir = ""
            mock_settings.tests_dir = temp_dir / "default"

            # Override with custom dest_dir
            result = await ensure_tests(dest_dir=tests_dir)
            assert result == tests_dir

    @pytest.mark.asyncio
    async def test_ensure_tests_with_subdir(self, temp_dir):
        """Test using subdirectory within repository."""
        tests_dir = temp_dir / "repo"
        tests_dir.mkdir()
        subdir = tests_dir / "tests" / "integration"
        subdir.mkdir(parents=True)
        (subdir / "test_example.py").write_text("def test_pass(): pass")

        with patch("labgrid_kci_adapter.test_sync.settings") as mock_settings:
            mock_settings.tests_repo_url = ""
            mock_settings.tests_repo_branch = "main"
            mock_settings.tests_repo_subdir = "tests/integration"
            mock_settings.tests_dir = tests_dir

            result = await ensure_tests()
            assert result == subdir
            assert (result / "test_example.py").exists()

    @pytest.mark.asyncio
    async def test_ensure_tests_subdir_not_exists(self, temp_dir):
        """Test error when subdirectory doesn't exist."""
        tests_dir = temp_dir / "repo"
        tests_dir.mkdir()
        (tests_dir / "test.py").write_text("# test")

        with patch("labgrid_kci_adapter.test_sync.settings") as mock_settings:
            mock_settings.tests_repo_url = ""
            mock_settings.tests_repo_branch = "main"
            mock_settings.tests_repo_subdir = "nonexistent/subdir"
            mock_settings.tests_dir = tests_dir

            with pytest.raises(RuntimeError, match="does not exist"):
                await ensure_tests()

    @pytest.mark.asyncio
    async def test_ensure_tests_subdir_override(self, temp_dir):
        """Test that subdir parameter overrides settings."""
        tests_dir = temp_dir / "repo"
        tests_dir.mkdir()
        subdir = tests_dir / "custom"
        subdir.mkdir()
        (subdir / "test.py").write_text("# test")

        with patch("labgrid_kci_adapter.test_sync.settings") as mock_settings:
            mock_settings.tests_repo_url = ""
            mock_settings.tests_repo_branch = "main"
            mock_settings.tests_repo_subdir = "default"
            mock_settings.tests_dir = tests_dir

            # Override with custom subdir
            result = await ensure_tests(subdir="custom")
            assert result == subdir

    @pytest.mark.asyncio
    async def test_ensure_tests_clone_with_subdir(self, temp_dir):
        """Test cloning repository with subdirectory."""
        tests_dir = temp_dir / "tests"

        # Create a "remote" repo with subdirectory structure
        remote_dir = temp_dir / "remote"
        remote_dir.mkdir()
        await _run_git("init", "--bare", cwd=remote_dir)

        # Create source with tests in subdirectory
        source_dir = temp_dir / "source"
        source_dir.mkdir()
        await _run_git("init", cwd=source_dir)
        await _run_git("config", "user.email", "test@test.com", cwd=source_dir)
        await _run_git("config", "user.name", "Test", cwd=source_dir)
        (source_dir / "tests").mkdir()
        (source_dir / "tests" / "test.py").write_text("# test")
        await _run_git("add", ".", cwd=source_dir)
        await _run_git("commit", "-m", "initial", cwd=source_dir)
        await _run_git("remote", "add", "origin", str(remote_dir), cwd=source_dir)
        await _run_git("push", "-u", "origin", "master", cwd=source_dir)

        with patch("labgrid_kci_adapter.test_sync.settings") as mock_settings:
            mock_settings.tests_repo_url = str(remote_dir)
            mock_settings.tests_repo_branch = "master"
            mock_settings.tests_repo_subdir = "tests"
            mock_settings.tests_dir = tests_dir

            result = await ensure_tests()
            assert result == tests_dir / "tests"
            assert (result / "test.py").exists()
