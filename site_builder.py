# ==========================================
# Version: 4.0.0
# Date: 2026-09-20
# Summary: AdSense/要約/FAQ削除、ショップリンクを一列に統一
# ==========================================
"""GitHub Pages 向けメディア型ページ生成。"""

from __future__ import annotations

import hashlib
import html
import json
import logging
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

DOCS = Path("docs")
TEMPLATES = Path("templates")
ENTRIES_FILE = DOCS / ".index_entries.json"
SITE_BASE = "https://dedicatebabe.github.io/trend-auto-system"
TOP_LIST_LIMIT = 9

BADGE_CLASS = {
    "すべて": "badge-all",
    "ポケモン": "badge-pokemon",
    "めじるしチャーム・ガチャ": "badge-gacha",
    "サンリオ・キャラグッズ": "badge-sanrio",
    "ベイブレード・トレカ": "badge-beyblade",
    "フィギュア・ホビー": "badge-figure",
    "NEWS": "badge-news",
    "注目トピック": "badge-item",
    "カルチャー": "badge-figure",
    "アニメ": "badge-figure",
    "ゲーム": "badge-beyblade",
    "ガジェット": "badge-item",
}

SITE_CATEGORIES = (
    "すべて",
    "ポケモン",
    "めじるしチャーム・ガチャ",
    "サンリオ・キャラグッズ",
    "ベイブレード・トレカ",
    "フィギュア・ホビー",
)

CONTENT_CATEGORIES = tuple(c for c in SITE_CATEGORIES if c != "すべて")

LEGACY_BADGE_MAP = {
    "商品": "フィギュア・ホビー",
    "商品ピックアップ": "フィギュア・ホビー",
    "注目アイテム": "フィギュア・ホビー",
    "注目トピック": "フィギュア・ホビー",
    "トレンドグッズ": "サンリオ・キャラグッズ",
    "カルチャー": "フィギュア・ホビー",
    "アニメ": "フィギュア・ホビー",
    "ゲーム": "ベイブレード・トレカ",
    "ガジェット": "フィギュア・ホビー",
    "NEWS": "フィギュア・ホビー",
}

_DISCLAIMER_PATTERNS = (
    re.compile(
        r"この記事は読み物[・･]?ニュース紹介です[。．]?.*"
        r"(買い物|商品)リンクは掲載していません[。．]?"
    ),
    re.compile(r"(買い物|商品)リンクは掲載していません[。．]?"),
    re.compile(r"この記事は読み物[・･]?ニュース紹介です[。．]?"),
    re.compile(r"商品そのものではなく、読み物としての発表です[。．]?"),
)


@dataclass
class ArticleEntry:
    """一覧・個別記事用エントリ。"""

    article_id: str
    filename: str
    title: str
    excerpt: str
    source_link: str
    created_at: str
    has_product_links: bool = False
    keyword: str = ""
    badge: str = "フィギュア・ホビー"
    image_seed: int = 1
    links: dict[str, str] = field(default_factory=dict)
    image_url: str = ""
    status: str = ""


def article_id_from_link(link: str) -> str:
    digest = hashlib.sha1(link.encode("utf-8")).hexdigest()[:10]
    return f"n_{digest}"


def article_filename(article_id: str) -> str:
    safe = re.sub(r"[^\w\-]", "_", article_id)
    return f"article_{safe}.html"


def article_public_url(article_id: str, *, base: str = SITE_BASE) -> str:
    return f"{base.rstrip('/')}/{article_filename(article_id)}"


def infer_category(*, title: str = "", keyword: str = "", badge: str | None = None) -> str:
    """タイトル／キーワードから新カテゴリを推定する。"""
    text = f"{title} {keyword}"
    if any(x in text for x in ("ポケモン", "ポケカ", "ピカチュウ", "ストームエメラルダ")):
        return "ポケモン"
    if any(x in text for x in ("めじるし", "ガチャ", "カプセルトイ", "ガシャポン", "ガシャ", "たまごっち")):
        return "めじるしチャーム・ガチャ"
    if any(
        x in text
        for x in ("サンリオ", "ハローキティ", "マイメロ", "シナモロール", "クロミ", "ポムポムプリン")
    ):
        return "サンリオ・キャラグッズ"
    if any(
        x in text
        for x in (
            "ベイブレード",
            "トレカ",
            "カードゲーム",
            "ONE PIECE",
            "ワンピカード",
            "遊戯王",
            "デュエマ",
            "BuilderCards",
        )
    ):
        return "ベイブレード・トレカ"
    if any(x in text for x in ("フィギュア", "NIKKE", "フリーレン", "ホビー", "一番くじ")):
        return "フィギュア・ホビー"
    if badge and badge.strip() in CONTENT_CATEGORIES:
        return badge.strip()
    if badge and badge.strip() in LEGACY_BADGE_MAP:
        return LEGACY_BADGE_MAP[badge.strip()]
    return "フィギュア・ホビー"


def infer_status(*, title: str = "", body: str = "", rank: int | None = None) -> str:
    """アイキャッチ用ステータスを推定。"""
    text = f"{title}\n{body}"
    if any(x in text for x in ("完売", "欠品", "売り切れ")):
        return "完売注意"
    if any(x in text for x in ("予約", "受注", "予約受付")):
        return "予約受付中"
    if rank is not None and rank <= 3:
        return "人気急上昇"
    if any(x in text for x in ("人気", "急上昇", "ランキング", "注目")):
        return "人気急上昇"
    return "人気急上昇"


def _badge_for(*, has_product_links: bool, badge: str | None = None, title: str = "", keyword: str = "") -> str:
    _ = has_product_links
    return infer_category(title=title, keyword=keyword, badge=badge)


def _image_seed_from_id(article_id: str) -> int:
    digest = hashlib.md5(article_id.encode("utf-8")).hexdigest()
    return (int(digest[:6], 16) % 90) + 1


def _load_template(name: str) -> str:
    path = TEMPLATES / name
    if not path.exists():
        raise FileNotFoundError(f"テンプレートがありません: {path}")
    return path.read_text(encoding="utf-8")


def _apply(template: str, mapping: dict[str, str]) -> str:
    out = template
    for key, value in mapping.items():
        out = out.replace("{{" + key + "}}", value)
    return out


def load_entries() -> list[ArticleEntry]:
    if not ENTRIES_FILE.exists():
        return []
    try:
        data = json.loads(ENTRIES_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("entries 読み込み失敗: %s", exc)
        return []
    rows = data.get("entries", [])
    entries: list[ArticleEntry] = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        aid = str(row.get("article_id", "")).strip()
        filename = str(row.get("filename", "")).strip()
        if not aid or not filename:
            continue
        has_links = bool(row.get("has_product_links", False))
        title = str(row.get("title", "") or aid)
        keyword = str(row.get("keyword", "") or "")
        badge = _badge_for(
            has_product_links=has_links,
            badge=str(row.get("badge", "") or ""),
            title=title,
            keyword=keyword,
        )
        seed_raw = row.get("image_seed")
        try:
            seed = int(seed_raw) if seed_raw is not None else _image_seed_from_id(aid)
        except (TypeError, ValueError):
            seed = _image_seed_from_id(aid)
        entries.append(
            ArticleEntry(
                article_id=aid,
                filename=filename,
                title=title,
                excerpt=str(row.get("excerpt", "") or ""),
                source_link=str(row.get("source_link", "") or ""),
                created_at=str(row.get("created_at", "") or ""),
                has_product_links=has_links,
                keyword=keyword,
                badge=badge,
                image_seed=seed,
                links={str(k): str(v) for k, v in (row.get("links") or {}).items()},
                image_url=str(row.get("image_url", "") or "").strip(),
                status=str(row.get("status", "") or "").strip(),
            )
        )
    return entries


def save_entries(entries: list[ArticleEntry]) -> None:
    DOCS.mkdir(parents=True, exist_ok=True)
    payload = {
        "entries": [
            {
                "article_id": e.article_id,
                "filename": e.filename,
                "title": e.title,
                "excerpt": e.excerpt,
                "source_link": e.source_link,
                "created_at": e.created_at,
                "has_product_links": e.has_product_links,
                "keyword": e.keyword,
                "badge": e.badge,
                "image_seed": e.image_seed,
                "links": e.links,
                "image_url": e.image_url,
                "status": e.status,
            }
            for e in entries
        ]
    }
    ENTRIES_FILE.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _sanitize_public_copy(text: str) -> str:
    out = (text or "").strip()
    for pat in _DISCLAIMER_PATTERNS:
        out = pat.sub("", out)
    out = re.sub(r"[ \t]+\n", "\n", out)
    out = re.sub(r"\n{3,}", "\n\n", out)
    out = re.sub(r"[ \t]{2,}", " ", out)
    return out.strip()


def _excerpt_from_body(text: str, *, limit: int = 140) -> str:
    cleaned = _sanitize_public_copy(text)
    lines = []
    for line in cleaned.splitlines():
        s = line.strip()
        if not s or s.startswith("##") or s.startswith("- "):
            continue
        lines.append(s)
    blob = " ".join(lines) if lines else re.sub(r"\s+", " ", cleaned)
    return blob.strip()[:limit]


def _plain_to_paragraphs(text: str) -> str:
    """メモ調の段落本文を HTML 化する（見出しは出さない）。"""
    cleaned = _sanitize_public_copy(text)
    if not cleaned:
        return "<p>記事本文はありません。</p>"

    strip_labels = {
        "ポイント",
        "向き不向き",
        "注意点",
        "見どころ",
        "刺さる人",
        "ひとこと注意",
        "一言注意",
        "向いている人",
        "購入前の確認",
        "見るべきところ",
        "こんな人向け",
        "この記事の注目ポイント3選",
        "よくある質問",
        "今すぐ各ショップで探す",
    }

    blocks = re.split(r"\n\s*\n", cleaned)
    parts: list[str] = []
    lead_done = False
    for block in blocks:
        lines = [ln.rstrip() for ln in block.splitlines() if ln.strip()]
        if not lines:
            continue

        flat: list[str] = []
        for ln in lines:
            s = ln.strip()
            if s.startswith("## "):
                continue
            if s in strip_labels:
                continue
            if s.startswith(("- ", "・")):
                item = s[2:].strip() if s.startswith("- ") else s[1:].strip()
                if item:
                    flat.append(item + "。")
                continue
            flat.append(s)
        if not flat:
            continue

        para = html.escape(" ".join(flat))
        if not lead_done and not parts:
            parts.append(f'<p class="lead">{para}</p>')
            lead_done = True
        else:
            parts.append(f"<p>{para}</p>")

    return "\n".join(parts) if parts else "<p>記事本文はありません。</p>"



def _product_image_html(image_url: str, *, title: str) -> str:
    url = (image_url or "").strip()
    if not url.startswith(("http://", "https://")):
        return ""
    low = url.lower()
    if any(
        b in low
        for b in (
            "share-icons",
            "amazon.png",
            "spinner",
            "grey-pixel",
            "transparent-pixel",
        )
    ):
        return ""
    return (
        f'<figure class="product-media">'
        f'<img src="{html.escape(url, quote=True)}" '
        f'alt="{html.escape(title)}" loading="lazy" decoding="async" '
        f'referrerpolicy="no-referrer">'
        f"</figure>"
    )


def _is_usable_source_url(url: str) -> bool:
    value = (url or "").strip().lower()
    if not value.startswith(("http://", "https://")):
        return False
    blocked = (
        "example.com",
        "prtimes.jp/example",
        "localhost",
        "127.0.0.1",
    )
    return not any(token in value for token in blocked)


def _source_link_html(source_link: str, *, label: str | None = None) -> str:
    if not _is_usable_source_url(source_link):
        return ""
    href = html.escape(source_link, quote=True)
    text = label or "元の発表・記事を見る"
    low = source_link.lower()
    if label is None and any(
        x in low
        for x in (
            "amazon.co.jp",
            "rakuten.co.jp",
            "hb.afl.rakuten",
            "mercari.com",
            "suruga-ya.jp",
            "shopping.yahoo.co.jp",
        )
    ):
        text = "商品ページを見る"
    return (
        f'<a class="source-link" href="{href}" rel="noopener sponsored nofollow" target="_blank">'
        f"{html.escape(text)}"
        "</a>"
    )


def _status_badge_html(status: str) -> str:
    label = (status or "").strip()
    if not label:
        return ""
    cls = "badge-status"
    if label == "予約受付中":
        cls += " is-reserve"
    elif label == "人気急上昇":
        cls += " is-rise"
    elif label == "完売注意":
        cls += " is-soldout"
    else:
        cls += " is-hot"
    return f'<span class="{cls}">{html.escape(label)}</span>'


def _shop_links_html(links: dict[str, str], *, has_product_links: bool) -> str:
    """Amazon〜駿河屋まで同一デザインのショップリンク。"""
    if not has_product_links:
        return ""
    mapping = (
        ("amazon", "Amazon", "shop-amazon"),
        ("rakuten", "楽天市場", "shop-rakuten"),
        ("yahoo", "Yahoo!", "shop-yahoo"),
        ("mercari", "メルカリ", "shop-mercari"),
        ("surugaya", "駿河屋", "shop-surugaya"),
    )
    buttons: list[str] = []
    for key, label, cls in mapping:
        url = (links.get(key) or "").strip()
        if not url:
            continue
        buttons.append(
            f'<a class="shop-link {cls}" href="{html.escape(url, quote=True)}" '
            f'rel="nofollow sponsored noopener" target="_blank">{html.escape(label)}</a>'
        )
    if not buttons:
        return ""
    return (
        '<section class="shop-panel" aria-label="各ショップで探す">'
        '<p class="shop-panel-label">各ショップで探す</p>'
        f'<div class="shop-grid">{"".join(buttons)}</div>'
        "</section>"
    )


def _html_to_plain(fragment: str) -> str:
    """既存記事 HTML 断片からプレーンテキストを復元する。"""
    text = fragment or ""
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</p\s*>", "\n\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</h2\s*>", "\n\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</li\s*>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<li[^>]*>", "- ", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _extract_content_plain_from_article(path: Path) -> str:
    if not path.exists():
        return ""
    raw = path.read_text(encoding="utf-8")
    match = re.search(
        r'<div class="content">\s*(.*?)\s*</div>',
        raw,
        flags=re.DOTALL | re.IGNORECASE,
    )
    if not match:
        return ""
    return _html_to_plain(match.group(1))


def render_article_page(
    *,
    title: str,
    body: str,
    source_link: str,
    created_at: str,
    has_product_links: bool,
    links: dict[str, str],
    canonical_url: str,
    badge: str | None = None,
    image_url: str = "",
    keyword: str = "",
    status: str = "",
) -> str:
    _ = source_link
    excerpt = _excerpt_from_body(body)
    badge_label = _badge_for(
        has_product_links=has_product_links,
        badge=badge,
        title=title,
        keyword=keyword,
    )
    status_label = status or infer_status(title=title, body=body)
    template = _load_template("article.html")
    return _apply(
        template,
        {
            "PAGE_TITLE": html.escape(title),
            "META_DESCRIPTION": html.escape(excerpt),
            "CANONICAL_URL": html.escape(canonical_url),
            "OG_IMAGE_TAG": (
                f'<meta property="og:image" content="{html.escape(image_url, quote=True)}">'
                if (image_url or "").startswith(("http://", "https://"))
                else ""
            ),
            "BADGE": html.escape(badge_label),
            "BADGE_CLASS": _badge_class(badge_label),
            "STATUS_BADGE": _status_badge_html(status_label),
            "PUBLISH_DATE": html.escape(created_at[:10]),
            "PRODUCT_IMAGE": _product_image_html(image_url, title=title),
            "ARTICLE_BODY": _plain_to_paragraphs(body),
            "SHOP_LINKS": _shop_links_html(links, has_product_links=has_product_links),
            "YEAR": str(datetime.now(timezone.utc).year),
        },
    )


def rebuild_all_article_pages() -> int:
    """既存エントリのレイアウトを新テンプレで再出力する。"""
    entries = load_entries()
    count = 0
    for entry in entries:
        path = DOCS / entry.filename
        body = _extract_content_plain_from_article(path)
        if not body:
            body = entry.excerpt or entry.title
        page = render_article_page(
            title=entry.title,
            body=body,
            source_link=entry.source_link,
            created_at=entry.created_at,
            has_product_links=entry.has_product_links,
            links=entry.links,
            canonical_url=article_public_url(entry.article_id),
            badge=entry.badge,
            image_url=entry.image_url,
            keyword=entry.keyword,
            status=entry.status,
        )
        path.write_text(page, encoding="utf-8")
        count += 1
    index_html = render_index_page(entries)
    (DOCS / "index.html").write_text(index_html, encoding="utf-8")
    logger.info("記事レイアウト再生成: %s 件", count)
    return count


def _badge_class(badge: str) -> str:
    return BADGE_CLASS.get(badge, "badge-figure")


def _pick_featured(entries: list[ArticleEntry]) -> ArticleEntry:
    product_first = [e for e in entries if e.has_product_links]
    pool = product_first or list(entries)
    return sorted(pool, key=lambda e: e.created_at, reverse=True)[0]


def _category_slug(name: str) -> str:
    mapping = {
        "すべて": "all",
        "ポケモン": "pokemon",
        "めじるしチャーム・ガチャ": "gacha",
        "サンリオ・キャラグッズ": "sanrio",
        "ベイブレード・トレカ": "beyblade",
        "フィギュア・ホビー": "figure",
    }
    return mapping.get(name, "all")


def _render_category_strip(entries: list[ArticleEntry]) -> str:
    counts = Counter(e.badge for e in entries if e.badge in CONTENT_CATEGORIES)
    chips: list[str] = []
    for name in SITE_CATEGORIES:
        slug = _category_slug(name)
        label = html.escape(name)
        if name == "すべて":
            chips.append(
                f'<a class="cat-chip is-active" href="#latest" data-category="all">'
                f"{label}"
                f'<span class="cat-count">{len(entries)}</span>'
                f"</a>"
            )
            continue
        count = counts.get(name, 0)
        if count > 0:
            chips.append(
                f'<a class="cat-chip is-active" href="#cat-{slug}" data-category="{slug}">'
                f"{label}"
                f'<span class="cat-count">{count}</span>'
                f"</a>"
            )
        else:
            chips.append(
                f'<span class="cat-chip is-empty" title="このカテゴリの記事はまだありません">'
                f"{label}"
                f'<span class="cat-soon">準備中</span>'
                f"</span>"
            )
    return "".join(chips)


def _render_card(entry: ArticleEntry, *, featured: bool = False) -> str:
    cls = "card card-featured" if featured else "card"
    title_tag = "h2" if featured else "h3"
    title = html.escape(entry.title)
    excerpt = html.escape((entry.excerpt or "")[:140])
    date = html.escape(entry.created_at[:10] if entry.created_at else "")
    badge = html.escape(entry.badge)
    badge_cls = _badge_class(entry.badge)
    href = html.escape(entry.filename)
    slug = _category_slug(entry.badge)
    media = ""
    if (entry.image_url or "").startswith(("http://", "https://")):
        media = (
            f'<div class="card-media">'
            f'<img src="{html.escape(entry.image_url, quote=True)}" '
            f'alt="" loading="lazy" decoding="async" referrerpolicy="no-referrer">'
            f"</div>"
        )
    status = _status_badge_html(entry.status) if entry.status else ""
    return (
        f'<a class="{cls}" href="{href}" data-category="{slug}">'
        f"{media}"
        f'<div class="card-body">'
        f'<div class="badge-row"><span class="badge {badge_cls}">{badge}</span>{status}</div>'
        f"<{title_tag}>{title}</{title_tag}>"
        f'<p class="card-excerpt">{excerpt}</p>'
        f'<span class="card-date">{date}</span>'
        f"</div></a>"
    )


def render_index_page(entries: list[ArticleEntry]) -> str:
    sorted_entries = sorted(entries, key=lambda e: e.created_at, reverse=True)
    template = _load_template("index.html")
    category_strip = _render_category_strip(sorted_entries)
    if not sorted_entries:
        featured = (
            '<div class="card card-featured">'
            '<div class="card-body">'
            "<h2>まだ記事がありません</h2>"
            '<p class="card-excerpt">自動更新後に最新トピックが表示されます。</p>'
            "</div></div>"
        )
        latest = '<p class="empty">追加の記事はまだありません。</p>'
    else:
        top = _pick_featured(sorted_entries)
        featured = _render_card(top, featured=True)
        rest = [e for e in sorted_entries if e.article_id != top.article_id][:TOP_LIST_LIMIT]
        latest = "\n".join(_render_card(e) for e in rest) or (
            '<p class="empty">追加の記事はまだありません。</p>'
        )
    return _apply(
        template,
        {
            "CATEGORY_STRIP": category_strip,
            "FEATURED_BLOCK": featured,
            "LATEST_LIST": latest,
            "YEAR": str(datetime.now(timezone.utc).year),
        },
    )


def publish_article(
    *,
    source_link: str,
    title: str,
    body: str,
    has_product_links: bool,
    keyword: str,
    links: dict[str, str] | None = None,
    created_at: str | None = None,
    badge: str | None = None,
    image_seed: int | None = None,
    image_url: str = "",
    status: str = "",
) -> ArticleEntry:
    """個別記事を書き、entries と index を更新する。"""
    DOCS.mkdir(parents=True, exist_ok=True)
    aid = article_id_from_link(source_link)
    filename = article_filename(aid)
    stamp = created_at or datetime.now(timezone.utc).isoformat()
    body = _sanitize_public_copy(body)
    excerpt = _excerpt_from_body(body)
    badge_label = _badge_for(
        has_product_links=has_product_links,
        badge=badge,
        title=title,
        keyword=keyword,
    )
    seed = image_seed if image_seed is not None else _image_seed_from_id(aid)
    image = (image_url or "").strip()
    status_label = status or infer_status(title=title, body=body)
    entry = ArticleEntry(
        article_id=aid,
        filename=filename,
        title=title,
        excerpt=excerpt,
        source_link=source_link,
        created_at=stamp,
        has_product_links=has_product_links,
        keyword=keyword,
        badge=badge_label,
        image_seed=seed,
        links=links or {},
        image_url=image,
        status=status_label,
    )

    page = render_article_page(
        title=title,
        body=body,
        source_link=source_link,
        created_at=stamp,
        has_product_links=has_product_links,
        links=entry.links,
        canonical_url=article_public_url(aid),
        badge=badge_label,
        image_url=image,
        keyword=keyword,
        status=status_label,
    )
    (DOCS / filename).write_text(page, encoding="utf-8")
    logger.info("個別記事を出力: %s", filename)

    entries = [e for e in load_entries() if e.article_id != aid]
    entries.append(entry)
    entries = sorted(entries, key=lambda e: e.created_at, reverse=True)[:80]
    save_entries(entries)

    index_html = render_index_page(entries)
    (DOCS / "index.html").write_text(index_html, encoding="utf-8")
    logger.info("index.html を再生成（件数=%s）", len(entries))
    return entry


def _is_demo_source(url: str) -> bool:
    return not _is_usable_source_url(url)


def purge_demo_entries() -> list[ArticleEntry]:
    entries = load_entries()
    kept: list[ArticleEntry] = []
    removed = 0
    for entry in entries:
        if _is_demo_source(entry.source_link):
            path = DOCS / entry.filename
            if path.exists():
                path.unlink()
            removed += 1
            continue
        kept.append(entry)
    save_entries(kept)
    (DOCS / "index.html").write_text(render_index_page(kept), encoding="utf-8")
    logger.info("ダミー記事を削除: %s 件（残 %s 件）", removed, len(kept))
    return kept


_BAD_TITLE_MARKERS = (
    "管理番号",
    "商品番号",
    "フィギュア（楽天）",
    "「Amazon」の情報をチェック",
)


def is_bad_product_article(entry: ArticleEntry) -> bool:
    title = entry.title or ""
    excerpt = entry.excerpt or ""
    blob = f"{title}\n{excerpt}"
    if any(m in blob for m in _BAD_TITLE_MARKERS):
        return True
    if re.search(r"（楽天）\s*\d{5,}", blob):
        return True
    if re.search(r"人気商品「Amazon」", title):
        return True
    return False


def purge_bad_product_entries() -> list[ArticleEntry]:
    entries = load_entries()
    kept: list[ArticleEntry] = []
    removed = 0
    for entry in entries:
        if entry.has_product_links and is_bad_product_article(entry):
            path = DOCS / entry.filename
            if path.exists():
                path.unlink()
            removed += 1
            logger.info("ゴミ商品記事を削除: %s (%s)", entry.article_id, entry.title[:40])
            continue
        kept.append(entry)
    save_entries(kept)
    (DOCS / "index.html").write_text(render_index_page(kept), encoding="utf-8")
    logger.info("ゴミ商品記事を削除: %s 件（残 %s 件）", removed, len(kept))
    return kept


def ensure_demo_volume(min_total: int = 7) -> list[ArticleEntry]:
    _ = min_total
    return purge_demo_entries()
