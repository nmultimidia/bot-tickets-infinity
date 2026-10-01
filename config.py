"""
Configurações centrais do bot.
Lê variáveis do arquivo .env (veja .env.example).
"""
import os
import re
import unicodedata
from dotenv import load_dotenv

load_dotenv()

# Token do bot (obrigatório) — pegue no Developer Portal do Discord.
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "")

# Pasta raiz onde os chamados serão salvos.
# Pode ser uma pasta local, um ponto de montagem de rede (servidor físico)
# ou uma pasta sincronizada com nuvem (Google Drive Desktop, rclone, etc).
STORAGE_ROOT = os.getenv("STORAGE_ROOT", "./chamados")

# ID do canal onde o painel "Abrir Ticket" será postado (opcional).
# Se vazio, use o comando /painel dentro do canal desejado.
PANEL_CHANNEL_ID = int(os.getenv("PANEL_CHANNEL_ID", "0") or "0")

# IDs de cargos com permissão administrativa no bot, separados por vírgula.
ADMIN_ROLE_IDS = [
    int(x) for x in os.getenv("ADMIN_ROLE_IDS", "").replace(" ", "").split(",") if x
]

# IDs de cargos adicionados automaticamente a todo ticket (ex: Equipe 1).
TICKET_ROLE_IDS = [
    int(x) for x in os.getenv("TICKET_ROLE_IDS", "").replace(" ", "").split(",") if x
]

# Tempo máximo (segundos) que o bot aguarda cada resposta antes de expirar o
# ticket. Deixe vazio ou "0" para o ticket nunca expirar por tempo.
_step_timeout_raw = os.getenv("STEP_TIMEOUT", "1800").strip()
STEP_TIMEOUT = int(_step_timeout_raw) if _step_timeout_raw and _step_timeout_raw != "0" else None

# Fluxo do ticket:
#   true  -> ticket livre (sem perguntas). O técnico envia texto/fotos à
#            vontade e escreve "pronto" para travar. Só a administração
#            (permissão "Gerenciar Tópicos") pode fechar/gerar o relatório.
#   false -> fluxo antigo, com perguntas passo a passo (script do fluxograma).
FLUXO_TICKET_LIVRE = os.getenv("FLUXO_TICKET_LIVRE", "true").lower() == "true"

# ---- Google Drive (opcional) ----------------------------------------------
# Se GDRIVE_ENABLED=true, cada PDF também é enviado para o Google Drive,
# recriando a mesma hierarquia de pastas dentro da pasta raiz indicada.
GDRIVE_ENABLED = os.getenv("GDRIVE_ENABLED", "false").lower() == "true"
# Caminho para a chave JSON da conta de serviço.
GDRIVE_CREDENTIALS = os.getenv("GDRIVE_CREDENTIALS", "service_account.json")
# ID da pasta do Drive onde tudo será salvo (o trecho após /folders/ na URL).
GDRIVE_ROOT_FOLDER_ID = os.getenv("GDRIVE_ROOT_FOLDER_ID", "")

# Autenticação do Drive: "oauth" (grava como você/cliente — funciona em Gmail
# pessoal, gravando na pasta indicada) ou "service_account".
GDRIVE_AUTH = os.getenv("GDRIVE_AUTH", "service_account")
# Modo OAuth: token gerado uma vez pelo authorize_drive.py.
GDRIVE_OAUTH_TOKEN = os.getenv("GDRIVE_OAUTH_TOKEN", "token.json")
GDRIVE_CLIENT_SECRET = os.getenv("GDRIVE_CLIENT_SECRET", "client_secret.json")

# ---- Logs administrativos --------------------------------------------------
# Os canais de destino são definidos com /definir_log (ou por nome — ver logs.py).
# Se true, ativa os logs de mensagens editadas/apagadas e entrada/saída de membros.
MSG_LOGS_ENABLED = os.getenv("MSG_LOGS_ENABLED", "false").lower() == "true"

# Meses em português para o nome da pasta (ex: "Julho 2026").
MESES = {
    1: "Janeiro", 2: "Fevereiro", 3: "Março", 4: "Abril",
    5: "Maio", 6: "Junho", 7: "Julho", 8: "Agosto",
    9: "Setembro", 10: "Outubro", 11: "Novembro", 12: "Dezembro",
}


def camel(texto: str) -> str:
    """Remove acentos e espaços, devolvendo em CamelCase para nome de arquivo.
    Ex: 'OS Instalação' -> 'OSInstalacao'; 'João Silva' -> 'JoaoSilva'."""
    texto = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    partes = re.split(r"[^A-Za-z0-9]+", texto)
    return "".join(p[:1].upper() + p[1:] for p in partes if p)
