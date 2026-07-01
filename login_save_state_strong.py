from playwright.sync_api import sync_playwright

LOGIN_URL = "https://finbox.com/login"
TEST_URL = "https://finbox.com/NSEI:SUZLON/models/historical-10yr/"

with sync_playwright() as p:
    browser = p.chromium.launch(
        headless=False,
        channel="chrome",   # sistem Chrome (daha az şüpheli)
        slow_mo=80          # biraz yavaşlatır, daha "insan" gibi
    )

    context = browser.new_context(
        locale="en-US",
        timezone_id="Europe/Istanbul",
        viewport={"width": 1280, "height": 800},
    )

    page = context.new_page()

    # Önce ana sayfa -> sonra login (bazı siteler direkt deep linkte naz yapıyor)
    page.goto("https://finbox.com", wait_until="domcontentloaded")
    page.wait_for_timeout(1200)

    page.goto(LOGIN_URL, wait_until="domcontentloaded")
    print("👉 Playwright açılan Chrome içinde FINBOX login yap. Ana sayfayı gördüğünde Enter'a bas.")
    input()

    # Login sonrası state kaydet
    context.storage_state(path="storage_state.json")
    print("✅ storage_state.json yeniden kaydedildi.")

    # Hemen test URL açıp kontrol edelim
    page.goto(TEST_URL, wait_until="domcontentloaded")
    page.wait_for_timeout(6000)

    print("Title:", page.title())
    input("Kontrol ettin mi? Kapatmak için Enter...")
    browser.close()
