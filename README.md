# OpenWrt Testing

> With great many support devices comes great many tests

OpenWrt Testing is a framework to run tests on OpenWrt devices, emulated or
real. Using [`labgrid`](https://labgrid.readthedocs.io/en/latest/) to control
the devices, the framework offers a simple way to write tests and run them on
different hardware.

## Requirements

- An OpenWrt firmware image
- Python and [`poetry`](https://python-poetry.org/)

## Setup

```shell
pip install -U poetry # optional
poetry install
```

## Run tests

Use this command to run tests on `malta/be` image:

```shell
pytest tests/ \
    --lg-env tests/qemu.yaml \
    --lg-log \
    --lg-colored-steps \
    --target malta-be \
    --firmware ../../openwrt/bin/targets/malta/be/openwrt-malta-be-vmlinux-initramfs.elf
```

### Using the Makefile

The Makefile offers a simple way to run tests directly from the `openwrt.git` repository:

First create a link of this repositories `Makefile` to the `openwrt.git` repository:

```shell
ln -s $(pwd)/Makefile /path/to/openwrt.git/Makefile.tests
```

Second, make sure that `Makefile.tests` is included in the `openwrt.git` repository. Add the following line to the top of `openwrt.git/Makefile`:

```
include Makefile.tests
```

Then run the tests:

```shell
cd /path/to/openwrt.git
make test-x86-64
```

## Adding tests

The framework uses `pytest` to execute commands and evaluate the output. Test
cases use the two _fixture_ `ssh_command` or `shell_command`. The object offers
the function `run(cmd)` and returns _stdout_, _stderr_ (SSH only) and the exit
code.

The example below runs `uname -a` and checks that the device is running
_GNU/Linux_

```python
def test_uname(shell_command):
    assert "GNU/Linux" in shell_command.run("uname -a")[0][0]
```
