"""Browser smoke tests: the real Studio and Viewer pages in Chromium against the real server, with an in-memory Drive.
Needs:  pip install playwright  &&  playwright install chromium   (skipped automatically when Playwright is missing)
Also needs internet: Three.js and Tailwind are loaded from their CDNs."""
import gzip, os, tempfile, threading, time
os.environ.setdefault("SCIC_DATA", tempfile.mkdtemp())
import pytest

pytest.importorskip("playwright.sync_api")
import uvicorn
from playwright.sync_api import sync_playwright

import app as appmod, db, storage

PORT = 8765
BASE = f"http://127.0.0.1:{PORT}"
FACETS = [(0, 2, 1), (0, 3, 2), (4, 5, 6), (4, 6, 7), (0, 1, 5), (0, 5, 4), (1, 2, 6), (1, 6, 5), (2, 3, 7), (2, 7, 6), (3, 0, 4), (3, 4, 7)]
VERTS = [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0), (0, 0, 1), (1, 0, 1), (1, 1, 1), (0, 1, 1)]
CUBE_STL = "solid c\n" + "".join("facet normal 0 0 0\nouter loop\n" + "".join("vertex %s %s %s\n" % VERTS[i] for i in f) + "endloop\nendfacet\n" for f in FACETS) + "endsolid\n"


@pytest.fixture(scope="module")
def server():
    cloud, ids, mp = {}, iter(range(1, 10**6)), pytest.MonkeyPatch()
    mp.setattr(storage, "configured", lambda: True)
    mp.setattr(storage, "connected", lambda lid: True)

    def upload(lid, p, name):
        fid = f"{lid}:{name}:{next(ids)}"; cloud[fid] = p.read_bytes(); return fid
    mp.setattr(storage, "upload", upload)
    mp.setattr(storage, "stream", lambda lid, fid: iter([cloud[fid]]))
    mp.setattr(storage, "delete", lambda lid, fid: cloud.pop(fid, None))
    srv = uvicorn.Server(uvicorn.Config(appmod.app, port=PORT, log_level="warning"))
    t = threading.Thread(target=srv.run, daemon=True); t.start()
    while not srv.started:
        time.sleep(0.05)
    yield
    srv.should_exit = True; t.join(5); mp.undo()


def test_studio_publish_projects_and_viewer(server):
    token = db.login_google("E2E User", "e2e@x.com", "test")[0]
    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"])
        ctx = browser.new_context()
        studio, errors = ctx.new_page(), []
        studio.on("pageerror", lambda e: errors.append(str(e)))
        studio.add_init_script(f"localStorage.setItem('scic_token','{token}')")
        studio.goto(BASE + "/studio")
        studio.wait_for_function("document.querySelector('#tier').textContent !== ''", timeout=30000)
        studio.set_input_files("#file", {"name": "cube.stl", "mimeType": "model/stl", "buffer": CUBE_STL.encode()})
        studio.wait_for_function("window.stage && window.stage.triCount() === 12", timeout=30000)

        studio.click("#share"); studio.fill("#sTitle", "E2E model"); studio.click("#sGo")
        studio.wait_for_selector("#sResult:not(.hidden)", timeout=30000)
        url = studio.input_value("#sUrl")
        assert "/v/" in url and "iframe" in studio.input_value("#sEmbed")

        studio.click("#sClose"); studio.click("#projects")
        studio.wait_for_selector("#projRows tr", timeout=10000)
        assert "E2E model" in studio.inner_text("#projRows")

        viewer = ctx.new_page(); viewer.on("pageerror", lambda e: errors.append(str(e)))
        viewer.goto(url)
        viewer.wait_for_function("document.title === 'E2E model'", timeout=30000)
        viewer.wait_for_function("window.stage && window.stage.triCount() === 12", timeout=30000)
        assert not errors, errors
        browser.close()


def test_admin_page_only_for_the_admin(server):
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for email, expect in ((db.ADMIN_EMAIL, "Overview"), ("someone@x.com", "not the site admin")):
            token = db.login_google("T", email, "test")[0]
            page = browser.new_context().new_page()
            page.add_init_script(f"localStorage.setItem('scic_token','{token}')")
            page.goto(BASE + "/admin")
            page.wait_for_function(f"document.body.innerText.includes({expect!r})", timeout=30000)
        browser.close()
