"""E2E テストの共通フィクスチャ。

既定ではスタブ版アプリをローカルに起動して検証する（GCP 不要・CI で常時実行）。
`E2E_BASE_URL` を指定すると、その URL に対して実行する
（デプロイ済みの staging / prod へのスモークテスト用）。
"""

import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_until_ready(url: str, timeout: float = 40.0) -> None:
    deadline = time.time() + timeout
    last_error: Exception | None = None
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"{url}/healthz", timeout=2) as res:
                if res.status == 200:
                    return
        except Exception as exc:  # noqa: BLE001  起動待ちの間は例外が出て当然
            last_error = exc
        time.sleep(0.3)
    raise RuntimeError(f"アプリが {timeout}s 以内に起動しませんでした: {last_error}")


@pytest.fixture(scope="session")
def base_url() -> str:
    """テスト対象の URL。外部指定がなければスタブ版アプリを起動する。"""
    external = os.environ.get("E2E_BASE_URL")
    if external:
        external = external.rstrip("/")
        _wait_until_ready(external)
        yield external
        return

    port = _free_port()
    proc = subprocess.Popen(
        [sys.executable, str(ROOT / "e2e" / "server.py"), str(port)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        cwd=str(ROOT),
    )
    url = f"http://127.0.0.1:{port}"
    try:
        _wait_until_ready(url)
        yield url
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


@pytest.fixture
def app_page(page, base_url):
    """アプリを開き、初期表示が終わるまで待った page を返す。"""
    page.set_default_timeout(15_000)
    page.goto(base_url)
    # 検索結果が描画されたら初期化完了とみなす
    page.wait_for_selector(".result-item")
    return page


@pytest.fixture(scope="session")
def browser_context_args(browser_context_args):
    # 実機に近い縦横比で確認する
    return {**browser_context_args, "viewport": {"width": 1280, "height": 900}}
