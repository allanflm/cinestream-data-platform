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

## Passo 4 — Simulação de usuários, assinaturas e sessões

_(em andamento)_
