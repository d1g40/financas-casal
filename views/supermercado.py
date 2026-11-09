"""Tela do Módulo 2 (e parte do 3): importação de NF-e, compra manual, preços e comparação de carrinho."""
from __future__ import annotations

from collections import Counter
from datetime import date, timedelta
from typing import Optional

import pandas as pd
import streamlit as st

from core import compras, nfe, supermercado as sm
from core import fluxo_caixa as fc
from core.database import transacao
from core.fluxo_caixa import listar_pessoas
from core.util import formatar_brl
from views.componentes import moeda

CONJUNTO = "Conjunto"
UNIDADES = ["UN", "KG", "G", "L", "ML", "PCT", "CX", "DZ"]
JANELAS = {"Todo o histórico": None, "Últimos 90 dias": 90, "Últimos 180 dias": 180, "Último ano": 365}
METRICAS = {"medio": "Preço médio histórico", "ultimo": "Último preço visto", "minimo": "Menor preço já registrado"}


def render() -> None:
    st.title("Supermercado")
    mensagem = st.session_state.pop("mensagem_sucesso", None)
    if mensagem:
        st.success(mensagem)
    with transacao() as conn:
        pessoas = {p["id"]: p["nome"] for p in listar_pessoas(conn)}

    abas = st.tabs(["Importar NF-e (XML)", "Compra manual", "Preços", "Comparar carrinho"])
    with abas[0]:
        _aba_importar_xml(pessoas)
    with abas[1]:
        _aba_manual(pessoas)
    with abas[2]:
        _aba_precos()
    with abas[3]:
        _aba_comparar()


# ----------------------------------------------------------------------
# Peças compartilhadas (quem pagou, rateio, salvar)
# ----------------------------------------------------------------------
def _quem_pagou(pessoas: dict[int, str], total: float, chave: str) -> dict[int, float]:
    """Widgets de 'quem pagou'. Devolve {pessoa_id: valor}. Pensado para um casal (2 pessoas)."""
    ids = list(pessoas)
    opcoes = [*pessoas.values(), "Ambos (dividir o pagamento)"]
    padrao = st.session_state.get("pessoa_logada")  # quem está usando o app já vem marcado
    escolha = st.radio("Quem pagou?", opcoes, index=opcoes.index(padrao) if padrao in opcoes else 0,
                       horizontal=True, key=f"{chave}_pagou")
    if escolha.startswith("Ambos"):
        primeiro, segundo = ids[0], ids[1]
        valor = st.number_input(
            f"Valor pago por {pessoas[primeiro]} (R$) — o restante é de {pessoas[segundo]}",
            min_value=0.0, max_value=max(total, 0.01), value=round(total / 2, 2), step=1.0, format="%.2f",
            key=f"{chave}_pago_primeiro_{total:.2f}")
        return {primeiro: round(valor, 2), segundo: round(total - valor, 2)}
    pessoa_id = next(i for i, nome in pessoas.items() if nome == escolha)
    return {pessoa_id: round(total, 2)}


def _rateio(pessoas: dict[int, str], chave: str) -> dict[int, float]:
    ids = list(pessoas)
    percentual = st.slider(
        f"Itens conjuntos: parte de {pessoas[ids[0]]} (%) — o restante fica com {pessoas[ids[1]]}",
        0, 100, 50, 1, key=f"{chave}_rateio")
    return {ids[0]: float(percentual), ids[1]: float(100 - percentual)}


def _salvar_compra(*, mercado: str, cnpj: Optional[str], data, itens: list[dict], pagamentos: dict[int, float],
                   rateio: dict[int, float], chave_nfe: Optional[str], origem: str,
                   limpar_estado: Optional[str] = None) -> None:
    try:
        with transacao() as conn:
            estabelecimento_id = sm.obter_ou_criar_estabelecimento(conn, mercado, cnpj)
            compras.registrar_compra(conn, estabelecimento_id=estabelecimento_id, data=data, itens=itens,
                                     pagamentos=pagamentos, rateio=rateio, chave_nfe=chave_nfe, origem=origem)
    except compras.CompraDuplicadaError:
        st.error("Esta nota já foi importada.")
    except ValueError as erro:
        st.error(str(erro))
    else:
        if limpar_estado:  # troca a chave do editor para a tabela voltar vazia
            st.session_state[limpar_estado] = st.session_state.get(limpar_estado, 0) + 1
        st.session_state["mensagem_sucesso"] = "Compra salva: preços atualizados e divisão registrada."
        st.rerun()


def _campo_mercado(mercados: list[dict], chave: str, container=st) -> str:
    """Escolha de um mercado existente OU digitação de um novo (o texto digitado vence)."""
    nomes = [m["nome"] for m in mercados]
    escolhido = container.selectbox("Mercado", nomes, key=f"{chave}_mercado") if nomes else None
    novo = container.text_input("…ou cadastre um novo mercado", key=f"{chave}_mercado_novo")
    return novo.strip() or escolhido or ""


def _rotulos_produtos(produtos: list[dict]) -> dict[int, str]:
    """id -> rótulo único (acrescenta #id quando há nomes repetidos)."""
    repetidos = Counter(p["nome"] for p in produtos)
    return {p["id"]: p["nome"] if repetidos[p["nome"]] == 1 else f"{p['nome']} (#{p['id']})" for p in produtos}


# ----------------------------------------------------------------------
# Importar XML
# ----------------------------------------------------------------------
def _aba_importar_xml(pessoas: dict[int, str]) -> None:
    arquivo = st.file_uploader("Arquivo XML da nota fiscal (NF-e / NFC-e)", type=["xml"], key="xml_upload")
    if arquivo is None:
        st.caption("Suba o XML recebido do mercado ou baixado do portal da SEFAZ do seu estado. "
                   "Para testar, use os arquivos da pasta `exemplos/`.")
        return
    try:
        nota = nfe.ler_nota_xml(arquivo.getvalue())
    except nfe.NotaFiscalError as erro:
        st.error(str(erro))
        return

    with transacao() as conn:
        existente = sm.buscar_estabelecimento_por_cnpj(conn, nota.emitente_cnpj)
        duplicada = compras.compra_existe(conn, nota.chave)
    if duplicada:
        st.warning("Esta nota já foi importada antes; nada a fazer.")
        return

    sufixo = nota.chave or arquivo.name
    
    # ------------------------------------------------------------------
    # Verificação inteligente de ramo: Supermercado vs Outras Lojas
    # ------------------------------------------------------------------
    ramo = nfe.classificar_ramo_estabelecimento(nota.emitente_nome)
    
    if ramo == "outro":
        st.info(f"Estabelecimento identificado como outro ramo: **{nota.emitente_nome}**")
        
        # Verifica se a nota possui pagamento parcelado no cartão de crédito (tPag == '03')
        pag_cartao = next((p for p in nota.pagamentos if p.tPag == "03" and p.n_parcelas > 1), None)
        
        if pag_cartao:
            st.warning(f"Detectada compra parcelada em {pag_cartao.n_parcelas}x no cartão de crédito nesta nota!")
            
            with transacao() as conn:
                cartoes = fc.listar_cartoes(conn)
            
            opcoes_cartao = {c["id"]: c["nome"] for c in cartoes}
            if not opcoes_cartao:
                st.error("Você precisa cadastrar pelo menos um cartão de crédito no módulo de Fluxo de Caixa antes de importar parcelamentos.")
            else:
                cartao_escolhido_id = st.selectbox("Escolha o cartão usado nesta compra", list(opcoes_cartao.keys()), format_func=opcoes_cartao.get, key=f"xml_cartao_outro_{sufixo}")
                
                if st.button("Cadastrar como Parcelamento", key=f"btn_cad_parc_{sufixo}", type="primary"):
                    try:
                        with transacao() as conn:
                            fc.criar_parcelamento(
                                conn,
                                descricao=f"Compra: {nota.emitente_nome}",
                                tipo="cartao",
                                num_parcelas=pag_cartao.n_parcelas,
                                mes_primeira_parcela=nota.data_emissao.strftime("%Y-%m"),
                                valor_total=nota.valor_nota,
                                cartao_id=cartao_escolhido_id
                            )
                        st.success("Parcelamento cadastrado com sucesso no Fluxo de Caixa!")
                        st.rerun()
                    except Exception as erro:
                        st.error(f"Erro ao criar parcelamento: {erro}")
        else:
            st.caption("Esta nota de outro comércio não é um parcelamento de cartão. Você pode tratá-la como lançamento avulso no Fluxo de Caixa se desejar.")
        return

    # ------------------------------------------------------------------
    # Fluxo normal para Supermercados
    # ------------------------------------------------------------------
    c1, c2 = st.columns(2)
    mercado = c1.text_input("Mercado", value=existente["nome"] if existente else nota.emitente_nome,
                            disabled=bool(existente), key=f"xml_mercado_{sufixo}",
                            help="Se o CNPJ já está cadastrado, o nome existente é mantido.")
    data = c2.date_input("Data da compra", value=nota.data_emissao, key=f"xml_data_{sufixo}")
    st.caption(f"CNPJ: {nota.emitente_cnpj or 'não informado'} · "
               f"Chave: {nota.chave or 'não informada'} · {len(nota.itens)} itens")

    nomes = list(pessoas.values())
    padrao = st.radio("Por padrão, os itens desta nota são de:", [CONJUNTO, *nomes], horizontal=True,
                      key=f"xml_padrao_{sufixo}")
    st.caption("Aжитe a coluna **Dono** item a item quando a nota tiver itens de uso individual.")
    base = pd.DataFrame([{"Produto": i.descricao, "Qtd": i.quantidade, "Un": i.unidade,
                          "Preço unit.": i.preco_unitario, "Total": i.valor_total, "Dono": padrao}
                         for i in nota.itens])
    editado = st.data_editor(
        base, key=f"xml_itens_{sufixo}_{padrao}", hide_index=True, column_config={
            "Qtd": st.column_config.NumberColumn(format="%.3f"),
            "Preço unit.": st.column_config.NumberColumn(format="%.4f"),
            "Total": st.column_config.NumberColumn(format="%.2f"),
            "Dono": st.column_config.SelectboxColumn(options=[CONJUNTO, *nomes], required=True)},
        disabled=["Qtd", "Un", "Preço unit.", "Total"])

    total = round(float(editado["Total"].sum()), 2)
    st.metric("Total da compra", formatar_brl(total))
    if nota.valor_nota is not None and abs(nota.valor_nota - total) > 0.05:
        st.warning(f"O total dos itens ({formatar_brl(total)}) difere do valor da nota "
                   f"({formatar_brl(nota.valor_nota)}). O sistema usa o total dos itens.")

    pagamentos = _quem_pagou(pessoas, total, f"xml_{sufixo}")
    rateio = _rateio(pessoas, f"xml_{sufixo}")

    if st.button("Salvar compra e atualizar preços", key=f"xml_salvar_{sufixo}", type="primary"):
        id_por_nome = {nome: i for i, nome in pessoas.items()}
        itens = [{"descricao": linha["Produto"], "ean": original.ean, "unidade": original.unidade,
                  "quantidade": original.quantidade, "preco_unitario": original.preco_unitario,
                  "valor_total": linha["Total"],
                  "dono_id": None if linha["Dono"] == CONJUNTO else id_por_nome[linha["Dono"]]}
                 for original, linha in zip(nota.itens, editado.to_dict("records"))]
        _salvar_compra(mercado=mercado, cnpj=nota.emitente_cnpj, data=data, itens=itens, pagamentos=pagamentos,
                       rateio=rateio, chave_nfe=nota.chave, origem="xml")


# ----------------------------------------------------------------------
# Compra manual
# ----------------------------------------------------------------------
def _aba_manual(pessoas: dict[int, str]) -> None:
    st.caption("Use quando não houver XML. Escreva os produtos sempre do mesmo jeito (maiúsculas e acentos "
               "não importam) para o histórico de preços reconhecê-los.")
    with transacao() as conn:
        mercados = sm.listar_estabelecimentos(conn)
    versao = st.session_state.get("manual_versao", 0)

    c1, c2 = st.columns(2)
    mercado = _campo_mercado(mercados, f"manual_{versao}", container=c1)
    data = c2.date_input("Data da compra", value=date.today(), key=f"manual_data_{versao}")

    nomes = list(pessoas.values())
    base = pd.DataFrame([{"Produto": "", "Qtd": 1.0, "Un": "UN", "Preço unit.": 0.0, "Dono": CONJUNTO}])
    editado = st.data_editor(
        base, num_rows="dynamic", hide_index=True, key=f"manual_itens_{versao}", column_config={
            "Produto": st.column_config.TextColumn(required=True),
            "Qtd": st.column_config.NumberColumn(min_value=0.001, format="%.3f"),
            "Un": st.column_config.SelectboxColumn(options=UNIDADES),
            "Preço unit.": st.column_config.NumberColumn(min_value=0.0, format="%.2f"),
            "Dono": st.column_config.SelectboxColumn(options=[CONJUNTO, *nomes])})

    linhas = editado.copy()
    linhas["Produto"] = linhas["Produto"].fillna("").astype(str).str.strip()
    linhas = linhas[linhas["Produto"] != ""]
    linhas["Qtd"] = linhas["Qtd"].fillna(1.0)
    linhas["Un"] = linhas["Un"].fillna("UN")
    linhas["Preço unit."] = linhas["Preço unit."].fillna(0.0)
    linhas["Dono"] = linhas["Dono"].fillna(CONJUNTO)
    total = round(float((linhas["Qtd"] * linhas["Preço unit."]).round(2).sum()), 2)
    st.metric("Total da compra", formatar_brl(total))

    pagamentos = _quem_pagou(pessoas, total, f"manual_{versao}")
    rateio = _rateio(pessoas, f"manual_{versao}")

    if st.button("Salvar compra", key=f"manual_salvar_{versao}", type="primary"):
        if not mercado:
            st.error("Escolha ou cadastre o mercado.")
            return
        id_por_nome = {nome: i for i, nome in pessoas.items()}
        itens = [{"descricao": r["Produto"], "unidade": r["Un"], "quantidade": float(r["Qtd"]),
                  "preco_unitario": float(r["Preço unit."]),
                  "dono_id": None if r["Dono"] == CONJUNTO else id_por_nome[r["Dono"]]}
                 for r in linhas.to_dict("records")]
        _salvar_compra(mercado=mercado, cnpj=None, data=data, itens=itens, pagamentos=pagamentos, rateio=rateio,
                       chave_nfe=None, origem="manual", limpar_estado="manual_versao")


# ----------------------------------------------------------------------
# Histórico de preços
# ----------------------------------------------------------------------
def _aba_precos() -> None:
    with transacao() as conn:
        mercados = sm.listar_estabelecimentos(conn)
        produtos = sm.listar_produtos(conn)

    with st.expander("Registrar um preço visto (sem compra)"):
        with st.form("form_preco", clear_on_submit=True):
            mercado = _campo_mercado(mercados, "preco")
            c1, c2, c3, c4 = st.columns(4)
            produto = c1.text_input("Produto")
            ean = c2.text_input("EAN (opcional)")
            preco = c3.number_input("Preço unitário (R$)", min_value=0.0, step=0.1, format="%.2f")
            data = c4.date_input("Data", value=date.today())
            unidade = st.selectbox("Unidade", UNIDADES)
            enviado = st.form_submit_button("Registrar preço")
        if enviado:
            try:
                with transacao() as conn:
                    mercado_id = sm.obter_ou_criar_estabelecimento(conn, mercado)
                    produto_id = sm.obter_ou_criar_produto(conn, produto, ean or None, unidade)
                    sm.registrar_preco(conn, produto_id, mercado_id, data, preco, origem="manual")
                st.success("Preço registrado.")
            except ValueError as erro:
                st.error(str(erro))

    if not produtos:
        st.info("Ainda não há produtos. Importe uma nota ou lance uma compra manual.")
        return
    rotulos = _rotulos_produtos(produtos)
    escolhido = st.selectbox("Produto", [None, *rotulos], format_func=lambda i: "— escolha —" if i is None else rotulos[i],
                             key="precos_produto")
    if escolhido is None:
        return

    with transacao() as conn:
        resumo = sm.resumo_precos(conn, escolhido)
        historico = sm.historico_precos(conn, escolhido)
    st.markdown("**Por mercado** (do mais barato na média ao mais caro)")
    tabela = pd.DataFrame(resumo).rename(columns={"mercado": "Mercado", "ultimo": "Último", "medio": "Médio",
                                                  "minimo": "Menor", "registros": "Registros"})
    st.dataframe(moeda(tabela, ["Último", "Médio", "Menor"]), hide_index=True)

    df = pd.DataFrame(historico)
    evolucao = df.pivot_table(index="data", columns="mercado", values="preco_unitario", aggfunc="mean").sort_index()
    st.markdown("**Evolução** (cada linha mantém o último preço conhecido do mercado)")
    st.line_chart(evolucao.ffill())
    st.markdown("**Todos os registros**")
    detalhe = df[["data", "mercado", "preco_unitario", "origem"]]
    detalhe.columns = ["Data", "Mercado", "Preço unit.", "Origem"]
    st.dataframe(moeda(detalhe, ["Preço unit."]), hide_index=True)


# ----------------------------------------------------------------------
# Comparar carrinho
# ----------------------------------------------------------------------
def _aba_comparar() -> None:
    with transacao() as conn:
        produtos = sm.listar_produtos(conn)
    if not produtos:
        st.info("Importe notas ou registre preços antes de comparar.")
        return
    rotulos = _rotulos_produtos(produtos)
    id_por_rotulo = {rotulo: i for i, rotulo in rotulos.items()}

    st.markdown("**Lista de compras** — use a linha em branco no fim da tabela para adicionar produtos.")
    base = pd.DataFrame({"Produto": pd.Series(dtype="object"), "Quantidade": pd.Series(dtype="float")})
    lista = st.data_editor(
        base, num_rows="dynamic", hide_index=True, key="comp_lista", column_config={
            "Produto": st.column_config.SelectboxColumn(options=list(id_por_rotulo), required=True),
            "Quantidade": st.column_config.NumberColumn(min_value=0.001, format="%.3f")})

    c1, c2 = st.columns(2)
    metrica = c1.radio("Como medir o custo histórico", list(METRICAS), format_func=METRICAS.get, key="comp_metrica")
    janela = c2.selectbox("Preços considerados", list(JANELAS), key="comp_janela")

    itens = []
    for produto, quantidade in zip(lista["Produto"], lista["Quantidade"]):
        if produto in id_por_rotulo:
            itens.append((id_por_rotulo[produto], 1.0 if pd.isna(quantidade) else float(quantidade)))
    if not itens:
        st.info("Adicione produtos à lista para comparar.")
        return

    dias = JANELAS[janela]
    desde = (date.today() - timedelta(days=dias)).isoformat() if dias else None
    with transacao() as conn:
        resultado = sm.comparar_carrinho(conn, itens, metrica, desde)

    ranking = resultado["por_mercado"]
    if not ranking:
        st.warning("Não há preços registrados para esses produtos no período escolhido.")
        return
    melhor = ranking[0]
    if melhor["completo"]:
        st.success(f"Mais barato para fechar o carrinho: **{melhor['mercado']}** — {formatar_brl(melhor['total'])}")
    else:
        st.warning("Nenhum mercado tem preço para todos os itens. O primeiro da lista é o que cobre mais itens.")

    tabela = pd.DataFrame([{
        "Mercado": m["mercado"], "Total": m["total"],
        "Itens com preço": f"{m['itens_encontrados']}/{m['itens_total']}",
        "Sem preço": ", ".join(m["faltantes"]) or "-"} for m in ranking])
    st.dataframe(moeda(tabela, ["Total"]), hide_index=True)
    if resultado["sem_preco_algum"]:
        st.caption("Sem nenhum preço registrado: " + ", ".join(resultado["sem_preco_algum"]))

    combinacao = resultado["melhor_combinacao"]
    if combinacao["itens"]:
        st.markdown(f"**Melhor combinação** (cada item no mercado mais barato dele): "
                    f"{formatar_brl(combinacao['total'])}")
        if combinacao["economia_vs_melhor_mercado"] is not None:
            st.caption(f"Economia de {formatar_brl(combinacao['economia_vs_melhor_mercado'])} em relação a comprar "
                       f"tudo em {melhor['mercado']}, ao custo de ir a mais de um mercado.")
        itens_combinacao = pd.DataFrame(combinacao["itens"])[
            ["produto", "mercado", "quantidade", "preco_unitario", "subtotal"]]
        itens_combinacao.columns = ["Produto", "Mercado", "Qtd", "Preço unit.", "Subtotal"]
        st.dataframe(moeda(itens_combinacao, ["Preço unit.", "Subtotal"]), hide_index=True)

    st.markdown("**Subtotal de cada item em cada mercado**")
    matriz = pd.DataFrame(resultado["detalhe"]).pivot_table(
        index="produto", columns="mercado", values="subtotal", aggfunc="first")
    st.dataframe(matriz.apply(lambda coluna: coluna.map(lambda v: formatar_brl(v) if pd.notna(v) else "—")))
    st.caption("Os preços são por unidade de venda; produtos com embalagens diferentes só se comparam se forem "
               "o mesmo item (mesmo EAN ou mesmo nome).")
    