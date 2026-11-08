"""Módulo 2 - Estabelecimentos, produtos, histórico de preços e comparação de carrinho."""
from __future__ import annotations

import re
import sqlite3
from collections import defaultdict
from typing import Any, Optional, Sequence

from .util import ean_valido, normalizar_texto, para_iso

METRICAS = ("medio", "ultimo", "minimo")


def _linhas(cursor: sqlite3.Cursor) -> list[dict[str, Any]]:
    return [dict(r) for r in cursor.fetchall()]


# ----------------------------------------------------------------------
# Estabelecimentos
# ----------------------------------------------------------------------
def obter_ou_criar_estabelecimento(conn, nome: str, cnpj: Optional[str] = None) -> int:
    """Acha o mercado pelo CNPJ (ou pelo nome, se não houver CNPJ) e cria se não existir."""
    nome = " ".join((nome or "").split())
    cnpj = re.sub(r"\D", "", cnpj or "") or None
    if not nome:
        raise ValueError("Informe o nome do estabelecimento.")

    if cnpj:
        linha = conn.execute("SELECT id FROM estabelecimento WHERE cnpj = ?", (cnpj,)).fetchone()
        if linha:
            return linha["id"]
        # Mercado cadastrado antes à mão (sem CNPJ) com o mesmo nome: completa o cadastro.
        linha = conn.execute("SELECT id FROM estabelecimento WHERE cnpj IS NULL AND LOWER(nome) = LOWER(?)",
                             (nome,)).fetchone()
        if linha:
            conn.execute("UPDATE estabelecimento SET cnpj = ? WHERE id = ?", (cnpj, linha["id"]))
            return linha["id"]
    else:
        linha = conn.execute("SELECT id FROM estabelecimento WHERE LOWER(nome) = LOWER(?) ORDER BY id",
                             (nome,)).fetchone()
        if linha:
            return linha["id"]
    return conn.execute("INSERT INTO estabelecimento (nome, cnpj) VALUES (?, ?)", (nome, cnpj)).lastrowid


def listar_estabelecimentos(conn) -> list[dict[str, Any]]:
    return _linhas(conn.execute("SELECT id, nome, cnpj FROM estabelecimento ORDER BY nome"))


def buscar_estabelecimento_por_cnpj(conn, cnpj: Optional[str]) -> Optional[dict[str, Any]]:
    cnpj = re.sub(r"\D", "", cnpj or "")
    if not cnpj:
        return None
    linha = conn.execute("SELECT id, nome, cnpj FROM estabelecimento WHERE cnpj = ?", (cnpj,)).fetchone()
    return dict(linha) if linha else None


# ----------------------------------------------------------------------
# Produtos
# ----------------------------------------------------------------------
def obter_ou_criar_produto(conn, descricao: str, ean: Optional[str] = None, unidade: str = "UN") -> int:
    """Casa o item com um produto existente (EAN primeiro, nome normalizado depois) ou cria um novo."""
    descricao = " ".join((descricao or "").split())
    if not descricao:
        raise ValueError("Informe a descrição do produto.")
    chave = normalizar_texto(descricao)
    ean = ean_valido(ean)

    if ean:
        linha = conn.execute("SELECT id FROM produto WHERE ean = ?", (ean,)).fetchone()
        if linha:
            return linha["id"]
        # Produto lançado antes à mão (sem EAN) com o mesmo nome: passa a ter o EAN.
        linha = conn.execute("SELECT id FROM produto WHERE ean IS NULL AND nome_normalizado = ? ORDER BY id",
                             (chave,)).fetchone()
        if linha:
            conn.execute("UPDATE produto SET ean = ? WHERE id = ?", (ean, linha["id"]))
            return linha["id"]
    else:
        linha = conn.execute("SELECT id FROM produto WHERE nome_normalizado = ? ORDER BY id", (chave,)).fetchone()
        if linha:
            return linha["id"]

    return conn.execute(
        "INSERT INTO produto (nome, nome_normalizado, ean, unidade) VALUES (?, ?, ?, ?)",
        (descricao, chave, ean, (unidade or "UN").upper()),
    ).lastrowid


def listar_produtos(conn) -> list[dict[str, Any]]:
    return _linhas(conn.execute("SELECT id, nome, ean, unidade FROM produto ORDER BY nome"))


# ----------------------------------------------------------------------
# Histórico de preços
# ----------------------------------------------------------------------
def registrar_preco(conn, produto_id: int, estabelecimento_id: int, data, preco_unitario: float,
                    origem: str = "manual", compra_item_id: Optional[int] = None) -> int:
    if preco_unitario < 0:
        raise ValueError("O preço não pode ser negativo.")
    return conn.execute(
        "INSERT INTO historico_preco (produto_id, estabelecimento_id, data, preco_unitario, origem, compra_item_id) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (produto_id, estabelecimento_id, para_iso(data), preco_unitario, origem, compra_item_id),
    ).lastrowid


def historico_precos(conn, produto_id: Optional[int] = None) -> list[dict[str, Any]]:
    sql = ("SELECT h.id, h.data, p.id AS produto_id, p.nome AS produto, e.nome AS mercado, "
           "h.preco_unitario, h.origem "
           "FROM historico_preco h "
           "JOIN produto p ON p.id = h.produto_id "
           "JOIN estabelecimento e ON e.id = h.estabelecimento_id")
    params: tuple = ()
    if produto_id is not None:
        sql += " WHERE h.produto_id = ?"
        params = (produto_id,)
    return _linhas(conn.execute(sql + " ORDER BY h.data DESC, h.id DESC", params))


def resumo_precos(conn, produto_id: int) -> list[dict[str, Any]]:
    """Último, médio e menor preço de um produto em cada mercado."""
    por_mercado: dict[str, list[float]] = defaultdict(list)
    for r in reversed(historico_precos(conn, produto_id)):  # do mais antigo para o mais novo
        por_mercado[r["mercado"]].append(r["preco_unitario"])
    resumo = [{"mercado": m, "ultimo": v[-1], "medio": round(sum(v) / len(v), 4), "minimo": min(v),
               "registros": len(v)} for m, v in por_mercado.items()]
    return sorted(resumo, key=lambda x: x["medio"])


# ----------------------------------------------------------------------
# Comparação de carrinho
# ----------------------------------------------------------------------
def _valor_por_metrica(precos: Sequence[float], metrica: str) -> float:
    if metrica == "ultimo":
        return precos[-1]
    if metrica == "minimo":
        return min(precos)
    return sum(precos) / len(precos)  # "medio"


def comparar_carrinho(conn, itens: Sequence[tuple[int, float]], metrica: str = "medio",
                      desde: Optional[str] = None) -> dict[str, Any]:
    """Diz em qual mercado o carrinho sai mais barato segundo o histórico de preços.

    itens   : lista de (produto_id, quantidade)
    metrica : 'medio' (preço médio histórico), 'ultimo' (último preço visto)
              ou 'minimo' (menor preço já registrado)
    desde   : 'YYYY-MM-DD' - ignora preços anteriores a essa data

    Retorna um dicionário com:
      por_mercado       - ranking (mercados com todos os itens vêm primeiro, do mais barato ao mais caro)
      melhor_combinacao - quanto custaria comprando cada item no mercado mais barato dele
      detalhe           - preço e subtotal de cada item em cada mercado
      sem_preco_algum   - produtos do carrinho sem nenhum preço registrado
    """
    if metrica not in METRICAS:
        raise ValueError(f"Métrica inválida: {metrica!r}.")
    quantidades: dict[int, float] = defaultdict(float)
    for produto_id, quantidade in itens:
        quantidades[produto_id] += quantidade
    if not quantidades:
        raise ValueError("O carrinho está vazio.")

    ids = list(quantidades)
    marcas = ",".join("?" * len(ids))
    nomes = {r["id"]: r["nome"] for r in conn.execute(f"SELECT id, nome FROM produto WHERE id IN ({marcas})", ids)}

    sql = ("SELECT h.produto_id, h.estabelecimento_id, e.nome AS mercado, h.preco_unitario "
           f"FROM historico_preco h JOIN estabelecimento e ON e.id = h.estabelecimento_id "
           f"WHERE h.produto_id IN ({marcas})")
    params: list[Any] = list(ids)
    if desde:
        sql += " AND h.data >= ?"
        params.append(para_iso(desde))
    sql += " ORDER BY h.data, h.id"

    precos: dict[tuple[int, int], list[float]] = defaultdict(list)
    mercados: dict[int, str] = {}
    for r in conn.execute(sql, params):
        precos[(r["produto_id"], r["estabelecimento_id"])].append(r["preco_unitario"])
        mercados[r["estabelecimento_id"]] = r["mercado"]

    detalhe: list[dict[str, Any]] = []
    for (produto_id, mercado_id), lista in precos.items():
        unitario = _valor_por_metrica(lista, metrica)
        detalhe.append({"produto_id": produto_id, "produto": nomes[produto_id], "mercado_id": mercado_id,
                        "mercado": mercados[mercado_id], "quantidade": quantidades[produto_id],
                        "preco_unitario": round(unitario, 4),
                        "subtotal": round(unitario * quantidades[produto_id], 2)})

    por_mercado: list[dict[str, Any]] = []
    for mercado_id, mercado in mercados.items():
        linhas = [d for d in detalhe if d["mercado_id"] == mercado_id]
        achados = {d["produto_id"] for d in linhas}
        faltantes = [nomes[p] for p in ids if p not in achados]
        por_mercado.append({"mercado_id": mercado_id, "mercado": mercado,
                            "total": round(sum(d["subtotal"] for d in linhas), 2),
                            "itens_encontrados": len(achados), "itens_total": len(ids),
                            "faltantes": faltantes, "completo": not faltantes})
    por_mercado.sort(key=lambda m: (not m["completo"], -m["itens_encontrados"], m["total"]))

    combinacao: list[dict[str, Any]] = []
    for produto_id in ids:
        candidatos = [d for d in detalhe if d["produto_id"] == produto_id]
        if candidatos:
            combinacao.append(min(candidatos, key=lambda d: d["preco_unitario"]))
    sem_preco = [nomes[p] for p in ids if not any(d["produto_id"] == p for d in detalhe)]
    total_combinacao = round(sum(d["subtotal"] for d in combinacao), 2)

    economia = None
    melhor_completo = next((m for m in por_mercado if m["completo"]), None)
    if melhor_completo and not sem_preco:
        economia = round(melhor_completo["total"] - total_combinacao, 2)

    return {"por_mercado": por_mercado,
            "melhor_combinacao": {"total": total_combinacao, "itens": combinacao,
                                  "economia_vs_melhor_mercado": economia},
            "detalhe": detalhe,
            "sem_preco_algum": sem_preco}
