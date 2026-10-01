"""NSS IIIT-NR inauguration system.

The public website is plain static files served by Nginx. This package is the small
administration layer that sits beside it for the duration of the launch: it owns the
site-state document, the admin session, and the one privileged operation
(decommissioning) — and nothing else. See ``inauguration/README.md``.
"""

__version__ = "1.0.0"
