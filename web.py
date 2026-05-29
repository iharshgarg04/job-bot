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


async def _get_field_label(el) -> str:
    """Get the question/label text for a form element."""
    return await el.evaluate("""el => {
        let p = el.parentElement;
        for (let i = 0; i < 6 && p; i++) {
            const labels = p.querySelectorAll('label, span, p');
            for (const l of labels) {
                const t = l.textContent.trim();
                // Skip generic/nav labels, only return question-like text
                if (t.length > 10 && t.length < 200 &&
                    (t.includes('?') || t.includes('How') || t.includes('What') ||
                     t.includes('Are you') || t.includes('Do you') || t.includes('years') ||
                     t.includes('CTC') || t.includes('salary') || t.includes('notice') ||
                     t.includes('experience'))) {
                    return t;
                }
            }
            p = p.parentElement;
        }
        return el.getAttribute('aria-label') || '';
    }""")


def _get_predefined_answer(label: str, field_type: str):
    """Try to answer from predefined knowledge. Returns None if unknown."""
    ll = label.lower()

    # Years of experience with a skill
    if "year" in ll and ("experience" in ll or "work" in ll):
        for skill, years in _SKILL_YEARS.items():
            if skill in ll:
                return years
        return "0"

    # Notice period (check before CTC since "notice period" is specific)
    if "notice" in ll:
        return "30"

    # Current CTC (check before generic CTC)
    if "current" in ll and ("ctc" in ll or "salary" in ll or "compensation" in ll):
        return "1200000"

    # Expected CTC
    if ("expected" in ll or "desired" in ll) and ("ctc" in ll or "salary" in ll):
        return "1800000"

    # Generic CTC/salary
    if "ctc" in ll or "salary" in ll or "compensation" in ll:
        return "1800000"

    # Location
    if "city" in ll or "location" in ll:
        return "Gurugram"

    # GPA
    if "gpa" in ll or "cgpa" in ll:
        return "9.44"

    # LLM / AI tools
    if "llm" in ll or "coding agent" in ll or "copilot" in ll or "cursor" in ll or "claude" in ll:
        return "1"

    # Generic "how many"
    if "how many" in ll:
        return "1"

    return None


def _ask_claude_for_answers(questions: list[dict]) -> dict:
    """Use Claude CLI to answer application questions we can't handle from predefined data."""
    import subprocess
    import json

    questions_text = ""
    for i, q in enumerate(questions):
        questions_text += f"\n{i+1}. Question: \"{q['label']}\"\n"
        questions_text += f"   Field type: {q['type']}\n"
        if q.get("options"):
            questions_text += f"   Options: {q['options']}\n"

    prompt = f"""You are filling a job application form for this candidate:

Name: Harsh Garg
Role: Software Developer at Coding Ninjas (1 year experience)
Skills: Ruby on Rails, Angular, PostgreSQL, Redis, AWS, JavaScript, TypeScript, Docker, React, Node.js, Python, Java
Education: B.E. Computer Science, Chitkara University, CGPA 9.44/10
Current CTC: 12 LPA | Expected: 18 LPA | Notice: 30 days
Location: Gurugram, India
LinkedIn: linkedin.com/in/harsh-garg04 | GitHub: github.com/iharshgarg04
Uses AI tools: Claude Code, Cursor, GitHub Copilot

Answer these application questions. Be positive and confident. For yes/no, prefer "Yes". For numbers, give realistic values.

{questions_text}

Return ONLY a JSON object mapping question number to answer:
{{"1": "answer1", "2": "answer2"}}

For select/dropdown questions, return the exact option text to select."""

    try:
        result = subprocess.run(
            ["claude", "-p", "--output-format", "text"],
            input=prompt,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            return {}

        text = result.stdout.strip()
        if "```" in text:
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
            text = text.strip()

        return json.loads(text)
    except Exception:
        return {}


async def _linkedin_fill_questions(page):
    """Fill additional questions in LinkedIn Easy Apply forms. Uses Claude for unknown questions."""
    unanswered = []  # Questions we need Claude for

    # Pass 1: Fill inputs with predefined answers, collect unknowns
    inputs = await page.query_selector_all("input")
    input_map = {}  # index → input element for Claude answers

    for inp in inputs:
        try:
            input_type = await inp.get_attribute("type") or "text"
            if input_type not in ("text", "number", ""):
                continue
            if not await inp.is_visible():
                continue
            val = await inp.input_value()
            if val:
                continue

            label = await _get_field_label(inp)
            if not label:
                continue

            answer = _get_predefined_answer(label, input_type)
            if answer is not None:
                await inp.fill(answer)
            else:
                # Collect for Claude
                idx = len(unanswered) + 1
                unanswered.append({"label": label, "type": input_type, "element_type": "input"})
                input_map[str(idx)] = inp
        except Exception:
            continue

    # Pass 1b: Fill select dropdowns with predefined answers, collect unknowns
    selects = await page.query_selector_all("select")
    select_map = {}

    for sel in selects:
        try:
            if not await sel.is_visible():
                continue

            # Check if already has a non-default value
            sel_text = await sel.evaluate("el => el.options[el.selectedIndex]?.text || ''")
            if sel_text and "select" not in sel_text.lower():
                continue

            label = await _get_field_label(sel)

            # Get options (strip whitespace/newlines from LinkedIn's markup)
            options = await sel.query_selector_all("option")
            option_texts = []
            for opt in options:
                txt = " ".join((await opt.inner_text()).split()).strip()
                if txt and "select" not in txt.lower():
                    option_texts.append(txt)

            if not option_texts:
                continue

            # Try predefined preferences
            selected = False
            for preferred in ["Yes", "yes", "30 days", "1 month", "Immediately",
                              "Less than 1 year", "1 year", "0-1 years", "1-2 years"]:
                if preferred in option_texts:
                    await sel.select_option(label=preferred)
                    selected = True
                    break

            if not selected:
                # Collect for Claude
                idx = len(unanswered) + 1
                unanswered.append({"label": label, "type": "select", "options": option_texts, "element_type": "select"})
                select_map[str(idx)] = (sel, option_texts)
        except Exception:
            continue

    # Pass 2: Ask Claude for remaining unanswered questions
    if unanswered:
        print(f"  Asking Claude for {len(unanswered)} questions...")
        claude_answers = _ask_claude_for_answers(unanswered)

        for key, answer in claude_answers.items():
            try:
                if key in input_map:
                    await input_map[key].fill(str(answer))
                elif key in select_map:
                    sel, opts = select_map[key]
                    # Find closest matching option
                    answer_lower = str(answer).lower()
                    matched = False
                    for opt in opts:
                        if opt.lower() == answer_lower or answer_lower in opt.lower():
                            await sel.select_option(label=opt)
                            matched = True
                            break
                    if not matched and opts:
                        await sel.select_option(label=opts[0])
            except Exception:
                continue


async def _apply_linkedin(page, job: Job) -> dict:
    if "login" in page.url or "authwall" in page.url:
        return {"applied": False, "message": "LinkedIn login required"}

    # Scroll to top so Easy Apply button is in viewport
    await page.evaluate("window.scrollTo(0, 0)")
    await page.wait_for_timeout(1000)

    # Check if it's external apply (no Easy Apply)
    page_text = await page.inner_text("body")
    has_easy_apply = "Easy Apply" in page_text

    if not has_easy_apply:
        return {"applied": False, "external": True, "message": "No Easy Apply — external link"}

    # Find Easy Apply button in viewport (top of page, job detail area)
    easy_apply = page.locator("a[aria-label*='Easy Apply'], button[aria-label*='Easy Apply']")
    ea_count = await easy_apply.count()

    if ea_count == 0:
        # Try text-based
        easy_apply = page.locator("a:has-text('Easy Apply'), button:has-text('Easy Apply')")
        ea_count = await easy_apply.count()

    if ea_count == 0:
        return {"applied": False, "external": True, "message": "Easy Apply button not found"}

    # Click using JavaScript to prevent navigation (it's an <a> tag)
    await easy_apply.first.evaluate("el => el.click()")
    await page.wait_for_timeout(5000)

    # Check if modal opened — look for Next/Submit button
    form_btn = page.locator("button:has-text('Next'):visible, button:has-text('Submit'):visible, button:has-text('Review'):visible")
    if await form_btn.count() == 0:
        # Try clicking normally as fallback
        try:
            await easy_apply.first.click()
            await page.wait_for_timeout(5000)
        except Exception:
            pass

        if await form_btn.count() == 0:
            if "search" in page.url:
                await page.go_back()
                await page.wait_for_timeout(3000)
            return {"applied": False, "external": True, "message": "Easy Apply modal didn't open"}

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
            still_review = False
            for btn_text in ["Review"]:
                btn = page.locator(f"button:has-text('{btn_text}'):visible")
                if await btn.count() > 0:
                    still_review = True

            if still_review and step == 3:
                # One more try: re-run question filler (Claude might answer differently)
                print("  Retrying with Claude for unfilled questions...")
                await _linkedin_fill_questions(page)
                await page.wait_for_timeout(1000)
            elif still_review and step >= 5:
                return {"applied": False, "external": True, "message": "Easy Apply has questions Claude couldn't answer"}

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
