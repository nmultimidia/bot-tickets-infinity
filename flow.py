"""
Definição do fluxo de perguntas do bot — traduzido diretamente do
fluxograma (fluxograma-bot-tickets.mermaid) enviado pelo cliente.

Cada categoria tem:
  - "folder": nome da pasta raiz da categoria (Bloco/Categoria)
  - "subtypes": dicionário {Tipo de Serviço: [lista de etapas]}

Cada ETAPA é um dicionário com "type" e "label":
  - text     -> resposta em texto livre
  - number   -> resposta numérica (ex: quilometragem)
  - photo    -> uma ou mais fotos (use "multiple": True para várias)
  - selfie   -> uma foto (selfie do colaborador)
  - yesno    -> pergunta Sim/Não (ex: "Serviço concluído?")

OBS: o campo "Número da OS" NÃO estava explícito no fluxograma, mas aparece
na descrição inicial do projeto ("Qual a OS do trabalho"). Ele foi adicionado
como primeira etapa nas OS de UFMT e VMMT. Se o cliente não quiser, basta
remover as linhas marcadas com  # [confirmar com o cliente].
"""

# Etapas reutilizáveis -------------------------------------------------------

_CONCLUIDO = {"type": "yesno", "label": "Serviço concluído?"}

_VMMT_FOTOS = [
    {"type": "photo", "label": "Foto da célula"},
    {"type": "photo", "label": "Foto da potência elétrica"},
    {"type": "photo", "label": "Foto da potência de fibra"},
    {"type": "photo", "label": "Foto da caixa hermética interna"},
    {"type": "text",  "label": "Texto auxiliar"},
]

# Fluxo completo -------------------------------------------------------------

FLOW = {
    "Abertura de OS - UFMT": {
        "OS Instalação": [
            {"type": "text",  "label": "Material utilizado"},
            {"type": "photo", "label": "Fotos do local de instalação", "multiple": True},
            _CONCLUIDO,
        ],
        "OS Manutenção": [
            {"type": "text",  "label": "Material utilizado"},
            {"type": "photo", "label": "Fotos do local de instalação", "multiple": True},
            _CONCLUIDO,
        ],
        "OS Remoção": [
            {"type": "photo", "label": "Fotos do local de instalação", "multiple": True},
            _CONCLUIDO,
        ],
        "OS Outros Assuntos": [
            {"type": "text",  "label": "Descreva o serviço"},
            {"type": "photo", "label": "Fotos auxiliares", "multiple": True},
            _CONCLUIDO,
        ],
    },

    "Abertura de OS - VMMT": {
        "OS Instalação": [*_VMMT_FOTOS, _CONCLUIDO],
        "OS Manutenção": [*_VMMT_FOTOS, _CONCLUIDO],
        "OS Remoção": [
            {"type": "photo", "label": "Fotos do local de instalação", "multiple": True},
            _CONCLUIDO,
        ],
        "OS Outros Assuntos": [
            {"type": "text",  "label": "Descreva o serviço"},
            {"type": "photo", "label": "Fotos auxiliares", "multiple": True},
            _CONCLUIDO,
        ],
    },

    "Abertura de OS - GCT": {
        "Bater Ponto": [
            {"type": "selfie", "label": "Selfie do colaborador"},
        ],
        "Abastecimento": [
            {"type": "photo",  "label": "Foto do painel de quilometragem"},
            {"type": "number", "label": "Quilometragem"},
            {"type": "photo",  "label": "Foto do veículo no posto"},
            {"type": "photo",  "label": "Foto da bomba de gasolina"},
        ],
        "Deslocamento": [
            {"type": "photo",  "label": "Foto do painel de quilometragem"},
            {"type": "number", "label": "Quilometragem"},
        ],
        "Outros Assuntos": [
            {"type": "text",  "label": "Descreva o serviço"},
            {"type": "photo", "label": "Fotos auxiliares", "multiple": True},
        ],
    },

    "COORDENAÇÃO DE SEGURANÇA": {
        "Ocorrência - Patrimonial": [
            {"type": "text",  "label": "Descreva a ocorrência"},
            {"type": "photo", "label": "Fotos da ocorrência (opcional)", "multiple": True, "optional": True},
        ],
        "Ocorrência - Pessoas": [
            {"type": "text",  "label": "Descreva a ocorrência"},
            {"type": "photo", "label": "Fotos da ocorrência (opcional)", "multiple": True, "optional": True},
        ],
        "Ocorrência - Câmeras": [
            {"type": "text",  "label": "Descreva a ocorrência"},
            {"type": "photo", "label": "Fotos da ocorrência (opcional)", "multiple": True, "optional": True},
        ],
        "Outros Assuntos": [
            {"type": "text",  "label": "Descreva a ocorrência"},
            {"type": "photo", "label": "Fotos da ocorrência (opcional)", "multiple": True, "optional": True},
        ],
    },
}


def categorias():
    return list(FLOW.keys())


def subtipos(categoria: str):
    return list(FLOW.get(categoria, {}).keys())


def etapas(categoria: str, subtipo: str):
    return FLOW.get(categoria, {}).get(subtipo, [])
