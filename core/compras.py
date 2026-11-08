"""Registro de compras de supermercado (XML ou manual): itens, pagadores, rateio e preços.

Uma compra une os módulos 2 e 3: grava os itens e o histórico de preços
(módulo 2) e quem pagou / como dividir (módulo 3).
"""
from __future__ import annotations

from typing import Any, Optional

from .supermercado import obter_ou_criar_produto, registrar_preco
from .util import para_iso

TOLERANCIA = 0.011  # diferença aceita (em reais / pontos percentuais) por causa de arredondamentos


class CompraDuplicadaError(ValueError):
    """A nota (chave de acesso) já foi importada antes."""


def compra_existe(conn, chave_nfe: Optional[str]) -> bool:
    if not chave_nfe:
        return False
    return conn.execute("SELECT 1 FROM compra WHERE chave_nfe = ?", (chave_nfe,)).fetchone() is not None


def registrar_compra(conn, *, estabelecimento_id: int, data, itens: list[dict[str, Any]],
                     pagamentos: dict[int, float], rateio: dict[int, float],
                     chave_nfe: Optional[str] = None, origem: str = "manual",
                     observacao: Optional[str] = None) -> int:
    """Grava a compra completa em uma única transação (use dentro de `transacao()`).

    itens      : [{descricao, quantidade, preco_unitario, unidade?, ean?, valor_total?, dono_id?}]
                 dono_id=None -> item conjunto (entra no rateio); dono_id=<pessoa> -> uso individual
    pagamentos : {pessoa_id: valor pago}; a soma precisa fechar com o total dos itens
    rateio     : {pessoa_id: percentual} dos itens conjuntos; a soma precisa dar 100
    """
    if not itens:
        raise ValueError("A compra não tem itens.")
    if compra_existe(conn, chave_nfe):
        raise CompraDuplicadaError("Esta nota fiscal já foi importada.")

    normalizados: list[dict[str, Any]] = []
    for item in itens:
        descricao = " ".join(str(item.get("descricao", "")).split())
        quantidade = float(item.get("quantidade") or 0)
        preco = float(item.get("preco_unitario") or 0)
        if not descricao:
            raise ValueError("Há um item sem descrição.")
        if quantidade <= 0 or preco < 0:
            raise ValueError(f"Item '{descricao}': quantidade deve ser maior que zero e preço não negativo.")
        valor_total = item.get("valor_total")
        valor_total = round(quantidade * preco, 2) if valor_total is None else round(float(valor_total), 2)
        normalizados.append({**item, "descricao": descricao, "quantidade": quantidade,
                             "preco_unitario": preco, "valor_total": valor_total})

    total = round(sum(i["valor_total"] for i in normalizados), 2)
    if abs(sum(pagamentos.values()) - total) > TOLERANCIA:
        raise ValueError(f"Os pagamentos ({sum(pagamentos.values()):.2f}) não fecham com o total da compra ({total:.2f}).")
    if abs(sum(rateio.values()) - 100) > TOLERANCIA:
        raise ValueError("Os percentuais do rateio devem somar 100%.")

    data_iso = para_iso(data)
    compra_id = conn.execute(
        "INSERT INTO compra (estabelecimento_id, data, chave_nfe, origem, total, observacao) VALUES (?, ?, ?, ?, ?, ?)",
        (estabelecimento_id, data_iso, chave_nfe, origem, total, observacao),
    ).lastrowid

    for pessoa_id, valor in pagamentos.items():
        if valor > 0:
            conn.execute("INSERT INTO compra_pagamento (compra_id, pessoa_id, valor) VALUES (?, ?, ?)",
                         (compra_id, pessoa_id, round(valor, 2)))
    for pessoa_id, percentual in rateio.items():
        conn.execute("INSERT INTO compra_rateio (compra_id, pessoa_id, percentual) VALUES (?, ?, ?)",
                     (compra_id, pessoa_id, percentual))

    for item in normalizados:
        unidade = (item.get("unidade") or "UN").upper()
        produto_id = obter_ou_criar_produto(conn, item["descricao"], item.get("ean"), unidade)
        item_id = conn.execute(
            "INSERT INTO compra_item (compra_id, produto_id, descricao_original, quantidade, unidade, "
            "preco_unitario, valor_total, dono_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (compra_id, produto_id, item["descricao"], item["quantidade"], unidade,
             item["preco_unitario"], item["valor_total"], item.get("dono_id")),
        ).lastrowid
        registrar_preco(conn, produto_id, estabelecimento_id, data_iso, item["preco_unitario"],
                        origem=origem if origem in ("xml", "manual") else "manual", compra_item_id=item_id)
    return compra_id


def listar_itens_compra(conn, compra_id: int) -> list[dict[str, Any]]:
    return [dict(r) for r in conn.execute(
        "SELECT ci.id, p.nome AS produto, ci.quantidade, ci.unidade, ci.preco_unitario, ci.valor_total, "
        "COALESCE(pe.nome, 'Conjunto') AS dono "
        "FROM compra_item ci JOIN produto p ON p.id = ci.produto_id "
        "LEFT JOIN pessoa pe ON pe.id = ci.dono_id WHERE ci.compra_id = ? ORDER BY ci.id", (compra_id,))]


def remover_compra(conn, compra_id: int) -> None:
    """Remove a compra, seus itens, pagamentos, rateio e os preços que ela gerou (ON DELETE CASCADE)."""
    conn.execute("DELETE FROM compra WHERE id = ?", (compra_id,))


def gasto_mensal(conn) -> list[dict[str, Any]]:
    """Total gasto em compras de mercado por mês (o arredondamento é feito aqui para valer nos dois bancos)."""
    return [{"mes": r["mes"], "total": round(r["total"], 2)} for r in conn.execute(
        "SELECT substr(data, 1, 7) AS mes, SUM(total) AS total FROM compra "
        "GROUP BY substr(data, 1, 7) ORDER BY substr(data, 1, 7)")]
