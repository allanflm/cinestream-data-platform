"""Conexão e utilitários compartilhados pelos scripts do gerador de dados."""
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MOVIELENS_DIR = PROJECT_ROOT / "data" / "movielens" 

CONNECTION_URL = URL.create(
    "mssql+pyodbc",
    username="sa",
    password="CineStream@2026",  # mesma senha do docker-compose.yml (só ambiente local)
    host="localhost",
    port=1433,
    database="CineStream",
    query={
        "driver": "ODBC Driver 18 for SQL Server",
        "TrustServerCertificate": "yes",
    },
)


def get_engine():
    return create_engine(CONNECTION_URL, fast_executemany=True)


def reset_identity(conn, table: str) -> None:
    """Faz o próximo IDENTITY da tabela (já vazia) começar em 1.

    Cuidado do SQL Server: RESEED 0 numa tabela que nunca recebeu linhas faz o
    primeiro id ser 0, e não 1. Por isso só reseta se a tabela já foi usada.
    """
    used = conn.execute(
        text("SELECT CAST(last_value AS BIGINT) FROM sys.identity_columns WHERE object_id = OBJECT_ID(:t)"),
        {"t": table},
    ).scalar()
    if used is not None:
        conn.execute(text(f"DBCC CHECKIDENT ('{table}', RESEED, 0)"))
