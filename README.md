# OpenWrt Testing

> With great many support devices comes great many tests

OpenWrt Testing is a framework to run tests on OpenWrt devices, emulated or
real. Using [`labgrid`](https://labgrid.readthedocs.io/en/latest/) to control
the devices, the framework offers a simple way to write tests and run them on
different hardware.

## Requirements

- An OpenWrt firmware image
- Python and [`poetry`](https://python-poetry.org/)
- QEMU
- bats (>1.5.0)


## Setup

For maximum convenience, clone the repository inside the `openwrt.git`
repository as `tests/`:

```shell
cd /path/to/openwrt.git/
git clone https://github.com/aparcar/openwrt-tests.git tests/
```

Install required packages to use Labgrid, QEMU and bats:

```shell
sudo apt-get update
sudo apt-get -y install \
    python3-poetry \
    qemu-system-mips \
    qemu-system-x86 \
    qemu-system-aarch64 \
    make \
    bats
poetry install
```

Verify the installation by running the tests:

```shell
make tests/setup V=s
```

## Running tests

You can run tests via the Makefile or directly using `pytest`. Shell tests with
`bats` must be executed from the right directory for it to find the shell
scripts to test, i.e. the `openwrt.git` directory.

### Using the Makefile

You can start runtime and shell tests via the Makefile.
#### Runtime tests

```shell
cd /path/to/openwrt.git
make tests/x86-64 V=s
```

#### Shell tests

```shell
cd /path/to/openwrt.git
make tests/shell V=s
```

### Standalone usage

If you don't plan to clone this repository inside the `openwrt.git` repository,
you can still run the tests. Use this command to run tests on `malta/be` image:

```shell
pytest tests/ \
    --lg-env targets/qemu-malta-be.yaml \
    --lg-log \
    --lg-colored-steps \
    --firmware ../../openwrt/bin/targets/malta/be/openwrt-malta-be-vmlinux-initramfs.elf
```

## Writing tests

There are runtime tests and shell tests. Runtime tests are executed on the
device or QEMU and shell tests are executed on the host using the `bats`
framework.

### Runtime tests

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

### Shell tests

In the `tests/bats` directory, create a new file with the test cases. The
filename must end with `.bats` and should be located in the same directory as
it's corresponding shell script inside `openwrt.git`. For example, the file
`package/base-files/files/lib/functions.sh` should have a corresponding test
file `tests/bats/package/base-files/files/lib/functions.sh.bats`.

The `bats` framework offers a `setup()` function to run before each test case,
just like a `teardown()` to cleanup. Each test case must start with `@test` and
should have a description of the test case. Below is an example of a test case
for the `append` function in `functions.sh`:

```shell
#!/usr/bin/env bats

setup() {
    . $(pwd)/package/base-files/files/lib/functions.sh
}

@test "test append" {
    VAR="a b"
    append VAR "c"
    [ "$VAR" = "a b c" ]
}
```
