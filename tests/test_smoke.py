"""Testes de fumaça da camada de negócio (sem Streamlit).

Rode com:  python tests/test_smoke.py     ou     pytest -q
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

import pytest  # noqa: E402

from core import compras, divisao, fluxo_caixa as fc, nfe, supermercado as sm  # noqa: E402
from core.database import conectar, inicializar_banco  # noqa: E402
from core.util import ean_valido, formatar_brl, normalizar_texto, somar_meses  # noqa: E402

EXEMPLOS = RAIZ / "exemplos"


@pytest.fixture()
def conn():
    with tempfile.TemporaryDirectory() as pasta:
        c = conectar(Path(pasta) / "teste.db")
        inicializar_banco(c)
        yield c
        c.close()


def _ids(conn):
    return {p["nome"]: p["id"] for p in fc.listar_pessoas(conn)}


# ----------------------------------------------------------------------
def test_utilitarios():
    assert somar_meses("2026-11", 3) == "2027-02"
    assert somar_meses("2026-01", -1) == "2025-12"
    assert formatar_brl(1234.5) == "R$ 1.234,50"
    assert formatar_brl(-0.001) == "R$ 0,00"
    assert normalizar_texto("  Leite  Condensado Moça ") == "LEITE CONDENSADO MOCA"
    assert ean_valido("7891000100103") == "7891000100103"
    assert ean_valido("7891000100104") is None      # dígito verificador errado
    assert ean_valido("SEM GTIN") is None
    assert ean_valido("2000000012345") is None      # prefixo 2 = código interno da loja


def test_fluxo_de_caixa_e_parcelas(conn):
    p = _ids(conn)
    fc.adicionar_receita(conn, p["David"], "Salário", 5000, "2026-11")
    fc.adicionar_receita(conn, p["Mylena"], "Salário", 4000, "2026-11")
    fc.adicionar_conta_fixa(conn, "Aluguel", 1800, 5, "2026-11")
    cartao = fc.adicionar_cartao(conn, "Nubank", "Nubank", 25, 5)
    # compra no cartão: 3000 em 10x -> 300/mês
    fc.criar_parcelamento(conn, "Notebook", "cartao", 10, "2026-11", valor_total=3000, cartao_id=cartao)
    # empréstimo em andamento: está na parcela 7 de 36 (R$ 500)
    fc.criar_parcelamento(conn, "Empréstimo pessoal", "emprestimo", 36, "2026-11",
                          valor_parcela=500, numero_inicial=7)
    fc.adicionar_lancamento(conn, "IPVA", "pagar", 700, "2026-12-10")
    fc.adicionar_lancamento(conn, "Freela", "receber", 400, "2026-11-20")

    proj = fc.projecao(conn, "2026-11", 3)
    assert [m["saldo_mes"] for m in proj] == [6800.0, 5700.0, 6400.0]
    assert [m["saldo_acumulado"] for m in proj] == [6800.0, 12500.0, 18900.0]
    assert proj[0]["por_pessoa"] == {"David": 5000.0, "Mylena": 4000.0}
    assert proj[0]["parcelas"] == 800.0 and proj[0]["fixas"] == 1800.0

    # parcelas se abatem sozinhas: notebook acaba em 2027-08; empréstimo em 2029-04
    def descricoes(mes):
        return [s["descricao"] for s in fc.extrato_previsto(conn, mes)["saidas"]]
    assert "Notebook (10/10)" in descricoes("2027-08")
    assert not any("Notebook" in d for d in descricoes("2027-09"))
    assert "Empréstimo pessoal (36/36)" in descricoes("2029-04")
    assert not any("Empréstimo" in d for d in descricoes("2029-05"))

    # total dividido com ajuste de centavos na última parcela
    pid = fc.criar_parcelamento(conn, "Teste", "outro", 3, "2026-11", valor_total=100)
    assert [x["valor"] for x in fc.listar_parcelas(conn, pid)] == [33.33, 33.33, 33.34]

    fatura = fc.faturas_do_mes(conn, "2026-11")
    assert len(fatura) == 1 and fatura[0]["cartao"] == "Nubank" and fatura[0]["valor"] == 300.0

    with pytest.raises(ValueError):
        fc.criar_parcelamento(conn, "X", "outro", 3, "2026-11", valor_total=90, numero_inicial=2)
    with pytest.raises(ValueError):
        fc.remover_cartao(conn, cartao)  # há parcelamento vinculado


def test_leitura_xml():
    nota = nfe.ler_nota_xml((EXEMPLOS / "nfce_supermercado_alfa.xml").read_bytes())
    assert nota.emitente_nome == "Supermercado Alfa" and nota.emitente_cnpj == "11222333000181"
    assert str(nota.data_emissao) == "2026-10-25" and len(nota.chave) == 44
    assert nota.valor_nota == 47.94 and len(nota.itens) == 3
    leite, banana, arroz = nota.itens
    assert leite.ean == "7891000100103" and leite.quantidade == 2 and leite.preco_unitario == 6.49
    assert banana.ean is None and banana.unidade == "KG"
    assert arroz.valor_total == 26.90 and arroz.preco_unitario == 26.90  # desconto aplicado
    assert round(sum(i.valor_total for i in nota.itens), 2) == nota.valor_nota

    with pytest.raises(nfe.NotaFiscalError):
        nfe.ler_nota_xml(b"<html></html>")
    with pytest.raises(nfe.NotaFiscalError):
        nfe.ler_nota_xml(b"isto nao e xml")


def _importar(conn, arquivo, dono_por_indice, pagamentos, rateio):
    nota = nfe.ler_nota_xml((EXEMPLOS / arquivo).read_bytes())
    mercado = sm.obter_ou_criar_estabelecimento(conn, nota.emitente_nome, nota.emitente_cnpj)
    itens = [{"descricao": i.descricao, "ean": i.ean, "unidade": i.unidade, "quantidade": i.quantidade,
              "preco_unitario": i.preco_unitario, "valor_total": i.valor_total,
              "dono_id": dono_por_indice.get(n)} for n, i in enumerate(nota.itens)]
    return compras.registrar_compra(conn, estabelecimento_id=mercado, data=nota.data_emissao, itens=itens,
                                    pagamentos=pagamentos, rateio=rateio, chave_nfe=nota.chave, origem="xml")


def _cenario_mercado(conn):
    p = _ids(conn)
    meio = {p["David"]: 50, p["Mylena"]: 50}
    # Alfa: leite conjunto, banana do David, arroz da Mylena; David pagou tudo
    _importar(conn, "nfce_supermercado_alfa.xml", {1: p["David"], 2: p["Mylena"]},
              {p["David"]: 47.94}, meio)
    # Beta: tudo conjunto; Mylena pagou 30 e David 17,32
    _importar(conn, "nfce_mercado_beta.xml", {}, {p["Mylena"]: 30.00, p["David"]: 17.32}, meio)
    return p


def test_importacao_casa_produtos_por_ean_e_nome(conn):
    _cenario_mercado(conn)
    nomes = [r["nome"] for r in sm.listar_produtos(conn)]
    assert len(nomes) == 4, nomes  # leite (EAN) / banana (nome) / arroz (EAN) / detergente
    leite = next(r["id"] for r in sm.listar_produtos(conn) if "CONDENSADO" in r["nome"].upper())
    resumo = sm.resumo_precos(conn, leite)
    assert {r["mercado"] for r in resumo} == {"Supermercado Alfa", "Mercado Beta"}


def test_nota_duplicada_e_pagamento_inconsistente(conn):
    p = _cenario_mercado(conn)
    with pytest.raises(compras.CompraDuplicadaError):
        _importar(conn, "nfce_supermercado_alfa.xml", {}, {p["David"]: 47.94}, {p["David"]: 50, p["Mylena"]: 50})
    with pytest.raises(ValueError):  # pagamentos não fecham com o total
        compras.registrar_compra(
            conn, estabelecimento_id=1, data="2026-11-03",
            itens=[{"descricao": "Pão", "quantidade": 1, "preco_unitario": 10}],
            pagamentos={p["David"]: 5}, rateio={p["David"]: 50, p["Mylena"]: 50})


def test_comparacao_de_carrinho(conn):
    _cenario_mercado(conn)
    prod = {r["nome"]: r["id"] for r in sm.listar_produtos(conn)}
    leite = next(i for n, i in prod.items() if "CONDENSADO" in n.upper())
    banana = next(i for n, i in prod.items() if "BANANA" in n.upper())
    arroz = next(i for n, i in prod.items() if "ARROZ" in n.upper())
    deterg = next(i for n, i in prod.items() if "DETERGENTE" in n.upper())

    r = sm.comparar_carrinho(conn, [(leite, 2), (arroz, 1), (banana, 1)], metrica="ultimo")
    ranking = {m["mercado"]: m["total"] for m in r["por_mercado"]}
    assert ranking == {"Supermercado Alfa": 45.87, "Mercado Beta": 45.97}
    assert r["por_mercado"][0]["mercado"] == "Supermercado Alfa"
    comb = r["melhor_combinacao"]
    assert comb["total"] == 44.87 and comb["economia_vs_melhor_mercado"] == 1.00

    # só o Beta vende detergente: mercado completo vem antes, mesmo sendo mais caro
    r = sm.comparar_carrinho(conn, [(leite, 1), (deterg, 1)], metrica="ultimo")
    assert r["por_mercado"][0]["mercado"] == "Mercado Beta" and r["por_mercado"][0]["completo"]
    assert r["por_mercado"][1]["faltantes"] == [dict((v, k) for k, v in prod.items())[deterg]]

    # preço avulso mais barato no Alfa: métricas medio / ultimo / minimo
    alfa = sm.obter_ou_criar_estabelecimento(conn, "Supermercado Alfa", "11222333000181")
    sm.registrar_preco(conn, leite, alfa, "2026-11-05", 5.50)
    total = lambda metrica: {m["mercado"]: m["total"]  # noqa: E731
                             for m in sm.comparar_carrinho(conn, [(leite, 1)], metrica)["por_mercado"]}
    assert total("ultimo")["Supermercado Alfa"] == 5.50
    assert total("medio")["Supermercado Alfa"] == 6.00  # (6.49 + 5.50) / 2 = 5.995 -> 6.00
    assert total("minimo")["Supermercado Alfa"] == 5.50

    # janela de datas: só preços a partir de 02/11 (descarta a nota do Alfa de 25/10)
    r = sm.comparar_carrinho(conn, [(leite, 1)], "ultimo", desde="2026-11-02")
    assert {m["mercado"] for m in r["por_mercado"]} == {"Supermercado Alfa", "Mercado Beta"}
    r = sm.comparar_carrinho(conn, [(arroz, 1)], "ultimo", desde="2026-11-02")
    assert [m["mercado"] for m in r["por_mercado"]] == ["Mercado Beta"]

    with pytest.raises(ValueError):
        sm.comparar_carrinho(conn, [])


def test_divisao_e_acerto(conn):
    p = _cenario_mercado(conn)
    saldo = divisao.saldo_casal(conn)
    por_nome = {x["nome"]: x for x in saldo["pessoas"]}
    # Alfa: David consome 6,49 + 8,06 = 14,55; Mylena 6,49 + 26,90 = 33,39 -> Mylena deve 33,39
    # Beta: cada um consome 23,66; David pagou 17,32 (-6,34) e Mylena 30,00 (+6,34)
    assert por_nome["David"]["saldo"] == pytest.approx(27.05, abs=0.01)
    assert por_nome["Mylena"]["saldo"] == pytest.approx(-27.05, abs=0.01)
    assert saldo["devedor_id"] == p["Mylena"] and saldo["credor_id"] == p["David"]
    assert saldo["resumo"] == "Mylena deve R$ 27,05 a David."

    divisao.registrar_acerto(conn, p["Mylena"], p["David"], 20.00, "2026-11-08", "Pix")
    assert divisao.saldo_casal(conn)["resumo"] == "Mylena deve R$ 7,05 a David."
    divisao.registrar_acerto(conn, p["Mylena"], p["David"], 7.05, "2026-11-09")
    assert divisao.saldo_casal(conn)["resumo"].startswith("Contas em dia")

    with pytest.raises(ValueError):
        divisao.registrar_acerto(conn, p["David"], p["David"], 10, "2026-11-09")


def test_remover_compra_limpa_precos(conn):
    _cenario_mercado(conn)
    antes = len(sm.historico_precos(conn))
    compra_id = conn.execute("SELECT id FROM compra WHERE chave_nfe LIKE '%000056781%'").fetchone()["id"]
    compras.remover_compra(conn, compra_id)
    assert len(sm.historico_precos(conn)) == antes - 4
    assert conn.execute("SELECT COUNT(*) FROM compra_pagamento WHERE compra_id = ?",
                        (compra_id,)).fetchone()[0] == 0


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
