"""End-to-end browser test. Start the app first:
AUTH_MODE=dev STORAGE_MODE=local uvicorn fasla.main:app --port 8077
then: python tests/e2e/run_e2e.py http://localhost:8077 /tmp/shots"""
import sys, re, time, os, uuid
from playwright.sync_api import sync_playwright, expect

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8077"
SHOTS = sys.argv[2] if len(sys.argv) > 2 else None
EXAMPLE = os.path.join(os.path.dirname(__file__), "..", "..", "data", "_example_e2e.xlsx")

def shot(page, name):
    if SHOTS: page.screenshot(path=f"{SHOTS}/{name}.png", full_page=False)

def main():
    errors = []
    with sync_playwright() as p:
        br = p.chromium.launch()
        ctx = br.new_context(viewport={"width": 1366, "height": 860}, accept_downloads=True)
        page = ctx.new_page()
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: m.type == "error" and "fonts.g" not in m.text and errors.append(m.text))
        email = f"e2e-{uuid.uuid4().hex[:6]}@example.com"
        page.goto(BASE + "/")
        expect(page.locator(".pitch h1")).to_be_visible(); shot(page, "01_login")
        page.click("#tUp"); page.fill("#em", email); page.fill("#pw", "short")
        page.click("#go"); expect(page.locator("#er")).not_to_be_empty()
        page.fill("#pw", "correct-horse-1"); page.click("#go")
        expect(page.locator("#bn")).to_be_visible(); shot(page, "02_onboard")
        page.fill("#bn", "Nile Thread"); page.click("form button.primary")
        expect(page.locator(".title h1")).to_have_text("Nile Thread")
        with page.expect_download() as d: page.click("#tx")
        d.value.save_as(EXAMPLE)
        with page.expect_download() as d2: page.click("#te")
        assert d2.value.suggested_filename.endswith(".xlsx")
        shot(page, "03_brand")
        # bad upload
        bad = "/tmp/bad.xlsx"; open(bad, "wb").write(b"not a workbook")
        page.set_input_files("#file", bad)
        expect(page.locator("#uperr .note.bad")).to_be_visible(); shot(page, "04_bad_upload")
        page.set_input_files("#file", EXAMPLE)
        expect(page.locator("#sf")).to_be_visible(timeout=20000); shot(page, "05_review")
        page.click("#go")
        expect(page.locator(".run")).to_be_visible(); time.sleep(4); shot(page, "06_running")
        expect(page.locator("iframe.results")).to_be_visible(timeout=240000)
        fr = page.frame_locator("iframe.results")
        expect(fr.locator("body")).to_contain_text(re.compile("EGP|جنيه"), timeout=20000)
        time.sleep(2); shot(page, "07_done")
        page.screenshot(path=f"{SHOTS}/07b_done_full.png", full_page=True) if SHOTS else None
        with page.expect_download() as d3: page.click("#rep")
        assert d3.value.suggested_filename.endswith(".xlsx")
        # arabic
        page.click("#lang"); expect(page.locator("html")).to_have_attribute("dir", "rtl"); time.sleep(2); shot(page, "08_done_ar")
        page.click("#lang")
        # history
        page.click("a.btn.ghost"); expect(page.locator("tr.row")).to_have_count(1)
        expect(page.locator(".chip.done")).to_be_visible(); shot(page, "09_history")
        # sign out, second user can't see
        page.click("#am > button"); page.click("#so")
        expect(page.locator("#tIn")).to_be_visible()
        page.click("#tUp"); page.fill("#em", "other-"+email); page.fill("#pw", "correct-horse-2"); page.click("#go")
        expect(page.locator("#bn")).to_be_visible()
        # phone
        m = br.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True, has_touch=True)
        mp = m.new_page(); mp.goto(BASE + "/"); shot(mp, "10_phone_login")
        mp.fill("#em", email); mp.fill("#pw", "correct-horse-1"); mp.click("#go")
        expect(mp.locator(".title h1")).to_be_visible(); shot(mp, "11_phone_brand")
        mp.click("tr.row"); expect(mp.locator("iframe.results")).to_be_visible(); time.sleep(3); shot(mp, "12_phone_done")
        br.close()
    real = [e for e in errors if "Failed to load resource" not in e]
    print("console errors:", real)
    assert not real
    print("E2E OK")

main()
