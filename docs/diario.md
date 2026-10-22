# Diário do projeto CineStream

Registro das decisões de cada passo: o que foi feito e **por quê**.

---

## Passo 0 — Definir o resultado

O projeto foi construído de trás para frente: primeiro o dashboard, depois as tabelas que ele exige.

- **v1:** 3 páginas no Power BI
  - Visão executiva: assinantes ativos, novos, cancelamentos, churn, MRR, ticket médio, receita por plano
  - Conteúdo: top filmes por horas assistidas, gêneros, taxa de conclusão, abandono, nota x popularidade
  - Comportamento: mapa de calor dia x hora, consumo por dispositivo e por estado
- **v2:** análise de coorte (retenção)

**Por quê:** definir o resultado antes evita modelar tabelas que ninguém vai usar e garante que a origem tenha o que o dashboard precisa.

## Passo 1 — Ambiente

- SQL Server 2022 rodando em **Docker** (`docker-compose.yml` com volume persistente)
- Cliente: extensão **mssql** do VS Code

**Por quê Docker:** a origem sobe com um único comando (`docker compose up -d`), não instala nada no sistema e qualquer pessoa reproduz o ambiente a partir do repositório.

**Problema encontrado:** o Docker Desktop exigia a feature *Virtual Machine Platform*. O comando `Enable-WindowsOptionalFeature` falhou no PowerShell 7 ("Classe não registrada"); resolvido com `dism.exe` + reinício do Windows + `wsl --update`.

**Um comando para subir tudo:** `.\dev.ps1` abre o Docker Desktop se estiver fechado, cria o `.venv` se não existir e sobe o SQL Server, só liberando quando o banco passa no *healthcheck*. `.\dev.ps1 down` desliga. Uma tarefa do VS Code (`runOn: folderOpen`) roda o script ao abrir o projeto.

- O caminho do Docker Desktop não era o padrão; o script usa `docker desktop start` (Docker 4.37+) e, como plano B, localiza o executável a partir do `docker.exe` no PATH.
- Scripts `.ps1` baixados pelo navegador são bloqueados pela política de execução (marca "veio da internet"); resolvido com `Unblock-File`.

## Passo 2 — Modelagem da origem (OLTP)

Banco `CineStream`, schema `app`, 9 tabelas: `plans`, `users`, `subscriptions`, `payments`, `genres`, `movies`, `movie_genres`, `viewing_sessions`, `ratings`.

Decisões:

1. **Modelo normalizado, não estrela.** A origem imita um sistema transacional real; o modelo dimensional fica para a camada Gold.
2. **Assinatura = um período em um plano.** Troca de plano fecha a linha atual (`end_reason = 'plan_change'`) e abre outra. Assim o histórico fica completo e o churn conta só quem realmente saiu (`end_reason = 'canceled'`).
3. **`updated_at` em todas as tabelas.** Permite carga incremental para o Databricks em vez de cópia completa.
4. **Restrições no banco** (`CHECK`, `UNIQUE`, FKs) para garantir que até os dados simulados respeitem as regras de negócio.
5. **Duração dos filmes simulada**, porque o MovieLens não traz essa informação, e ela é necessária para calcular o % assistido.

Script: `sql/01_create_schema.sql`

## Passo 3 — Catálogo de filmes (MovieLens)

Fonte: **MovieLens ml-latest-small** (GroupLens), `movies.csv`. Carga via Python (`pandas` + `SQLAlchemy` + `pyodbc`).

Resultado: **9.742 filmes, 19 gêneros, 22.050 vínculos** filme-gênero. 12 filmes sem ano no título.

Decisões:

1. **Título limpo na origem.** O MovieLens grava "Matrix, The (1999)"; o script separa o ano e devolve o artigo ao início ("The Matrix"), inclusive em títulos com nome alternativo entre parênteses.
2. **Duração simulada por gênero**, com distribuição normal em torno de uma média por gênero (animação ~90 min, guerra ~130 min), limitada entre 70 e 200 min. Resultado: média de 110 min.
3. **Seed fixa (42).** Quem clonar o repositório gera exatamente o mesmo catálogo.
4. **Carga idempotente com proteção.** O script limpa e recarrega o catálogo, mas aborta se já existirem sessões ou avaliações, para não quebrar as FKs.
5. **`data/` fora do Git.** Dado bruto não vai para o repositório; o README explica de onde baixar.

Script: `data_generator/01_load_movielens.py`

**Bug encontrado depois:** `DBCC CHECKIDENT ... RESEED 0` numa tabela que nunca recebeu linhas faz o primeiro IDENTITY ser **0**, não 1, e os gêneros nasceram com `genre_id` começando em 0. Corrigido com uma função `reset_identity` (em `data_generator/db.py`) que só faz o reseed se a tabela já foi usada.

## Passo 4 — Simulação de usuários, assinaturas e sessões

Histórico simulado de **jan/2025 a 21/out/2026**, com seed fixa (reprodutível).

| Tabela | Linhas |
|---|---|
| users | 50.000 |
| subscriptions | 57.905 (31.481 ativas) |
| payments | 336.118 |
| viewing_sessions | 2.227.099 |
| ratings | 212.309 |

Churn mensal médio de **5,9%** e taxa de conclusão de **54,8%**.

Regras de negócio simuladas:

1. **Crescimento com sazonalidade:** cadastros crescem ao longo do tempo, com picos em janeiro, julho e dezembro.
2. **Churn decrescente com o tempo de casa:** ~9% no 1º mês, ~3,5% depois de um ano; maior no plano Básico e em quem assiste pouco.
3. **Troca de plano** (3% por ciclo, 65% upgrades), **falha de pagamento** encerrando a assinatura (churn involuntário) e **reativação** de 15% de quem cancelou.
4. **Sessões realistas:** pico às 21h, mais consumo no fim de semana e nas férias, TV com metade das sessões, cada usuário com aparelho e gêneros favoritos.
5. **Sinal de churn:** quem vai cancelar assiste bem menos nos 30 dias anteriores.
6. **Catálogo real orientando o consumo:** popularidade e qualidade de cada filme vêm das ~100 mil avaliações reais do MovieLens; filmes mal avaliados são mais abandonados, e no celular se abandona mais.
7. **`created_at`/`updated_at` com a data real de cada evento**, e não a data da carga, para que a carga incremental faça sentido.

Desempenho: a simulação leva ~20 s; a gravação leva ~6 min, quase tudo nas 2,2 milhões de sessões (`to_sql` + `fast_executemany`).

Script: `data_generator/02_simulate_platform.py`

## Passo 5 — Ingestão no Databricks

_(em andamento)_
