import os

import pytest


class TestApk:
    def test_apk_procd_installed(self, ssh_command):
        assert "procd" in "\n".join(ssh_command.run_check("apk list"))

    @pytest.mark.skipif(
        os.getenv("LG_FEATURE_ONLINE") is None, reason="LG_FEATURE_ONLINE not defined"
    )
    def test_apk_add_ucert(self, ssh_command):
        try:
            ssh_command.run_check("apk add ucert")
            assert "ucert" in "\n".join(ssh_command.run_check("apk list | grep ucert"))
        finally:
            ssh_command.run("apk del ucert")
