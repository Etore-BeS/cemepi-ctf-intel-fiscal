# Sentença outcome taxonomy

> **Implementation:** rule logic in `src/utils/normalization_review.py`; export via `scripts/movimentacoes/export_normalization_review.py` (see [PROJECT_SOURCE_OF_TRUTH.md §6](../PROJECT_SOURCE_OF_TRUTH.md)).

Classification rules for manual review of `tipo_sentença`. Perspective: **FESP as creditor** in execução fiscal.

Two **independent** dimensions:

| Dimension | Values | Meaning |
|---|---|---|
| `resultado_processo` | `favoravel`, `desfavoravel`, `inconclusivo` | Legal outcome for the creditor |
| `credito_recuperado` | `sim`, `nao`, `inconclusivo` | Economic recovery of the tax credit |

When either dimension is ambiguous, use `inconclusivo` — never force a binary label.

## Automatic proposals

`scripts/movimentacoes/export_normalization_review.py` applies ordered regex rules (first match wins) in `src/utils/normalization_review.py`. Proposals populate `resultado_processo`, `credito_recuperado`, `confianca_regra`, and `justificativa`. The reviewer may override any editable field.

## Rule summary

| Pattern (substring) | Resultado | Crédito | Confiança |
|---|---|---|---|
| Satisfação da obrigação / Pagamento integral do débito | favorável | sim | alta |
| Prescrição intercorrente / Decadência ou prescrição | desfavorável | não | alta |
| Cancelamento da dívida ativa / Renúncia ao crédito | desfavorável | não | alta |
| Desistência / Renúncia (sem mérito) | desfavorável | não | média |
| Devedor não encontrado / Sem bens penhoráveis | desfavorável | não | média |
| Embargos/impugnação procedentes | desfavorável | não | alta |
| Embargos/impugnação improcedentes | favorável | inconclusivo | média |
| Homologação de transação/acordo | inconclusivo | inconclusivo | baixa |
| Procedente em parte / Parcialmente procedente | inconclusivo | inconclusivo | média |
| Improcedente a ação/pedido | favorável | inconclusivo | média |
| Procedente a ação/pedido | desfavorável | inconclusivo | baixa |
| No match | inconclusivo | inconclusivo | baixa |

## Scenarios to stress-test manual review

1. **Extinção por satisfação** — both dimensions should be favorável + sim (high confidence).
2. **Prescrição intercorrente** — desfavorável + não; legal loss without payment.
3. **Homologação de acordo** — inconclusivo on both; reviewer checks whether terms imply partial payment.
4. **Embargos improcedentes** — favorável legally, but crédito remains inconclusivo until satisfaction event.
5. **Procedente em parte** — always inconclusivo; requires reading the dispositivo.

## After review

Approved rows (`status_revisao = aprovado`) with filled `normalizado_final` become the canonical mapping for downstream analysis joins.
