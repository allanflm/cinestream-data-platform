"""
CineStream - Passo 3: carga do catálogo de filmes (MovieLens ml-latest-small)

O que faz:
  1. Lê data/movielens/ml-latest-small/movies.csv
  2. Separa o ano do título e corrige artigos ("Matrix, The" -> "The Matrix")
  3. Quebra os gêneros em app.genres e app.movie_genres
  4. Simula a duração de cada filme a partir dos gêneros (seed fixa = reprodutível)
  5. Carrega no SQL Server (idempotente: pode rodar de novo sem duplicar)

Uso (na raiz do projeto, com o .venv ativo):
  python data_generator/01_load_movielens.py
"""

import re
from pathlib import Path

import numpy as np
import pandas as pd
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL

# ---------------------------------------------------------------------
# Configuração
# ---------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parents[1]
MOVIES_CSV = PROJECT_ROOT / "data" / "movielens" / "movies.csv"

SEED = 42

CONNECTION_URL = URL.create(
    "mssql+pyodbc",
    username="sa",
    password="CineStream@2026",  # mesma senha do docker-compose.yml
    host="localhost",
    port=1433,
    database="CineStream",
    query={
        "driver": "ODBC Driver 18 for SQL Server",
        "TrustServerCertificate": "yes",
    },
)

# Duração média (min) por gênero; filmes com vários gêneros usam a média deles
GENRE_AVG_DURATION = {
    "Animation": 90, "Children": 92, "Documentary": 95, "Comedy": 100,
    "Horror": 98, "Musical": 110, "Romance": 108, "Film-Noir": 100,
    "Mystery": 110, "Thriller": 112, "Fantasy": 115, "Sci-Fi": 118,
    "Crime": 115, "Action": 118, "Adventure": 118, "Western": 115,
    "Drama": 120, "War": 130, "IMAX": 125,
}
DEFAULT_DURATION = 105

# Artigos que o MovieLens joga para o fim do título: "Matrix, The (1999)"
TRAILING_ARTICLES = ["The", "A", "An", "La", "Le", "Les", "L'", "Il", "El", "Das", "Die", "Der"]


# ---------------------------------------------------------------------
# Transformações
# ---------------------------------------------------------------------
YEAR_PATTERN = re.compile(r"\s*\((\d{4})(?:[–-]\d{0,4})?\)\s*$")
# Também cobre títulos com nome alternativo: "City of Lost Children, The (Cité des...)"
ARTICLE_PATTERN = re.compile(
    r"^(.*?), (" + "|".join(re.escape(a) for a in TRAILING_ARTICLES) + r")(\s\(.*\))?$"
)


def split_title_year(raw_title: str) -> tuple[str, int | None]:
    """'Toy Story (1995)' -> ('Toy Story', 1995). Filmes sem ano ficam com None."""
    title = raw_title.strip()
    match = YEAR_PATTERN.search(title)
    year = None
    if match:
        year = int(match.group(1))
        title = title[: match.start()].strip()

    article = ARTICLE_PATTERN.match(title)
    if article:
        name, art, alt = article.groups()
        sep = "" if art.endswith("'") else " "
        title = f"{art}{sep}{name}{alt or ''}"

    return title, year


def simulate_duration(genres: list[str], rng: np.random.Generator) -> int:
    means = [GENRE_AVG_DURATION.get(g, DEFAULT_DURATION) for g in genres]
    mean = float(np.mean(means)) if means else DEFAULT_DURATION
    duration = rng.normal(loc=mean, scale=14)
    return int(np.clip(round(duration), 70, 200))


def build_dataframes(movies_raw: pd.DataFrame):
    rng = np.random.default_rng(SEED)

    movies_raw = movies_raw.copy()
    movies_raw["genre_list"] = movies_raw["genres"].apply(
        lambda g: [] if g == "(no genres listed)" else g.split("|")
    )

    parsed = movies_raw["title"].apply(split_title_year)
    movies = pd.DataFrame({
        "movie_id": movies_raw["movieId"].astype(int),
        "title": parsed.str[0],
        "release_year": parsed.str[1].astype("Int64"),
        "duration_min": [simulate_duration(g, rng) for g in movies_raw["genre_list"]],
    })

    genre_names = sorted({g for gl in movies_raw["genre_list"] for g in gl})
    genres = pd.DataFrame({"genre_name": genre_names})

    movie_genres = (
        movies_raw[["movieId", "genre_list"]]
        .explode("genre_list")
        .dropna(subset=["genre_list"])
        .rename(columns={"movieId": "movie_id", "genre_list": "genre_name"})
    )

    return movies, genres, movie_genres


# ---------------------------------------------------------------------
# Carga
# ---------------------------------------------------------------------
def load(movies: pd.DataFrame, genres: pd.DataFrame, movie_genres: pd.DataFrame) -> None:
    engine = create_engine(CONNECTION_URL, fast_executemany=True)

    with engine.begin() as conn:
        # Proteção: o catálogo não pode ser recarregado depois que existirem sessões/avaliações
        used = conn.execute(text(
            "SELECT (SELECT COUNT(*) FROM app.viewing_sessions) + (SELECT COUNT(*) FROM app.ratings)"
        )).scalar()
        if used:
            raise RuntimeError(
                "Já existem sessões/avaliações apontando para o catálogo. "
                "Recarregar agora quebraria as FKs; abortando."
            )

        # Limpa para a carga ser idempotente
        conn.execute(text("DELETE FROM app.movie_genres"))
        conn.execute(text("DELETE FROM app.movies"))
        conn.execute(text("DELETE FROM app.genres"))
        conn.execute(text("DBCC CHECKIDENT ('app.genres', RESEED, 0)"))

        genres.to_sql("genres", conn, schema="app", if_exists="append", index=False)

        genre_ids = pd.read_sql(text("SELECT genre_id, genre_name FROM app.genres"), conn)
        mg = movie_genres.merge(genre_ids, on="genre_name", how="inner")[["movie_id", "genre_id"]]

        movies.to_sql("movies", conn, schema="app", if_exists="append", index=False, chunksize=1000)
        mg.to_sql("movie_genres", conn, schema="app", if_exists="append", index=False, chunksize=2000)

        # Conferência
        summary = pd.read_sql(text("""
            SELECT 'movies' AS tabela, COUNT(*) AS linhas FROM app.movies
            UNION ALL SELECT 'genres', COUNT(*) FROM app.genres
            UNION ALL SELECT 'movie_genres', COUNT(*) FROM app.movie_genres
        """), conn)

    print("\nCarga concluída:")
    print(summary.to_string(index=False))


def main() -> None:
    if not MOVIES_CSV.exists():
        raise FileNotFoundError(f"Arquivo não encontrado: {MOVIES_CSV}")

    movies_raw = pd.read_csv(MOVIES_CSV)
    movies, genres, movie_genres = build_dataframes(movies_raw)

    sem_ano = movies["release_year"].isna().sum()
    print(f"Filmes lidos: {len(movies)} | sem ano no título: {sem_ano}")
    print(f"Duração simulada: média {movies['duration_min'].mean():.0f} min "
          f"(min {movies['duration_min'].min()}, max {movies['duration_min'].max()})")

    load(movies, genres, movie_genres)


if __name__ == "__main__":
    main()
