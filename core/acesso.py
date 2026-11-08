"""Regra de acesso (quem pode entrar), separada da interface para ser testável.

Na nuvem (PostgreSQL) o login é OBRIGATÓRIO: se ele não estiver configurado, o app
recusa-se a abrir em vez de ficar aberto na internet. Só entram os e-mails listados
em [acesso] nos segredos; cada e-mail está ligado ao nome de uma pessoa do casal.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Optional


@dataclass(frozen=True)
class Decisao:
    estado: str                 # 'liberado' | 'sem_login' | 'nao_configurado' | 'pedir_login' | 'negado'
    pessoa: Optional[str] = None
    email: Optional[str] = None


def mapa_emails(acesso: Optional[Mapping[str, str]]) -> dict[str, str]:
    """{'David': 'a@x.com', 'Mylena': 'b@x.com'} -> {'a@x.com': 'David', 'b@x.com': 'Mylena'}."""
    return {str(email).strip().lower(): str(nome).strip()
            for nome, email in (acesso or {}).items() if str(email).strip()}


def decidir_acesso(*, na_nuvem: bool, auth_configurado: bool, emails: Mapping[str, str],
                   logado: bool, email: Optional[str], email_verificado: Optional[bool] = True) -> Decisao:
    """Decide o que mostrar: liberar, pedir login, negar ou recusar por falta de configuração."""
    if not na_nuvem and not auth_configurado:
        return Decisao("sem_login")            # uso local: abre direto, sem login
    if not auth_configurado or not emails:
        return Decisao("nao_configurado")      # na nuvem sem login configurado: nunca abre
    if not logado:
        return Decisao("pedir_login")
    email_normalizado = (email or "").strip().lower()
    pessoa = emails.get(email_normalizado)
    if pessoa is None or email_verificado is False:
        return Decisao("negado", email=email_normalizado or None)
    return Decisao("liberado", pessoa=pessoa, email=email_normalizado)
