"""Tela inicial: visão do mês, projeção dos próximos meses e acerto do casal."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from core.compras import gasto_mensal
from core.database import transacao
from core.divisao import saldo_casal
from core.fluxo_caixa import projecao
from core.util import formatar_brl, rotulo_mes
from views.componentes import seletor_mes


def render() -> None:
    st.title("Painel")
    mes = seletor_mes("Mês de referência", "painel_mes")

    with transacao() as conn:
        meses = projecao(conn, mes, 12)
        acerto = saldo_casal(conn)
        gastos = {g["mes"]: g["total"] for g in gasto_mensal(conn)}

    atual = meses[0]
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Entradas previstas", formatar_brl(atual["entradas"]))
    c2.metric("Saídas previstas", formatar_brl(atual["saidas"]))
    c3.metric("Saldo do mês", formatar_brl(atual["saldo_mes"]))
    c4.metric("Compras de mercado no mês", formatar_brl(gastos.get(mes, 0.0)))

    st.subheader("Acerto do casal")
    st.info(acerto["resumo"])

    st.subheader("Projeção dos próximos 12 meses")
    df = pd.DataFrame(meses)
    
    # 1. Garante a ordenação cronológica real baseada no formato 'YYYY-MM'
    df = df.sort_values("mes")
    
    df["Mês"] = df["mes"].map(rotulo_mes)
    
    # 2. Define o 'Mês' como índice mantendo a ordem correta no gráfico
    grafico = df.set_index("Mês")[["saldo_mes", "saldo_acumulado"]]
    grafico.columns = ["Saldo do mês", "Saldo acumulado"]
    
    st.bar_chart(grafico["Saldo do mês"])
    st.line_chart(grafico["Saldo acumulado"])

    if atual["saldo_mes"] < 0:
        st.warning(f"Em {rotulo_mes(mes)} as saídas previstas superam as entradas.")
    negativos = [rotulo_mes(m["mes"]) for m in meses if m["saldo_acumulado"] < 0]
    if negativos:
        st.warning("Saldo acumulado negativo em: " + ", ".join(negativos) + ".")
    