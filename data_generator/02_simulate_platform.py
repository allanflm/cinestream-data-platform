"""
CineStream - Passo 4: simulação da operação da plataforma

Gera, de forma reprodutível (seed fixa), o histórico de jan/2025 até "hoje":
  - usuários           cadastros crescendo mês a mês, com picos em férias
  - assinaturas        churn maior nos primeiros meses, trocas de plano e reativações
  - pagamentos         uma cobrança por ciclo; falhas encerram a assinatura
  - sessões            pico à noite, mais no fim de semana, filmes populares mais vistos
  - avaliações         notas coerentes com a qualidade do filme

Comportamentos embutidos de propósito (para o dashboard ter o que mostrar):
  - quem vai cancelar assiste bem menos nos 30 dias anteriores
  - quem assiste pouco cancela mais
  - filmes bem avaliados têm mais conclusão; no celular se abandona mais

Uso (na raiz do projeto, com o .venv ativo e o catálogo já carregado):
  python data_generator/02_simulate_platform.py
"""

import time
import unicodedata
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
from dateutil.relativedelta import relativedelta
from sqlalchemy import text

from db import MOVIELENS_DIR, get_engine, reset_identity

# =====================================================================
# Parâmetros da simulação
# =====================================================================
SEED = 42
START = datetime(2025, 1, 1)
END = datetime(2026, 10, 21, 23, 59, 59)  # "hoje" dentro da simulação
N_USERS = 50_000

PLAN_MIX = {"Basico": 0.45, "Padrao": 0.38, "Premium": 0.17}
PLAN_ORDER = ["Basico", "Padrao", "Premium"]
PLAN_SESSION_FACTOR = {"Basico": 0.85, "Padrao": 1.0, "Premium": 1.25}
PLAN_CHURN_FACTOR = {"Basico": 1.2, "Padrao": 1.0, "Premium": 0.85}

PLAN_CHANGE_RATE = 0.03        # por ciclo
UPGRADE_SHARE = 0.65           # das trocas, quantas são upgrade
PAYMENT_FAIL_RATE = 0.012      # falha encerra a assinatura (churn involuntário)
REFUND_RATE = 0.004
WINBACK_RATE = 0.15            # quem cancelou e volta depois de 1 a 6 meses

SESSIONS_PER_WEEK_MEAN = 1.6   # engajamento médio; cada usuário tem o seu
PRE_CHURN_FACTOR = 0.4         # queda de uso nos 30 dias antes de cancelar
FAVORITE_GENRE_SHARE = 0.5     # parte das sessões que vem dos gêneros favoritos
RATING_RATE_COMPLETED = 0.15
RATING_RATE_ABANDONED = 0.03

DEVICES = ["tv", "mobile", "notebook", "tablet"]
DEVICE_MIX = [0.50, 0.25, 0.18, 0.07]
PRIMARY_DEVICE_SHARE = 0.7
PAYMENT_METHODS = ["credit_card", "pix", "boleto"]
PAYMENT_MIX = [0.70, 0.22, 0.08]

# Sazonalidade
DOW_WEIGHT = np.array([0.85, 0.85, 0.90, 0.95, 1.10, 1.25, 1.20])  # seg..dom
MONTH_WEIGHT = {1: 1.15, 2: 0.95, 3: 0.95, 4: 0.95, 5: 0.95, 6: 1.0,
                7: 1.15, 8: 0.95, 9: 0.95, 10: 1.0, 11: 1.0, 12: 1.2}
SIGNUP_MONTH_WEIGHT = {1: 1.25, 7: 1.15, 12: 1.2}

HOUR_WEEKDAY = np.array([0.6, 0.3, 0.15, 0.1, 0.1, 0.1, 0.2, 0.4, 0.5, 0.6, 0.7, 0.9,
                         1.3, 1.2, 0.9, 0.9, 1.0, 1.3, 2.0, 2.8, 3.8, 4.2, 3.6, 1.8])
HOUR_WEEKEND = np.array([1.0, 0.6, 0.35, 0.2, 0.1, 0.1, 0.15, 0.3, 0.6, 1.0, 1.4, 1.7,
                         1.9, 2.0, 2.2, 2.3, 2.3, 2.3, 2.5, 2.9, 3.5, 3.8, 3.4, 2.2])

# Estados com peso aproximado pela população
STATES = {
    "SP": (21.9, "São Paulo"), "MG": (10.0, "Belo Horizonte"), "RJ": (8.1, "Rio de Janeiro"),
    "BA": (6.9, "Salvador"), "PR": (5.6, "Curitiba"), "RS": (5.3, "Porto Alegre"),
    "PE": (4.5, "Recife"), "CE": (4.3, "Fortaleza"), "PA": (4.0, "Belém"),
    "SC": (3.7, "Florianópolis"), "GO": (3.5, "Goiânia"), "MA": (3.3, "São Luís"),
    "AM": (1.9, "Manaus"), "ES": (1.9, "Vitória"), "PB": (1.9, "João Pessoa"),
    "MT": (1.8, "Cuiabá"), "RN": (1.6, "Natal"), "PI": (1.6, "Teresina"),
    "AL": (1.5, "Maceió"), "DF": (1.4, "Brasília"), "MS": (1.4, "Campo Grande"),
    "SE": (1.1, "Aracaju"), "RO": (0.8, "Porto Velho"), "TO": (0.7, "Palmas"),
    "AC": (0.4, "Rio Branco"), "AP": (0.4, "Macapá"), "RR": (0.3, "Boa Vista"),
}

FIRST_NAMES = [
    "Ana", "Maria", "Juliana", "Fernanda", "Camila", "Beatriz", "Larissa", "Gabriela", "Mariana", "Letícia",
    "Patrícia", "Aline", "Bruna", "Carolina", "Amanda", "Vanessa", "Luana", "Isabela", "Rafaela", "Natália",
    "Sofia", "Helena", "Alice", "Laura", "Manuela", "Valentina", "Júlia", "Lívia", "Clara", "Débora",
    "João", "José", "Pedro", "Lucas", "Gabriel", "Rafael", "Mateus", "Gustavo", "Felipe", "Bruno",
    "Rodrigo", "Thiago", "Leonardo", "Eduardo", "Marcelo", "Daniel", "Vinícius", "André", "Diego", "Carlos",
    "Miguel", "Arthur", "Heitor", "Davi", "Bernardo", "Samuel", "Enzo", "Murilo", "Caio", "Henrique",
]
LAST_NAMES = [
    "Silva", "Santos", "Oliveira", "Souza", "Rodrigues", "Ferreira", "Alves", "Pereira", "Lima", "Gomes",
    "Costa", "Ribeiro", "Martins", "Carvalho", "Almeida", "Lopes", "Soares", "Fernandes", "Vieira", "Barbosa",
    "Rocha", "Dias", "Nascimento", "Andrade", "Moreira", "Nunes", "Marques", "Machado", "Mendes", "Freitas",
    "Cardoso", "Ramos", "Gonçalves", "Santana", "Teixeira", "Araújo", "Pinto", "Correia", "Moura", "Cavalcanti",
]
EMAIL_DOMAINS = ["gmail.com", "hotmail.com", "outlook.com", "yahoo.com.br", "uol.com.br", "icloud.com"]
EMAIL_MIX = [0.55, 0.15, 0.12, 0.08, 0.05, 0.05]


# =====================================================================
# Utilitários
# =====================================================================
def ascii_slug(s: str) -> str:
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()


def weighted_hours(rng, n, weights):
    return rng.choice(24, size=n, p=weights / weights.sum())


def step(msg):
    print(f"  {msg}", flush=True)


# =====================================================================
# 1. Usuários
# =====================================================================
def gen_users(rng):
    months = pd.period_range(START, END, freq="M")
    growth = np.linspace(1.0, 3.0, len(months))
    season = np.array([SIGNUP_MONTH_WEIGHT.get(p.month, 1.0) for p in months])
    w = growth * season
    w[-1] *= END.day / months[-1].days_in_month  # último mês ainda está correndo
    counts = rng.multinomial(N_USERS, w / w.sum())

    days = []
    for p, c in zip(months, counts):
        last_day = min(p.days_in_month, END.day if p == months[-1] else p.days_in_month)
        days.append(p.start_time + pd.to_timedelta(rng.integers(0, last_day, c), unit="D"))
    days = pd.DatetimeIndex(np.concatenate(days))
    dow = days.dayofweek.values
    hours = np.where(dow >= 5, weighted_hours(rng, len(days), HOUR_WEEKEND), weighted_hours(rng, len(days), HOUR_WEEKDAY))
    signup = days + pd.to_timedelta(hours, unit="h") + pd.to_timedelta(rng.integers(0, 3600, len(days)), unit="s")
    signup = np.sort(signup.values)
    signup = pd.to_datetime(signup)
    signup = signup.where(signup <= END, pd.Timestamp(END))

    n = len(signup)
    first = rng.choice(FIRST_NAMES, n)
    last1 = rng.choice(LAST_NAMES, n)
    last2 = rng.choice(LAST_NAMES, n)
    user_id = np.arange(1, n + 1)
    domains = rng.choice(EMAIL_DOMAINS, n, p=EMAIL_MIX)
    emails = [f"{ascii_slug(f)}.{ascii_slug(l)}{uid}@{d}" for f, l, uid, d in zip(first, last2, user_id, domains)]

    age_years = np.clip(rng.normal(34, 11, n), 18, 75)
    birth = (signup - pd.to_timedelta(age_years * 365.25, unit="D")).normalize()

    codes = list(STATES)
    pw = np.array([STATES[c][0] for c in codes])
    state = rng.choice(codes, n, p=pw / pw.sum())

    users = pd.DataFrame({
        "user_id": user_id,
        "full_name": [f"{f} {a} {b}" for f, a, b in zip(first, last1, last2)],
        "email": emails,
        "birth_date": birth.date,
        "state_code": state,
        "city": [STATES[s][1] for s in state],
        "signup_at": signup,
    })
    users["created_at"] = users["signup_at"]
    users["updated_at"] = users["signup_at"]

    # Traços de comportamento (não vão para o banco)
    traits = pd.DataFrame({
        "lam": rng.gamma(shape=2.0, scale=SESSIONS_PER_WEEK_MEAN / 2.0, size=n),  # sessões/semana
        "device": rng.choice(len(DEVICES), n, p=DEVICE_MIX),
        "pay_method": rng.choice(len(PAYMENT_METHODS), n, p=PAYMENT_MIX),
        "bias": rng.normal(0, 0.4, n),  # usuário mais "generoso" ou mais "chato" nas notas
    })
    return users, traits


# =====================================================================
# 2. Assinaturas e pagamentos
# =====================================================================
def churn_prob(tenure, plan, lam):
    base = 0.03 + 0.06 * np.exp(-(tenure - 1) / 2.5)  # ~9% no 1º mês, ~3,5% depois de 1 ano
    engagement = 1.4 if lam < 0.7 else (0.7 if lam > 2.5 else 1.0)
    return base * PLAN_CHURN_FACTOR[plan] * engagement


def gen_subscriptions(rng, users, traits, plans):
    price = dict(zip(plans["plan_name"], plans["monthly_price"].astype(float)))
    plan_names = list(PLAN_MIX)
    plan_p = list(PLAN_MIX.values())

    subs, pays = [], []
    sub_id = 0
    signup = users["signup_at"].dt.to_pydatetime()
    lam_arr = traits["lam"].values
    method_arr = traits["pay_method"].values
    r = rng.random

    for u in range(len(users)):
        uid = int(users["user_id"].iat[u])
        lam = lam_arr[u]
        t = signup[u]
        plan = plan_names[rng.choice(3, p=plan_p)]
        tenure = 0

        while t <= END:
            sub_id += 1
            start = t
            k = 0
            ended, reason, next_plan = None, None, None
            while True:
                charge = start + relativedelta(months=k)
                k += 1
                tenure += 1
                # --- cobrança do ciclo
                if k > 1 and r() < PAYMENT_FAIL_RATE:
                    pays.append((sub_id, charge, price[plan], method_arr[u], "failed", None))
                    ended, reason = charge, "payment_failed"
                    break
                status = "refunded" if r() < REFUND_RATE else "paid"
                m = method_arr[u]
                delay = (timedelta(seconds=float(rng.integers(5, 300))) if m == 0
                         else timedelta(hours=float(r() * 6)) if m == 1
                         else timedelta(days=float(1 + r() * 2)))
                pays.append((sub_id, charge, price[plan], m, status, min(charge + delay, END)))
                # --- decisões no fim do ciclo
                nxt = start + relativedelta(months=k)
                if nxt > END:
                    break
                if r() < churn_prob(tenure, plan, lam):
                    ended, reason = nxt, "canceled"
                    break
                if r() < PLAN_CHANGE_RATE:
                    idx = PLAN_ORDER.index(plan)
                    up = r() < UPGRADE_SHARE
                    if up and idx < 2:
                        next_plan = PLAN_ORDER[idx + 1]
                    elif not up and idx > 0:
                        next_plan = PLAN_ORDER[idx - 1]
                    if next_plan:
                        ended, reason = nxt, "plan_change"
                        break

            subs.append((sub_id, uid, plan, start, ended, reason))

            if reason == "plan_change":
                t, plan = ended, next_plan
            elif reason == "canceled" and r() < WINBACK_RATE:
                t = ended + timedelta(days=float(rng.integers(30, 181)), hours=float(r() * 12))
                plan = plan_names[rng.choice(3, p=plan_p)]
                tenure = 0
            else:
                break

    subs = pd.DataFrame(subs, columns=["subscription_id", "user_id", "plan_name", "started_at", "ended_at", "end_reason"])
    subs["plan_id"] = subs["plan_name"].map(dict(zip(plans["plan_name"], plans["plan_id"])))
    subs["status"] = np.where(subs["ended_at"].isna(), "active", "ended")
    subs["created_at"] = subs["started_at"]
    subs["updated_at"] = subs["ended_at"].fillna(subs["started_at"])

    pays = pd.DataFrame(pays, columns=["subscription_id", "charged_at", "amount", "method_idx", "status", "paid_at"])
    pays["reference_month"] = pays["charged_at"].values.astype("datetime64[M]").astype("datetime64[ns]")
    pays["reference_month"] = pays["reference_month"].dt.date
    pays["payment_method"] = np.array(PAYMENT_METHODS)[pays["method_idx"].values]
    pays["created_at"] = pays["charged_at"]
    refund_at = pays["paid_at"] + pd.to_timedelta(rng.integers(1, 8, len(pays)), unit="D")
    pays["updated_at"] = np.where(pays["status"] == "refunded", refund_at.clip(upper=END), pays["paid_at"].fillna(pays["charged_at"]))
    pays["updated_at"] = pd.to_datetime(pays["updated_at"])
    return subs, pays


# =====================================================================
# 3. Sessões de visualização
# =====================================================================
def gen_sessions(rng, users, traits, subs, catalog, genre_movies):
    day0 = np.datetime64(START.date())
    end_day = np.datetime64(END.date())
    all_days = np.arange(day0, end_day + np.timedelta64(1, "D"))
    dow = (all_days.astype("datetime64[D]").view("int64") - 4) % 7  # 1970-01-01 foi quinta
    months = all_days.astype("datetime64[M]").astype(int) % 12 + 1
    day_w = DOW_WEIGHT[dow] * np.array([MONTH_WEIGHT[m] for m in months])

    lam = traits["lam"].values
    user_idx_of = dict(zip(users["user_id"].values, range(len(users))))

    s_user, s_day, s_sub_start, s_sub_end = [], [], [], []
    starts = subs["started_at"].values.astype("datetime64[s]")
    ends = subs["ended_at"].fillna(pd.Timestamp(END)).values.astype("datetime64[s]")
    reasons = subs["end_reason"].values
    plan_names = subs["plan_name"].values
    uids = subs["user_id"].values

    for i in range(len(subs)):
        a = (starts[i].astype("datetime64[D]") - day0).astype(int)
        b = (ends[i].astype("datetime64[D]") - day0).astype(int)
        if b < a:
            continue
        w = day_w[a:b + 1].copy()
        if reasons[i] in ("canceled", "payment_failed"):
            w[-30:] *= PRE_CHURN_FACTOR
        u = user_idx_of[uids[i]]
        expected = lam[u] / 7.0 * PLAN_SESSION_FACTOR[plan_names[i]] * w.sum()
        n = rng.poisson(expected)
        if n == 0:
            continue
        d = rng.choice(np.arange(a, b + 1), size=n, p=w / w.sum())
        s_user.append(np.full(n, u))
        s_day.append(d)
        s_sub_start.append(np.full(n, starts[i]))
        s_sub_end.append(np.full(n, ends[i]))

    u = np.concatenate(s_user)
    d = np.concatenate(s_day)
    sub_start = np.concatenate(s_sub_start)
    sub_end = np.concatenate(s_sub_end)
    n = len(u)

    # horário
    weekend = dow[d] >= 5
    hours = np.where(weekend, weighted_hours(rng, n, HOUR_WEEKEND), weighted_hours(rng, n, HOUR_WEEKDAY))
    started = (day0 + d).astype("datetime64[s]") + (hours * 3600 + rng.integers(0, 3600, n)).astype("timedelta64[s]")
    keep = (started >= sub_start) & (started <= sub_end)

    # filme: metade vem dos gêneros favoritos do usuário, o resto da popularidade geral
    movie_ids = catalog["movie_id"].values
    pop = catalog["popularity"].values
    movie = rng.choice(movie_ids, size=n, p=pop / pop.sum())
    fav_genres = traits[["fav1", "fav2"]].values
    from_fav = rng.random(n) < FAVORITE_GENRE_SHARE
    chosen_genre = np.where(rng.random(n) < 0.5, fav_genres[u, 0], fav_genres[u, 1])
    for g, (g_movies, g_p) in genre_movies.items():
        mask = from_fav & (chosen_genre == g)
        if mask.any():
            movie[mask] = rng.choice(g_movies, size=mask.sum(), p=g_p)

    # dispositivo: 70% no aparelho preferido
    device = np.where(rng.random(n) < PRIMARY_DEVICE_SHARE, traits["device"].values[u],
                      rng.choice(len(DEVICES), n, p=DEVICE_MIX))

    # quanto assistiu
    cat = catalog.set_index("movie_id")
    quality = cat.loc[movie, "quality"].values
    duration = cat.loc[movie, "duration_min"].values
    p_complete = np.clip(0.55 + 0.25 * (quality - 3.5), 0.25, 0.85) - np.where(device == 1, 0.08, 0.0)
    roll = rng.random(n)
    completed = roll < p_complete
    early = (~completed) & (rng.random(n) < 0.55)
    progress = np.where(completed, np.where(rng.random(n) < 0.5, 100.0, rng.uniform(92, 100, n)),
                        np.where(early, rng.uniform(2, 30, n), rng.uniform(30, 92, n)))
    progress = np.round(progress, 2)
    watched = np.round(progress / 100 * duration * 60).astype(np.int64)
    wall = (watched * (1 + rng.uniform(0, 0.15, n))).astype("timedelta64[s]")  # pausas
    ended = started + wall
    keep &= ended <= np.datetime64(END)

    sessions = pd.DataFrame({
        "user_id": users["user_id"].values[u],
        "movie_id": movie,
        "device_type": np.array(DEVICES)[device],
        "started_at": started,
        "ended_at": ended,
        "watched_seconds": watched,
        "progress_pct": progress,
        "_quality": quality,
        "_user_idx": u,
    })[keep]
    sessions = sessions.sort_values("started_at", kind="stable").reset_index(drop=True)
    sessions["created_at"] = sessions["ended_at"]
    sessions["updated_at"] = sessions["ended_at"]
    return sessions


# =====================================================================
# 4. Avaliações
# =====================================================================
def gen_ratings(rng, sessions, traits):
    n = len(sessions)
    completed = sessions["progress_pct"].values >= 90
    p = np.where(completed, RATING_RATE_COMPLETED, RATING_RATE_ABANDONED)
    rated = sessions[rng.random(n) < p].copy()

    bias = traits["bias"].values[rated["_user_idx"].values]
    penalty = np.where(rated["progress_pct"].values >= 90, 0.0, -1.0)
    value = rated["_quality"].values + bias + penalty + rng.normal(0, 0.6, len(rated))
    rated["rating"] = np.clip(np.round(value * 2) / 2, 0.5, 5.0)
    rated["rated_at"] = rated["ended_at"] + pd.to_timedelta(rng.integers(0, 1200, len(rated)), unit="s")
    rated = rated[rated["rated_at"] <= END]
    rated = rated.drop_duplicates(["user_id", "movie_id"], keep="first")  # uma nota por usuário/filme

    ratings = rated[["user_id", "movie_id", "rating", "rated_at"]].copy()
    ratings["created_at"] = ratings["rated_at"]
    ratings["updated_at"] = ratings["rated_at"]
    return ratings.reset_index(drop=True)


# =====================================================================
# Orquestração
# =====================================================================
def prepare_catalog(movies, movie_genres, ml_ratings):
    """Popularidade e 'qualidade' de cada filme vêm das avaliações reais do MovieLens."""
    stats = ml_ratings.groupby("movieId")["rating"].agg(["count", "sum"])
    catalog = movies.merge(stats, left_on="movie_id", right_index=True, how="left").fillna({"count": 0, "sum": 0})
    catalog["popularity"] = catalog["count"] + 1.0
    catalog["quality"] = (catalog["sum"] + 3.5 * 10) / (catalog["count"] + 10)  # média "encolhida" para 3,5

    genre_movies = {}
    for g, grp in movie_genres.merge(catalog[["movie_id", "popularity"]], on="movie_id").groupby("genre_id"):
        genre_movies[g] = (grp["movie_id"].values, (grp["popularity"] / grp["popularity"].sum()).values)
    genre_weight = movie_genres.merge(catalog[["movie_id", "popularity"]], on="movie_id").groupby("genre_id")["popularity"].sum()
    return catalog, genre_movies, genre_weight


def simulate(movies, movie_genres, ml_ratings, plans):
    rng = np.random.default_rng(SEED)
    t0 = time.time()
    catalog, genre_movies, genre_weight = prepare_catalog(movies, movie_genres, ml_ratings)

    step("👤 usuários...")
    users, traits = gen_users(rng)
    gw = genre_weight.values / genre_weight.values.sum()
    favs = np.array([rng.choice(genre_weight.index.values, 2, replace=False, p=gw) for _ in range(len(users))])
    traits["fav1"], traits["fav2"] = favs[:, 0], favs[:, 1]

    step("💳 assinaturas e pagamentos...")
    subs, pays = gen_subscriptions(rng, users, traits, plans)

    step("🎬 sessões de visualização...")
    sessions = gen_sessions(rng, users, traits, subs, catalog, genre_movies)

    step("⭐ avaliações...")
    ratings = gen_ratings(rng, sessions, traits)

    step(f"simulação pronta em {time.time() - t0:.0f}s")
    return users, subs, pays, sessions, ratings


def print_summary(users, subs, pays, sessions, ratings):
    print("\n  Resumo da simulação")
    print(f"    usuários ............ {len(users):>10,}")
    print(f"    assinaturas ......... {len(subs):>10,}  (ativas hoje: {(subs['status'] == 'active').sum():,})")
    print(f"    pagamentos .......... {len(pays):>10,}")
    print(f"    sessões ............. {len(sessions):>10,}")
    print(f"    avaliações .......... {len(ratings):>10,}")
    canceled = subs[subs["end_reason"] == "canceled"]
    months = pd.period_range(START, END, freq="M")
    churn = []
    for p in months[:-1]:
        active_start = ((subs["started_at"] < p.start_time) & (subs["ended_at"].isna() | (subs["ended_at"] >= p.start_time))).sum()
        lost = ((canceled["ended_at"] >= p.start_time) & (canceled["ended_at"] <= p.end_time)).sum()
        if active_start:
            churn.append(lost / active_start)
    print(f"    churn mensal médio .. {np.mean(churn[3:]):>10.1%}")
    print(f"    taxa de conclusão ... {(sessions['progress_pct'] >= 90).mean():>10.1%}")


def reset_tables(conn):
    for t in ["app.ratings", "app.viewing_sessions", "app.payments", "app.subscriptions", "app.users"]:
        conn.execute(text(f"DELETE FROM {t}"))
    for t in ["app.ratings", "app.viewing_sessions", "app.payments"]:
        reset_identity(conn, t)


def insert(engine, df, table, explicit_id=False, chunksize=50_000):
    t0 = time.time()
    with engine.begin() as conn:
        if explicit_id:
            conn.execute(text(f"SET IDENTITY_INSERT app.{table} ON"))
        df.to_sql(table, conn, schema="app", if_exists="append", index=False, chunksize=chunksize)
        if explicit_id:
            conn.execute(text(f"SET IDENTITY_INSERT app.{table} OFF"))
    step(f"✅ app.{table:<17} {len(df):>10,} linhas  ({time.time() - t0:.0f}s)")


def floor_seconds(df, cols):
    for c in cols:
        df[c] = pd.to_datetime(df[c]).dt.floor("s")
    return df


def main():
    engine = get_engine()
    with engine.connect() as conn:
        movies = pd.read_sql(text("SELECT movie_id, duration_min FROM app.movies"), conn)
        movie_genres = pd.read_sql(text("SELECT movie_id, genre_id FROM app.movie_genres"), conn)
        plans = pd.read_sql(text("SELECT plan_id, plan_name, monthly_price FROM app.plans"), conn)
    if movies.empty:
        raise RuntimeError("Catálogo vazio. Rode antes: python data_generator/01_load_movielens.py")
    ml_ratings = pd.read_csv(MOVIELENS_DIR / "ratings.csv", usecols=["movieId", "rating"])

    print("\n  🎲 Simulando a plataforma (seed fixa, sempre o mesmo resultado)")
    users, subs, pays, sessions, ratings = simulate(movies, movie_genres, ml_ratings, plans)
    print_summary(users, subs, pays, sessions, ratings)

    print("\n  💾 Gravando no SQL Server")
    with engine.begin() as conn:
        reset_tables(conn)

    insert(engine, floor_seconds(users, ["signup_at", "created_at", "updated_at"]), "users", explicit_id=True)
    insert(engine, floor_seconds(subs[["subscription_id", "user_id", "plan_id", "status", "started_at", "ended_at",
                                       "end_reason", "created_at", "updated_at"]].copy(),
                                 ["started_at", "ended_at", "created_at", "updated_at"]),
           "subscriptions", explicit_id=True)
    insert(engine, floor_seconds(pays[["subscription_id", "reference_month", "amount", "payment_method", "status",
                                       "paid_at", "created_at", "updated_at"]].copy(),
                                 ["paid_at", "created_at", "updated_at"]), "payments")
    insert(engine, floor_seconds(sessions[["user_id", "movie_id", "device_type", "started_at", "ended_at",
                                           "watched_seconds", "progress_pct", "created_at", "updated_at"]].copy(),
                                 ["started_at", "ended_at", "created_at", "updated_at"]), "viewing_sessions")
    insert(engine, floor_seconds(ratings, ["rated_at", "created_at", "updated_at"]), "ratings")
    print("\n  🎉 Pronto!\n")


if __name__ == "__main__":
    main()
