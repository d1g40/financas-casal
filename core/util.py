"""Utilitários: competências (YYYY-MM), moeda em reais, normalização de texto e validação de EAN."""
from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime
from typing import Optional, Union

MESES_ABREV = ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"]


# ----------------------------------------------------------------------
# Competências (mês no formato 'YYYY-MM')
# ----------------------------------------------------------------------
def mes_atual() -> str:
    hoje = date.today()
    return f"{hoje.year:04d}-{hoje.month:02d}"


def validar_mes(mes: str) -> str:
    """Garante o formato 'YYYY-MM' e devolve o próprio valor."""
    if not isinstance(mes, str) or not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", mes):
        raise ValueError(f"Mês inválido: {mes!r} (use o formato AAAA-MM).")
    return mes


def somar_meses(mes: str, n: int) -> str:
    """'2026-11' + 3 -> '2027-02'. Aceita n negativo."""
    validar_mes(mes)
    total = int(mes[:4]) * 12 + (int(mes[5:7]) - 1) + n
    return f"{total // 12:04d}-{total % 12 + 1:02d}"


def intervalo_meses(inicio: str, quantidade: int) -> list[str]:
    """Lista `quantidade` meses consecutivos a partir de `inicio` (inclusive)."""
    return [somar_meses(inicio, i) for i in range(quantidade)]


def mes_de_data(valor: Union[date, str]) -> str:
    """Converte date ou 'YYYY-MM-DD' em 'YYYY-MM'."""
    if isinstance(valor, (date, datetime)):
        return f"{valor.year:04d}-{valor.month:02d}"
    return validar_mes(str(valor)[:7])


def rotulo_mes(mes: str) -> str:
    """'2026-11' -> 'nov/2026' (para exibição)."""
    return f"{MESES_ABREV[int(mes[5:7]) - 1]}/{mes[:4]}"


def para_iso(valor: Union[date, str]) -> str:
    """Converte date em 'YYYY-MM-DD' (strings são validadas e devolvidas)."""
    if isinstance(valor, (date, datetime)):
        return valor.strftime("%Y-%m-%d")
    return date.fromisoformat(str(valor)[:10]).isoformat()


# ----------------------------------------------------------------------
# Moeda e texto
# ----------------------------------------------------------------------
def formatar_brl(valor: float) -> str:
    """1234.5 -> 'R$ 1.234,50'."""
    valor = 0.0 if abs(valor) < 0.005 else valor
    sinal = "-" if valor < 0 else ""
    texto = f"{abs(valor):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{sinal}R$ {texto}"


def normalizar_texto(texto: str) -> str:
    """Sem acentos, maiúsculo e com espaços únicos - usado para casar nomes de produtos."""
    sem_acento = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"\s+", " ", sem_acento).strip().upper()


def ean_valido(codigo: Optional[str]) -> Optional[str]:
    """Devolve o GTIN/EAN se for um código de barras de verdade; senão None.

    Rejeita 'SEM GTIN', códigos internos curtos, códigos de uso restrito do
    estabelecimento (prefixos 20-29 em EAN-13, que variam de loja para loja)
    e códigos com dígito verificador incorreto.
    """
    if not codigo:
        return None
    codigo = codigo.strip()
    if not codigo.isdigit() or len(codigo) not in (8, 12, 13, 14):
        return None
    if len(codigo) == 13 and codigo[0] == "2":
        return None
    corpo, digito = codigo[:-1], int(codigo[-1])
    soma = sum(int(d) * (3 if i % 2 == 0 else 1) for i, d in enumerate(reversed(corpo)))
    return codigo if (10 - soma % 10) % 10 == digito else None
