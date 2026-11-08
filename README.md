# Finanças do Casal

App web (Streamlit) para o fluxo de caixa do casal, compras de supermercado com comparação de preços
(importando XML de NF-e/NFC-e) e divisão de despesas entre David e Mylena.

Dois modos, o mesmo código:

| Modo | Banco | Login | Quando usar |
|---|---|---|---|
| **Local** | SQLite em `data/financas.db` | nenhum | testar no seu computador |
| **Nuvem** | PostgreSQL (Neon) | Google, só os 2 e-mails | uso diário pelos celulares |

O modo é decidido pela presença de `DATABASE_URL` nos segredos. **Na nuvem o login é obrigatório:** se ele
não estiver configurado, o app se recusa a abrir.

---

## Rodar no computador (modo local)

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
streamlit run app.py
```

Para testar o supermercado sem notas reais, suba os XMLs da pasta `exemplos/` em
**Supermercado > Importar NF-e (XML)**. Testes: `pip install pytest && pytest -q`.

---

## Publicar para os dois usarem pelo celular (modo nuvem)

Custo: R$ 0 nos serviços abaixo (limites e regras dos planos gratuitos mudam; confira nas páginas deles).
Faça na ordem. Tempo estimado: 30 a 45 minutos.

### 1. Banco de dados (Neon)

1. Crie uma conta em <https://neon.com> e um projeto (escolha a região mais próxima, ex.: São Paulo, se houver).
2. Na tela do projeto, clique em **Connect** e copie a *connection string* (começa com `postgresql://`).
   Guarde: ela é a senha do seu banco.

No plano gratuito do Neon o banco "dorme" após alguns minutos parado e acorda sozinho no próximo acesso.
Na primeira abertura depois de um tempo, pode levar alguns segundos a mais.

### 2. Login com Google

1. Em <https://console.cloud.google.com>, crie um projeto (ex.: "Financas Casal").
2. **APIs e serviços > Tela de permissão OAuth**: tipo **Externo**, preencha nome do app e seu e-mail.
   Em **Usuários de teste**, adicione **os dois e-mails** (o seu e o da Mylena). Deixe em modo "Teste":
   só quem está na lista consegue entrar, e isso já basta.
3. **Credenciais > Criar credenciais > ID do cliente OAuth > Aplicativo da Web**.
   Em **URIs de redirecionamento autorizados**, coloque
   `https://SEU-APP.streamlit.app/oauth2callback` (o endereço do passo 4; dá para voltar e ajustar depois).
4. Copie o **ID do cliente** e a **chave secreta do cliente**.

### 3. Código no GitHub

1. Crie um repositório **privado** no GitHub e envie esta pasta (o `.gitignore` já impede que
   `.streamlit/secrets.toml` e o banco local subam).
2. Confirme que o arquivo `.streamlit/secrets.toml` **não** aparece no repositório. Segredos nunca vão para o GitHub.

### 4. Streamlit Community Cloud

1. Entre em <https://share.streamlit.io> com o GitHub, **Create app**, escolha o repositório, branch `main`
   e arquivo `app.py`. Em **Advanced settings**, escolha o endereço (subdomínio) do app.
2. Ainda em **Advanced settings > Secrets**, cole o modelo de `.streamlit/secrets.toml.example`
   preenchido: `DATABASE_URL`, o bloco `[auth]` (inclusive `redirect_uri` com o endereço escolhido e
   `cookie_secret` com uma sequência aleatória longa) e o bloco `[acesso]` com os e-mails.
3. **Deploy**. O app cria as tabelas sozinho na primeira abertura.

### 5. No celular

- **Android (Chrome):** abra o endereço, entre com o Google, menu ⋮ > **Instalar app** (ou *Adicionar à tela inicial*).
- **iPhone (Safari):** abra o endereço, entre com o Google, **Compartilhar > Adicionar à Tela de Início**.

Cada um entra com a própria conta; o app já marca "quem pagou" com a pessoa logada.

### Checklist do primeiro acesso (importante)

A camada de PostgreSQL foi escrita e testada com uma conexão simulada, mas **nunca rodou contra um servidor
PostgreSQL de verdade** antes da entrega. Na primeira vez, confira:

- [ ] O app abre e pede login; uma conta de fora da lista é recusada.
- [ ] Barra lateral > **Backup dos dados** mostra "Banco em uso: PostgreSQL (nuvem)".
- [ ] Cadastre uma receita, uma conta fixa e um parcelamento: aparecem no **Painel** e na **Projeção**.
- [ ] Importe um XML de `exemplos/` e confira a divisão em **Casal**.
- [ ] Exclua uma compra e um parcelamento (teste de apagar em cascata).
- [ ] Abra o app no celular da Mylena, lance uma compra manual, e veja aparecer no seu.

Se algo falhar, abra **Manage app > Logs** no Streamlit Cloud e mande o erro: a correção costuma ser pequena.

### Limites do plano gratuito (confirmados nas páginas oficiais em nov/2026)

- O app do Streamlit **dorme após 12 horas sem acesso**; quem abrir o endereço vê um botão
  "Yes, get this app back up!" e ele volta em instantes.
- Neon gratuito: 1 GB por projeto, banco "dorme" após 5 minutos parado, restauração para qualquer ponto das últimas 6 horas.
  Para uso de casal, 1 GB sobra por muitos anos.
- Supabase (alternativa) pausa projetos gratuitos após 1 semana sem uso e não tem backup, por isso a
  escolha foi o Neon.

### Backup

O Neon só guarda histórico recente. Faça, de vez em quando (sugestão: todo dia 1º), em
**barra lateral > Backup dos dados > Preparar backup > Baixar backup (.zip)** e guarde o arquivo no seu
drive. O zip tem um CSV por tabela.

---

## Estrutura

```
app.py                  entrada: segredos, login, navegação, backup
core/                   regras de negócio, sem nenhuma dependência de interface
  database.py           SQLite local OU PostgreSQL (mesma interface), esquema, pool de conexões
  acesso.py             regra de quem pode entrar (testável)
  backup.py             exporta todas as tabelas em CSV dentro de um ZIP
  util.py               meses (AAAA-MM), moeda em R$, normalização de texto, validação de EAN
  fluxo_caixa.py        receitas, contas fixas, avulsos, cartões, parcelamentos, projeção
  nfe.py                leitor de XML de NF-e/NFC-e
  supermercado.py       mercados, produtos, histórico de preços, comparação de carrinho
  compras.py            grava uma compra completa (itens + preços + quem pagou + rateio)
  divisao.py            saldo entre o casal e acertos
views/                  telas Streamlit (uma por módulo), login e componentes comuns
db/schema.sql           modelo do esquema (marcadores trocados pelo tipo de cada banco)
.streamlit/secrets.toml.example   modelo dos segredos (nunca coloque dados reais nele)
exemplos/               duas NFC-e de exemplo para testar a importação
tests/                  test_smoke.py (regras) e test_nuvem.py (PostgreSQL, acesso, backup)
```

## Modelo de dados (resumo)

| Módulo | Tabelas |
|---|---|
| Pessoas | `pessoa` |
| Fluxo de caixa | `receita`, `conta_fixa`, `lancamento` (avulsos), `cartao`, `parcelamento`, `parcela` |
| Supermercado | `estabelecimento`, `produto`, `compra`, `compra_item`, `historico_preco` |
| Divisão | `compra_pagamento` (quem pagou), `compra_rateio` (% dos itens conjuntos), `acerto` |

Convenções: mês como texto `AAAA-MM`, data como `AAAA-MM-DD`, chaves estrangeiras ativas.

## Como as regras funcionam

**Projeção mensal.** Entradas = salários (recorrentes) + avulsos a receber. Saídas = contas fixas +
parcelas do mês + avulsos a pagar. Cada parcelamento gera uma linha em `parcela` por competência, então
as parcelas "abatem" sozinhas: quando o mês passa, ele deixa de pesar nos seguintes. Para cartão, a
competência é o mês em que a fatura vence. Contratos em andamento (parcela 7 de 36) são suportados.

**XML.** O leitor ignora namespaces, usa o preço **líquido de desconto**, aceita o XML com ou sem o
envelope `nfeProc` e impede importar a mesma nota duas vezes (chave de acesso). Antes de salvar você vê
os itens, escolhe o dono de cada um e quem pagou.

**Produtos entre mercados.** O mesmo produto é reconhecido pelo **EAN** (quando é um código de barras
válido) e, na falta dele, pelo **nome normalizado** (sem acento, maiúsculo, espaços únicos).

**Comparação de carrinho.** Dada a lista, mostra o total em cada mercado (os que têm todos os itens vêm
primeiro) e a "melhor combinação" comprando cada item onde é mais barato. Você escolhe o custo histórico:
preço médio, último preço ou menor preço já registrado, e a janela de tempo.

**Divisão.** Cada item da compra é de uma pessoa ou **conjunto**; itens conjuntos são rateados pelo
percentual definido na compra (padrão 50/50). O pagamento pode ser de um, do outro ou dividido. Saldo =
pago − consumido + acertos enviados − acertos recebidos. Registre um Pix entre vocês em **Casal > Registrar acerto**.

## Limitações conhecidas

- A divisão do casal cobre as **compras de supermercado**; contas fixas e parcelas entram só no fluxo de caixa.
- A interface assume exatamente duas pessoas (David e Mylena, criadas automaticamente).
- Não há edição de registros: exclua e lance de novo. Importa-se um XML por vez.
- No celular, a importação de XML é incômoda (a nota costuma chegar como QR code/link, não como arquivo);
  a compra manual funciona bem. Não há notificações nem uso offline.
- O backup é uma exportação em CSV; ainda não há botão para restaurar a partir dele.
- Valores em ponto flutuante com arredondamento a 2 casas (suficiente para uso pessoal).
- Produtos sem EAN e com nomes muito diferentes entre mercados viram produtos separados.
- Preços são por unidade de venda; embalagens de tamanhos diferentes não são convertidas.
