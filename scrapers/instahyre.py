"""Instahyre job scraper using Playwright with persistent login."""

from playwright.async_api import async_playwright
from scrapers.base import BaseScraper, Job
from scrapers.browser import launch_browser, close_browser


class InstahyreScraper(BaseScraper):
    name = "instahyre"

    async def scrape(self, titles: list[str], locations: list[str], experience: int) -> list[Job]:
        jobs = []

        async with async_playwright() as p:
            browser = await launch_browser(p)
            page = browser.pages[0] if browser.pages else await browser.new_page()
            await page.add_init_script('Object.defineProperty(navigator, "webdriver", {get: () => undefined})')

            # Go to opportunities page (recommended jobs, requires login)
            await page.goto("https://www.instahyre.com/candidate/opportunities/?matching=true",
                            wait_until="domcontentloaded", timeout=15000)
            await page.wait_for_timeout(4000)

            # Check if logged in
            if "login" in page.url or "signup" in page.url:
                print("  [instahyre] Not logged in. Run 'python3 login.py' first.")
                await close_browser(browser)
                return jobs

            # Scrape jobs from the opportunities page
            jobs = await self._extract_jobs(page)
            print(f"  [instahyre] Page 1: {len(jobs)} jobs")

            # Click through pages
            for page_num in range(2, 6):
                try:
                    btns = await page.query_selector_all("[ng-click='nthPage(pageNumber)']")
                    clicked = False
                    for btn in btns:
                        txt = (await btn.inner_text()).strip()
                        if txt == str(page_num):
                            await btn.click()
                            await page.wait_for_timeout(3000)
                            clicked = True
                            break
                    if not clicked:
                        break
                    new_jobs = await self._extract_jobs(page)
                    print(f"  [instahyre] Page {page_num}: {len(new_jobs)} jobs")
                    jobs.extend(new_jobs)
                except Exception:
                    break

            await close_browser(browser)

        # Deduplicate
        seen = set()
        unique = []
        for job in jobs:
            key = f"{job.title}|{job.company}"
            if key not in seen:
                seen.add(key)
                unique.append(job)
        return unique

    async def _extract_jobs(self, page) -> list[Job]:
        """Extract job details from the current Instahyre page."""
        jobs = []

        cards = await page.query_selector_all("div.employer-details")

        for card in cards:
            try:
                title_el = await card.query_selector("div.company-name")
                loc_el = await card.query_selector("div.employer-locations span.ng-binding")
                skill_els = await card.query_selector_all("ul.tags li, span.tag, li.ng-binding.ng-scope")

                raw_title = self._clean_text(await title_el.inner_text()) if title_el else ""
                if not raw_title:
                    continue

                # Title format is "Company - Job Title"
                parts = raw_title.split(" - ", 1)
                if len(parts) == 2:
                    company = parts[0].strip()
                    title = parts[1].strip()
                else:
                    company = ""
                    title = raw_title

                location = ""
                if loc_el:
                    loc_text = self._clean_text(await loc_el.inner_text())
                    location = loc_text.replace("Job available in ", "").strip()

                skills = []
                for s in skill_els[:8]:
                    txt = (await s.inner_text()).strip()
                    if txt and not txt.startswith("+"):
                        skills.append(txt)

                # Create unique URL using company + title (Instahyre has no per-job URLs)
                slug = f"{company}-{title}".lower().replace(" ", "-").replace("/", "-")
                job = Job(
                    title=title,
                    company=company,
                    location=location,
                    url=f"https://www.instahyre.com/candidate/opportunities/?job={slug}",
                    source="instahyre",
                    skills=skills,
                )
                jobs.append(job)
            except Exception:
                continue

        return jobs
