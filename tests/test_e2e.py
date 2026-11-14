"""Browser smoke tests: the real Studio and Viewer pages in Chromium against the real server, with an in-memory Drive.
Needs:  pip install playwright  &&  playwright install chromium   (skipped automatically when Playwright is missing)
Also needs internet: Three.js and Tailwind are loaded from their CDNs."""
import gzip, os, tempfile, threading, time
os.environ.setdefault("SCIC_DATA", tempfile.mkdtemp())
import pytest

pytest.importorskip("playwright.sync_api")
import uvicorn
from playwright.sync_api import Page, sync_playwright

_goto = Page.goto  # the pages load Tailwind and Three.js from public CDNs: give a slow connection two minutes instead of thirty seconds


def _patient_goto(self, url, **kw):
    kw.setdefault("timeout", 120000)
    return _goto(self, url, **kw)


Page.goto = _patient_goto

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

    def upload(lid, p, name, progress=None):
        fid = f"{lid}:{name}:{next(ids)}"; cloud[fid] = p.read_bytes()
        if progress:
            progress(len(cloud[fid]), len(cloud[fid]))
        return fid
    mp.setattr(storage, "upload", upload)
    mp.setattr(storage, "stream", lambda lid, fid: iter([cloud[fid]]))
    mp.setattr(storage, "delete", lambda lid, fid: cloud.pop(fid, None))
    srv = uvicorn.Server(uvicorn.Config(appmod.app, port=PORT, log_level="warning"))
    t = threading.Thread(target=srv.run, daemon=True); t.start()
    while not srv.started:
        time.sleep(0.05)
    yield
    srv.should_exit = True; t.join(5); mp.undo()


SMALL = dict(iAz=8, iEl=2, iPx=256, iQ=70, iElMin=10, iElMax=40)  # Enterprise "Custom": a tiny pack keeps tests fast


def run_wizard(page, title=None, custom=None, download=False):
    """Click through the 6-step sharing wizard and return the link."""
    page.click("#share")
    if title:
        page.fill("#sTitle", title)
    page.click("#sNext")                      # 1 title -> 2 download
    if download:
        page.check("#pDl")
    page.click("#sNext")                      # 2 -> 3 quality
    if custom:
        page.select_option("#iPreset", "custom")
        for k, v in custom.items():
            page.fill("#" + k, str(v))
        page.fill("#iWm", "(c) E2E Studio")
    page.click("#sNext")                      # 3 -> 4 expiry
    page.click("#sNext")                      # 4 -> 5 processing starts
    page.wait_for_selector("#sResult:not(.hidden)", timeout=420000)   # 6 publish
    return page.input_value("#sUrl")


def test_studio_publish_projects_and_viewer(server):
    token = db.login_google("E2E User", "e2e@x.com", "test")[0]
    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"])
        ctx = browser.new_context(bypass_csp=True)  # Playwright polls with eval(), which the site CSP rightly forbids
        studio, errors = ctx.new_page(), []
        studio.on("pageerror", lambda e: errors.append(str(e)))
        studio.add_init_script(f"localStorage.setItem('scic_token','{token}')")
        studio.goto(BASE + "/studio")
        studio.wait_for_function("document.querySelector('#tier').textContent !== ''", timeout=30000)
        studio.set_input_files("#file", {"name": "cube.stl", "mimeType": "model/stl", "buffer": CUBE_STL.encode()})
        studio.wait_for_function("window.stage?.triCount?.() === 12", timeout=30000)

        studio.click("#share")
        assert studio.input_value("#sTitle") == "" and studio.get_attribute("#sTitle", "placeholder") == "cube"   # defaults to the file name
        studio.click("#sClose")
        url = run_wizard(studio, title="E2E model")
        assert "/v/" in url and "iframe" in studio.input_value("#sEmbed")
        log = studio.eval_on_selector("#pvLog", "e => e.value")                           # the Processing step explained what it did
        assert studio.get_attribute("#pvLog", "readonly") is not None
        for part in ("Starting:", "Drew all", "Sending to the server", "Sent in", "Watermark applied", "Uploaded the pictures", "Done. Your link is ready."):
            assert part in log, (part, log)
        assert "%" in log and "[00:" in log and " so far" in log or "Drew all" in log        # percentages and times
        assert studio.locator("#tl .tl-dot.done").count() == 5                              # the timeline has moved all the way

        studio.click("#sClose"); studio.click("[data-menu=file]"); studio.click("#projects")
        studio.wait_for_selector("#projRows tr", timeout=10000)
        assert "E2E model" in studio.inner_text("#projRows")

        viewer = ctx.new_page(); viewer.on("pageerror", lambda e: errors.append(str(e)))
        viewer.goto(url)
        viewer.wait_for_function("document.title === 'E2E model'", timeout=30000)
        viewer.wait_for_function("document.querySelector('#stage canvas') && document.querySelector('#stage canvas').width > 1", timeout=30000)
        assert not errors, errors
        browser.close()


def test_share_dialog_on_a_phone(server):
    token = db.login_google("Phone User", "phone@x.com", "test")[0]
    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"])
        page = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True, bypass_csp=True).new_page()
        page.add_init_script(f"localStorage.setItem('scic_token','{token}')")
        page.goto(BASE + "/studio")
        page.wait_for_function("document.querySelector('#tier').textContent !== ''", timeout=30000)
        page.set_input_files("#file", {"name": "cube.stl", "mimeType": "model/stl", "buffer": CUBE_STL.encode()})
        page.wait_for_function("window.stage?.triCount?.() === 12", timeout=30000)
        shown = lambda sel: page.evaluate(f"getComputedStyle(document.querySelector('{sel}')).display")
        assert page.evaluate("document.querySelector('#panel').offsetHeight") <= 2      # settings sheet starts closed on a phone (just its border shows)
        page.click("#share")
        assert [shown(s) for s in ("#panel", "#tools", "#tour")] == ["none"] * 3         # windows behind the dialog are hidden
        card = page.locator("#shareModal .modal-card").bounding_box()
        assert card["width"] >= 388 and card["y"] > 0                                    # a full-width bottom sheet
        go = page.locator("#sNext").bounding_box()
        assert go["height"] >= 44 and go["y"] + go["height"] <= 844                      # thumb-sized, always on screen
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")       # no sideways scrolling
        for _ in range(3):                                                               # every step fits the phone
            page.click("#sNext"); assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.click("#sNext")
        page.wait_for_selector("#sResult:not(.hidden)", timeout=420000)
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.click("#sClose")
        assert shown("#tools") != "none"                                                 # everything is back after closing
        browser.close()


def test_admin_edits_pauses_and_deletes_a_license(server):
    adm = db.login_google("Boss", db.ADMIN_EMAIL, "test")[0]
    db.create_license("Pat Pending", "pat@x.com", "PRO", "30d")
    lic = lambda: db.connect().execute("SELECT * FROM licenses WHERE email='pat@x.com' AND duration_days IS NOT NULL").fetchone()
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page, errors = browser.new_context(bypass_csp=True).new_page(), []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.add_init_script(f"localStorage.setItem('scic_token','{adm}')")
        page.goto(BASE + "/admin")
        page.click("[data-tab=licenses]")
        row = lambda: page.locator("#lrows tr", has_text="pat@x.com")
        row().get_by_role("button", name="Edit").click()
        page.fill("#eName", "Patricia"); page.fill("#eNote", "vip customer"); page.select_option("#eTier", "ENTERPRISE"); page.fill("#eDur", "120")
        page.click("#editForm button:has-text('Save changes')")
        page.wait_for_function("document.querySelector('#lrows').innerText.includes('Patricia')", timeout=15000)
        l = lic()
        assert (l["name"], l["note"], l["tier"], l["duration_days"]) == ("Patricia", "vip customer", "ENTERPRISE", 120)
        page.click("#eClose")
        row().get_by_role("button", name="Pause").click()
        page.wait_for_function("document.querySelector('#lrows').innerText.includes('paused')", timeout=15000)
        assert lic()["paused_at"]
        row().get_by_role("button", name="Resume").click()
        page.wait_for_function("!document.querySelector('#lrows').innerText.includes('paused')", timeout=15000)
        assert lic()["paused_at"] is None
        page.once("dialog", lambda d: d.accept())
        row().get_by_role("button", name="Delete").click()
        page.wait_for_function("!document.querySelector('#lrows').innerText.includes('pat@x.com')", timeout=15000)
        assert lic() is None and not errors, errors
        browser.close()


def test_viewer_menu_fits_a_phone_and_stacks_its_options(server):
    db.create_license("Menu M", "menu@x.com", "ENTERPRISE", "30d")
    token = db.login_google("Menu M", "menu@x.com", "test")[0]
    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"])
        url = publish_images(browser, token, SMALL)
        page = browser.new_context(bypass_csp=True, viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True).new_page()
        page.goto(url)
        page.wait_for_function("document.querySelector('#stage canvas')?.dataset.frame !== undefined", timeout=30000)
        page.click("#menuBtn")                                                            # the options live in a burger menu, stacked under each other
        box = page.locator("#menu").bounding_box(); vp = page.viewport_size
        assert box["x"] >= 0 and box["x"] + box["width"] <= vp["width"] and box["y"] + box["height"] <= vp["height"]   # fully on screen
        ys = [page.locator(f"#menu {sel}").bounding_box() for sel in ("button:has-text('Front')", "button:has-text('Side')", "#rotate", "#full") if page.locator(f"#menu {sel}").is_visible()]
        assert len(ys) >= 3 and all(b["y"] > a["y"] and abs(b["x"] - a["x"]) < 2 for a, b in zip(ys, ys[1:]))       # one under the other
        page.keyboard.press("Escape"); assert page.locator("#menu").is_hidden()
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        browser.close()


def test_admin_gives_a_license_from_the_edit_page(server):
    adm = db.login_google("Boss", db.ADMIN_EMAIL, "test")[0]
    person = db.login_google("Gina Gift", "gina@x.com", "test")[0]                 # signed in once: a Free account
    tier = lambda: db.session_user(person)["tier"]
    assert tier() == "FREE"
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page, errors = browser.new_context(bypass_csp=True).new_page(), []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.add_init_script(f"localStorage.setItem('scic_token','{adm}')")
        page.goto(BASE + "/admin")
        page.click("[data-tab=licenses]")
        page.locator("#lrows tr", has_text="gina@x.com").get_by_role("button", name="Give license").click()
        assert page.input_value("#eTier") == "PRO" and page.is_visible("#eDur") and page.is_visible("#eUnit")   # Pro + a length, ready to save
        assert page.is_hidden("#eStatusWrap") and "Name & Surname" in page.inner_text("#editForm") and "Surname" not in page.inner_text("#eName")
        page.fill("#eDur", "6"); page.select_option("#eUnit", "30")                 # 6 months
        page.click("#editForm button:has-text('Save changes')")
        page.wait_for_function("document.querySelector('#eTitle').textContent === 'Edit license'", timeout=15000)   # it is a license now
        row = db.connect().execute("SELECT * FROM licenses WHERE email='gina@x.com' AND duration_days IS NOT NULL").fetchone()
        assert row["tier"] == "PRO" and row["duration_days"] == 180 and row["activated_at"]
        assert tier() == "PRO"                                                      # Gina has it without signing in again
        page.select_option("#eTier", "ENTERPRISE"); page.select_option("#eStatusSel", "paused")
        page.click("#editForm button:has-text('Save changes')")
        page.wait_for_function("document.querySelector('#eStatus').innerText.includes('paused')", timeout=15000)
        assert tier() == "FREE"                                                     # paused: Free for now
        page.select_option("#eStatusSel", "active"); page.click("#editForm button:has-text('Save changes')")
        page.wait_for_function("document.querySelector('#eStatus').innerText.includes('active')", timeout=15000)
        assert tier() == "ENTERPRISE" and not errors, errors
        browser.close()


def test_admin_page_only_for_the_admin(server):
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for email, expect in ((db.ADMIN_EMAIL, "Overview"), ("someone@x.com", "not the site admin")):
            token = db.login_google("T", email, "test")[0]
            page = browser.new_context(bypass_csp=True).new_page()
            page.add_init_script(f"localStorage.setItem('scic_token','{token}')")
            page.goto(BASE + "/admin")
            page.wait_for_function(f"document.body.innerText.includes({expect!r})", timeout=30000)
        browser.close()


def publish_images(browser, token, custom=None, download=False):
    studio = browser.new_context(bypass_csp=True, viewport={"width": 1280, "height": 900}).new_page()
    studio.add_init_script(f"localStorage.setItem('scic_token','{token}')")
    studio.goto(BASE + "/studio")
    studio.wait_for_function("document.querySelector('#tier').textContent !== ''", timeout=30000)
    studio.set_input_files("#file", {"name": "cube.stl", "mimeType": "model/stl", "buffer": CUBE_STL.encode()})
    studio.wait_for_function("window.stage?.triCount?.() === 12", timeout=30000)
    return run_wizard(studio, custom=custom, download=download)


def test_image_link_viewer_has_no_3d_and_rotates_with_the_mouse(server):
    db.create_license("Ent User", "ent@x.com", "ENTERPRISE", "30d")
    token = db.login_google("Ent User", "ent@x.com", "test")[0]
    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"])
        url = publish_images(browser, token, SMALL)
        viewer, reqs, errors = browser.new_context(bypass_csp=True, viewport={"width": 900, "height": 700}).new_page(), [], []
        viewer.on("request", lambda r: reqs.append(r.url)); viewer.on("pageerror", lambda e: errors.append(str(e)))
        viewer.goto(url)
        viewer.wait_for_function("document.querySelector('#stage canvas') && document.querySelector('#stage canvas').width > 1", timeout=30000)
        viewer.wait_for_timeout(600)
        shot = lambda: viewer.evaluate("document.querySelector('#stage canvas').toDataURL()")
        before = shot()
        assert any("/images" in u for u in reqs)
        assert not any("/model" in u or "three" in u or "stage.js" in u for u in reqs), reqs   # no model, no 3D engine
        assert viewer.evaluate("typeof window.stage?.exportAs") == "undefined"          # (window.stage is just the #stage div here)
        viewer.mouse.move(300, 350); viewer.mouse.down(); viewer.mouse.move(600, 350, steps=12); viewer.mouse.up()
        viewer.wait_for_timeout(900)
        assert shot() != before                                                          # dragging shows other pictures
        assert not errors, errors
        browser.close()


def test_free_image_link_draws_the_watermark(server):
    token = db.login_google("Free Img", "freeimg@x.com", "test")[0]
    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"])
        url = publish_images(browser, token)                                              # Light preset: 36 x 3 pictures
        viewer, errors = browser.new_context(bypass_csp=True).new_page(), []
        viewer.on("pageerror", lambda e: errors.append(str(e)))
        viewer.goto(url)
        viewer.wait_for_function("document.querySelector('#stage canvas') && document.querySelector('#stage canvas').width > 1", timeout=30000)
        viewer.wait_for_timeout(800)
        # the watermark text and the cross are painted on the canvas itself: pixels exist even at a corner where the cube is not
        corner = viewer.evaluate("""() => { const c = document.querySelector('#stage canvas'), d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data;
            let n = 0; for (let i = 3; i < d.length; i += 4) if (d[i]) n++; return n / (c.width * c.height); }""")
        assert corner > 0.005
        assert not errors, errors
        browser.close()


def test_image_link_callouts_toggle_and_a_click_turns_to_the_tag(server):
    db.create_license("Call Out", "callout@x.com", "ENTERPRISE", "30d")
    token = db.login_google("Call Out", "callout@x.com", "test")[0]
    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"])
        studio = browser.new_context(bypass_csp=True, viewport={"width": 1280, "height": 900}).new_page()
        studio.add_init_script(f"localStorage.setItem('scic_token','{token}')")
        studio.goto(BASE + "/studio")
        studio.wait_for_function("document.querySelector('#tier').textContent !== ''", timeout=30000)
        studio.set_input_files("#file", {"name": "cube.stl", "mimeType": "model/stl", "buffer": CUBE_STL.encode()})
        studio.wait_for_function("window.stage?.triCount?.() === 12", timeout=30000)
        # a callout on the +Z face of the 2x2x2 cube; the camera starts at 45 degrees, so it is seen only at an angle
        studio.evaluate("() => { stage.annotations.push({ text: 'Front', point: [0, 1, 1], normal: [0, 0, 1], cam: [1, 1, 3], target: [0, 1, 0] }); stage.buildAnnos(); }")
        url = run_wizard(studio, custom=SMALL)
        viewer = browser.new_context(bypass_csp=True, viewport={"width": 900, "height": 700}).new_page()
        viewer.goto(url)
        viewer.wait_for_selector(".callout:not([hidden])", timeout=30000)
        tour = viewer.locator("[aria-label='Callout tour']")
        assert tour.is_visible()                                                          # tour buttons exist when there are callouts
        tour.get_by_role("button", name="Next callout").click()
        assert "1/1" in tour.inner_text()
        vh = 700                                                                          # nothing sits over the bottom quarter, where watermarks are
        for el in (tour.get_by_role("button", name="Next callout"), tour.get_by_role("button", name="Previous callout"), tour.locator("[aria-live]")):
            b = el.bounding_box(); assert b["y"] + b["height"] < vh * 0.75, b
        viewer.click("#menuBtn"); viewer.click("button:has-text('Play tour')")            # play and stop live in the menu
        viewer.click("#menuBtn"); assert viewer.locator("#menu button", has_text="Stop tour").is_visible()
        viewer.click("#menu button >> text=Stop tour")
        viewer.click("#menuBtn"); assert viewer.locator("#menu button", has_text="Play tour").is_visible(); viewer.keyboard.press("Escape")
        viewer.click("#menuBtn"); viewer.click("button:has-text('Callouts')")             # viewers can hide the tags...
        viewer.wait_for_selector(".callout", state="hidden", timeout=5000)
        assert tour.is_hidden()                                                           # ...and the tour buttons go with them
        viewer.click("#menuBtn"); viewer.click("button:has-text('Callouts')")
        viewer.wait_for_selector(".callout", state="visible", timeout=5000)
        assert tour.is_visible()
        viewer.click(".callout")                                                          # turns to the picture where the tag faces us
        viewer.wait_for_timeout(1200)
        box = viewer.locator(".callout").bounding_box(); cv = viewer.locator("#stage canvas").bounding_box()
        assert viewer.locator(".callout").is_visible()                                    # never ends up on the far side
        assert abs(box["x"] + box["width"] / 2 - (cv["x"] + cv["width"] / 2)) < 40        # and sits in the middle
        browser.close()


def test_image_link_rotates_when_zoomed_and_has_front_side_presets(server):
    db.create_license("Zoom Z", "zoom@x.com", "ENTERPRISE", "30d")
    token = db.login_google("Zoom Z", "zoom@x.com", "test")[0]
    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"])
        url = publish_images(browser, token, SMALL)                                      # 8 pictures around, 2 heights (10 and 40 degrees)
        viewer = browser.new_context(bypass_csp=True, viewport={"width": 900, "height": 700}).new_page()
        viewer.goto(url)
        viewer.wait_for_function("document.querySelector('#stage canvas')?.dataset.frame !== undefined", timeout=30000)
        frame = lambda: int(viewer.evaluate("document.querySelector('#stage canvas').dataset.frame"))
        viewer.mouse.move(450, 350); viewer.mouse.wheel(0, -600); viewer.wait_for_timeout(300)   # zoom in
        before = frame()
        viewer.mouse.down(); viewer.mouse.move(750, 350, steps=12); viewer.mouse.up(); viewer.wait_for_timeout(900)
        assert frame() != before                                                         # dragging still rotates while zoomed
        viewer.click("#menuBtn"); viewer.click("button:has-text('Side')")
        viewer.wait_for_function("document.querySelector('#stage canvas').dataset.frame === '2'", timeout=8000)   # a quarter turn from the front, lowest height
        viewer.click("#menuBtn"); viewer.click("button:has-text('Front')")
        viewer.wait_for_function("document.querySelector('#stage canvas').dataset.frame === '0'", timeout=8000)
        browser.close()


def test_image_viewer_uses_previews_while_moving_and_never_goes_blank(server):
    db.create_license("Fast F", "fast@x.com", "ENTERPRISE", "30d")
    token = db.login_google("Fast F", "fast@x.com", "test")[0]
    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"])
        url = publish_images(browser, token, dict(iAz=30, iEl=4, iPx=256, iQ=60, iElMin=10, iElMax=45))   # 120 pictures, more than the caches hold
        viewer = browser.new_context(bypass_csp=True, viewport={"width": 900, "height": 700}).new_page()
        viewer.goto(url)
        viewer.wait_for_function("document.querySelector('#stage canvas')?.dataset.quality === 'full'", timeout=30000)   # at rest: the full picture
        painted = lambda: viewer.evaluate("""() => { const c = document.querySelector('#stage canvas'), d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data;
            let n = 0; for (let i = 3; i < d.length; i += 4) if (d[i]) n++; return n / (c.width * c.height); }""")
        assert painted() > .05
        viewer.evaluate("() => { window.__q = new Set(); setInterval(() => __q.add(document.querySelector('#stage canvas').dataset.quality), 15); }")
        viewer.mouse.move(200, 350); viewer.mouse.down(); viewer.mouse.move(700, 350, steps=25); viewer.mouse.up()
        viewer.wait_for_timeout(1000)
        assert "low" in viewer.evaluate("[...window.__q]")                                # previews were used while moving
        viewer.wait_for_function("document.querySelector('#stage canvas').dataset.quality === 'full'", timeout=8000)   # and the full picture came back once it rested
        for i in range(24):                                                               # many fast turns and rests: the picture never vanishes
            viewer.mouse.move(150 + 20 * (i % 5), 350); viewer.mouse.down(); viewer.mouse.move(750 - 30 * (i % 4), 330, steps=6); viewer.mouse.up()
            viewer.wait_for_timeout(60 if i % 3 else 330)
            assert painted() > .05, i
        browser.close()


def stl_box(sx, sy, sz):  # a box of the given size, as STL text
    v = [(x * sx, y * sy, z * sz) for x, y, z in VERTS]
    return "solid b\n" + "".join("facet normal 0 0 0\nouter loop\n" + "".join("vertex %s %s %s\n" % v[i] for i in f) + "endloop\nendfacet\n" for f in FACETS) + "endsolid\n"


def test_z_up_files_stand_upright_and_the_button_fixes_other_files(server):
    token = db.login_google("Z Up", "zup@x.com", "test")[0]
    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"])
        page = browser.new_context(bypass_csp=True).new_page()
        page.add_init_script(f"localStorage.setItem('scic_token','{token}')")
        page.goto(BASE + "/studio")
        page.wait_for_function("document.querySelector('#tier').textContent !== ''", timeout=30000)
        size = lambda: page.evaluate("""() => { stage.assembly.updateMatrixWorld(true); const V = stage.camera.position.constructor, lo = [1e9, 1e9, 1e9], hi = [-1e9, -1e9, -1e9];
            for (const m of stage.meshesAll()) { m.geometry.computeBoundingBox(); const b = m.geometry.boundingBox;
              for (const x of [b.min.x, b.max.x]) for (const y of [b.min.y, b.max.y]) for (const z of [b.min.z, b.max.z]) { const q = new V(x, y, z).applyMatrix4(m.matrixWorld);
                [q.x, q.y, q.z].forEach((c, i) => { lo[i] = Math.min(lo[i], c); hi[i] = Math.max(hi[i], c); }); } }
            return [hi[0] - lo[0], hi[1] - lo[1], hi[2] - lo[2], lo[1]]; }""")
        # a post that is 3 long along Z in the file: it must stand up (tallest along Y) and rest on the floor
        page.set_input_files("#file", {"name": "post.stl", "mimeType": "model/stl", "buffer": stl_box(1, 1, 3).encode()})
        page.wait_for_function("window.stage?.triCount?.() === 12", timeout=30000)
        dx, dy, dz, floor = size()
        assert dy > dx * 1.5 and dy > dz * 1.5 and abs(floor) < 1e-3, (dx, dy, dz, floor)
        page.click("[data-menu=model]"); page.click("#zup")                              # for other formats: one click lays it down again (Z becomes Y)
        dx, dy, dz, floor = size()
        assert dz > dy * 1.5 and abs(floor) < 1e-3, (dx, dy, dz, floor)
        browser.close()


def open_studio(browser, email, tier="ENTERPRISE", viewport=None, mobile=False):
    if tier != "FREE":
        db.create_license("Tester", email, tier, "30d")
    token = db.login_google("Tester", email, "test")[0]
    page = browser.new_context(bypass_csp=True, viewport=viewport or {"width": 1280, "height": 800}, is_mobile=mobile, has_touch=mobile).new_page()
    page.add_init_script(f"localStorage.setItem('scic_token','{token}')")
    page.goto(BASE + "/studio")
    page.wait_for_function("document.querySelector('#tier').textContent !== ''", timeout=30000)
    page.set_input_files("#file", {"name": "post.stl", "mimeType": "model/stl", "buffer": stl_box(1, 1, 3).encode()})
    page.wait_for_function("window.stage?.triCount?.() === 12", timeout=30000)
    return page


def test_callouts_follow_their_part_and_undo_redo_work(server):
    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"])
        page = open_studio(browser, "follow@x.com")
        # a callout on the front of the post (as a click on the model would make it)
        page.evaluate("""() => { const V = stage.camera.position.constructor, m = stage.meshesAll()[0]; stage.ask = () => 'Top';
            stage._annotateAt({ point: new V(0, 1, 1), face: { normal: new V(0, 0, 1) }, object: m }); }""")
        a0 = page.evaluate("stage.annotations[0]")
        assert a0["part"] and a0["lp"] and len(a0["lcam"]) == 3                           # stored relative to its part
        page.evaluate("stage.assembly.children[0].position.x += 0.7")                    # move the part: the callout follows it
        page.wait_for_function("stage.annotations[0].point[0] > %s + 0.69" % a0["point"][0], timeout=5000)
        page.evaluate("stage.assembly.children[0].rotation.x += Math.PI / 2")             # and turns with it (its normal pointed up, now it points at the camera)
        page.wait_for_function("Math.abs(stage.annotations[0].normal[2]) > 0.9", timeout=5000)
        cfg = page.evaluate("stage.getConfig().annotations[0]")
        assert cfg["part"] == a0["part"] and abs(cfg["normal"][2]) > 0.9                  # the saved scene has the moved values
        # callouts from older projects get attached to the nearest part when a scene is applied
        page.evaluate("stage.applyConfig({ annotations: [{ text: 'old', point: [0, 1, 1], normal: [0, 0, 1], cam: [1, 1, 3], target: [0, 1, 0] }] })")
        assert page.evaluate("!!stage.annotations[0].part")
        # undo / redo
        page.evaluate("stage.history = []; stage.redoStack = []; stage.annotations = []; stage.buildAnnos()")
        page.evaluate("""() => { const V = stage.camera.position.constructor; stage.ask = () => 'One';
            stage._annotateAt({ point: new V(0, 1, 1), face: { normal: new V(0, 0, 1) }, object: stage.meshesAll()[0] }); }""")
        assert page.evaluate("stage.annotations.length") == 1
        page.keyboard.press("Control+z"); assert page.evaluate("stage.annotations.length") == 0
        page.keyboard.press("Control+Shift+z"); assert page.evaluate("stage.annotations.length") == 1
        page.click("[data-menu=edit]"); assert page.locator("#mUndo").is_enabled(); page.click("#mUndo")
        assert page.evaluate("stage.annotations.length") == 0
        browser.close()


def drag_rotate_ring(page, pre_deg, steps=14):
    """Press the rotate gizmo with the real mouse and drag along it; returns the part's angles in degrees."""
    import math
    page.evaluate("pre => { const p = stage.assembly.children[0]; p.rotation.set(0, pre * Math.PI / 180, 0); p.updateMatrixWorld(true); stage.select(p); stage.setGizmoMode('rotate'); }", pre_deg)
    page.wait_for_timeout(300)
    c = page.evaluate("""() => { const V = stage.camera.position.constructor, v = new V(0, 1, 0).project(stage.camera), r = stage.renderer.domElement.getBoundingClientRect();
        return [r.left + (v.x * .5 + .5) * r.width, r.top + (-v.y * .5 + .5) * r.height]; }""")
    for rad in range(60, 260, 6):
        for a in range(0, 360, 12):
            page.mouse.move(c[0] + rad * math.cos(math.radians(a)), c[1] + rad * math.sin(math.radians(a)))
            if page.evaluate("stage.tc.axis") in ("X", "Y", "Z"):
                page.mouse.down()
                for k in range(1, steps + 1):
                    t = math.radians(a + k * 4)
                    page.mouse.move(c[0] + rad * math.cos(t), c[1] + rad * math.sin(t))
                rot = page.evaluate("(() => { const r = stage.assembly.children[0].rotation, d = 180 / Math.PI; return [r.x * d, r.y * d, r.z * d]; })()")
                page.mouse.up()
                return rot
    raise AssertionError("the rotate gizmo was not found under the mouse")


def test_rotation_snapping(server):
    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"])
        page = open_studio(browser, "snap@x.com")
        assert page.evaluate("stage.snap.on") is False
        page.click("#snapBtn"); assert page.evaluate("stage.snap") == {"on": True, "deg": 15}
        assert page.evaluate("stage.tc.translationSnap") == 0.1
        page.select_option("#snapDeg", "45"); assert page.evaluate("stage.snap.deg") == 45
        page.keyboard.press("s"); assert page.evaluate("stage.snap.on") is False         # S toggles it
        page.click("[data-menu=edit]"); page.click("#mSnap"); assert page.evaluate("stage.snap.on") is True
        assert page.locator("#snapBtn").get_attribute("aria-pressed") == "true"
        page.select_option("#snapDeg", "15")
        near = lambda deg: abs(deg / 15 - round(deg / 15)) < 1e-3                          # a multiple of 15 degrees
        from_aligned = drag_rotate_ring(page, 0)
        assert all(near(v) for v in from_aligned) and any(abs(v) > 1 for v in from_aligned), from_aligned
        from_off_grid = drag_rotate_ring(page, 10)                                          # a part that already sits at 10 degrees
        assert all(near(v) for v in from_off_grid), from_off_grid                          # still ends up on the grid, not at 25, 40...
        page.click("#snapBtn"); assert page.evaluate("stage.snap.on") is False             # off: free rotation again
        free = drag_rotate_ring(page, 10)
        assert not all(near(v) for v in free), free
        browser.close()


def test_save_and_open_a_project_in_google_drive(server):
    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"])
        page = open_studio(browser, "saver@x.com")
        assert "Unsaved" in page.inner_text("#saveState") and page.locator("#saveDot").is_visible()
        assert "0/" in page.inner_text("#quota") and "projects" in page.inner_text("#quota")
        page.click("[data-menu=file]"); page.click("#mSave")                              # first save asks for a name
        page.fill("#saveName", "My bracket"); page.click("#saveGo")
        page.wait_for_function("document.querySelector('#saveState').textContent.includes('Saved')", timeout=30000)
        assert "My bracket" in page.inner_text("#saveState") and page.locator("#saveDot").is_hidden()
        assert "1/" in page.inner_text("#quota")
        page.evaluate("document.querySelector('#light').value = 1.4; document.querySelector('#light').dispatchEvent(new Event('input', { bubbles: true }))")
        assert "Unsaved" in page.inner_text("#saveState")                                 # a change marks the project as unsaved
        page.keyboard.press("Control+s")                                                  # Ctrl+S saves over it, no dialog
        page.wait_for_function("document.querySelector('#saveState').textContent.includes('Saved')", timeout=30000)
        assert page.locator("#saveModal").is_hidden()
        page.click("[data-menu=file]"); page.click("#mOpenDrive")                         # open it again from Drive
        page.wait_for_selector("#openGrid .pcard", timeout=10000)
        assert "My bracket" in page.inner_text("#openGrid") and "1 of" in page.inner_text("#openCount")
        page.evaluate("stage.assembly.clear(); stage.emit('tree')")
        page.locator("#openGrid .pcard").get_by_role("button", name="Open").click()
        page.wait_for_function("stage.triCount() === 12", timeout=30000)                  # the model came back from Drive
        assert "Saved" in page.inner_text("#saveState") and page.locator("#saveDot").is_hidden()
        page.click("[data-menu=file]"); page.click("#mOpenDrive"); page.wait_for_selector("#openGrid .pcard")
        page.once("dialog", lambda d: d.accept())
        page.locator("#openGrid .pcard").get_by_role("button", name="Delete").click()
        page.wait_for_function("document.querySelector('#openGrid').children.length === 0", timeout=10000)
        browser.close()


def test_menus_work_with_the_keyboard_and_dialogs_are_labelled(server):
    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"])
        page = open_studio(browser, "keys@x.com")
        page.focus("[data-menu=file]"); page.keyboard.press("ArrowDown")
        assert page.evaluate("document.activeElement.id") == "open"                       # the first item has focus
        page.keyboard.press("ArrowRight"); assert page.evaluate("document.activeElement.id") == "mUndo" or page.locator("#menu-edit").is_visible()
        page.keyboard.press("Escape"); assert page.locator("#menu-edit").is_hidden()
        assert page.locator("#shareModal").get_attribute("role") == "dialog" and page.locator("#shareModal").get_attribute("aria-modal") == "true"
        assert page.locator("#shareModal").get_attribute("aria-labelledby")
        page.click("#share"); page.keyboard.press("Escape"); assert page.locator("#shareModal").is_hidden()   # Escape closes dialogs
        page.evaluate("document.activeElement.blur()")
        page.keyboard.press("?"); assert page.locator("#helpModal").is_visible()
        page.click("#helpClose")
        for label in ("Select", "Move", "Rotate", "Scale", "Snap", "Brush", "Callout"):
            assert page.locator(f"#tools [aria-label='{label}']").count() == 1             # icon buttons all have names
        browser.close()


def test_studio_has_no_tools_that_the_viewer_never_shows(server):
    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"])
        page = open_studio(browser, "clean@x.com")
        for label in ("Ruler", "Angle", "Thickness"):
            assert page.locator(f"#tools [aria-label='{label}']").count() == 0           # measuring never reaches a picture link
        assert page.locator("#unit, #uscale, #clearDims").count() == 0 and page.locator("#grid").count() == 0
        assert page.evaluate("typeof stage.setUnit") == "undefined" and page.evaluate("typeof stage.clearDims") == "undefined"
        cfg = page.evaluate("stage.getConfig()")
        assert "units" not in cfg and "grid" not in cfg                                   # nor does the saved scene carry them
        page.click("[data-menu=view]"); page.click("#vGrid")                              # the floor grid is only a viewing aid in the View menu
        assert page.evaluate("stage.grid.visible") is False and page.locator("#vGrid").get_attribute("aria-checked") == "false"
        page.click("[data-menu=view]"); page.click("#vGrid"); assert page.evaluate("stage.grid.visible") is True
        browser.close()


def test_inspector_collapses_and_menus_stay_on_top(server):
    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"])
        page = open_studio(browser, "collapse@x.com")
        shown = lambda: page.evaluate("getComputedStyle(document.querySelector('#panel')).display") != "none"
        assert shown() and page.locator("#panelToggle").is_visible()
        right_open = page.locator("#panelToggle").bounding_box()
        page.click("#panelToggle")                                                         # collapse: the whole scene is visible
        assert not shown() and page.locator("#panelToggle").get_attribute("aria-expanded") == "false"
        assert page.locator("#panelToggle").bounding_box()["x"] > right_open["x"] + 100    # the handle moved to the right edge
        page.reload(); page.wait_for_function("document.querySelector('#tier').textContent !== ''", timeout=60000)
        assert not shown()                                                                 # and the choice is remembered
        page.click("[data-menu=view]"); page.click("#vPanel"); assert shown()              # the View menu brings it back too
        page.click("#panelToggle"); page.click("#panelToggle"); assert shown()
        # the dropdown menus must be drawn above the tool rail on the left and the inspector on the right
        on_top = """sel => { const m = document.querySelector(sel), r = m.getBoundingClientRect(); const e = document.elementFromPoint(r.left + r.width / 2, r.top + 20); return !!e && m.contains(e); }"""
        page.click("[data-menu=file]"); assert page.evaluate(on_top, "#menu-file")
        page.click("#accountBtn"); assert page.evaluate(on_top, "#menu-account")
        page.keyboard.press("Escape")
        browser.close()
