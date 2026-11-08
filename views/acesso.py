"""Tela de login com Google e bloqueio de quem não está na lista (usa o login nativo do Streamlit)."""
from __future__ import annotations

from typing import Optional

import streamlit as st

from core.acesso import decidir_acesso, mapa_emails
from core.database import modo_banco

AJUDA_CONFIGURACAO = (
    "O login ainda não foi configurado, então o app **não abre** para proteger os dados de vocês. "
    "Preencha os blocos `[auth]` e `[acesso]` nos segredos do app "
    "(modelo em `.streamlit/secrets.toml.example`; passo a passo no README)."
)


def _segredo(chave: str):
    try:
        return st.secrets[chave]
    except Exception:  # sem arquivo de segredos (uso local) ou bloco ausente
        return None


def exigir_acesso() -> Optional[str]:
    """Garante que só os e-mails permitidos entram. Devolve o nome da pessoa logada (None no uso local sem login)."""
    auth = _segredo("auth")
    emails = mapa_emails(dict(_segredo("acesso") or {}))
    usuario = st.user if auth else None
    logado = bool(usuario is not None and getattr(usuario, "is_logged_in", False))

    decisao = decidir_acesso(
        na_nuvem=modo_banco() == "postgres",
        auth_configurado=bool(auth),
        emails=emails,
        logado=logado,
        email=getattr(usuario, "email", None) if logado else None,
        email_verificado=getattr(usuario, "email_verified", True) if logado else True,
    )

    if decisao.estado == "sem_login":
        return None
    if decisao.estado == "nao_configurado":
        st.title("Finanças do Casal")
        st.error(AJUDA_CONFIGURACAO)
        st.stop()
    if decisao.estado == "pedir_login":
        st.title("Finanças do Casal")
        st.write("Entre com a conta Google que está na lista de acesso.")
        if st.button("Entrar com Google", type="primary"):
            st.login()
        st.stop()
    if decisao.estado == "negado":
        st.title("Finanças do Casal")
        st.error(f"A conta {decisao.email or '(sem e-mail)'} não tem acesso a este app.")
        st.button("Sair e tentar outra conta", on_click=st.logout)
        st.stop()

    st.session_state["pessoa_logada"] = decisao.pessoa
    st.sidebar.caption(f"Conectado como **{decisao.pessoa}**")
    st.sidebar.button("Sair", on_click=st.logout)
    return decisao.pessoa
