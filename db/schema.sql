-- =====================================================================
-- Esquema do banco - Finanças do Casal (SQLite local ou PostgreSQL na nuvem)
-- Este arquivo é um MODELO: core/database.py troca os marcadores entre
-- chaves pelo tipo certo de cada banco:
--     PK    -> chave primária automática (SQLite: rowid; PostgreSQL: IDENTITY)
--     REAL  -> número decimal (SQLite: REAL; PostgreSQL: DOUBLE PRECISION)
--     HOJE  -> data de hoje como texto 'YYYY-MM-DD'
-- Convenções:
--   * Competência (mês) sempre como texto 'YYYY-MM'; datas como 'YYYY-MM-DD'.
--   * Valores monetários em ponto flutuante, arredondados a 2 casas
--     (uso pessoal); preços unitários de notas fiscais podem ter mais casas.
--   * É seguro executar este arquivo várias vezes (IF NOT EXISTS).
-- =====================================================================

-- ---------------------------------------------------------------------
-- Pessoas (David e Mylena são criadas pelo código na primeira execução)
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS pessoa (
    id   {PK},
    nome TEXT NOT NULL UNIQUE
);

-- =====================================================================
-- MÓDULO 1 - FLUXO DE CAIXA
-- =====================================================================

-- Receitas (salários etc.). Recorrente = vale todo mês de mes_inicio até
-- mes_fim (NULL = sem fim). Não recorrente = vale só em mes_inicio.
CREATE TABLE IF NOT EXISTS receita (
    id         {PK},
    pessoa_id  INTEGER NOT NULL REFERENCES pessoa(id),
    descricao  TEXT    NOT NULL,
    valor      {REAL}    NOT NULL CHECK (valor >= 0),
    mes_inicio TEXT    NOT NULL,
    mes_fim    TEXT,
    recorrente INTEGER NOT NULL DEFAULT 1 CHECK (recorrente IN (0, 1))
);

-- Contas fixas mensais (aluguel, internet, condomínio...).
CREATE TABLE IF NOT EXISTS conta_fixa (
    id             {PK},
    descricao      TEXT    NOT NULL,
    categoria      TEXT,
    valor          {REAL}    NOT NULL CHECK (valor >= 0),
    dia_vencimento INTEGER NOT NULL CHECK (dia_vencimento BETWEEN 1 AND 31),
    mes_inicio     TEXT    NOT NULL,
    mes_fim        TEXT
);

-- Lançamentos avulsos (uma única vez): contas a pagar e a receber.
CREATE TABLE IF NOT EXISTS lancamento (
    id        {PK},
    descricao TEXT    NOT NULL,
    tipo      TEXT    NOT NULL CHECK (tipo IN ('pagar', 'receber')),
    valor     {REAL}    NOT NULL CHECK (valor >= 0),
    data      TEXT    NOT NULL,
    pago      INTEGER NOT NULL DEFAULT 0 CHECK (pago IN (0, 1))
);

-- Cartões de crédito de diferentes bancos/fintechs.
CREATE TABLE IF NOT EXISTS cartao (
    id             {PK},
    nome           TEXT NOT NULL UNIQUE,
    banco          TEXT,
    dia_fechamento INTEGER CHECK (dia_fechamento BETWEEN 1 AND 31),
    dia_vencimento INTEGER CHECK (dia_vencimento BETWEEN 1 AND 31)
);

-- Parcelamentos: compras parceladas no cartão, empréstimos, financiamentos.
CREATE TABLE IF NOT EXISTS parcelamento (
    id            {PK},
    descricao     TEXT    NOT NULL,
    tipo          TEXT    NOT NULL
                  CHECK (tipo IN ('cartao', 'emprestimo', 'financiamento', 'outro')),
    cartao_id     INTEGER REFERENCES cartao(id),
    valor_parcela {REAL}    NOT NULL CHECK (valor_parcela >= 0),
    num_parcelas  INTEGER NOT NULL CHECK (num_parcelas >= 1),
    criado_em     TEXT    NOT NULL DEFAULT {HOJE}
);

-- Uma linha por parcela futura (gerada na criação do parcelamento).
-- 'competencia' = mês em que a parcela pesa no caixa (para cartão: mês do
-- vencimento da fatura). A projeção mensal soma as parcelas por competência,
-- por isso as parcelas "se abatem" sozinhas dos meses seguintes.
CREATE TABLE IF NOT EXISTS parcela (
    id              {PK},
    parcelamento_id INTEGER NOT NULL REFERENCES parcelamento(id) ON DELETE CASCADE,
    numero          INTEGER NOT NULL,
    competencia     TEXT    NOT NULL,
    valor           {REAL}    NOT NULL,
    paga            INTEGER NOT NULL DEFAULT 0 CHECK (paga IN (0, 1)),
    UNIQUE (parcelamento_id, numero)
);
CREATE INDEX IF NOT EXISTS ix_parcela_competencia ON parcela(competencia);

-- =====================================================================
-- MÓDULO 2 - SUPERMERCADOS E INTELIGÊNCIA DE PREÇOS
-- =====================================================================

-- Estabelecimentos. CNPJ (só dígitos) é a chave natural quando vem de XML.
CREATE TABLE IF NOT EXISTS estabelecimento (
    id   {PK},
    nome TEXT NOT NULL,
    cnpj TEXT UNIQUE
);

-- Produtos. O casamento entre notas usa o EAN (GTIN) quando existe e,
-- na falta dele, o nome normalizado (sem acento, maiúsculo, espaços únicos).
CREATE TABLE IF NOT EXISTS produto (
    id               {PK},
    nome             TEXT NOT NULL,
    nome_normalizado TEXT NOT NULL,
    ean              TEXT UNIQUE,
    unidade          TEXT NOT NULL DEFAULT 'UN'
);
CREATE INDEX IF NOT EXISTS ix_produto_nome_norm ON produto(nome_normalizado);

-- Compra = uma ida ao mercado (nota fiscal importada ou lançada à mão).
CREATE TABLE IF NOT EXISTS compra (
    id                 {PK},
    estabelecimento_id INTEGER NOT NULL REFERENCES estabelecimento(id),
    data               TEXT    NOT NULL,
    chave_nfe          TEXT UNIQUE,            -- evita importar a mesma nota 2x
    origem             TEXT    NOT NULL DEFAULT 'manual' CHECK (origem IN ('xml', 'manual')),
    total              {REAL}    NOT NULL,
    observacao         TEXT
);
CREATE INDEX IF NOT EXISTS ix_compra_data ON compra(data);

-- Itens da compra. dono_id NULL = item conjunto (entra no rateio da compra);
-- dono_id preenchido = item de uso individual daquela pessoa.
CREATE TABLE IF NOT EXISTS compra_item (
    id                 {PK},
    compra_id          INTEGER NOT NULL REFERENCES compra(id) ON DELETE CASCADE,
    produto_id         INTEGER NOT NULL REFERENCES produto(id),
    descricao_original TEXT,
    quantidade         {REAL}    NOT NULL CHECK (quantidade > 0),
    unidade            TEXT    NOT NULL DEFAULT 'UN',
    preco_unitario     {REAL}    NOT NULL CHECK (preco_unitario >= 0),
    valor_total        {REAL}    NOT NULL,
    dono_id            INTEGER REFERENCES pessoa(id)
);

-- Histórico de preços por produto e estabelecimento. Recebe uma linha por
-- item de compra (XML ou manual) e também observações avulsas de preço
-- (compra_item_id NULL), ex.: preço visto na prateleira.
CREATE TABLE IF NOT EXISTS historico_preco (
    id                 {PK},
    produto_id         INTEGER NOT NULL REFERENCES produto(id),
    estabelecimento_id INTEGER NOT NULL REFERENCES estabelecimento(id),
    data               TEXT    NOT NULL,
    preco_unitario     {REAL}    NOT NULL CHECK (preco_unitario >= 0),
    origem             TEXT    NOT NULL DEFAULT 'manual' CHECK (origem IN ('xml', 'manual')),
    compra_item_id     INTEGER REFERENCES compra_item(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS ix_hist_produto ON historico_preco(produto_id, estabelecimento_id, data);

-- =====================================================================
-- MÓDULO 3 - PESSOAS E DIVISÃO DE COMPRAS
-- =====================================================================

-- Quem pagou cada compra (uma ou mais pessoas; a soma deve fechar o total).
CREATE TABLE IF NOT EXISTS compra_pagamento (
    compra_id INTEGER NOT NULL REFERENCES compra(id) ON DELETE CASCADE,
    pessoa_id INTEGER NOT NULL REFERENCES pessoa(id),
    valor     {REAL}    NOT NULL CHECK (valor >= 0),
    PRIMARY KEY (compra_id, pessoa_id)
);

-- Rateio dos itens conjuntos (percentuais por pessoa somando 100).
CREATE TABLE IF NOT EXISTS compra_rateio (
    compra_id  INTEGER NOT NULL REFERENCES compra(id) ON DELETE CASCADE,
    pessoa_id  INTEGER NOT NULL REFERENCES pessoa(id),
    percentual {REAL}    NOT NULL CHECK (percentual BETWEEN 0 AND 100),
    PRIMARY KEY (compra_id, pessoa_id)
);

-- Transferências entre o casal para quitar o saldo ("de" pagou "para").
CREATE TABLE IF NOT EXISTS acerto (
    id             {PK},
    data           TEXT    NOT NULL,
    de_pessoa_id   INTEGER NOT NULL REFERENCES pessoa(id),
    para_pessoa_id INTEGER NOT NULL REFERENCES pessoa(id),
    valor          {REAL}    NOT NULL CHECK (valor > 0),
    observacao     TEXT,
    CHECK (de_pessoa_id <> para_pessoa_id)
);
