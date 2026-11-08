"""Backup: exporta todas as tabelas para um ZIP de arquivos CSV (funciona em SQLite e PostgreSQL)."""
from __future__ import annotations

import csv
import io
import zipfile
from datetime import datetime
from typing import Any

# Ordem segura para restaurar (tabelas "pai" antes das que dependem delas).
TABELAS = (
    "pessoa", "receita", "conta_fixa", "lancamento", "cartao", "parcelamento", "parcela",
    "estabelecimento", "produto", "compra", "compra_item", "historico_preco",
    "compra_pagamento", "compra_rateio", "acerto",
)


def gerar_backup_zip(conn: Any) -> bytes:
    """Devolve o conteúdo de um .zip com um CSV por tabela (UTF-8 com BOM, abre direto no Excel)."""
    memoria = io.BytesIO()
    with zipfile.ZipFile(memoria, "w", zipfile.ZIP_DEFLATED) as zip_:
        for posicao, tabela in enumerate(TABELAS, start=1):
            cursor = conn.execute(f"SELECT * FROM {tabela} ORDER BY 1")  # tabela vem da lista fixa acima
            linhas = [dict(r) for r in cursor.fetchall()]
            texto = io.StringIO()
            if linhas:
                escritor = csv.DictWriter(texto, fieldnames=list(linhas[0].keys()))
                escritor.writeheader()
                escritor.writerows(linhas)
            zip_.writestr(f"{posicao:02d}_{tabela}.csv", "﻿" + texto.getvalue())
        zip_.writestr("LEIA-ME.txt", (
            f"Backup das Finanças do Casal gerado em {datetime.now():%d/%m/%Y %H:%M}.\n"
            "Um CSV por tabela, na ordem em que devem ser restauradas.\n"))
    return memoria.getvalue()
