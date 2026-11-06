# Declared and assessed VAT debt by CNAE 2.3

This note is a lab analysis. It is not a page of the published site.

Declared means `ICMS Declarado`. The debt was declared and not paid.
Assessed means `ICMS Autuação` plus `ICMS Autuação Simples Nacional`. The debt comes from a tax assessment.
Fecoep and the GIA amendment stay out of both segments.

## Population

The population is the lab ICMS extract for December 2025. Every row is a legal entity.
The age reference date is 31 December 2025.
Payments are GARE amounts dated before 1 January 2026.
The 2026 payment partition is not read. No payment in the 2016–2025 files has a 2026 date.

Firm attributes come from the Receita Federal CNPJ registry already used in the lab.
The registry is later than the debt outcomes. Status and activity describe the firm. They are not causes.

CNAE parents and names come from the official IBGE file CNAE-Subclasses 2.3
(`reference/CNAE_Subclasses_2_3_Estrutura_Detalhada.xlsx`, SHA-256 `96853a62e65a…dc06ac`).
The file has 1,331 subclasses.
Section names on the charts are short English labels.
Division, group, class and subclass names stay in the official Portuguese. IBGE does not publish an English list.

## Checks against the published lab

The published page reports 2,325,280 legal-entity VAT CDAs and R$ 427.880 billion in December 2025.
This extract has 2,325,277 CDAs and R$ 427.880 billion.
The gap is 3 CDAs and about R$ 68,000.
`ICMS Autuação` is short by 1 CDA. The GIA amendment matches exactly.

Assessed in this run is 59,542 CDAs and R$ 311.40 billion.
Declared is 2,257,189 CDAs and R$ 116.42 billion.
Simples Nacional is 818 CDAs and R$ 0.145 billion. It is inside Assessed and is 0.05% of assessed stock.

One placeholder code, `8888888`, is not in CNAE 2.3. It covers 808 CDAs. Every other debtor subclass is in the official file.

## Measures

A firm is the 8-digit CNPJ root.
Payment rate is the share of CDAs with any GARE payment before 2026.
Recovery is those payments divided by the December 2025 stock.
Debt age is years since enrollment. Dates before 1960 or after 31 December 2025 are missing.
Firm age uses the CNPJ opening date. The same window applies.
The top 1% is the largest `ceil(1% × N)` CDAs with a positive value.
Gini uses the same positive values.

Mann–Whitney compares Assessed with Declared. The rank-biserial is positive when Assessed tends to be larger.
Chi-square uses firm counts. Cramér's V is the effect size.
For section, status, size, UF and legal nature, each firm is placed in the segment that holds more of its stock.
11,928 firms have debt in both segments. The repeated-enrollment table keeps a firm in each segment where it has a CDA.
CDA-level tests describe the portfolio. CDAs of one firm are not independent. Use the firm-level effect size for that claim.
Samples are large, so every p-value is 0 at floating-point precision. Read the effect size.

## Main differences

| Contrast | Declared | Assessed | Effect size |
|---|---:|---:|---|
| CDAs | 2,257,189 | 59,542 | — |
| Stock | R$ 116.42 billion | R$ 311.40 billion | — |
| Median CDA value | R$ 7,334 | R$ 578,084 | rank-biserial 0.89 |
| Median firm stock | R$ 7,916 | R$ 770,756 | rank-biserial 0.74 |
| CDAs with any payment | 9.1% | 17.4% | — |
| Payments / December stock | 4.73% | 0.48% | — |
| Median debt age (CDA) | 4.7 years | 9.3 years | rank-biserial 0.39 |
| Median debt age (firm, stock-weighted) | 2.1 years | 8.9 years | rank-biserial 0.55 |
| Stock older than 10 years | 39.5% | 43.7% | — |
| Median firm age | 13.4 years | 24.1 years | rank-biserial 0.45 |
| Stock in firms aged 20 years or more | 66.3% | 76.0% | — |
| Active firms, share of stock | 74.4% | 52.9% | Cramér's V 0.25 for status |
| Closed (baixada), share of stock | 9.7% | 24.1% | same test |
| Unfit (inapta), share of stock | 15.2% | 21.7% | same test |
| Trade, share of segment stock | 27.6% | 45.4% | Cramér's V 0.10 for section |
| Manufacturing, share of segment stock | 50.8% | 34.5% | same test |
| Transport, share of segment stock | 12.5% | 4.6% | same test |
| Information, share of segment stock | 0.8% | 6.0% | same test |
| Small firms, share of stock | 15.3% | 7.6% | Cramér's V 0.18 for size |
| Stock in São Paulo state | 88.0% | 95.7% | Cramér's V 0.15 for UF |
| Firms with two or more CDAs | 119,833 | 9,565 | Cramér's V 0.26 |
| Share of segment stock in those firms | 99.2% | 82.3% | same test |

Wholesale trade (division 46) is 32.0% of assessed stock and 14.4% of declared stock.
Telecommunications (division 61) is 5.5% of assessed stock and 0.5% of declared stock.
Coke and petroleum products (division 19) is 9.8% of declared stock and 1.4% of assessed stock.
Land transport (division 49) is 11.7% of declared stock and 3.3% of assessed stock.
Inside trade and manufacturing, the largest 1% of CDAs still hold about 35% to 42% of that section's stock. The Gini inside those sections is about 0.82 to 0.87.

São Paulo city holds 27.2% of assessed stock and 22.5% of declared stock.
Rio de Janeiro city holds 7.8% of declared stock.
Paulínia holds 4.9% of assessed stock in 215 firms.

Legal form is similar in the stock. A limited company (`206-2`) holds 77.0% of assessed stock and 77.8% of declared stock. Cramér's V for legal nature is 0.12.

## Why the top 1% cut holds about 75% of the stock

The 75% figure is the pooled book. It is not the figure inside one segment.

Positive values only. Declared and Assessed together:

| Cut, from the largest CDAs | CDAs | Share of CDAs | Share of stock |
|---|---:|---:|---:|
| Largest 10% | 231,369 | 10% | 91.9% |
| Largest 5% | 115,685 | 5% | 87.5% |
| Largest 1% | 23,137 | 1.00% | 75.8% |
| Largest 0.5% | 11,569 | 0.5% | 69.1% |
| Largest 0.1% | 2,314 | 0.1% | 48.5% |

The pooled Gini is 0.946.
The Lorenz curve is `figures/lorenz_by_segment.png`.
The largest 50% of CDAs already hold 99.4% of the stock. The curve stays near zero and then rises at the right edge.
That shape is why a 1% cut is a real break in this book.

Inside the largest 1% there are 18,230 assessed CDAs and 4,907 declared CDAs.
Those assessed CDAs hold 68.8% of the pooled stock.
Those declared CDAs hold 7.1%.
Together they are the 75.8%.

Inside Declared alone, the largest 1% (22,544 CDAs) hold 39.5% of declared stock. The Gini is 0.839.
Inside Assessed alone, the largest 1% (593 CDAs) hold 39.9% of assessed stock. The Gini is 0.863.
The pooled 75% appears because assessed values are much larger and assessed is 72.8% of the two-segment stock in 2.6% of the CDAs.

## Repeated enrolments

A firm with two or more CDAs in a segment is the repeated case.

Assessed has 9,565 such firms. They are 31.9% of assessed firms and hold 82.3% of assessed stock.
Declared has 119,833 such firms. They are 67.4% of declared firms and hold 99.2% of declared stock.
Across both segments, 125,305 firms have two or more CDAs.
11,928 firms have at least one CDA in each segment.

## What this does not show

The registry date is after the debt. A closed or unfit firm may have reached that status because of the debt.
Recovery can be small for Assessed because the stock is large, even when more CDAs have some payment.
The firm-level chi-square puts a dual-segment firm in one segment. The overlap is reported separately.
Cells with 1 to 4 firms are blank in the saved tables.

## Files

Run this only with the external disk mounted.

Fish:

```fish
cd ~/Code/cemepi-lab
uv run --no-sync python lab/eda/2026-10-31_cnae_declared_assessed/analyze.py
```

Bash:

```bash
cd ~/Code/cemepi-lab
uv run --no-sync python lab/eda/2026-10-31_cnae_declared_assessed/analyze.py
```

- `analyze.py` builds the tables and the figures.
- `tables/` holds the CNAE levels, tests, age bands, status, size, UF, municipalities and `key_numbers.json`.
- `figures/` holds the six PNG charts.
