# ==========================================
# Version: 1.1.0
# Date: 2026-09-24
# Summary: 単発投稿（リンク込み）を追加。リプライは任意
# ==========================================
"""X Web UI 経由の投稿（API課金なし）。"""

from __future__ import annotations

import json
import logging
import os
import random
import time
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_PROFILE_DIR = Path.home() / ".trend-pick" / "x-browser-profile"
BROWSERS_PATH = Path.home() / "Library" / "Caches" / "ms-playwright"
COMPOSE_URL = "https://x.com/compose/post"
HOME_URL = "https://x.com/home"


def ensure_browsers_path() -> None:
    """Playwright のブラウザをユーザーホーム配下から読む。"""
    BROWSERS_PATH.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(BROWSERS_PATH))


def browser_profile_dir() -> Path:
    """永続ログイン用プロファイルディレクトリ。"""
    path = DEFAULT_PROFILE_DIR
    path.mkdir(parents=True, exist_ok=True)
    return path


def launch_x_context(playwright, *, headless: bool, profile_dir: Path):
    """
    X 操作用の永続コンテキストを起動する。
    ログインと自動投稿で同じ Playwright Chromium を使う。
    """
    ensure_browsers_path()
    common = {
        "user_data_dir": str(profile_dir),
        "headless": headless,
        "viewport": {"width": 1280, "height": 900},
        "locale": "ja-JP",
        "args": [
            "--disable-blink-features=AutomationControlled",
            "--no-first-run",
            "--no-default-browser-check",
        ],
    }
    logger.info("ブラウザ: Playwright Chromium headless=%s", headless)
    try:
        return playwright.chromium.launch_persistent_context(**common)
    except Exception as exc:
        raise RuntimeError(
            "ブラウザを起動できません。ターミナルで次を実行してください:\n"
            f'  PLAYWRIGHT_BROWSERS_PATH="{BROWSERS_PATH}" '
            "/Users/shin/anaconda3/bin/playwright install chromium"
        ) from exc


def _sleep(a: float = 0.4, b: float = 1.2) -> None:
    time.sleep(random.uniform(a, b))


def _fill_composer(page, text: str) -> None:
    """投稿テキスト欄に入力する（contenteditable 対応）。"""
    box = page.locator('[data-testid="tweetTextarea_0"]').first
    box.wait_for(state="visible", timeout=30000)
    box.click()
    _sleep(0.2, 0.5)
    page.keyboard.press("Meta+A")
    page.keyboard.press("Backspace")
    _sleep(0.1, 0.2)
    page.keyboard.insert_text(text)
    _sleep(0.5, 1.0)
    current = (box.inner_text() or "").strip()
    if len(current) < max(1, len(text) // 3):
        box.type(text, delay=12)
        _sleep(0.4, 0.8)


def _attach_image(page, image_path: Path) -> None:
    """画像を添付する。"""
    if not image_path.is_file():
        raise FileNotFoundError(f"画像がありません: {image_path}")
    file_input = page.locator('input[data-testid="fileInput"]').first
    file_input.set_input_files(str(image_path))
    page.locator('[data-testid="attachments"]').first.wait_for(
        state="visible",
        timeout=30000,
    )
    _sleep(0.8, 1.5)


def _dismiss_blocking_dialogs(page) -> None:
    """Cookie / 通知などのダイアログだけ閉じる（Escape は使わない）。"""
    candidates = [
        '[data-testid="confirmationSheetConfirm"]',
        'div[role="dialog"] button:has-text("OK")',
        'div[role="dialog"] button:has-text("Close")',
        'div[role="dialog"] button:has-text("閉じる")',
        'div[role="dialog"] button:has-text("Not now")',
        'div[role="dialog"] button:has-text("Not Now")',
        'div[role="dialog"] button:has-text("後で")',
        'button:has-text("Accept all cookies")',
        'button:has-text("Accept")',
        'button:has-text("同意")',
    ]
    for sel in candidates:
        loc = page.locator(sel)
        try:
            if loc.count() and loc.first.is_visible():
                loc.first.click(timeout=2000)
                _sleep(0.3, 0.6)
        except Exception:  # noqa: BLE001
            continue


def _click_post(page) -> None:
    """投稿を送る。オーバーレイ対策でショートカット優先。"""
    _dismiss_blocking_dialogs(page)
    box = page.locator('[data-testid="tweetTextarea_0"]').first
    try:
        box.click(force=True, timeout=5000)
    except Exception:  # noqa: BLE001
        pass
    _sleep(0.2, 0.4)
    page.keyboard.press("Meta+Enter")
    _sleep(1.0, 1.5)

    # まだ作曲欄が残っているならボタンも試す
    still = page.locator('[data-testid="tweetTextarea_0"]')
    try:
        if still.count() == 0 or not still.first.is_visible():
            return
    except Exception:  # noqa: BLE001
        return

    page.evaluate(
        """() => {
          const blockers = document.querySelectorAll('div.r-aqfbo4, div.r-1p0dtai');
          blockers.forEach((el) => {
            el.style.pointerEvents = 'none';
          });
          const btn =
            document.querySelector('[data-testid="tweetButton"]') ||
            document.querySelector('[data-testid="tweetButtonInline"]');
          if (btn) btn.click();
        }"""
    )
    _sleep(0.8, 1.2)


def _extract_tweet_id(payload: object) -> str | None:
    """CreateTweet JSON から新規投稿の rest_id だけを拾う。"""
    if not isinstance(payload, dict):
        return None
    data = payload.get("data")
    if not isinstance(data, dict):
        return None
    create = data.get("create_tweet") or data.get("CreateTweet")
    if not isinstance(create, dict):
        return None
    results = create.get("tweet_results")
    if not isinstance(results, dict):
        return None
    result = results.get("result")
    if not isinstance(result, dict):
        return None
    if result.get("rest_id"):
        return str(result["rest_id"])
    # TweetWithVisibilityResults など
    tweet = result.get("tweet")
    if isinstance(tweet, dict) and tweet.get("rest_id"):
        return str(tweet["rest_id"])
    return None


def _wait_compose_closed(page, timeout_ms: int = 30000) -> bool:
    """投稿ダイアログが閉じたら True。"""
    deadline = time.time() + timeout_ms / 1000
    while time.time() < deadline:
        still = page.locator('[data-testid="tweetTextarea_0"]')
        try:
            if still.count() == 0 or not still.first.is_visible():
                return True
        except Exception:  # noqa: BLE001
            return True
        toast = page.locator('[data-testid="toast"]')
        try:
            if toast.count():
                text = (toast.first.inner_text() or "").lower()
                if "posted" in text or "ポスト" in text or "送信" in text:
                    return True
        except Exception:  # noqa: BLE001
            pass
        _sleep(0.4, 0.7)
    return False


def _submit_composer(page) -> str:
    """現在の作曲画面を送信し、作成された tweet_id を返す。"""
    captured: list[str] = []
    errors: list[str] = []

    def on_response(response) -> None:
        try:
            url = response.url
            if "CreateTweet" not in url:
                return
            body_text = ""
            try:
                body_text = response.text()[:2000]
            except Exception:  # noqa: BLE001
                body_text = ""
            if response.status != 200:
                errors.append(f"status={response.status} body={body_text[:300]}")
                return
            data = response.json()
            tid = _extract_tweet_id(data)
            if tid:
                captured.append(tid)
                logger.info("CreateTweet 応答 tweet_id=%s", tid)
                return
            # エラーメッセージを残す
            raw = json.dumps(data, ensure_ascii=False)[:500]
            errors.append(f"no_id body={raw}")
        except Exception as exc:  # noqa: BLE001
            errors.append(f"parse={exc}")

    page.on("response", on_response)
    try:
        _click_post(page)
        deadline = time.time() + 45
        while time.time() < deadline and not captured:
            if _wait_compose_closed(page, timeout_ms=1500):
                break
            _sleep(0.3, 0.5)
        if not captured:
            _sleep(2.0, 3.0)
        if not captured:
            detail = " / ".join(errors[-3:]) if errors else "CreateTweet応答なし"
            raise RuntimeError(f"投稿は押したが tweet_id を取得できませんでした。{detail}")
        return captured[-1]
    finally:
        try:
            page.remove_listener("response", on_response)
        except Exception:  # noqa: BLE001
            pass


def _open_status(page, tweet_id: str) -> str:
    """ステータスページを開く。"""
    url = f"https://x.com/i/web/status/{tweet_id}"
    page.goto(url, wait_until="domcontentloaded")
    _sleep(1.2, 2.0)
    return url


def _open_reply_composer(page, parent_tweet_id: str) -> None:
    """親投稿ページの返信欄を開く。"""
    url = f"https://x.com/i/web/status/{parent_tweet_id}"
    page.goto(url, wait_until="domcontentloaded")
    _sleep(1.5, 2.5)
    # 画面下部またはインラインの返信欄
    box = page.locator('[data-testid="tweetTextarea_0"]').first
    try:
        box.wait_for(state="visible", timeout=8000)
    except Exception:  # noqa: BLE001
        reply_btn = page.locator('[data-testid="reply"]').first
        reply_btn.click(timeout=10000)
        _sleep(0.8, 1.4)
        box = page.locator('[data-testid="tweetTextarea_0"]').first
        box.wait_for(state="visible", timeout=20000)
    box.click()
    _sleep(0.3, 0.6)


def _save_failure_screenshot(page, label: str) -> Path | None:
    """失敗時の画面を保存する。"""
    try:
        out_dir = Path(__file__).resolve().parent.parent / "logs" / "screenshots"
        out_dir.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d_%H%M%S")
        path = out_dir / f"{stamp}_{label}.png"
        page.screenshot(path=str(path), full_page=True)
        logger.info("失敗スクリーンショット: %s", path)
        return path
    except Exception as exc:  # noqa: BLE001
        logger.warning("スクリーンショット保存失敗: %s", exc)
        return None


def _ensure_logged_in(page) -> None:
    """ログイン済みか確認する。"""
    page.goto(HOME_URL, wait_until="domcontentloaded")
    try:
        page.wait_for_load_state("networkidle", timeout=15000)
    except Exception:  # noqa: BLE001
        pass
    _sleep(1.5, 2.5)
    url = page.url.lower()
    if "login" in url or "i/flow/login" in url:
        _save_failure_screenshot(page, "not_logged_in")
        raise RuntimeError(
            "X にログインしていません。"
            " python scripts/x_browser_login.py を実行してログインしてください。"
        )

    markers = [
        '[data-testid="SideNav_NewTweet_Button"]',
        '[data-testid="AppTabBar_Home_Link"]',
        '[data-testid="primaryColumn"]',
        '[aria-label="ポストする"]',
        '[aria-label="Post"]',
        'a[href="/compose/post"]',
    ]
    for sel in markers:
        loc = page.locator(sel)
        try:
            if loc.count() and loc.first.is_visible():
                logger.info("ログイン確認OK marker=%s", sel)
                return
        except Exception:  # noqa: BLE001
            continue
        try:
            loc.first.wait_for(state="visible", timeout=5000)
            logger.info("ログイン確認OK marker=%s", sel)
            return
        except Exception:  # noqa: BLE001
            continue

    page.goto(COMPOSE_URL, wait_until="domcontentloaded")
    _sleep(1.0, 2.0)
    url = page.url.lower()
    if "login" in url or "i/flow/login" in url:
        _save_failure_screenshot(page, "not_logged_in")
        raise RuntimeError(
            "X にログインしていません。"
            " python scripts/x_browser_login.py を実行してログインしてください。"
        )
    box = page.locator('[data-testid="tweetTextarea_0"]')
    try:
        box.first.wait_for(state="visible", timeout=10000)
        logger.info("ログイン確認OK compose textarea")
        return
    except Exception:  # noqa: BLE001
        _save_failure_screenshot(page, "home_unrecognized")
        raise RuntimeError(
            "X のホームを認識できません。再ログインしてください"
            "（python scripts/x_browser_login.py）。"
        )


def post_single_to_x_via_browser(
    text: str,
    *,
    local_image_path: str | None = None,
    headless: bool = True,
    profile_dir: Path | None = None,
) -> str:
    """
    ブラウザで単発ポストする（リンク込み本文想定）。

    戻り値: tweet_id
    """
    body = (text or "").strip()
    if not body:
        raise ValueError("投稿文が空です。")

    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError(
            "playwright が未インストールです。"
            " pip install playwright && playwright install chromium"
        ) from exc

    profile = Path(profile_dir) if profile_dir else browser_profile_dir()
    profile.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        context = launch_x_context(p, headless=headless, profile_dir=profile)
        page = context.pages[0] if context.pages else context.new_page()
        try:
            _ensure_logged_in(page)
            logger.info("X ブラウザ投稿: 単発ポストを作成します")
            page.goto(COMPOSE_URL, wait_until="domcontentloaded")
            _sleep(1.0, 2.0)
            _dismiss_blocking_dialogs(page)
            _fill_composer(page, body)
            if local_image_path:
                _attach_image(page, Path(local_image_path))
            tweet_id = _submit_composer(page)
            status_url = _open_status(page, tweet_id)
            logger.info("X ブラウザ投稿: 完了 %s", status_url)
            return tweet_id
        except Exception:
            shot = _save_failure_screenshot(page, "x_browser_fail")
            if shot:
                logger.error("画面キャプチャ: %s", shot)
            raise
        finally:
            context.close()


def post_to_x_via_browser(
    parent_text: str,
    reply_text: str,
    *,
    local_image_path: str | None = None,
    headless: bool = True,
    profile_dir: Path | None = None,
) -> tuple[str, str]:
    """
    ブラウザで親ポスト→リンクリプライを投稿する。

    戻り値: (親tweet_id, リプライtweet_id)
    """
    parent = (parent_text or "").strip()
    reply = (reply_text or "").strip()
    if not parent:
        raise ValueError("親ポストが空です。")
    if not reply:
        raise ValueError("リプライが空です。")

    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError(
            "playwright が未インストールです。"
            " pip install playwright && playwright install chromium"
        ) from exc

    profile = Path(profile_dir) if profile_dir else browser_profile_dir()
    profile.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        context = launch_x_context(p, headless=headless, profile_dir=profile)
        page = context.pages[0] if context.pages else context.new_page()
        try:
            _ensure_logged_in(page)
            logger.info("X ブラウザ投稿: 親ポストを作成します")
            page.goto(COMPOSE_URL, wait_until="domcontentloaded")
            _sleep(1.0, 2.0)
            _dismiss_blocking_dialogs(page)
            _fill_composer(page, parent)
            if local_image_path:
                _attach_image(page, Path(local_image_path))
            parent_id = _submit_composer(page)
            parent_url = _open_status(page, parent_id)
            logger.info("X ブラウザ投稿: 親完了 %s", parent_url)

            logger.info("X ブラウザ投稿: リプライを作成します")
            try:
                _open_reply_composer(page, parent_id)
                _dismiss_blocking_dialogs(page)
                _fill_composer(page, reply)
                reply_id = _submit_composer(page)
                logger.info("X ブラウザ投稿: リプライ完了 reply_id=%s", reply_id)
                return parent_id, reply_id
            except Exception as reply_exc:  # noqa: BLE001
                logger.error(
                    "リプライ失敗（親は投稿済み）。手貼り用:\n%s\n%s",
                    parent_url,
                    reply,
                )
                logger.exception("リプライ例外: %s", reply_exc)
                return parent_id, ""
        except Exception:
            shot = _save_failure_screenshot(page, "x_browser_fail")
            if shot:
                logger.error("画面キャプチャ: %s", shot)
            raise
        finally:
            context.close()
