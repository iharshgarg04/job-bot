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

# Mapping: keyword in question → years of experience to fill
_SKILL_YEARS = {
    "ruby": "1", "rails": "1", "ruby on rails": "1",
    "angular": "1", "react": "1", "node": "1", "express": "1",
    "javascript": "2", "typescript": "1",
    "python": "1", "java": "1",
    "postgresql": "1", "mysql": "1", "sql": "1", "mongodb": "1", "redis": "1",
    "html": "2", "css": "2", "html and css": "2",
    "aws": "1", "docker": "1", "git": "2", "github": "2", "linux": "1",
    "rest": "1", "api": "1", "rest api": "1",
    "software": "1", "development": "1", "engineering": "1",
    "full stack": "1", "fullstack": "1", "backend": "1", "frontend": "1",
    "web": "1", "programming": "2", "coding": "2",
}


async def _linkedin_fill_questions(page):
    """Fill additional questions in LinkedIn Easy Apply forms."""
    # LinkedIn puts form fields in the main frame — find inputs with labels
    inputs = await page.query_selector_all("input")

    for inp in inputs:
        try:
            input_type = await inp.get_attribute("type") or "text"
            if input_type not in ("text", "number", ""):
                continue

            visible = await inp.is_visible()
            if not visible:
                continue

            val = await inp.input_value()
            if val:  # Already filled
                continue

            # Get label by walking up the DOM tree
            label_text = await inp.evaluate("""el => {
                let p = el.parentElement;
                for (let i = 0; i < 5 && p; i++) {
                    const labels = p.querySelectorAll('label, span, p');
                    for (const l of labels) {
                        const t = l.textContent.trim();
                        if (t.length > 5 && t.length < 150) return t;
                    }
                    p = p.parentElement;
                }
                return el.getAttribute('aria-label') || el.placeholder || '';
            }""")

            if not label_text:
                continue

            label_lower = label_text.lower()

            # "How many years of experience with X?"
            if "year" in label_lower and ("experience" in label_lower or "work" in label_lower):
                answer = "0"  # Default 0 if unknown skill
                for skill, years in _SKILL_YEARS.items():
                    if skill in label_lower:
                        answer = years
                        break
                await inp.fill(answer)

            # Expected CTC / salary
            elif ("expected" in label_lower or "desired" in label_lower) and ("ctc" in label_lower or "salary" in label_lower):
                await inp.fill("1800000")

            # CTC / salary (generic — assume expected)
            elif "ctc" in label_lower or "salary" in label_lower or "compensation" in label_lower:
                await inp.fill("1800000")

            # Notice period
            elif "notice" in label_lower:
                await inp.fill("30")

            # Current CTC
            elif "current" in label_lower and ("ctc" in label_lower or "salary" in label_lower or "compensation" in label_lower):
                await inp.fill("1200000")

            # Location
            elif "city" in label_lower or "location" in label_lower:
                await inp.fill("Gurugram")

            # GPA / percentage
            elif "gpa" in label_lower or "cgpa" in label_lower:
                await inp.fill("9.44")

            # LLM / AI tools question
            elif "llm" in label_lower or "coding agent" in label_lower or "copilot" in label_lower or "cursor" in label_lower:
                await inp.fill("1")

            # Generic number question — default to 1
            elif "how many" in label_lower:
                await inp.fill("1")

            # Any remaining empty required field with number validation — fill 1
            else:
                # Check if it expects a number
                is_number = input_type == "number"
                if not is_number:
                    # Check for error messages nearby suggesting number input
                    has_num_error = await inp.evaluate("""el => {
                        const p = el.parentElement;
                        if (!p) return false;
                        return p.textContent.includes('number') || p.textContent.includes('decimal');
                    }""")
                    is_number = has_num_error
                if is_number:
                    await inp.fill("1")

        except Exception:
            continue

    # Handle select dropdowns
    selects = await page.query_selector_all("select")
    for sel in selects:
        try:
            visible = await sel.is_visible()
            if not visible:
                continue
            val = await sel.input_value()
            if val:
                continue

            label_text = await sel.evaluate("""el => {
                let p = el.parentElement;
                for (let i = 0; i < 5 && p; i++) {
                    const labels = p.querySelectorAll('label, span, p');
                    for (const l of labels) {
                        const t = l.textContent.trim();
                        if (t.length > 5 && t.length < 150) return t;
                    }
                    p = p.parentElement;
                }
                return '';
            }""")

            label_lower = (label_text or "").lower()

            # Get available options
            options = await sel.query_selector_all("option")
            option_texts = []
            for opt in options:
                txt = (await opt.inner_text()).strip()
                if txt and txt != "Select an option":
                    option_texts.append(txt)

            if not option_texts:
                continue

            # Try preferred answers in order
            selected = False
            for preferred in ["Yes", "yes", "30 days", "1 month", "Immediately",
                              "Less than 1 year", "1 year", "0-1 years", "1-2 years"]:
                if preferred in option_texts:
                    await sel.select_option(label=preferred)
                    selected = True
                    break

            # If no preferred match, just select the first non-empty option
            if not selected and option_texts:
                await sel.select_option(label=option_texts[0])
        except Exception:
            continue


async def _apply_linkedin(page, job: Job) -> dict:
    if "login" in page.url or "authwall" in page.url:
        return {"applied": False, "message": "LinkedIn login required"}

    # Scroll to top so Easy Apply button is in viewport
    await page.evaluate("window.scrollTo(0, 0)")
    await page.wait_for_timeout(1000)

    # Find Easy Apply — could be <a> or <button>, use aria-label or text
    easy_apply = page.locator("a[aria-label*='Easy Apply'], button[aria-label*='Easy Apply'], a:has-text('Easy Apply'), button:has-text('Easy Apply')")

    # Filter: only the one in the job detail area (visible in viewport, not in job list sidebar)
    ea_count = await easy_apply.count()
    if ea_count == 0:
        # Check if it's an external "Apply" button (with external link icon)
        external = page.locator("a[aria-label*='Apply on company'], a:has-text('Apply'):visible")
        if await external.count() > 0:
            return {"applied": False, "external": True, "message": "External apply — no Easy Apply"}
        return {"applied": False, "external": True, "message": "No apply button found"}

    # Click the first visible Easy Apply
    await easy_apply.first.click()
    await page.wait_for_timeout(3000)

    # Now step through the Easy Apply form
    for step in range(8):
        await page.wait_for_timeout(1500)

        # Check for success
        done = page.locator("text='Application sent', text='application was sent', text='Your application was sent'")
        if await done.count() > 0:
            return {"applied": True, "message": "Applied via LinkedIn Easy Apply"}

        # Check for Submit button
        submit = page.locator("button:has-text('Submit application'), button:has-text('Submit')")
        submit_visible = False
        for i in range(await submit.count()):
            if await submit.nth(i).is_visible():
                submit_visible = True
                await submit.nth(i).click()
                await page.wait_for_timeout(3000)

                done = page.locator("text='Application sent', text='application was sent', text='Your application was sent'")
                if await done.count() > 0:
                    return {"applied": True, "message": "Applied via LinkedIn Easy Apply"}
                return {"applied": True, "message": "Submitted on LinkedIn"}

        if submit_visible:
            break

        # Upload resume if asked
        upload = page.locator("input[type='file']")
        if await upload.count() > 0:
            from config import RESUME_PDF_PATH
            try:
                await upload.first.set_input_files(RESUME_PDF_PATH)
                await page.wait_for_timeout(1000)
            except Exception:
                pass

        # Fill phone if empty
        phone_input = page.locator("input[name*='phoneNumber'], input[aria-label*='Phone'], input[id*='phone']")
        if await phone_input.count() > 0:
            try:
                val = await phone_input.first.input_value()
                if not val:
                    await phone_input.first.fill("8864833148")
            except Exception:
                pass

        # Fill additional questions (years of experience, etc.)
        await _linkedin_fill_questions(page)

        # Click Next/Continue/Review (find only visible ones)
        clicked = False
        for btn_text in ["Next", "Continue", "Review"]:
            btn = page.locator(f"button:has-text('{btn_text}'):visible")
            if await btn.count() > 0:
                prev_url = page.url
                await btn.first.click()
                await page.wait_for_timeout(1500)
                clicked = True
                break

        if not clicked:
            break

        # Detect if we're stuck (same step repeating — means validation errors)
        if step > 0:
            still_has_same_btn = False
            for btn_text in ["Review"]:
                btn = page.locator(f"button:has-text('{btn_text}'):visible")
                if await btn.count() > 0:
                    still_has_same_btn = True
            # If Review is still showing after clicking it, we're stuck on questions
            if still_has_same_btn and step >= 4:
                return {"applied": False, "external": True, "message": "Easy Apply has additional questions — needs manual input"}

    # Dismiss the modal if still open
    dismiss = page.locator("button[aria-label*='Dismiss'], button:has-text('Dismiss')")
    if await dismiss.count() > 0:
        try:
            await dismiss.first.click()
        except Exception:
            pass

    return {"applied": False, "external": True, "message": "Easy Apply form incomplete — needs manual input"}


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
