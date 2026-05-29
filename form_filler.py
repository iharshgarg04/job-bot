"""Smart form filler — auto-fills job application forms on company career sites."""

import asyncio
import subprocess
import tempfile
import os
from profile import PROFILE
from config import RESUME_PDF_PATH

# Common field values from your profile
FIELD_DATA = {
    "name": PROFILE["name"],
    "first_name": "Harsh",
    "last_name": "Garg",
    "email": PROFILE["email"],
    "phone": PROFILE["phone"],
    "linkedin": PROFILE["linkedin"],
    "github": PROFILE["github"],
    "location": "Gurugram, India",
    "current_company": PROFILE["current_company"],
    "current_title": PROFILE["current_role"],
    "experience_years": "1",
    "salary_expected": "1800000",
    "notice_period": "15 days",
    "website": PROFILE["github"],
    "summary": PROFILE["summary"],
}


async def auto_fill_and_apply(page, job_url: str) -> dict:
    """Detect form on current page, fill it, and submit. Page should already be loaded."""
    try:
        current_url = page.url
        content = await page.content()

        # If we're on a job listing page (not the form), find and click Apply
        apply_btn = page.locator(
            "a:has-text('Apply Now'), a:has-text('Apply for this job'), "
            "button:has-text('Apply Now'), button:has-text('Apply for this job'), "
            "a:has-text('Apply'), button:has-text('Apply')"
        )
        if await apply_btn.count() > 0:
            # Check if there's actually a form on the page already
            form_inputs = await page.query_selector_all("input[type='text'], input[type='email'], textarea")
            if len(form_inputs) < 2:
                # No form yet — need to click Apply to get to it
                pages_before = set(p.url for p in page.context.pages)
                await apply_btn.first.click()
                await page.wait_for_timeout(3000)

                # Check for new tab
                for p in page.context.pages:
                    if p.url not in pages_before and p.url != "about:blank":
                        page = p
                        await page.wait_for_load_state("domcontentloaded")
                        break

                current_url = page.url
                content = await page.content()

        # Detect ATS platform
        platform = _detect_platform(current_url, content)

        if platform == "greenhouse":
            return await _fill_greenhouse(page)
        elif platform == "lever":
            return await _fill_lever(page)
        elif platform == "workday":
            return await _fill_workday(page)
        elif platform == "ashby":
            return await _fill_ashby(page)
        elif platform == "smartrecruiters":
            return await _fill_smartrecruiters(page)
        else:
            # Unknown form — use AI to analyze and fill
            return await _fill_generic(page)

    except Exception as e:
        return {"applied": False, "message": f"Form fill error: {str(e)}"}


def _detect_platform(url: str, html: str) -> str:
    """Detect which ATS platform the application form uses."""
    url_lower = url.lower()
    html_lower = html.lower()

    if "greenhouse.io" in url_lower or "boards.greenhouse" in url_lower:
        return "greenhouse"
    if "lever.co" in url_lower or "jobs.lever" in url_lower:
        return "lever"
    if "workday" in url_lower or "myworkdayjobs" in url_lower:
        return "workday"
    if "ashbyhq.com" in url_lower:
        return "ashby"
    if "smartrecruiters" in url_lower:
        return "smartrecruiters"

    # Check HTML content for ATS signatures
    if "greenhouse" in html_lower and "application" in html_lower:
        return "greenhouse"
    if "lever" in html_lower and "lever-application" in html_lower:
        return "lever"

    return "unknown"


async def _fill_greenhouse(page) -> dict:
    """Fill Greenhouse application form."""
    try:
        # Name
        await _fill_input(page, "#first_name, input[name*='first_name']", FIELD_DATA["first_name"])
        await _fill_input(page, "#last_name, input[name*='last_name']", FIELD_DATA["last_name"])

        # Contact
        await _fill_input(page, "#email, input[name*='email']", FIELD_DATA["email"])
        await _fill_input(page, "#phone, input[name*='phone']", FIELD_DATA["phone"])

        # LinkedIn & links
        await _fill_input(page, "input[name*='linkedin'], input[id*='linkedin']", FIELD_DATA["linkedin"])
        await _fill_input(page, "input[name*='github'], input[id*='github']", FIELD_DATA["github"])
        await _fill_input(page, "input[name*='website'], input[id*='website']", FIELD_DATA["github"])

        # Resume upload
        await _upload_resume(page)

        # Location
        await _fill_input(page, "input[name*='location'], input[id*='location']", FIELD_DATA["location"])

        # Submit
        return await _click_submit(page)

    except Exception as e:
        return {"applied": False, "message": f"Greenhouse form error: {e}"}


async def _fill_lever(page) -> dict:
    """Fill Lever application form."""
    try:
        # Lever has a simpler form
        await _fill_input(page, "input[name='name']", FIELD_DATA["name"])
        await _fill_input(page, "input[name='email']", FIELD_DATA["email"])
        await _fill_input(page, "input[name='phone']", FIELD_DATA["phone"])
        await _fill_input(page, "input[name='urls[LinkedIn]'], input[name*='linkedin']", FIELD_DATA["linkedin"])
        await _fill_input(page, "input[name='urls[GitHub]'], input[name*='github']", FIELD_DATA["github"])
        await _fill_input(page, "input[name='org'], input[name*='company']", FIELD_DATA["current_company"])

        # Resume
        await _upload_resume(page)

        # Cover letter textarea
        await _fill_input(page, "textarea[name='comments'], textarea[name*='cover']", FIELD_DATA["summary"])

        return await _click_submit(page)

    except Exception as e:
        return {"applied": False, "message": f"Lever form error: {e}"}


async def _fill_workday(page) -> dict:
    """Fill Workday application form — complex multi-step."""
    try:
        # Workday forms are multi-step, try basic fields
        await _fill_input(page, "input[data-automation-id*='name'], input[aria-label*='Name']", FIELD_DATA["name"])
        await _fill_input(page, "input[data-automation-id*='email'], input[aria-label*='Email']", FIELD_DATA["email"])
        await _fill_input(page, "input[data-automation-id*='phone'], input[aria-label*='Phone']", FIELD_DATA["phone"])

        await _upload_resume(page)
        return await _click_submit(page)

    except Exception as e:
        return {"applied": False, "message": f"Workday form error: {e}"}


async def _fill_ashby(page) -> dict:
    """Fill Ashby application form."""
    try:
        await _fill_input(page, "input[name*='name'], input[name*='Name']", FIELD_DATA["name"])
        await _fill_input(page, "input[name*='email'], input[name*='Email']", FIELD_DATA["email"])
        await _fill_input(page, "input[name*='phone'], input[name*='Phone']", FIELD_DATA["phone"])
        await _fill_input(page, "input[name*='linkedin'], input[name*='LinkedIn']", FIELD_DATA["linkedin"])

        await _upload_resume(page)
        return await _click_submit(page)

    except Exception as e:
        return {"applied": False, "message": f"Ashby form error: {e}"}


async def _fill_smartrecruiters(page) -> dict:
    """Fill SmartRecruiters application form."""
    try:
        await _fill_input(page, "input[name*='firstName']", FIELD_DATA["first_name"])
        await _fill_input(page, "input[name*='lastName']", FIELD_DATA["last_name"])
        await _fill_input(page, "input[name*='email']", FIELD_DATA["email"])
        await _fill_input(page, "input[name*='phone']", FIELD_DATA["phone"])

        await _upload_resume(page)
        return await _click_submit(page)

    except Exception as e:
        return {"applied": False, "message": f"SmartRecruiters form error: {e}"}


async def _fill_generic(page) -> dict:
    """Use Claude AI to analyze unknown forms and fill them."""
    try:
        # Get all form fields from the page
        fields = await page.evaluate("""() => {
            const inputs = document.querySelectorAll('input, textarea, select');
            return Array.from(inputs).map(el => ({
                tag: el.tagName,
                type: el.type || '',
                name: el.name || '',
                id: el.id || '',
                placeholder: el.placeholder || '',
                label: el.labels?.[0]?.textContent?.trim() || '',
                ariaLabel: el.getAttribute('aria-label') || '',
                required: el.required,
                value: el.value || '',
                visible: el.offsetParent !== null,
            })).filter(f => f.visible && f.type !== 'hidden' && f.type !== 'submit');
        }""")

        if not fields:
            return {"applied": False, "message": "No form fields found on page"}

        # Try to fill obvious fields without AI first
        filled_count = 0
        for field in fields:
            label = f"{field['label']} {field['name']} {field['id']} {field['placeholder']} {field['ariaLabel']}".lower()
            selector = f"#{field['id']}" if field['id'] else f"[name='{field['name']}']" if field['name'] else None

            if not selector:
                continue

            value = _match_field_to_value(label, field['type'])
            if value:
                await _fill_input(page, selector, value)
                filled_count += 1

        # Upload resume
        await _upload_resume(page)

        if filled_count == 0:
            # No fields matched — use Claude to figure out the mapping
            return await _fill_with_claude(page, fields)

        return await _click_submit(page)

    except Exception as e:
        return {"applied": False, "message": f"Generic form error: {e}"}


async def _fill_with_claude(page, fields: list) -> dict:
    """Use Claude CLI to analyze form fields and determine values."""
    import shutil
    if not shutil.which("claude"):
        return {"applied": False, "message": "Unknown form — Claude CLI needed for AI form-fill"}

    fields_desc = "\n".join([
        f"- {f['tag']} type={f['type']} name={f['name']} id={f['id']} "
        f"label=\"{f['label']}\" placeholder=\"{f['placeholder']}\" required={f['required']}"
        for f in fields[:20]
    ])

    prompt = f"""You are filling a job application form. Map these form fields to the candidate's data.

CANDIDATE:
- Name: Harsh Garg
- Email: iharshgarg04@gmail.com
- Phone: +91-8864833148
- LinkedIn: https://linkedin.com/in/harsh-garg04
- GitHub: https://github.com/iharshgarg04
- Location: Gurugram, India
- Current Role: Software Developer at Coding Ninjas
- Experience: 1 year
- Expected Salary: 18 LPA
- Notice Period: 15 days

FORM FIELDS:
{fields_desc}

Return a JSON array of objects to fill:
[{{"selector": "#field_id or [name='field_name']", "value": "value to fill"}}]

Only include fields you can confidently fill. Use id-based selectors when available.
Return ONLY valid JSON array."""

    try:
        result = subprocess.run(
            ["claude", "-p", "--output-format", "text"],
            input=prompt,
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode != 0:
            return {"applied": False, "message": "Claude couldn't analyze form"}

        import json
        text = result.stdout.strip()
        if "```" in text:
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
            text = text.strip()

        mappings = json.loads(text)
        for m in mappings:
            await _fill_input(page, m["selector"], m["value"])

        await _upload_resume(page)
        return await _click_submit(page)

    except Exception as e:
        return {"applied": False, "message": f"AI form-fill error: {e}"}


def _match_field_to_value(label: str, field_type: str) -> str:
    """Match a form field label to the right value from profile."""
    label = label.lower()

    # File inputs handled separately
    if field_type == "file":
        return ""

    # Name fields
    if any(k in label for k in ["full name", "your name", "candidate name"]):
        return FIELD_DATA["name"]
    if "first" in label and "name" in label:
        return FIELD_DATA["first_name"]
    if "last" in label and "name" in label:
        return FIELD_DATA["last_name"]
    if "name" in label and "company" not in label and "user" not in label:
        return FIELD_DATA["name"]

    # Contact
    if "email" in label:
        return FIELD_DATA["email"]
    if "phone" in label or "mobile" in label or "contact" in label:
        return FIELD_DATA["phone"]

    # Links
    if "linkedin" in label:
        return FIELD_DATA["linkedin"]
    if "github" in label:
        return FIELD_DATA["github"]
    if "website" in label or "portfolio" in label or "url" in label:
        return FIELD_DATA["github"]

    # Professional
    if "current company" in label or "current org" in label or "employer" in label:
        return FIELD_DATA["current_company"]
    if "current title" in label or "current role" in label or "designation" in label or "job title" in label:
        return FIELD_DATA["current_title"]
    if "experience" in label and ("year" in label or "yr" in label):
        return FIELD_DATA["experience_years"]
    if "salary" in label or "ctc" in label or "compensation" in label:
        return FIELD_DATA["salary_expected"]
    if "notice" in label:
        return FIELD_DATA["notice_period"]

    # Location
    if "city" in label or "location" in label or "address" in label:
        return FIELD_DATA["location"]

    # Cover letter / summary
    if "cover" in label or "summary" in label or "about" in label or "why" in label:
        return FIELD_DATA["summary"]

    return ""


async def _fill_input(page, selector: str, value: str):
    """Safely fill an input field."""
    if not value:
        return
    try:
        el = page.locator(selector)
        if await el.count() > 0:
            tag = await el.first.evaluate("el => el.tagName")
            if tag == "SELECT":
                # For select, try to find matching option
                await el.first.select_option(label=value)
            elif tag == "TEXTAREA":
                await el.first.fill(value)
            else:
                input_type = await el.first.get_attribute("type") or "text"
                if input_type in ("text", "email", "tel", "url", "number", "search", ""):
                    await el.first.fill(value)
    except Exception:
        pass


async def _upload_resume(page):
    """Upload resume PDF to any file input on the page."""
    try:
        file_inputs = page.locator("input[type='file']")
        if await file_inputs.count() > 0:
            await file_inputs.first.set_input_files(RESUME_PDF_PATH)
            await page.wait_for_timeout(1000)
    except Exception:
        pass


async def _click_submit(page) -> dict:
    """Find and click the submit/apply button."""
    submit_selectors = [
        "button[type='submit']",
        "input[type='submit']",
        "button:has-text('Submit Application')",
        "button:has-text('Submit')",
        "button:has-text('Apply')",
        "button:has-text('Send Application')",
        "button:has-text('Complete Application')",
        "a:has-text('Submit Application')",
    ]

    for selector in submit_selectors:
        btn = page.locator(selector)
        if await btn.count() > 0:
            await btn.first.click()
            await page.wait_for_timeout(3000)

            # Check for success indicators
            success = page.locator(
                "text='Thank you', text='Application submitted', "
                "text='application has been received', text='Successfully applied', "
                "text='submitted successfully'"
            )
            if await success.count() > 0:
                return {"applied": True, "message": "Form submitted successfully"}

            return {"applied": True, "message": "Clicked submit on application form"}

    return {"applied": False, "message": "Submit button not found"}
