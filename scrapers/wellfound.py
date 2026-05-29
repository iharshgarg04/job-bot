"""Foundit (formerly Monster India) job scraper — replaces Wellfound (Cloudflare blocked)."""

from playwright.async_api import async_playwright
from scrapers.base import BaseScraper, Job
from scrapers.browser import launch_browser, close_browser


class WellfoundScraper(BaseScraper):
    """Actually scrapes Foundit.in (Monster India) since Wellfound blocks all bots."""

    name = "foundit"

    async def scrape(self, titles: list[str], locations: list[str], experience: int) -> list[Job]:
        jobs = []
        search_titles = titles[:4]
        search_locations = ["Bangalore", "Gurugram", "Pune", "Hyderabad"]

        async with async_playwright() as p:
            browser = await launch_browser(p)
            page = browser.pages[0] if browser.pages else await browser.new_page()
            await page.add_init_script('Object.defineProperty(navigator, "webdriver", {get: () => undefined})')

            for title in search_titles:
                for location in search_locations:
                    new_jobs = await self._search(page, title, location, experience)
                    jobs.extend(new_jobs)

            await close_browser(browser)

        seen = set()
        unique = []
        for job in jobs:
            if job.url and job.url not in seen:
                seen.add(job.url)
                unique.append(job)
        return unique

    async def _search(self, page, title: str, location: str, experience: int) -> list[Job]:
        jobs = []
        url = f"https://www.foundit.in/srp/results?query={title.replace(' ', '+')}&locations={location}&experienceRanges={experience}~3"

        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=15000)
            await page.wait_for_timeout(4000)

            cards = await page.query_selector_all("div.cardContainer[id]")

            for card in cards[:15]:
                try:
                    title_el = await card.query_selector("div.jobTitle, #jobCardTitle")
                    company_el = await card.query_selector("div.companyName p")
                    loc_el = await card.query_selector("div.details.location")
                    exp_el = await card.query_selector("div.experienceSalary span.details")
                    sal_el = await card.query_selector("div.experienceSalary div.bodyRow:nth-child(2) span.details")

                    job_title = await title_el.inner_text() if title_el else ""
                    if not job_title:
                        continue

                    # Get job ID for URL
                    card_id = await card.get_attribute("id")
                    job_url = f"https://www.foundit.in/job/{card_id}" if card_id else url

                    job = Job(
                        title=self._clean_text(job_title),
                        company=self._clean_text(await company_el.inner_text()) if company_el else "",
                        location=self._clean_text(await loc_el.inner_text()) if loc_el else location,
                        url=job_url,
                        source="foundit",
                        experience=self._clean_text(await exp_el.inner_text()) if exp_el else "",
                        salary=self._clean_text(await sal_el.inner_text()) if sal_el else "",
                    )
                    jobs.append(job)
                except Exception:
                    continue

        except Exception as e:
            print(f"  [foundit] Error scraping {title} in {location}: {e}")

        return jobs
