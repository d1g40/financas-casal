"""Ponto de entrada do app: `streamlit run app.py`."""
import os
from datetime import date

import streamlit as st

st.set_page_config(page_title="Finanças do Casal", page_icon="💰", layout="wide")

# Na nuvem, o endereço do banco vem dos segredos do Streamlit; o resto do código lê DATABASE_URL.
try:
    if st.secrets.get("DATABASE_URL") and not os.environ.get("DATABASE_URL"):
        os.environ["DATABASE_URL"] = str(st.secrets["DATABASE_URL"])
except Exception:  # sem arquivo de segredos: uso local com SQLite
    pass

from core.backup import gerar_backup_zip  # noqa: E402
from core.database import garantir_banco, modo_banco, transacao  # noqa: E402
from views import casal, fluxo_caixa, painel, supermercado  # noqa: E402
from views.acesso import exigir_acesso  # noqa: E402

exigir_acesso()  # nada abaixo roda sem login válido quando o app está na nuvem


@st.cache_resource
def _iniciar_banco() -> bool:
    """Cria as tabelas na primeira execução (uma vez por sessão do servidor)."""
    garantir_banco()
    return True


_iniciar_banco()

PAGINAS = {
    "Painel": painel.render,
    "Fluxo de caixa": fluxo_caixa.render,
    "Supermercado": supermercado.render,
    "Casal (divisão e acertos)": casal.render,
}

st.sidebar.title("Finanças do Casal")
escolha = st.sidebar.radio("Navegação", list(PAGINAS), label_visibility="collapsed")

with st.sidebar.expander("Backup dos dados"):
    st.caption("Baixa um .zip com todas as tabelas em CSV. Faça de vez em quando e guarde fora do app.")
    if st.button("Preparar backup", key="backup_preparar"):
        with transacao() as conn:
            st.session_state["backup_zip"] = gerar_backup_zip(conn)
    if "backup_zip" in st.session_state:
        st.download_button("Baixar backup (.zip)", st.session_state["backup_zip"], mime="application/zip",
                           file_name=f"backup-financas-{date.today():%Y-%m-%d}.zip", key="backup_baixar")
    st.caption(f"Banco em uso: {'PostgreSQL (nuvem)' if modo_banco() == 'postgres' else 'SQLite (este computador)'}")

PAGINAS[escolha]()
