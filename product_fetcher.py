# ==========================================
# Version: 1.2.0
# Date: 2026-09-16
# Summary: 楽天は検索HTMLの実商品名を採用し管理番号記事を防ぐ
# ==========================================
"""
売れ筋ランキング起点の商品取得。

ルール:
- 各ショップで上位 RANKING_POOL 件を取得
- フィルタ後、スコア順に PUBLISH_PER_SOURCE 件まで採用
- 返す URL は商品ページ直リンクのみ（検索URL禁止）
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from html import unescape as html_unescape
from typing import Any
from urllib.parse import quote, urljoin

import requests

logger = logging.getLogger(__name__)

# ランキングから何件見るか / 何件記事化するか
RANKING_POOL = 20
PUBLISH_PER_SOURCE = 5

USER_AGENT = (
    "TrendPickBot/2.0 (+https://dedicatebabe.github.io/trend-auto-system/; affiliate-research)"
)

# ジャンル寄せ（アダルト除外・ホビー寄り）
DEFAULT_KEYWORDS = (
    "Nintendo Switch ソフト",
    "ワイヤレスイヤホン",
    "フィギュア",
    "アニメ Blu-ray",
    "一番くじ",
)

ADULT_NG = (
    "アダルト",
    "エロ",
    "成人向け",
    "R18",
    "セクシー",
    "下着",
)

# 人間が読めない・ゴミタイトル
BAD_TITLE_PATTERNS = (
    re.compile(r"^Amazon商品\s*B0", re.I),
    re.compile(r"^Amazon$", re.I),
    re.compile(r"管理番号"),
    re.compile(r"商品番号"),
    re.compile(r"（楽天）\s*\d+"),
    re.compile(r"フィギュア（楽天）"),
    re.compile(r"^https?://", re.I),
)


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
        """売れそうスコア（高いほど優先）。"""
        rank_score = max(0.0, 21.0 - float(self.rank or 20))
        review_score = min(5.0, (self.review_count or 0) / 40.0)
        rating_score = (self.rating or 0.0) * 1.2
        price_bonus = 1.0 if self.price and 1000 <= self.price <= 30000 else 0.0
        return rank_score + review_score + rating_score + price_bonus


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


def _is_ng_title(title: str) -> bool:
    return any(ng in (title or "") for ng in ADULT_NG)


def is_usable_product_title(title: str) -> bool:
    """紹介に耐える商品名か。"""
    text = (title or "").strip()
    if len(text) < 8:
        return False
    if _is_ng_title(text):
        return False
    if any(pat.search(text) for pat in BAD_TITLE_PATTERNS):
        return False
    # 数字だけのIDっぽい末尾だけ、などは除外
    if re.fullmatch(r".{0,20}\d{6,}", text) and "フィギュア（楽天）" in text:
        return False
    return True


def _badge_for_title(title: str) -> str:
    t = title or ""
    if any(x in t for x in ("Switch", "PlayStation", "PS5", "ゲーム", "ソフト")):
        return "ゲーム"
    if any(x in t for x in ("イヤホン", "ガジェット", "スマホ", "充電", "キーボード")):
        return "ガジェット"
    if any(x in t for x in ("フィギュア", "一番くじ", "くじ", "グッズ", "アクリル")):
        return "カルチャー"
    if any(x in t for x in ("アニメ", "Blu-ray", "ブルーレイ", "漫画", "コミック")):
        return "アニメ"
    return "注目トピック"


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
    楽天市場ランキングから商品ページ直URLを取得。

    RAKUTEN_APPLICATION_ID があれば公式API、なければランキングHTMLから抽出。
    genre_id 既定: おもちゃ・ホビー寄り。
    """
    sess = session or _session()
    app_id = os.getenv("RAKUTEN_APPLICATION_ID", "").strip()
    items: list[ProductItem] = []

    if app_id:
        url = "https://app.rakuten.co.jp/services/api/IchibaItem/Ranking/20170628"
        params = {
            "applicationId": app_id,
            "affiliateId": os.getenv("RAKUTEN_AF_ID", "").strip() or None,
            "genreId": genre_id,
            "formatVersion": 2,
        }
        try:
            res = sess.get(url, params={k: v for k, v in params.items() if v}, timeout=25)
            res.raise_for_status()
            data = res.json()
            for row in data.get("Items", [])[:pool]:
                # formatVersion=2 はフラット、古い形式は Item ネスト
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
                aff = rakuten_product_affiliate_url(item_url)
                items.append(
                    ProductItem(
                        product_id=f"rakuten:{code}",
                        source="rakuten",
                        title=title,
                        url=aff,
                        price=price,
                        image_url=str(it.get("mediumImageUrls", [{}])[0].get("imageUrl", "")
                                      if isinstance(it.get("mediumImageUrls"), list) and it.get("mediumImageUrls")
                                      else it.get("imageUrl") or ""),
                        rank=len(items) + 1,
                        keyword=title[:40],
                        badge=_badge_for_title(title),
                        extra={"raw_url": item_url},
                    )
                )
            if items:
                return items[:pool]
        except Exception as exc:  # noqa: BLE001
            logger.warning("楽天APIランキング失敗、HTMLへフォールバック: %s", exc)

    # HTML フォールバック（検索結果の JSON-LD / アンカー文言から実商品名＋直URL）
    queries = ("フィギュア", "Nintendo Switch", "ワイヤレスイヤホン")
    seen: set[str] = set()
    for q in queries:
        page = f"https://search.rakuten.co.jp/search/mall/{quote(q)}/"
        try:
            html_text = sess.get(page, timeout=20).text
        except requests.RequestException as exc:
            logger.warning("楽天検索HTML取得失敗 (%s): %s", q, exc)
            continue

        pairs: list[tuple[str, str]] = []

        # 1) schema.org ItemList（最も安定）
        for block in re.findall(
            r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>',
            html_text,
            flags=re.I | re.S,
        ):
            if '"ItemList"' not in block and '"Product"' not in block:
                continue
            for m in re.finditer(
                r'"name"\s*:\s*"((?:\\.|[^"\\]){8,200})"[\s\S]{0,400}?'
                r'"url"\s*:\s*"(https://item\.rakuten\.co\.jp/[^"]+)"',
                block,
            ):
                title = html_unescape(m.group(1))
                title = title.replace('\\"', '"').replace("\\/", "/")
                title = re.sub(r"\s+", " ", title).strip()
                item_url = m.group(2).split("?")[0].rstrip("/")
                pairs.append((item_url + "/", title))

        # 2) アンカー文言フォールバック
        if not pairs:
            for m in re.finditer(
                r'href="(https://item\.rakuten\.co\.jp/[^"]+)"[^>]*>'
                r"([^<]{10,160})<",
                html_text,
                flags=re.I,
            ):
                item_url = m.group(1).split("?")[0].rstrip("/") + "/"
                title = html_unescape(m.group(2)).strip()
                title = re.sub(r"\s+", " ", title)
                pairs.append((item_url, title))

        for item_url, title in pairs:
            if item_url in seen:
                continue
            if not is_usable_product_title(title):
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
                    rank=len(items) + 1,
                    keyword=title[:40],
                    badge=_badge_for_title(title),
                    extra={"raw_url": item_url},
                )
            )
            if len(items) >= pool:
                return items
    return items


def fetch_amazon_bestsellers(
    *,
    category_path: str = "toys",
    pool: int = RANKING_POOL,
    session: requests.Session | None = None,
) -> list[ProductItem]:
    """
    Amazon.co.jp 売れ筋から ASIN 直リンクを抽出。

    PA-API が無い場合のHTML抽出。ブロック時は空配列。
    """
    sess = session or _session()
    url = f"https://www.amazon.co.jp/gp/bestsellers/{category_path}/"
    try:
        res = sess.get(url, timeout=25)
        res.raise_for_status()
        html_text = res.text
    except requests.RequestException as exc:
        logger.warning("Amazon売れ筋取得失敗: %s", exc)
        return []

    # data-asin と近傍テキスト
    asins = re.findall(r'data-asin="([A-Z0-9]{10})"', html_text)
    items: list[ProductItem] = []
    seen: set[str] = set()
    for asin in asins:
        if asin in seen:
            continue
        seen.add(asin)
        # タイトル推定（厳密でなくてよい）
        title_match = re.search(
            rf'data-asin="{asin}"[\s\S]{{0,800}}?alt="([^"]{{8,160}})"',
            html_text,
        )
        title = title_match.group(1).strip() if title_match else f"Amazon商品 {asin}"
        if _is_ng_title(title):
            continue
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
                rank=len(items) + 1,
                keyword=title[:40],
                badge=_badge_for_title(title),
                extra={"asin": asin},
            )
        )
        if len(items) >= pool:
            break
    return items


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
    """選定後のAmazon商品だけ productTitle / og:title でタイトルを補強。"""
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
        except Exception as exc:  # noqa: BLE001
            logger.info("Amazonタイトル補強失敗 %s: %s", asin, exc)
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
    """スコア順に採用。検索URLは除外済み前提。"""
    skipped = skip_ids or set()
    filtered = []
    for p in products:
        if p.product_id in skipped or not p.url or "/search" in p.url:
            continue
        if _is_ng_title(p.title):
            continue
        # 楽天HTMLフォールバックは title 空で来るので、選別時は許可し enrich 後に落とす
        if require_usable_title and p.title.strip() and not is_usable_product_title(p.title):
            continue
        if require_usable_title and not p.title.strip() and p.source != "rakuten":
            continue
        filtered.append(p)
    filtered.sort(key=lambda p: p.score, reverse=True)
    return filtered[: max(0, int(limit))]


def fetch_all_marketplace_products(
    *,
    pool: int = RANKING_POOL,
    per_source: int = PUBLISH_PER_SOURCE,
    skip_ids: set[str] | None = None,
    mercari_keyword: str = "フィギュア",
) -> list[ProductItem]:
    """
    3ショップから売れ筋を取り、各 per_source 件ずつ返す。

    合計の目安: per_source * 3（既定 5*3=15）
    """
    sess = _session()
    buckets = [
        ("amazon", fetch_amazon_bestsellers(pool=pool, session=sess)),
        ("rakuten", fetch_rakuten_ranking(pool=pool, session=sess)),
        ("mercari", fetch_mercari_items(keyword=mercari_keyword, pool=pool, session=sess)),
    ]
    selected: list[ProductItem] = []
    for name, rows in buckets:
        # タイトル補強前は空タイトル許可（楽天HTML）
        picked = select_products_for_publish(
            rows, limit=per_source * 2, skip_ids=skip_ids, require_usable_title=False
        )
        logger.info("%s: pool=%s candidate=%s", name, len(rows), len(picked))
        selected.extend(picked)

    enriched = enrich_product_titles(selected, session=sess)
    usable = [p for p in enriched if is_usable_product_title(p.title)]
    # ソース別に上限
    out: list[ProductItem] = []
    counts: dict[str, int] = {}
    for p in sorted(usable, key=lambda x: x.score, reverse=True):
        n = counts.get(p.source, 0)
        if n >= per_source:
            continue
        counts[p.source] = n + 1
        out.append(p)
    logger.info(
        "usable after enrich: %s / published: %s (%s)",
        len(usable),
        len(out),
        counts,
    )
    return out
