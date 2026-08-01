"""BigQuery のテーブルスキーマ定義。

自動検出だと DATE が STRING に、空配列の STRUCT 列が推論できずに落ちるため、
型が曖昧な列だけ明示スキーマを与える（benefits）。それ以外のノード/エッジテーブルは
列数が少なく固定的なので、そのまま明示的に定義している。
"""

import pandas as pd
from google.cloud import bigquery


def _link_field(name: str) -> bigquery.SchemaField:
    return bigquery.SchemaField(
        name,
        "RECORD",
        mode="REPEATED",
        fields=[bigquery.SchemaField("title", "STRING"), bigquery.SchemaField("uri", "STRING")],
    )


BENEFITS_EXPLICIT_FIELDS = [
    bigquery.SchemaField("min_age_months", "INT64"),
    bigquery.SchemaField("max_age_months", "INT64"),
    bigquery.SchemaField("inferred_min_age_months", "INT64"),
    bigquery.SchemaField("inferred_max_age_months", "INT64"),
    bigquery.SchemaField("effective_min_age_months", "INT64"),
    bigquery.SchemaField("effective_max_age_months", "INT64"),
    bigquery.SchemaField("institution_type", "INT64"),
    bigquery.SchemaField("class_code", "INT64"),
    bigquery.SchemaField("update_date", "DATE"),
    bigquery.SchemaField("od_update_date", "DATE"),
    bigquery.SchemaField("implementation_period_from_date", "DATE"),
    bigquery.SchemaField("implementation_period_to_date", "DATE"),
    bigquery.SchemaField("procedure_period_from_date", "DATE"),
    bigquery.SchemaField("procedure_period_to_date", "DATE"),
    _link_field("related_links"),
    _link_field("form_links"),
    _link_field("embedded_links"),
]


def build_benefits_schema(df: pd.DataFrame):
    """DataFrame の列順に合わせて、明示型の列はそれを、他は STRING/BOOL を割り当てる。"""
    explicit = {f.name: f for f in BENEFITS_EXPLICIT_FIELDS}
    bool_columns = {
        "has_free_text_conditions",
        "is_free",
        "electronic_submission",
        "use_consideration_flag",
        "is_prenatal",
    }
    array_string_columns = {
        "category_codes",
        "target_codes",
        "content_codes",
        "category_code_labels",
        "target_code_labels",
        "content_code_labels",
    }

    schema = []
    for column in df.columns:
        if column in explicit:
            schema.append(explicit[column])
        elif column in bool_columns:
            schema.append(bigquery.SchemaField(column, "BOOL"))
        elif column in array_string_columns:
            schema.append(bigquery.SchemaField(column, "STRING", mode="REPEATED"))
        else:
            schema.append(bigquery.SchemaField(column, "STRING"))
    return schema


TABLE_SCHEMAS = {
    "statuses": [
        bigquery.SchemaField("status_id", "STRING"),
        bigquery.SchemaField("name", "STRING"),
        bigquery.SchemaField("type", "STRING"),
        bigquery.SchemaField("min_age_months", "INT64"),
        bigquery.SchemaField("max_age_months", "INT64"),
        bigquery.SchemaField("code", "STRING"),
    ],
    "documents": [
        bigquery.SchemaField("doc_id", "STRING"),
        bigquery.SchemaField("doc_name", "STRING"),
        bigquery.SchemaField("original_name", "STRING"),
        bigquery.SchemaField("is_probable_document", "BOOL"),
        bigquery.SchemaField("doc_url", "STRING"),
    ],
    "schemes": [
        bigquery.SchemaField("scheme_id", "STRING"),
        bigquery.SchemaField("scheme_name", "STRING"),
        bigquery.SchemaField("municipality_count", "INT64"),
        bigquery.SchemaField("benefit_count", "INT64"),
        bigquery.SchemaField("min_age_months", "INT64"),
        bigquery.SchemaField("max_age_months", "INT64"),
    ],
    "benefit_in_scheme": [
        bigquery.SchemaField("benefit_id", "STRING"),
        bigquery.SchemaField("scheme_id", "STRING"),
    ],
    "benefit_leads_to": [
        bigquery.SchemaField("from_benefit_id", "STRING"),
        bigquery.SchemaField("to_benefit_id", "STRING"),
        bigquery.SchemaField("relation", "STRING"),
        bigquery.SchemaField("reason", "STRING"),
    ],
    "benefit_requires_status": [
        bigquery.SchemaField("benefit_id", "STRING"),
        bigquery.SchemaField("status_id", "STRING"),
    ],
    "benefit_requires_doc": [
        bigquery.SchemaField("benefit_id", "STRING"),
        bigquery.SchemaField("doc_id", "STRING"),
    ],
}
