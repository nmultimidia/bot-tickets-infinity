"""
Organização e gravação dos arquivos no disco, seguindo EXATAMENTE
a hierarquia definida pelo cliente:

  [Bloco/Categoria] / [Mês Ano] / [Tipo de Serviço] / [Dia] / arquivo.pdf

Nome do PDF:
  [Dia]_[HoraMin]_[TipoServico]_[NomeColaborador].pdf
  ex: 10_1432_OSInstalacao_JoaoSilva.pdf

Os arquivos originais (fotos em resolução cheia, vídeos, documentos) ficam
ao lado, na pasta  [nome do PDF sem .pdf]_arquivos/.
"""
import os
import re
from datetime import datetime

from config import STORAGE_ROOT, MESES, camel

SUFIXO_ANEXOS = "_arquivos"


def componentes_pasta(categoria: str, subtipo: str, quando: datetime):
    """Devolve a lista ordenada de subpastas: [categoria, mês, tipo, dia].
    Usada tanto no disco local quanto para recriar a árvore no Google Drive."""
    mes = f"{MESES[quando.month]} {quando.year}"
    dia = quando.strftime("%d")
    return [categoria, mes, subtipo, dia]


def montar_caminho(categoria: str, subtipo: str, colaborador: str, quando: datetime):
    """Cria (se necessário) as pastas e devolve o caminho completo do PDF.
    Nunca sobrescreve um relatório existente: acrescenta _2, _3..."""
    comps = componentes_pasta(categoria, subtipo, quando)
    pasta = os.path.join(STORAGE_ROOT, *comps)
    os.makedirs(pasta, exist_ok=True)

    dia = quando.strftime("%d")
    base = f"{dia}_{quando.strftime('%H%M')}_{camel(subtipo)}_{camel(colaborador)}"
    caminho = os.path.join(pasta, base + ".pdf")
    contador = 2
    while os.path.exists(caminho) or os.path.exists(pasta_anexos(caminho)):
        caminho = os.path.join(pasta, f"{base}_{contador}.pdf")
        contador += 1
    return caminho


def pasta_anexos(caminho_pdf: str) -> str:
    return os.path.splitext(caminho_pdf)[0] + SUFIXO_ANEXOS


def nome_anexo(indice: int, nome_original: str) -> str:
    """Prefixo numérico preserva a ordem e evita colisão entre nomes iguais."""
    seguro = re.sub(r'[\/:*?"<>|\x00-\x1f]+', "_", os.path.basename(nome_original or ""))
    return f"{indice:03d}_{seguro.strip(' .') or 'arquivo'}"


def salvar_anexos(pasta: str, arquivos):
    """arquivos: lista de (nome_original, bytes). Devolve a pasta ou None."""
    if not arquivos:
        return None
    os.makedirs(pasta, exist_ok=True)
    for i, (nome, dados) in enumerate(arquivos, 1):
        with open(os.path.join(pasta, nome_anexo(i, nome)), "wb") as f:
            f.write(dados)
    return pasta


def listar_chamados(categoria: str = None, mes: str = None):
    """Lista PDFs salvos, opcionalmente filtrando por categoria e/ou mês."""
    encontrados = []
    if not os.path.isdir(STORAGE_ROOT):
        return encontrados
    for cat in sorted(os.listdir(STORAGE_ROOT)):
        if categoria and cat != categoria:
            continue
        cam_cat = os.path.join(STORAGE_ROOT, cat)
        if not os.path.isdir(cam_cat):
            continue
        for m in sorted(os.listdir(cam_cat)):
            if mes and m != mes:
                continue
            for raiz, dirs, arquivos in os.walk(os.path.join(cam_cat, m)):
                # PDFs anexados pelo técnico não são relatórios
                dirs[:] = [d for d in dirs if not d.endswith(SUFIXO_ANEXOS)]
                for a in arquivos:
                    if a.lower().endswith(".pdf"):
                        encontrados.append(os.path.join(raiz, a))
    return encontrados
