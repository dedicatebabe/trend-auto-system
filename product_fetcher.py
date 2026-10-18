# ==========================================
# Version: 3.0.1
# Date: 2026-10-18
# Summary: 需要選定に加え汎用ぬいぐるみ除外とカテゴリ収集打ち切り改善
# ==========================================
"""
需要根拠のある商品を選定する。

ルール:
- 楽天公式ランキングAPI（RAKUTEN_APPLICATION_ID）を最優先
- 次点でニュース一致＋カテゴリ分散したキーワード検索
- 検索結果を「売れ筋ランキング」として扱わない
- Amazon売れ筋HTMLは規約リスクがあるためランキング根拠に使わない
- メルカリは認証API化のためソースから外す
- 返す URL は商品ページ直リンクのみ（検索URL禁止）
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from html import unescape as html_unescape
from pathlib import Path
from typing import Any
from urllib.parse import quote

import requests

logger = logging.getLogger(__name__)

RANKING_POOL = 30
PUBLISH_PER_SOURCE = 3

USER_AGENT = (
    "TrendPickBot/3.0 (+https://dedicatebabe.github.io/trend-auto-system/; affiliate-research)"
)

# カテゴリ分散用の検索候補（固定優先リストではない）
TREND_SEARCH_QUERIES = (
    "ぬいぐるみ キャラクター",
    "マスコット キーホルダー",
    "サンリオ グッズ",
    "ポケモン ぬいぐるみ",
    "たまごっち",
    "ベイブレードX",
    "ポケモンカード BOX",
    "ワンピースカード BOX",
    "遊戯王 ブースター",
    "ガンプラ HG",
    "ガンプラ おすすめ",
    "一番くじ",
    "フィギュア 再販",
    "ねんどろいど 予約",
)

# 楽天ランキングAPI用ジャンル（公式API時のみ使用）
RAKUTEN_RANKING_GENRES = (
    ("101205", "おもちゃ・ホビー・ゲーム"),
    ("101164", "ホビー"),
    ("112493", "ぬいぐるみ・人形・フィギュア"),
)

# タイトルに1つ以上あれば採用候補
ALLOW_TITLE_MARKERS = (
    "フィギュア",
    "スケール",
    "ねんどろいど",
    "Nendoroid",
    "figma",
    "予約",
    "再販",
    "受注",
    "カードゲーム",
    "ブースター",
    "BOX",
    "ポケモン",
    "ポケカ",
    "ワンピースカード",
    "ワンピカード",
    "遊戯王",
    "デュエル・マスターズ",
    "デュエマ",
    "ベイブレード",
    "一番くじ",
    "Blu-ray",
    "ブルーレイ",
    "ガンプラ",
    "プラモデル",
    "ガンダム",
    "amiibo",
    "トレカ",
    "原作",
    "コミック",
    "漫画",
    "ぬいぐるみ",
    "マスコット",
    "キーホルダー",
    "たまごっち",
    "サンリオ",
    "ハローキティ",
    "クロミ",
    "シナモロール",
    "マイメロ",
    "パペット",
    "スンスン",
    "ちいかわ",
    "ガチャ",
    "カプセルトイ",
)

# ブランドから外す（幼児玩具・汎用品・ランキング端材）
DENY_TITLE_MARKERS = (
    "シルバニア",
    "メルちゃん",
    "リカちゃん",
    "おせわいっぱい",
    "バルーン",
    "パズルフレーム",
    "パネルマックス",
    "ワイヤレスイヤホン",
    "AirPods",
    "ACアダプタ",
    "純正ドック",
    "数字バルーン",
    "王冠付き",
    "トイプードルの赤ちゃん",
    "ゆりかご",
    "おむつ",
    "抱っこひも",
    "印鑑",
    "訂正印",
    "ナースグッズ",
    "サウナハット",
    "サウナマット",
    "サウナセット",
)

# ぬいぐるみ等のソフトグッズ単体では不足。キャラクター根拠が必要
SOFT_GOODS_MARKERS = (
    "ぬいぐるみ",
    "マスコット",
    "キーホルダー",
    "抱き枕",
    "添い寝枕",
)
CHARACTER_BRAND_MARKERS = (
    "ポケモン",
    "ピカチュウ",
    "サンリオ",
    "ハローキティ",
    "キティ",
    "クロミ",
    "シナモロール",
    "マイメロ",
    "ポムポムプリン",
    "ちいかわ",
    "スンスン",
    "パペットスンスン",
    "たまごっち",
    "ベイブレード",
    "マリオ",
    "ヨッシー",
    "スプラトゥーン",
    "Splatoon",
    "ミッフィー",
    "ワンピース",
    "遊戯王",
    "ガンダム",
    "フリーレン",
    "鬼滅",
    "初音ミク",
    "NIKKE",
    "あんスタ",
    "ホロライブ",
    "一番くじ",
)
HARD_PRODUCT_MARKERS = (
    "フィギュア",
    "figma",
    "ねんどろいど",
    "Nendoroid",
    "スケール",
    "カード",
    "トレカ",
    "ブースター",
    "BOX",
    "ガンプラ",
    "プラモデル",
    "ベイブレード",
    "たまごっち",
    "Blu-ray",
    "ブルーレイ",
    "amiibo",
)

ADULT_NG = (
    "アダルト",
    "エロ",
    "成人向け",
    "R18",
    "セクシー",
    "下着",
)

BAD_TITLE_PATTERNS = (
    re.compile(r"^Amazon商品\s*B0", re.I),
    re.compile(r"^Amazon$", re.I),
    re.compile(r"管理番号"),
    re.compile(r"商品番号"),
    re.compile(r"（楽天）\s*\d+"),
    re.compile(r"フィギュア（楽天）"),
    re.compile(r"^https?://", re.I),
)


def normalize_product_id(product_id: str) -> str:
    """スキップ照合用に product_id を正規化する（末尾スラッシュ差を吸収）。"""
    text = (product_id or "").strip()
    if not text:
        return ""
    if "://" in text:
        # rakuten:https://.../path/ → 末尾 / を除去
        prefix, _, rest = text.partition(":")
        if rest.startswith("//") or rest.startswith("http"):
            # "rakuten:https://..."
            url = rest.rstrip("/")
            return f"{prefix}:{url}" if prefix and not prefix.startswith("http") else url
        return text.rstrip("/")
    return text.rstrip("/")


def title_fingerprint(title: str) -> str:
    """同一商品判定用のタイトル指紋。"""
    text = (title or "").lower()
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"[\s　]+", "", text)
    text = re.sub(r"[【】\[\]（）()「」『』・･/／|｜,，.。!！?？:：;；\"'“”‘’]", "", text)
    text = re.sub(r"(送料無料|後払い不可|対象年齢\d+才以上|stマーク認証)", "", text)
    return text[:64]


@dataclass
class ProductItem:
    """紹介対象の商品1件。"""

    product_id: str
    source: str  # amazon / rakuten / mercari
    title: str
    url: str
    price: int | None = None
    image_url: str = ""
    rating: float | None = None
    review_count: int | None = None
    rank: int = 0
    keyword: str = ""
    badge: str = "注目トピック"
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def score(self) -> float:
        """需要スコア（高いほど優先）。未計算時は弱い検索順位のみ。"""
        if "demand_score" in (self.extra or {}):
            try:
                return float(self.extra["demand_score"])
            except (TypeError, ValueError):
                pass
        # 推測で高得点を付けない。検索順位は弱い補助信号のみ
        return max(0.0, 8.0 - float(self.rank or 8)) * 0.4


def demand_category(title: str) -> str:
    """需要カテゴリ（分散制御用）。"""
    t = title or ""
    if any(
        x in t
        for x in (
            "カード",
            "トレカ",
            "ブースター",
            "ポケカ",
            "遊戯王",
            "デュエマ",
            "デュエル・マスターズ",
            "ワンピースカード",
            "ワンピカード",
        )
    ):
        return "tcg"
    if any(x in t for x in ("ガンプラ", "プラモデル", "HG ", "RG ", "MG ", "PG ")):
        return "plamodel"
    if any(
        x in t
        for x in (
            "たまごっち",
            "ベイブレード",
            "カプセルトイ",
            "ガチャ",
            "ガシャポン",
        )
    ):
        return "toys"
    if any(
        x in t
        for x in (
            "ぬいぐるみ",
            "マスコット",
            "サンリオ",
            "ハローキティ",
            "クロミ",
            "シナモロール",
            "マイメロ",
            "パペット",
            "スンスン",
            "キーホルダー",
        )
    ):
        return "character_goods"
    if any(
        x in t
        for x in (
            "フィギュア",
            "figma",
            "ねんどろいど",
            "Nendoroid",
            "スケール",
        )
    ):
        return "figure"
    return "other"


def series_fingerprint(title: str) -> str:
    """同一シリーズ連続採用を抑えるための粗い指紋。"""
    t = title or ""
    series_markers = (
        "ポケモン",
        "たまごっち",
        "サンリオ",
        "クロミ",
        "ちいかわ",
        "スンスン",
        "ガンダム",
        "ガンプラ",
        "ワンピース",
        "遊戯王",
        "ベイブレード",
        "フリーレン",
        "NIKKE",
        "初音ミク",
        "鬼滅",
        "ジョジョ",
        "Splatoon",
        "スプラトゥーン",
    )
    for marker in series_markers:
        if marker.lower() in t.lower():
            return marker.lower()
    # 先頭の作品名っぽい語を短く切る
    cleaned = re.sub(r"[【】\[\]（）()『』「」]", " ", t)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned[:18].lower()


def _parse_iso_dt(raw: object) -> datetime | None:
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def load_recent_selection_context(
    *,
    days: int = 7,
    posted_path: str | Path = "posted.json",
) -> dict[str, Any]:
    """直近投稿からカテゴリ／シリーズ集中度を読み取る。"""
    path = Path(posted_path)
    category_counts: dict[str, int] = {}
    series_counts: dict[str, int] = {}
    total = 0
    if not path.exists():
        return {
            "category_counts": category_counts,
            "series_counts": series_counts,
            "total": 0,
        }
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("posted.json 読み込み失敗（分散制御をスキップ）: %s", exc)
        return {
            "category_counts": category_counts,
            "series_counts": series_counts,
            "total": 0,
        }
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    for row in data.get("posts", []) if isinstance(data, dict) else []:
        if not isinstance(row, dict):
            continue
        posted_at = _parse_iso_dt(row.get("posted_at"))
        if posted_at is None or posted_at < cutoff:
            continue
        title = str(row.get("title", ""))
        cat = demand_category(title)
        series = series_fingerprint(title)
        category_counts[cat] = category_counts.get(cat, 0) + 1
        if series:
            series_counts[series] = series_counts.get(series, 0) + 1
        total += 1
    return {
        "category_counts": category_counts,
        "series_counts": series_counts,
        "total": total,
    }


def _news_titles_for_match(*, news_limit: int = 12) -> list[str]:
    try:
        from rss_fetcher import list_relevant_news

        return [n.title for n in list_relevant_news(limit=news_limit)]
    except Exception as exc:  # noqa: BLE001
        logger.warning("ニュース題名取得失敗: %s", exc)
        return []


def _title_matches_news(title: str, news_titles: list[str]) -> tuple[bool, str]:
    """公式ニュース題名との粗い一致。推測一致はしない。"""
    product = (title or "").lower()
    if len(product) < 8:
        return False, ""
    for news in news_titles:
        n = (news or "").strip()
        if len(n) < 6:
            continue
        # 作品名・括弧内固有名
        for m in re.finditer(r"[『「]([^』」]{2,30})[』」]", n):
            name = m.group(1).strip().lower()
            if len(name) >= 2 and name in product:
                return True, n[:80]
        # ニュース側の主要語が商品名に含まれる場合のみ
        tokens = [
            tok
            for tok in re.split(r"[\s　/／|｜・･]+", n)
            if len(tok) >= 4
            and tok
            not in {
                "について",
                "のお知らせ",
                "を発売",
                "予約開始",
                "受注開始",
            }
        ]
        hit = [tok for tok in tokens[:6] if tok.lower() in product]
        if len(hit) >= 2:
            return True, n[:80]
    return False, ""


def compute_demand_score(
    product: ProductItem,
    *,
    recent_ctx: dict[str, Any] | None = None,
    news_titles: list[str] | None = None,
) -> tuple[float, str, dict[str, Any]]:
    """
    需要スコアと採用理由を返す。

    存在しないデータには高得点を付けない。
    """
    extra = dict(product.extra or {})
    demand_source = str(extra.get("demand_source") or "keyword_search")
    cat = demand_category(product.title)
    series = series_fingerprint(product.title)
    score = 0.0
    reasons: list[str] = []

    ranking_rank = extra.get("ranking_rank")
    if demand_source == "rakuten_ranking_api" and isinstance(ranking_rank, int):
        score += 30.0
        score += max(0.0, 25.0 - float(ranking_rank))
        reasons.append(f"楽天公式ランキング{ranking_rank}位")
        fetched_at = str(extra.get("ranking_fetched_at") or "")
        if fetched_at:
            reasons.append(f"取得:{fetched_at[:10]}")
    elif demand_source == "keyword_search":
        # 検索順位は弱い補助のみ（ランキングと混同しない）
        score += max(0.0, 6.0 - float(product.rank or 6)) * 0.5
        reasons.append(f"検索候補(順位{product.rank or '-'})")

    news_hit, news_title = _title_matches_news(product.title, news_titles or [])
    if news_hit:
        score += 18.0
        reasons.append(f"ニュース一致:{news_title[:40]}")
        extra["news_matched"] = True
        extra["news_title"] = news_title
    else:
        extra["news_matched"] = False

    # カテゴリ需要は「根拠があるとき」だけ軽い加点
    if demand_source == "rakuten_ranking_api":
        if cat in {"character_goods", "toys", "tcg", "plamodel"}:
            score += 4.0
            reasons.append(f"ランキング内カテゴリ:{cat}")
        elif cat == "figure":
            score += 2.0
            reasons.append("ランキング内フィギュア")

    # 価格は妥当帯のみ軽い加点（割安判定はしない）
    if isinstance(product.price, int) and 800 <= product.price <= 25000:
        score += 2.0
        reasons.append(f"価格帯OK:{product.price}")

    # 高額転売っぽい価格は減点（データがある場合のみ）
    if isinstance(product.price, int) and product.price >= 80000:
        score -= 12.0
        reasons.append(f"高額のため減点:{product.price}")

    ctx = recent_ctx or {}
    total = int(ctx.get("total") or 0)
    cat_counts = ctx.get("category_counts") or {}
    series_counts = ctx.get("series_counts") or {}
    if total > 0:
        figure_n = int(cat_counts.get("figure", 0))
        figure_ratio = figure_n / float(total)
        if cat == "figure" and figure_ratio >= 0.30:
            penalty = 12.0 + 20.0 * max(0.0, figure_ratio - 0.30)
            score -= penalty
            reasons.append(
                f"直近フィギュア偏重抑制({figure_ratio:.0%}→-{penalty:.1f})"
            )
        elif cat != "figure" and figure_ratio >= 0.50:
            score += 6.0
            reasons.append("非フィギュア候補を優先加点")

        same_cat = int(cat_counts.get(cat, 0))
        if same_cat >= 3:
            score -= 4.0 * (same_cat - 2)
            reasons.append(f"同一カテゴリ連続抑制:{cat}x{same_cat}")

        same_series = int(series_counts.get(series, 0)) if series else 0
        if same_series >= 1:
            score -= 8.0 * same_series
            reasons.append(f"同一シリーズ抑制:{series}x{same_series}")

    reason = " / ".join(reasons) if reasons else "根拠弱"
    extra["demand_score"] = round(score, 3)
    extra["demand_category"] = cat
    extra["series_fingerprint"] = series
    extra["select_reason"] = reason
    return score, reason, extra


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update(
        {
            "User-Agent": USER_AGENT,
            "Accept": "application/json,text/html,application/xhtml+xml,*/*",
            "Accept-Language": "ja,en;q=0.8",
        }
    )
    return s


def _is_good_product_image(url: str) -> bool:
    """ロゴ・スピナー等のゴミ画像を弾く。"""
    u = (url or "").strip().lower()
    if not u.startswith(("http://", "https://")):
        return False
    bad = (
        "share-icons",
        "amazon.png",
        "spinner",
        "grey-pixel",
        "transparent-pixel",
        "loading",
        "placeholder",
    )
    if any(b in u for b in bad):
        return False
    return True


def _is_ng_title(title: str) -> bool:
    return any(ng in (title or "") for ng in ADULT_NG)


def is_on_brand_product(title: str) -> bool:
    """たいちアカウント／サイトの顔に合う商品か。"""
    text = (title or "").strip()
    if len(text) < 8:
        return False
    if any(ng in text for ng in DENY_TITLE_MARKERS):
        return False
    if _is_ng_title(text):
        return False
    if not any(ok in text for ok in ALLOW_TITLE_MARKERS):
        return False
    # 汎用ぬいぐるみ・マスコットのみは除外（キャラクター根拠が必要）
    soft_only = any(m in text for m in SOFT_GOODS_MARKERS)
    hard = any(m in text for m in HARD_PRODUCT_MARKERS)
    branded = any(m in text for m in CHARACTER_BRAND_MARKERS)
    if soft_only and not hard and not branded:
        return False
    return True


def is_usable_product_title(title: str) -> bool:
    """紹介に耐える商品名か（可読性＋ブランド適合）。"""
    text = (title or "").strip()
    if len(text) < 8:
        return False
    if _is_ng_title(text):
        return False
    if any(pat.search(text) for pat in BAD_TITLE_PATTERNS):
        return False
    if re.fullmatch(r".{0,20}\d{6,}", text) and "フィギュア（楽天）" in text:
        return False
    if not is_on_brand_product(text):
        return False
    return True


def _badge_for_title(title: str) -> str:
    """新カテゴリ体系でバッジを返す。"""
    t = title or ""
    if any(x in t for x in ("ポケモン", "ポケカ", "ピカチュウ", "ストームエメラルダ")):
        return "ポケモン"
    if any(x in t for x in ("めじるし", "ガチャ", "カプセルトイ", "ガシャポン", "ガシャ")):
        return "めじるしチャーム・ガチャ"
    if any(
        x in t
        for x in (
            "サンリオ",
            "ハローキティ",
            "マイメロ",
            "シナモロール",
            "クロミ",
            "ポムポムプリン",
        )
    ):
        return "サンリオ・キャラグッズ"
    if any(
        x in t
        for x in (
            "ベイブレード",
            "トレカ",
            "カードゲーム",
            "ONE PIECE",
            "ワンピカード",
            "遊戯王",
            "デュエマ",
        )
    ):
        return "ベイブレード・トレカ"
    if any(x in t for x in ("フィギュア", "NIKKE", "フリーレン", "ホビー", "一番くじ")):
        return "フィギュア・ホビー"
    return "フィギュア・ホビー"


def amazon_product_url(asin: str, *, tag: str | None = None) -> str:
    associate = (tag if tag is not None else os.getenv("AMAZON_ASSOCIATE_TAG", "")).strip()
    if not associate:
        raise RuntimeError("AMAZON_ASSOCIATE_TAG が未設定です。")
    asin = asin.strip().upper()
    return f"https://www.amazon.co.jp/dp/{asin}?tag={quote(associate, safe='')}"


def rakuten_product_affiliate_url(item_url: str, *, af_id: str | None = None) -> str:
    rid = (af_id if af_id is not None else os.getenv("RAKUTEN_AF_ID", "")).strip()
    if not rid:
        raise RuntimeError("RAKUTEN_AF_ID が未設定です。")
    pc = quote(item_url.strip(), safe="")
    return f"https://hb.afl.rakuten.co.jp/ichiba/{rid}/?pc={pc}&link_type=text&id=0"


def mercari_item_url(item_id: str, *, afid: str | None = None) -> str:
    af = (afid if afid is not None else os.getenv("MERCARI_AFID", "")).strip()
    if not af:
        raise RuntimeError("MERCARI_AFID が未設定です。")
    iid = item_id.strip()
    if iid.startswith("http"):
        base = iid
        sep = "&" if "?" in base else "?"
        return f"{base}{sep}afid={quote(af, safe='')}"
    return f"https://jp.mercari.com/item/{quote(iid, safe='')}?afid={quote(af, safe='')}"


def fetch_rakuten_ranking(
    *,
    genre_id: str = "101205",
    pool: int = RANKING_POOL,
    session: requests.Session | None = None,
) -> list[ProductItem]:
    """
    楽天市場の公式ランキングAPIから商品を取得する。

    RAKUTEN_APPLICATION_ID が必須。未設定や失敗時は空配列を返す。
    検索結果HTMLをランキングとして扱わない（以前のフォールバックを廃止）。
    """
    sess = session or _session()
    app_id = os.getenv("RAKUTEN_APPLICATION_ID", "").strip()
    items: list[ProductItem] = []
    if not app_id:
        logger.warning(
            "楽天公式ランキングAPIを使うには RAKUTEN_APPLICATION_ID が必要です。"
            "未設定のためランキング取得をスキップします。"
        )
        return items

    url = "https://app.rakuten.co.jp/services/api/IchibaItem/Ranking/20170628"
    params = {
        "applicationId": app_id,
        "affiliateId": os.getenv("RAKUTEN_AF_ID", "").strip() or None,
        "genreId": genre_id,
        "formatVersion": 2,
    }
    fetched_at = datetime.now(timezone.utc).isoformat()
    try:
        res = sess.get(url, params={k: v for k, v in params.items() if v}, timeout=25)
        res.raise_for_status()
        data = res.json()
    except Exception as exc:  # noqa: BLE001
        logger.warning("楽天APIランキング失敗 genre=%s: %s", genre_id, exc)
        return items

    for idx, row in enumerate(data.get("Items", [])[:pool], start=1):
        it = row.get("Item", row) if isinstance(row, dict) else {}
        title = str(it.get("itemName") or it.get("title") or "").strip()
        item_url = str(it.get("itemUrl") or it.get("url") or "").strip()
        if not title or not item_url or _is_ng_title(title):
            continue
        try:
            price = int(it.get("itemPrice") or it.get("price") or 0) or None
        except (TypeError, ValueError):
            price = None
        code = str(it.get("itemCode") or item_url)
        try:
            aff = rakuten_product_affiliate_url(item_url)
        except RuntimeError:
            break
        image_url = ""
        mid_images = it.get("mediumImageUrls")
        if isinstance(mid_images, list) and mid_images:
            first = mid_images[0]
            if isinstance(first, dict):
                image_url = str(first.get("imageUrl") or "")
            else:
                image_url = str(first)
        if not image_url:
            image_url = str(it.get("imageUrl") or "")
        rank_no = int(it.get("rank") or idx)
        items.append(
            ProductItem(
                product_id=f"rakuten:{code}",
                source="rakuten",
                title=title,
                url=aff,
                price=price,
                image_url=image_url,
                rank=rank_no,
                keyword=title[:40],
                badge=_badge_for_title(title),
                extra={
                    "raw_url": item_url,
                    "demand_source": "rakuten_ranking_api",
                    "ranking_rank": rank_no,
                    "ranking_genre_id": genre_id,
                    "ranking_fetched_at": fetched_at,
                },
            )
        )
    logger.info(
        "楽天公式ランキング genre=%s got=%s",
        genre_id,
        len(items),
    )
    return items


def fetch_amazon_bestsellers(
    *,
    category_path: str = "toys",
    pool: int = RANKING_POOL,
    session: requests.Session | None = None,
) -> list[ProductItem]:
    """
    Amazon売れ筋の取得スタブ。

    非公式HTMLスクレイピングは規約リスクがあるため、需要根拠としては使わない。
    PA-API等の正規手段が使えるようになるまで空配列を返す。
    """
    _ = category_path, pool, session
    logger.info(
        "Amazon売れ筋は正規API未設定のため未使用（HTMLスクレイピングは行いません）。"
        " category=%s",
        category_path,
    )
    return []


def fetch_amazon_keyword_search(
    keyword: str,
    *,
    pool: int = RANKING_POOL,
    session: requests.Session | None = None,
) -> list[ProductItem]:
    """Amazon キーワード検索から ASIN 直リンクを抽出する。"""
    sess = session or _session()
    kw = (keyword or "").strip()
    if not kw:
        return []
    url = f"https://www.amazon.co.jp/s?k={quote(kw)}"
    try:
        res = sess.get(url, timeout=25)
        res.raise_for_status()
        html_text = res.text
    except requests.RequestException as exc:
        logger.warning("Amazon検索取得失敗 (%s): %s", kw, exc)
        return []

    asins = re.findall(r'data-asin="([A-Z0-9]{10})"', html_text)
    items: list[ProductItem] = []
    seen: set[str] = set()
    for asin in asins:
        if asin in seen:
            continue
        seen.add(asin)
        title_match = re.search(
            rf'data-asin="{asin}"[\s\S]{{0,900}}?alt="([^"]{{8,160}})"',
            html_text,
        )
        title = title_match.group(1).strip() if title_match else f"Amazon商品 {asin}"
        if not is_on_brand_product(title) and not title.startswith("Amazon商品"):
            # タイトル未確定のものは後で enrich。暫定NGはスキップ
            if not title.startswith("Amazon商品"):
                continue
        img_match = re.search(
            rf'data-asin="{asin}"[\s\S]{{0,1200}}?src="(https://[^"]+(?:images-amazon|media-amazon)[^"]+)"',
            html_text,
            re.I,
        )
        image_url = img_match.group(1).strip() if img_match else ""
        if not _is_good_product_image(image_url):
            image_url = ""
        try:
            product_url = amazon_product_url(asin)
        except RuntimeError:
            break
        items.append(
            ProductItem(
                product_id=f"amazon:{asin}",
                source="amazon",
                title=title,
                url=product_url,
                image_url=image_url,
                rank=len(items) + 1,
                keyword=kw[:40],
                badge=_badge_for_title(title),
                extra={"asin": asin, "search_kw": kw},
            )
        )
        if len(items) >= pool:
            break
    return items


def fetch_rakuten_keyword_search(
    keyword: str,
    *,
    pool: int = RANKING_POOL,
    session: requests.Session | None = None,
) -> list[ProductItem]:
    """楽天キーワード検索から商品直URLを抽出する。"""
    sess = session or _session()
    kw = (keyword or "").strip()
    if not kw:
        return []
    page = f"https://search.rakuten.co.jp/search/mall/{quote(kw)}/"
    try:
        html_text = sess.get(page, timeout=20).text
    except requests.RequestException as exc:
        logger.warning("楽天検索HTML取得失敗 (%s): %s", kw, exc)
        return []

    pairs: list[tuple[str, str, str]] = []
    for block in re.findall(
        r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>',
        html_text,
        flags=re.I | re.S,
    ):
        if '"ItemList"' not in block and '"Product"' not in block:
            continue
        for m in re.finditer(
            r'"@type"\s*:\s*"Product"[\s\S]*?'
            r'"name"\s*:\s*"((?:\\.|[^"\\]){8,200})"([\s\S]{0,800}?)'
            r'"url"\s*:\s*"(https://item\.rakuten\.co\.jp/[^"]+)"',
            block,
        ):
            title = html_unescape(m.group(1)).replace('\\"', '"').replace("\\/", "/")
            title = re.sub(r"\s+", " ", title).strip()
            mid = m.group(2)
            img_m = re.search(r'"image"\s*:\s*\[\s*"([^"]+)"', mid) or re.search(
                r'"image"\s*:\s*"([^"]+)"', mid
            )
            image_url = ""
            if img_m:
                image_url = html_unescape(img_m.group(1)).replace("\\/", "/")
            item_url = m.group(3).split("?")[0].rstrip("/") + "/"
            pairs.append((item_url, title, image_url))

    if not pairs:
        for m in re.finditer(
            r'href="(https://item\.rakuten\.co\.jp/[^"]+)"[^>]*>'
            r"([^<]{10,160})<",
            html_text,
            flags=re.I,
        ):
            item_url = m.group(1).split("?")[0].rstrip("/") + "/"
            title = re.sub(r"\s+", " ", html_unescape(m.group(2)).strip())
            pairs.append((item_url, title, ""))

    items: list[ProductItem] = []
    seen: set[str] = set()
    for item_url, title, image_url in pairs:
        if item_url in seen:
            continue
        if not is_on_brand_product(title):
            continue
        seen.add(item_url)
        try:
            aff = rakuten_product_affiliate_url(item_url)
        except RuntimeError:
            return items
        items.append(
            ProductItem(
                product_id=f"rakuten:{item_url}",
                source="rakuten",
                title=title[:120],
                url=aff,
                image_url=image_url,
                rank=len(items) + 1,
                keyword=kw[:40],
                badge=_badge_for_title(title),
                extra={"raw_url": item_url, "search_kw": kw},
            )
        )
        if len(items) >= pool:
            break
    return items


def build_trend_search_queries(*, news_limit: int = 8) -> list[str]:
    """定番トレンド語を先に、続いてニュース由来の検索語を返す。"""
    queries: list[str] = []
    seen: set[str] = set()

    def add(q: str) -> None:
        text = re.sub(r"\s+", " ", (q or "").strip())
        if len(text) < 3:
            return
        key = text.lower()
        if key in seen:
            return
        seen.add(key)
        queries.append(text)

    # 枯渇しにくい定番を先に
    for q in TREND_SEARCH_QUERIES:
        add(q)

    try:
        from rss_fetcher import list_relevant_news

        for news in list_relevant_news(limit=news_limit):
            for seed in _search_seeds_from_news_title(news.title):
                add(seed)
    except Exception as exc:  # noqa: BLE001
        logger.warning("ニュース由来クエリ取得失敗: %s", exc)

    return queries


def _search_seeds_from_news_title(title: str) -> list[str]:
    """ニュースタイトルから商品検索語を作る（フィギュア偏重を避ける）。"""
    text = (title or "").strip()
    if not text:
        return []
    seeds: list[str] = []
    for m in re.finditer(r"[『「]([^』」]{2,40})[』」]", text):
        name = m.group(1).strip()
        if any(x in text for x in ("Blu-ray", "ブルーレイ", "BD", "DVD")):
            seeds.append(f"{name} Blu-ray")
        elif any(x in text for x in ("カード", "ブースター", "BOX", "パック")):
            seeds.append(f"{name} カード")
            seeds.append(f"{name} BOX")
        elif any(x in text for x in ("ぬいぐるみ", "マスコット", "グッズ", "一番くじ")):
            seeds.append(f"{name} ぬいぐるみ")
            seeds.append(f"{name} グッズ")
        elif any(x in text for x in ("ガンプラ", "プラモデル", "プラモ")):
            seeds.append(f"{name} ガンプラ")
            seeds.append(f"{name} プラモデル")
        elif any(x in text for x in ("再販", "予約", "受注", "フィギュア")):
            seeds.append(f"{name} 再販")
            seeds.append(f"{name} フィギュア")
        else:
            seeds.append(f"{name} グッズ")
            seeds.append(f"{name} ぬいぐるみ")
    if not seeds:
        short = re.sub(r"[【】\[\]（）()]", " ", text)
        short = re.sub(r"\s+", " ", short).strip()[:24]
        if len(short) >= 4:
            seeds.append(f"{short} グッズ")
    return seeds[:4]


def fetch_mercari_items(
    *,
    keyword: str = "フィギュア",
    pool: int = RANKING_POOL,
    session: requests.Session | None = None,
) -> list[ProductItem]:
    """
    メルカリから商品ページ直URLを取得。

    現状の公開APIは認証必須のため、取得できない場合は空配列を返す。
    将来 item URL が取れた場合は mercari_item_url() でアフィ化できる。
    """
    sess = session or _session()
    endpoints = (
        "https://api.mercari.jp/v2/entities:search",
        "https://api.mercari.jp/search_index/search",
    )
    data: dict[str, Any] | None = None
    for endpoint in endpoints:
        try:
            if endpoint.endswith("entities:search"):
                res = sess.post(
                    endpoint,
                    json={
                        "userId": "",
                        "pageSize": pool,
                        "searchSessionId": "trend-pick",
                        "indexRouting": "INDEX_ROUTING_UNSPECIFIED",
                        "searchCondition": {
                            "keyword": keyword,
                            "sort": "SORT_SCORE",
                            "order": "ORDER_DESC",
                            "status": ["STATUS_ON_SALE"],
                        },
                        "defaultDatasets": ["DATASET_TYPE_MERCARI"],
                    },
                    timeout=15,
                )
            else:
                res = sess.get(
                    endpoint,
                    params={
                        "keyword": keyword,
                        "limit": pool,
                        "sort": "score",
                        "order": "desc",
                        "status": "on_sale",
                    },
                    timeout=15,
                )
            if res.status_code >= 400:
                logger.info("メルカリAPI %s -> %s", endpoint, res.status_code)
                continue
            data = res.json()
            break
        except Exception as exc:  # noqa: BLE001
            logger.info("メルカリAPI失敗 %s: %s", endpoint, exc)
            continue

    items: list[ProductItem] = []
    if not data:
        logger.warning(
            "メルカリは認証付きAPI化のため自動取得不可。"
            "商品直URLが取れる別経路が必要です（検索URLは使いません）。"
        )
        return items

    candidates = (
        data.get("items")
        or data.get("data")
        or data.get("products")
        or []
    )
    if isinstance(candidates, dict):
        candidates = candidates.get("items") or []

    for row in candidates:
        if not isinstance(row, dict):
            continue
        item_id = str(row.get("id") or row.get("item_id") or row.get("productId") or "").strip()
        title = str(row.get("name") or row.get("title") or "").strip()
        if not item_id or not title or _is_ng_title(title):
            continue
        price_raw = row.get("price")
        try:
            price = int(price_raw) if price_raw is not None else None
        except (TypeError, ValueError):
            price = None
        try:
            product_url = mercari_item_url(item_id)
        except RuntimeError:
            break
        thumb = ""
        thumbs = row.get("thumbnails") or row.get("photos") or []
        if isinstance(thumbs, list) and thumbs:
            first = thumbs[0]
            thumb = first if isinstance(first, str) else str(first.get("url") or "")
        items.append(
            ProductItem(
                product_id=f"mercari:{item_id}",
                source="mercari",
                title=title,
                url=product_url,
                price=price,
                image_url=thumb,
                rank=len(items) + 1,
                keyword=keyword,
                badge=_badge_for_title(title),
                extra={"item_id": item_id},
            )
        )
        if len(items) >= pool:
            break
    return items


def enrich_amazon_titles(
    products: list[ProductItem],
    *,
    session: requests.Session | None = None,
    limit: int = 10,
) -> list[ProductItem]:
    """選定後のAmazon商品だけ productTitle / og:title / 画像を補強。"""
    sess = session or _session()
    out: list[ProductItem] = []
    for i, product in enumerate(products):
        if product.source != "amazon" or i >= limit:
            out.append(product)
            continue
        asin = str((product.extra or {}).get("asin") or "")
        if not asin:
            out.append(product)
            continue
        title = product.title
        image_url = product.image_url
        try:
            html_text = sess.get(
                f"https://www.amazon.co.jp/dp/{asin}",
                timeout=12,
            ).text
            for pat in (
                r'id="productTitle"[^>]*>\s*([^<]+?)\s*<',
                r'<meta\s+property="og:title"\s+content="([^"]+)"',
                r"<title>([^<]+)</title>",
            ):
                m = re.search(pat, html_text, re.I)
                if not m:
                    continue
                cand = html_unescape(m.group(1)).strip()
                cand = re.sub(r"^Amazon\.co\.jp[:：]\s*", "", cand)
                cand = cand.replace("\n", " ")
                cand = re.sub(r"\s+", " ", cand).strip()
                if len(cand) >= 8 and cand.lower() not in {"amazon", "amazon.co.jp"}:
                    title = cand[:120]
                    break
            if not image_url:
                for pat in (
                    r'"hiRes"\s*:\s*"(https://m\.media-amazon\.com/images/I/[^"]+)"',
                    r'"large"\s*:\s*"(https://m\.media-amazon\.com/images/I/[^"]+)"',
                    r'data-old-hires="(https://[^"]+)"',
                    r'<meta\s+property="og:image"\s+content="([^"]+)"',
                    r'id="landingImage"[^>]+src="(https://[^"]+)"',
                ):
                    for m in re.finditer(pat, html_text, re.I):
                        cand = html_unescape(m.group(1)).strip()
                        if _is_good_product_image(cand):
                            image_url = cand
                            break
                    if image_url:
                        break
        except Exception as exc:  # noqa: BLE001
            logger.info("Amazonタイトル補強失敗 %s: %s", asin, exc)
        if not _is_good_product_image(image_url):
            image_url = ""
        out.append(
            ProductItem(
                product_id=product.product_id,
                source=product.source,
                title=title,
                url=product.url,
                price=product.price,
                image_url=image_url,
                rating=product.rating,
                review_count=product.review_count,
                rank=product.rank,
                keyword=title[:40],
                badge=_badge_for_title(title),
                extra=product.extra,
            )
        )
    return out


def enrich_product_titles(
    products: list[ProductItem],
    *,
    session: requests.Session | None = None,
) -> list[ProductItem]:
    """Amazon / 楽天のタイトルを商品ページから補強。"""
    sess = session or _session()
    enriched = enrich_amazon_titles(products, session=sess)
    out: list[ProductItem] = []
    for product in enriched:
        if product.source != "rakuten":
            out.append(product)
            continue
        # 検索HTMLで実商品名が取れている場合は商品ページを叩かない
        if is_usable_product_title(product.title):
            out.append(product)
            continue
        raw = str((product.extra or {}).get("raw_url") or "").strip()
        if not raw:
            out.append(product)
            continue
        title = product.title
        try:
            html_text = sess.get(raw, timeout=20).text
            for pat in (
                r'<meta\s+property="og:title"\s+content="([^"]+)"',
                r"<title>([^<]+)</title>",
            ):
                m = re.search(pat, html_text, re.I)
                if not m:
                    continue
                cand = html_unescape(m.group(1)).strip()
                cand = re.sub(r"\s*[|\|].*$", "", cand).strip()
                cand = re.sub(r"\s+", " ", cand)
                if len(cand) >= 8:
                    title = cand[:120]
                    break
        except Exception as exc:  # noqa: BLE001
            logger.info("楽天タイトル補強失敗: %s", exc)
        out.append(
            ProductItem(
                product_id=product.product_id,
                source=product.source,
                title=title,
                url=product.url,
                price=product.price,
                image_url=product.image_url,
                rating=product.rating,
                review_count=product.review_count,
                rank=product.rank,
                keyword=title[:40],
                badge=_badge_for_title(title),
                extra=product.extra,
            )
        )
    return out


def select_products_for_publish(
    products: list[ProductItem],
    *,
    limit: int = PUBLISH_PER_SOURCE,
    skip_ids: set[str] | None = None,
    require_usable_title: bool = True,
) -> list[ProductItem]:
    """需要スコア順に採用。検索URLは除外済み前提。"""
    skipped = {normalize_product_id(x) for x in (skip_ids or set()) if normalize_product_id(x)}
    filtered = []
    for p in products:
        pid = normalize_product_id(p.product_id)
        if pid in skipped or not p.url or "/search" in p.url:
            continue
        if _is_ng_title(p.title):
            continue
        if require_usable_title and p.title.strip() and not is_usable_product_title(p.title):
            continue
        if require_usable_title and not p.title.strip() and p.source != "rakuten":
            continue
        filtered.append(p)
    filtered.sort(key=lambda p: p.score, reverse=True)
    return filtered[: max(0, int(limit))]


def _annotate_search_product(product: ProductItem) -> ProductItem:
    """検索由来であることを明示する。"""
    extra = dict(product.extra or {})
    if not extra.get("demand_source"):
        extra["demand_source"] = "keyword_search"
    return ProductItem(
        product_id=product.product_id,
        source=product.source,
        title=product.title,
        url=product.url,
        price=product.price,
        image_url=product.image_url,
        rating=product.rating,
        review_count=product.review_count,
        rank=product.rank,
        keyword=product.keyword,
        badge=product.badge,
        extra=extra,
    )


def apply_demand_scores(
    products: list[ProductItem],
    *,
    recent_ctx: dict[str, Any] | None = None,
    news_titles: list[str] | None = None,
) -> list[ProductItem]:
    """全候補に需要スコアと採用理由を付与する。"""
    ctx = recent_ctx if recent_ctx is not None else load_recent_selection_context()
    news = news_titles if news_titles is not None else _news_titles_for_match()
    scored: list[ProductItem] = []
    for product in products:
        score, reason, extra = compute_demand_score(
            product,
            recent_ctx=ctx,
            news_titles=news,
        )
        updated = ProductItem(
            product_id=product.product_id,
            source=product.source,
            title=product.title,
            url=product.url,
            price=product.price,
            image_url=product.image_url,
            rating=product.rating,
            review_count=product.review_count,
            rank=product.rank,
            keyword=product.keyword,
            badge=product.badge,
            extra=extra,
        )
        logger.info(
            "候補スコア=%.2f cat=%s src=%s reason=%s title=%s",
            score,
            extra.get("demand_category"),
            extra.get("demand_source"),
            reason[:120],
            product.title[:50],
        )
        scored.append(updated)
    scored.sort(key=lambda p: p.score, reverse=True)
    return scored


def fetch_all_marketplace_products(
    *,
    pool: int = RANKING_POOL,
    per_source: int = PUBLISH_PER_SOURCE,
    skip_ids: set[str] | None = None,
    mercari_keyword: str = "フィギュア",
    max_queries: int = 10,
) -> list[ProductItem]:
    """
    需要根拠のある商品を収集して返す。

    優先:
    1. 楽天公式ランキングAPI（複数ジャンル）
    2. ニュース＋分散キーワード検索（Amazon/楽天）
    メルカリはソースから外す。Amazon売れ筋HTMLは使わない。
    """
    _ = mercari_keyword
    sess = _session()
    skipped = {normalize_product_id(x) for x in (skip_ids or set()) if normalize_product_id(x)}
    recent_ctx = load_recent_selection_context(days=7)
    news_titles = _news_titles_for_match(news_limit=12)
    logger.info(
        "選定コンテキスト: recent_posts=%s cats=%s news=%s",
        recent_ctx.get("total"),
        recent_ctx.get("category_counts"),
        len(news_titles),
    )

    collected: list[ProductItem] = []
    seen_ids: set[str] = set()

    def _add(product: ProductItem) -> bool:
        pid = normalize_product_id(product.product_id)
        if not pid or pid in skipped or pid in seen_ids:
            return False
        if product.source == "amazon" and str(product.title).startswith("Amazon商品"):
            pass
        elif product.title.strip() and not is_on_brand_product(product.title):
            return False
        seen_ids.add(pid)
        collected.append(product)
        return True

    # 1) 楽天公式ランキング
    ranking_pool = max(8, min(20, int(pool)))
    ranking_count = 0
    for genre_id, label in RAKUTEN_RANKING_GENRES:
        try:
            rows = fetch_rakuten_ranking(
                genre_id=genre_id,
                pool=ranking_pool,
                session=sess,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("楽天ランキング取得例外 %s: %s", label, exc)
            continue
        added = 0
        for p in rows:
            if _add(p):
                added += 1
                ranking_count += 1
        logger.info("ranking/%s: kept=%s", label, added)

    # Amazon売れ筋は規約上使わない（明示呼び出しでログのみ）
    fetch_amazon_bestsellers(category_path="toys", pool=1, session=sess)

    # 2) 分散キーワード検索
    queries = build_trend_search_queries(news_limit=8)[: max(1, int(max_queries))]
    logger.info("検索クエリ %s 件: %s", len(queries), queries[:6])
    per_query = max(3, min(6, int(pool) // 3))
    for q_index, qw in enumerate(queries):
        for fetcher, name in (
            (fetch_rakuten_keyword_search, "rakuten"),
            (fetch_amazon_keyword_search, "amazon"),
        ):
            try:
                rows = fetcher(qw, pool=per_query, session=sess)
            except Exception as exc:  # noqa: BLE001
                logger.warning("%s 検索失敗 (%s): %s", name, qw, exc)
                continue
            added = 0
            for p in rows:
                annotated = _annotate_search_product(p)
                if _add(annotated):
                    added += 1
            logger.info("%s/%s: got=%s kept=%s", name, qw[:20], len(rows), added)
        coverage = {
            demand_category(p.title)
            for p in collected
            if demand_category(p.title)
            in {"character_goods", "toys", "tcg", "plamodel", "figure"}
        }
        # 主要クエリを一通り回してから、カテゴリが揃った場合のみ打ち切る
        if q_index < min(7, len(queries) - 1):
            continue
        if ranking_count >= 12 and len(collected) >= max(20, per_source * 8) and len(coverage) >= 3:
            break
        if ranking_count == 0 and len(collected) >= max(36, per_source * 12) and len(coverage) >= 3:
            break

    if not collected:
        logger.warning("候補0件。分散クエリの再試行のみ。")
        for qw in TREND_SEARCH_QUERIES[:6]:
            for fetcher in (fetch_rakuten_keyword_search, fetch_amazon_keyword_search):
                for p in fetcher(qw, pool=per_query, session=sess):
                    _add(_annotate_search_product(p))

    # enrich 前に多めに残し、需要スコア後に絞る
    preselected = select_products_for_publish(
        collected,
        limit=max(per_source * 8, 36),
        skip_ids=skipped,
        require_usable_title=False,
    )
    enriched = enrich_product_titles(preselected, session=sess)
    usable = [
        p
        for p in enriched
        if is_usable_product_title(p.title) and is_on_brand_product(p.title)
    ]
    scored = apply_demand_scores(
        usable,
        recent_ctx=recent_ctx,
        news_titles=news_titles,
    )

    out: list[ProductItem] = []
    source_counts: dict[str, int] = {}
    category_counts: dict[str, int] = {}
    for p in scored:
        # 質の低い候補で無理に埋めない
        if p.score < 1.0 and (p.extra or {}).get("demand_source") == "keyword_search":
            logger.info("低スコア除外: %.2f %s", p.score, p.title[:50])
            continue
        src_n = source_counts.get(p.source, 0)
        if src_n >= per_source:
            continue
        cat = str((p.extra or {}).get("demand_category") or demand_category(p.title))
        # 1実行で同カテゴリを重ねすぎない（limit>1のとき）
        if category_counts.get(cat, 0) >= max(1, per_source):
            if any(
                category_counts.get(c, 0) == 0
                for c in ("character_goods", "toys", "tcg", "plamodel", "figure")
                if c != cat
            ):
                # 他カテゴリ未採用ならスキップして多様性を優先
                continue
        source_counts[p.source] = src_n + 1
        category_counts[cat] = category_counts.get(cat, 0) + 1
        out.append(p)
        logger.info(
            "採用: score=%.2f cat=%s src=%s basis=%s title=%s",
            p.score,
            cat,
            (p.extra or {}).get("demand_source"),
            (p.extra or {}).get("select_reason", "")[:100],
            p.title[:50],
        )
        if len(out) >= max(per_source * 2, per_source):
            # per_source は販路別上限。全体は per_source*2 まで（通常 limit でさらに絞る）
            if sum(source_counts.values()) >= per_source * 2:
                break

    # フォールバック: 厳しすぎて0件ならスコア上位を返す
    if not out and scored:
        logger.warning("分散条件で0件のためスコア上位フォールバック")
        for p in scored[: max(1, per_source)]:
            out.append(p)

    ranking_ratio = 0.0
    if scored:
        ranking_ratio = sum(
            1
            for p in scored
            if (p.extra or {}).get("demand_source") == "rakuten_ranking_api"
        ) / float(len(scored))

    logger.info(
        "usable=%s / out=%s / sources=%s / cats=%s / ranking_candidates=%.0f%%",
        len(usable),
        len(out),
        source_counts,
        category_counts,
        ranking_ratio * 100,
    )
    return out
