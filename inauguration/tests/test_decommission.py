"""The destructive path.

``nss-decommission`` is the one thing in this system that can lose data, so it is
tested from three directions:

* **statically** — the allowlist is a literal, and nothing in it can reach a
  permanent file, a backup or a log;
* **by running it** — against a sandboxed copy of the whole layout, with nginx,
  systemd, curl and id stubbed, asserting the permanent site survives and the
  temporary files do not;
* **by breaking it** — at each phase in turn, asserting that everything not yet
  removed is still there, that the running mechanism survives, and that the
  recovery path is intact.

The sandbox works by rewriting the script's path constants into a temporary root.
The shipped file keeps its absolute paths — it must, or a request could steer it —
and a test below asserts that it has no other way to learn a path.
"""

from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "inauguration" / "deploy" / "sbin" / "nss-decommission"

BASH = shutil.which("bash")

pytestmark = pytest.mark.skipif(BASH is None, reason="bash is required to run the cleanup")


# ---------------------------------------------------------------------------
# Static reading of the shipped script
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def source() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def _array(source: str, name: str) -> list[str]:
    """The literal entries of a `readonly NAME=( ... )` block."""
    match = re.search(rf"readonly {name}=\(\s*(.*?)\s*\)", source, re.S)
    assert match, f"{name} is not declared as a literal array"
    return [line.strip() for line in match.group(1).splitlines() if line.strip()]


def test_the_allowlist_is_exactly_the_expected_set(source: str):
    """Written down by hand, so a new entry has to be added deliberately in review."""
    assert _array(source, "TEMP_FILES") == [
        "/var/www/nss/inauguration.html",
        "/var/www/nss/coming-soon.html",
    ]
    assert _array(source, "TEMP_DIRS") == ["/var/www/nss/admin"]


def test_no_entry_matches_a_permanent_file(source: str):
    permanent = set(_array(source, "PERMANENT_FILES")) | set(_array(source, "PERMANENT_DIRS"))
    allowlist = set(_array(source, "TEMP_FILES")) | set(_array(source, "TEMP_DIRS"))

    assert not (permanent & allowlist), "the allowlist names a permanent path"


def test_nothing_under_backups_or_logs_can_be_removed(source: str):
    """A failed cleanup is exactly when the snapshot has to still exist."""
    for entry in _array(source, "TEMP_FILES") + _array(source, "TEMP_DIRS"):
        assert not entry.startswith("/var/backups/"), entry
        assert not entry.startswith("/var/log/"), entry


def test_the_permanent_asset_list_covers_the_site(source: str):
    for required in ("index.html", "team.html", "nss_logo.jpg"):
        assert any(required in p for p in _array(source, "PERMANENT_FILES"))
    for required in ("css", "js", "vendor", "assets"):
        assert any(p.endswith("/" + required) for p in _array(source, "PERMANENT_DIRS"))


def test_the_script_declares_exactly_the_expected_paths(source: str):
    """Every removable path is a literal declared at the top of this file.

    Enumerated rather than sampled: a new path constant has to be added here too,
    which means it gets read in review. The allowlist arrays are excluded — they
    are checked separately, entry by entry.
    """
    declared = dict(re.findall(r"^readonly ([A-Z0-9_]+)=(?!\()(.*)$", source, re.M))

    assert set(declared) == {
        "PROGRAM",
        "INSTALL_DIR", "CONFIG_DIR", "SITE_JSON", "VENV_DIR",
        "WEB_ROOT", "BACKUP_ROOT", "LOG_DIR", "CLEANUP_LOG",
        "NGINX_INC", "NGINX_SITE", "NGINX_BIN",
        "SERVICE", "UNIT", "SUDOERS", "SELF",
        "SITE_HOST", "CHECK_BASE", "PERMANENT_INCLUDE",
    }, "the path constants changed; review what the script can now reach"

    # The only ones under the web root are the two temporary files, via the
    # allowlist arrays — WEB_ROOT itself is never removed from.
    assert declared["WEB_ROOT"] == "/var/www/nss"
    assert declared["BACKUP_ROOT"].startswith("/var/backups/")
    assert declared["LOG_DIR"].startswith("/var/log/")


def test_there_is_no_variable_path_anywhere_in_a_removal(source: str):
    """The whole security argument.

    A removal may only name a variable that this file itself declares — a path
    constant, a ``local``, a ``mktemp`` result, or the allowlist loop element. No
    substitution of any kind appears in a removal, so there is no expression that
    could carry a path in from outside.
    """
    # Everything this script can name: its own declarations, plus the loop
    # element and the helper's own $0.
    declared = set(re.findall(r"^readonly ([A-Za-z_][A-Za-z0-9_]*)=", source, re.M))
    declared |= set(re.findall(r"^\s*local ([A-Za-z_][A-Za-z0-9_]*)", source, re.M))
    declared |= {"f", "0"}

    removals = [
        line.strip() for line in source.splitlines() if re.search(r"(^|\s)rm\s", line)
    ]
    assert removals, "no removals found; this test has stopped testing anything"

    for line in removals:
        assert "$1" not in line, f"a removal takes an argument: {line}"
        assert "${" not in line, f"a removal interpolates a compound variable: {line}"
        assert "$(" not in line and "`" not in line, f"a removal runs a substitution: {line}"

        for name in re.findall(r"\$([A-Za-z_][A-Za-z0-9_]*)", line):
            assert name in declared, (
                f"a removal uses ${name}, which this script does not declare: {line}"
            )

        # A literal path in a removal must be one of the temporary web files.
        for literal in re.findall(r"/var/www/nss/\S+", line):
            assert literal.strip("\"'") in (
                "/var/www/nss/inauguration.html",
                "/var/www/nss/coming-soon.html",
                "/var/www/nss/admin",
            ), f"a removal names a path outside the allowlist: {literal}"

    # The web-file removals iterate the allowlist arrays and nothing else.
    loops = set(re.findall(r"for f in \"\$\{(TEMP_FILES|TEMP_DIRS)\[@\]\}\"", source))
    assert loops == {"TEMP_FILES", "TEMP_DIRS"}, (
        "the web-file removals do not iterate both allowlist arrays"
    )

    # Nothing under the web root is removed except by that loop.
    for line in removals:
        if "/var/www/nss" in line:
            assert "$f" in line or line.startswith("rm -f -- /var/www/nss/"), line


def test_the_script_runs_no_find_and_no_shell(source: str):
    assert "find /" not in source, "a recursive find over an absolute root"
    assert "sh -c" not in source
    assert "eval " not in source
    assert "os.system" not in source


def test_the_script_takes_no_arguments(source: str):
    assert "require_no_arguments" in source
    assert re.search(r'\$#"?\s*-ne|"\$#" -ne', source), "no argument count check"


def test_the_permanent_include_matches_the_application(source: str):
    """Reproduced in the script so cleanup never needs the app to render it, and
    pinned here so the two cannot drift apart."""
    sys.path.insert(0, str(REPO / "inauguration" / "backend"))
    from app.nginx_mode import render_include

    match = re.search(r"readonly PERMANENT_INCLUDE='(.*?)'\n", source, re.S)
    assert match, "PERMANENT_INCLUDE is not a single-quoted literal"

    # write_include appends exactly one newline via printf '%s\n'.
    assert match.group(1) + "\n" == render_include("permanent")


def test_self_removal_waits_for_the_script_to_exit(source: str):
    """The mechanism must not delete itself mid-run."""
    assert "phase_6_self_removal" in source
    assert "kill -0" in source, "the helper does not wait for this pid"
    assert "setsid" in source or "nohup" in source


# ---------------------------------------------------------------------------
# The sandbox
# ---------------------------------------------------------------------------

REWRITES = (
    ("/usr/local/sbin/nss-decommission", "{r}/sbin/nss-decommission"),
    ("/etc/systemd/system/nss-inauguration.service", "{r}/etc/nss-inauguration.service"),
    ("/etc/sudoers.d/nss-inauguration", "{r}/etc/nss-inauguration.sudoers"),
    ("/etc/nginx/sites-available/nss", "{r}/etc/nss-site"),
    ("/etc/nginx/nss-mode.inc", "{r}/etc/nss-mode.inc"),
    ("/var/backups/nss-inauguration", "{r}/backups"),
    ("/var/log/nss-inauguration", "{r}/log"),
    ("/opt/nss-inauguration", "{r}/opt"),
    ("/var/www/nss", "{r}/web"),
    ("/usr/sbin/nginx", "{r}/stub/nginx"),
)


class Sandbox:
    """A whole production layout in a temporary directory, plus stubbed binaries."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.web = root / "web"
        self.opt = root / "opt"
        self.backups = root / "backups"
        self.log = root / "log"
        self.etc = root / "etc"
        self.stub = root / "stub"
        self.probe = root / "probe.log"
        self.script = root / "sbin" / "nss-decommission"
        for d in (self.web, self.opt, self.backups, self.log, self.etc, self.stub, root / "sbin"):
            d.mkdir(parents=True, exist_ok=True)

    # -- layout ------------------------------------------------------------

    @staticmethod
    def _write(path: Path, text: str) -> None:
        """Write the way Linux would: LF, never the host's CRLF.

        The script reads a file, holds it in a shell variable and writes it back, and
        that round trip preserves content but not line endings. On the VM every file
        is LF so nothing is lost; a sandbox written with the host's CRLF would fail
        the byte comparisons for a reason that cannot happen in production.
        """
        path.write_text(text, encoding="utf-8", newline="\n")

    def build_site(self) -> None:
        (self.web / "css").mkdir(exist_ok=True)
        (self.web / "js").mkdir(exist_ok=True)
        (self.web / "vendor").mkdir(exist_ok=True)
        (self.web / "assets").mkdir(exist_ok=True)
        (self.web / "admin").mkdir(exist_ok=True)

        self._write(
            self.web / "index.html",
            "<html><head><title>NSS IIIT Naya Raipur</title></head><body>permanent home</body></html>",
        )
        self._write(self.web / "team.html", "<html>permanent team</html>")
        (self.web / "nss_logo.jpg").write_bytes(b"\xff\xd8\xff\xe0jpeg")
        self._write(self.web / "css" / "style.css", ":root{}")
        self._write(self.web / "js" / "main.js", "// main")
        self._write(self.web / "admin" / "index.html", "<html>portal</html>")
        self._write(self.web / "inauguration.html", "<html>ceremony</html>")
        self._write(self.web / "coming-soon.html", "<html>hold</html>")

    def build_installation(self) -> None:
        (self.opt / "config").mkdir(parents=True, exist_ok=True)
        (self.opt / "venv" / "bin").mkdir(parents=True, exist_ok=True)
        self._write(
            self.opt / "config" / "site.json",
            '{"site_mode": "permanent", "inauguration_completed": true,'
            ' "scheduled_at": "2026-10-02T10:15:00+05:30"}',
        )
        self._write(self.opt / "config" / "admin.json", '{"users": []}')

        # The state reader shells out to the venv's python. Point it at the real one.
        shim = self.opt / "venv" / "bin" / "python"
        self._write(shim, f'#!/bin/bash\nexec "{sys.executable}" "$@"\n')
        self._make_executable(shim)

    def build_nginx(self, include: str | None = None) -> None:
        self._write(
            self.etc / "nss-mode.inc",
            include if include is not None
            else "location ~* \\.html$ { return 302 /coming-soon.html; }\n",
        )
        self._write(self.etc / "nss-site", "server { listen 80; }\n")
        self._write(self.etc / "nss-inauguration.service", "[Unit]\nDescription=nss\n")
        self._write(
            self.etc / "nss-inauguration.sudoers",
            "nss-inaug ALL=(root) NOPASSWD: /usr/local/sbin/nss-decommission\n",
        )

    # -- stubs -------------------------------------------------------------

    def _make_executable(self, path: Path) -> None:
        os.chmod(path, 0o755)
        # MSYS decides `-x` partly on the extension, so make the intent explicit.
        if os.name == "nt":
            with open(path, "ab") as fh:
                fh.write(b"")

    def _write_stub(self, name: str, body: str) -> Path:
        path = self.stub / name
        path.write_text("#!/bin/bash\n" + body, encoding="utf-8", newline="\n")
        self._make_executable(path)
        return path

    def build_stubs(self) -> None:
        web = self.web.as_posix()

        self._write_stub("id", 'echo 0\n')

        self._write_stub(
            "systemctl",
            f'echo "systemctl $*" >> "{self.probe.as_posix()}"\nexit 0\n',
        )

        self._write_stub(
            "nginx",
            f'echo "nginx $*" >> "{self.probe.as_posix()}"\n'
            f'if [ "$1" = "-t" ] && [ -f "{self.stub.as_posix()}/fail-test" ]; then\n'
            '  echo "nginx: configuration file test failed" >&2; exit 1\n'
            "fi\n"
            f'if [ "$1" = "-s" ] && [ -f "{self.stub.as_posix()}/fail-reload" ]; then\n'
            '  echo "nginx: reload failed" >&2; exit 1\n'
            "fi\n"
            "exit 0\n",
        )

        # A curl that answers the way the permanent site would: / is index.html,
        # the assets are present, and the portal is gone.
        self._write_stub(
            "curl",
            "url=''; out=''; status_mode=0\n"
            "while [ $# -gt 0 ]; do\n"
            "  case \"$1\" in\n"
            "    -o) out=\"$2\"; shift 2;;\n"
            "    -w) status_mode=1; shift 2;;\n"
            "    -H|--max-time) shift 2;;\n"
            "    -s) shift;;\n"
            "    *) url=\"$1\"; shift;;\n"
            "  esac\n"
            "done\n"
            "path=$(printf '%s' \"$url\" | sed 's#^[a-z]*://[^/]*##')\n"
            f'if [ -f "{self.stub.as_posix()}/http-broken" ]; then\n'
            "  case \"$path\" in /) status=500;; *) status=404;; esac\n"
            "else\n"
            "  case \"$path\" in\n"
            "    /) status=200;;\n"
            "    /nss_logo.jpg|/css/style.css|/js/main.js) status=200;;\n"
            "    *) status=404;;\n"
            "  esac\n"
            "fi\n"
            f'exists=0; [ -d "{self.opt.as_posix()}" ] && exists=1\n'
            f'echo "curl $path $status install_exists=$exists" >> "{self.probe.as_posix()}"\n'
            "if [ \"$status_mode\" = 1 ]; then printf '%s' \"$status\"; exit 0; fi\n"
            "if [ \"$status\" = 200 ] && [ \"$path\" = / ]; then\n"
            f'  if [ -n "$out" ]; then cat "{web}/index.html" > "$out"; else cat "{web}/index.html"; fi\n'
            "  exit 0\n"
            "fi\n"
            "[ -n \"$out\" ] && : > \"$out\"\n"
            "exit 0\n",
        )

    # -- the script under test --------------------------------------------

    def build_script(self) -> Path:
        text = SCRIPT.read_text(encoding="utf-8")
        root = self.root.as_posix()
        for old, new in REWRITES:
            text = text.replace(old, new.format(r=root))
        self.script.write_text(text, encoding="utf-8", newline="\n")
        self._make_executable(self.script)
        return self.script

    def build(self) -> "Sandbox":
        self.build_site()
        self.build_installation()
        self.build_nginx()
        self.build_stubs()
        self.build_script()
        return self

    # -- running -----------------------------------------------------------

    def run(self, *args: str) -> subprocess.CompletedProcess:
        env = dict(os.environ)
        env["PATH"] = self.stub.as_posix() + os.pathsep + env.get("PATH", "")
        # The real script is invoked by sudo with an empty argv and never reads stdin.
        # Closing it means a child that blocks on input fails here instead of hanging.
        return subprocess.run(
            [BASH, str(self.script), *args],
            capture_output=True, text=True, env=env, timeout=120,
            stdin=subprocess.DEVNULL,
        )

    # -- assertions --------------------------------------------------------

    def permanent_files(self) -> list[Path]:
        return [
            self.web / "index.html",
            self.web / "team.html",
            self.web / "nss_logo.jpg",
            self.web / "css" / "style.css",
            self.web / "js" / "main.js",
        ]

    def assert_permanent_site_survived(self) -> None:
        for path in self.permanent_files():
            assert path.is_file() and path.stat().st_size > 0, f"{path} is gone"
        for name in ("css", "js", "vendor", "assets"):
            assert (self.web / name).is_dir(), f"{name}/ is gone"

    def probe_lines(self) -> list[str]:
        if not self.probe.exists():
            return []
        return [ln for ln in self.probe.read_text(encoding="utf-8").splitlines() if ln.strip()]


@pytest.fixture
def sandbox(tmp_path: Path) -> Sandbox:
    return Sandbox(tmp_path / "root").build()


# ---------------------------------------------------------------------------
# A successful run
# ---------------------------------------------------------------------------


def test_a_clean_run_removes_the_temporary_files_and_keeps_the_site(sandbox: Sandbox):
    result = sandbox.run()

    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"

    sandbox.assert_permanent_site_survived()
    assert not (sandbox.web / "inauguration.html").exists()
    assert not (sandbox.web / "coming-soon.html").exists()
    assert not (sandbox.web / "admin").exists()

    # The backend goes last, and goes.
    assert not sandbox.opt.exists()


def test_a_clean_run_writes_a_verified_backup(sandbox: Sandbox):
    assert sandbox.run().returncode == 0

    backups = list(sandbox.backups.glob("cleanup-*"))
    assert len(backups) == 1, "the run did not leave exactly one snapshot"

    snapshot = backups[0]
    assert (snapshot / "MANIFEST.sha256").is_file()
    assert (snapshot / "nss-mode.inc").is_file()
    assert (snapshot / "config" / "site.json").is_file()
    assert (snapshot / "web" / "inauguration.html").is_file()


def test_a_clean_run_records_what_it_did(sandbox: Sandbox):
    assert sandbox.run().returncode == 0

    log = (sandbox.log / "cleanup.log").read_text(encoding="utf-8")
    assert "phase=phase 2" in log
    assert "phase=phase 4" in log
    assert "cleanup completed successfully" in log
    assert "/var/www/nss/inauguration.html" in log or "inauguration.html" in log


def test_the_routing_becomes_the_permanent_include(sandbox: Sandbox):
    assert sandbox.run().returncode == 0

    sys.path.insert(0, str(REPO / "inauguration" / "backend"))
    from app.nginx_mode import render_include

    assert (sandbox.etc / "nss-mode.inc").read_text(encoding="utf-8") == render_include("permanent")


def test_the_backend_is_removed_only_after_the_site_verifies_over_http(sandbox: Sandbox):
    """The ordering rule, proved from the instrumented probes rather than asserted."""
    assert sandbox.run().returncode == 0

    lines = sandbox.probe_lines()
    reload_at = next(i for i, ln in enumerate(lines) if ln.startswith("nginx -s reload"))
    verified_at = next(
        i for i, ln in enumerate(lines) if ln.startswith("curl / 200") and i > reload_at
    )

    assert "install_exists=1" in lines[verified_at], (
        "the site was verified only after the backend had already been removed"
    )
    # ...and the stop came after that verification, not before it.
    stop_at = next(i for i, ln in enumerate(lines) if "systemctl stop" in ln)
    assert stop_at > verified_at


def test_running_it_twice_is_a_clean_no_op(sandbox: Sandbox):
    assert sandbox.run().returncode == 0
    after_first = {
        p: p.read_bytes() for p in sandbox.permanent_files()
    }

    second = sandbox.run()
    assert second.returncode == 0, f"{second.stdout}\n{second.stderr}"
    assert "already gone" in second.stdout or "Nothing to do" in second.stdout

    sandbox.assert_permanent_site_survived()
    for path, before in after_first.items():
        assert path.read_bytes() == before


def test_it_refuses_to_take_arguments(sandbox: Sandbox):
    result = sandbox.run("/etc/passwd")

    assert result.returncode != 0
    assert "no arguments" in result.stdout
    sandbox.assert_permanent_site_survived()
    assert (sandbox.web / "inauguration.html").exists(), "it acted despite refusing"


# ---------------------------------------------------------------------------
# Failure at each phase
# ---------------------------------------------------------------------------


def test_a_failed_nginx_test_changes_nothing_at_all(sandbox: Sandbox):
    before = (sandbox.etc / "nss-mode.inc").read_bytes()
    (sandbox.stub / "fail-test").write_text("", encoding="utf-8")

    result = sandbox.run()
    assert result.returncode != 0

    sandbox.assert_permanent_site_survived()
    assert (sandbox.web / "inauguration.html").exists()
    assert (sandbox.web / "admin").is_dir()
    assert sandbox.opt.is_dir(), "the backend was removed despite phase 2 failing"
    assert (sandbox.etc / "nss-mode.inc").read_bytes() == before, "the include was left rewritten"


def test_a_failed_reload_puts_the_previous_routing_back(sandbox: Sandbox):
    before = (sandbox.etc / "nss-mode.inc").read_bytes()
    (sandbox.stub / "fail-reload").write_text("", encoding="utf-8")

    result = sandbox.run()
    assert result.returncode != 0

    assert (sandbox.etc / "nss-mode.inc").read_bytes() == before
    assert sandbox.opt.is_dir()
    sandbox.assert_permanent_site_survived()
    assert (sandbox.web / "inauguration.html").exists()


def test_a_site_that_will_not_serve_stops_everything_after_the_backup(sandbox: Sandbox):
    """Phase 2's HTTP check is what gates the destructive phases."""
    (sandbox.stub / "http-broken").write_text("", encoding="utf-8")

    result = sandbox.run()
    assert result.returncode != 0

    sandbox.assert_permanent_site_survived()
    assert (sandbox.web / "inauguration.html").exists(), "web files were removed anyway"
    assert sandbox.opt.is_dir(), "the backend was removed anyway"
    assert list(sandbox.backups.glob("cleanup-*")), "no snapshot was taken"


def test_every_failure_preserves_the_snapshot_and_the_log(sandbox: Sandbox):
    for stub in ("fail-test", "fail-reload", "http-broken"):
        (sandbox.stub / stub).write_text("", encoding="utf-8")
        result = sandbox.run()
        assert result.returncode != 0, f"{stub}: the run unexpectedly succeeded"

        snapshots = list(sandbox.backups.glob("cleanup-*"))
        assert snapshots, f"{stub}: no snapshot survived"
        assert (snapshots[0] / "MANIFEST.sha256").is_file()
        assert (sandbox.log / "cleanup.log").is_file()
        assert "FAILED" in (sandbox.log / "cleanup.log").read_text(encoding="utf-8")

        (sandbox.stub / stub).unlink()


def test_the_failure_log_names_the_phase_that_failed(sandbox: Sandbox):
    (sandbox.stub / "fail-test").write_text("", encoding="utf-8")
    assert sandbox.run().returncode != 0

    log = (sandbox.log / "cleanup.log").read_text(encoding="utf-8")
    assert "phase=phase 2" in log
    assert "restored" in log


def test_it_refuses_before_touching_anything_when_the_ceremony_is_not_complete(sandbox: Sandbox):
    (sandbox.opt / "config" / "site.json").write_text(
        '{"site_mode": "inauguration", "inauguration_completed": false}', encoding="utf-8"
    )

    result = sandbox.run()
    assert result.returncode != 0
    assert "not marked complete" in result.stdout

    sandbox.assert_permanent_site_survived()
    assert (sandbox.web / "inauguration.html").exists()
    assert not list(sandbox.backups.glob("cleanup-*")), "it took a snapshot anyway"


def test_it_refuses_when_a_permanent_file_is_missing(sandbox: Sandbox):
    """The pre-flight check that stops a cleanup running against a broken site."""
    (sandbox.web / "team.html").unlink()

    result = sandbox.run()
    assert result.returncode != 0
    assert "permanent file missing" in result.stdout

    assert (sandbox.web / "inauguration.html").exists()
    assert sandbox.opt.is_dir()


# ---------------------------------------------------------------------------
# The recovery path after a failure
# ---------------------------------------------------------------------------


def test_the_snapshot_can_put_the_inauguration_system_back(sandbox: Sandbox):
    """A failure has to leave the operator able to undo, not just to stare."""
    (sandbox.stub / "fail-test").write_text("", encoding="utf-8")
    assert sandbox.run().returncode != 0

    snapshot = next(sandbox.backups.glob("cleanup-*"))

    # The snapshot holds the state, the routing, the site block and the ceremony
    # page — everything needed to serve the event again.
    assert (snapshot / "config" / "site.json").is_file()
    assert (snapshot / "nss-mode.inc").is_file()
    assert (snapshot / "nss-site").is_file()
    assert (snapshot / "web" / "inauguration.html").is_file()
    assert (snapshot / "web" / "admin" / "index.html").is_file()


def test_checksums_in_the_snapshot_verify(sandbox: Sandbox):
    assert sandbox.run().returncode == 0
    snapshot = next(sandbox.backups.glob("cleanup-*"))

    checked = subprocess.run(
        [BASH, "-c", "sha256sum --check --quiet MANIFEST.sha256"],
        cwd=snapshot, capture_output=True, text=True,
    )
    assert checked.returncode == 0, checked.stderr
