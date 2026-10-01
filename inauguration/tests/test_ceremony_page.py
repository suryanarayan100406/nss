"""What the ceremony page is allowed to claim, and when.

``inauguration.html`` is one large self-contained page, and three of its behaviours
only ever go wrong in a browser: which page the reveal frames, whether a preview can
be mistaken for a real cut, and whether the credit line survives being relabelled.
None of that is reachable from a route test, so it is asserted against the source
here — deliberately, because each of these was a live bug caught by rendering the
page rather than by the suite.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
PAGE = REPO / "inauguration.html"


@pytest.fixture(scope="module")
def page() -> str:
    return PAGE.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def script(page: str) -> str:
    """The page's own JavaScript, with the markup and CSS left behind."""
    blocks = re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", page, re.S)
    assert blocks, "the ceremony page has no inline script"
    return "\n".join(blocks)


# --- the reveal frame -------------------------------------------------------


def test_the_reveal_frame_is_not_loaded_until_the_server_confirms(script: str):
    """During the ceremony /index.html is redirected back to the ceremony page.

    So a frame pointed at it up front either renders this page inside itself, or —
    for the admin — races the redirect against the nginx reload that ends it. The
    assignment has to come from the committed branch, after the 200.
    """
    assert "liveFrame.src = 'index.html'" in script, "the frame is never pointed at the home page"

    # Exactly one place assigns the source, and it is inside loadLiveFrame.
    assert script.count("liveFrame.src") == 1

    # ...and the only call to it sits in the branch where the server said 200.
    calls = [m.start() for m in re.finditer(r"(?<!function )loadLiveFrame\(\);", script)]
    assert len(calls) == 1, f"loadLiveFrame is called {len(calls)} times; expected once"
    committed = script.index("Inauguration completed at")
    assert calls[0] > committed, "the frame is loaded before the cut is confirmed"


def test_the_frame_is_created_empty(script: str):
    """No `src` at construction, or the browser fetches before the cut is in."""
    assert "f.src" not in script, "the frame is given a source outside loadLiveFrame"
    assert "document.createElement('iframe')" in script


def test_the_unloaded_frame_is_kept_out_of_the_document(script: str):
    """An iframe with no src still paints an opaque box over the stand-in.

    Appending it at reveal time put a black rectangle where the designed
    stand-in should be, for every visitor who sees a preview.
    """
    reveal = script[script.index("function revealLive") : script.index("function loadLiveFrame")]
    assert "$('#shotBody').appendChild" not in reveal, "the frame is appended before it is loaded"

    load = script[script.index("function loadLiveFrame") : script.index("function markPreviewReveal")]
    assert "$('#shotBody').appendChild(liveFrame)" in load


def test_the_stand_in_is_kept_until_a_real_page_arrives(script: str):
    """The mock is dropped on the frame's load event, not when the frame is made —
    otherwise a preview shows an empty box, and a slow load flashes blank."""
    assert "$('#shotMock')" in script
    load = script.index("function loadLiveFrame")
    body = script[load : script.index("function markPreviewReveal")]
    assert "addEventListener('load'" in body
    assert "#shotMock" in body


@pytest.fixture(scope="module")
def branches(script: str) -> tuple[str, str]:
    """The two halves of persist(): what the server accepted, and what it refused.

    Split here rather than by offset, so the assertions below are about which branch
    a string lives in and not about the order two of them happen to be written in.
    """
    start = script.index("}).then(function (s) {")
    catch = script.index("}).catch(function (err) {")
    return script[start:catch], script[catch:]


# --- claiming the site is live ----------------------------------------------


def test_only_the_committed_path_says_the_site_is_live(script: str, branches: tuple[str, str]):
    """Every visitor plays the same animation, so the wording is the whole
    difference between a preview and a record of the inauguration."""
    committed, refused = branches

    assert "The website is live" in committed
    assert "The website is live" not in refused
    # Every occurrence in the file lives on the committed path, so a later edit
    # cannot leave a second claim of liveness somewhere the server never agreed to.
    assert script.count("The website is live") == committed.count("The website is live")


def test_a_preview_relabels_the_reveal(page: str, script: str, branches: tuple[str, str]):
    committed, refused = branches

    assert "function markPreviewReveal" in script
    assert "is still <em>held</em>" in script

    # The preview wording is applied on every path that did not commit...
    assert "markPreviewReveal();" in refused
    # ...and nowhere on the path that did.
    assert "markPreviewReveal" not in committed


def test_relabelling_the_credit_line_does_not_destroy_it(page: str, script: str):
    """#liveBy and #liveDate are written by the committed path and read back when
    the record is checked. Rewriting the paragraph's textContent would delete them,
    which is what a rendered run of the preview caught."""
    assert 'id="liveBy"' in page
    assert 'id="liveDate"' in page
    assert "$('#liveCred').textContent" not in script, "the credit line is being overwritten"
    assert "$('#liveCredLive').hidden = true" in script
    assert "$('#liveCredPreview').hidden = false" in script


def test_a_preview_offers_nothing_to_enter(script: str, branches: tuple[str, str]):
    """"Enter the website" points at index.html, which during the ceremony is
    redirected back to this page — so on a preview it is a link to here."""
    committed, refused = branches

    preview = script[
        script.index("function markPreviewReveal") : script.index("}).then(function (s) {")
    ]
    assert "$('#enter').hidden = true" in preview
    # ...and the committed path never hides it, so a real cut keeps the link.
    assert "$('#enter')" not in committed
    assert "markPreviewReveal();" in refused


def test_the_preview_notice_is_a_constant(script: str):
    """One string, so the banner cannot drift from what the tests assert."""
    assert "PREVIEW_TEXT" in script
    assert "this ceremony is not official" in script


# --- hiding things -----------------------------------------------------------


def test_anything_hidden_by_script_has_a_hidden_rule(page: str):
    """`el.hidden = true` only works while no author rule sets `display`.

    An author declaration beats the browser's own ``[hidden]{display:none}``, so a
    styled element silently ignores the attribute and stays on screen — which is how
    the "Enter the website" button survived being hidden on a preview. Every element
    the page hides that also has its own display rule needs the rule restated.
    """
    # Both of these are set to `display` by an author rule above.
    for selector, styled_by in ((".preview-flag", "display:flex"), (".enter", "display:inline-flex")):
        block = re.search(re.escape(selector) + r"\{(.*?)\}", page, re.S)
        assert block, f"{selector} has no style rule"
        assert styled_by.replace("display:", "") in block.group(1), (
            f"{selector} no longer sets display; this test can be relaxed"
        )
        assert f"{selector}[hidden]{{display:none;}}" in page, (
            f"{selector} sets display but has no [hidden] rule, so hiding it does nothing"
        )


# --- what the page must not do any more -------------------------------------


def test_the_page_no_longer_retires_files(script: str):
    """Nothing is deleted at cut time since the cut became a server-side commit."""
    for stale in ("__ceremony", "X-Ceremony", "Retiring the ceremony files", "Inaugurate.bat"):
        assert stale not in script, f"{stale!r} is left over from the Node-era page"


def test_the_page_talks_to_the_real_api(script: str):
    assert "api('/api/ceremony/cut'" in script
    assert "api('/api/ceremony/state')" in script
