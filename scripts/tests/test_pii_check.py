"""Testes do scripts/pii_check.py. Todos os CPFs/CNPJs são sintéticos, gerados aqui."""
import hashlib
import json

import pytest

import pii_check as pc


# ----------------------------------------------------------------- geradores sintéticos
def make_cpf(base9: str) -> str:
    nums = [int(c) for c in base9]
    for n in (9, 10):
        s = sum(nums[i] * (n + 1 - i) for i in range(n))
        nums.append((s * 10) % 11 % 10)
    return "".join(map(str, nums))


def make_cnpj(base12: str) -> str:
    nums = [int(c) for c in base12]
    p1 = [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
    for pesos in (p1, [6] + p1):
        r = sum(n * p for n, p in zip(nums, pesos)) % 11
        nums.append(0 if r < 2 else 11 - r)
    return "".join(map(str, nums))


def fmt_cpf(d):
    return f"{d[:3]}.{d[3:6]}.{d[6:9]}-{d[9:]}"


def fmt_cnpj(d):
    return f"{d[:2]}.{d[2:5]}.{d[5:8]}/{d[8:12]}-{d[12:]}"


def wrong_dv(d):
    return d[:-1] + str((int(d[-1]) + 1) % 10)


CPF = make_cpf("529982247")
CNPJ = make_cnpj("112223330001")


@pytest.fixture
def cfg():
    return pc.load_config(None)


def rules(text, cfg, path="lab/x.qmd"):
    return [f.rule for f in pc.scan_text(text, path, cfg)]


# ----------------------------------------------------------------- dígito verificador
def test_dv_cpf_e_cnpj():
    assert pc.cpf_valido(CPF)
    assert not pc.cpf_valido(wrong_dv(CPF))
    assert not pc.cpf_valido("11111111111")
    assert pc.cnpj_valido(CNPJ)
    assert not pc.cnpj_valido(wrong_dv(CNPJ))
    assert not pc.cnpj_valido("00000000000000")


# ----------------------------------------------------------------- CPF / CNPJ
@pytest.mark.parametrize("texto", [fmt_cpf(CPF), CPF, f"CPF: {CPF}.", f'"{CPF}"', f"<td>{fmt_cpf(CPF)}</td>"])
def test_cpf_detectado(texto, cfg):
    assert rules(texto, cfg) == ["cpf"]


@pytest.mark.parametrize("texto", [fmt_cnpj(CNPJ), CNPJ, f"cnpj={CNPJ}", f"<td>{fmt_cnpj(CNPJ)}</td>"])
def test_cnpj_detectado(texto, cfg):
    assert rules(texto, cfg) == ["cnpj"]


@pytest.mark.parametrize("texto", [
    wrong_dv(CPF), fmt_cpf(wrong_dv(CPF)), wrong_dv(CNPJ), fmt_cnpj(wrong_dv(CNPJ)),
    f"0.{CPF}",                 # parte decimal de float
    f"{CPF}.5",                 # float
    f"1.{CNPJ}e-05",            # notação científica
    f"bdata: 'AbC{CPF}xYz'",    # base64 de gráfico plotly
    f"a{CNPJ}f",                # hash hexadecimal
    f"+55{CPF}",                # telefone com DDI
    "1234567890123456",         # 16 dígitos (não é CPF nem CNPJ)
])
def test_nao_e_cpf_nem_cnpj(texto, cfg):
    assert rules(texto, cfg) == []


# ----------------------------------------------------------------- e-mails
def test_email_detectado_e_mascarado(cfg):
    fs = pc.scan_text("contato: fulano.silva@usp.br", "lab/a.md", cfg)
    assert [f.rule for f in fs] == ["email"]
    assert "fulano" not in str(fs[0]) and fs[0].masked.startswith("f***@usp.br")


def test_email_dominio_permitido():
    cfg = pc.load_config(None)
    cfg.allowed_email_domains = {"example.com"}
    assert rules("x@example.com e y@sub.example.com", cfg) == []
    assert rules("z@exemplo.com", cfg) == ["email"]


# ----------------------------------------------------------------- colunas proibidas
@pytest.mark.parametrize("texto", [
    '{"NOME_DEVEDOR": "MARIA DA SILVA"}',
    "nome_socio: Joao Pereira",
    "WHERE NOME_DEVEDOR = 'FULANO'",
    "df.NOME_DEVEDOR == 'X'",
    '"DEBITO.CPF_DEVEDOR": "abc"',
    '<th>NOME_DEVEDOR</th>',
    '<th class="col">cpf_cnpj_socio</th>',
    "CNPJ_ROOT,NOME_DEVEDOR,VALOR\nab12,FULANO,10\n",
    "| cnpj | nome_socio |\n|---|---|\n| x | FULANO |\n",
    "│ NOME_DEVEDOR ┆ VALOR │\n│ FULANO       ┆ 10    │\n",
    "   NOME_DEVEDOR   VALOR\n0  FULANO    10\n",
    '"    \\"NOME_DEVEDOR\\": \\"MARIA DA SILVA\\",\\n",',   # dentro de .ipynb (JSON escapado)
])
def test_coluna_proibida_com_valor(texto, cfg):
    assert "coluna_proibida" in rules(texto, cfg)


@pytest.mark.parametrize("texto", [
    "Nunca usamos NOME_DEVEDOR nem CPF_DEVEDOR nas análises.",
    '"y": ["DEBITO.NOME_DEVEDOR", "AJUIZAMENTO.PEF", "DEBITO.DATA_INSCRICAO"]',   # rótulo de gráfico
    '{"NOME_DEVEDOR": null}',
    '{"NOME_DEVEDOR": ""}',
    "NOME_DEVEDOR: [NOME_HASH_3f2a]",
    '"CPF_DEVEDOR": "9f86d081884c7d659a2feaa0c55ad015"',
    "nome_socio: pl.Utf8",
    "SELECT COUNT(*) FROM debito -- sem NOME_DEVEDOR",
    '"  null CPF_DEVEDOR: 37.2%\\n",',                                  # taxa de nulos impressa
    "  'NOME_DEVEDOR': 1.23e-05,",                                         # estatística numérica
    '"  \'NOME_DEVEDOR\',\\n",\n"  \'CNPJ_DEVEDOR\',\\n",\n"  \'CPF_DEVEDOR\'],\\n",',  # lista de colunas
    '"CPF_DEVEDOR",\n"DATA_INSCRICAO",\n"NOME_DEVEDOR"',                  # lista JSON multilinha
])
def test_mencao_ou_valor_benigno_nao_dispara(texto, cfg):
    assert "coluna_proibida" not in rules(texto, cfg)


def test_lista_de_colunas_configuravel():
    cfg = pc.load_config(None)
    cfg.forbidden_columns = ["RAZAO_SOCIAL"]
    assert rules('{"RAZAO_SOCIAL": "ACME LTDA"}', cfg) == ["coluna_proibida"]
    assert rules('{"NOME_DEVEDOR": "X"}', cfg) == []


# ----------------------------------------------------------------- arquivos, allowlist e CLI
def write(p, text):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


def test_cli_limpo_retorna_0(tmp_path, capsys):
    write(tmp_path / "lab/index.qmd", "# Capa\nSó agregados.")
    assert pc.main([str(tmp_path / "lab"), "--no-config", "--root", str(tmp_path)]) == 0


def test_cli_achado_retorna_1_sem_vazar_valor(tmp_path, capsys):
    write(tmp_path / "lab/_freeze/eda/x/execute-results/html.json",
          json.dumps({"hash": "0", "result": {"markdown": f"<td>{fmt_cpf(CPF)}</td>"}}))
    rc = pc.main([str(tmp_path / "lab"), "--no-config", "--root", str(tmp_path)])
    out = capsys.readouterr().out
    assert rc == 1
    assert "lab/_freeze/eda/x/execute-results/html.json:1: [cpf]" in out
    assert CPF not in out and fmt_cpf(CPF) not in out
    assert out.strip().endswith(fmt_cpf(CPF)[-2:])


def test_html_gerado_e_site_libs_excluido(tmp_path):
    write(tmp_path / "lab/_site/eda/x.html", f"<p>{fmt_cnpj(CNPJ)}</p>")
    write(tmp_path / "lab/_site/site_libs/lib.js", f"// {fmt_cnpj(CNPJ)}")
    cfg = pc.load_config(None)
    fs = pc.scan_paths([tmp_path / "lab"], cfg, tmp_path)
    assert [(f.path, f.rule) for f in fs] == [("lab/_site/eda/x.html", "cnpj")]


def test_binario_ignorado(tmp_path):
    p = tmp_path / "lab/dado.txt"
    p.parent.mkdir(parents=True)
    p.write_bytes(b"\x00\x01" + CPF.encode())
    assert pc.scan_paths([tmp_path / "lab"], pc.load_config(None), tmp_path) == []


def test_allowlist_por_arquivo_regra_e_sha256(tmp_path):
    write(tmp_path / "lab/a.qmd", f"total {CNPJ}\ncontato x@usp.br")
    write(tmp_path / "lab/b.qmd", f"total {CNPJ}")
    h = hashlib.sha256(CNPJ.encode()).hexdigest()
    cfgfile = write(tmp_path / "allow.toml", f"""
[[allow]]
path = "lab/a.qmd"
rules = ["cnpj"]
sha256 = ["{h}"]
reason = "falso positivo"
""")
    cfg = pc.load_config(cfgfile)
    fs = pc.scan_paths([tmp_path / "lab"], cfg, tmp_path)
    assert sorted((f.path, f.rule) for f in fs) == [("lab/a.qmd", "email"), ("lab/b.qmd", "cnpj")]


def test_allowlist_glob_e_curinga(tmp_path):
    write(tmp_path / "lab/eda/a.qmd", f"{CPF} y@usp.br")
    cfgfile = write(tmp_path / "allow.toml", '[[allow]]\npath = "lab/eda/*"\nrules = ["*"]\n')
    assert pc.scan_paths([tmp_path / "lab"], pc.load_config(cfgfile), tmp_path) == []


def test_exclude_configuravel(tmp_path):
    write(tmp_path / "lab/vendor/x.js", CPF)
    cfgfile = write(tmp_path / "c.toml", 'exclude = ["lab/vendor/**"]\n')
    assert pc.scan_paths([tmp_path / "lab"], pc.load_config(cfgfile), tmp_path) == []


def test_config_invalida_retorna_2(tmp_path):
    bad = write(tmp_path / "c.toml", '[[allow]]\npath = "x"\nrules = ["telefone"]\n')
    assert pc.main(["--config", str(bad), str(tmp_path)]) == 2
    assert pc.main(["--config", str(tmp_path / "nao_existe.toml"), str(tmp_path)]) == 2


def test_config_padrao_do_repo_carrega():
    cfg = pc.load_config(pc.DEFAULT_CONFIG)
    assert "NOME_DEVEDOR" in cfg.forbidden_columns


def test_numero_de_linha(cfg):
    fs = pc.scan_text("a\nb\nc " + CPF, "lab/x.md", cfg)
    assert fs[0].line == 3
