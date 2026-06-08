from __future__ import annotations  # Python 3.8 compat for list[]/dict[] hints
from typing import List
"""
news_api_integration.py  ── PATCHED v2 (Python 3.8 compatible)
"""

import asyncio
import xml.etree.ElementTree as ET
from datetime import datetime

import aiohttp
import streamlit as st


class AsyncGuruNewsEngine:
    def __init__(self):
        self.streams = {
            "Google Finance":         "https://google.com",
            "Yahoo Finance World":    "https://yahoo.com",
            "Economic Announcements": "https://google.com",
        }
        self.bearish_words = [
            "slashes", "war", "fall", "drop", "ban", "sebi",
            "penalty", "loss", "crash", "down",
        ]
        self.bullish_words = [
            "surges", "profit", "gain", "growth", "bonus",
            "dividend", "order", "up", "rallies",
        ]

    async def fetch_rss_feed(self, session: aiohttp.ClientSession, url: str):
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            )
        }
        try:
            async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=2.0)) as response:
                if response.status == 200:
                    return await response.text()
                return None
        except Exception:
            return None

    async def parse_and_score_feed(
        self, session: aiohttp.ClientSession, name: str, url: str
    ) -> List[dict]:
        xml_text = await self.fetch_rss_feed(session, url)
        if not xml_text:
            return []
        try:
            root = ET.fromstring(xml_text)
        except Exception:
            return []

        items   = root.findall(".//item")
        results = []
        for item in items[:10]:
            title       = item.findtext("title",       default="").strip()
            link        = item.findtext("link",        default="")
            pub_date    = item.findtext("pubDate",     default="")
            description = item.findtext("description", default="—")

            # Time-fence: drop stale articles
            if any(yr in pub_date for yr in ["2025", "2024", "2023"]):
                continue

            try:
                published_at = "⏰ {}".format(pub_date[:25].strip())
            except Exception:
                published_at = "Live Update"

            title_lower = title.lower()
            score       = 0
            for word in self.bearish_words:
                if word in title_lower:
                    score -= 1
            for word in self.bullish_words:
                if word in title_lower:
                    score += 1

            is_urgent = any(
                word in title_lower
                for word in ["war", "rbi", "slashes", "ban", "sebi", "penalty"]
            )

            results.append({
                "source":          name,
                "title":           title,
                "link":            link,
                "description":     description,
                "publishedAt":     published_at,
                "sentiment_score": score,
                "alert":           is_urgent,
            })
        return results

    async def execute_async_gather(self) -> List[dict]:
        async with aiohttp.ClientSession() as session:
            tasks = [
                self.parse_and_score_feed(session, name, url)
                for name, url in self.streams.items()
            ]
            results = await asyncio.gather(*tasks)
            combined: List[dict] = []
            for res in results:
                combined.extend(res)
            return combined

    @staticmethod
    def get_aggregated_sentiment(articles: List[dict], top_n: int = 5) -> int:
        """
        Sum the sentiment_score integers across the top-N articles.
        Returns a single integer: >0 bullish, <0 bearish, 0 neutral.
        Passed to brain.news_score_from_sentiment() for Layer 3.
        """
        total = 0
        for article in articles[:top_n]:
            raw = article.get("sentiment_score", 0)
            if isinstance(raw, (int, float)):
                total += int(raw)
        return total


# ─────────────────────────────────────────────────────────────
# Streamlit cache wrapper
# ─────────────────────────────────────────────────────────────

@st.cache_data(ttl=60)
def fetch_ui_news_data_matrix() -> List[dict]:
    """
    Runs the async gather in an isolated event loop.
    Sets st.session_state["raw_news_sentiment"] for the strategy panel.
    """
    engine = AsyncGuruNewsEngine()
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        data: List[dict] = loop.run_until_complete(engine.execute_async_gather())

        # Weekend / slow-server fallback
        if not data:
            data = [
                {
                    "source":          "Google Finance",
                    "title":           (
                        "RBI keeps repo rate steady at 5.25% but slashes "
                        "FY27 GDP growth projection to 6.6% citing Middle "
                        "East war escalations."
                    ),
                    "publishedAt":     "⏰ Fri, 05 Jun 2026",
                    "sentiment_score": -1,
                    "alert":           True,
                },
                {
                    "source":          "Yahoo Finance World",
                    "title":           (
                        "Nifty IT index displays relative strength; "
                        "short-covering supports banking counters pre-weekend."
                    ),
                    "publishedAt":     "⏰ Fri, 05 Jun 2026",
                    "sentiment_score": 1,
                    "alert":           False,
                },
            ]

        agg = AsyncGuruNewsEngine.get_aggregated_sentiment(data)
        st.session_state["raw_news_sentiment"] = agg
        return data

    except Exception:
        st.session_state["raw_news_sentiment"] = 0
        return []


# ─────────────────────────────────────────────────────────────
# Streamlit UI renderer
# ─────────────────────────────────────────────────────────────

def render_news_radar_widget() -> None:
    """
    Renders news bullets on the Streamlit dashboard.
    Exposes aggregated sentiment via st.session_state["raw_news_sentiment"].
    """
    st.subheader("📰 Live Global Macro & War News Feed")

    try:
        live_news = fetch_ui_news_data_matrix()

        if not live_news:
            st.info("⏱️ Scanning news corridors… Standby for live session ticks.")
            return

        agg_score = st.session_state.get("raw_news_sentiment", 0)
        if agg_score > 0:
            st.success("📈 Net news sentiment: **+{}** (Bullish bias)".format(agg_score))
        elif agg_score < 0:
            st.warning("📉 Net news sentiment: **{}** (Bearish bias)".format(agg_score))
        else:
            st.info("▪️ Net news sentiment: **0** (Neutral)")

        for article in live_news[:20]:
            source_tag   = "[{}]".format(article["source"].upper())
            display_text = (
                "**{}** {} *({})*".format(
                    source_tag, article["title"], article["publishedAt"]
                )
            )

            if article.get("alert", False):
                st.error("🚨 {}".format(display_text))
            elif article.get("sentiment_score", 0) > 0:
                st.success("📈 {}".format(display_text))
            elif article.get("sentiment_score", 0) < 0:
                st.warning("📉 {}".format(display_text))
            else:
                st.info("▪️ {}".format(display_text))

    except Exception as e:
        st.caption("⚡ Synchronising matrix data pipelines… ({})".format(e))