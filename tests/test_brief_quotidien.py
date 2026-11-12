import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

import worker.workflows.brief_quotidien as bq
from worker.sources import FeedSource
from worker.workflows.brief_quotidien import (
    Article,
    BriefInput,
    BriefQuotidien,
    FeedStatus,
    SourceRef,
    Story,
    clean_text,
    compute_reliability,
    format_brief,
    parse_feed,
)

pytest.importorskip("mistralai.workflows.testing")

from mistralai.workflows.testing import create_test_worker

SAMPLE_RSS = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>Test</title>
<item>
  <title><![CDATA[Premier titre]]></title>
  <link>https://exemple.fr/article-1</link>
  <pubDate>Thu, 12 Nov 2026 08:00:00 GMT</pubDate>
  <description><![CDATA[<p>Resume du <b>premier</b> article</p>]]></description>
</item>
<item>
  <title>Deuxi&amp;me titre</title>
  <link>https://exemple.fr/article-2</link>
  <description>Resume deux</description>
</item>
<item>
  <title></title>
  <link>https://exemple.fr/article-3</link>
</item>
</channel></rss>"""


def test_clean_text_strips_html_and_entities():
    assert clean_text("<p>Resume du <b>premier</b> article</p>") == "Resume du premier article"
    assert clean_text("a &amp; b") == "a & b"
    assert clean_text(None) == ""
    assert clean_text("  multi\n\tspaces  ") == "multi spaces"


def test_parse_feed_reads_rss_items():
    articles = parse_feed("Test", SAMPLE_RSS)
    assert len(articles) == 2
    assert articles[0].source == "Test"
    assert articles[0].title == "Premier titre"
    assert articles[0].url == "https://exemple.fr/article-1"
    assert articles[0].published is not None
    assert articles[0].published.year == 2026
    assert articles[0].summary == "Resume du premier article"
    assert articles[1].title == "Deuxi&me titre"


def test_parse_feed_respects_limit():
    articles = parse_feed("Test", SAMPLE_RSS, limit=1)
    assert len(articles) == 1


def make_story(media: str, url: str = "https://exemple.fr/a") -> Story:
    story = Story(
        titre="Titre",
        resume="Resume",
        sources=[SourceRef(media=media, url=url)],
    )
    story.fiabilite = compute_reliability(story)
    return story


def test_compute_reliability_two_independent_sources_confirmed():
    story = make_story("Médiapart")
    story.sources.append(SourceRef(media="Le Monde", url="https://exemple.fr/b"))
    assert compute_reliability(story) == bq.RELIABILITY_CONFIRMED


def test_compute_reliability_same_media_twice_is_single_source():
    story = make_story("Médiapart")
    story.sources.append(SourceRef(media="Médiapart", url="https://exemple.fr/b"))
    assert compute_reliability(story) == bq.RELIABILITY_SINGLE_SOURCE


def test_compute_reliability_unknown_media_is_single_source():
    assert compute_reliability(make_story("Inconnu")) == bq.RELIABILITY_SINGLE_SOURCE


def test_format_brief_lists_sources_and_failures():
    story = make_story("Médiapart")
    story.sources.append(SourceRef(media="Le Monde", url="https://exemple.fr/b"))
    story.fiabilite = compute_reliability(story)
    story.verifications = ["Rapporté par deux médias indépendants."]
    statuses = [
        FeedStatus(source="Médiapart", ok=True, articles=10),
        FeedStatus(source="Frontières", ok=False, error="timeout"),
    ]
    message = format_brief("12/11/2026", [story], statuses)
    assert "Brief vérifié du 12/11/2026" in message
    assert "✅ Confirmé" in message
    assert "<https://exemple.fr/a|article>" in message
    assert "Sources indisponibles aujourd'hui : Frontières" in message
    assert "Sources consultées : 1/2" in message


@pytest.fixture
async def worker(temporal_env, monkeypatch):
    async def fake_fetch_feed_text(client, source):
        return SAMPLE_RSS

    async def fake_fetch_feeds(max_articles_per_source: int = 12) -> bq.FetchResult:
        articles = parse_feed("Test", SAMPLE_RSS)
        return bq.FetchResult(
            articles=articles,
            statuses=[FeedStatus(source="Test", ok=True, articles=len(articles))],
            fetched_at=datetime(2026, 11, 12, 17, 0, tzinfo=timezone.utc),
        )

    async def fake_verify_stories(articles, statuses, max_stories=8, model=bq.DEFAULT_MODEL):
        story = make_story("Médiapart")
        story.sources.append(SourceRef(media="Le Monde", url="https://exemple.fr/b"))
        story.fiabilite = compute_reliability(story)
        return [story]

    async def fake_post_slack(message: str) -> bool:
        return True

    monkeypatch.setattr(bq, "fetch_feeds", fake_fetch_feeds)
    monkeypatch.setattr(bq, "verify_stories", fake_verify_stories)
    monkeypatch.setattr(bq, "post_slack", fake_post_slack)

    import mistralai.workflows as workflows

    async with create_test_worker(
        temporal_env,
        workflows=[BriefQuotidien],
        activities=[],
    ):
        yield workflows


async def test_workflow_runs_end_to_end_in_dry_run(worker):
    import mistralai.workflows as workflows

    result = await workflows.execute_workflow(BriefQuotidien, BriefInput(dry_run=True))
    assert isinstance(result, bq.BriefOutput)
    assert result.posted_to_slack is False
    assert len(result.stories) == 1
    assert result.stories[0].fiabilite == bq.RELIABILITY_CONFIRMED
    assert "Brief vérifié du 12/11/2026" in result.message
