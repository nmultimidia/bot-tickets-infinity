"""
Envio dos PDFs para uma pasta PRIVADA do Google Drive, recriando a mesma
hierarquia usada localmente:  Categoria / Mês Ano / Tipo de Serviço / Dia.

Usa uma CONTA DE SERVIÇO (service account) -> funciona 24/7, sem login manual.
Se GDRIVE_ENABLED=false no .env, o módulo não faz nada (o salvamento local
continua funcionando normalmente).

Setup resumido (detalhes na resposta):
  1. Criar um projeto no Google Cloud e ativar a "Google Drive API".
  2. Criar uma Conta de Serviço e baixar a chave JSON (service_account.json).
  3. Compartilhar a pasta do Drive com o e-mail da conta de serviço (como Editor).
  4. Preencher no .env: GDRIVE_ENABLED, GDRIVE_CREDENTIALS, GDRIVE_ROOT_FOLDER_ID.
"""
import mimetypes
import os
import threading

from config import GDRIVE_ENABLED, GDRIVE_CREDENTIALS, GDRIVE_ROOT_FOLDER_ID
from config import GDRIVE_AUTH, GDRIVE_OAUTH_TOKEN

_service = None
_folder_cache = {}          # evita recriar/reprocurar pastas a cada chamado
# O cliente HTTP do Google não é thread-safe e os uploads rodam fora do
# event loop (asyncio.to_thread): um chamado por vez.
_lock = threading.Lock()

_SCOPES = ["https://www.googleapis.com/auth/drive"]


def _oauth_creds():
    """Credenciais OAuth (grava como o usuário que autorizou). Usa token.json."""
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request

    if not os.path.exists(GDRIVE_OAUTH_TOKEN):
        raise RuntimeError(
            f"'{GDRIVE_OAUTH_TOKEN}' não encontrado. Rode authorize_drive.py uma vez."
        )

    creds = Credentials.from_authorized_user_file(GDRIVE_OAUTH_TOKEN, _SCOPES)
    if not creds.valid:
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            with open(GDRIVE_OAUTH_TOKEN, "w") as f:
                f.write(creds.to_json())
        else:
            raise RuntimeError(
                "token.json inválido/expirado. Rode authorize_drive.py de novo."
            )
    return creds


def _get_service():
    global _service
    if _service is None:
        from googleapiclient.discovery import build

        if GDRIVE_AUTH == "oauth":
            creds = _oauth_creds()
        else:
            from google.oauth2 import service_account
            creds = service_account.Credentials.from_service_account_file(
                GDRIVE_CREDENTIALS, scopes=_SCOPES)

        _service = build("drive", "v3", credentials=creds, cache_discovery=False)
    return _service


def _pasta(service, nome, parent_id):
    """Procura a subpasta 'nome' dentro de parent_id; cria se não existir."""
    chave = (parent_id, nome)
    if chave in _folder_cache:
        return _folder_cache[chave]

    seguro = nome.replace("\\", "\\\\").replace("'", "\\'")
    q = (f"name = '{seguro}' and mimeType = 'application/vnd.google-apps.folder' "
         f"and '{parent_id}' in parents and trashed = false")
    resp = service.files().list(
        q=q, fields="files(id, name)", spaces="drive",
        supportsAllDrives=True, includeItemsFromAllDrives=True,
    ).execute()
    achados = resp.get("files", [])

    if achados:
        fid = achados[0]["id"]
    else:
        meta = {
            "name": nome,
            "mimeType": "application/vnd.google-apps.folder",
            "parents": [parent_id],
        }
        fid = service.files().create(
            body=meta, fields="id", supportsAllDrives=True).execute()["id"]

    _folder_cache[chave] = fid
    return fid


def habilitado():
    return GDRIVE_ENABLED


def _executar(acao):
    """Roda acao(service). Em caso de erro, descarta cache de pastas e o
    serviço (token renovado, pasta apagada no Drive...) e tenta mais uma vez."""
    global _service
    with _lock:
        try:
            return acao(_get_service())
        except Exception:  # noqa
            _service = None
            _folder_cache.clear()
            return acao(_get_service())


def _criar_pastas(service, subpastas):
    parent = GDRIVE_ROOT_FOLDER_ID
    for nome in subpastas:
        parent = _pasta(service, nome, parent)
    return parent


def _enviar(service, caminho_local, parent, mimetype):
    from googleapiclient.http import MediaFileUpload

    media = MediaFileUpload(caminho_local, mimetype=mimetype, resumable=True)
    meta = {"name": os.path.basename(caminho_local), "parents": [parent]}
    return service.files().create(
        body=meta, media_body=media, fields="id, webViewLink",
        supportsAllDrives=True,
    ).execute()


def _checar_config():
    if not GDRIVE_ROOT_FOLDER_ID:
        raise RuntimeError("GDRIVE_ROOT_FOLDER_ID não configurado no .env")


def upload_pdf(caminho_local, subpastas):
    """
    caminho_local: caminho do PDF no disco.
    subpastas:     lista ordenada de nomes de pasta a partir da raiz do Drive,
                   ex: ["Abertura de OS - UFMT", "Julho 2026", "OS Instalação", "10"]
    Retorna o link (webViewLink) do arquivo no Drive, ou None se desabilitado.
    """
    if not GDRIVE_ENABLED:
        return None
    _checar_config()

    def acao(service):
        parent = _criar_pastas(service, subpastas)
        return _enviar(service, caminho_local, parent, "application/pdf")

    arquivo = _executar(acao)
    return (arquivo.get("webViewLink")
            or f"https://drive.google.com/file/d/{arquivo['id']}/view")


def upload_pasta(pasta_local, subpastas):
    """Envia todos os arquivos de pasta_local para uma subpasta de mesmo nome
    dentro de subpastas (ao lado do PDF). Para no primeiro erro."""
    if not GDRIVE_ENABLED or not os.path.isdir(pasta_local):
        return None
    _checar_config()
    destino = list(subpastas) + [os.path.basename(pasta_local)]
    for nome in sorted(os.listdir(pasta_local)):
        caminho = os.path.join(pasta_local, nome)
        tipo = mimetypes.guess_type(nome)[0] or "application/octet-stream"
        _executar(lambda service: _enviar(
            service, caminho, _criar_pastas(service, destino), tipo))
    return destino


def explicar_erro(erro) -> str:
    """Traduz os erros mais comuns do Google em instruções de correção."""
    texto = f"{type(erro).__name__}: {erro}"
    baixo = texto.lower()
    if "storagequotaexceeded" in baixo or "do not have storage quota" in baixo:
        return (texto + " → Contas de serviço não têm espaço em Drive pessoal. "
                "Use GDRIVE_AUTH=oauth e gere o token.json com authorize_drive.py.")
    if "invalid_grant" in baixo or "token.json" in baixo or "refresherror" in baixo:
        return (texto + " → Autorização do Google expirada/revogada. Rode "
                "authorize_drive.py de novo. Se o app OAuth estiver em modo "
                "'Teste' no Google Cloud, o token expira a cada 7 dias: mude "
                "para 'Em produção'.")
    if "notfound" in baixo or "file not found" in baixo or " 404" in baixo:
        return (texto + " → Pasta raiz do Drive não encontrada ou sem acesso. "
                "Confira GDRIVE_ROOT_FOLDER_ID e se a pasta está compartilhada "
                "com a conta usada pelo bot (Editor).")
    if "insufficientpermissions" in baixo or " 403" in baixo:
        return texto + " → A conta usada pelo bot não tem permissão de Editor na pasta."
    return texto
