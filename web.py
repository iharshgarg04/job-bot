#!/usr/bin/env python3
"""
Job Bot Web Dashboard
Usage: python3 web.py
Opens at http://localhost:5050
"""

import asyncio
import json
import threading
from flask import Flask, render_template, jsonify, request

from config import TARGET_TITLES, TARGET_LOCATIONS, EXPERIENCE_YEARS
from scrapers import ALL_SCRAPERS
from scrapers.base import Job
from matcher import match_jobs_with_claude, match_jobs_keyword
from tracker import save_matched_jobs, load_matched_jobs, log_application, get_applied_urls
from dedup import deduplicate_jobs

app = Flask(__name__)

job_store: list[Job] = []

AUTO_APPLY_SOURCES = {"naukri", "linkedin", "instahyre"}


def _load_store():
    global job_store
    job_store = load_matched_jobs()


@app.route("/")
def index():
    return render_template("dashboard.html")


@app.route("/api/jobs")
def get_jobs():
    return jsonify([j.to_dict() for j in job_store])


@app.route("/api/jobs/<int:idx>/approve", methods=["POST"])
def approve_job(idx):
    if 0 <= idx < len(job_store):
        job_store[idx].status = "approved"
        log_application(job_store[idx], "approved")
        save_matched_jobs(job_store)
        return jsonify({"ok": True})
    return jsonify({"ok": False, "error": "Invalid index"}), 400


@app.route("/api/jobs/<int:idx>/reject", methods=["POST"])
def reject_job(idx):
    if 0 <= idx < len(job_store):
        job_store[idx].status = "rejected"
        log_application(job_store[idx], "rejected")
        save_matched_jobs(job_store)
        return jsonify({"ok": True})
    return jsonify({"ok": False, "error": "Invalid index"}), 400


@app.route("/api/jobs/<int:idx>/mark-applied", methods=["POST"])
def mark_applied(idx):
    """Manually mark an external job as applied (user applied themselves)."""
    if 0 <= idx < len(job_store):
        job_store[idx].status = "applied"
        log_application(job_store[idx], "applied")
        save_matched_jobs(job_store)
        return jsonify({"ok": True})
    return jsonify({"ok": False, "error": "Invalid index"}), 400


@app.route("/api/jobs/<int:idx>/apply", methods=["POST"])
def apply_single(idx):
    if not (0 <= idx < len(job_store)):
        return jsonify({"ok": False, "error": "Invalid index"}), 400

    job = job_store[idx]

    if job.source in AUTO_APPLY_SOURCES:
        try:
            loop = asyncio.new_event_loop()
            result = loop.run_until_complete(_auto_apply_job(job))
            loop.close()

            is_external = result.get("external", False)

            if result["applied"]:
                job.status = "applied"
            elif is_external:
                job.status = "external"
            else:
                job.status = "apply_failed"

            log_application(job, job.status)
            save_matched_jobs(job_store)
            return jsonify({
                "ok": True,
                "auto": True,
                "applied": result["applied"],
                "external": is_external,
                "message": result["message"],
                "url": job.url,
            })
        except Exception as e:
            return jsonify({"ok": False, "auto": True, "error": str(e), "url": job.url})
    else:
        job.status = "external"
        log_application(job, "external")
        save_matched_jobs(job_store)
        return jsonify({"ok": True, "auto": False, "external": True, "url": job.url})


@app.route("/api/jobs/approve-bulk", methods=["POST"])
def approve_bulk():
    data = request.json
    indices = data.get("indices", [])
    count = 0
    for idx in indices:
        if 0 <= idx < len(job_store) and job_store[idx].status not in ("applied",):
            job_store[idx].status = "approved"
            log_application(job_store[idx], "approved")
            count += 1
    save_matched_jobs(job_store)
    return jsonify({"ok": True, "count": count})


@app.route("/api/jobs/reject-bulk", methods=["POST"])
def reject_bulk():
    data = request.json
    indices = data.get("indices", [])
    count = 0
    for idx in indices:
        if 0 <= idx < len(job_store) and job_store[idx].status not in ("applied", "approved"):
            job_store[idx].status = "rejected"
            log_application(job_store[idx], "rejected")
            count += 1
    save_matched_jobs(job_store)
    return jsonify({"ok": True, "count": count})


@app.route("/api/jobs/apply-bulk", methods=["POST"])
def apply_bulk():
    data = request.json
    indices = data.get("indices", [])
    auto_applied = []
    external_jobs = []
    failed = []

    try:
        loop = asyncio.new_event_loop()

        async def _bulk():
            from playwright.async_api import async_playwright
            from scrapers.browser import launch_browser, close_browser

            async with async_playwright() as p:
                browser = await launch_browser(p)
                page = browser.pages[0] if browser.pages else await browser.new_page()
                await page.add_init_script('Object.defineProperty(navigator, "webdriver", {get: () => undefined})')

                try:
                    for idx in indices:
                        if not (0 <= idx < len(job_store)) or job_store[idx].status in ("applied", "external"):
                            continue

                        job = job_store[idx]

                        if job.source not in AUTO_APPLY_SOURCES:
                            job.status = "external"
                            log_application(job, "external")
                            external_jobs.append(idx)
                            continue

                        try:
                            result = await _apply_single_job(page, job)
                            if result["applied"]:
                                job.status = "applied"
                                log_application(job, "applied")
                                auto_applied.append(idx)
                            elif result.get("external"):
                                job.status = "external"
                                log_application(job, "external")
                                external_jobs.append(idx)
                            else:
                                failed.append({"idx": idx, "url": job.url, "reason": result["message"]})
                        except Exception as e:
                            failed.append({"idx": idx, "url": job.url, "reason": str(e)})
                        await asyncio.sleep(2)
                finally:
                    await close_browser(browser)

        loop.run_until_complete(_bulk())
        loop.close()
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})

    save_matched_jobs(job_store)
    return jsonify({
        "ok": True,
        "applied": auto_applied,
        "external": external_jobs,
        "failed": failed,
    })


@app.route("/api/scan", methods=["POST"])
def scan_jobs():
    try:
        loop = asyncio.new_event_loop()

        async def _scan():
            all_jobs = []
            existing_urls = {j.url for j in job_store}
            applied_urls = get_applied_urls()
            skip_urls = existing_urls | applied_urls

            for ScraperClass in ALL_SCRAPERS:
                scraper = ScraperClass()
                try:
                    jobs = await scraper.scrape(TARGET_TITLES, TARGET_LOCATIONS, EXPERIENCE_YEARS)
                    jobs = [j for j in jobs if j.url and j.url not in skip_urls]
                    all_jobs.extend(jobs)
                except Exception as e:
                    print(f"  Error with {scraper.name}: {e}")

            if not all_jobs:
                return 0, 0, []

            before = len(all_jobs)
            all_jobs = deduplicate_jobs(all_jobs)
            print(f"  Deduplication: {before} -> {len(all_jobs)} jobs")

            try:
                matched = await match_jobs_with_claude(all_jobs)
            except Exception:
                matched = match_jobs_keyword(all_jobs)

            return len(all_jobs), len(matched), matched

        result = loop.run_until_complete(_scan())
        loop.close()

        if result[0] == 0:
            return jsonify({"ok": True, "total_scraped": 0, "matched": 0})

        total_scraped, matched_count, matched_jobs = result

        global job_store
        existing_urls = {j.url for j in job_store}
        new_jobs = [j for j in matched_jobs if j.url not in existing_urls]
        job_store.extend(new_jobs)

        job_store.sort(key=lambda j: j.match_score, reverse=True)
        save_matched_jobs(job_store)

        return jsonify({
            "ok": True,
            "total_scraped": total_scraped,
            "matched": len(new_jobs),
        })

    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ── Apply helpers ──────────────────────────────────────────────

async def _apply_single_job(page, job: Job) -> dict:
    """Apply to a single job using an already-open browser page."""
    try:
        if job.source == "instahyre":
            return await _apply_instahyre(page, job)

        await page.goto(job.url, wait_until="domcontentloaded", timeout=15000)
        await page.wait_for_timeout(3000)

        if job.source == "naukri":
            return await _apply_naukri(page, job)
        elif job.source == "linkedin":
            return await _apply_linkedin(page, job)
        else:
            return {"applied": False, "external": True, "message": "Unsupported source"}
    except Exception as e:
        return {"applied": False, "message": str(e)}


async def _auto_apply_job(job: Job) -> dict:
    """Open browser, apply to one job, close browser."""
    from playwright.async_api import async_playwright
    from scrapers.browser import launch_browser, close_browser

    async with async_playwright() as p:
        browser = await launch_browser(p)
        page = browser.pages[0] if browser.pages else await browser.new_page()
        await page.add_init_script('Object.defineProperty(navigator, "webdriver", {get: () => undefined})')
        try:
            return await _apply_single_job(page, job)
        finally:
            await close_browser(browser)


# ── Naukri ─────────────────────────────────────────────────────

async def _apply_naukri(page, job: Job) -> dict:
    if "nlogin" in page.url or "login" in page.url:
        return {"applied": False, "message": "Naukri login required"}

    # Check if already applied
    already = page.locator("text='Already Applied', text='already applied'")
    if await already.count() > 0:
        return {"applied": True, "message": "Already applied on Naukri"}

    # Check for "Apply on company site" — mark as external immediately
    external_btn = page.locator("a:has-text('Apply on company site'), a:has-text('apply on company')")
    if await external_btn.count() > 0:
        return {"applied": False, "external": True, "message": "Requires apply on company site"}

    # Look for direct Apply button
    apply_btn = page.locator("button#apply-button, button:has-text('Apply'), button.apply-button")
    if await apply_btn.count() > 0:
        await apply_btn.first.click()
        await page.wait_for_timeout(3000)

        # Did we get redirected to external site?
        if "naukri.com" not in page.url:
            return {"applied": False, "external": True, "message": "Redirected to external site"}

        # Check for success
        success = page.locator("text='applied successfully', text='Application Submitted', text='Successfully Applied'")
        if await success.count() > 0:
            return {"applied": True, "message": "Applied on Naukri"}

        # Already applied after clicking
        already = page.locator("text='Already Applied', text='already applied'")
        if await already.count() > 0:
            return {"applied": True, "message": "Already applied on Naukri"}

        # Chatbot form — try submit
        submit = page.locator("button:has-text('Submit'), button:has-text('Apply')")
        if await submit.count() > 0:
            await submit.first.click()
            await page.wait_for_timeout(2000)

            # Check again if we got redirected
            if "naukri.com" not in page.url:
                return {"applied": False, "external": True, "message": "Redirected to external site after submit"}

            return {"applied": True, "message": "Submitted on Naukri"}

        # Still on naukri but no clear success — check for external redirect indicators
        # Some jobs show "Apply" but then open a new tab to company site
        pages_count = len(page.context.pages)
        if pages_count > 1:
            # New tab opened — external apply
            for p in page.context.pages[1:]:
                await p.close()
            return {"applied": False, "external": True, "message": "Opened company site in new tab"}

        return {"applied": True, "message": "Applied on Naukri"}

    return {"applied": False, "message": "Apply button not found on Naukri"}


# ── LinkedIn ───────────────────────────────────────────────────

async def _apply_linkedin(page, job: Job) -> dict:
    if "login" in page.url or "authwall" in page.url:
        return {"applied": False, "message": "LinkedIn login required"}

    easy_apply = page.locator("[aria-label*='Easy Apply'], button.jobs-apply-button, button:has-text('Easy Apply'), a:has-text('Easy Apply')")
    if await easy_apply.count() > 0:
        await easy_apply.first.click()
        await page.wait_for_timeout(3000)

        for step in range(8):
            await page.wait_for_timeout(1500)

            # Success?
            done = page.locator("text='Application sent', text='application was sent', text='Your application was sent'")
            if await done.count() > 0:
                return {"applied": True, "message": "Applied via LinkedIn Easy Apply"}

            # Submit?
            submit = page.locator(
                "button[aria-label*='Submit application'], "
                "button:has-text('Submit application'), "
                "button:has-text('Submit')"
            )
            if await submit.count() > 0:
                await submit.first.click()
                await page.wait_for_timeout(3000)

                done = page.locator("text='Application sent', text='application was sent', text='Your application was sent'")
                if await done.count() > 0:
                    return {"applied": True, "message": "Applied via LinkedIn Easy Apply"}
                return {"applied": True, "message": "Submitted on LinkedIn"}

            # Upload resume
            upload = page.locator("input[type='file']")
            if await upload.count() > 0:
                from config import RESUME_PDF_PATH
                try:
                    await upload.first.set_input_files(RESUME_PDF_PATH)
                    await page.wait_for_timeout(1000)
                except Exception:
                    pass

            # Fill phone
            phone_input = page.locator("input[name*='phoneNumber'], input[aria-label*='Phone'], input[id*='phone']")
            if await phone_input.count() > 0:
                val = await phone_input.first.input_value()
                if not val:
                    await phone_input.first.fill("+91-8864833148")

            # Next step
            next_btn = page.locator(
                "button[aria-label*='Continue'], button[aria-label*='Next'], "
                "button[aria-label*='Review'], "
                "button:has-text('Next'), button:has-text('Review'), button:has-text('Continue')"
            )
            if await next_btn.count() > 0:
                await next_btn.first.click()
            else:
                break

        # Couldn't complete Easy Apply — mark as external so user can finish manually
        return {"applied": False, "external": True, "message": "Easy Apply form needs manual input"}

    # No Easy Apply — external
    return {"applied": False, "external": True, "message": "No Easy Apply — external link"}


# ── Instahyre ──────────────────────────────────────────────────

async def _instahyre_dismiss_modal(page):
    """Handle the 'Apply to similar jobs' modal."""
    await page.wait_for_timeout(1000)

    modal_text = page.locator("text='Application successful'")
    if await modal_text.count() > 0:
        modal_apply = page.locator("[ng-click='applyBulk()']")
        if await modal_apply.count() > 0:
            await modal_apply.first.click()
            await page.wait_for_timeout(2000)
            return
        modal_cancel = page.locator("[ng-click='applyBulkCancel()']")
        if await modal_cancel.count() > 0:
            await modal_cancel.first.click()
            await page.wait_for_timeout(1000)
            return

    close_btn = page.locator("button.close:visible, [aria-label='Close']:visible")
    if await close_btn.count() > 0:
        await close_btn.first.click()
        await page.wait_for_timeout(500)

    try:
        await page.keyboard.press("Escape")
        await page.wait_for_timeout(500)
    except Exception:
        pass


async def _instahyre_click_apply(page) -> bool:
    apply_btn = page.locator("[ng-click='submitChoice(opp, true)']")
    if await apply_btn.count() > 0:
        await apply_btn.first.click()
        await page.wait_for_timeout(2000)
        await _instahyre_dismiss_modal(page)
        return True
    return False


async def _apply_instahyre(page, job: Job) -> dict:
    company_lower = job.company.lower()
    title_lower = job.title.lower()

    # Dismiss any leftover modal
    if "instahyre.com" in page.url:
        await _instahyre_dismiss_modal(page)

    # Fast path: if Apply button already visible (chained from previous)
    apply_btn = page.locator("[ng-click='submitChoice(opp, true)']")
    if await apply_btn.count() > 0:
        detail_title = page.locator("div.company-name:visible")
        if await detail_title.count() > 0:
            showing = (await detail_title.first.inner_text()).strip().lower()
            if company_lower in showing or title_lower in showing:
                if await _instahyre_click_apply(page):
                    return {"applied": True, "message": f"Applied: {job.company} - {job.title}"}

    # Navigate if needed
    view_btns = page.locator("button.button-interested")
    if "instahyre.com/candidate/opportunities" not in page.url or await view_btns.count() == 0:
        if await apply_btn.count() == 0:
            await page.goto("https://www.instahyre.com/candidate/opportunities/?matching=true",
                             wait_until="domcontentloaded", timeout=15000)
            await page.wait_for_timeout(4000)

    if "login" in page.url:
        return {"applied": False, "message": "Instahyre login required"}

    # Find job in the list
    for _ in range(3):
        containers = await page.query_selector_all("div[ng-repeat*='opportunities']")

        for container in containers:
            name_el = await container.query_selector("div.company-name")
            if not name_el:
                continue

            card_text = (await name_el.inner_text()).strip().lower()
            if company_lower not in card_text and title_lower not in card_text:
                continue

            view_btn = await container.query_selector("button.button-interested")
            if not view_btn:
                continue

            await view_btn.click()
            await page.wait_for_timeout(3000)

            if await _instahyre_click_apply(page):
                return {"applied": True, "message": f"Applied: {job.company} - {job.title}"}

            return {"applied": False, "message": f"Apply button not found: {job.company}"}

        next_btn = page.locator("[ng-click='nextPage()']")
        if await next_btn.count() > 0:
            await next_btn.first.click()
            await page.wait_for_timeout(3000)
        else:
            break

    # Job not in list = already applied or expired
    return {"applied": True, "message": f"Already applied/not listed: {job.company}"}


if __name__ == "__main__":
    _load_store()
    print("\n  Job Bot Dashboard")
    print("  Open: http://localhost:5050\n")
    import webbrowser
    webbrowser.open("http://localhost:5050")
    app.run(host="0.0.0.0", port=5050, debug=False)
