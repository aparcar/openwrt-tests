"""The one place that decides what counts as an error in the kernel log.

Used by test_base.test_kernel_errors and by test_sysupgrade after each
upgrade or reboot. Add new patterns (or harmless messages to ignore) here, so
every check picks them up.
"""

import re

# A line matching any of these is an error.
ERROR_PATTERNS = [
    r" Oops:",  # don't trigger on "ramoops"
    r"(PC is at |pc : )([^+\[ ]+).*",
    r"\bBUG:",  # don't trigger on "DEBUG:"
    r"corruption",
    r"do_page_fault\(\): sending",
    r"EIP: \[<.*>\] ([^+ ]+).*",
    r"epc\s+:\s+\S+\s+([^+ ]+).*",
    r"error.*in",
    r"hung task",
    r"Kernel panic",
    r"Out of memory",
    r"segfault",
    r"stack overflow",
    r"traps:.*general protection",
    r"Unable to handle kernel",
    # Storage and filesystems (flash, SD, the overlay)
    r"SQUASHFS error",
    r"UBIFS error",
    r"jffs2.*(error|CRC)",
    r"EXT4-fs error",
    r"Buffer I/O error",
    # Block I/O errors on reads and writes (op 0x0/0x1)
    r"I/O error, dev \S+, sector \d+ op 0x[01]:",
]

# A line matching any of these is never an error, even if it matches above.
IGNORE_PATTERNS = [
    # Failed WRITE_ZEROES (0x9) or DISCARD (0x3) requests have a fallback. On
    # x86, ext4's lazy inode-table init on a freshly formatted overlay (first
    # boot, sysupgrade -n) sends WRITE_ZEROES to the rootfs_data loop device,
    # which can't pass it down: "I/O error, dev loop0, ... op 0x9:(WRITE_ZEROES)",
    # no data lost.
    r"I/O error, dev \S+, sector \d+ op 0x(?![01]:)[0-9a-f]+:",
]

_ERROR = re.compile("|".join(f"(?:{p})" for p in ERROR_PATTERNS))
_IGNORE = re.compile("|".join(f"(?:{p})" for p in IGNORE_PATTERNS))


def kernel_errors(log):
    """Error lines in a kernel log (a string, or the list of lines run() returns)."""
    lines = log.splitlines() if isinstance(log, str) else log
    return [line for line in lines if _ERROR.search(line) and not _IGNORE.search(line)]
