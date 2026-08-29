"""榜单画板模式：作品级元数据抽取与聚合（v1.6.0 任务 2 回归）。

背景：热榜原先只有文字表格。改画板要封面/评分，但本机未配 TMDB_API_KEY，
所以数据必须来自站点搜索接口自带的字段（Mukaku 的 image/doub_score 等），
且站点没提供时要优雅降级成占位，而不是裂图或显示 0 分。
"""

from app.providers.indexer.generic_api import DEFAULT_MEDIA_MAP, GenericApiIndexer
from app.services.trending import _Bucket


def _indexer(**options):
    return GenericApiIndexer({"url": "https://example.com", "options": options})


class TestMediaMetaExtract:
    """从搜索列表项里抽作品级元数据。"""

    def test_抽取典型影视站字段(self):
        meta = _indexer()._media_meta({
            "image": "https://img.example.com/a.png",
            "doub_score": "7.3",
            "doub_score_peo_num": 354675,
            "years": "2024",
            "class": "剧情,古装",
            "production_area": "中国大陆",
            "episodes": 36,
            "abstract": "简介文本",
            "performer": "张若昀,李沁",
            "director": "孙皓",
            "ejs": "全集",
        })
        assert meta["poster"] == "https://img.example.com/a.png"
        assert meta["rating"] == 7.3
        assert meta["rating_people"] == 354675
        assert meta["year"] == "2024"
        assert meta["genres"] == ["剧情", "古装"]
        assert meta["actors"] == ["张若昀", "李沁"]
        assert meta["total_episodes"] == 36
        assert meta["status_text"] == "全集"

    def test_零分与空评分不显示(self):
        """站点用 0/空/文字表示"暂无评分"，不能显示成 0.0 分。"""
        for value in ("0", "", "暂无", None, "N/A"):
            meta = _indexer()._media_meta({"doub_score": value})
            assert "rating" not in meta, value

    def test_相对路径封面补全为绝对地址(self):
        meta = _indexer()._media_meta({"image": "/upload/a.jpg"})
        assert meta["poster"].startswith("https://example.com/")

    def test_非http封面被丢弃(self):
        """data: 或垃圾值塞进 img src 只会得到裂图。"""
        for value in ("data:image/png;base64,AAA", "javascript:alert(1)", "无"):
            assert "poster" not in _indexer()._media_meta({"image": value})

    def test_站点无元数据时返回空(self):
        """盘搜这类只给链接的源，不该凭空造出字段。"""
        assert _indexer()._media_meta({"title": "某资源", "link": "magnet:?xt=1"}) == {}

    def test_映射可被站点配置覆盖(self):
        meta = _indexer(media_map={"poster": "cover_url"})._media_meta(
            {"cover_url": "https://x.com/b.jpg"}
        )
        assert meta["poster"] == "https://x.com/b.jpg"

    def test_多种分隔符都能切成列表(self):
        for text in ("剧情,古装", "剧情/古装", "剧情、古装", "剧情|古装"):
            assert _indexer()._media_meta({"class": text})["genres"] == ["剧情", "古装"]

    def test_默认映射含画板所需字段(self):
        for key in ("poster", "rating", "year", "genres", "total_episodes"):
            assert key in DEFAULT_MEDIA_MAP


class TestBucketMedia:
    """榜单聚合时合并各站点的元数据。"""

    def test_逐字段补齐(self):
        """A 站只有封面、B 站只有评分，合并后两者都要有。"""
        bucket = _Bucket()
        bucket.absorb_media({"poster": "https://x/a.png"})
        bucket.absorb_media({"rating": 8.1})
        assert bucket.media["poster"] == "https://x/a.png"
        assert bucket.media["rating"] == 8.1

    def test_先到先得不被覆盖(self):
        bucket = _Bucket()
        bucket.absorb_media({"rating": 8.1})
        bucket.absorb_media({"rating": 5.0})
        assert bucket.media["rating"] == 8.1

    def test_空值不覆盖已有值(self):
        bucket = _Bucket()
        bucket.absorb_media({"poster": "https://x/a.png"})
        bucket.absorb_media({"poster": "", "rating": None, "genres": []})
        assert bucket.media["poster"] == "https://x/a.png"
        assert "rating" not in bucket.media

    def test_absorb_合并元数据(self):
        """未知类型桶折叠进已知桶时，元数据不能丢。"""
        known, unknown = _Bucket(), _Bucket()
        unknown.absorb_media({"poster": "https://x/a.png", "rating": 7.0})
        known.absorb(unknown)
        assert known.media["poster"] == "https://x/a.png"

    def test_to_dict_暴露画板字段(self):
        bucket = _Bucket()
        bucket.title = "某剧"
        bucket.absorb_media({"poster": "https://x/a.png", "rating": 7.3, "year": "2024"})
        data = bucket.to_dict()
        assert data["poster"] == "https://x/a.png"
        assert data["rating"] == 7.3
        assert data["year"] == "2024"

    def test_无元数据时画板字段为空而非报错(self):
        """PanSou 等无元数据源必须能安全降级（前端画占位）。"""
        data = _Bucket().to_dict()
        assert data["poster"] is None
        assert data["rating"] is None
        assert data["genres"] == []
        assert data["actors"] == []
