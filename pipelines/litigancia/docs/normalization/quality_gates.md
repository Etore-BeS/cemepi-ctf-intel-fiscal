# Normalization quality gates

> **Canonical commands:** [PROJECT_SOURCE_OF_TRUTH.md §6](../PROJECT_SOURCE_OF_TRUTH.md) (normalization review).

Objective criteria to close a normalization dictionary version.

## Coverage targets

| Metric | Target | Scope |
|---|---|---|
| Frequency coverage (mapped) | ≥ 95% | Sum of `frequencia` where `normalizado_final ≠ __NAO_MAPEADO__` |
| Round 1 review scope | ≥ 80% | Top-frequency rows reviewed first |
| Inconclusivo share (sentença only) | ≤ 10% | Sum of `frequencia` where `resultado_processo = inconclusivo` after manual pass |

Check programmatically:

```bash
# bash
uv run python scripts/movimentacoes/export_normalization_review.py --check-quality

# fish
uv run python scripts/movimentacoes/export_normalization_review.py --check-quality
```

Stats are also written to `notebooks/playground/output/normalization_review_stats.json`.

## Row-level acceptance

Before marking `status_revisao = aprovado`:

1. `normalizado_final` is non-empty and not `__NAO_MAPEADO__`.
2. For sentença: `resultado_processo` and `credito_recuperado` are set (may be `inconclusivo`).
3. `justificativa` filled when `confianca_regra = baixa` or `status_revisao = duvida`.
4. `revisor` and `data_revisao` populated on approval.

## Reproducibility

- Same `valor_bruto` must always map to the same `normalizado_final` and outcome labels.
- Conflicting mappings for the same raw value are rejected at merge time (last manual export wins; resolve duplicates before approval).

## Sign-off checklist

- [ ] ≥ 95% frequency coverage on movimentação
- [ ] ≥ 95% frequency coverage on sentença
- [ ] Sentença inconclusivo rate ≤ 10% (or documented exception)
- [ ] All `duvida` rows resolved or explicitly descartar
- [ ] Stats JSON archived with dictionary version tag
