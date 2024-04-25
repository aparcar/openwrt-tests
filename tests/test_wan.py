def check_download(
    command,
    url,
    expect_output=None,
    expect_exitcode=0,
    expect_content=None,
    remove=True,
    filename="index.html",
):
    output, _, exitcode = command.run(f"wget {url} -O {filename}")
    if expect_output:
        found = False
        for line in output:
            if expect_output in line:
                found = True
                break
        assert found, f"Expected output '{expect_output}' not found in {output}"

    assert (
        expect_exitcode == exitcode
    ), f"Expected exit code {expect_exitcode} not found in {exitcode}"
    if expect_content:
        assert (
            expect_content in command.run(f"cat {url.split('/')[-1]}")[0]
        ), f"Expected content '{expect_content}' not found in {url.split('/')[-1]}"
    if remove:
        command.run(f"rm {filename}")


def test_https_download(shell_command):
    check_download(
        shell_command,
        "https://downloads.openwrt.org/releases/21.02.2/targets/armvirt/64/config.buildinfo",
        "Download completed",
        filename="config.buildinfo",
        remove=False,
    )

    assert (
        "26b85383a138594b1197e581bd13c6825c0b6b5f23829870a6dbc5d37ccf6cd8  config.buildinfo"
        in shell_command.run("sha256sum config.buildinfo")[0]
    )
    shell_command.run("rm config.buildinfo")


def test_http_download(shell_command):
    check_download(
        shell_command,
        "http://http.badssl.com",
        "Download completed",
    )


def test_https_mozilla(shell_command):
    check_download(
        shell_command,
        "https://www.mozilla.org/",
        "Download completed",
    )


def test_https_untrusted(shell_command):
    check_download(
        shell_command,
        "https://untrusted-root.badssl.com/",
        "Connection error: Invalid SSL certificate",
        5,
    )


def test_https_wrong(shell_command):
    check_download(
        shell_command,
        "https://wrong.host.badssl.com/",
        "Connection error: Server hostname does not match SSL certificate",
        5,
    )


def test_https_expired(shell_command):
    check_download(
        shell_command,
        "https://expired.badssl.com/",
        "Connection error: Invalid SSL certificate",
        5,
    )


def test_https_rc4(shell_command):
    check_download(
        shell_command,
        "https://rc4.badssl.com/",
        "Connection error: Connection failed",
        4,
    )
