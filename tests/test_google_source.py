"""Google source: the search is limited to resume-like files, and it says so when it is not set up."""

from src.handlers.http_clients.sourcing.sources import google


def test_the_search_asks_for_resume_files_and_leaves_out_job_sites():
    text = google.search_text("java developer chennai")
    assert text.startswith("java developer chennai")
    assert "filetype:pdf" in text and "filetype:docx" in text
    for site in ("linkedin.com", "naukri.com", "indeed.com"):
        assert f"-site:{site}" in text


async def test_it_explains_when_the_keys_are_missing(monkeypatch):
    monkeypatch.setattr(google.settings, "google_cse_api_key", None)
    monkeypatch.setattr(google.settings, "google_cse_cx", None)
    result = await google.collect("java developer", 10)
    assert not result.files
    assert "GOOGLE_CSE_API_KEY" in result.errors[0]
