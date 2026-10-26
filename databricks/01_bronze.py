# Databricks notebook source
# MAGIC %md
# MAGIC # 🥉 Bronze: landing zone → Delta
# MAGIC
# MAGIC Lê os Parquet que o extrator envia para `/Volumes/cinestream/landing/raw/<tabela>/`
# MAGIC e grava em `cinestream.bronze.<tabela>`, **sem transformar nada**: a Bronze é a cópia fiel
# MAGIC do que chegou, com metadados de rastreabilidade.
# MAGIC
# MAGIC - **Auto Loader** (`cloudFiles`): processa só os arquivos novos, controlados pelo checkpoint.
# MAGIC - **`availableNow`**: lê o que estiver pendente e encerra (o modo do serverless, ideal para jobs agendados).
# MAGIC - **Append-only**: a Bronze guarda o histórico de tudo o que chegou, inclusive linhas repetidas
# MAGIC   do limite inclusivo do extrator. Quem deduplica é a Silver.
# MAGIC - Colunas adicionadas: `ingest_date` (da pasta), `_source_file` e `_ingested_at`.

# COMMAND ----------

from pyspark.sql import functions as F

CATALOG = "cinestream"
LANDING = f"/Volumes/{CATALOG}/landing/raw"
CHECKPOINTS = f"/Volumes/{CATALOG}/bronze/checkpoints"

TABLES = [
    "plans", "genres", "movies", "movie_genres", "users",
    "subscriptions", "payments", "viewing_sessions", "ratings",
]

spark.sql(f"CREATE VOLUME IF NOT EXISTS {CATALOG}.bronze.checkpoints")

# COMMAND ----------

def ingest(table: str) -> int:
    """Leva os arquivos novos da landing para a tabela Bronze e devolve quantas linhas entraram."""
    checkpoint = f"{CHECKPOINTS}/{table}"
    target = f"{CATALOG}.bronze.{table}"

    query = (
        spark.readStream.format("cloudFiles")
        .option("cloudFiles.format", "parquet")
        .option("cloudFiles.schemaLocation", f"{checkpoint}/_schema")
        .option("cloudFiles.partitionColumns", "ingest_date")
        .load(f"{LANDING}/{table}")
        .select(
            "*",
            F.col("_metadata.file_path").alias("_source_file"),
            F.current_timestamp().alias("_ingested_at"),
        )
        .writeStream
        .option("checkpointLocation", checkpoint)
        .option("mergeSchema", "true")
        .trigger(availableNow=True)
        .toTable(target)
    )
    query.awaitTermination()
    return sum(p["numInputRows"] for p in query.recentProgress)

# COMMAND ----------

resultado = []
for table in TABLES:
    novas = ingest(table)
    total = spark.table(f"{CATALOG}.bronze.{table}").count()
    resultado.append((table, novas, total))
    print(f"{'✅' if novas else '⚪'} {table:<17} +{novas:>10,}  (total {total:,})")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Conferência
# MAGIC Linhas que entraram nesta execução e o total acumulado em cada tabela Bronze.

# COMMAND ----------

display(spark.createDataFrame(resultado, "tabela string, linhas_novas long, total_bronze long"))
