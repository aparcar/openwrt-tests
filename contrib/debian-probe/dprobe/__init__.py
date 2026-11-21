# SPDX-License-Identifier: LGPL-2.1-only
"""dprobe: the Debian lab probe for openwrt-tests (runs on the probe host).

Every command prints one JSON document on stdout and exits with
0 (ok), 1 (ran, but the expected thing didn't happen: no offer, no RA, ...)
or 2 (error: bad arguments, missing slot, a command failed).
"""

__version__ = "0.1.0"
