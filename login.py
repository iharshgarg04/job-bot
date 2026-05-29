#!/usr/bin/env python3
"""
One-time login — Log into all job sites once. Sessions are saved and reused forever.
No need to close your Chrome. This opens a separate browser.

Usage: python3 login.py
"""

import asyncio
from playwright.async_api import async_playwright

BROWSER_DATA = "/Users/mac/job-bot/.browser-data"

SITES = [
    ("Naukri", "https://www.naukri.com/nlogin/login"),
    ("LinkedIn", "https://www.linkedin.com/login"),
    ("Indeed", "https://secure.indeed.com/account/login"),
    ("Instahyre", "https://www.instahyre.com/login/"),
    ("Foundit", "https://www.foundit.in/"),
]


async def main():
    print("\n  Job Bot — One-Time Login Setup")
    print("  ================================")
    print("  A separate browser will open (won't affect your Chrome).")
    print("  Log into each site. Sessions are saved permanently.\n")

    async with async_playwright() as p:
        browser = await p.chromium.launch_persistent_context(
            BROWSER_DATA,
            headless=False,
            args=["--disable-blink-features=AutomationControlled"],
            viewport={"width": 1280, "height": 800},
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        )
        page = browser.pages[0] if browser.pages else await browser.new_page()
        await page.add_init_script('Object.defineProperty(navigator, "webdriver", {get: () => undefined})')

        for name, url in SITES:
            print(f"\n  [{name}] Opening login page...")
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=15000)
            except Exception:
                # Some sites redirect aggressively, try with networkidle
                try:
                    await page.goto(url, wait_until="load", timeout=15000)
                except Exception:
                    print(f"  [{name}] Page loaded with warnings, but should be usable.")
            input(f"  Log into {name}, then press Enter here...")
            print(f"  [{name}] Saved!")

        print("\n  All done! Sessions saved permanently.")
        print("  Now run: python3 web.py")
        print("  You only need to run this again if sessions expire.\n")
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
