"""Local benchmark: how fast is a deterministic Playwright fill?

Runs against an in-memory 22-control form so the number is not diluted by
network latency. Real applications add page load time, which is the browser's
cost, not the filler's.

    python -m resume_pipeline.apply.bench
"""

from __future__ import annotations

import time

from .answers import AnswerBook
from .fields import scan
from .fill import fill_form

HTML = """
<!doctype html>
<html><body>
<form>
  <label for="first">First name *</label><input id="first" name="first_name" required>
  <label for="last">Last name *</label><input id="last" name="last_name" required>
  <label for="email">Email *</label><input id="email" name="email" type="email" autocomplete="email" required>
  <label for="phone">Phone</label><input id="phone" name="phone" type="tel" autocomplete="tel">
  <label for="linkedin">LinkedIn</label><input id="linkedin" name="linkedin" type="url">
  <label for="github">GitHub</label><input id="github" name="github" type="url">
  <label for="site">Portfolio website</label><input id="site" name="website" type="url">
  <label for="city">City</label><input id="city" name="city" autocomplete="address-level2">
  <label for="country">Country</label>
  <select id="country" name="country">
    <option value="">Select</option><option value="IN">India</option><option value="US">United States</option>
  </select>
  <label for="exp">Years of experience</label><input id="exp" name="years" type="number">
  <label for="salary">Expected salary</label><input id="salary" name="salary">
  <label for="notice">Notice period</label><input id="notice" name="notice_period">
  <label for="company">Current company</label><input id="company" name="company">
  <label for="title">Current title</label><input id="title" name="title">
  <label for="resume">Resume *</label><input id="resume" name="resume" type="file" required>
  <label for="cover">Cover letter</label><input id="cover" name="cover" type="file">
  <fieldset><legend>Are you legally authorized to work here?</legend>
    <label><input type="radio" name="auth" value="yes"> Yes</label>
    <label><input type="radio" name="auth" value="no"> No</label>
  </fieldset>
  <fieldset><legend>Will you now or in the future require sponsorship?</legend>
    <label><input type="radio" name="sponsor" value="yes"> Yes</label>
    <label><input type="radio" name="sponsor" value="no"> No</label>
  </fieldset>
  <label><input type="checkbox" name="consent"> I agree to the privacy policy *</label>
  <label><input type="checkbox" name="news"> Send me job alerts</label>
  <label for="why">Why do you want this role?</label><textarea id="why" name="why"></textarea>
  <button type="submit">Submit application</button>
</form>
</body></html>
"""

ANSWERS = AnswerBook(
    {
        "first_name": "Harsh",
        "last_name": "Patel",
        "email": "harsh@example.com",
        "phone": "+91 90000 00000",
        "linkedin": "https://linkedin.com/in/example",
        "github": "https://github.com/example",
        "portfolio": "https://example.dev",
        "city": "Ahmedabad",
        "country": "India",
        "years_experience": "2",
        "salary_expectation": "1200000",
        "notice_period": "30 days",
        "current_company": "Example Corp",
        "current_title": "Backend Developer",
        "work_authorization": True,
        "requires_sponsorship": False,
        "consent": True,
        "newsletter": False,
    },
    {"why do you want": "I build backend systems."},
)


def main() -> int:
    from playwright.sync_api import sync_playwright
    from pathlib import Path
    import tempfile

    with tempfile.NamedTemporaryFile("wb", suffix=".pdf", delete=False) as tmp:
        tmp.write(b"%PDF-1.4\n% benchmark placeholder\n")
        resume = Path(tmp.name)

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content(HTML)
        page.wait_for_timeout(50)

        t0 = time.perf_counter()
        fields = scan(page)
        t_scan = (time.perf_counter() - t0) * 1000

        t0 = time.perf_counter()
        results = fill_form(page, fields, ANSWERS, resume_path=resume)
        t_fill = (time.perf_counter() - t0) * 1000

        browser.close()

    filled = [r for r in results if r.status in {"filled", "selected", "checked", "uploaded"}]
    print(f"controls found : {len(fields)}")
    print(f"controls filled: {len(filled)}")
    print(f"scan           : {t_scan:.0f} ms (one DOM pass)")
    print(f"fill           : {t_fill:.0f} ms ({t_fill / max(1, len(filled)):.1f} ms per control)")
    print(f"total          : {t_scan + t_fill:.0f} ms")
    for result in results:
        if result.status not in {"filled", "selected", "checked", "uploaded"}:
            print(f"  {result.status}: {result.field.label or result.field.name} ({result.detail})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
