# Bot de Tickets — Discord (N MULTIMIDIA)

Bot de abertura de chamados para técnicos em campo. Ao clicar em **Abrir Ticket**,
o bot conduz o colaborador pelo fluxo de perguntas (conforme o fluxograma do cliente),
coleta textos e fotos, gera um **PDF** com todo o histórico e salva na estrutura de
pastas padronizada.

## O que já está implementado

- ✅ Botão **"Abrir Ticket"** (persistente — sobrevive a reinícios)
- ✅ Fluxo completo do fluxograma: UFMT, VMMT, IFT e Coordenação de Segurança, com todos os subtipos e perguntas
- ✅ Coleta de textos, números e **fotos** (uma ou várias por etapa)
- ✅ Pergunta **"Serviço concluído? (Sim/Não)"** onde o fluxo prevê
- ✅ Registro automático: colaborador (login), data/hora e **localização via EXIF da foto**
- ✅ Geração de **PDF** com metadados + respostas + fotos + transcrição da conversa
- ✅ Salvamento na hierarquia: `Categoria / Mês Ano / Tipo de Serviço / Dia / arquivo.pdf`
- ✅ Nome do PDF: `Dia_HoraMin_TipoServico_NomeColaborador.pdf`
- ✅ Upload do PDF também para o **Google Drive** (opcional, via conta de serviço)
- ✅ **Logs administrativos** em canais de Staff: aviso de ticket criado/finalizado,
  mensagens editadas/apagadas e entrada/saída de membros
- ✅ Comandos admin: `/painel`, `/listar`, `/fechar`, `/adicionar`, `/remover`,
  `/definir_log`, `/ver_logs`

## Instalação

```bash
python3 -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env              # depois edite o .env
```

## Configuração do bot no Discord

1. Acesse o **Discord Developer Portal** → New Application.
2. Aba **Bot** → *Reset Token* → copie o token para `DISCORD_TOKEN` no `.env`.
3. Ainda em **Bot**, ative **Privileged Gateway Intents**:
   - *Server Members Intent*
   - *Message Content Intent*
4. Aba **OAuth2 → URL Generator**: marque `bot` e `applications.commands`.
   Permissões mínimas: *Manage Threads, Send Messages, Read Message History,
   Attach Files, Create Private Threads, Embed Links*.
5. Abra a URL gerada e adicione o bot ao servidor.

## Como usar

```bash
python bot.py
```

- Rode `/painel` no canal onde os técnicos vão abrir chamados.
- O técnico clica em **Abrir Ticket** → o bot cria uma thread privada e faz as perguntas.
- Dentro do ticket, a equipe usa `/adicionar @membro` (ou escolhe um cargo)
  para convidar alguém e `/remover` para retirar seu acesso.
- Ao final, o PDF é enviado na thread **e** salvo na pasta `STORAGE_ROOT`.
- `/limpar [dias]` apaga as threads de ticket já arquivadas (pede confirmação); use `dias` para só apagar as mais antigas.

## Logs administrativos (canais de Staff)

O bot envia avisos para canais de log, um por tipo de evento:

| Tipo | Quando dispara | Ativo por padrão? |
|---|---|---|
| Tickets gerados | Ticket criado e finalizado (com resumo e link do PDF) | ✅ Sim |
| Mensagens editadas | Alguém edita uma mensagem (mostra antes/depois) | Só com `MSG_LOGS_ENABLED=true` |
| Mensagens deletadas | Alguém apaga uma mensagem (mostra o conteúdo) | Só com `MSG_LOGS_ENABLED=true` |
| Entrada de membros | Membro entra no servidor | Só com `MSG_LOGS_ENABLED=true` |
| Saída de membros | Membro sai do servidor | Só com `MSG_LOGS_ENABLED=true` |

**Como apontar cada tipo para um canal:**

1. **Recomendado:** entre no canal desejado e rode `/definir_log` escolhendo o tipo.
   O bot grava o **ID** do canal em `log_config.json` — pode renomear o canal depois
   que continua funcionando.
2. **Automático (fallback):** sem configurar nada, o bot procura canais cujo nome
   contenha `logs-tickets-gerados`, `logs-msg-editada`, `logs-msg-deletas`,
   `logs-entrada` ou `logs-saida`.

Use `/ver_logs` para conferir qual canal está ligado a cada tipo.

> O bot precisa de *Ver Canal* e *Enviar Mensagens* em cada canal de log.
> Para mostrar o conteúdo de mensagens apagadas/editadas, a mensagem precisa
> ter sido enviada enquanto o bot estava online (limitação do Discord).

## Onde os arquivos são salvos

Definido por `STORAGE_ROOT` no `.env`:
- **Servidor físico:** aponte para um ponto de montagem de rede (ex: `/mnt/servidor/chamados`).
- **Google Drive / nuvem:** aponte para uma pasta sincronizada pelo *Google Drive para Desktop* ou por *rclone*.

### Upload direto para o Google Drive (opcional)

Além do salvamento local, cada PDF pode ser enviado direto para o Drive via
conta de serviço, recriando a mesma hierarquia de pastas. No `.env`:

```ini
GDRIVE_ENABLED=true
GDRIVE_CREDENTIALS=service_account.json   # chave JSON da conta de serviço
GDRIVE_ROOT_FOLDER_ID=xxxxxxxx            # trecho após /folders/ na URL da pasta
```

A pasta do Drive precisa estar **compartilhada com o e-mail da conta de serviço**
(permissão de Editor). Uma falha no upload não cancela o chamado — o PDF já fica
salvo localmente e o erro aparece no log do bot.

## Deploy 24/7

### Opção A — systemd (VPS Linux, recomendado)

Crie `/etc/systemd/system/bot-tickets.service`:

```ini
[Unit]
Description=Bot de Tickets Discord
After=network.target

[Service]
WorkingDirectory=/opt/bot-tickets
ExecStart=/opt/bot-tickets/venv/bin/python bot.py
Restart=always
RestartSec=5
User=botuser

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now bot-tickets
sudo journalctl -u bot-tickets -f      # ver logs
```

### Opção B — Docker

```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
CMD ["python", "bot.py"]
```

```bash
docker build -t bot-tickets .
docker run -d --restart=always --env-file .env -v /dados/chamados:/app/chamados bot-tickets
```

## Ajustar o fluxo de perguntas

Todo o fluxo fica em **`flow.py`**, num único dicionário fácil de editar.
Para adicionar/remover perguntas, basta mexer nas listas de etapas.
Tipos disponíveis: `text`, `number`, `photo` (use `"multiple": True` p/ várias),
`selfie`, `yesno`.

> Observação: o campo **"Número da OS"** foi adicionado nas OS de UFMT/VMMT porque
> aparece na descrição original do projeto, mas **não** estava no fluxograma. As
> linhas estão marcadas com `# [confirmar com o cliente]` — remova se ele não quiser.
