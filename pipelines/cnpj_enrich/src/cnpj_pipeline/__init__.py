"""Resilient pipeline that enriches CEMEPI debtor CNPJs with the Receita Federal open CNPJ data.

Stages (each one is resumable and idempotent; see README.md):
    collect  -> distinct debtor CNPJs from the DEBITO dump (all taxes), partitioned Parquet
    download -> RFB open-data zips with HTTP Range resume, retries, size + CRC check, manifest
    enrich   -> stream each zip through DuckDB, semi-join with the debtor keys, Parquet per unit
    build    -> consolidated table cnpj_enriquecido + coverage report
    socios   -> partners of the debtor firms (local layer only) + network edge list
    regime   -> tax regime per CNPJ and year (RFB Regimes Tributários, ECF)
    export   -> package for the API server (Parquet, CSV.gz, DDL, idempotent upsert scripts)
"""
__version__ = "0.2.0"
