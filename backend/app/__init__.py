"""
Process-wide network patch, applied on package import - before any
submodule (data_sources/*) gets a chance to make an HTTP request.

Render (and many other free-tier hosts) containers commonly have no
outbound IPv6 route, but glibc's DNS resolver still returns AAAA (IPv6)
records for hosts that publish one, and Python's default getaddrinfo
behavior tries those first. The result is `[Errno 101] Network is
unreachable` on the very first connection attempt to any such host - not
a timeout, not a rate limit, a routing gap. Observed live against
screener.in while nseindia.com (a different failure mode: a real
connection that then times out) kept working, which is exactly what
you'd expect if only some hosts happen to publish an IPv6 record.

Forcing IPv4-only address resolution for urllib3 (which every plain
`requests` call in this codebase goes through) fixes this at the root
instead of chasing it per-host. curl_cffi (used for Yahoo Finance) has
its own networking stack and is unaffected by this patch either way.
"""
import socket
import urllib3.util.connection as _urllib3_connection


def _force_ipv4_only():
    return socket.AF_INET


_urllib3_connection.allowed_gai_family = _force_ipv4_only
