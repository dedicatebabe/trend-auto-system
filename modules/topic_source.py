# ==========================================
# Version: 1.0.0
# Date: 2026-09-13
# Summary: トピック選定と TrendItem 定義
# ==========================================
"""一般向けトレンドトピックの読み込みと選定。"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TrendItem:
    """記事生成用トピック。"""

    topic_id: str
    category: str
    category_label: str
    title: str
    search_keyword: str
    summary_hint: str
    description: str
    mercari_url: str = ""
    surugaya_url: str = ""
    image_url: str = ""


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def load_topics(path: Path | None = None) -> list[TrendItem]:
    """data/topics.json からトピック一覧を読み込む。"""
    topics_path = path or (_project_root() / "data" / "topics.json")
    raw = json.loads(topics_path.read_text(encoding="utf-8"))
    items: list[TrendItem] = []
    for row in raw.get("topics", []):
        if not isinstance(row, dict):
            continue
        topic_id = str(row.get("topic_id", "")).strip()
        title = str(row.get("title", "")).strip()
        if not topic_id or not title:
            continue
        items.append(
            TrendItem(
                topic_id=topic_id,
                category=str(row.get("category", "goods")).strip() or "goods",
                category_label=str(row.get("category_label", "トレンド")).strip()
                or "トレンド",
                title=title,
                search_keyword=str(row.get("search_keyword", title)).strip() or title,
                summary_hint=str(row.get("summary_hint", "")).strip(),
                description=str(row.get("description", "")).strip(),
                image_url=str(row.get("image_url", "")).strip(),
            )
        )
    return items


def pick_topic(
    *,
    skip_topic_ids: set[str],
    preferred_category: str | None = None,
) -> TrendItem:
    """
    未投稿トピックを1件返す。

    preferred_category があれば同カテゴリを優先し、なければ先頭から選ぶ。
    """
    topics = load_topics()
    if not topics:
        raise RuntimeError("topics.json にトピックがありません。")

    candidates = [t for t in topics if t.topic_id not in skip_topic_ids]
    if not candidates:
        logger.warning("未投稿トピックが尽きたため、全件から再選択します。")
        candidates = list(topics)

    if preferred_category:
        preferred = [t for t in candidates if t.category == preferred_category]
        if preferred:
            return preferred[0]
    return candidates[0]


def get_topic_by_id(topic_id: str) -> TrendItem:
    """topic_id でトピックを取得する。"""
    for topic in load_topics():
        if topic.topic_id == topic_id:
            return topic
    raise KeyError(f"トピックが見つかりません: {topic_id}")
