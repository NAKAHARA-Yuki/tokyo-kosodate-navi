-- BigQuery の CREATE PROPERTY GRAPH は元テーブルに PRIMARY KEY (NOT ENFORCED) が必要。
-- 再実行できるよう DROP を先に挟む。
ALTER TABLE `{{PROJECT_ID}}.{{DATASET}}.benefits`
  DROP PRIMARY KEY IF EXISTS;
ALTER TABLE `{{PROJECT_ID}}.{{DATASET}}.benefits`
  ADD PRIMARY KEY (benefit_id) NOT ENFORCED;

ALTER TABLE `{{PROJECT_ID}}.{{DATASET}}.statuses`
  DROP PRIMARY KEY IF EXISTS;
ALTER TABLE `{{PROJECT_ID}}.{{DATASET}}.statuses`
  ADD PRIMARY KEY (status_id) NOT ENFORCED;

ALTER TABLE `{{PROJECT_ID}}.{{DATASET}}.documents`
  DROP PRIMARY KEY IF EXISTS;
ALTER TABLE `{{PROJECT_ID}}.{{DATASET}}.documents`
  ADD PRIMARY KEY (doc_id) NOT ENFORCED;

ALTER TABLE `{{PROJECT_ID}}.{{DATASET}}.schemes`
  DROP PRIMARY KEY IF EXISTS;
ALTER TABLE `{{PROJECT_ID}}.{{DATASET}}.schemes`
  ADD PRIMARY KEY (scheme_id) NOT ENFORCED;

ALTER TABLE `{{PROJECT_ID}}.{{DATASET}}.benefit_requires_status`
  DROP PRIMARY KEY IF EXISTS;
ALTER TABLE `{{PROJECT_ID}}.{{DATASET}}.benefit_requires_status`
  ADD PRIMARY KEY (benefit_id, status_id) NOT ENFORCED;

ALTER TABLE `{{PROJECT_ID}}.{{DATASET}}.benefit_requires_doc`
  DROP PRIMARY KEY IF EXISTS;
ALTER TABLE `{{PROJECT_ID}}.{{DATASET}}.benefit_requires_doc`
  ADD PRIMARY KEY (benefit_id, doc_id) NOT ENFORCED;

ALTER TABLE `{{PROJECT_ID}}.{{DATASET}}.benefit_in_scheme`
  DROP PRIMARY KEY IF EXISTS;
ALTER TABLE `{{PROJECT_ID}}.{{DATASET}}.benefit_in_scheme`
  ADD PRIMARY KEY (benefit_id, scheme_id) NOT ENFORCED;

ALTER TABLE `{{PROJECT_ID}}.{{DATASET}}.benefit_leads_to`
  DROP PRIMARY KEY IF EXISTS;
ALTER TABLE `{{PROJECT_ID}}.{{DATASET}}.benefit_leads_to`
  ADD PRIMARY KEY (from_benefit_id, to_benefit_id, relation) NOT ENFORCED;

CREATE OR REPLACE PROPERTY GRAPH `{{PROJECT_ID}}.{{DATASET}}.kosodate_graph`
  NODE TABLES (
    `{{PROJECT_ID}}.{{DATASET}}.benefits` AS benefits KEY (benefit_id) LABEL Benefit,
    `{{PROJECT_ID}}.{{DATASET}}.statuses` AS statuses KEY (status_id) LABEL Status,
    `{{PROJECT_ID}}.{{DATASET}}.documents` AS documents KEY (doc_id) LABEL Document,
    `{{PROJECT_ID}}.{{DATASET}}.schemes` AS schemes KEY (scheme_id) LABEL Scheme
  )
  EDGE TABLES (
    `{{PROJECT_ID}}.{{DATASET}}.benefit_requires_status`
      SOURCE KEY (benefit_id) REFERENCES benefits (benefit_id)
      DESTINATION KEY (status_id) REFERENCES statuses (status_id)
      LABEL REQUIRES,
    `{{PROJECT_ID}}.{{DATASET}}.benefit_requires_doc`
      SOURCE KEY (benefit_id) REFERENCES benefits (benefit_id)
      DESTINATION KEY (doc_id) REFERENCES documents (doc_id)
      LABEL REQUIRES_DOC,
    `{{PROJECT_ID}}.{{DATASET}}.benefit_in_scheme`
      SOURCE KEY (benefit_id) REFERENCES benefits (benefit_id)
      DESTINATION KEY (scheme_id) REFERENCES schemes (scheme_id)
      LABEL IN_SCHEME,
    -- スキルツリー: 制度から制度への「次の一歩」「ついで申請」
    `{{PROJECT_ID}}.{{DATASET}}.benefit_leads_to`
      SOURCE KEY (from_benefit_id) REFERENCES benefits (benefit_id)
      DESTINATION KEY (to_benefit_id) REFERENCES benefits (benefit_id)
      LABEL LEADS_TO
  );
