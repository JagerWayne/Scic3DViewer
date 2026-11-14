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
    page.wait_for_selector("#sResult:not(.hidden)", timeout=180000)   # 6 publish
    return page.input_value("#sUrl")


def make_interactive_link(studio):  # the wizard publishes picture links; the 3D link type is still served, so make one through the API
    return studio.evaluate("""async () => { const fd = new FormData(); fd.append('model', await stage.exportAs('glb'), 'm.glb');
        fd.append('config', JSON.stringify(stage.getConfig())); fd.append('title', 'Interactive');
        const r = await fetch('/api/projects/share', { method: 'POST', headers: { Authorization: 'Bearer ' + localStorage.scic_token }, body: fd });
        return location.origin + (await r.json()).url; }""")


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
        assert studio.locator("#tl .tl-dot.done").count() == 5                              # the timeline has moved all the way

        studio.click("#sClose"); studio.click("#projects")
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
        page.wait_for_selector("#sResult:not(.hidden)", timeout=180000)
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


def test_viewer_parts_panel_starts_closed_and_can_be_opened(server):
    token = db.login_google("Viewer Owner", "vo@x.com", "test")[0]
    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"])
        studio = browser.new_context(bypass_csp=True).new_page()
        studio.add_init_script(f"localStorage.setItem('scic_token','{token}')")
        studio.goto(BASE + "/studio")
        studio.wait_for_function("document.querySelector('#tier').textContent !== ''", timeout=30000)
        studio.set_input_files("#file", {"name": "cube.stl", "mimeType": "model/stl", "buffer": CUBE_STL.encode()})
        studio.wait_for_function("window.stage?.triCount?.() === 12", timeout=30000)
        url = make_interactive_link(studio)
        for name, ctx in (("desktop", dict(viewport={"width": 1280, "height": 800})),
                          ("phone", dict(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True))):
            page = browser.new_context(bypass_csp=True, **ctx).new_page()
            page.goto(url)
            page.wait_for_function("window.stage?.triCount?.() === 12", timeout=30000)
            shown = lambda: page.evaluate("getComputedStyle(document.querySelector('#panel')).display")
            fits = page.evaluate("""() => { const s = window.stage, V = s.camera.position.constructor;
                return [[-1, 0, -1], [1, 0, -1], [-1, 2, 1], [1, 2, 1], [-1, 0, 1], [1, 2, -1]].every(p => { const n = new V(...p).project(s.camera); return Math.abs(n.x) <= 1 && Math.abs(n.y) <= 1; }); }""")
            assert fits, name                                                  # the whole 2x2x2 cube is on screen, also on a narrow phone
            assert shown() == "none", name                                     # nothing covers the model at first
            page.click("#menuBtn")                                              # the options live in a burger menu, stacked under each other
            box = page.locator("#menu").bounding_box(); vp = page.viewport_size
            assert box["x"] >= 0 and box["x"] + box["width"] <= vp["width"] and box["y"] + box["height"] <= vp["height"], name   # fully on screen
            ys = [page.locator(f"#menu {sel}").bounding_box() for sel in ("#rotate", "#full", "#panelBtn") if page.locator(f"#menu {sel}").is_visible()]
            assert len(ys) >= 2 and all(b["y"] > a["y"] and abs(b["x"] - a["x"]) < 2 for a, b in zip(ys, ys[1:])), name        # one under the other
            page.click("#panelBtn"); assert shown() != "none", name            # the Parts option opens the panel (and closes the menu)
            assert page.locator("#menu").is_hidden(), name
            page.click("#panelClose"); assert shown() == "none", name          # the X closes it again
            page.click("#menuBtn"); page.click("#panelBtn"); page.click("#menuBtn"); page.click("#panelBtn"); assert shown() == "none", name   # and the option toggles
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
        assert viewer.evaluate("getComputedStyle(document.querySelector('#panelBtn')).display") == "none"
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
        viewer.click("#menuBtn"); viewer.click("button:has-text('Callouts')")             # viewers can hide the tags
        viewer.wait_for_selector(".callout", state="hidden", timeout=5000)
        viewer.click("#menuBtn"); viewer.click("button:has-text('Callouts')")
        viewer.wait_for_selector(".callout", state="visible", timeout=5000)
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
        page.click("#zup")                                                               # for other formats: one click lays it down again (Z becomes Y)
        dx, dy, dz, floor = size()
        assert dz > dy * 1.5 and abs(floor) < 1e-3, (dx, dy, dz, floor)
        browser.close()
