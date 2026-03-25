import os
import time
import logging
import hashlib

logger = logging.getLogger("SentimentEngine")

try:
    from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
    _VADER_OK = True
except ImportError:
    _VADER_OK = False
    logger.warning("vaderSentiment não instalado. Sentimento neutro (0.5) será usado.")

try:
    import httpx
    _HTTPX_OK = True
except ImportError:
    _HTTPX_OK = False

NEWS_API_KEY = os.environ.get("NEWS_API_KEY", "")
DEFENSIVE_THRESHOLD = 0.4
CACHE_TTL_SECONDS = 1800


class SentimentEngine:
    def __init__(self):
        self.analyzer = SentimentIntensityAnalyzer() if _VADER_OK else None
        self._cache: dict[str, tuple[float, float]] = {}

    def _cache_key(self, query: str) -> str:
        return hashlib.md5(query.encode()).hexdigest()

    def _is_cache_valid(self, key: str) -> bool:
        if key not in self._cache:
            return False
        _, ts = self._cache[key]
        return (time.time() - ts) < CACHE_TTL_SECONDS

    def _fetch_headlines(self, query: str) -> list[str]:
        headlines = []
        if NEWS_API_KEY and _HTTPX_OK:
            try:
                r = httpx.get(
                    "https://newsapi.org/v2/everything",
                    params={
                        "q": query,
                        "language": "en",
                        "sortBy": "publishedAt",
                        "pageSize": 20,
                        "apiKey": NEWS_API_KEY,
                    },
                    timeout=8,
                )
                if r.status_code == 200:
                    articles = r.json().get("articles", [])
                    headlines = [a.get("title", "") + " " + a.get("description", "") for a in articles]
            except Exception as e:
                logger.warning(f"NewsAPI error: {e}")

        if not headlines and _HTTPX_OK:
            try:
                rss_url = f"https://feeds.finance.yahoo.com/rss/2.0/headline?s={query.replace(' ','%20')}&region=US&lang=en-US"
                r = httpx.get(rss_url, timeout=8)
                import re
                titles = re.findall(r"<title><!\[CDATA\[(.*?)\]\]></title>", r.text)
                headlines = titles[:20]
            except Exception as e:
                logger.warning(f"RSS fallback error: {e}")

        return headlines

    def analyze(self, query: str = "stock market economy") -> float:
        """
        Retorna score de sentimento [0..1].
        < 0.4 → modo defensivo
        >= 0.4 → normal
        """
        if not _VADER_OK:
            return 0.5

        key = self._cache_key(query)
        if self._is_cache_valid(key):
            score, _ = self._cache[key]
            logger.debug(f"Cache sentimento: {score:.3f} (query={query})")
            return score

        headlines = self._fetch_headlines(query)
        if not headlines:
            logger.warning("Sem manchetes disponíveis. Sentimento neutro.")
            return 0.5

        scores = []
        for text in headlines:
            if not text.strip():
                continue
            vs = self.analyzer.polarity_scores(text)
            normalized = (vs["compound"] + 1.0) / 2.0
            scores.append(normalized)

        score = float(sum(scores) / len(scores)) if scores else 0.5
        self._cache[key] = (score, time.time())
        logger.info(f"Sentimento calculado: {score:.4f} ({len(scores)} manchetes)")
        return score

    def is_defensive(self, score: float) -> bool:
        return score < DEFENSIVE_THRESHOLD
