/* =====================================================================
   CineStream - Banco de origem (OLTP)
   Passo 2: criação do banco, schema e tabelas
   Executar conectado em: master
   ===================================================================== */

IF DB_ID('CineStream') IS NULL
    CREATE DATABASE CineStream;
GO

USE CineStream;
GO

IF SCHEMA_ID('app') IS NULL
    EXEC('CREATE SCHEMA app');
GO

/* ---------------------------------------------------------------------
   Planos
   --------------------------------------------------------------------- */
CREATE TABLE app.plans (
    plan_id        INT IDENTITY(1,1) PRIMARY KEY,
    plan_name      VARCHAR(30)    NOT NULL UNIQUE,
    monthly_price  DECIMAL(10,2)  NOT NULL CHECK (monthly_price > 0),
    max_screens    TINYINT        NOT NULL,
    video_quality  VARCHAR(10)    NOT NULL CHECK (video_quality IN ('HD', 'FULL_HD', '4K')),
    is_active      BIT            NOT NULL DEFAULT 1,
    created_at     DATETIME2(0)   NOT NULL DEFAULT SYSDATETIME(),
    updated_at     DATETIME2(0)   NOT NULL DEFAULT SYSDATETIME()
);

/* ---------------------------------------------------------------------
   Usuários
   --------------------------------------------------------------------- */
CREATE TABLE app.users (
    user_id     INT IDENTITY(1,1) PRIMARY KEY,
    full_name   VARCHAR(120)  NOT NULL,
    email       VARCHAR(150)  NOT NULL UNIQUE,
    birth_date  DATE          NULL,
    state_code  CHAR(2)       NOT NULL,          -- UF: SP, RJ, SC...
    city        VARCHAR(80)   NULL,
    signup_at   DATETIME2(0)  NOT NULL,
    created_at  DATETIME2(0)  NOT NULL DEFAULT SYSDATETIME(),
    updated_at  DATETIME2(0)  NOT NULL DEFAULT SYSDATETIME()
);

/* ---------------------------------------------------------------------
   Assinaturas: cada linha = um período em um plano.
   Troca de plano fecha a linha atual (end_reason = 'plan_change')
   e abre uma nova. Cancelamento: end_reason = 'canceled'.
   --------------------------------------------------------------------- */
CREATE TABLE app.subscriptions (
    subscription_id  INT IDENTITY(1,1) PRIMARY KEY,
    user_id          INT           NOT NULL REFERENCES app.users(user_id),
    plan_id          INT           NOT NULL REFERENCES app.plans(plan_id),
    status           VARCHAR(10)   NOT NULL CHECK (status IN ('active', 'ended')),
    started_at       DATETIME2(0)  NOT NULL,
    ended_at         DATETIME2(0)  NULL,
    end_reason       VARCHAR(20)   NULL CHECK (end_reason IN ('canceled', 'plan_change', 'payment_failed')),
    created_at       DATETIME2(0)  NOT NULL DEFAULT SYSDATETIME(),
    updated_at       DATETIME2(0)  NOT NULL DEFAULT SYSDATETIME(),
    CONSTRAINT ck_subscriptions_end CHECK (
        (status = 'active' AND ended_at IS NULL AND end_reason IS NULL)
     OR (status = 'ended'  AND ended_at IS NOT NULL AND end_reason IS NOT NULL AND ended_at >= started_at)
    )
);

CREATE INDEX ix_subscriptions_user ON app.subscriptions(user_id);

/* ---------------------------------------------------------------------
   Pagamentos: uma cobrança por assinatura por mês de referência
   --------------------------------------------------------------------- */
CREATE TABLE app.payments (
    payment_id       INT IDENTITY(1,1) PRIMARY KEY,
    subscription_id  INT            NOT NULL REFERENCES app.subscriptions(subscription_id),
    reference_month  DATE           NOT NULL,      -- sempre dia 1 do mês
    amount           DECIMAL(10,2)  NOT NULL CHECK (amount >= 0),
    payment_method   VARCHAR(15)    NOT NULL CHECK (payment_method IN ('credit_card', 'pix', 'boleto')),
    status           VARCHAR(10)    NOT NULL CHECK (status IN ('paid', 'failed', 'refunded')),
    paid_at          DATETIME2(0)   NULL,
    created_at       DATETIME2(0)   NOT NULL DEFAULT SYSDATETIME(),
    updated_at       DATETIME2(0)   NOT NULL DEFAULT SYSDATETIME(),
    CONSTRAINT uq_payments_sub_month UNIQUE (subscription_id, reference_month)
);

/* ---------------------------------------------------------------------
   Catálogo (MovieLens)
   --------------------------------------------------------------------- */
CREATE TABLE app.genres (
    genre_id    INT IDENTITY(1,1) PRIMARY KEY,
    genre_name  VARCHAR(40)  NOT NULL UNIQUE,
    created_at  DATETIME2(0) NOT NULL DEFAULT SYSDATETIME(),
    updated_at  DATETIME2(0) NOT NULL DEFAULT SYSDATETIME()
);

CREATE TABLE app.movies (
    movie_id      INT           NOT NULL PRIMARY KEY,   -- mesmo id do MovieLens
    title         VARCHAR(300)  NOT NULL,
    release_year  SMALLINT      NULL,
    duration_min  SMALLINT      NOT NULL CHECK (duration_min > 0),  -- simulado
    created_at    DATETIME2(0)  NOT NULL DEFAULT SYSDATETIME(),
    updated_at    DATETIME2(0)  NOT NULL DEFAULT SYSDATETIME()
);

CREATE TABLE app.movie_genres (
    movie_id  INT NOT NULL REFERENCES app.movies(movie_id),
    genre_id  INT NOT NULL REFERENCES app.genres(genre_id),
    CONSTRAINT pk_movie_genres PRIMARY KEY (movie_id, genre_id)
);

/* ---------------------------------------------------------------------
   Sessões de visualização
   --------------------------------------------------------------------- */
CREATE TABLE app.viewing_sessions (
    session_id       BIGINT IDENTITY(1,1) PRIMARY KEY,
    user_id          INT           NOT NULL REFERENCES app.users(user_id),
    movie_id         INT           NOT NULL REFERENCES app.movies(movie_id),
    device_type      VARCHAR(10)   NOT NULL CHECK (device_type IN ('tv', 'mobile', 'notebook', 'tablet')),
    started_at       DATETIME2(0)  NOT NULL,
    ended_at         DATETIME2(0)  NOT NULL,
    watched_seconds  INT           NOT NULL CHECK (watched_seconds >= 0),
    progress_pct     DECIMAL(5,2)  NOT NULL CHECK (progress_pct BETWEEN 0 AND 100),
    created_at       DATETIME2(0)  NOT NULL DEFAULT SYSDATETIME(),
    updated_at       DATETIME2(0)  NOT NULL DEFAULT SYSDATETIME(),
    CONSTRAINT ck_sessions_time CHECK (ended_at >= started_at)
);

CREATE INDEX ix_sessions_user    ON app.viewing_sessions(user_id);
CREATE INDEX ix_sessions_movie   ON app.viewing_sessions(movie_id);
CREATE INDEX ix_sessions_updated ON app.viewing_sessions(updated_at);

/* ---------------------------------------------------------------------
   Avaliações: uma nota por usuário por filme (pode ser atualizada)
   --------------------------------------------------------------------- */
CREATE TABLE app.ratings (
    rating_id   INT IDENTITY(1,1) PRIMARY KEY,
    user_id     INT           NOT NULL REFERENCES app.users(user_id),
    movie_id    INT           NOT NULL REFERENCES app.movies(movie_id),
    rating      DECIMAL(2,1)  NOT NULL CHECK (rating BETWEEN 0.5 AND 5.0),
    rated_at    DATETIME2(0)  NOT NULL,
    created_at  DATETIME2(0)  NOT NULL DEFAULT SYSDATETIME(),
    updated_at  DATETIME2(0)  NOT NULL DEFAULT SYSDATETIME(),
    CONSTRAINT uq_ratings_user_movie UNIQUE (user_id, movie_id)
);
GO

/* ---------------------------------------------------------------------
   Planos fixos (dado de referência)
   --------------------------------------------------------------------- */
INSERT INTO app.plans (plan_name, monthly_price, max_screens, video_quality) VALUES
    ('Basico',  24.90, 1, 'HD'),
    ('Padrao',  39.90, 2, 'FULL_HD'),
    ('Premium', 59.90, 4, '4K');
GO

/* Conferência */
SELECT s.name AS schema_name, t.name AS table_name
FROM sys.tables t
JOIN sys.schemas s ON s.schema_id = t.schema_id
WHERE s.name = 'app'
ORDER BY t.name;
