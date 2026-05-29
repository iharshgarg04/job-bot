"""Indeed.com job scraper using Playwright (stealth mode)."""

from playwright.async_api import async_playwright
from scrapers.base import BaseScraper, Job
from scrapers.browser import launch_browser, close_browser


class IndeedScraper(BaseScraper):
    name = "indeed"

    async def scrape(self, titles: list[str], locations: list[str], experience: int) -> list[Job]:
        jobs = []
        search_titles = titles[:3]
        search_locations = ["Bangalore", "Gurugram", "Pune", "Hyderabad"]

        async with async_playwright() as p:
            browser = await launch_browser(p)
            page = browser.pages[0] if browser.pages else await browser.new_page()
            await page.add_init_script('Object.defineProperty(navigator, "webdriver", {get: () => undefined})')

            for title in search_titles:
                for location in search_locations:
                    new_jobs = await self._search(page, title, location)
                    jobs.extend(new_jobs)

            await close_browser(browser)

        seen = set()
        unique = []
        for job in jobs:
            if job.url and job.url not in seen:
                seen.add(job.url)
                unique.append(job)
        return unique

    async def _search(self, page, title: str, location: str) -> list[Job]:
        jobs = []
        url = f"https://in.indeed.com/jobs?q={title}&l={location}&fromage=14"

        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=15000)
            await page.wait_for_timeout(3000)

            cards = await page.query_selector_all("div.job_seen_beacon")

            for card in cards[:15]:
                try:
                    title_el = await card.query_selector("a.jcs-JobTitle span")
                    company_el = await card.query_selector("[data-testid='company-name'], span.css-1h7lukg")
                    location_el = await card.query_selector("[data-testid='text-location'], div.css-1restlb")
                    link_el = await card.query_selector("a.jcs-JobTitle")
                    sal_el = await card.query_selector("div.salary-snippet-container span, div.metadata.salary-snippet-container")
                    snippet_el = await card.query_selector("div.job-snippet")

                    job_title = await title_el.inner_text() if title_el else ""
                    if not job_title:
                        continue

                    job_url = ""
                    if link_el:
                        href = await link_el.get_attribute("href")
                        if href:
                            job_url = f"https://in.indeed.com{href}" if href.startswith("/") else href

                    job = Job(
                        title=self._clean_text(job_title),
                        company=self._clean_text(await company_el.inner_text()) if company_el else "",
                        location=self._clean_text(await location_el.inner_text()) if location_el else location,
                        url=job_url,
                        source="indeed",
                        salary=self._clean_text(await sal_el.inner_text()) if sal_el else "",
                        description=self._clean_text(await snippet_el.inner_text()) if snippet_el else "",
                    )
                    jobs.append(job)
                except Exception:
                    continue

        except Exception as e:
            print(f"  [indeed] Error scraping {title} in {location}: {e}")

        return jobs
