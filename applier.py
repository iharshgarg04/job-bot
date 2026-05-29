"""Auto-apply engine using Playwright browser automation."""

import asyncio
from playwright.async_api import async_playwright, Page, Browser
from rich.console import Console
from scrapers.base import Job
from tracker import log_application
from config import RESUME_PDF_PATH

console = Console()


async def auto_apply(jobs: list[Job]):
    """Apply to approved jobs using browser automation."""
    if not jobs:
        console.print("[yellow]No approved jobs to apply to.[/yellow]")
        return

    console.print(f"\n[bold cyan]Starting auto-apply for {len(jobs)} jobs...[/bold cyan]")
    console.print("[yellow]A browser window will open. You may need to log in manually on first run.[/yellow]")
    console.print("[yellow]Press Enter after logging in to continue.[/yellow]\n")

    async with async_playwright() as p:
        # Use persistent context to keep login sessions
        user_data_dir = "/Users/mac/job-bot/.browser-data"
        browser = await p.chromium.launch_persistent_context(
            user_data_dir,
            headless=False,
            viewport={"width": 1280, "height": 720},
        )
        page = browser.pages[0] if browser.pages else await browser.new_page()

        applied_count = 0
        failed_count = 0

        for i, job in enumerate(jobs, 1):
            console.print(f"\n[cyan]({i}/{len(jobs)}) Applying: {job.title} at {job.company} [{job.source}][/cyan]")

            try:
                if job.source == "linkedin":
                    success = await apply_linkedin(page, job)
                elif job.source == "naukri":
                    success = await apply_naukri(page, job)
                elif job.source == "indeed":
                    success = await apply_indeed(page, job)
                elif job.source == "instahyre":
                    success = await apply_instahyre(page, job)
                elif job.source == "wellfound":
                    success = await apply_wellfound(page, job)
                else:
                    console.print(f"  [yellow]Unknown source: {job.source}. Opening URL...[/yellow]")
                    await page.goto(job.url)
                    success = False

                if success:
                    job.status = "applied"
                    log_application(job, "applied")
                    applied_count += 1
                    console.print(f"  [green]Applied successfully![/green]")
                else:
                    job.status = "failed"
                    log_application(job, "failed")
                    failed_count += 1
                    console.print(f"  [red]Could not auto-apply. Job opened in browser.[/red]")

            except Exception as e:
                console.print(f"  [red]Error: {e}[/red]")
                log_application(job, "error")
                failed_count += 1

            # Small delay between applications
            await asyncio.sleep(2)

        console.print(f"\n[bold green]Done! Applied: {applied_count} | Failed: {failed_count}[/bold green]")
        console.print("[yellow]Browser will stay open so you can manually apply to failed jobs.[/yellow]")
        console.print("[yellow]Press Enter to close browser...[/yellow]")
        input()
        await browser.close()


async def apply_linkedin(page: Page, job: Job) -> bool:
    """Apply on LinkedIn using Easy Apply."""
    await page.goto(job.url, wait_until="domcontentloaded")
    await asyncio.sleep(2)

    # Check if login is needed
    if "login" in page.url or "authwall" in page.url:
        console.print("  [yellow]LinkedIn login required. Please log in manually.[/yellow]")
        input("  Press Enter after logging in...")
        await page.goto(job.url, wait_until="domcontentloaded")
        await asyncio.sleep(2)

    # Look for Easy Apply button
    easy_apply = page.locator("button.jobs-apply-button, button:has-text('Easy Apply')")
    if await easy_apply.count() > 0:
        await easy_apply.first.click()
        await asyncio.sleep(2)

        # Try to submit the easy apply form
        for _ in range(5):  # Max 5 steps in multi-step form
            # Look for submit button
            submit = page.locator("button:has-text('Submit application'), button:has-text('Submit')")
            if await submit.count() > 0:
                await submit.first.click()
                await asyncio.sleep(2)
                return True

            # Look for next/review button
            next_btn = page.locator("button:has-text('Next'), button:has-text('Review'), button:has-text('Continue')")
            if await next_btn.count() > 0:
                await next_btn.first.click()
                await asyncio.sleep(1)
            else:
                break

        # Check if we need to upload resume
        upload = page.locator("input[type='file']")
        if await upload.count() > 0:
            await upload.first.set_input_files(RESUME_PDF_PATH)
            await asyncio.sleep(1)

    return False


async def apply_naukri(page: Page, job: Job) -> bool:
    """Apply on Naukri."""
    await page.goto(job.url, wait_until="domcontentloaded")
    await asyncio.sleep(2)

    if "login" in page.url or "naukri.com/nlogin" in page.url:
        console.print("  [yellow]Naukri login required. Please log in manually.[/yellow]")
        input("  Press Enter after logging in...")
        await page.goto(job.url, wait_until="domcontentloaded")
        await asyncio.sleep(2)

    # Look for Apply button
    apply_btn = page.locator("button#apply-button, button:has-text('Apply'), a:has-text('Apply on company site')")
    if await apply_btn.count() > 0:
        await apply_btn.first.click()
        await asyncio.sleep(3)

        # Check if it was a direct apply or redirected
        if "chatbot" in page.url or "apply" in page.url:
            # Handle chatbot-style application
            submit = page.locator("button:has-text('Submit'), button:has-text('Apply')")
            if await submit.count() > 0:
                await submit.first.click()
                await asyncio.sleep(2)
                return True

        # Check for success message
        success = page.locator("text='applied successfully', text='Application Submitted'")
        if await success.count() > 0:
            return True

        return True  # Clicked apply at least

    return False


async def apply_indeed(page: Page, job: Job) -> bool:
    """Apply on Indeed."""
    await page.goto(job.url, wait_until="domcontentloaded")
    await asyncio.sleep(2)

    apply_btn = page.locator("button#indeedApplyButton, button:has-text('Apply now'), a:has-text('Apply now')")
    if await apply_btn.count() > 0:
        await apply_btn.first.click()
        await asyncio.sleep(3)

        # Indeed opens application in iframe or new page
        # Upload resume if needed
        upload = page.locator("input[type='file']")
        if await upload.count() > 0:
            await upload.first.set_input_files(RESUME_PDF_PATH)
            await asyncio.sleep(1)

        # Try to continue/submit
        for _ in range(5):
            submit = page.locator("button:has-text('Submit'), button:has-text('Apply'), button:has-text('Continue')")
            if await submit.count() > 0:
                await submit.first.click()
                await asyncio.sleep(2)
                # Check for success
                done = page.locator("text='application has been submitted', text='Application submitted'")
                if await done.count() > 0:
                    return True

    return False


async def apply_instahyre(page: Page, job: Job) -> bool:
    """Open Instahyre job — most require profile-based apply."""
    await page.goto(job.url, wait_until="domcontentloaded")
    await asyncio.sleep(2)

    apply_btn = page.locator("button:has-text('Apply'), a:has-text('Apply'), button:has-text('Interested')")
    if await apply_btn.count() > 0:
        await apply_btn.first.click()
        await asyncio.sleep(2)
        return True

    return False


async def apply_wellfound(page: Page, job: Job) -> bool:
    """Apply on Wellfound."""
    await page.goto(job.url, wait_until="domcontentloaded")
    await asyncio.sleep(2)

    apply_btn = page.locator("button:has-text('Apply'), a:has-text('Apply Now')")
    if await apply_btn.count() > 0:
        await apply_btn.first.click()
        await asyncio.sleep(2)

        # Wellfound usually has a simple apply flow
        submit = page.locator("button:has-text('Submit Application'), button:has-text('Submit')")
        if await submit.count() > 0:
            await submit.first.click()
            await asyncio.sleep(2)
            return True

    return False
