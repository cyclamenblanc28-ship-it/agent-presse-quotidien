from dataclasses import dataclass


@dataclass(frozen=True)
class FeedSource:
    name: str
    url: str


FEEDS: list[FeedSource] = [
    FeedSource(name="Médiapart", url="https://www.mediapart.fr/articles/feed"),
    FeedSource(name="Frontières", url="https://www.frontieresmedia.fr/feed"),
    FeedSource(name="Blast", url="https://api.blast-info.fr/rss_articles.xml"),
    FeedSource(name="Le Média", url="https://video.lemediatv.fr/feeds/videos.xml?sort=-publishedAt"),
    FeedSource(name="Orient XXI", url="https://orientxxi.info/?page=backend&lang=fr"),
    FeedSource(name="Le Monde", url="https://www.lemonde.fr/rss/une.xml"),
    FeedSource(name="Libération", url="https://www.liberation.fr/rss/latest/"),
    FeedSource(name="Le Figaro", url="https://www.lefigaro.fr/rss/figaro_actualites.xml"),
    FeedSource(name="Les Échos", url="https://services.lesechos.fr/rss/les-echos-economie-france.xml"),
    FeedSource(name="France Info", url="https://www.francetvinfo.fr/titres.rss"),
]

SOURCE_NAMES: list[str] = [source.name for source in FEEDS]
