"""Módulo 3 - Divisão de despesas do casal e saldo de acerto de contas.

Para cada pessoa:
    saldo = o que PAGOU nas compras - o que CONSUMIU (itens próprios + sua parte do rateio)
            + acertos que enviou - acertos que recebeu
Saldo positivo = a pessoa tem a receber do outro; negativo = deve ao outro.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Optional

from .util import formatar_brl, para_iso


def saldo_casal(conn) -> dict[str, Any]:
    pessoas = {r["id"]: r["nome"] for r in conn.execute("SELECT id, nome FROM pessoa ORDER BY id")}
    pago = dict.fromkeys(pessoas, 0.0)
    consumo = dict.fromkeys(pessoas, 0.0)
    enviados = dict.fromkeys(pessoas, 0.0)
    recebidos = dict.fromkeys(pessoas, 0.0)

    pagamentos: dict[int, dict[int, float]] = defaultdict(dict)
    for r in conn.execute("SELECT compra_id, pessoa_id, valor FROM compra_pagamento"):
        pagamentos[r["compra_id"]][r["pessoa_id"]] = r["valor"]

    rateios: dict[int, dict[int, float]] = defaultdict(dict)
    for r in conn.execute("SELECT compra_id, pessoa_id, percentual FROM compra_rateio"):
        rateios[r["compra_id"]][r["pessoa_id"]] = r["percentual"]

    itens: dict[int, dict[Optional[int], float]] = defaultdict(lambda: defaultdict(float))
    for r in conn.execute("SELECT compra_id, dono_id, SUM(valor_total) AS soma FROM compra_item "
                          "GROUP BY compra_id, dono_id"):
        itens[r["compra_id"]][r["dono_id"]] += r["soma"]

    por_compra: list[dict[str, Any]] = []
    compras = conn.execute(
        "SELECT c.id, c.data, c.total, e.nome AS mercado FROM compra c "
        "JOIN estabelecimento e ON e.id = c.estabelecimento_id ORDER BY c.data DESC, c.id DESC").fetchall()
    for c in compras:
        consumo_compra = dict.fromkeys(pessoas, 0.0)
        for dono_id, soma in itens[c["id"]].items():
            if dono_id is None:  # item conjunto: divide pelo rateio configurado na compra
                for pessoa_id, percentual in rateios[c["id"]].items():
                    consumo_compra[pessoa_id] += soma * percentual / 100
            else:
                consumo_compra[dono_id] += soma
        for pessoa_id, valor in pagamentos[c["id"]].items():
            pago[pessoa_id] += valor
        for pessoa_id, valor in consumo_compra.items():
            consumo[pessoa_id] += valor
        por_compra.append({
            "id": c["id"], "data": c["data"], "mercado": c["mercado"], "total": c["total"],
            "pagou": {pessoas[p]: round(v, 2) for p, v in pagamentos[c["id"]].items()},
            "consumo": {pessoas[p]: round(v, 2) for p, v in consumo_compra.items()},
        })

    for r in conn.execute("SELECT de_pessoa_id, para_pessoa_id, valor FROM acerto"):
        enviados[r["de_pessoa_id"]] += r["valor"]
        recebidos[r["para_pessoa_id"]] += r["valor"]

    resumo_pessoas = []
    for pessoa_id, nome in pessoas.items():
        saldo = pago[pessoa_id] - consumo[pessoa_id] + enviados[pessoa_id] - recebidos[pessoa_id]
        resumo_pessoas.append({"id": pessoa_id, "nome": nome, "pago": round(pago[pessoa_id], 2),
                               "consumo": round(consumo[pessoa_id], 2),
                               "acertos_enviados": round(enviados[pessoa_id], 2),
                               "acertos_recebidos": round(recebidos[pessoa_id], 2),
                               "saldo": round(saldo, 2)})

    credor = max(resumo_pessoas, key=lambda p: p["saldo"], default=None)
    devedor = min(resumo_pessoas, key=lambda p: p["saldo"], default=None)
    if credor is None or credor["saldo"] < 0.01:
        texto, valor, credor_id, devedor_id = "Contas em dia: ninguém deve nada.", 0.0, None, None
    else:
        valor, credor_id, devedor_id = credor["saldo"], credor["id"], devedor["id"]
        texto = f"{devedor['nome']} deve {formatar_brl(valor)} a {credor['nome']}."
    return {"pessoas": resumo_pessoas, "resumo": texto, "valor": valor,
            "credor_id": credor_id, "devedor_id": devedor_id, "por_compra": por_compra}


def registrar_acerto(conn, de_pessoa_id: int, para_pessoa_id: int, valor: float, data,
                     observacao: Optional[str] = None) -> int:
    """Registra que `de` transferiu `valor` para `para` (quitando parte ou todo o saldo)."""
    if de_pessoa_id == para_pessoa_id:
        raise ValueError("Quem paga e quem recebe devem ser pessoas diferentes.")
    if valor <= 0:
        raise ValueError("O valor do acerto deve ser maior que zero.")
    return conn.execute(
        "INSERT INTO acerto (data, de_pessoa_id, para_pessoa_id, valor, observacao) VALUES (?, ?, ?, ?, ?)",
        (para_iso(data), de_pessoa_id, para_pessoa_id, round(valor, 2), observacao or None),
    ).lastrowid


def listar_acertos(conn) -> list[dict[str, Any]]:
    return [dict(r) for r in conn.execute(
        "SELECT a.id, a.data, d.nome AS de, p.nome AS para, a.valor, a.observacao "
        "FROM acerto a JOIN pessoa d ON d.id = a.de_pessoa_id JOIN pessoa p ON p.id = a.para_pessoa_id "
        "ORDER BY a.data DESC, a.id DESC")]


def remover_acerto(conn, acerto_id: int) -> None:
    conn.execute("DELETE FROM acerto WHERE id = ?", (acerto_id,))
