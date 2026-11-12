from __future__ import annotations

import asyncio
import calendar
import html
import json
import os
import re
from datetime import datetime, timedelta, timezone

import feedparser
import httpx
from pydantic import BaseModel, Field

import mistralai.workflows as workflows
from mistralai.workflows.client import get_mistral_client

from worker.sources import FEEDS, SOURCE_NAMES, FeedSource

DEFAULT_MODEL = "mistral-medium-latest"

USER_AGENT = "agent-presse-quotidien/0.1 (+https://github.com/cyclamenblanc28-ship-it/agent-presse-quotidien)"

RELIABILITY_CONFIRMED = "CONFIRME"
RELIABILITY_SINGLE_SOURCE = "SOURCE_UNIQUE"

RELIABILITY_LABELS = {
    RELIABILITY_CONFIRMED: "✅ Confirmé",
    RELIABILITY_SINGLE_SOURCE: "⚠️ Source unique",
}

VERIFICATION_PROMPT = """Tu es un fact-checker rigoureux. Voici les dépêches du jour, récupérées via les flux RSS de plusieurs médias français : {sources}.

Consignes impératives :
1. Identifie les {max_stories} informations les plus importantes de la journée.
2. Croise systématiquement les sources : pour chaque information, liste TOUS les médias dont la dépêche la rapporte, avec l'URL exacte de l'article.
3. N'utilise QUE les dépêches fournies ci-dessous. N'invente aucun fait, aucune URL, aucun média. Si une information n'apparaît que dans un seul média, dis-le explicitement dans "verifications".
4. Dans "verifications", note pour chaque information ce qui la renforce ou l'affaiblit : nombre de médias indépendants qui la rapportent, désaccords entre médias, éléments non corroborés.
5. Rédige en français, de façon factuelle et neutre.

Réponds STRICTEMENT en JSON, sans texte autour, sous la forme exacte :
{{"stories": [{{"titre": "...", "resume": "...", "sources": [{{"media": "Nom du média", "url": "https://..."}}], "verifications": ["..."]}}]}}

Dépêches du jour :
{articles}"""


class BriefInput(BaseModel):
    dry_run: bool = False
    max_articles_per_source: int = 12
    max_stories: int = 8
    model: str = DEFAULT_MODEL


class Article(BaseModel):
    source: str
    title: str
    url: str
    summary: str = ""
    published: datetime | None = None


class FeedStatus(BaseModel):
    source: str
    ok: bool
    articles: int = 0
    error: str = ""


class FetchResult(BaseModel):
    articles: list[Article]
    statuses: list[FeedStatus]
    fetched_at: datetime


class SourceRef(BaseModel):
    media: str
    url: str


class Story(BaseModel):
    titre: str
    resume: str
    sources: list[SourceRef]
    fiabilite: str = RELIABILITY_SINGLE_SOURCE
    verifications: list[str] = Field(default_factory=list)


class BriefOutput(BaseModel):
    date: str
    stories: list[Story]
    statuses: list[FeedStatus]
    message: str
    posted_to_slack: bool = False


def clean_text(value: str | None) -> str:
    if not value:
        return ""
    text = re.sub(r"<[^>]+>", " ", value)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def parse_feed(source_name: str, content: bytes, limit: int = 50) -> list[Article]:
    parsed = feedparser.parse(content)
    articles: list[Article] = []
    for entry in parsed.entries:
        title = clean_text(getattr(entry, "title", None))
        link = getattr(entry, "link", None)
        if not title or not link:
            continue
        summary = clean_text(getattr(entry, "summary", "") or getattr(entry, "description", ""))
        published = None
        for key in ("published_parsed", "updated_parsed"):
            struct = getattr(entry, key, None)
            if struct:
                published = datetime.fromtimestamp(calendar.timegm(struct), tz=timezone.utc)
                break
        articles.append(
            Article(
                source=source_name,
                title=title[:300],
                url=link,
                summary=summary[:600],
                published=published,
            )
        )
        if len(articles) >= limit:
            break
    return articles


def compute_reliability(story: Story) -> str:
    distinct_media = {ref.media for ref in story.sources if ref.media in SOURCE_NAMES}
    if len(distinct_media) >= 2:
        return RELIABILITY_CONFIRMED
    return RELIABILITY_SINGLE_SOURCE


async def _fetch_feed_text(client: httpx.AsyncClient, source: FeedSource) -> bytes:
    response = await client.get(source.url, headers={"User-Agent": USER_AGENT}, timeout=20)
    response.raise_for_status()
    return response.content


@workflows.activity(start_to_close_timeout=timedelta(seconds=180), retry_policy_max_attempts=2)
async def fetch_feeds(max_articles_per_source: int = 12) -> FetchResult:
    async def fetch_one(source: FeedSource) -> tuple[list[Article], FeedStatus]:
        try:
            async with httpx.AsyncClient(follow_redirects=True) as client:
                content = await _fetch_feed_text(client, source)
            articles = parse_feed(source.name, content)[:max_articles_per_source]
            return articles, FeedStatus(source=source.name, ok=True, articles=len(articles))
        except Exception as exc:
            return [], FeedStatus(source=source.name, ok=False, articles=0, error=str(exc)[:200])

    results = await asyncio.gather(*(fetch_one(source) for source in FEEDS))
    articles = [article for fetched, _ in results for article in fetched]
    statuses = [status for _, status in results]
    if not articles:
        raise workflows.WorkflowError(
            "Aucun article récupéré : toutes les sources ont échoué", non_retryable=True
        )
    return FetchResult(
        articles=articles,
        statuses=statuses,
        fetched_at=datetime.now(timezone.utc),
    )


@workflows.activity(start_to_close_timeout=timedelta(seconds=300), retry_policy_max_attempts=3)
async def verify_stories(
    articles: list[Article],
    statuses: list[FeedStatus],
    max_stories: int = 8,
    model: str = DEFAULT_MODEL,
) -> list[Story]:
    lines = [
        f"- [{article.source}] {article.title} — {article.summary} ({article.url})"
        for article in articles
    ]
    prompt = VERIFICATION_PROMPT.format(
        sources=", ".join(SOURCE_NAMES),
        max_stories=max_stories,
        articles="\n".join(lines),
    )
    client = get_mistral_client()
    response = await client.chat.complete_async(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        response_format={"type": "json_object"},
        temperature=0.1,
    )
    content = response.choices[0].message.content
    if not content:
        raise workflows.WorkflowError("Le modèle n'a renvoyé aucune vérification")
    data = json.loads(content)
    stories = [Story.model_validate(story) for story in data.get("stories", [])][:max_stories]
    for story in stories:
        story.sources = [ref for ref in story.sources if ref.media in SOURCE_NAMES]
        story.fiabilite = compute_reliability(story)
    return stories


def format_brief(date_label: str, stories: list[Story], statuses: list[FeedStatus]) -> str:
    sources_ok = sum(1 for status in statuses if status.ok)
    lines = [f"📰 *Brief vérifié du {date_label}*", ""]
    for index, story in enumerate(stories, start=1):
        label = RELIABILITY_LABELS.get(story.fiabilite, story.fiabilite)
        lines.append(f"*{index}. {story.titre}* — {label}")
        lines.append(story.resume)
        for ref in story.sources:
            lines.append(f"• {ref.media} : <{ref.url}|article>")
        if story.verifications:
            for note in story.verifications:
                lines.append(f"🔎 _{note}_")
        lines.append("")
    failed = [status for status in statuses if not status.ok]
    lines.append(f"_Sources consultées : {sources_ok}/{len(statuses)} disponibles._")
    if failed:
        names = ", ".join(status.source for status in failed)
        lines.append(f"_Sources indisponibles aujourd'hui : {names}._")
    return "\n".join(lines)


@workflows.activity(start_to_close_timeout=timedelta(seconds=60), retry_policy_max_attempts=3)
async def post_slack(message: str) -> bool:
    token = os.environ.get("SLACK_BOT_TOKEN")
    channel = os.environ.get("SLACK_CHANNEL")
    if not token or not channel:
        raise workflows.WorkflowError(
            "SLACK_BOT_TOKEN et SLACK_CHANNEL doivent être définis comme secrets du déploiement",
            non_retryable=True,
        )
    async with httpx.AsyncClient() as client:
        response = await client.post(
            "https://slack.com/api/chat.postMessage",
            headers={"Authorization": f"Bearer {token}"},
            json={"channel": channel, "text": message},
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()
    if not data.get("ok"):
        raise workflows.WorkflowError(
            f"Slack a refusé le message : {data.get('error')}", non_retryable=True
        )
    return True


@workflows.workflow.define(
    name="brief-quotidien",
    workflow_display_name="Brief quotidien vérifié",
    workflow_description=(
        "Collecte chaque jour les dépêches de médias français (de Médiapart à Frontières), "
        "vérifie chaque information en croisant les sources, puis publie un résumé sourcé sur Slack."
    ),
    enforce_determinism=False,
)
class BriefQuotidien:
    @workflows.workflow.entrypoint
    async def run(self, input: BriefInput) -> BriefOutput:
        fetched = await fetch_feeds(input.max_articles_per_source)
        stories = await verify_stories(
            fetched.articles,
            fetched.statuses,
            input.max_stories,
            input.model,
        )
        date_label = fetched.fetched_at.strftime("%d/%m/%Y")
        message = format_brief(date_label, stories, fetched.statuses)
        posted = False
        if not input.dry_run:
            posted = await post_slack(message)
        return BriefOutput(
            date=date_label,
            stories=stories,
            statuses=fetched.statuses,
            message=message,
            posted_to_slack=posted,
        )
