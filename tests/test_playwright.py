from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page()
    resp = page.goto('https://immi.homeaffairs.gov.au/visas/getting-a-visa/visa-listing/skilled-independent-189')
    print(resp.status)
    browser.close()
