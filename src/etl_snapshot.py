"""ロード前に、いま入っているテーブルのスナップショットを撮る。

ETL は `WRITE_TRUNCATE` で8テーブルを全置換する。**取り違えても壊れても、
戻す手段が用意されていなかった**（issue #160）。いま戻せるのは BigQuery の
タイムトラベルだけで、それも 168 時間（7日）で切れる。

元データが壊れた状態で配信された日に ETL を回すと、その内容で全置換される。
品質チェック（`etl_quality`）は件数の急減や NULL 率は見ているが、
**中身のすり替わりまでは見ていない**。気づくのが8日後だと、もう戻せない。

スナップショットは元テーブルとストレージを共有し、**差分にしか課金されない**。
毎回の ETL で撮っても、置き換わった分だけしか増えない。

**同じデータセットの中に `snap_` 接頭辞で置く。** 別データセットに分けるほうが
きれいだが、`bigquery.datasets.create` が要る。手元の `claude-dev` はこれを持って
おらず（403 を実測）、退避の仕組みが「CI でしか試せない」ものになる。
**壊れたときに試せない復旧手順は、無いのと同じ。**

そのぶん `make cleanup`（正規8テーブル以外を消す）が消してしまわないよう、
`scripts/cleanup_dev.py` 側で `snap_` を残すようにしている。
"""

from datetime import UTC, datetime, timedelta

from config import DATASET_ID
from google.cloud import bigquery
from google.cloud.exceptions import NotFound

# スナップショットの保持期間。タイムトラベル（7日）で足りないから撮るので、
# それより十分長くする。差分課金なので、伸ばしても費用はデータの変化量で決まる。
SNAPSHOT_EXPIRATION_DAYS = 30

# この接頭辞が付いたテーブルは退避。`make cleanup` はこれを消さない。
SNAPSHOT_PREFIX = "snap_"


def snapshot_suffix(now: datetime | None = None) -> str:
    """`20260822T081500Z`。**UTC で撮る。** 実行は GitHub Actions（UTC）なので揃える。"""
    now = now or datetime.now(UTC)
    return now.strftime("%Y%m%dT%H%M%SZ")


def snapshot_tables(
    client: bigquery.Client,
    project_id: str,
    table_names,
    now: datetime | None = None,
) -> list[str]:
    """いまあるテーブルのスナップショットを撮り、作った名前を返す。

    **まだ存在しないテーブルは黙って飛ばす**（初回実行やデータセットを作り直した直後）。
    ここで落とすと、初回の ETL が永久に通らなくなる。
    """
    suffix = snapshot_suffix(now)
    expires = (now or datetime.now(UTC)) + timedelta(days=SNAPSHOT_EXPIRATION_DAYS)
    created: list[str] = []
    failed: list[str] = []

    for name in table_names:
        source = f"{project_id}.{DATASET_ID}.{name}"
        try:
            client.get_table(source)
        except NotFound:
            continue
        target = f"{project_id}.{DATASET_ID}.{SNAPSHOT_PREFIX}{name}_{suffix}"
        try:
            client.query(
                f"CREATE SNAPSHOT TABLE `{target}` CLONE `{source}` "
                f"OPTIONS(expiration_timestamp = TIMESTAMP '{expires:%Y-%m-%d %H:%M:%S} UTC')"
            ).result()
            created.append(target)
        except Exception as exc:  # noqa: BLE001
            # **退避に失敗しても ETL は止めない。**
            # 守るための仕組みが、守る対象を止めてしまっては本末転倒。
            # 実際に権限不足（bigquery.tables.deleteSnapshot）で ETL 全体が落ちた。
            # 退避が無いまま上書きされることになるので、黙って続けず必ず出す。
            failed.append(f"{name}: {type(exc).__name__}: {exc}")

    if created:
        print(
            f"[snapshot] {len(created)} 件を退避した（{SNAPSHOT_EXPIRATION_DAYS}日で自動削除）: "
            f"{DATASET_ID}.{SNAPSHOT_PREFIX}*_{suffix}",
            flush=True,
        )
    if failed:
        print(
            f"[snapshot] ⚠ {len(failed)} 件の退避に失敗した。**このまま上書きすると戻せない**",
            flush=True,
        )
        for line in failed:
            print(f"  - {line}", flush=True)
    if not created and not failed:
        print("[snapshot] 退避するテーブルが無い（初回実行）", flush=True)
    return created
