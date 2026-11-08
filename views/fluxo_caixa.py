"""Tela do Módulo 1: receitas, contas fixas, cartões e parcelamentos, avulsos e projeção."""
from __future__ import annotations

from datetime import date

import altair as alt
import pandas as pd
import streamlit as st

from core import fluxo_caixa as fc
from core.database import transacao
from core.util import formatar_brl, mes_atual, rotulo_mes
from views.componentes import moeda, renomear, seletor_mes, tabela_com_exclusao

ROTULO_TIPO = {"cartao": "Compra no cartão", "emprestimo": "Empréstimo",
               "financiamento": "Financiamento", "outro": "Outro"}


def render() -> None:
    st.title("Fluxo de caixa")
    abas = st.tabs(["Projeção", "Receitas", "Contas fixas", "Parcelamentos e cartões", "Avulsos (a pagar/receber)"])
    with abas[0]:
        _aba_projecao()
    with abas[1]:
        _aba_receitas()
    with abas[2]:
        _aba_contas_fixas()
    with abas[3]:
        _aba_parcelamentos()
    with abas[4]:
        _aba_avulsos()


# ----------------------------------------------------------------------
def _aba_projecao() -> None:
    c1, c2, c3 = st.columns(3)
    inicio = seletor_mes("Começar em", "proj_inicio", container=c1)
    n_meses = c2.selectbox("Quantidade de meses", [6, 12, 18, 24, 36], index=1, key="proj_n")
    saldo_inicial = c3.number_input("Saldo inicial (R$)", value=0.0, step=100.0, format="%.2f", key="proj_saldo")

    with transacao() as conn:
        meses = fc.projecao(conn, inicio, n_meses, saldo_inicial)
    if not any(m["entradas"] or m["saidas"] for m in meses):
        st.info("Cadastre receitas, contas e parcelamentos para ver a projeção.")
        return

    df = pd.DataFrame(meses)
    
    # Garante a ordenação cronológica real baseada no formato 'YYYY-MM'
    df = df.sort_values("mes")
    df["Mês"] = df["mes"].map(rotulo_mes)

    tabela = pd.DataFrame({
        "Mês": df["Mês"], "Salários": df["receitas"], "A receber": df["a_receber"],
        "Contas fixas": df["fixas"], "Parcelas": df["parcelas"], "Avulsos": df["avulsos"],
        "Saldo do mês": df["saldo_mes"], "Saldo acumulado": df["saldo_acumulado"],
    })
    st.dataframe(moeda(tabela, [c for c in tabela.columns if c != "Mês"]), hide_index=True)

    # Gráfico de barras ordenado cronologicamente com Altair
    chart_barras = alt.Chart(df).mark_bar().encode(
        x=alt.X("Mês:N", sort=list(df["Mês"]), title="Mês"),
        y=alt.Y("saldo_mes:Q", title="Saldo do mês (R$)"),
        tooltip=["Mês", "saldo_mes"]
    ).properties(height=300)
    st.altair_chart(chart_barras, use_container_width=True)

    # Gráfico de linha para o saldo acumulado ordenado com Altair
    chart_linha = alt.Chart(df).mark_line(point=True).encode(
        x=alt.X("Mês:N", sort=list(df["Mês"]), title="Mês"),
        y=alt.Y("saldo_acumulado:Q", title="Saldo acumulado (R$)"),
        tooltip=["Mês", "saldo_acumulado"]
    ).properties(height=300)
    st.altair_chart(chart_linha, use_container_width=True)

    st.subheader("Detalhe de um mês")
    mes = st.selectbox("Mês", [m["mes"] for m in meses], format_func=rotulo_mes, key="proj_detalhe")
    with transacao() as conn:
        extrato = fc.extrato_previsto(conn, mes)
    nomes_origem = {"receita": "Salário/receita", "a_receber": "A receber", "conta_fixa": "Conta fixa",
                    "parcela": "Parcela", "avulso": "Avulso"}
    for titulo, chave in (("Entradas", "entradas"), ("Saídas", "saidas")):
        st.markdown(f"**{titulo}**")
        linhas = extrato[chave]
        if not linhas:
            st.caption("Nenhum lançamento.")
            continue
        detalhe = pd.DataFrame(linhas)
        detalhe["Tipo"] = detalhe["origem"].map(nomes_origem)
        detalhe = detalhe[["data", "Tipo", "descricao", "pessoa", "ref", "valor"]]
        detalhe.columns = ["Data", "Tipo", "Descrição", "Pessoa", "Referência", "Valor"]
        st.dataframe(moeda(detalhe.fillna(""), ["Valor"]), hide_index=True)


# ----------------------------------------------------------------------
def _aba_receitas() -> None:
    with transacao() as conn:
        pessoas = {p["id"]: p["nome"] for p in fc.listar_pessoas(conn)}

    with st.form("form_receita", clear_on_submit=True):
        c1, c2, c3 = st.columns(3)
        pessoa_id = c1.selectbox("Pessoa", list(pessoas), format_func=pessoas.get)
        descricao = c2.text_input("Descrição", value="Salário")
        valor = c3.number_input("Valor mensal (R$)", min_value=0.0, step=100.0, format="%.2f")
        c4, c5, c6 = st.columns(3)
        mes_inicio = seletor_mes("A partir de", "rec_inicio", container=c4)
        recorrente = c5.checkbox("Repete todo mês", value=True)
        tem_fim = c6.checkbox("Tem mês final")
        mes_fim = seletor_mes("Mês final (se marcado)", "rec_fim", container=c6)
        enviado = st.form_submit_button("Adicionar receita")
    if enviado:
        try:
            with transacao() as conn:
                fc.adicionar_receita(conn, pessoa_id, descricao.strip() or "Receita", valor, mes_inicio,
                                   mes_fim if (tem_fim and recorrente) else None, recorrente)
            st.success("Receita adicionada.")
        except ValueError as erro:
            st.error(str(erro))

    with transacao() as conn:
        linhas = fc.listar_receitas(conn)
    for linha in linhas:
        linha["recorrente"] = "Sim" if linha["recorrente"] else "Só no mês inicial"
        linha["mes_fim"] = linha["mes_fim"] or "-"
    tabela_com_exclusao(
        renomear(linhas, {"id": "id", "pessoa": "Pessoa", "descricao": "Descrição", "valor": "Valor",
                          "mes_inicio": "Início", "mes_fim": "Fim", "recorrente": "Repete"}),
        "tab_receitas", "Excluir selecionadas", _excluir(fc.remover_receita), ["Valor"])


# ----------------------------------------------------------------------
def _aba_contas_fixas() -> None:
    with st.form("form_conta_fixa", clear_on_submit=True):
        c1, c2, c3 = st.columns(3)
        descricao = c1.text_input("Descrição (ex.: Aluguel)")
        categoria = c2.text_input("Categoria (opcional)")
        valor = c3.number_input("Valor mensal (R$)", min_value=0.0, step=10.0, format="%.2f")
        c4, c5, c6 = st.columns(3)
        dia = c4.number_input("Dia do vencimento", min_value=1, max_value=31, value=5, step=1)
        mes_inicio = seletor_mes("A partir de", "fixa_inicio", container=c5)
        tem_fim = c6.checkbox("Tem mês final")
        mes_fim = seletor_mes("Mês final (se marcado)", "fixa_fim", container=c6)
        enviado = st.form_submit_button("Adicionar conta fixa")
    if enviado:
        if not descricao.strip():
            st.error("Informe a descrição.")
        else:
            try:
                with transacao() as conn:
                    fc.adicionar_conta_fixa(conn, descricao.strip(), valor, int(dia), mes_inicio,
                                            mes_fim if tem_fim else None, categoria.strip())
                st.success("Conta fixa adicionada.")
            except ValueError as erro:
                st.error(str(erro))

    with transacao() as conn:
        linhas = fc.listar_contas_fixas(conn)
    for linha in linhas:
        linha["categoria"] = linha["categoria"] or "-"
        linha["mes_fim"] = linha["mes_fim"] or "-"
    tabela_com_exclusao(
        renomear(linhas, {"id": "id", "descricao": "Descrição", "categoria": "Categoria", "valor": "Valor",
                          "dia_vencimento": "Dia", "mes_inicio": "Início", "mes_fim": "Fim"}),
        "tab_fixas", "Excluir selecionadas", _excluir(fc.remover_conta_fixa), ["Valor"])


# ----------------------------------------------------------------------
def _aba_parcelamentos() -> None:
    st.subheader("Cartões de crédito")
    with st.form("form_cartao", clear_on_submit=True):
        c1, c2, c3, c4 = st.columns(4)
        nome = c1.text_input("Nome (ex.: Nubank David)")
        banco = c2.text_input("Banco/fintech")
        fechamento = c3.number_input("Dia de fechamento", min_value=1, max_value=31, value=25, step=1)
        vencimento = c4.number_input("Dia de vencimento", min_value=1, max_value=31, value=5, step=1)
        enviado = st.form_submit_button("Adicionar cartão")
    if enviado:
        if not nome.strip():
            st.error("Informe o nome do cartão.")
        else:
            try:
                with transacao() as conn:
                    fc.adicionar_cartao(conn, nome.strip(), banco.strip(), int(fechamento), int(vencimento))
                st.success("Cartão adicionado.")
            except Exception as erro:  # nome duplicado cai aqui (IntegrityError)
                st.error(f"Não foi possível salvar o cartão: {erro}")

    with transacao() as conn:
        cartoes = fc.listar_cartoes(conn)
    tabela_com_exclusao(
        renomear([{**c, "banco": c["banco"] or "-"} for c in cartoes],
                 {"id": "id", "nome": "Cartão", "banco": "Banco", "dia_fechamento": "Fecha dia",
                  "dia_vencimento": "Vence dia"}),
        "tab_cartoes", "Excluir cartões selecionados", _excluir(fc.remover_cartao))

    st.divider()
    st.subheader("Novo parcelamento")
    st.caption("Compra parcelada no cartão, empréstimo ou financiamento. A 'competência' é o mês em que a "
               "parcela pesa no caixa (para cartão, o mês de vencimento da fatura).")
    opcoes_cartao = {None: "—"} | {c["id"]: c["nome"] for c in cartoes}
    with st.form("form_parcelamento", clear_on_submit=True):
        c1, c2, c3 = st.columns(3)
        descricao = c1.text_input("Descrição (ex.: Notebook, Financiamento do carro)")
        tipo = c2.selectbox("Tipo", list(ROTULO_TIPO), format_func=ROTULO_TIPO.get)
        cartao_id = c3.selectbox("Cartão (se for compra no cartão)", list(opcoes_cartao),
                                 format_func=opcoes_cartao.get)
        c4, c5, c6 = st.columns(3)
        valor_parcela = c4.number_input("Valor da parcela (R$)", min_value=0.0, step=10.0, format="%.2f")
        valor_total = c5.number_input("OU valor total (R$)", min_value=0.0, step=50.0, format="%.2f",
                                      help="Use só se não souber a parcela. Se os dois forem preenchidos, vale a parcela.")
        num_parcelas = c6.number_input("Total de parcelas", min_value=1, max_value=480, value=10, step=1)
        c7, c8 = st.columns(2)
        numero_inicial = c7.number_input("Parcela em que você está", min_value=1, max_value=480, value=1, step=1,
                                         help="Para contratos em andamento: se já pagou 6 de 36, informe 7.")
        mes_primeira = seletor_mes("Mês dessa primeira parcela a lançar", "parc_mes", container=c8)
        enviado = st.form_submit_button("Criar parcelamento")
    if enviado:
        try:
            if not descricao.strip():
                raise ValueError("Informe a descrição.")
            if valor_parcela <= 0 and valor_total <= 0:
                raise ValueError("Informe o valor da parcela ou o valor total.")
            with transacao() as conn:
                fc.criar_parcelamento(
                    conn, descricao.strip(), tipo, int(num_parcelas), mes_primeira,
                    valor_parcela=valor_parcela if valor_parcela > 0 else None,
                    valor_total=None if valor_parcela > 0 else valor_total,
                    cartao_id=cartao_id, numero_inicial=int(numero_inicial))
            st.success("Parcelamento criado; as parcelas já entram na projeção.")
        except ValueError as erro:
            st.error(str(erro))

    st.divider()
    st.subheader("Parcelamentos cadastrados")
    with transacao() as conn:
        parcelamentos = fc.listar_parcelamentos(conn, mes_atual())
    linhas = [{**p, "tipo": ROTULO_TIPO[p["tipo"]], "cartao": p["cartao"] or "-",
               "ultima_competencia": rotulo_mes(p["ultima_competencia"]) if p["ultima_competencia"] else "-"}
              for p in parcelamentos]
    tabela_com_exclusao(
        renomear(linhas, {"id": "id", "descricao": "Descrição", "tipo": "Tipo", "cartao": "Cartão",
                          "valor_parcela": "Parcela", "num_parcelas": "Nº parcelas",
                          "parcelas_restantes": "Restantes", "saldo_restante": "Falta pagar",
                          "ultima_competencia": "Termina em"}),
        "tab_parcelamentos", "Excluir parcelamentos selecionados", _excluir(fc.remover_parcelamento),
        ["Parcela", "Falta pagar"])

    if parcelamentos:
        st.markdown("**Marcar parcelas como pagas**")
        escolhido = st.selectbox("Parcelamento", [p["id"] for p in parcelamentos], key="parc_escolhido",
                                 format_func=lambda i: next(p["descricao"] for p in parcelamentos if p["id"] == i))
        with transacao() as conn:
            parcelas = fc.listar_parcelas(conn, escolhido)
        df = pd.DataFrame(parcelas)
        df["competencia"] = df["competencia"].map(rotulo_mes)
        df["paga"] = df["paga"].astype(bool)
        editado = st.data_editor(
            df, key=f"parcelas_{escolhido}", hide_index=True, column_config={
                "id": None, "numero": "Nº", "competencia": "Competência",
                "valor": st.column_config.NumberColumn("Valor", format="%.2f"),
                "paga": st.column_config.CheckboxColumn("Paga")},
            disabled=["numero", "competencia", "valor"])
        if st.button("Salvar situação das parcelas", key="parc_salvar"):
            with transacao() as conn:
                for original, novo in zip(df.itertuples(), editado.itertuples()):
                    if bool(original.paga) != bool(novo.paga):
                        fc.marcar_parcela_paga(conn, int(original.id), bool(novo.paga))
            st.rerun()

    st.divider()
    st.subheader("Fatura dos cartões no mês")
    mes = seletor_mes("Mês", "fatura_mes")
    with transacao() as conn:
        itens = fc.faturas_do_mes(conn, mes)
    if not itens:
        st.info("Nenhuma parcela de cartão neste mês.")
    for cartao, grupo in pd.DataFrame(itens).groupby("cartao") if itens else []:
        st.markdown(f"**{cartao}** — total {formatar_brl(grupo['valor'].sum())}")
        exibicao = pd.DataFrame({
            "Descrição": grupo["descricao"], "Parcela": grupo["numero"].astype(str) + "/" + grupo["num_parcelas"].astype(str),
            "Valor": grupo["valor"], "Paga": grupo["paga"].astype(bool).map({True: "Sim", False: "Não"})})
        st.dataframe(moeda(exibicao, ["Valor"]), hide_index=True)


# ----------------------------------------------------------------------
def _aba_avulsos() -> None:
    with st.form("form_avulso", clear_on_submit=True):
        c1, c2, c3, c4 = st.columns(4)
        descricao = c1.text_input("Descrição (ex.: IPVA, Freela)")
        tipo = c2.radio("Tipo", ["pagar", "receber"], format_func=lambda t: "A pagar" if t == "pagar" else "A receber",
                        horizontal=True)
        valor = c3.number_input("Valor (R$)", min_value=0.0, step=10.0, format="%.2f")
        data = c4.date_input("Data", value=date.today())
        enviado = st.form_submit_button("Adicionar lançamento")
    if enviado:
        if not descricao.strip():
            st.error("Informe a descrição.")
        else:
            with transacao() as conn:
                fc.adicionar_lancamento(conn, descricao.strip(), tipo, valor, data)
            st.success("Lançamento adicionado.")

    mes = seletor_mes("Mês exibido", "avulso_mes")
    with transacao() as conn:
        linhas = fc.listar_lancamentos(conn, mes)
    if not linhas:
        st.info("Nenhum lançamento neste mês.")
        return
    df = pd.DataFrame(linhas)
    df["tipo"] = df["tipo"].map({"pagar": "A pagar", "receber": "A receber"})
    df["pago"] = df["pago"].astype(bool)
    df.insert(0, "excluir", False)
    editado = st.data_editor(
        df, key=f"avulsos_{mes}", hide_index=True, column_config={
            "id": None, "excluir": st.column_config.CheckboxColumn("Excluir"), "data": "Data",
            "descricao": "Descrição", "tipo": "Tipo",
            "valor": st.column_config.NumberColumn("Valor", format="%.2f"),
            "pago": st.column_config.CheckboxColumn("Pago/recebido")},
        disabled=["data", "descricao", "tipo", "valor"])
    if st.button("Aplicar alterações", key="avulso_aplicar"):
        with transacao() as conn:
            for original, novo in zip(df.itertuples(), editado.itertuples()):
                if novo.excluir:
                    fc.remover_lancamento(conn, int(original.id))
                elif bool(original.pago) != bool(novo.pago):
                    fc.marcar_lancamento_pago(conn, int(original.id), bool(novo.pago))
        st.rerun()


def _excluir(funcao):
    """Adapta uma função do core (conn, id) para o formato esperado por `tabela_com_exclusao`."""
    def executar(item_id: int) -> None:
        with transacao() as conn:
            funcao(conn, item_id)
    return executar
