"""
Geração do relatório final em PDF.

O PDF contém:
  - Cabeçalho com dados do chamado (colaborador, data/hora, localização, categoria, tipo)
  - Todas as informações preenchidas (perguntas e respostas)
  - Todas as fotos enviadas
  - Histórico da conversa (transcrição)
"""
import io
from datetime import datetime
from xml.sax.saxutils import escape

from PIL import Image as PILImage, ExifTags
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Image as RLImage, Table, TableStyle,
)

# Fotos HEIC/HEIF (iPhone enviando como arquivo), se a lib estiver instalada
try:
    from pillow_heif import register_heif_opener
    register_heif_opener()
except ImportError:
    pass


def _texto(valor) -> str:
    """Escapa o texto do usuário: '<', '>' e '&' são marcação no reportlab e
    derrubavam a geração do PDF inteiro."""
    return escape(str(valor)).replace("\n", "<br/>")

# ---------------------------------------------------------------------------
# EXIF / GPS
# ---------------------------------------------------------------------------

_GPS_TAG = next((k for k, v in ExifTags.TAGS.items() if v == "GPSInfo"), None)


def _to_degrees(value):
    d, m, s = value
    return float(d) + float(m) / 60.0 + float(s) / 3600.0


def extrair_localizacao(image_bytes: bytes):
    """Tenta extrair latitude/longitude do EXIF da foto.
    Retorna string 'lat, lon' + link do Maps, ou None."""
    try:
        img = PILImage.open(io.BytesIO(image_bytes))
        exif = img._getexif() or {}
        gps = exif.get(_GPS_TAG)
        if not gps:
            return None
        g = {ExifTags.GPSTAGS.get(k, k): v for k, v in gps.items()}
        lat = _to_degrees(g["GPSLatitude"])
        lon = _to_degrees(g["GPSLongitude"])
        if g.get("GPSLatitudeRef") == "S":
            lat = -lat
        if g.get("GPSLongitudeRef") == "W":
            lon = -lon
        return f"{lat:.6f}, {lon:.6f}"
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Imagens para o PDF
# ---------------------------------------------------------------------------

def _imagem_para_pdf(image_bytes: bytes, largura_max=14 * cm, altura_max=20 * cm):
    """Normaliza a imagem (converte p/ RGB, reduz tamanho) e devolve um RLImage.

    Limita LARGURA e ALTURA: fotos em pé (retrato), típicas de celular,
    estouravam a altura da página e quebravam a geração do PDF."""
    img = PILImage.open(io.BytesIO(image_bytes))
    # Aplica a rotação gravada no EXIF (senão a foto sai deitada)
    try:
        from PIL import ImageOps
        img = ImageOps.exif_transpose(img)
    except Exception:
        pass
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    # Reduz imagens muito grandes para não estourar o PDF
    max_px = 1600
    if max(img.size) > max_px:
        ratio = max_px / max(img.size)
        img = img.resize((int(img.width * ratio), int(img.height * ratio)))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=80)
    buf.seek(0)
    w, h = img.size
    # menor escala entre largura e altura -> cabe sempre na página
    escala = min(largura_max / w, altura_max / h, 1.0)
    return RLImage(buf, width=w * escala, height=h * escala)


# ---------------------------------------------------------------------------
# Gerador do PDF
# ---------------------------------------------------------------------------

def gerar_pdf(caminho_saida, *, meta, respostas, transcricao):
    """
    meta: dict com colaborador, data_hora (datetime), categoria, subtipo, localizacao
    respostas: lista de dicts {label, type, valor (texto) ou imagens (list[bytes])}
    transcricao: lista de tuplas (autor, texto)
    """
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle("Rotulo", parent=styles["Normal"],
                              fontName="Helvetica-Bold", fontSize=10, spaceAfter=2))
    styles.add(ParagraphStyle("Valor", parent=styles["Normal"],
                              fontSize=10, spaceAfter=8, leftIndent=6))
    titulo = ParagraphStyle("TituloDoc", parent=styles["Title"], fontSize=18)
    subt = ParagraphStyle("Sub", parent=styles["Normal"], fontSize=9, textColor=colors.grey)

    doc = SimpleDocTemplate(
        caminho_saida, pagesize=A4,
        leftMargin=2 * cm, rightMargin=2 * cm, topMargin=1.8 * cm, bottomMargin=1.8 * cm,
        title="Relatório de Chamado",
    )
    els = []

    els.append(Paragraph("Relatório de Chamado", titulo))
    els.append(Paragraph(_texto(meta["categoria"] + " • " + meta["subtipo"]), subt))
    els.append(Spacer(1, 0.5 * cm))

    # Tabela de metadados
    dh = meta["data_hora"]
    linhas = [
        ["Colaborador", meta["colaborador"]],
        ["Data/Hora", dh.strftime("%d/%m/%Y %H:%M")],
        ["Categoria", meta["categoria"]],
        ["Tipo de serviço", meta["subtipo"]],
        ["Localização (via foto)", meta.get("localizacao") or "Não disponível"],
    ]
    if meta.get("gerado_em"):
        linhas.append(["Relatório gerado em", meta["gerado_em"].strftime("%d/%m/%Y %H:%M")])
    tab = Table(linhas, colWidths=[5 * cm, 11 * cm])
    tab.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#f0f0f0")),
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cccccc")),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    els.append(tab)
    els.append(Spacer(1, 0.6 * cm))

    # Respostas
    els.append(Paragraph("Informações preenchidas", styles["Heading2"]))
    for r in respostas:
        els.append(Paragraph(_texto(r["label"]), styles["Rotulo"]))
        if r["type"] in ("photo", "selfie"):
            imgs = r.get("imagens", [])
            if not imgs:
                els.append(Paragraph("— (sem foto)", styles["Valor"]))
            for b in imgs:
                try:
                    els.append(_imagem_para_pdf(b))
                    els.append(Spacer(1, 0.3 * cm))
                except Exception:
                    els.append(Paragraph("[falha ao renderizar imagem]", styles["Valor"]))
        else:
            valor = r.get("valor", "") or "—"
            els.append(Paragraph(_texto(valor), styles["Valor"]))

    # Transcrição
    els.append(Spacer(1, 0.4 * cm))
    els.append(Paragraph("Histórico da conversa", styles["Heading2"]))
    for autor, texto in transcricao:
        els.append(Paragraph(f"<b>{_texto(autor)}:</b> {_texto(texto)}", styles["Valor"]))

    doc.build(els)
    return caminho_saida
