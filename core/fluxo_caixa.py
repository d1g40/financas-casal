"""Módulo 1 - Fluxo de caixa: receitas, contas fixas, avulsos, cartões, parcelamentos e projeção.

Princípio central: a projeção de cada mês é calculada a partir dos cadastros
(receitas recorrentes, contas fixas e uma linha de `parcela` por competência).
Nada é "baixado" manualmente: quando um mês passa, as parcelas daquele mês
simplesmente deixam de entrar nas projeções dos meses seguintes.
"""
from __future__ import annotations

import calendar
import sqlite3
from typing import Any, Optional

from .util import intervalo_meses, para_iso, somar_meses, validar_mes

TIPOS_PARCELAMENTO = ("cartao", "emprestimo", "financiamento", "outro")


def _linhas(cursor: sqlite3.Cursor) -> list[dict[str, Any]]:
    return [dict(r) for r in cursor.fetchall()]


# ----------------------------------------------------------------------
# Pessoas
# ----------------------------------------------------------------------
def listar_pessoas(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    return _linhas(conn.execute("SELECT id, nome FROM pessoa ORDER BY id"))


# ----------------------------------------------------------------------
# Receitas (salários etc.)
# ----------------------------------------------------------------------
def adicionar_receita(conn, pessoa_id: int, descricao: str, valor: float, mes_inicio: str,
                      mes_fim: Optional[str] = None, recorrente: bool = True) -> int:
    validar_mes(mes_inicio)
    if mes_fim:
        validar_mes(mes_fim)
        if mes_fim < mes_inicio:
            raise ValueError("O mês final não pode ser anterior ao inicial.")
    cur = conn.execute(
        "INSERT INTO receita (pessoa_id, descricao, valor, mes_inicio, mes_fim, recorrente) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (pessoa_id, descricao, round(valor, 2), mes_inicio, mes_fim or None, int(recorrente)),
    )
    return cur.lastrowid


def listar_receitas(conn) -> list[dict[str, Any]]:
    return _linhas(conn.execute(
        "SELECT r.id, p.nome AS pessoa, r.descricao, r.valor, r.mes_inicio, r.mes_fim, r.recorrente "
        "FROM receita r JOIN pessoa p ON p.id = r.pessoa_id ORDER BY p.nome, r.mes_inicio"))


def remover_receita(conn, receita_id: int) -> None:
    conn.execute("DELETE FROM receita WHERE id = ?", (receita_id,))


# ----------------------------------------------------------------------
# Contas fixas mensais
# ----------------------------------------------------------------------
def adicionar_conta_fixa(conn, descricao: str, valor: float, dia_vencimento: int, mes_inicio: str,
                         mes_fim: Optional[str] = None, categoria: Optional[str] = None) -> int:
    validar_mes(mes_inicio)
    if mes_fim:
        validar_mes(mes_fim)
        if mes_fim < mes_inicio:
            raise ValueError("O mês final não pode ser anterior ao inicial.")
    cur = conn.execute(
        "INSERT INTO conta_fixa (descricao, categoria, valor, dia_vencimento, mes_inicio, mes_fim) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (descricao, categoria or None, round(valor, 2), dia_vencimento, mes_inicio, mes_fim or None),
    )
    return cur.lastrowid


def listar_contas_fixas(conn) -> list[dict[str, Any]]:
    return _linhas(conn.execute(
        "SELECT id, descricao, categoria, valor, dia_vencimento, mes_inicio, mes_fim "
        "FROM conta_fixa ORDER BY dia_vencimento, descricao"))


def remover_conta_fixa(conn, conta_id: int) -> None:
    conn.execute("DELETE FROM conta_fixa WHERE id = ?", (conta_id,))


# ----------------------------------------------------------------------
# Lançamentos avulsos (a pagar / a receber)
# ----------------------------------------------------------------------
def adicionar_lancamento(conn, descricao: str, tipo: str, valor: float, data, pago: bool = False) -> int:
    if tipo not in ("pagar", "receber"):
        raise ValueError("Tipo deve ser 'pagar' ou 'receber'.")
    cur = conn.execute(
        "INSERT INTO lancamento (descricao, tipo, valor, data, pago) VALUES (?, ?, ?, ?, ?)",
        (descricao, tipo, round(valor, 2), para_iso(data), int(pago)),
    )
    return cur.lastrowid


def listar_lancamentos(conn, mes: Optional[str] = None) -> list[dict[str, Any]]:
    sql = "SELECT id, data, descricao, tipo, valor, pago FROM lancamento"
    params: tuple = ()
    if mes:
        validar_mes(mes)
        sql += " WHERE substr(data, 1, 7) = ?"
        params = (mes,)
    return _linhas(conn.execute(sql + " ORDER BY data, id", params))


def marcar_lancamento_pago(conn, lancamento_id: int, pago: bool) -> None:
    conn.execute("UPDATE lancamento SET pago = ? WHERE id = ?", (int(pago), lancamento_id))


def remover_lancamento(conn, lancamento_id: int) -> None:
    conn.execute("DELETE FROM lancamento WHERE id = ?", (lancamento_id,))


# ----------------------------------------------------------------------
# Cartões de crédito
# ----------------------------------------------------------------------
def adicionar_cartao(conn, nome: str, banco: Optional[str] = None,
                     dia_fechamento: Optional[int] = None, dia_vencimento: Optional[int] = None) -> int:
    cur = conn.execute(
        "INSERT INTO cartao (nome, banco, dia_fechamento, dia_vencimento) VALUES (?, ?, ?, ?)",
        (nome, banco or None, dia_fechamento, dia_vencimento),
    )
    return cur.lastrowid


def listar_cartoes(conn) -> list[dict[str, Any]]:
    return _linhas(conn.execute(
        "SELECT id, nome, banco, dia_fechamento, dia_vencimento FROM cartao ORDER BY nome"))


def remover_cartao(conn, cartao_id: int) -> None:
    em_uso = conn.execute(
        "SELECT COUNT(*) FROM parcelamento WHERE cartao_id = ?", (cartao_id,)).fetchone()[0]
    if em_uso:
        raise ValueError("Este cartão possui parcelamentos vinculados; remova-os primeiro.")
    conn.execute("DELETE FROM cartao WHERE id = ?", (cartao_id,))


# ----------------------------------------------------------------------
# Parcelamentos (cartão, empréstimo, financiamento)
# ----------------------------------------------------------------------
def criar_parcelamento(conn, descricao: str, tipo: str, num_parcelas: int, mes_primeira_parcela: str,
                       *, valor_parcela: Optional[float] = None, valor_total: Optional[float] = None,
                       cartao_id: Optional[int] = None, numero_inicial: int = 1) -> int:
    """Cria o parcelamento e gera uma linha em `parcela` para cada competência futura.

    - Informe `valor_parcela` (parcelas iguais; típico de empréstimo/financiamento)
      OU `valor_total` (compra no cartão: a última parcela absorve os centavos).
    - `numero_inicial` > 1 serve para contratos já em andamento: se você está
      na parcela 7 de 36, passe numero_inicial=7 e `mes_primeira_parcela` como
      o mês da parcela 7; só as parcelas 7..36 serão geradas.
    """
    validar_mes(mes_primeira_parcela)
    if tipo not in TIPOS_PARCELAMENTO:
        raise ValueError(f"Tipo inválido: {tipo!r}.")
    if (valor_parcela is None) == (valor_total is None):
        raise ValueError("Informe o valor da parcela OU o valor total (apenas um).")
    if num_parcelas < 1:
        raise ValueError("O número de parcelas deve ser pelo menos 1.")
    if not 1 <= numero_inicial <= num_parcelas:
        raise ValueError("A parcela inicial deve estar entre 1 e o número de parcelas.")
    if valor_total is not None and numero_inicial != 1:
        raise ValueError("Para contratos em andamento informe o valor da parcela, não o total.")

    if valor_total is not None:
        base = round(valor_total / num_parcelas, 2)
        valores = [base] * num_parcelas
        valores[-1] = round(valor_total - base * (num_parcelas - 1), 2)  # ajuste de centavos
        valor_parcela = base
    else:
        valores = [round(valor_parcela, 2)] * num_parcelas

    if tipo != "cartao":
        cartao_id = None
    cur = conn.execute(
        "INSERT INTO parcelamento (descricao, tipo, cartao_id, valor_parcela, num_parcelas) "
        "VALUES (?, ?, ?, ?, ?)",
        (descricao, tipo, cartao_id, round(valor_parcela, 2), num_parcelas),
    )
    parcelamento_id = cur.lastrowid
    conn.executemany(
        "INSERT INTO parcela (parcelamento_id, numero, competencia, valor) VALUES (?, ?, ?, ?)",
        [(parcelamento_id, n, somar_meses(mes_primeira_parcela, n - numero_inicial), valores[n - 1])
         for n in range(numero_inicial, num_parcelas + 1)],
    )
    return parcelamento_id


def listar_parcelamentos(conn, mes_ref: str) -> list[dict[str, Any]]:
    """Parcelamentos com o que ainda falta pagar a partir de `mes_ref` (inclusive)."""
    validar_mes(mes_ref)
    return _linhas(conn.execute(
        """
        SELECT pc.id, pc.descricao, pc.tipo, c.nome AS cartao, pc.valor_parcela, pc.num_parcelas,
               COALESCE(SUM(CASE WHEN pa.competencia >= ? THEN 1 ELSE 0 END), 0) AS parcelas_restantes,
               COALESCE(SUM(CASE WHEN pa.competencia >= ? THEN pa.valor END), 0) AS saldo_restante,
               MAX(pa.competencia) AS ultima_competencia
        FROM parcelamento pc
        LEFT JOIN cartao c ON c.id = pc.cartao_id
        LEFT JOIN parcela pa ON pa.parcelamento_id = pc.id
        GROUP BY pc.id, c.nome
        ORDER BY pc.descricao
        """, (mes_ref, mes_ref)))


def listar_parcelas(conn, parcelamento_id: int) -> list[dict[str, Any]]:
    return _linhas(conn.execute(
        "SELECT id, numero, competencia, valor, paga FROM parcela "
        "WHERE parcelamento_id = ? ORDER BY numero", (parcelamento_id,)))


def marcar_parcela_paga(conn, parcela_id: int, paga: bool) -> None:
    conn.execute("UPDATE parcela SET paga = ? WHERE id = ?", (int(paga), parcela_id))


def remover_parcelamento(conn, parcelamento_id: int) -> None:
    conn.execute("DELETE FROM parcelamento WHERE id = ?", (parcelamento_id,))  # parcelas: ON DELETE CASCADE


def faturas_do_mes(conn, mes: str) -> list[dict[str, Any]]:
    """Itens de fatura (parcelas de compras no cartão) cuja competência é `mes`."""
    validar_mes(mes)
    return _linhas(conn.execute(
        """
        SELECT c.nome AS cartao, c.banco, pc.descricao, pa.numero, pc.num_parcelas, pa.valor, pa.paga
        FROM parcela pa
        JOIN parcelamento pc ON pc.id = pa.parcelamento_id
        JOIN cartao c ON c.id = pc.cartao_id
        WHERE pa.competencia = ?
        ORDER BY c.nome, pc.descricao
        """, (mes,)))


# ----------------------------------------------------------------------
# Extrato previsto e projeção
# ----------------------------------------------------------------------
def _ativo_no_mes(mes: str, inicio: str, fim: Optional[str], recorrente: bool = True) -> bool:
    if not recorrente:
        return mes == inicio
    return inicio <= mes and (fim is None or mes <= fim)  # 'YYYY-MM' compara certo como texto


def _item(origem: str, descricao: str, valor: float, pessoa: Optional[str] = None,
          data: Optional[str] = None, pago: Optional[bool] = None, ref: Optional[str] = None) -> dict[str, Any]:
    return {"origem": origem, "descricao": descricao, "valor": round(valor, 2), "pessoa": pessoa,
            "data": data, "pago": pago, "ref": ref}


def extrato_previsto(conn, mes: str) -> dict[str, list[dict[str, Any]]]:
    """Tudo o que entra e sai em `mes`, linha a linha (base da projeção)."""
    validar_mes(mes)
    ultimo_dia = calendar.monthrange(int(mes[:4]), int(mes[5:7]))[1]

    def data_no_mes(dia: Optional[int]) -> Optional[str]:
        return f"{mes}-{min(dia, ultimo_dia):02d}" if dia else None

    entradas: list[dict[str, Any]] = []
    saidas: list[dict[str, Any]] = []

    for r in conn.execute("SELECT r.descricao, r.valor, r.mes_inicio, r.mes_fim, r.recorrente, p.nome AS pessoa "
                          "FROM receita r JOIN pessoa p ON p.id = r.pessoa_id"):
        if _ativo_no_mes(mes, r["mes_inicio"], r["mes_fim"], bool(r["recorrente"])):
            entradas.append(_item("receita", r["descricao"], r["valor"], pessoa=r["pessoa"]))

    for r in conn.execute("SELECT descricao, valor, data, pago, tipo FROM lancamento "
                          "WHERE substr(data, 1, 7) = ?", (mes,)):
        if r["tipo"] == "receber":
            entradas.append(_item("a_receber", r["descricao"], r["valor"], data=r["data"], pago=bool(r["pago"])))
        else:
            saidas.append(_item("avulso", r["descricao"], r["valor"], data=r["data"], pago=bool(r["pago"])))

    for r in conn.execute("SELECT descricao, categoria, valor, dia_vencimento, mes_inicio, mes_fim FROM conta_fixa"):
        if _ativo_no_mes(mes, r["mes_inicio"], r["mes_fim"]):
            saidas.append(_item("conta_fixa", r["descricao"], r["valor"],
                                data=data_no_mes(r["dia_vencimento"]), ref=r["categoria"]))

    for r in conn.execute(
            """
            SELECT pa.numero, pa.valor, pa.paga, pc.descricao, pc.num_parcelas, pc.tipo,
                   c.nome AS cartao, c.dia_vencimento
            FROM parcela pa
            JOIN parcelamento pc ON pc.id = pa.parcelamento_id
            LEFT JOIN cartao c ON c.id = pc.cartao_id
            WHERE pa.competencia = ?
            """, (mes,)):
        saidas.append(_item("parcela", f"{r['descricao']} ({r['numero']}/{r['num_parcelas']})", r["valor"],
                            data=data_no_mes(r["dia_vencimento"]), pago=bool(r["paga"]),
                            ref=r["cartao"] or r["tipo"]))

    saidas.sort(key=lambda x: (x["data"] or "9999-99-99", x["descricao"]))
    return {"entradas": entradas, "saidas": saidas}


def projecao(conn, mes_inicial: str, n_meses: int = 12, saldo_inicial: float = 0.0) -> list[dict[str, Any]]:
    """Projeção mês a mês: entradas (salários + a receber) - saídas (fixas + parcelas + avulsos)."""
    resultado: list[dict[str, Any]] = []
    acumulado = saldo_inicial
    for mes in intervalo_meses(mes_inicial, n_meses):
        extrato = extrato_previsto(conn, mes)
        por_pessoa: dict[str, float] = {}
        receitas = a_receber = 0.0
        for e in extrato["entradas"]:
            if e["origem"] == "receita":
                receitas += e["valor"]
                por_pessoa[e["pessoa"]] = por_pessoa.get(e["pessoa"], 0.0) + e["valor"]
            else:
                a_receber += e["valor"]
        soma = lambda origem: sum(s["valor"] for s in extrato["saidas"] if s["origem"] == origem)  # noqa: E731
        fixas, parcelas, avulsos = soma("conta_fixa"), soma("parcela"), soma("avulso")
        saidas = fixas + parcelas + avulsos
        saldo_mes = receitas + a_receber - saidas
        acumulado += saldo_mes
        resultado.append({
            "mes": mes,
            "receitas": round(receitas, 2),
            "por_pessoa": {k: round(v, 2) for k, v in por_pessoa.items()},
            "a_receber": round(a_receber, 2),
            "entradas": round(receitas + a_receber, 2),
            "fixas": round(fixas, 2),
            "parcelas": round(parcelas, 2),
            "avulsos": round(avulsos, 2),
            "saidas": round(saidas, 2),
            "saldo_mes": round(saldo_mes, 2),
            "saldo_acumulado": round(acumulado, 2),
        })
    return resultado
