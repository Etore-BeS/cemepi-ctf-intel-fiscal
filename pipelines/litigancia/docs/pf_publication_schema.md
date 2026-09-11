# PF publication schema (paper outputs)

This document defines how **natural persons (PF)** and **CNPJ entities (PJ)** appear in
published artifacts. Raw decision text and direct identifiers are never published.

## Principles

1. **Raw `decisao` stays internal** — only derived `decisao_anonimizada` may appear in exports.
2. **PF are never named** — use stable pseudonymous IDs (`pf_id`).
3. **CNPJ extraction is rule-based** — syntax patterns + rejection gates (`cda_number`, `natural_person`).
4. **Reproducibility** — pseudonymization uses a salted HMAC documented below.

## PF fields (paper-ready)

| Column | Type | Description |
|--------|------|-------------|
| `pf_id` | string | Stable pseudonym, e.g. `pf_a1b2c3d4e5f67890` (16 hex chars) |
| `pf_role` | enum | `autor`, `reu`, `both`, or null if unknown |
| `cd_processo` | string | Internal process key (already non-PII in lake) |
| `id_processo` | string | CNJ number (public metadata) |

**Never publish:** `canonical_name`, raw FACE party strings for PF, CPF, RG, OAB, e-mail, phone.

### Pseudonymization algorithm

Implementation: [`src/utils/pf_pseudonym.py`](../src/utils/pf_pseudonym.py)

```
canonical_key = normalize_name(pf_name) [+ "|" + normalize_name(comarca) if scoped]
pf_id = "pf_" + HMAC_SHA256(PF_HASH_SALT, canonical_key)[:16]
```

- **`PF_HASH_SALT`**: environment secret (not committed). Use a fixed salt per paper version and document the version in methods.
- **Collision handling**: if two distinct canonical keys map to the same prefix, append `_2`, `_3`, … deterministically by sorted key order within the export batch.

## CNPJ fields (paper-ready)

| Column | Type | Description |
|--------|------|-------------|
| `cnpj` | string | Normalized 14-digit ID with check digits; null for PF/CDA rejections |
| `cnpj_syntax_pattern` | string | Pattern ID from extraction rules |
| `cnpj_face_pole` | string | `autores` or `reus` |
| `cnpj_confidence` | string | `high`, `medium`, `low`, or `null` (rejected) |
| `cnpj_rejected_reason` | string | `cda_number`, `natural_person`, or null |

## Anonymized text (paper-ready)

| Column | Type | Description |
|--------|------|-------------|
| `decisao_anonimizada` | string | Redacted decision text |
| `redaction_counts` | map | Counts by label (`cpf`, `pf_name`, …) |

Placeholders: `[CPF]`, `[RG]`, `[OAB]`, `[EMAIL]`, `[PHONE]`, `[PESSOA]`.

## Example export row (process level)

```json
{
  "cd_processo": "2I000SEKW0000",
  "id_processo": "0507366-72.2013.8.26.0068",
  "pf_parties": [
    {"pf_id": "pf_8c2f1a0b3d4e5f60", "pf_role": "reu"}
  ],
  "cnpj": "68.347.343/0005-94",
  "cnpj_syntax_pattern": "debtor_header_block",
  "cnpj_face_pole": "reus",
  "cnpj_confidence": "high",
  "decisao_anonimizada": "Exequente: [PESSOA] Executado: [PESSOA] CNPJ: 68.347.343/0005-94 ..."
}
```

## Quality gates before publication

Run [`scripts/maintenance/explore_quality.py`](../scripts/maintenance/explore_quality.py) and confirm promotion gates in
[`notebooks/playground/output/quality_exploration_report.md`](../notebooks/playground/output/quality_exploration_report.md).

Minimum thresholds (heuristic proxy; confirm with manual labels):

| Gate | Threshold |
|------|-----------|
| `pf_name` precision | ≥ 0.95 |
| regex (`cpf`, `email`, `phone`) precision | ≥ 0.98 |
| `cda_number` rejection precision | ≥ 0.99 |
| `natural_person` rejection precision | ≥ 0.95 |
| critical PF leakage (PJ mis-redacted) | 0 |

## Pipeline placement (future silver)

```
processos_delta.decisao (raw, internal)
  → pf_redaction.anonymize_decision → decisao_anonimizada
  → cnpj_extraction.extract_mentions → cnpj columns
  → pf_pseudonym.pseudonymize_face_parties → pf_id list
  → paper_export (public)
```
