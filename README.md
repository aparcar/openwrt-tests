# OpenWrt Testing

> With great many support devices comes great many tests

OpenWrt Testing is a framework to run tests on OpenWrt devices, emulated or
real. Using [`labgrid`](https://labgrid.readthedocs.io/en/latest/) to control
the devices, the framework offers a simple way to write tests and run them on
different hardware.

## Requirements

- An OpenWrt firmware image
- Python and [`uv`](https://docs.astral.sh/uv/)
- QEMU


## Setup

For maximum convenience, clone the repository inside the `openwrt.git`
repository as `tests/`:

```shell
cd /path/to/openwrt.git/
git clone https://github.com/aparcar/openwrt-tests.git tests/
```

Install required packages to use Labgrid and QEMU:

```shell
curl -LsSf https://astral.sh/uv/install.sh | sh

sudo apt-get update
sudo apt-get -y install \
    qemu-system-mips \
    qemu-system-x86 \
    qemu-system-aarch64 \
    make
```

Verify the installation by running the tests:

```shell
make tests/setup V=s
```

## Running tests

You can run tests via the Makefile or directly using `pytest`.

### Using the Makefile

You can start runtime and shell tests via the Makefile.

#### Runtime tests

```shell
cd /path/to/openwrt.git
make tests/x86-64 V=s
```

### Standalone usage

If you don't plan to clone this repository inside the `openwrt.git` repository,
you can still run the tests. Use this command to run tests on `malta/be` image:

```shell
pytest tests/ \
    --lg-env targets/qemu-malta-be.yaml \
    --lg-log \
    --log-cli-level=CONSOLE \
    --lg-colored-steps \
    --firmware ../../openwrt/bin/targets/malta/be/openwrt-malta-be-vmlinux-initramfs.elf
```

## Writing tests

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
