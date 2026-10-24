"""Funções do K-means com várias inicializações (CTF-03) e da base do top 1% (B.5.17-B.5.19).

A base do top 1% repete a célula B.5.17 do paper EDA v1. Com snapshot 2026-03 e sem corte de
pagamentos, a matriz de Gower bate com a chave do cache do paper (HIT), o que confere a réplica.
"""
from __future__ import annotations

import numpy as np
import polars as pl
from sklearn.cluster import AgglomerativeClustering, KMeans
from sklearn.metrics import adjusted_rand_score, silhouette_score

import _lab_base as B

NUM = ["log_value", "age", "recovery", "time_to_payment", "n_payments", "programme_share"]
CAT = ["ICMS_ORIGIN", "SITUACAO_DEBITO", "STATUS_AJUIZAMENTO_DEBITO"]


def top_features(snap_year: int, snap_month: int, pay_cutoff: str | None) -> pl.DataFrame:
    """Top 1% das CDAs de ICMS (PJ) por valor na foto; variáveis do B.5.17. pay_cutoff='AAAA-MM-DD' exclusivo."""
    snap = (B.scan_icms().filter((pl.col("year") == snap_year) & (pl.col("MES_EXTRACAO") == snap_month))
            .with_columns(((pl.date(snap_year, snap_month, 1) - pl.col("DATA_INSCRICAO")).dt.total_days() / 365.25).alias("age")))
    raw_values = B.collect(snap.select("TIPO_DEBITO", pl.col("VALOR_SEM_HONORARIOS").alias("value"), "age", "ICMS_ORIGIN",
                                       "STATUS_AJUIZAMENTO_DEBITO", "SITUACAO_DEBITO", "ID_DEBITO", "CNPJ_ROOT"))
    ranked = raw_values.sort("value", descending=True).with_columns(pl.int_range(1, pl.len() + 1).alias("rank"))
    top = ranked.filter(pl.col("rank") <= max(1, round(0.01 * ranked.height)))
    top_ids = top["ID_DEBITO"].to_list()
    gare = B.scan_gare()
    if pay_cutoff:
        gare = gare.filter(pl.col("DATA_ARRECADACAO_GARE") < pl.lit(pay_cutoff).str.to_date())
    first_pay = B.collect(gare.filter(pl.col("ID_DEBITO").is_in(top_ids)).group_by("ID_DEBITO")
                          .agg(pl.col("DATA_ARRECADACAO_GARE").min().alias("first_payment")))
    cda = (B.collect(B.attributes.filter(pl.col("ID_DEBITO").is_in(top_ids)).select("ID_DEBITO", "DATA_INSCRICAO"))
           .join(first_pay, on="ID_DEBITO", how="left")
           .with_columns(((pl.col("first_payment") - pl.col("DATA_INSCRICAO")).dt.total_days() / 365.25).alias("years_to_first_payment")))
    in_prog = pl.any_horizontal([pl.col("DATA_ARRECADACAO_GARE").is_between(pl.lit(a).str.to_date(), pl.lit(b).str.to_date())
                                 for _, a, b in B.PROGRAMMES])
    prog_top = B.collect(gare.filter(pl.col("ID_DEBITO").is_in(top_ids)).with_columns(in_prog.alias("in_programme"))
                         .group_by("ID_DEBITO").agg(pl.len().alias("n_payments"),
                                                    pl.col("VALOR_RECEITA").filter(pl.col("in_programme")).sum().alias("paid_programme"),
                                                    pl.col("VALOR_RECEITA").sum().alias("paid_total")))
    return (top.select("ID_DEBITO", "CNPJ_ROOT", "value", "age", "ICMS_ORIGIN", "SITUACAO_DEBITO", "STATUS_AJUIZAMENTO_DEBITO")
            .join(cda.select("ID_DEBITO", "years_to_first_payment"), on="ID_DEBITO", how="left")
            .join(prog_top, on="ID_DEBITO", how="left")
            .with_columns(pl.col("n_payments").fill_null(0), pl.col("paid_total").fill_null(0), pl.col("paid_programme").fill_null(0))
            .with_columns(pl.col("value").log10().alias("log_value"), (pl.col("paid_total") / pl.col("value")).alias("recovery"),
                          (pl.col("paid_programme") / pl.col("paid_total")).fill_nan(0).fill_null(0).alias("programme_share"),
                          pl.col("years_to_first_payment").fill_null(pl.col("age")).alias("time_to_payment"))
            .sort("ID_DEBITO"))


def paper_matrices(features: pl.DataFrame) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """X_raw, X_num (mediana/IQR, como no paper) e X_cat (códigos)."""
    X_raw = features.select(NUM).to_numpy().astype(np.float32)
    med, iqr = np.nanmedian(X_raw, axis=0), np.subtract(*np.nanpercentile(X_raw, [75, 25], axis=0))
    X_num = np.nan_to_num((X_raw - med) / np.where(iqr == 0, 1, iqr)).astype(np.float32)
    X_cat = np.column_stack([features[c].cast(pl.Categorical).to_physical().to_numpy() for c in CAT]).astype(np.int32)
    return X_raw, X_num, X_cat


def gower_blocks(num: np.ndarray, cat: np.ndarray, block: int = 512):
    rng = num.max(axis=0) - num.min(axis=0)
    rng[rng == 0] = 1.0
    numr = (num / rng).astype(np.float32)
    k = num.shape[1] + cat.shape[1]

    def build(mm: np.memmap) -> None:
        for start in range(0, numr.shape[0], block):
            stop = min(start + block, numr.shape[0])
            blk = np.abs(numr[start:stop, None, :] - numr[None, :, :]).sum(axis=2)
            blk += (cat[start:stop, None, :] != cat[None, :, :]).sum(axis=2)
            mm[start:stop] = blk / k
    return build


def kmeans_design(X_num_raw: np.ndarray, X_cat: np.ndarray) -> np.ndarray:
    """Escala coerente com o Gower: numéricas pela amplitude (NaN -> mediana), categóricas one-hot x 1/sqrt(2).
    Assim, cada variável contribui no máximo 1 para a distância euclidiana ao quadrado."""
    X = np.asarray(X_num_raw, dtype=np.float64)
    X = np.where(np.isnan(X), np.nanmedian(X, axis=0), X)
    rng = X.max(axis=0) - X.min(axis=0)
    rng[rng == 0] = 1.0
    blocks = [(X - X.min(axis=0)) / rng]
    for j in range(X_cat.shape[1]):
        codes = np.asarray(X_cat[:, j])
        cats = np.unique(codes)
        blocks.append((codes[:, None] == cats[None, :]).astype(np.float64) / np.sqrt(2.0))
    return np.hstack(blocks)


def kmeans_multi_init(X, D, sub_idx, ks=range(2, 9), n_seeds=100, base_seed=B.SEED, n_blocks=5, tol_rel=1e-3) -> dict:
    """Para cada k: n_seeds ajustes (k-means++, n_init=1, sementes distintas). Guarda a menor inércia,
    a distribuição das inércias, a estabilidade entre sementes e as silhuetas da melhor partição."""
    rows, best = [], {}
    Dsub = np.asarray(D[np.ix_(sub_idx, sub_idx)]) if D is not None else None
    for k in ks:
        inertias, labs = [], []
        for s in range(n_seeds):
            km = KMeans(n_clusters=k, init="k-means++", n_init=1, max_iter=500,
                        random_state=base_seed + 1000 * k + s).fit(X)
            inertias.append(float(km.inertia_))
            labs.append(km.labels_.astype(np.int32))
        inertias = np.asarray(inertias)
        b = int(inertias.argmin())
        best[k] = labs[b]
        ari = np.array([adjusted_rand_score(labs[b], l) for i, l in enumerate(labs) if i != b])
        blk = np.array_split(np.arange(n_seeds), n_blocks)
        bb = [labs[int(ix[inertias[ix].argmin()])] for ix in blk]
        ari_blocks = [adjusted_rand_score(bb[i], bb[j]) for i in range(n_blocks) for j in range(i + 1, n_blocks)]
        sizes = np.bincount(labs[b], minlength=k)
        rows.append({
            "k": int(k), "n_seeds": n_seeds,
            "inertia_min": float(inertias.min()), "inertia_median": float(np.median(inertias)),
            "inertia_max": float(inertias.max()),
            "share_seeds_at_min": float(np.mean(inertias <= inertias.min() * (1 + tol_rel))),
            "ari_vs_best_mean": float(ari.mean()), "ari_vs_best_p05": float(np.percentile(ari, 5)),
            "ari_block_best_min": float(np.min(ari_blocks)),
            "silhouette_euclid": float(silhouette_score(X[sub_idx], labs[b][sub_idx])),
            "silhouette_gower": float(silhouette_score(Dsub, labs[b][sub_idx], metric="precomputed")) if Dsub is not None else None,
            "smallest_cluster_pct": float(100 * sizes.min() / sizes.sum()),
        })
    mins = np.array([r["inertia_min"] for r in rows])
    drops = np.r_[np.nan, (mins[:-1] - mins[1:]) / mins[:-1]]
    curv = np.r_[np.nan, mins[:-2] - 2 * mins[1:-1] + mins[2:], np.nan] / mins[0]
    for r, d, c in zip(rows, drops, curv):
        r["rel_drop_from_prev"] = None if np.isnan(d) else float(d)
        r["curvature"] = None if np.isnan(c) else float(c)
    return {"rows": rows, "best": {str(k): v.tolist() for k, v in best.items()}}


def agglomerative_silhouettes(D, sub_idx, ks=range(2, 9)) -> dict:
    Ds = np.asarray(D[np.ix_(sub_idx, sub_idx)])
    return {str(k): float(silhouette_score(
        Ds, AgglomerativeClustering(n_clusters=k, metric="precomputed", linkage="average").fit_predict(Ds),
        metric="precomputed")) for k in ks}


def agglomerative_full(D, k: int, fit_idx: np.ndarray, block: int = 1024) -> list[int]:
    Ds = np.asarray(D[np.ix_(fit_idx, fit_idx)])
    fit = AgglomerativeClustering(n_clusters=k, metric="precomputed", linkage="average").fit_predict(Ds)
    out = np.empty(D.shape[0], dtype=np.int64)
    for start in range(0, D.shape[0], block):
        stop = min(start + block, D.shape[0])
        out[start:stop] = fit[np.asarray(D[start:stop][:, fit_idx]).argmin(axis=1)]
    out[fit_idx] = fit
    return out.tolist()
