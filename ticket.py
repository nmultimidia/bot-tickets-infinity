"""
Motor do ticket.

Fluxo livre (padrão): o técnico escolhe só a categoria e envia textos e fotos
à vontade. Ao escrever "FINALIZAR TICKET", o canal trava para ele e a
administração recebe o botão para gerar o relatório.

O relatório do fluxo livre é montado a partir do HISTÓRICO do canal, e não da
memória do bot. Assim nada se perde se o bot reiniciar, se o técnico demorar
horas entre uma foto e outra, ou se outra pessoa do ticket enviar as fotos.
Categoria, tipo e autor ficam gravados no tópico do canal.

Fluxo com perguntas (FLUXO_TICKET_LIVRE=false): conduz o colaborador pelo
fluxograma passo a passo.
"""
import asyncio
import io
import os
import re
import traceback
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import discord
from PIL import Image as PILImage

import flow
import gdrive
import storage
import config
import logs
from config import STEP_TIMEOUT
from pdf_generator import gerar_pdf, extrair_localizacao

# Palavras que encerram o envio de várias fotos no fluxo com perguntas
PALAVRAS_FIM = {"pronto", "pronta", "fim", "ok", "concluir", "concluido",
                "finalizar", "finalizado"}
# No fluxo livre "ok" fica de fora: é resposta comum numa conversa e
# travaria o ticket sem querer.
PALAVRAS_FIM_LIVRE = PALAVRAS_FIM - {"ok"}
_NEGACOES = {"nao", "n", "nem", "ainda"}

_EXTENSOES_IMAGEM = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tif",
                     ".tiff", ".heic", ".heif"}


def _anexo_imagem(anexo):
    tipo = (anexo.content_type or "").lower()
    return tipo.startswith("image/") or Path(anexo.filename).suffix.lower() in _EXTENSOES_IMAGEM


def _palavras(texto):
    texto = unicodedata.normalize("NFKD", texto or "").encode("ascii", "ignore").decode()
    return re.findall(r"[a-z0-9]+", texto.lower())


def eh_pedido_de_fim(texto, palavras=PALAVRAS_FIM_LIVRE, max_palavras=3):
    """Aceita "pronto", "Pronto.", "pronto!", "tá pronto", "pronto ✅"...
    mas não frases longas nem negações ("ainda não está pronto")."""
    msg = _palavras(texto)
    if not msg or len(msg) > max_palavras or _NEGACOES & set(msg):
        return False
    return any(p in palavras for p in msg)


# No fluxo livre, só esta frase trava o ticket ("pronto" sozinho não vale mais).
FRASE_FINALIZAR = "FINALIZAR TICKET"


def eh_finalizar_ticket(texto):
    """Aceita "FINALIZAR TICKET", "finalizar ticket", "Finalizar ticket!"..."""
    return _palavras(texto) == _palavras(FRASE_FINALIZAR)


# --------------------------------------------------------------------------
# Tópico do canal: guarda autor, categoria e tipo de forma persistente
# --------------------------------------------------------------------------
def ler_topico(canal) -> dict:
    dados = {}
    for parte in (getattr(canal, "topic", None) or "").split("|"):
        chave, sep, valor = parte.partition("=")
        if sep:
            dados[chave.strip()] = valor.strip()
    return dados


def montar_topico(**campos) -> str:
    return " | ".join(f"{k}={v}" for k, v in campos.items() if v)[:1024]


async def _gravar_categoria(canal, autor_id, categoria, subtipo):
    if not isinstance(canal, discord.TextChannel):
        return
    try:
        await canal.edit(topic=montar_topico(autor=autor_id, categoria=categoria, tipo=subtipo))
    except discord.HTTPException as e:
        print("Falha ao gravar o tópico do ticket:", e)


async def _autor_do_canal(canal, dados=None):
    """Quem abriu o ticket: pelo tópico ou, em tickets antigos, pela permissão
    dada na criação (o criador tem manage_channels=False explícito)."""
    dados = dados if dados is not None else ler_topico(canal)
    aid = dados.get("autor", "")
    if aid.isdigit():
        membro = canal.guild.get_member(int(aid))
        if membro:
            return membro
        try:
            return await canal.guild.fetch_member(int(aid))
        except discord.HTTPException:
            pass
    for alvo, ow in getattr(canal, "overwrites", {}).items():
        if (isinstance(alvo, discord.Member) and not alvo.bot
                and ow.view_channel and ow.manage_channels is False):
            return alvo
    return None


async def _permitir_envio(canal, membro, permitir: bool):
    if not isinstance(canal, discord.TextChannel) or not isinstance(membro, discord.Member):
        return
    ow = canal.overwrites_for(membro)
    ow.update(view_channel=True, read_message_history=True, send_messages=permitir)
    try:
        await canal.set_permissions(membro, overwrite=ow)
    except discord.HTTPException as e:
        print("Falha ao alterar permissão no ticket:", e)


async def _renomear(canal, nome):
    # Renomear tem limite de 2 por 10 min no Discord; roda em segundo plano
    # para não segurar o fechamento esperando o rate limit.
    try:
        await canal.edit(name=nome[:100])
    except discord.HTTPException as e:
        print("Falha ao renomear o ticket:", e)


async def encerrar_canal(canal, autor):
    if isinstance(canal, discord.Thread):
        try:
            await canal.edit(archived=True, locked=True)
        except discord.HTTPException:
            pass
        return
    await _permitir_envio(canal, autor, False)
    if not canal.name.endswith("-fechado"):
        asyncio.create_task(_renomear(canal, f"{canal.name}-fechado"))


# --------------------------------------------------------------------------
# Componentes
# --------------------------------------------------------------------------
class SelectView(discord.ui.View):
    """Menu suspenso genérico que resolve um Future com a opção escolhida."""

    def __init__(self, opcoes, autor_id, placeholder="Selecione...", timeout=STEP_TIMEOUT):
        super().__init__(timeout=timeout)
        self.autor_id = autor_id
        self.future: asyncio.Future = asyncio.get_event_loop().create_future()
        select = discord.ui.Select(
            placeholder=placeholder,
            options=[discord.SelectOption(label=o) for o in opcoes],
        )
        select.callback = self._on_select
        self._select = select
        self.add_item(select)

    async def _on_select(self, interaction: discord.Interaction):
        if interaction.user.id != self.autor_id:
            await interaction.response.send_message(
                "Apenas quem abriu o ticket pode responder.", ephemeral=True)
            return
        await interaction.response.defer()
        if not self.future.done():
            self.future.set_result(self._select.values[0])
        self.stop()

    async def on_timeout(self):
        # Sem isso quem espera o Future ficaria travado para sempre.
        if not self.future.done():
            self.future.set_exception(asyncio.TimeoutError())


class YesNoView(discord.ui.View):
    def __init__(self, autor_id):
        super().__init__(timeout=STEP_TIMEOUT)
        self.autor_id = autor_id
        self.future: asyncio.Future = asyncio.get_event_loop().create_future()

    async def _responder(self, interaction, valor):
        if interaction.user.id != self.autor_id:
            await interaction.response.send_message(
                "Apenas quem abriu o ticket pode responder.", ephemeral=True)
            return
        await interaction.response.defer()
        if not self.future.done():
            self.future.set_result(valor)
        self.stop()

    async def on_timeout(self):
        if not self.future.done():
            self.future.set_exception(asyncio.TimeoutError())

    @discord.ui.button(label="Sim", style=discord.ButtonStyle.success)
    async def sim(self, interaction: discord.Interaction, _):
        await self._responder(interaction, "Sim")

    @discord.ui.button(label="Não", style=discord.ButtonStyle.danger)
    async def nao(self, interaction: discord.Interaction, _):
        await self._responder(interaction, "Não")


async def escolher_categoria(canal, autor_id, timeout=None):
    """Pergunta só a categoria pelo menu (o tipo de serviço não é mais pedido)."""
    pergunta = "Qual o tipo de assunto?"
    view = SelectView(flow.categorias(), autor_id, placeholder=pergunta, timeout=timeout)
    await canal.send(pergunta, view=view)
    return await view.future


def _pode_fechar(member: discord.Member) -> bool:
    """Só administração fecha o ticket livre: Administrator, Gerenciar
    Tópicos, ou um dos cargos em ADMIN_ROLE_IDS."""
    perms = getattr(member, "guild_permissions", None)
    if perms and (perms.administrator or perms.manage_threads):
        return True
    ids = {r.id for r in getattr(member, "roles", [])}
    return bool(ids & set(config.ADMIN_ROLE_IDS))


class FecharChamadoView(discord.ui.View):
    """Botão exclusivo da administração: gera o PDF/relatório e fecha.

    Não guarda estado: tudo vem do canal. Registrado com bot.add_view, então
    continua funcionando depois de reiniciar o bot (inclusive em botões
    publicados antes desta versão, que usam o mesmo custom_id)."""

    def __init__(self, desativado=False):
        super().__init__(timeout=None)
        self.fechar.disabled = desativado

    @discord.ui.button(label="Fechar e gerar relatório", style=discord.ButtonStyle.success,
                       emoji="📋", custom_id="fechar_chamado_livre")
    async def fechar(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not _pode_fechar(interaction.user):
            await interaction.response.send_message(
                "Apenas a administração pode fechar este chamado.", ephemeral=True)
            return
        canal = interaction.channel
        if getattr(canal, "name", "").endswith("-fechado"):
            await interaction.response.send_message(
                "Este chamado já foi fechado. Para gerar o relatório de novo, "
                "use `/gerar_relatorio`.", ephemeral=True)
            return
        await interaction.response.edit_message(view=FecharChamadoView(desativado=True))
        ok = await finalizar_ticket_livre(interaction.client, canal)
        if not ok:
            try:
                await interaction.message.edit(view=FecharChamadoView())
            except discord.HTTPException:
                pass


# --------------------------------------------------------------------------
# Fluxo livre: "pronto" e geração do relatório a partir do histórico
# --------------------------------------------------------------------------
_marcando_pronto = set()
_travas = {}


def _eh_canal_ticket(canal) -> bool:
    """Ticket pelo prefixo do nome ou, em canal de texto, pelo tópico
    "autor=<id>" gravado na criação — assim o ticket continua reconhecido
    depois que a equipe renomeia o canal (ex: nome da obra)."""
    if not isinstance(canal, (discord.TextChannel, discord.Thread)):
        return False
    if canal.name.startswith("ticket-"):
        return True
    return isinstance(canal, discord.TextChannel) and "autor" in ler_topico(canal)


async def tratar_mensagem(bot, msg: discord.Message):
    """Listener global de on_message: detecta o "FINALIZAR TICKET" em qualquer
    ticket, de qualquer participante, mesmo depois de o bot reiniciar."""
    if msg.author.bot or not config.FLUXO_TICKET_LIVRE:
        return
    canal = msg.channel
    if not _eh_canal_ticket(canal) or canal.name.endswith("-fechado"):
        return
    if not eh_finalizar_ticket(msg.content):
        return
    if canal.id in _marcando_pronto:
        return
    _marcando_pronto.add(canal.id)
    try:
        await marcar_pronto(canal, msg.author)
    except asyncio.TimeoutError:
        await canal.send(f"⏱️ A categoria não foi escolhida. Escreva **{FRASE_FINALIZAR}** "
                         "de novo quando quiser.")
    except Exception as e:  # noqa
        traceback.print_exc()
        await canal.send(
            f"❌ Não consegui marcar o ticket como concluído: `{e}`\n"
            "Nada foi perdido: todas as mensagens e fotos continuam neste canal. "
            f"Escreva **{FRASE_FINALIZAR}** de novo ou chame a administração.")
    finally:
        _marcando_pronto.discard(canal.id)


async def marcar_pronto(canal, quem):
    dados = ler_topico(canal)
    autor = await _autor_do_canal(canal, dados) or quem
    # Tickets antigos podem ter tipo de serviço gravado; os novos só categoria.
    categoria, subtipo = dados.get("categoria"), dados.get("tipo")
    if not categoria:
        await canal.send(f"{quem.mention}, antes de finalizar escolha o tipo de assunto:")
        categoria, subtipo = await escolher_categoria(canal, quem.id, timeout=3600), None
        await _gravar_categoria(canal, autor.id, categoria, subtipo)

    await _permitir_envio(canal, autor, False)
    await canal.send(
        "🔒 Atendimento marcado como concluído. Todas as mensagens e fotos deste "
        "canal vão para o relatório. Um administrador vai revisar e fechar o chamado.")

    embed = discord.Embed(
        title="🔒 Ticket pronto para fechamento",
        description=(f"Colaborador: {autor.mention}\n"
                     f"{_rotulo_categoria(categoria, subtipo)}\n"
                     f"Canal: {canal.mention}"),
        color=0xf1c40f,
    )
    embed.timestamp = discord.utils.utcnow()
    await logs.enviar(canal.guild, "ticket", embed)

    await canal.send(
        "Administração: clique abaixo para gerar o relatório e fechar.",
        view=FecharChamadoView())


@dataclass
class Historico:
    textos: list = field(default_factory=list)
    imagens: list = field(default_factory=list)       # bytes, para o PDF
    outros: list = field(default_factory=list)        # nomes de anexos não-imagem
    transcricao: list = field(default_factory=list)
    falhas: list = field(default_factory=list)
    primeiro_autor: object = None
    total_anexos: int = 0


async def _salvar_anexo(att, destino, tentativas=3):
    for i in range(tentativas):
        try:
            await att.save(destino, use_cached=False)
            return
        except Exception:  # noqa — rede/CDN instável: tenta de novo
            if i == tentativas - 1:
                raise
            await asyncio.sleep(2 * (i + 1))


async def coletar_historico(canal, bot_user, autor_id, pasta_anexos) -> Historico:
    """Lê o canal inteiro e salva cada anexo original em pasta_anexos."""
    h = Historico()
    async for msg in canal.history(limit=None, oldest_first=True):
        if msg.author.bot:
            if bot_user and msg.author.id == bot_user.id and msg.content:
                h.transcricao.append(("Bot", msg.content))
            continue

        if h.primeiro_autor is None:
            h.primeiro_autor = msg.author
        nome = msg.author.display_name
        quando = msg.created_at.astimezone().strftime("%d/%m %H:%M")
        conteudo = (msg.content or "").strip()
        # A frase de fechamento (e o "pronto" de tickets antigos) não entra no relatório.
        if conteudo and not (eh_finalizar_ticket(conteudo) or eh_pedido_de_fim(conteudo)):
            h.textos.append(conteudo if msg.author.id == autor_id else f"{nome}: {conteudo}")

        for att in msg.attachments:
            h.total_anexos += 1
            os.makedirs(pasta_anexos, exist_ok=True)
            destino = os.path.join(pasta_anexos, storage.nome_anexo(h.total_anexos, att.filename))
            try:
                await _salvar_anexo(att, destino)
            except Exception as e:  # noqa
                h.falhas.append(f"{att.filename} ({nome}, {quando}): {e}")
                continue
            if _anexo_imagem(att):
                with open(destino, "rb") as f:
                    h.imagens.append(f.read())
            else:
                h.outros.append(att.filename)

        resumo = conteudo
        if msg.attachments:
            resumo = f"{resumo} [{len(msg.attachments)} anexo(s)]".strip()
        if resumo:
            h.transcricao.append((f"{nome} ({quando})", resumo))
    return h


async def finalizar_ticket_livre(bot, canal, *, categoria=None, subtipo=None, autor=None):
    """Gera o relatório a partir do histórico do canal. Devolve True se tudo
    (PDF, arquivos e Google Drive) deu certo e o ticket foi fechado."""
    trava = _travas.setdefault(canal.id, asyncio.Lock())
    if trava.locked():
        await canal.send("⏳ O relatório deste ticket já está sendo gerado. Aguarde.")
        return False
    async with trava:
        try:
            dados = ler_topico(canal)
            if not categoria:
                categoria, subtipo = dados.get("categoria"), dados.get("tipo")
            if not categoria:
                await canal.send(
                    "❌ Não sei a categoria deste ticket. A administração pode usar "
                    "`/gerar_relatorio` informando a categoria.")
                return False

            await canal.send("📥 Lendo o histórico do ticket e baixando os arquivos... ⏳")
            autor = autor or await _autor_do_canal(canal, dados)
            quando = canal.created_at.astimezone()
            nome_autor = autor.display_name if autor else "Colaborador"
            caminho = storage.montar_caminho(categoria, subtipo, nome_autor, quando)
            pasta = storage.pasta_anexos(caminho)

            h = await coletar_historico(canal, bot.user, autor.id if autor else None, pasta)
            if autor is None and h.primeiro_autor is not None:
                autor = h.primeiro_autor

            respostas = [
                {"label": "Registro do atendimento", "type": "text",
                 "valor": "\n".join(h.textos) if h.textos else "—"},
                {"label": "Fotos enviadas", "type": "photo", "imagens": h.imagens},
            ]
            if h.outros:
                respostas.append({"label": "Outros arquivos (salvos junto com o PDF)",
                                  "type": "text", "valor": "\n".join(h.outros)})

            localizacao = next(filter(None, map(extrair_localizacao, h.imagens)), None)
            ok = await registrar_chamado(
                canal, autor=autor, categoria=categoria, subtipo=subtipo, quando=quando,
                caminho=caminho, pasta=pasta, respostas=respostas,
                transcricao=h.transcricao, localizacao=localizacao,
                n_fotos=len(h.imagens), n_outros=len(h.outros), falhas=h.falhas)
            if ok:
                await encerrar_canal(canal, autor)
            return ok
        except Exception as e:  # noqa
            traceback.print_exc()
            await canal.send(
                f"❌ Erro ao gerar o relatório: `{e}`\n"
                "Nada foi perdido: todas as mensagens e fotos continuam neste canal. "
                "Tente de novo pelo botão ou com `/gerar_relatorio`.")
            return False


def _rotulo_categoria(categoria, subtipo=None):
    texto = f"Categoria: **{categoria}**"
    return texto + (f" • Tipo: **{subtipo}**" if subtipo else "")


def _corta(texto, limite=300):
    texto = str(texto)
    return texto if len(texto) <= limite else texto[:limite] + "…"


async def registrar_chamado(canal, *, autor, categoria, subtipo, quando, caminho, pasta,
                            respostas, transcricao, localizacao, n_fotos, n_outros=0,
                            falhas=()):
    """Gera o PDF, envia PDF + originais ao Drive e avisa no canal e no log.
    Devolve True só se não houve nenhuma pendência."""
    nome_autor = autor.display_name if autor else "Colaborador"
    meta = {
        "colaborador": nome_autor,
        "data_hora": quando,
        "gerado_em": datetime.now(),
        "categoria": categoria,
        "subtipo": subtipo,
        "localizacao": localizacao,
    }
    # PDF e upload são síncronos e pesados: fora do event loop para não
    # derrubar a conexão do bot com o Discord.
    await asyncio.to_thread(gerar_pdf, caminho, meta=meta, respostas=respostas,
                            transcricao=transcricao)

    link_drive, erro_drive = None, None
    if gdrive.habilitado():
        try:
            comps = storage.componentes_pasta(categoria, subtipo, quando)
            link_drive = await asyncio.to_thread(gdrive.upload_pdf, caminho, comps)
            if os.path.isdir(pasta):
                await asyncio.to_thread(gdrive.upload_pasta, pasta, comps)
        except Exception as e:  # noqa
            traceback.print_exc()
            erro_drive = gdrive.explicar_erro(e)

    nome = os.path.basename(caminho)
    local = os.path.relpath(caminho, config.STORAGE_ROOT)
    ok = not erro_drive and not falhas
    linhas = [
        "✅ **Chamado registrado!**" if ok else "⚠️ **Chamado registrado com pendências**",
        f"Colaborador: **{nome_autor}**",
        _rotulo_categoria(categoria, subtipo),
        f"Data/Hora: **{quando.strftime('%d/%m/%Y %H:%M')}**",
        f"Localização: **{localizacao or 'não disponível'}**",
        f"Fotos: **{n_fotos}**" + (f" • Outros arquivos: **{n_outros}**" if n_outros else ""),
        f"Arquivo: `{nome}`",
    ]
    if link_drive:
        linhas.append(f"☁️ Google Drive: {link_drive}")
    if erro_drive:
        linhas.append(f"❌ **Google Drive falhou:** `{_corta(erro_drive)}`\n"
                      f"O PDF e os arquivos estão salvos no servidor (`{local}`).")
    if falhas:
        linhas.append("❌ **Não consegui baixar:** " + "; ".join(_corta(f, 120) for f in falhas[:5]))
    if not ok:
        linhas.append("O ticket continua aberto. Depois de corrigir, gere de novo "
                      "pelo botão ou com `/gerar_relatorio`.")
    texto = "\n".join(linhas)[:2000]

    limite = getattr(getattr(canal, "guild", None), "filesize_limit", 8 * 1024 * 1024)
    try:
        if os.path.getsize(caminho) <= limite:
            await canal.send(texto, file=discord.File(caminho))
        else:
            await canal.send((texto + "\n_(PDF grande demais para anexar no Discord.)_")[:2000])
    except discord.HTTPException:
        await canal.send(texto)

    embed = discord.Embed(
        title="✅ Ticket finalizado" if ok else "⚠️ Ticket com pendências",
        description=(f"Colaborador: {autor.mention if autor else nome_autor}\n"
                     f"{_rotulo_categoria(categoria, subtipo)}\n"
                     f"Data/Hora: **{quando.strftime('%d/%m/%Y %H:%M')}**\n"
                     f"Canal: {canal.mention}\n"
                     f"Arquivo: `{nome}`"),
        color=0x2ecc71 if ok else 0xe67e22,
    )
    if link_drive:
        embed.add_field(name="☁️ Google Drive",
                        value=f"[Abrir PDF no Google Drive]({link_drive})", inline=False)
    if erro_drive:
        embed.add_field(name="❌ Google Drive falhou", value=_corta(erro_drive, 1000), inline=False)
    if falhas:
        embed.add_field(name="❌ Arquivos não baixados",
                        value=_corta("\n".join(falhas), 1000), inline=False)
    embed.timestamp = discord.utils.utcnow()
    await logs.enviar(canal.guild, "ticket", embed)
    return ok


def _extensao(dados):
    try:
        fmt = PILImage.open(io.BytesIO(dados)).format
        return "." + ({"JPEG": "jpg"}.get(fmt, fmt or "bin")).lower()
    except Exception:  # noqa
        return ".bin"


# --------------------------------------------------------------------------
# Fluxo do ticket
# --------------------------------------------------------------------------
class TicketFlow:
    def __init__(self, bot, thread: discord.Thread, autor: discord.Member):
        self.bot = bot
        self.thread = thread
        self.autor = autor
        self.transcricao = []   # [(autor, texto)]
        self.respostas = []     # [{label, type, valor/imagens}]
        self.localizacao = None
        self._mensagens = asyncio.Queue()

    # -- utilidades de conversa ------------------------------------------

    async def _bot_diz(self, texto, view=None):
        self.transcricao.append(("Bot", texto))
        return await self.thread.send(texto, view=view)

    def _check_msg(self, m):
        return m.author.id == self.autor.id and m.channel.id == self.thread.id

    async def _aguardar_msg(self):
        return await asyncio.wait_for(self._mensagens.get(), timeout=STEP_TIMEOUT)

    async def _receber_msg(self, msg):
        if self._check_msg(msg):
            self._mensagens.put_nowait(msg)

    async def _ask_select(self, pergunta, opcoes):
        view = SelectView(opcoes, self.autor.id, placeholder=pergunta[:100])
        await self._bot_diz(pergunta, view=view)
        escolha = await view.future
        self.transcricao.append((self.autor.display_name, escolha))
        return escolha

    async def _ask_yesno(self, pergunta):
        view = YesNoView(self.autor.id)
        await self._bot_diz(pergunta, view=view)
        resp = await view.future
        self.transcricao.append((self.autor.display_name, resp))
        return resp

    async def _ask_text(self, pergunta):
        await self._bot_diz(pergunta)
        msg = await self._aguardar_msg()
        self.transcricao.append((self.autor.display_name, msg.content))
        return msg.content.strip()

    async def _ask_number(self, pergunta):
        while True:
            texto = await self._ask_text(pergunta + " (apenas números)")
            limpo = texto.replace(",", ".").strip()
            try:
                float(limpo)
                return limpo
            except ValueError:
                await self._bot_diz("Valor inválido. Envie apenas números, por favor.")

    async def _coletar_fotos(self, pergunta, multiplas, opcional=False):
        extra = ""
        if multiplas:
            extra = " Você pode enviar várias; digite **pronto** quando terminar."
        if opcional:
            extra += " (ou digite **pular** para não enviar)"
        await self._bot_diz(pergunta + extra)

        imagens = []
        while True:
            msg = await self._aguardar_msg()
            palavras = set(_palavras(msg.content))
            fim = multiplas and eh_pedido_de_fim(msg.content, PALAVRAS_FIM)

            if opcional and palavras and palavras <= {"pular", "nao", "n"} and not msg.attachments:
                self.transcricao.append((self.autor.display_name, "(sem foto)"))
                break

            if msg.attachments:
                recebidas = 0
                for att in msg.attachments:
                    if _anexo_imagem(att):
                        imagens.append(await att.read())
                        recebidas += 1
                if not recebidas:
                    await self._bot_diz("Nenhuma foto reconhecida. Envie uma imagem como JPG ou PNG.")
                    continue
                self.transcricao.append(
                    (self.autor.display_name, f"[{len(msg.attachments)} anexo(s)]"))
                if not multiplas or fim:
                    break
                await self._bot_diz("Foto recebida. Envie mais ou digite **pronto**.")
                continue

            if fim:
                if imagens or opcional:
                    break
                await self._bot_diz("Nenhuma foto recebida ainda. Envie ao menos uma.")
                continue

            await self._bot_diz("Por favor, envie uma imagem (anexo).")

        # tenta localização a partir da primeira foto que tiver GPS
        for b in imagens:
            if self.localizacao is None:
                self.localizacao = extrair_localizacao(b)
        return imagens

    # -- execução do fluxo ------------------------------------------------

    async def run(self):
        self.bot.add_listener(self._receber_msg, "on_message")
        try:
            if config.FLUXO_TICKET_LIVRE:
                await self._run_livre()
            else:
                await self._run_com_perguntas()
        finally:
            self.bot.remove_listener(self._receber_msg, "on_message")

    async def _run_livre(self):
        """Ticket sem perguntas: só pede a categoria e grava no tópico.
        O "FINALIZAR TICKET" e o relatório são tratados por tratar_mensagem e
        finalizar_ticket_livre, a partir do histórico do canal."""
        try:
            categoria = await escolher_categoria(self.thread, self.autor.id)
            await _gravar_categoria(self.thread, self.autor.id, categoria, None)
            await self._bot_diz(
                f"📂 **{categoria}** registrado.\n"
                f"Olá, {self.autor.mention}! Pode enviar fotos, textos e informações "
                f"à vontade, sem limite de tempo. Quando terminar o atendimento, "
                f"escreva **{FRASE_FINALIZAR}**.")
        except Exception as e:  # noqa
            traceback.print_exc()
            await self.thread.send(
                f"❌ Erro ao registrar a categoria: `{e}`. Pode continuar enviando "
                f"fotos e textos; ao escrever **{FRASE_FINALIZAR}** eu pergunto de novo.")

    async def _run_com_perguntas(self):
        try:
            await self._bot_diz(
                f"Olá, {self.autor.mention}! Vou abrir seu chamado. "
                f"Responda as perguntas a seguir.")

            categoria = await self._ask_select(
                "Qual o tipo de assunto?", flow.categorias())
            subtipo = await self._ask_select(
                "Qual o tipo de serviço?", flow.subtipos(categoria))

            for etapa in flow.etapas(categoria, subtipo):
                tipo, label = etapa["type"], etapa["label"]

                if tipo == "text":
                    valor = await self._ask_text(label + ":")
                    self.respostas.append({"label": label, "type": "text", "valor": valor})

                elif tipo == "number":
                    valor = await self._ask_number(label + ":")
                    self.respostas.append({"label": label, "type": "number", "valor": valor})

                elif tipo in ("photo", "selfie"):
                    imgs = await self._coletar_fotos(
                        label + ":",
                        multiplas=etapa.get("multiple", False),
                        opcional=etapa.get("optional", False),
                    )
                    self.respostas.append({"label": label, "type": tipo, "imagens": imgs})

                elif tipo == "yesno":
                    valor = await self._ask_yesno(label)
                    self.respostas.append({"label": label, "type": "yesno", "valor": valor})

            await self._finalizar(categoria, subtipo)

        except asyncio.TimeoutError:
            await self.thread.send(
                "⏱️ Tempo esgotado sem resposta. O ticket foi cancelado. "
                "Abra um novo quando quiser.")
        except Exception as e:  # noqa
            traceback.print_exc()
            await self.thread.send(f"❌ Ocorreu um erro ao processar o ticket: `{e}`")

    async def _finalizar(self, categoria, subtipo):
        await self._bot_diz("Registrando o chamado e gerando o relatório... ⏳")

        quando = datetime.now()
        caminho = storage.montar_caminho(categoria, subtipo, self.autor.display_name, quando)
        pasta = storage.pasta_anexos(caminho)
        fotos = [b for r in self.respostas for b in r.get("imagens", [])]
        arquivos = [(f"foto{_extensao(b)}", b) for b in fotos]
        await asyncio.to_thread(storage.salvar_anexos, pasta, arquivos)

        ok = await registrar_chamado(
            self.thread, autor=self.autor, categoria=categoria, subtipo=subtipo,
            quando=quando, caminho=caminho, pasta=pasta, respostas=self.respostas,
            transcricao=self.transcricao, localizacao=self.localizacao,
            n_fotos=len(fotos))
        if ok:
            await asyncio.sleep(2)
            await encerrar_canal(self.thread, self.autor)
