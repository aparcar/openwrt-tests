# OpenWrt Testing

This allows to run automated tests in OpenWrt.

Currently uses existing firmware images and start them in QEMU. Later it should
also be supported to run the tests on real hardware or in docker containers with
OpenWrt.

## Requirements

* An already build OpenWrt image
* Python 3.8 or more recent

## Setup

Link the folder containing tests into your OpenWrt source folder.

```shell
cd path/to/openwrt.git/
ln -s path/to/openwrt-tests/tests/ ./tests
```

## Run tests

Use this command to run tests on `malta/be` image:

```shell
pytest tests/ --target malta/be
```

## Add tests

The framework uses `pexpect` to execute commands and evaluate the output. Test
cases use a *Pytest Fixture* called `dut`. The object offers the function
`dut.send_cmd(command, expect=None)`. It sends a command to the device and if
`expect` is defined, checks for the specified output.

An example below runs `uname -a` and checks that the device is running
*GNU/Linux*

```python
def test_echo(dut):
    dut.send_cmd("uname -a", "GNU/Linux")
```
