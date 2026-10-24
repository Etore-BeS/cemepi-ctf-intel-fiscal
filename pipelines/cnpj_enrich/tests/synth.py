"""Synthetic CEMEPI DEBITO dump and synthetic RFB release (mini zips with the real layout). No real data."""
from __future__ import annotations

import random
import zipfile
from pathlib import Path

import duckdb

from cnpj_pipeline.cnpj import make_cnpj
from cnpj_pipeline.layouts import CODE_FILES

SENTINEL = "SEGREDO_PII"   # written into every forbidden column (names, addresses, phones, e-mails)
TIPOS = ["ICMS Declarado", "ICMS Autuação", "IPVA", "ITCMD", "Taxa Judiciária"]


def _fmt(c: str, rng: random.Random) -> str:
    r = rng.random()
    if r < 0.3 and c.isdigit():
        return f"{c[:2]}.{c[2:5]}.{c[5:8]}/{c[8:12]}-{c[12:]}"
    if r < 0.35 and c.isdigit() and c.startswith("0") and len(c.lstrip("0")) >= 12:
        return c.lstrip("0")          # lost leading zeros (numeric cast upstream)
    return c


def make_world(root: Path, n_roots: int = 300, seed: int = 0, years=(2024, 2025, 2026), est_per_root=(1, 3)):
    """Returns dict with debtors (truth), paths of the DuckDB dump and of the RFB release dir."""
    rng = random.Random(seed)
    roots = set()
    while len(roots) < n_roots:
        roots.add(f"{rng.randint(0, 10**8 - 1):08d}")
    roots = sorted(roots)
    roots[0] = "12ABC345"                                     # alphanumeric CNPJ (IN RFB 2.229/2024)
    firms = {}
    for i, r in enumerate(roots):
        n = rng.randint(*est_per_root)
        cnpjs = [make_cnpj(r + f"{k + 1:04d}") if r != "12ABC345" else make_cnpj(r + ("01DE" if k == 0 else f"{k:04d}"))
                 for k in range(n)]
        firms[r] = {"cnpjs": cnpjs, "in_rfb": i % 15 != 7, "estab_in_rfb": i % 15 not in (7, 8),
                    "mei": i % 10 == 3, "natureza": "2135" if i % 10 == 3 else rng.choice(["2062", "2054", "2240"])}
    # ------------------------------------------------------------------ DEBITO dump (DuckDB, like cemepi.duckdb)
    db = root / "cemepi.duckdb"
    con = duckdb.connect(str(db))
    con.execute("""CREATE TABLE DEBITO (ID_HT_EXTRACAO_REL_SGR BIGINT, ANO_EXTRACAO INTEGER, MES_EXTRACAO INTEGER,
                   ID_DEBITO BIGINT, CDA_COMPLETA VARCHAR, DATA_INSCRICAO VARCHAR, SITUACAO_DEBITO VARCHAR,
                   TIPO_DEBITO VARCHAR, STATUS_AJUIZAMENTO_DEBITO VARCHAR, VALOR_COM_VH VARCHAR,
                   VALOR_SEM_HONORARIOS VARCHAR, VALOR_VH VARCHAR, HONORARIOS_ADMINISTRATIVOS VARCHAR,
                   NOME_DEVEDOR VARCHAR, CNPJ_DEVEDOR VARCHAR, CPF_DEVEDOR VARCHAR)""")
    rows, truth, did = [], {}, 0
    debt_cnpjs = []
    for r in roots:
        for c in firms[r]["cnpjs"]:
            if rng.random() < 0.8:
                debt_cnpjs.append(c)
    debt_cnpjs.append("11111111111111")                       # invalid check digits (masked-looking)
    for c in debt_cnpjs:
        tipos = rng.sample(TIPOS, rng.randint(1, 2))
        for t in tipos:
            did += 1
            y0 = rng.choice(years)
            for y in years:
                if y < y0:
                    continue
                for m in range(1, 13):
                    if y == 2026 and m > 3:
                        break
                    if rng.random() < 0.4:
                        continue
                    v = round(rng.uniform(100, 1e6), 2)
                    rows.append((1, y, m, did, f"CDA{did}", f"{y0}-01-15", "ATIVO", t, "Ajuizado", str(v), str(v),
                                 "0", "0", SENTINEL, _fmt(c, rng), None))
                    truth.setdefault(c, set()).add((t, y * 100 + m))
    for k in range(40):                                        # natural persons: must be excluded
        rows.append((1, 2026, 3, 10**6 + k, "CDAPF", "2020-01-01", "ATIVO", "IPVA", "", "1", "1", "0", "0",
                     SENTINEL, None, f"{rng.randint(10**10, 10**11 - 1)}"))
    con.executemany("INSERT INTO DEBITO VALUES (" + ",".join("?" * 16) + ")", rows)
    con.close()
    # ------------------------------------------------------------------ RFB release (zips, Latin-1, ';', no header)
    rfb = root / "release" / "2026-03"
    rfb.mkdir(parents=True)
    qv = lambda *v: ";".join(f'"{x}"' for x in v)
    emp = {i: [] for i in range(10)}; est = {i: [] for i in range(10)}; simp = []
    for i, r in enumerate(roots):
        f = firms[r]
        if not f["in_rfb"]:
            continue
        b = i % 10
        emp[b].append(qv(r, f"{SENTINEL} LTDA ÇÃO", f["natureza"], "49", f"{rng.randint(1, 10**6)},00",
                         "01" if f["mei"] else rng.choice(["01", "03", "05"]), ""))
        if f["estab_in_rfb"]:
            for k, c in enumerate(f["cnpjs"]):
                est[b].append(qv(r, c[8:12], c[12:], "1" if k == 0 else "2", SENTINEL, rng.choice(["02", "04", "08"]),
                                 "20210305", "01", "", "", "20100101", rng.choice(["4711302", "2511000", "6201501"]),
                                 "4930202,6202300", "RUA", SENTINEL, "10", "", SENTINEL, "01000000", "SP", "6291" if i % 30 == 13 else "7107",
                                 "11", "99999999", "", "", "", "", f"{SENTINEL}@x.com", "", ""))
        opt = "S" if f["mei"] or rng.random() < 0.5 else "N"
        f["in_simples"] = f["mei"] or i % 10 != 7           # some RFB firms never appear in the Simples file
        if f["in_simples"]:
            simp.append(qv(r, opt, "20180101", "00000000",
                           "S" if f["mei"] else "N", "20180101" if f["mei"] else "00000000", "00000000"))
    # duplicates across files + a malformed line + a non-debtor firm
    first_in = next(r for r in roots if firms[r]["in_rfb"] and firms[r]["estab_in_rfb"])
    c0 = firms[first_in]["cnpjs"][0]
    est[9].append(qv(first_in, c0[8:12], c0[12:], "1", SENTINEL, "08", "20250101", "01", "", "", "20100101",
                     "4711302", "", "RUA", SENTINEL, "1", "", SENTINEL, "0", "SP", "7107", "", "", "", "", "", "",
                     SENTINEL, "", ""))
    est[5].append('"broken;line"";;')
    emp[3].append(qv("99999999", "NAO DEVEDOR", "2062", "49", "1,00", "05", ""))
    def z(name: str, member: str, lines: list[str]):
        with zipfile.ZipFile(rfb / name, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(member, ("\n".join(lines) + "\n").encode("latin-1"))
    for b in range(10):
        z(f"Empresas{b}.zip", f"K3241.K03200Y{b}.D60314.EMPRECSV", emp[b])
        z(f"Estabelecimentos{b}.zip", f"K3241.K03200Y{b}.D60314.ESTABELE", est[b])
    z("Simples.zip", "F.K03200$W.SIMPLES.CSV.D60314", simp)
    codes = {"cnaes": [qv("4711302", "Comércio varejista de mercadorias"), qv("2511000", "Fabricação de estruturas metálicas"),
                       qv("6201501", "Desenvolvimento de programas de computador sob encomenda")],
             "naturezas": [qv("2062", "Sociedade Empresária Limitada"), qv("2135", "Empresário (Individual)"),
                           qv("2054", "Sociedade Anônima Fechada"), qv("2240", "Sociedade Simples Limitada")],
             "municipios": [qv("7107", "SAO PAULO"), qv("6291", "CAMPINAS")], "motivos": [qv("01", "EXTINCAO POR ENCERRAMENTO")],
             "qualificacoes": [qv("49", "Sócio-Administrador"), qv("22", "Sócio"),
                               qv("37", "Sócio Pessoa Jurídica Domiciliado no Exterior"), qv("17", "Procurador"),
                               qv("00", "Não informada")],
             "paises": [qv("105", "BRASIL"), qv("077", "BAHAMAS, ILHAS")]}
    for zname, t in CODE_FILES.items():
        z(zname, f"F.K03200$Z.D60314.{t.upper()}CSV", codes[t])
    (rfb / "municipios_ibge.csv").write_bytes(
        ("CÓDIGO DO MUNICÍPIO - TOM;CÓDIGO DO MUNICÍPIO - IBGE;MUNICÍPIO - TOM;MUNICÍPIO - IBGE;UF\n"
         "7107;3550308;SAO PAULO;São Paulo;SP\n6291;3509502;CAMPINAS;Campinas;SP\n").encode("latin-1"))
    socios_rows = make_socios(rfb, roots, firms, truth, rng, qv)
    regime_dir, regime_truth = make_regime(root / "regime_share", roots, firms, truth)
    return {"db": db, "release": rfb.parent, "truth": truth, "firms": firms, "roots": roots,
            "socios_rows": socios_rows, "regime_share": regime_dir, "regime_truth": regime_truth}


PF_NAME = "NOME_PF_SOCIO"        # marker in every natural-person name: must never reach the export package
REP_NAME = "NOME_REPRESENTANTE"


def make_socios(rfb: Path, roots, firms, truth, rng, qv) -> list[tuple]:
    """Socios0..9.zip with the real 11-column layout. Returns every generated row (as tuples of the 11 fields)."""
    debtor = sorted({c[:8] for c in truth})
    nondebtor_pj = make_cnpj("888888880001")
    rows = []
    for i, r in enumerate(roots):
        if not firms[r]["in_rfb"]:
            continue
        rows.append((r, "2", f"{PF_NAME} {i} JOSÉ ÇÃO", f"***{i:06d}**", "49", "20200115", "", "***000000**", "",
                     "00", str(1 + i % 9)))
        if i % 4 == 0:                                   # PJ partner: another firm (debtor or not)
            other = roots[(i + 1) % len(roots)]
            rows.append((r, "1", f"EMPRESA SOCIA {i} LTDA", firms[other]["cnpjs"][0], "22", "20190301", "",
                         "***000000**", "", "00", "0"))
        if i % 7 == 0:                                   # foreign partner with a legal representative
            rows.append((r, "3", f"FOREIGN HOLDING {i}", "", "37", "20180510", "077", f"***{i:06d}**",
                         f"{REP_NAME} {i}", "17", "0"))
    # homonym: same name and same masked CPF in two debtor firms (approximate PF key merges them)
    rows.append((debtor[1], "2", f"{PF_NAME} MARIA HOMONIMA", "***555555**", "49", "20210101", "", "***000000**", "",
                 "00", "4"))
    rows.append((debtor[2], "2", f"{PF_NAME} MARIA HOMONIMA", "***555555**", "22", "20220101", "", "***000000**", "",
                 "00", "4"))
    # second degree: non-debtor firm whose PJ partner is a debtor -> included
    rows.append(("99999999", "1", "HOLDING DEVEDORA SA", firms[debtor[3]]["cnpjs"][0], "22", "20150101", "",
                 "***000000**", "", "00", "0"))
    rows.append(("99999999", "2", f"{PF_NAME} SOCIO DA NAO DEVEDORA", "***777777**", "49", "20150101", "",
                 "***000000**", "", "00", "6"))
    # excluded: non-debtor firm with a PF partner only / with a non-debtor PJ partner
    rows.append(("99999998", "2", f"{PF_NAME} FORA", "***888888**", "49", "20150101", "", "***000000**", "", "00", "6"))
    rows.append(("99999997", "1", "OUTRA SA", nondebtor_pj, "22", "20150101", "", "***000000**", "", "00", "0"))
    assert not {"99999999", "99999998", "99999997", "88888888"} & set(roots)
    files = {b: [] for b in range(10)}
    for k, row in enumerate(rows):
        files[k % 10].append(qv(*row))
    files[3].append(files[3][0])                          # exact duplicate line in the RFB file
    files[6].append('"broken;line"";;')                   # malformed line
    for b in range(10):
        with zipfile.ZipFile(rfb / f"Socios{b}.zip", "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(f"K3241.K03200Y{b}.D60314.SOCIOCSV", ("\n".join(files[b]) + "\n").encode("latin-1"))
    return rows


def _fmt_cnpj(c: str) -> str:
    return f"{c[:2]}.{c[2:5]}.{c[5:8]}/{c[8:12]}-{c[12:]}"


def make_regime(share: Path, roots, firms, truth):
    """The 4 entidades-*.zip of the 'Regimes Tributários' share (CSV with header, ','). Returns (dir, expected)."""
    share.mkdir(parents=True)
    debtor = [r for r in sorted({c[:8] for c in truth}) if r.isdigit()]
    A, B, C, E = debtor[1], debtor[2], debtor[3], debtor[4]
    cn = lambda r, k=0: _fmt_cnpj(firms[r]["cnpjs"][k])
    hdr = "ano,cnpj,cnpj_da_scp,forma_de_tributacao,quantidade_de_escrituracoes"
    real = [hdr, f"2022,{cn(A)},0,LUCRO REAL,1", f"2023,{cn(A)},0,LUCRO REAL,1",
            f"2023,{cn(B)},0,LUCRO PRESUMIDO/REAL,1",
            f"2024,{cn(C)},{make_cnpj('777777770001')},LUCRO REAL,1",          # SCP row: not the firm itself
            f"2022,{cn(E)},0,LUCRO REAL,1",
            f"2024,{_fmt_cnpj(make_cnpj('999999990001'))},0,LUCRO REAL,1"]      # non-debtor: excluded
    pres21 = [hdr, f"2021,{cn(A)},0,LUCRO PRESUMIDO,1"]
    pres23 = [hdr, f"2023,{cn(B)},0,LUCRO PRESUMIDO/REAL,1"]
    arb = [hdr, f"2022,{cn(E)},0,LUCRO ARBITRADO,1"]
    imu = [hdr, f"2024,{cn(C)},0,IMUNE DE IRPJ,1"]
    def z(name, members):
        with zipfile.ZipFile(share / name, "w", zipfile.ZIP_DEFLATED) as zf:
            for m, lines, eol in members:
                zf.writestr(m, (eol.join(lines) + eol).encode("latin-1"))
    z("entidades-lucro-real.zip", [("Lucro Real.csv", real, "\r\n")])
    z("entidades-lucro-presumido.zip", [("Lucro Presumido 2021.csv", pres21, "\n"),
                                        ("Lucro Presumido 2023.csv", pres23, "\n")])
    z("entidades-lucro-arbitrado.zip", [("Lucro Arbitrado.csv", arb, "\r\n")])
    z("entidades-imunes-e-isentas.zip", [("Imunes e Isentas.csv", imu, "\n")])
    expected = {A: ("LUCRO REAL", 2023, "2021:LUCRO PRESUMIDO; 2022:LUCRO REAL; 2023:LUCRO REAL"),
                B: ("LUCRO PRESUMIDO/REAL", 2023, "2023:LUCRO PRESUMIDO/REAL"),
                C: ("IMUNE DE IRPJ", 2024, "2024:IMUNE DE IRPJ"),
                E: ("LUCRO ARBITRADO + LUCRO REAL", 2022, "2022:LUCRO ARBITRADO + LUCRO REAL")}
    return share, expected


def big_estab_zip(path: Path, n_rows: int, seed: int = 1, debtor_roots: list[str] | None = None) -> int:
    """One large Estabelecimentos zip (for the memory test); returns the CSV size in bytes."""
    rng = random.Random(seed)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf, zf.open("K.ESTABELE", "w", force_zip64=True) as fh:
        size = 0
        for i in range(n_rows):
            r = rng.choice(debtor_roots) if debtor_roots and i % 2 else f"{rng.randint(0, 10**8 - 1):08d}"
            line = (f'"{r}";"{i % 10000:04d}";"00";"1";"{SENTINEL} FANTASIA {i}";"02";"20200101";"00";"";"";"20100101";"4711302";'
                    f'"4930202,6202300,4711301";"RUA";"{SENTINEL} LOGRADOURO MUITO LONGO {i}";"{i}";"SALA {i}";'
                    f'"BAIRRO {i}";"01000000";"SP";"7107";"11";"{i:08d}";"";"";"";"";"{SENTINEL}{i}@x.com";"";""\n')
            b = line.encode("latin-1"); fh.write(b); size += len(b)
    return size
