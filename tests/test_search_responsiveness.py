"""流式搜索应立即开始，等待并发名额不占用站点自己的请求预算。"""

import asyncio

from app.core.config import settings
from app.providers.base import Resource, SearchProvider
from app.services import search


class Provider(SearchProvider):
    name = "responsive"
    kind = "indexer"

    async def search(self, keyword, **kwargs):
        return [
            Resource(
                title=f"{keyword} 1080p",
                link="https://example.test/file.torrent",
                site=self.site_name,
            )
        ]


def test_stream_start_does_not_wait_for_title_aliases(monkeypatch):
    entered = []

    async def aliases(*args):
        entered.append(True)
        await asyncio.sleep(10)

    monkeypatch.setattr(search, "_expand_aliases", aliases)

    async def run():
        stream = search.search_stream(
            "Example", providers=[Provider({"name": "fast"})], save_history=False
        )
        event = await asyncio.wait_for(anext(stream), 0.1)
        assert event["type"] == "start"
        assert entered == []
        await stream.aclose()

    asyncio.run(run())


def test_alias_preparation_has_a_deadline(monkeypatch):
    async def aliases(*args):
        await asyncio.sleep(10)

    monkeypatch.setattr(search, "_expand_aliases", aliases)

    async def run():
        keywords, rule, _ = await asyncio.wait_for(
            search._prepare_search(
                "Example",
                None,
                None,
                None,
                None,
                None,
                None,
                alias_timeout=0.01,
            ),
            0.2,
        )
        assert "Example" in keywords
        assert rule.title_keywords

    asyncio.run(run())


def test_queued_provider_gets_its_own_timeout_budget(monkeypatch):
    monkeypatch.setattr(settings, "SEARCH_TIMEOUT", 1)

    async def run():
        semaphore = asyncio.Semaphore(0)
        task = asyncio.create_task(
            search._search_one(
                Provider({"name": "queued-budget-case"}),
                ["Example"],
                media_type=None,
                season=None,
                episode=None,
                semaphore=semaphore,
            )
        )
        await asyncio.sleep(1.02)
        semaphore.release()
        resources, outcome = await task
        assert resources and outcome.status == "ok"

    asyncio.run(run())
