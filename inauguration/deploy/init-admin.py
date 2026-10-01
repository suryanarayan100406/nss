#!/usr/bin/env python3
"""Create the admin account, once, on the server.

There is no public registration route and there is no signup page. The account that
can complete the inauguration and destroy the inauguration system is created here, by
whoever is already root on the machine, and by nothing else.

    sudo /opt/nss-inauguration/venv/bin/python \
        /opt/nss-inauguration/deploy/init-admin.py

The password is read from ``NSS_ADMIN_PASSWORD`` if it is set, and otherwise prompted
for twice with no echo. It is never written to a log, never passed on a command line
(where `ps` would show it to every user on the box), and never stored in the clear —
``AdminStore`` holds an argon2 hash.

Refuses to overwrite an existing account unless ``--force`` is given, because the
alternative is that a re-run silently locks the operator out of a ceremony that is
about to start.
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.auth import AdminStore, AuthError  # noqa: E402
from app.config import Settings  # noqa: E402

MIN_PASSWORD_LENGTH = 12


def read_password() -> str:
    from_env = os.environ.get("NSS_ADMIN_PASSWORD")
    if from_env:
        # Deliberately not echoed back or confirmed: this path exists so the account
        # can be created non-interactively from a provisioning script.
        return from_env

    if not sys.stdin.isatty():
        raise SystemExit(
            "no terminal to prompt on. Set NSS_ADMIN_PASSWORD to create the account "
            "non-interactively, or run this with a tty."
        )

    first = getpass.getpass("Password for the admin account: ")
    second = getpass.getpass("Repeat it: ")
    if first != second:
        raise SystemExit("the two passwords do not match; nothing was created")
    return first


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Create the NSS inauguration admin account.")
    parser.add_argument("--username", default="nss-admin", help="default: nss-admin")
    parser.add_argument(
        "--force", action="store_true",
        help="replace an existing account (invalidates every live session)",
    )
    args = parser.parse_args(argv)

    settings = Settings.from_env()
    store = AdminStore(settings)

    if not args.username.strip():
        raise SystemExit("the username cannot be empty")

    if store.exists() and not args.force:
        # Being explicit about the way out, because the person reading this is
        # probably mid-incident and does not want to go looking for it.
        print(f"An admin account already exists at {settings.admin_json}.", file=sys.stderr)
        print("Nothing was changed. Use --force to replace it.", file=sys.stderr)
        print("To rotate the password without touching the username, use --force and", file=sys.stderr)
        print("re-enter the same name. Every live session is invalidated either way.", file=sys.stderr)
        return 1

    password = read_password()
    if len(password) < MIN_PASSWORD_LENGTH:
        raise SystemExit(
            f"the password must be at least {MIN_PASSWORD_LENGTH} characters; nothing was created"
        )

    # config/ is owned by the service account and is where the state, the account and
    # the session key live. Creating it here means this script works on a machine
    # where the service has not started yet.
    settings.config_dir.mkdir(parents=True, exist_ok=True)

    try:
        store.create(args.username, password)
    except AuthError as exc:
        raise SystemExit(f"could not create the account: {exc}") from exc

    if os.name != "nt":
        # Matches what systemd starts the service as. Without this the service reads
        # the file as root's and cannot, so login fails with no useful error.
        try:
            import grp
            import pwd

            user = pwd.getpwnam("nss-inaug")
            group = grp.getgrnam("nss-inaug")
            os.chown(settings.admin_json, user.pw_uid, group.gr_gid)
            os.chmod(settings.admin_json, 0o640)
        except (KeyError, OSError, ImportError):
            print(
                "note: could not set ownership on "
                f"{settings.admin_json}; check it is readable by the service account",
                file=sys.stderr,
            )

    print(f"Created the admin account {args.username!r} in {settings.admin_json}.")
    print("Sign in at https://nss.iiitnr.ac.in/admin/")
    if store.exists() and args.force:
        print("Any session that was open has been invalidated.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
