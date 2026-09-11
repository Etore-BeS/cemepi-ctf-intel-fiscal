# Litigância / inadimplência contumaz

Pergunta de pesquisa: com o dump administrativo (Inteligência Fiscal) e o lake judicial (TJSP/FACE), é possível construir evidência e features comportamentais por contribuinte que separem padrões de litigância estratégica de defesa legítima — sem tratar a API como sistema operacional em tempo real.

## Escopo
- Join dump↔lake pela chave de processo (**PEF** / CNJ ↔ FACE `numero` / `num_processo_limpo`)
- Features administrativas (débito, ajuizamento, cobrança) e processuais (duração, desfecho) no subset matched
- Análises exploratórias / de duração no matched set (ex.: Kaplan–Meier), sem score jurídico operacional de “contumaz”

## Layout
```text
notebooks/
  playground/     # toys e exploração
  dump_samples/   # samples do dump
  data_paper/     # dataset overview
  articles/       # artigos em andamento
manuscript/       # data descriptor
docs/             # descrição da frente + galeria de figuras
references/       # aponta ao SoT em docs/references/
```

## Figuras
Ver [docs/figures_gallery.md](docs/figures_gallery.md).

## Descrição detalhada
[docs/A_litigancia_protelatoria.md](docs/A_litigancia_protelatoria.md)
