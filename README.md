# OpenWrt Testing

> With great many support devices comes great many tests

OpenWrt Testing is a framework to run tests on OpenWrt devices, emulated or
real. Using [`labgrid`](https://labgrid.readthedocs.io/en/latest/) to control
the devices, the framework offers a simple way to write tests and run them on
different hardware.

## Requirements

* An OpenWrt firmware image
* Python and [`poetry`](https://python-poetry.org/)

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
## Adding tests

The framework uses `pytest` to execute commands and evaluate the output. Test
cases use the two *fixture* `ssh_command` or `shell_command`. The object offers
the function `run(cmd)` and returns *stdout*, *stderr* (SSH only) and the exit
code.

The example below runs `uname -a` and checks that the device is running
*GNU/Linux*

```python
def test_uname(shell_command):
    assert "GNU/Linux" in shell_command.run("uname -a")[0][0]
```
