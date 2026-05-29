"""Naukri.com job scraper using Playwright (non-headless + stealth)."""

from playwright.async_api import async_playwright
from scrapers.base import BaseScraper, Job
from scrapers.browser import launch_browser, close_browser


class NaukriScraper(BaseScraper):
    name = "naukri"

    async def scrape(self, titles: list[str], locations: list[str], experience: int) -> list[Job]:
        jobs = []
        search_titles = titles[:5]
        search_locations = ["bangalore", "gurugram", "pune", "hyderabad"]

        async with async_playwright() as p:
            browser = await launch_browser(p)
            page = browser.pages[0] if browser.pages else await browser.new_page()
            await page.add_init_script('Object.defineProperty(navigator, "webdriver", {get: () => undefined})')

            for title in search_titles:
                for location in search_locations:
                    new_jobs = await self._search(page, title, location, experience)
                    jobs.extend(new_jobs)

            await close_browser(browser)

        # Deduplicate by URL
        seen = set()
        unique = []
        for job in jobs:
            if job.url and job.url not in seen:
                seen.add(job.url)
                unique.append(job)
        return unique

    async def _search(self, page, title: str, location: str, experience: int) -> list[Job]:
        jobs = []
        query = title.replace(" ", "-").lower()
        loc = location.lower()
        url = f"https://www.naukri.com/{query}-jobs-in-{loc}?experience={experience}"

        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=15000)
            await page.wait_for_timeout(4000)

            cards = await page.query_selector_all("div.cust-job-tuple")

            for card in cards[:20]:
                try:
                    title_el = await card.query_selector("a.title")
                    company_el = await card.query_selector("a.comp-name")
                    loc_el = await card.query_selector("span.locWdth")
                    sal_el = await card.query_selector("span.sal-wrap span.ni-job-tuple-icon-srp-rupee span")
                    exp_el = await card.query_selector("span.exp-wrap span.ni-job-tuple-icon-srp-experience span")
                    tags = await card.query_selector_all("ul.tags-gt li.tag-li")
                    desc_el = await card.query_selector("span.job-desc")

                    job_title = await title_el.inner_text() if title_el else ""
                    job_url = await title_el.get_attribute("href") if title_el else ""

                    if not job_title or not job_url:
                        continue

                    skills = []
                    for t in tags[:10]:
                        skills.append(self._clean_text(await t.inner_text()))

                    job = Job(
                        title=self._clean_text(job_title),
                        company=self._clean_text(await company_el.inner_text()) if company_el else "",
                        location=self._clean_text(await loc_el.inner_text()) if loc_el else location,
                        url=job_url,
                        source="naukri",
                        description=self._clean_text(await desc_el.inner_text()) if desc_el else "",
                        salary=self._clean_text(await sal_el.inner_text()) if sal_el else "",
                        experience=self._clean_text(await exp_el.inner_text()) if exp_el else "",
                        skills=skills,
                    )
                    jobs.append(job)
                except Exception:
                    continue

        except Exception as e:
            print(f"  [naukri] Error scraping {title} in {location}: {e}")

        return jobs
