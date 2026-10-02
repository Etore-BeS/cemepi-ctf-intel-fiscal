# EDA completo

Abra `notebooks/playground/EDA_completo.ipynb` na raiz do monorepo, com o HD montado.

Bash: `export DATA_ROOT="/Volumes/Meedi_Etore_HD1/CEMEPI/api_ctf/"`

Fish: `set -gx DATA_ROOT "/Volumes/Meedi_Etore_HD1/CEMEPI/api_ctf/"`

Sem `DATA_ROOT`, o notebook usa esse caminho. Se o volume não estiver montado, as seções imprimem `fonte não montada` e seguem sem preencher contagens.
