import time


def check_https_download(dut, url, expect_str, expect_code=0):
    dut.send_cmd(f"wget {url}", expect_str)
    dut.send_cmd("echo $?", str(expect_code))


def test_download(dut):
    # Wait till network is really available
    # Todo try to find a better way to detect that the WAN is working.
    time.sleep(5)
    # Test a http(s) connection to multiple server and check the results
    check_https_download(
        dut,
        "https://downloads.openwrt.org/releases/21.02.2/targets/armvirt/64/config.buildinfo",
        "Download completed ",
    )

    dut.send_cmd(
        "sha256sum config.buildinfo",
        "26b85383a138594b1197e581bd13c6825c0b6b5f23829870a6dbc5d37ccf6cd8  config.buildinfo",
    )

    dut.send_cmd("rm config.buildinfo")

    check_https_download(
        dut,
        "http://http.badssl.com/",
        "Download completed ",
    )

    dut.send_cmd("rm index.html")

    check_https_download(
        dut,
        "https://letsencrypt.org",
        "Download completed ",
    )

    dut.send_cmd("rm index.html")

    check_https_download(
        dut,
        "https://www.mozilla.org/",
        "Download completed ",
    )

    dut.send_cmd("rm en-US")

    check_https_download(
        dut,
        "https://untrusted-root.badssl.com/",
        "Connection error: Invalid SSL certificate",
        5,
    )

    check_https_download(
        dut,
        "https://wrong.host.badssl.com",
        "Connection error: Server hostname does not match SSL certificate",
        5,
    )

    check_https_download(
        dut,
        "https://expired.badssl.com",
        "Connection error: Invalid SSL certificate",
        5,
    )

    check_https_download(
        dut,
        "https://rc4.badssl.com",
        "Connection error: Connection failed",
        4,
    )
