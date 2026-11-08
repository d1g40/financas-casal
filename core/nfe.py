"""Leitura de XML de NF-e (modelo 55) e NFC-e (modelo 65).

O parser é tolerante: remove namespaces, aceita o XML com ou sem o envelope
<nfeProc> e usa o preço líquido (descontando vDesc) como preço unitário.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Optional
from xml.etree.ElementTree import Element, ParseError

try:  # defusedxml protege contra XML malicioso (entidades recursivas etc.)
    from defusedxml.ElementTree import fromstring as _fromstring
except ImportError:  # pragma: no cover - fallback para a biblioteca padrão
    from xml.etree.ElementTree import fromstring as _fromstring

from .util import ean_valido


class NotaFiscalError(ValueError):
    """XML inválido ou que não parece ser uma NF-e/NFC-e."""


@dataclass
class ItemNota:
    numero: int
    codigo: str
    ean: Optional[str]          # None quando não há GTIN válido
    descricao: str
    unidade: str
    quantidade: float
    preco_unitario: float       # já líquido de desconto
    valor_total: float          # vProd - vDesc


@dataclass
class NotaFiscal:
    chave: Optional[str]
    emitente_nome: str
    emitente_cnpj: Optional[str]
    data_emissao: date
    valor_nota: Optional[float]  # vNF informado na nota (para conferência)
    itens: list[ItemNota] = field(default_factory=list)


def _sem_namespace(raiz: Element) -> None:
    for el in raiz.iter():
        if isinstance(el.tag, str) and "}" in el.tag:
            el.tag = el.tag.split("}", 1)[1]


def _numero(texto: Optional[str], padrao: float = 0.0) -> float:
    if texto is None or not texto.strip():
        return padrao
    return float(texto.strip().replace(",", "."))


def ler_nota_xml(conteudo: bytes) -> NotaFiscal:
    """Converte o conteúdo (bytes) de um XML de nota fiscal em `NotaFiscal`."""
    try:
        raiz = _fromstring(conteudo)
    except ParseError as erro:
        raise NotaFiscalError(f"XML mal formado: {erro}") from erro
    except Exception as erro:  # defusedxml levanta exceções próprias para conteúdo perigoso
        raise NotaFiscalError(f"XML recusado por segurança ou inválido: {erro}") from erro

    _sem_namespace(raiz)
    inf = raiz if raiz.tag == "infNFe" else raiz.find(".//infNFe")
    if inf is None:
        raise NotaFiscalError("O arquivo não parece ser uma NF-e/NFC-e (elemento infNFe ausente).")

    chave = (inf.get("Id") or "").replace("NFe", "") or (raiz.findtext(".//chNFe") or "")
    chave = chave if re.fullmatch(r"\d{44}", chave) else None

    emit = inf.find("emit")
    if emit is None:
        raise NotaFiscalError("Emitente (emit) não encontrado na nota.")
    nome = (emit.findtext("xFant") or emit.findtext("xNome") or "").strip()
    cnpj = re.sub(r"\D", "", emit.findtext("CNPJ") or "") or None
    if not nome:
        raise NotaFiscalError("Nome do emitente não encontrado na nota.")

    texto_data = inf.findtext("ide/dhEmi") or inf.findtext("ide/dEmi")
    try:
        emissao = date.fromisoformat((texto_data or "")[:10])
    except ValueError as erro:
        raise NotaFiscalError("Data de emissão ausente ou inválida.") from erro

    itens: list[ItemNota] = []
    for det in inf.findall("det"):
        prod = det.find("prod")
        if prod is None:
            continue
        quantidade = _numero(prod.findtext("qCom"))
        if quantidade <= 0:
            continue
        bruto = _numero(prod.findtext("vProd"))
        desconto = _numero(prod.findtext("vDesc"))
        liquido = round(bruto - desconto, 2)
        preco = _numero(prod.findtext("vUnCom")) if desconto == 0 else round(liquido / quantidade, 4)
        ean = ean_valido(prod.findtext("cEAN")) or ean_valido(prod.findtext("cEANTrib"))
        itens.append(ItemNota(
            numero=int(det.get("nItem") or len(itens) + 1),
            codigo=(prod.findtext("cProd") or "").strip(),
            ean=ean,
            descricao=" ".join((prod.findtext("xProd") or "").split()),
            unidade=(prod.findtext("uCom") or "UN").strip().upper(),
            quantidade=quantidade,
            preco_unitario=preco,
            valor_total=liquido,
        ))
    if not itens:
        raise NotaFiscalError("Nenhum item encontrado na nota.")

    total_texto = inf.findtext("total/ICMSTot/vNF")
    return NotaFiscal(chave=chave, emitente_nome=nome, emitente_cnpj=cnpj, data_emissao=emissao,
                      valor_nota=_numero(total_texto) if total_texto else None, itens=itens)
