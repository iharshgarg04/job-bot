"""Job matching engine using Claude CLI (Max plan) + keyword scoring."""

import json
import subprocess
import shutil
import tempfile
import os
from scrapers.base import Job
from profile import PROFILE


def keyword_score(job: Job) -> float:
    """Quick keyword-based relevance score (0-100)."""
    score = 0.0
    all_skills = PROFILE["primary_skills"] + PROFILE["secondary_skills"]

    job_text = f"{job.title} {job.description} {' '.join(job.skills)} {job.company}".lower()

    # Title match — weighted heavily since LinkedIn jobs often only have titles
    title_lower = job.title.lower()
    strong_title_keywords = ["software developer", "software engineer", "sde", "full stack developer",
                              "backend developer", "frontend developer", "web developer",
                              "ruby on rails", "angular developer", "react developer"]
    for kw in strong_title_keywords:
        if kw in title_lower:
            score += 25

    weak_title_keywords = ["software", "developer", "engineer", "full stack", "backend",
                           "frontend", "ruby", "rails", "angular", "react", "python",
                           "javascript", "typescript", "node"]
    for kw in weak_title_keywords:
        if kw in title_lower:
            score += 10

    # Skill match (from description/skills if available)
    for skill in all_skills:
        if skill.lower() in job_text:
            score += 5

    # Primary skills worth more
    for skill in PROFILE["primary_skills"]:
        if skill.lower() in job_text:
            score += 5  # Extra bonus

    # Negative signals (too senior)
    senior_keywords = ["senior", "sr.", "lead", "principal", "staff", "manager",
                       "director", "vp", "architect", "10+", "8+", "7+", "6+", "5+"]
    for kw in senior_keywords:
        if kw in job_text:
            score -= 20

    # Positive signals
    junior_keywords = ["fresher", "0-1", "0-2", "1-3", "1-2", "junior", "jr.", "entry level", "associate"]
    for kw in junior_keywords:
        if kw in job_text:
            score += 10

    # Location match bonus
    target_locs = ["bangalore", "bengaluru", "gurugram", "gurgaon", "pune", "hyderabad", "remote"]
    loc_lower = job.location.lower()
    for loc in target_locs:
        if loc in loc_lower:
            score += 5
            break

    return min(max(score, 0), 100)


def _claude_cli_available() -> bool:
    """Check if claude CLI is available."""
    return shutil.which("claude") is not None


def _call_claude_cli(prompt: str) -> str:
    """Call Claude using the CLI (uses Max plan, no API key needed)."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        f.write(prompt)
        tmp_path = f.name

    try:
        result = subprocess.run(
            ["claude", "-p", "--output-format", "text"],
            input=prompt,
            capture_output=True,
            text=True,
            timeout=180,
        )
        if result.returncode != 0:
            stderr = result.stderr.strip()
            stdout = result.stdout.strip()
            raise RuntimeError(f"Claude CLI failed (exit {result.returncode})\nstderr: {stderr}\nstdout: {stdout}")
        return result.stdout.strip()
    finally:
        os.unlink(tmp_path)


async def match_jobs_with_claude(jobs: list[Job]) -> list[Job]:
    """Use Claude CLI to score top jobs, then combine with keyword-matched jobs."""
    # Score ALL jobs with keywords first
    for job in jobs:
        job.match_score = keyword_score(job)

    if not _claude_cli_available():
        print("  Claude CLI not found. Using keyword matching only.")
        return match_jobs_keyword(jobs)

    # Sort by keyword score
    candidates = sorted(jobs, key=lambda j: j.match_score, reverse=True)

    # Send top 50 to Claude for AI scoring (in batches to avoid prompt length issues)
    top_for_claude = [j for j in candidates if j.match_score > 15][:50]

    claude_scored = set()  # Track which jobs Claude scored

    if top_for_claude:
        # Split into batches of 25 to keep prompts manageable
        batches = [top_for_claude[i:i+25] for i in range(0, len(top_for_claude), 25)]

        for batch_num, batch in enumerate(batches):
            jobs_text = ""
            for i, job in enumerate(batch):
                jobs_text += f"\n---JOB {i+1}---\n"
                jobs_text += f"Title: {job.title}\n"
                jobs_text += f"Company: {job.company}\n"
                jobs_text += f"Location: {job.location}\n"
                jobs_text += f"Skills: {', '.join(job.skills[:10])}\n"
                jobs_text += f"Salary: {job.salary}\n"
                jobs_text += f"Experience: {job.experience}\n"
                jobs_text += f"Description: {job.description[:300]}\n"

            prompt = f"""You are a job matching assistant. Score each job's relevance to this candidate.

CANDIDATE:
- Software Developer with 1 year exp at Coding Ninjas
- Primary: Ruby on Rails, Angular, PostgreSQL, Redis, AWS, JavaScript, TypeScript, Docker, Git, REST APIs
- Secondary: React, Node.js, Python, Java, SQL, MongoDB
- Looking for: SDE-1 / Software Developer / Full Stack Developer
- Salary: 16-18 LPA
- Locations: Bangalore, Gurugram, Pune, Hyderabad

Be GENEROUS with scoring. If the title is relevant (Software Developer/Engineer/SDE) and location matches, score at least 50.
Only reject clearly unrelated jobs (data science, devops, management, 5+ years required).

JOBS:
{jobs_text}

Return a JSON array with ALL jobs scored:
- "index": job number (1-based)
- "score": relevance score 0-100
- "reason": one-line reason

Include ALL jobs. Return ONLY valid JSON array."""

            try:
                print(f"  Calling Claude (batch {batch_num + 1}/{len(batches)})...")
                result_text = _call_claude_cli(prompt)

                # Extract JSON
                if "```" in result_text:
                    result_text = result_text.split("```")[1]
                    if result_text.startswith("json"):
                        result_text = result_text[4:]
                    result_text = result_text.strip()

                scores = json.loads(result_text)

                for item in scores:
                    idx = item["index"] - 1
                    if 0 <= idx < len(batch):
                        batch[idx].match_score = item["score"]
                        batch[idx].match_reason = item.get("reason", "")
                        batch[idx].status = "matched"
                        claude_scored.add(id(batch[idx]))

            except Exception as e:
                print(f"  Claude batch {batch_num + 1} error: {e}")

    # Combine: Claude-scored jobs + keyword-matched jobs that weren't sent to Claude
    all_matched = []

    # Add Claude-scored jobs (score >= 40)
    for job in jobs:
        if id(job) in claude_scored and job.match_score >= 40:
            job.status = "matched"
            all_matched.append(job)

    # Add keyword-matched jobs that Claude didn't score (score >= 20)
    for job in jobs:
        if id(job) not in claude_scored and job.match_score >= 20:
            job.status = "matched"
            job.match_reason = "Keyword match"
            all_matched.append(job)

    # Deduplicate and sort
    seen_urls = set()
    unique = []
    for job in sorted(all_matched, key=lambda j: j.match_score, reverse=True):
        if job.url not in seen_urls:
            seen_urls.add(job.url)
            unique.append(job)

    return unique


def match_jobs_keyword(jobs: list[Job]) -> list[Job]:
    """Fallback: keyword-only matching."""
    for job in jobs:
        job.match_score = keyword_score(job)
        if job.match_score >= 20:
            job.status = "matched"
            job.match_reason = "Keyword match"

    matched = [j for j in jobs if j.status == "matched"]
    matched.sort(key=lambda j: j.match_score, reverse=True)
    return matched
