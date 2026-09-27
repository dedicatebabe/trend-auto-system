# ==========================================
# Version: 1.0.0
# Date: 2026-09-27
# Summary: X添付用画像DLとWebPサムネ生成
# ==========================================
"""商品画像のダウンロードと WebP 変換。"""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path
from urllib.parse import urlparse

import requests

logger = logging.getLogger(__name__)

_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


def _is_http_url(url: str) -> bool:
    return (url or "").strip().lower().startswith(("http://", "https://"))


def download_image_bytes(url: str, *, timeout: float = 20.0) -> bytes | None:
    """画像URLからバイト列を取得する。失敗時は None。"""
    if not _is_http_url(url):
        return None
    try:
        resp = requests.get(
            url,
            timeout=timeout,
            headers={"User-Agent": _USER_AGENT, "Accept": "image/*,*/*"},
        )
        if resp.status_code != 200 or not resp.content:
            logger.warning("画像DL失敗 status=%s url=%s", resp.status_code, url[:120])
            return None
        ctype = (resp.headers.get("Content-Type") or "").lower()
        if "html" in ctype:
            logger.warning("画像ではなくHTMLが返った: %s", url[:120])
            return None
        if len(resp.content) < 800:
            logger.warning("画像が小さすぎる: %s bytes", len(resp.content))
            return None
        return resp.content
    except requests.RequestException as exc:
        logger.warning("画像DL例外: %s", exc)
        return None


def _suffix_from_url(url: str) -> str:
    path = urlparse(url).path.lower()
    for ext in (".jpg", ".jpeg", ".png", ".webp", ".gif"):
        if path.endswith(ext):
            return ext if ext != ".jpeg" else ".jpg"
    return ".jpg"


def save_temp_image(data: bytes, *, suffix: str = ".jpg") -> Path | None:
    """一時ファイルへ保存してパスを返す。"""
    try:
        fd, name = tempfile.mkstemp(prefix="trendpick_x_", suffix=suffix)
        path = Path(name)
        with open(fd, "wb") as fh:
            fh.write(data)
        return path
    except OSError as exc:
        logger.warning("一時画像保存失敗: %s", exc)
        return None


def to_webp_file(
    data: bytes,
    dest: Path,
    *,
    max_side: int = 800,
    quality: int = 78,
) -> Path | None:
    """バイト列を WebP に変換して dest に保存する。"""
    try:
        from io import BytesIO

        from PIL import Image
    except ImportError:
        logger.warning("Pillow 未導入のため WebP 変換をスキップ")
        return None

    try:
        img = Image.open(BytesIO(data))
        img = img.convert("RGB")
        w, h = img.size
        scale = min(1.0, float(max_side) / float(max(w, h) or 1))
        if scale < 1.0:
            img = img.resize(
                (max(1, int(w * scale)), max(1, int(h * scale))),
                Image.Resampling.LANCZOS,
            )
        dest.parent.mkdir(parents=True, exist_ok=True)
        img.save(dest, format="WEBP", quality=quality, method=4)
        return dest if dest.is_file() else None
    except Exception as exc:  # noqa: BLE001
        logger.warning("WebP変換失敗: %s", exc)
        return None


def prepare_parent_images(
    image_urls: list[str],
    *,
    max_count: int = 4,
) -> list[Path]:
    """親ポスト用に最大 max_count 枚のローカル画像パスを用意する。"""
    paths: list[Path] = []
    seen: set[str] = set()
    for raw in image_urls:
        url = (raw or "").strip()
        if not url or url in seen:
            continue
        seen.add(url)
        data = download_image_bytes(url)
        if not data:
            continue
        path = save_temp_image(data, suffix=_suffix_from_url(url))
        if path:
            paths.append(path)
        if len(paths) >= max_count:
            break
    return paths


def prepare_reply_webp(
    image_url: str,
    *,
    dest: Path | None = None,
) -> Path | None:
    """リプライ用 WebP サムネを生成する。"""
    data = download_image_bytes(image_url)
    if not data:
        return None
    if dest is None:
        tmp = tempfile.NamedTemporaryFile(
            prefix="trendpick_reply_",
            suffix=".webp",
            delete=False,
        )
        dest = Path(tmp.name)
        tmp.close()
    return to_webp_file(data, dest)


def cache_article_webp(
    image_url: str,
    *,
    docs_dir: Path,
    article_id: str,
    site_base: str,
) -> tuple[str, Path | None]:
    """
    記事用に docs/media/{id}.webp を生成する。

    戻り値: (表示用URL, ローカルWebPパス)
    失敗時は元URLと None。
    """
    raw = (image_url or "").strip()
    if not _is_http_url(raw):
        return "", None
    dest = docs_dir / "media" / f"{article_id}.webp"
    data = download_image_bytes(raw)
    if not data:
        return raw, None
    saved = to_webp_file(data, dest)
    if not saved:
        return raw, None
    public = f"{site_base.rstrip('/')}/media/{article_id}.webp"
    return public, saved
