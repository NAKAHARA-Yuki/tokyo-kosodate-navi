"""E2E テストの共通フィクスチャ。

画面は frontend/（Next.js）が持ち、backend（FastAPI）は API だけを返す（issue #33 / ADR 0013）。
そのため既定では **2つのプロセスを起動する**:

1. backend スタブ（`e2e/server.py`。BigQuery と Gemini を差し替え済み。GCP 認証不要）
2. frontend（Next.js の本番ビルドを `npm start` で起動し、1 を `BACKEND_URL` に指定）

`E2E_BASE_URL` を指定すると、その URL（デプロイ済みの **frontend**）に対して実行する
（staging / prod へのスモークテスト用）。
"""

import os
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
FRONTEND_DIR = ROOT / "frontend"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_until_ready(url: str, path: str = "/api/healthz", timeout: float = 90.0) -> None:
    deadline = time.time() + timeout
    last_error: Exception | None = None
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"{url}{path}", timeout=2) as res:
                if res.status == 200:
                    return
        except Exception as exc:  # noqa: BLE001  起動待ちの間は例外が出て当然
            last_error = exc
        time.sleep(0.3)
    raise RuntimeError(f"{url}{path} が {timeout}s 以内に応答しませんでした: {last_error}")


def _terminate(proc: subprocess.Popen) -> None:
    """**プロセスグループごと落とす。**

    `npm start` は自分では待ち受けず、子として `next-server` を起動する。
    `proc.terminate()` は npm にしか届かないので、**next-server が生き残って
    ポートとメモリを掴んだまま init に引き取られる**（`ppid=1` の孤児になる）。

    実際にこれが溜まっていた。E2E を回すたびに1つ増え、15日で 47個・約2.5GB を占有して
    swap を食い潰し、`npm run build` が終わらなくなっていた（issue #64 の変異検証中に判明）。
    テストは緑のままなので、遅くなる以外に兆候が出ない。
    """
    if proc.poll() is not None:
        return
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            proc.kill()
        proc.wait(timeout=10)


@pytest.fixture(scope="session")
def base_url() -> str:
    """テスト対象（frontend）の URL。外部指定がなければ backend スタブごと起動する。"""
    external = os.environ.get("E2E_BASE_URL")
    if external:
        external = external.rstrip("/")
        # frontend 側は /api/... をプロキシするので healthz はそのまま使える
        _wait_until_ready(external)
        yield external
        return

    if shutil.which("npm") is None:
        pytest.skip("npm が無いため frontend を起動できません（Node.js が必要）")

    backend_port = _free_port()
    backend = subprocess.Popen(
        [sys.executable, str(ROOT / "e2e" / "server.py"), str(backend_port)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        cwd=str(ROOT),
        # 自分をリーダーにした新しいプロセスグループで起動する。
        # こうしないと `_terminate` の killpg が pytest 自身を巻き込む。
        start_new_session=True,
    )
    backend_url = f"http://127.0.0.1:{backend_port}"

    frontend = None
    try:
        _wait_until_ready(backend_url)

        # 本番ビルドで動かす（dev サーバーは初回アクセス時にコンパイルが走り、
        # page.goto のタイムアウトと区別が付かない待ち時間が出るため）。
        subprocess.run(
            ["npm", "run", "build"],
            cwd=str(FRONTEND_DIR),
            check=True,
            env={**os.environ, "NEXT_TELEMETRY_DISABLED": "1"},
        )

        frontend_port = _free_port()
        frontend = subprocess.Popen(
            ["npm", "start", "--", "--port", str(frontend_port)],
            cwd=str(FRONTEND_DIR),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env={
                **os.environ,
                # スタブ backend は認証を要求しないので ID トークンは付けない
                "BACKEND_URL": backend_url,
                "BACKEND_REQUIRES_AUTH": "false",
                "NEXT_TELEMETRY_DISABLED": "1",
            },
            # npm は next-server を子として起動する。グループごと落とすために要る。
            start_new_session=True,
        )
        frontend_url = f"http://127.0.0.1:{frontend_port}"
        _wait_until_ready(frontend_url)
        yield frontend_url
    finally:
        if frontend is not None:
            _terminate(frontend)
        _terminate(backend)


@pytest.fixture
def app_page(page, base_url):
    """アプリを開き、初期表示が終わるまで待った page を返す。

    既存画面（cytoscape.js）は frontend の /debug が配信する（issue #33）。
    """
    page.set_default_timeout(15_000)
    page.goto(f"{base_url}/debug")
    # 検索結果が描画されたら初期化完了とみなす
    page.wait_for_selector(".result-item")
    return page


@pytest.fixture(scope="session")
def browser_context_args(browser_context_args):
    # 実機に近い縦横比で確認する
    return {**browser_context_args, "viewport": {"width": 1280, "height": 900}}
