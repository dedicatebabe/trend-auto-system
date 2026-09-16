# ==========================================
# Version: 2.2.0
# Date: 2026-09-16
# Summary: 商品画像表示と見出し付き本文レンダリングを追加
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
TOP_LIST_LIMIT = 6

BADGE_CLASS = {
    "NEWS": "badge-news",
    "注目トピック": "badge-item",
    "カルチャー": "badge-goods",
    "アニメ": "badge-anime",
    "ゲーム": "badge-game",
    "ガジェット": "badge-gadget",
}

# 話題カテゴリを先に、関連トピック系は後ろに置く
SITE_CATEGORIES = (
    "NEWS",
    "アニメ",
    "ゲーム",
    "ガジェット",
    "注目トピック",
    "カルチャー",
)

FEATURE_TOPIC_BADGES = frozenset({"NEWS", "アニメ", "ゲーム", "ガジェット", "カルチャー"})
LEGACY_BADGE_MAP = {
    "商品": "注目トピック",
    "商品ピックアップ": "注目トピック",
    "注目アイテム": "注目トピック",
    "トレンドグッズ": "カルチャー",
}

# 記事本文・抜粋から除去する断り／メタ文言
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
    badge: str = "NEWS"
    image_seed: int = 1
    links: dict[str, str] = field(default_factory=dict)
    image_url: str = ""


def article_id_from_link(link: str) -> str:
    digest = hashlib.sha1(link.encode("utf-8")).hexdigest()[:10]
    return f"n_{digest}"


def article_filename(article_id: str) -> str:
    safe = re.sub(r"[^\w\-]", "_", article_id)
    return f"article_{safe}.html"


def article_public_url(article_id: str, *, base: str = SITE_BASE) -> str:
    return f"{base.rstrip('/')}/{article_filename(article_id)}"


def _badge_for(*, has_product_links: bool, badge: str | None = None) -> str:
    if badge and badge.strip():
        raw = badge.strip()
        return LEGACY_BADGE_MAP.get(raw, raw)
    # 商品リンク有無だけでカテゴリを決めない（話題メディアの体裁を優先）
    return "NEWS"


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
        badge = _badge_for(
            has_product_links=has_links,
            badge=str(row.get("badge", "") or ""),
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
                title=str(row.get("title", "") or aid),
                excerpt=str(row.get("excerpt", "") or ""),
                source_link=str(row.get("source_link", "") or ""),
                created_at=str(row.get("created_at", "") or ""),
                has_product_links=has_links,
                keyword=str(row.get("keyword", "") or ""),
                badge=badge,
                image_seed=seed,
                links={str(k): str(v) for k, v in (row.get("links") or {}).items()},
                image_url=str(row.get("image_url", "") or "").strip(),
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
            }
            for e in entries
        ]
    }
    ENTRIES_FILE.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _sanitize_public_copy(text: str) -> str:
    """公開文面から買い物リンク無しなどの断りを取り除く。"""
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
    """プレーンテキストを見出し・箇条書き付きHTMLに変換。"""
    cleaned = _sanitize_public_copy(text)
    if not cleaned:
        return "<p>記事本文はありません。</p>"

    blocks = re.split(r"\n\s*\n", cleaned)
    parts: list[str] = []
    lead_done = False
    for block in blocks:
        lines = [ln.rstrip() for ln in block.splitlines() if ln.strip()]
        if not lines:
            continue

        # 見出し単体ブロック
        if len(lines) == 1 and lines[0].startswith("## "):
            parts.append(f"<h2>{html.escape(lines[0][3:].strip())}</h2>")
            continue

        # 見出し＋続き
        if lines[0].startswith("## "):
            parts.append(f"<h2>{html.escape(lines[0][3:].strip())}</h2>")
            lines = lines[1:]
            if not lines:
                continue

        # 箇条書き
        if all(ln.startswith(("- ", "・")) for ln in lines):
            items = []
            for ln in lines:
                item = ln[2:].strip() if ln.startswith("- ") else ln[1:].strip()
                items.append(f"<li>{html.escape(item)}</li>")
            parts.append(f"<ul>{''.join(items)}</ul>")
            continue

        para = html.escape("\n".join(lines)).replace("\n", "<br>")
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
    """デモ用・明らかに無効な出典URLは出さない。"""
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
        )
    ):
        text = "商品ページを見る"
    return (
        f'<a class="source-link" href="{href}" rel="noopener sponsored nofollow" target="_blank">'
        f"{html.escape(text)}"
        "</a>"
    )


def _product_links_html(links: dict[str, str], *, has_product_links: bool) -> str:
    if not has_product_links:
        return ""
    items = [
        ("amazon", "Amazon", "A", "btn-amazon"),
        ("rakuten", "楽天", "楽", "btn-rakuten"),
        ("mercari", "メルカリ", "M", "btn-mercari"),
        ("surugaya", "駿河屋", "駿", "btn-surugaya"),
    ]
    buttons: list[str] = []
    for key, label, mark, cls in items:
        url = links.get(key, "").strip()
        if not url:
            continue
        buttons.append(
            f'<a class="shop-icon {cls}" href="{html.escape(url, quote=True)}" '
            f'rel="nofollow sponsored noopener" target="_blank" '
            f'title="{html.escape(label)}" aria-label="{html.escape(label)}">'
            f'<span aria-hidden="true">{html.escape(mark)}</span>'
            f"</a>"
        )
    if not buttons:
        return ""
    return f'<div class="shop-icons" aria-label="関連ショップ">{"".join(buttons)}</div>'


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
) -> str:
    excerpt = _excerpt_from_body(body)
    badge_label = _badge_for(has_product_links=has_product_links, badge=badge)
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
            "PUBLISH_DATE": html.escape(created_at[:10]),
            "PRODUCT_IMAGE": _product_image_html(image_url, title=title),
            "ARTICLE_BODY": _plain_to_paragraphs(body),
            "SOURCE_LINK": _source_link_html(source_link),
            "PRODUCT_LINKS": _product_links_html(
                links, has_product_links=has_product_links
            ),
            "YEAR": str(datetime.now(timezone.utc).year),
        },
    )


def _badge_class(badge: str) -> str:
    return BADGE_CLASS.get(badge, "badge-news")


def _pick_featured(entries: list[ArticleEntry]) -> ArticleEntry:
    """
    注目枠用に1本選ぶ。

    アフィリ収益優先のため、商品リンクありを先に、なければ最新話題。
    """
    product_first = [e for e in entries if e.has_product_links]
    pool = product_first or list(entries)
    return sorted(pool, key=lambda e: e.created_at, reverse=True)[0]


def _render_category_strip(entries: list[ArticleEntry]) -> str:
    """記事ありは件数付きchip、ゼロ件は破線の「準備中」chip。"""
    counts = Counter(e.badge for e in entries)
    chips: list[str] = []
    for name in SITE_CATEGORIES:
        count = counts.get(name, 0)
        label = html.escape(name)
        if count > 0:
            chips.append(
                f'<a class="cat-chip is-active" href="#latest">'
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
    media = ""
    if (entry.image_url or "").startswith(("http://", "https://")):
        media = (
            f'<div class="card-media">'
            f'<img src="{html.escape(entry.image_url, quote=True)}" '
            f'alt="" loading="lazy" decoding="async" referrerpolicy="no-referrer">'
            f"</div>"
        )
    return (
        f'<a class="{cls}" href="{href}">'
        f"{media}"
        f'<div class="card-body">'
        f'<span class="badge {badge_cls}">{badge}</span>'
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
        rest = [e for e in sorted_entries if e.article_id != top.article_id][
            :TOP_LIST_LIMIT
        ]
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
) -> ArticleEntry:
    """個別記事を書き、entries と index を更新する。"""
    DOCS.mkdir(parents=True, exist_ok=True)
    aid = article_id_from_link(source_link)
    filename = article_filename(aid)
    stamp = created_at or datetime.now(timezone.utc).isoformat()
    body = _sanitize_public_copy(body)
    excerpt = _excerpt_from_body(body)
    badge_label = _badge_for(has_product_links=has_product_links, badge=badge)
    seed = image_seed if image_seed is not None else _image_seed_from_id(aid)
    image = (image_url or "").strip()
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
    """
    ダミー記事を削除し、実記事だけ残して index を再生成する。

    ダミーは自動では消えないため、明示的に掃除して使う。
    """
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
    """人間が読めない商品紹介記事か。"""
    title = entry.title or ""
    excerpt = entry.excerpt or ""
    blob = f"{title}\n{excerpt}"
    if any(m in blob for m in _BAD_TITLE_MARKERS):
        return True
    if re.search(r"（楽天）\s*\d{5,}", blob):
        return True
    # タイトルがほぼ "Amazon" だけ
    if re.search(r"人気商品「Amazon」", title):
        return True
    return False


def purge_bad_product_entries() -> list[ArticleEntry]:
    """管理番号・スラッグ名など読めない商品記事を削除して index 再生成。"""
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
    """
    互換のためのスタブ。

    ダミー補充は行わず、既存ダミーを削除して実記事のみにする。
    """
    _ = min_total
    return purge_demo_entries()
