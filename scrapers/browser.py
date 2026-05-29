"""Shared browser launcher — persistent Playwright browser with saved logins."""

BROWSER_DATA = "/Users/mac/job-bot/.browser-data"


async def launch_browser(playwright):
    """Launch persistent Chromium browser with stealth settings.

    Login sessions are saved in .browser-data/ and persist across runs.
    Run 'python3 login.py' once to log into all sites.
    """
    return await playwright.chromium.launch_persistent_context(
        BROWSER_DATA,
        headless=False,
        args=["--disable-blink-features=AutomationControlled"],
        viewport={"width": 1280, "height": 720},
        user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        locale="en-IN",
    )


async def close_browser(context):
    """Close the browser context."""
    await context.close()
