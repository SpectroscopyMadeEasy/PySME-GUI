"""Accept connections from this user only (Linux).

The server listens on 127.0.0.1, which every user of a computer can reach, and
the access token is what keeps the others out. But the token can be seen: a web
browser is started with the address on its command line, which other users of a
shared Linux computer may be able to list. Linux tells which user holds each end
of a local connection, so there a connection of another user is refused before
anything is read from it, token or not.

A connection forwarded by ssh to your own account counts as yours. Where the
system does not tell, as on macOS and Windows, only the token protects.
"""

import os
import socket
import sys
import time

# The second lists IPv6 sockets, which also carry connections to 127.0.0.1.
TABLES = ("/proc/net/tcp", "/proc/net/tcp6")
# A table that changes while it is read can leave a line out: look again.
ATTEMPTS = 3


def encoded(address, port, ipv6=False):
    """An IPv4 address and port as these tables write them."""
    packed = socket.inet_aton(address)
    if ipv6:
        packed = bytes(10) + b"\xff\xff" + packed
    words = (packed[i:i + 4] for i in range(0, len(packed), 4))
    return "".join(f"{int.from_bytes(word, sys.byteorder):08X}" for word in words) + f":{port:04X}"


def owner(client, server, tables=TABLES):
    """The user id of the client's end of a connection to ``server``.

    ``client`` and ``server`` are (address, port). None if no open socket is
    listed for it. OSError if the first table cannot be read.
    """
    for number, path in enumerate(tables):
        wanted = [encoded(*client, ipv6=number > 0), encoded(*server, ipv6=number > 0)]
        try:
            with open(path, encoding="ascii") as handle:
                lines = handle.readlines()[1:]
        except OSError:
            if number == 0:
                raise
            continue                      # a system without IPv6
        for line in lines:
            fields = line.split()
            # Fields: 1 local, 2 remote, 7 user, 9 inode. A socket that was
            # already closed has no inode, and is listed as root's.
            if len(fields) > 9 and fields[1:3] == wanted and fields[9] != "0":
                return int(fields[7])
    return None


def can_tell():
    """True if this system names the user of a connection correctly.

    Tried on a connection of our own, so that a system that lists nothing, or
    something else, does not shut its own user out.
    """
    if not sys.platform.startswith("linux"):
        return False
    try:
        with socket.create_server(("127.0.0.1", 0)) as listener:
            with socket.create_connection(listener.getsockname(), timeout=5) as client:
                accepted, _ = listener.accept()
                with accepted:
                    return any(
                        owner(client.getsockname(), listener.getsockname()) == os.getuid()
                        for _ in range(ATTEMPTS)
                    )
    except OSError:
        return False


def is_this_user(client, server):
    """True only if the client's end of a connection is this user's.

    For use where :func:`can_tell` is true: whatever cannot be seen is refused.
    """
    for attempt in range(ATTEMPTS):
        try:
            found = owner(client, server)
        except OSError:
            found = None
        if found is not None:
            return found == os.getuid()
        if attempt + 1 < ATTEMPTS:
            time.sleep(0.02)
    return False
