"""Tela do Módulo 3: saldo entre o casal, compras divididas e registro de acertos."""
from __future__ import annotations

from datetime import date

import pandas as pd
import streamlit as st

from core import compras, divisao
from core.database import transacao
from core.fluxo_caixa import listar_pessoas
from core.util import formatar_brl
from views.componentes import moeda, renomear, tabela_com_exclusao


def render() -> None:
    st.title("Casal: divisão e acertos")
    mensagem = st.session_state.pop("mensagem_sucesso", None)
    if mensagem:
        st.success(mensagem)

    with transacao() as conn:
        pessoas = {p["id"]: p["nome"] for p in listar_pessoas(conn)}
        saldo = divisao.saldo_casal(conn)

    st.markdown(f"### {saldo['resumo']}")
    colunas = st.columns(len(saldo["pessoas"]))
    for coluna, pessoa in zip(colunas, saldo["pessoas"]):
        coluna.markdown(f"**{pessoa['nome']}**")
        coluna.metric("Pagou nas compras", formatar_brl(pessoa["pago"]))
        coluna.metric("Consumiu (itens próprios + rateio)", formatar_brl(pessoa["consumo"]))
        coluna.metric("Saldo", formatar_brl(pessoa["saldo"]),
                      help="Positivo: tem a receber do outro. Negativo: deve ao outro.")

    st.subheader("Registrar acerto")
    st.caption("Use quando um fizer um Pix/transferência para o outro, quitando parte ou todo o saldo.")
    ids = list(pessoas)
    de_padrao = ids.index(saldo["devedor_id"]) if saldo["devedor_id"] in ids else 0
    para_padrao = ids.index(saldo["credor_id"]) if saldo["credor_id"] in ids else min(1, len(ids) - 1)
    with st.form("form_acerto", clear_on_submit=True):
        c1, c2, c3, c4 = st.columns(4)
        de = c1.selectbox("Quem pagou", ids, index=de_padrao, format_func=pessoas.get)
        para = c2.selectbox("Quem recebeu", ids, index=para_padrao, format_func=pessoas.get)
        valor = c3.number_input("Valor (R$)", min_value=0.01, value=max(saldo["valor"], 0.01), step=1.0, format="%.2f")
        data = c4.date_input("Data", value=date.today())
        observacao = st.text_input("Observação (opcional)")
        enviado = st.form_submit_button("Registrar acerto")
    if enviado:
        try:
            with transacao() as conn:
                divisao.registrar_acerto(conn, de, para, valor, data, observacao.strip())
            st.session_state["mensagem_sucesso"] = "Acerto registrado."
            st.rerun()
        except ValueError as erro:
            st.error(str(erro))

    st.subheader("Compras e como foram divididas")
    nomes = [p["nome"] for p in saldo["pessoas"]]
    linhas = []
    for c in saldo["por_compra"]:
        linha = {"id": c["id"], "Data": c["data"], "Mercado": c["mercado"], "Total": c["total"]}
        for nome in nomes:
            linha[f"Pagou {nome}"] = c["pagou"].get(nome, 0.0)
        for nome in nomes:
            linha[f"Consumo {nome}"] = c["consumo"].get(nome, 0.0)
        linhas.append(linha)
    colunas_moeda = ["Total"] + [f"Pagou {n}" for n in nomes] + [f"Consumo {n}" for n in nomes]
    tabela_com_exclusao(linhas, "tab_compras", "Excluir compras selecionadas",
                        _excluir(compras.remover_compra), colunas_moeda)

    if saldo["por_compra"]:
        rotulo = {c["id"]: f"{c['data']} — {c['mercado']} — {formatar_brl(c['total'])}" for c in saldo["por_compra"]}
        escolhida = st.selectbox("Ver itens da compra", list(rotulo), format_func=rotulo.get, key="casal_compra")
        with transacao() as conn:
            itens = compras.listar_itens_compra(conn, escolhida)
        tabela = pd.DataFrame(renomear(itens, {"produto": "Produto", "quantidade": "Qtd", "unidade": "Un",
                                               "preco_unitario": "Preço unit.", "valor_total": "Total",
                                               "dono": "Dono"}))
        st.dataframe(moeda(tabela, ["Preço unit.", "Total"]), hide_index=True)

    st.subheader("Acertos já feitos")
    with transacao() as conn:
        acertos = divisao.listar_acertos(conn)
    tabela_com_exclusao(
        renomear([{**a, "observacao": a["observacao"] or "-"} for a in acertos],
                 {"id": "id", "data": "Data", "de": "Quem pagou", "para": "Quem recebeu", "valor": "Valor",
                  "observacao": "Observação"}),
        "tab_acertos", "Excluir acertos selecionados", _excluir(divisao.remover_acerto), ["Valor"])


def _excluir(funcao):
    """Adapta uma função do core (conn, id) para o formato esperado por `tabela_com_exclusao`."""
    def executar(item_id: int) -> None:
        with transacao() as conn:
            funcao(conn, item_id)
    return executar
