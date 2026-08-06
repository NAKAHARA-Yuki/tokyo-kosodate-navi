"""やさしい解説（Gemini 生成）の結果を保存して再利用する（issue #68 / docs/adr/0015）。

Gemini の呼び出しは実測で9〜13秒かかる。制度は 7,812 件あって内容はそう頻繁に変わらないので、
同じ制度に対して毎回作り直すのは時間と費用の無駄になる。

Cloud Run は `min-instances 0` でインスタンスが使い捨てのため、プロセス内に持っても意味がない。
インスタンスをまたいで共有できる場所として BigQuery のテーブルを使っている
（既に使っているサービスなので IAM もローカル開発手順も増えない。選定理由は ADR 0015）。

**キャッシュするのは `mode=explain` だけ。** `mode=review` の入力は利用者が書いた下書きで、
氏名や世帯の事情といった個人情報が入りうるため保存しない。

**この層は判定には一切関わらない**（ADR 0001）。扱うのは Gemini の言い換え結果だけ。
"""

import hashlib
from datetime import UTC, datetime

import dependencies
from config import DATASET_ID, PROJECT_ID
from google.cloud import bigquery

TABLE_NAME = "benefit_explanations"

# プロンプトを変えたら手で上げる。上げると全件が作り直しになる。
# 「内容は同じだが作り直したい」ときの唯一のレバーでもある（有効期限は設けていない。ADR 0015）。
PROMPT_VERSION = 1

_SCHEMA = [
    bigquery.SchemaField("cache_key", "STRING", mode="REQUIRED", description="下の4要素のハッシュ"),
    bigquery.SchemaField("benefit_id", "STRING", mode="REQUIRED"),
    bigquery.SchemaField(
        "facts_hash", "STRING", mode="REQUIRED", description="Geminiに渡した事実文字列のハッシュ"
    ),
    bigquery.SchemaField("model", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("prompt_version", "INT64", mode="REQUIRED"),
    bigquery.SchemaField("result", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("generated_at", "TIMESTAMP", mode="REQUIRED"),
]

# テーブルの存在確認はインスタンスごとに1回でよい（毎回だと書き込みのたびに往復が増える）。
_table_ready = False


def table_id() -> str:
    return f"{PROJECT_ID}.{DATASET_ID}.{TABLE_NAME}"


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def facts_hash(facts: str) -> str:
    """Gemini に渡した事実文字列そのもののハッシュ。

    `benefits.update_date` を無効化の判定に使ってはいけない。あれは自治体側の更新日で、
    こちらが Gemini に渡した内容が変わったかどうかとは一致しない
    （更新日が動いても渡す内容は同じ、逆に更新日が動かなくても列が変わることがある）。
    渡した内容そのものを見れば、取りこぼしも無駄な作り直しも起きない。
    """
    return _sha256(facts)


def cache_key(benefit_id: str, facts: str, model: str, prompt_version: int = PROMPT_VERSION) -> str:
    """出力を決める要素をすべて含める。どれか1つでも変われば別キーになり、自動で作り直される。"""
    return _sha256(f"{benefit_id}\n{facts_hash(facts)}\n{model}\n{prompt_version}")


def lookup(key: str) -> dict | None:
    """キャッシュを引く。無ければ None。

    **失敗しても例外を投げない。** キャッシュは高速化のための仕組みであって、
    ここが落ちたせいで解説そのものが出せなくなるのは本末転倒なので、
    引けなければ「無かった」として通常の生成に進む（テーブル未作成の初回もここを通る）。
    """
    query = f"""
        SELECT result, generated_at
        FROM `{table_id()}`
        WHERE cache_key = @cache_key
        ORDER BY generated_at DESC
        LIMIT 1
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ScalarQueryParameter("cache_key", "STRING", key)]
    )
    try:
        rows = list(dependencies.get_client().query(query, job_config=job_config).result())
    except Exception:  # noqa: BLE001
        return None
    if not rows:
        return None
    generated_at = rows[0]["generated_at"]
    return {
        "result": rows[0]["result"],
        "generated_at": generated_at.isoformat() if hasattr(generated_at, "isoformat") else generated_at,
    }


def _ensure_table(client) -> None:
    global _table_ready
    if _table_ready:
        return
    table = bigquery.Table(table_id(), schema=_SCHEMA)
    # 点参照しかしないのでキーでクラスタリングする（スキャン量が減る）。
    table.clustering_fields = ["cache_key"]
    client.create_table(table, exists_ok=True)
    _table_ready = True


def store(
    key: str, benefit_id: str, facts: str, model: str, result: str, generated_at: datetime | None = None
) -> str:
    """生成結果を保存し、保存した生成日時を ISO8601 で返す。

    **失敗しても例外を投げない**（lookup と同じ理由）。保存できなければ次回また生成するだけで、
    利用者に見せる内容は変わらない。バックエンドの実行 SA から BigQuery に書けない構成に
    なったとしても、機能そのものは動き続ける。
    """
    generated_at = generated_at or datetime.now(UTC)
    row = {
        "cache_key": key,
        "benefit_id": benefit_id,
        "facts_hash": facts_hash(facts),
        "model": model,
        "prompt_version": PROMPT_VERSION,
        "result": result,
        "generated_at": generated_at.isoformat(),
    }
    try:
        client = dependencies.get_client()
        _ensure_table(client)
        client.insert_rows_json(table_id(), [row])
    except Exception:  # noqa: BLE001
        pass
    return generated_at.isoformat()
