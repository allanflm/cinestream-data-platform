"""
CineStream - Passo 5: extração incremental SQL Server -> landing zone no Databricks

O Databricks Free Edition não alcança o SQL Server local, então os dados são
*empurrados*: este script lê o que mudou, grava Parquet e envia para um Volume
do Unity Catalog. Daí em diante, o Auto Loader leva para a camada Bronze.

Como funciona:
  - Tabelas com updated_at: só extrai linhas com updated_at >= último watermark.
    O limite inferior é inclusivo de propósito (at-least-once): é melhor repetir
    uma linha do que perder uma que caiu no mesmo segundo. A Silver deduplica.
  - Tabelas sem updated_at (movie_genres): extração completa a cada execução.
  - O schema do Parquet vem do próprio SQL Server (INFORMATION_SCHEMA), então
    DECIMAL(10,2) chega como decimal(10,2) em todos os arquivos.
  - Os watermarks ficam no próprio Volume (_state/watermarks.json): se a landing
    for apagada, o estado vai junto e a próxima execução refaz a carga completa.

Layout no Volume:
  /Volumes/cinestream/landing/raw/<tabela>/ingest_date=AAAA-MM-DD/<tabela>_<run_id>_<parte>.parquet

Uso (na raiz do projeto, com o .venv ativo e o SQL Server no ar):
  python ingestion/extract_to_landing.py          # incremental
  python ingestion/extract_to_landing.py --full   # ignora watermarks e extrai tudo
"""

import argparse
import io
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from databricks.sdk import WorkspaceClient
from databricks.sdk.errors import NotFound
from sqlalchemy import text

# Reaproveita a conexão do gerador de dados
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "data_generator"))
from db import get_engine  # noqa: E402

# =====================================================================
# Configuração
# =====================================================================
DATABRICKS_PROFILE = "cinestream"
VOLUME_ROOT = "/Volumes/cinestream/landing/raw"
STATE_PATH = f"{VOLUME_ROOT}/_state/watermarks.json"
CHUNK_ROWS = 500_000  # linhas por arquivo Parquet

# tabela -> coluna de watermark (None = extração completa)
TABLES = {
    "plans": "updated_at",
    "genres": "updated_at",
    "movies": "updated_at",
    "movie_genres": None,
    "users": "updated_at",
    "subscriptions": "updated_at",
    "payments": "updated_at",
    "viewing_sessions": "updated_at",
    "ratings": "updated_at",
}


# =====================================================================
# Schema: tipos do SQL Server -> tipos do Parquet
# =====================================================================
def arrow_type(data_type: str, precision, scale) -> pa.DataType:
    t = data_type.lower()
    if t == "bigint":
        return pa.int64()
    if t == "int":
        return pa.int32()
    if t in ("smallint", "tinyint"):
        return pa.int16()
    if t == "bit":
        return pa.bool_()
    if t in ("decimal", "numeric"):
        return pa.decimal128(int(precision), int(scale))
    if t in ("float", "real"):
        return pa.float64()
    if t == "date":
        return pa.date32()
    if t in ("datetime", "datetime2", "smalldatetime"):
        return pa.timestamp("us")
    return pa.string()


def source_schema(conn, table: str) -> pa.Schema:
    rows = conn.execute(text("""
        SELECT COLUMN_NAME, DATA_TYPE, NUMERIC_PRECISION, NUMERIC_SCALE, IS_NULLABLE
        FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA = 'app' AND TABLE_NAME = :t
        ORDER BY ORDINAL_POSITION
    """), {"t": table}).fetchall()
    return pa.schema([
        pa.field(name, arrow_type(dtype, prec, scale), nullable=(nullable == "YES"))
        for name, dtype, prec, scale, nullable in rows
    ])


def to_parquet_bytes(df: pd.DataFrame, schema: pa.Schema) -> bytes:
    table = pa.Table.from_pandas(df, schema=schema, preserve_index=False)
    buf = io.BytesIO()
    pq.write_table(table, buf, compression="snappy")
    return buf.getvalue()


# =====================================================================
# Watermarks (guardados no próprio Volume)
# =====================================================================
def load_state(w: WorkspaceClient) -> dict:
    try:
        return json.loads(w.files.download(STATE_PATH).contents.read())
    except NotFound:
        return {}


def save_state(w: WorkspaceClient, state: dict) -> None:
    payload = json.dumps(state, indent=2, ensure_ascii=False).encode()
    w.files.upload(STATE_PATH, io.BytesIO(payload), overwrite=True)


# =====================================================================
# Extração
# =====================================================================
def extract_table(engine, w, table, wm_col, state, full, run_id, ingest_date):
    t0 = time.time()
    with engine.connect() as conn:
        schema = source_schema(conn, table)
        params = {}
        where = ""
        upper = None
        if wm_col:
            upper = conn.execute(text(f"SELECT MAX({wm_col}) FROM app.{table}")).scalar()
            if upper is None:
                return 0, 0, None, "vazia"
            last = None if full else state.get(table)
            where = f"WHERE {wm_col} <= :upper"
            params["upper"] = upper
            if last:
                last_dt = datetime.fromisoformat(last)
                if upper <= last_dt:
                    return 0, 0, None, "nada novo"
                where += f" AND {wm_col} >= :last"
                params["last"] = last_dt
        query = text(f"SELECT * FROM app.{table} {where}")

        rows, files = 0, 0
        for part, chunk in enumerate(pd.read_sql(query, conn, params=params,
                                                 chunksize=CHUNK_ROWS, coerce_float=False)):
            if chunk.empty:
                continue
            path = f"{VOLUME_ROOT}/{table}/ingest_date={ingest_date}/{table}_{run_id}_{part:03d}.parquet"
            w.files.upload(path, io.BytesIO(to_parquet_bytes(chunk, schema)), overwrite=True)
            rows += len(chunk)
            files += 1

    new_wm = upper.isoformat() if upper is not None else None
    return rows, files, new_wm, f"{time.time() - t0:.0f}s"


def main():
    parser = argparse.ArgumentParser(description="Extrai o SQL Server para a landing zone do Databricks.")
    parser.add_argument("--full", action="store_true", help="ignora os watermarks e extrai tudo")
    args = parser.parse_args()

    now = datetime.now(timezone.utc)
    run_id = now.strftime("%Y%m%dT%H%M%S")
    ingest_date = now.strftime("%Y-%m-%d")

    w = WorkspaceClient(profile=DATABRICKS_PROFILE)
    engine = get_engine()
    state = load_state(w)

    modo = "completa (--full)" if args.full or not state else "incremental"
    print(f"\n  📦 Extração {modo} · run {run_id}")
    print(f"     destino: {VOLUME_ROOT}\n")

    t0 = time.time()
    total = 0
    for table, wm_col in TABLES.items():
        rows, files, new_wm, info = extract_table(engine, w, table, wm_col, state, args.full, run_id, ingest_date)
        total += rows
        tipo = "incremental" if wm_col else "completa"
        if rows:
            print(f"  ✅ {table:<17} {rows:>10,} linhas  {files} arquivo(s)  [{tipo}]  {info}")
        else:
            print(f"  ⚪ {table:<17} {'nada novo':>10}  [{tipo}]")
        if new_wm:
            state[table] = new_wm
            save_state(w, state)  # salva por tabela: se cair no meio, o que já foi enviado não se repete

    print(f"\n  🎉 {total:,} linhas enviadas em {time.time() - t0:.0f}s\n")


if __name__ == "__main__":
    main()
