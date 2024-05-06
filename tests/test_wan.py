def check_download(
    command,
    url,
    expect_stdout=None,
    expect_stderr=None,
    expect_exitcode=0,
    expect_content=None,
    remove=True,
    filename="index.html",
):
    stdout, stderr, exitcode = command.run(f"wget {url} -O {filename}")
    if expect_stdout:
        found = False
        for line in output:
            if expect_stdout in line:
                found = True
                break
        assert found, f"Expected output '{expect_stdout}' not found in {stdout}"

    if expect_stderr:
        found = False
        for line in stderr:
            if expect_stderr in line:
                found = True
                break
        assert found, f"Expected error '{expect_stderr}' not found in {stderr}"

    assert (
        expect_exitcode == exitcode
    ), f"Expected exit code {expect_exitcode} not found in {exitcode}"
    if expect_content:
        assert (
            expect_content in command.run(f"cat {url.split('/')[-1]}")[0]
        ), f"Expected content '{expect_content}' not found in {url.split('/')[-1]}"
    if remove:
        command.run(f"rm {filename}")


def test_https_download(ssh_command):
    check_download(
        ssh_command,
        "https://downloads.openwrt.org/releases/21.02.2/targets/armvirt/64/config.buildinfo",
        expect_stderr="Download completed",
        filename="config.buildinfo",
        remove=False,
    )

    assert (
        "26b85383a138594b1197e581bd13c6825c0b6b5f23829870a6dbc5d37ccf6cd8  config.buildinfo"
        in ssh_command.run("sha256sum config.buildinfo")[0]
    )
    ssh_command.run("rm config.buildinfo")


def test_http_download(ssh_command):
    check_download(
        ssh_command,
        "http://http.badssl.com",
        expect_stderr="Download completed",
    )


def test_https_mozilla(ssh_command):
    check_download(
        ssh_command,
        "https://www.mozilla.org/",
        expect_stderr="Download completed",
    )


def test_https_untrusted(ssh_command):
    check_download(
        ssh_command,
        "https://untrusted-root.badssl.com/",
        expect_stderr="Connection error: Invalid SSL certificate",
        expect_exitcode=5,
    )


def test_https_wrong(ssh_command):
    check_download(
        ssh_command,
        "https://wrong.host.badssl.com/",
        expect_stderr="Connection error: Server hostname does not match SSL certificate",
        expect_exitcode=5,
    )


def test_https_expired(ssh_command):
    check_download(
        ssh_command,
        "https://expired.badssl.com/",
        expect_stderr="Connection error: Invalid SSL certificate",
        expect_exitcode=5,
    )


# def test_https_rc4(ssh_command):
#     check_download(
#         ssh_command,
#         "https://rc4.badssl.com/",
#         expect_stderr="Connection error: Connection failed",
#         4,
#     )
