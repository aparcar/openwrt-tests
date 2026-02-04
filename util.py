import time
from pathlib import Path


def parse_cram_test(
    lines,
    shell="/bin/sh",
    indent=2,
    testname=None,
    env=None,
    cleanenv=True,
    debug=False,
    dos2unix=False,
    escape7bit=False,
):
    indent = b" " * indent
    cmdline = indent + b"$ "
    conline = indent + b"> "
    salt = b"PRYSK%.5f" % time.time()

    lines = lines.splitlines(True) if isinstance(lines, bytes) else lines

    after = {}
    refout, _postout = [], []
    i = pos = prepos = -1
    stdin = []
    for i, line in enumerate(lines):
        # Convert Windows style line endings to UNIX
        if dos2unix and line.endswith(b"\r\n"):
            line = line[:-2] + b"\n"
        elif not line.endswith(b"\n"):
            line += b"\n"
        refout.append(line)
        if line.startswith(cmdline):
            after.setdefault(pos, []).append(line)
            prepos = pos
            pos = i
            stdin.append(b"echo %s %d $?\n" % (salt, i))
            stdin.append(line[len(cmdline) :])
        elif line.startswith(conline):
            after.setdefault(prepos, []).append(line)
            stdin.append(line[len(conline) :])
        elif not line.startswith(indent):
            after.setdefault(pos, []).append(line)
    stdin.append(b"echo %s %d $?\n" % (salt, i + 1))

    print(stdin)
    # return after, refout, postout, stdin


base_test = Path("tests/cram/base.t").read_bytes()
# print(base_test)

print(parse_cram_test(base_test))
