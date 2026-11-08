"""Componentes de interface reutilizados pelas telas."""
from __future__ import annotations

from typing import Callable, Iterable, Optional

import pandas as pd
import streamlit as st

from core.util import formatar_brl, intervalo_meses, mes_atual, rotulo_mes, somar_meses


def seletor_mes(rotulo: str, chave: str, padrao: Optional[str] = None, container=st,
                meses_passados: int = 12, meses_futuros: int = 60) -> str:
    """Caixa de seleção de mês; devolve 'YYYY-MM' (o Streamlit não tem seletor de mês nativo)."""
    base = mes_atual()
    opcoes = intervalo_meses(somar_meses(base, -meses_passados), meses_passados + meses_futuros + 1)
    padrao = padrao or base
    indice = opcoes.index(padrao) if padrao in opcoes else meses_passados
    return container.selectbox(rotulo, opcoes, index=indice, format_func=rotulo_mes, key=chave)


def moeda(df: pd.DataFrame, colunas: Iterable[str]) -> pd.DataFrame:
    """Cópia do DataFrame com as colunas indicadas formatadas como R$ 1.234,56."""
    saida = df.copy()
    for coluna in colunas:
        saida[coluna] = saida[coluna].map(formatar_brl)
    return saida


def renomear(linhas: list[dict], mapa: dict[str, str]) -> list[dict]:
    """Seleciona e renomeia chaves: mapa = {'chave_interna': 'Título exibido'} (mantenha 'id': 'id')."""
    return [{novo: linha[antigo] for antigo, novo in mapa.items()} for linha in linhas]


def tabela_com_exclusao(linhas: list[dict], chave: str, rotulo_botao: str,
                        excluir: Callable[[int], None], colunas_moeda: Iterable[str] = ()) -> None:
    """Tabela com caixa 'Excluir' por linha. `linhas` precisa ter a coluna 'id' (que fica oculta)."""
    if not linhas:
        st.info("Nada cadastrado ainda.")
        return
    df = pd.DataFrame(linhas)
    exibicao = moeda(df, colunas_moeda)
    exibicao.insert(0, "Excluir", False)
    editado = st.data_editor(
        exibicao, key=chave, hide_index=True, column_config={"id": None},
        disabled=[c for c in exibicao.columns if c != "Excluir"],
    )
    if st.button(rotulo_botao, key=f"{chave}_botao"):
        ids = df.loc[editado["Excluir"].astype(bool), "id"].tolist()
        if not ids:
            st.warning("Marque pelo menos uma linha em 'Excluir'.")
            return
        try:
            for item_id in ids:
                excluir(int(item_id))
        except ValueError as erro:
            st.error(str(erro))
            return
        st.rerun()
