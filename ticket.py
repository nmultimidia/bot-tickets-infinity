"""
Motor do ticket. Conduz o colaborador pelo fluxo de perguntas do fluxograma,
coleta textos e fotos, gera o PDF e salva na estrutura de pastas.
"""
import asyncio
from pathlib import Path
from datetime import datetime

import discord

import flow
import storage
import config
import logs
from config import STEP_TIMEOUT
from pdf_generator import gerar_pdf, extrair_localizacao

# Palavras que o colaborador digita para encerrar o envio de várias fotos
PALAVRAS_FIM = {"pronto", "fim", "ok", "concluir", "finalizar"}


def _anexo_imagem(anexo):
    tipo = (anexo.content_type or "").lower()
    return tipo.startswith("image/") or Path(anexo.filename).suffix.lower() in {
        ".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tif", ".tiff",
    }


class SelectView(discord.ui.View):
    """Menu suspenso genérico que resolve um Future com a opção escolhida."""

    def __init__(self, opcoes, autor_id, placeholder="Selecione..."):
        super().__init__(timeout=STEP_TIMEOUT)
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

    @discord.ui.button(label="Sim", style=discord.ButtonStyle.success)
    async def sim(self, interaction: discord.Interaction, _):
        await self._responder(interaction, "Sim")

    @discord.ui.button(label="Não", style=discord.ButtonStyle.danger)
    async def nao(self, interaction: discord.Interaction, _):
        await self._responder(interaction, "Não")


def _pode_fechar(member: discord.Member) -> bool:
    """Só administração fecha o ticket livre: Administrator, Gerenciar
    Tópicos, ou um dos cargos em ADMIN_ROLE_IDS."""
    perms = getattr(member, "guild_permissions", None)
    if perms and (perms.administrator or perms.manage_threads):
        return True
    ids = {r.id for r in getattr(member, "roles", [])}
    return bool(ids & set(config.ADMIN_ROLE_IDS))


class FecharChamadoView(discord.ui.View):
    """Botão exclusivo da administração: gera o PDF/relatório e arquiva."""

    def __init__(self, ticket_flow, categoria, subtipo):
        super().__init__(timeout=None)
        self.ticket_flow = ticket_flow
        self.categoria = categoria
        self.subtipo = subtipo

    @discord.ui.button(label="Fechar e gerar relatório", style=discord.ButtonStyle.success,
                       emoji="📋", custom_id="fechar_chamado_livre")
    async def fechar(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not _pode_fechar(interaction.user):
            await interaction.response.send_message(
                "Apenas a administração pode fechar este chamado.", ephemeral=True)
            return
        button.disabled = True
        await interaction.response.edit_message(view=self)
        await interaction.followup.send("Gerando relatório... ⏳")
        await self.ticket_flow._finalizar(self.categoria, self.subtipo)


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
            conteudo = msg.content.strip().lower()

            if opcional and conteudo in {"pular", "nao", "não"} and not msg.attachments:
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
                if not multiplas:
                    break
                await self._bot_diz("Foto recebida. Envie mais ou digite **pronto**.")
                continue

            if multiplas and conteudo in PALAVRAS_FIM:
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
        """Ticket sem perguntas: o técnico envia texto/fotos à vontade e
        escreve 'pronto' para travar. Só a administração fecha e gera o PDF."""
        try:
            categoria = await self._ask_select(
                "Qual o tipo de assunto?", flow.categorias())
            subtipo = await self._ask_select(
                "Qual o tipo de serviço?", flow.subtipos(categoria))

            await self._bot_diz(
                f"Pode enviar fotos, textos e informações à vontade, {self.autor.mention}. "
                f"Quando finalizar o atendimento, escreva **pronto**.")

            textos = []
            fotos = []
            while True:
                msg = await self._aguardar_msg()
                conteudo = (msg.content or "").strip()

                if msg.attachments:
                    for att in msg.attachments:
                        if _anexo_imagem(att):
                            fotos.append(await att.read())
                    self.transcricao.append(
                        (self.autor.display_name,
                         conteudo or f"[{len(msg.attachments)} anexo(s)]"))
                    if conteudo:
                        textos.append(conteudo)
                    continue

                if conteudo.lower() == "pronto":
                    self.transcricao.append((self.autor.display_name, conteudo))
                    break

                if conteudo:
                    textos.append(conteudo)
                    self.transcricao.append((self.autor.display_name, conteudo))

            for b in fotos:
                if self.localizacao is None:
                    self.localizacao = extrair_localizacao(b)

            self.respostas = [
                {"label": "Registro do atendimento", "type": "text",
                 "valor": "\n".join(textos) if textos else "—"},
                {"label": "Fotos enviadas", "type": "photo", "imagens": fotos},
            ]

            # Trava a thread: quem não tem "Gerenciar Tópicos" não consegue
            # mais escrever. A administração continua conseguindo agir.
            try:
                await self.thread.edit(locked=True)
            except discord.HTTPException:
                pass

            await self._bot_diz(
                "🔒 Atendimento marcado como concluído e travado. "
                "Um administrador vai revisar e fechar o chamado.")

            embed = discord.Embed(
                title="🔒 Ticket pronto para fechamento",
                description=(f"Colaborador: {self.autor.mention}\n"
                             f"Categoria: **{categoria}** • Tipo: **{subtipo}**\n"
                             f"Thread: {self.thread.mention}"),
                color=0xf1c40f,
            )
            embed.timestamp = discord.utils.utcnow()
            await logs.enviar(self.thread.guild, "ticket", embed)

            view = FecharChamadoView(self, categoria, subtipo)
            await self.thread.send(
                "Administração: clique abaixo para gerar o relatório e arquivar.",
                view=view)

        except asyncio.TimeoutError:
            await self.thread.send(
                "⏱️ Tempo esgotado sem resposta. O ticket foi cancelado. "
                "Abra um novo quando quiser.")
        except Exception as e:  # noqa
            await self.thread.send(f"❌ Ocorreu um erro ao processar o ticket: `{e}`")

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
            await self.thread.send(f"❌ Ocorreu um erro ao processar o ticket: `{e}`")

    async def _finalizar(self, categoria, subtipo):
        await self._bot_diz("Registrando o chamado e gerando o relatório... ⏳")

        quando = datetime.now()
        meta = {
            "colaborador": self.autor.display_name,
            "data_hora": quando,
            "categoria": categoria,
            "subtipo": subtipo,
            "localizacao": self.localizacao,
        }

        caminho = storage.montar_caminho(categoria, subtipo, self.autor.display_name, quando)
        gerar_pdf(caminho, meta=meta, respostas=self.respostas, transcricao=self.transcricao)

        # Envia também para o Google Drive (se habilitado). Uma falha aqui NÃO
        # cancela o chamado — o PDF já está salvo localmente.
        link_drive = None
        try:
            import gdrive
            comps = storage.componentes_pasta(categoria, subtipo, quando)
            link_drive = gdrive.upload_pdf(caminho, comps)
        except Exception as e:  # noqa
            print("Falha ao enviar para o Google Drive:", e)

        import os
        nome = os.path.basename(caminho)
        linha_drive = f"\n☁️ Google Drive: {link_drive}" if link_drive else ""
        await self.thread.send(
            f"✅ **Chamado finalizado!**\n"
            f"Colaborador: **{self.autor.display_name}**\n"
            f"Categoria: **{categoria}** • Tipo: **{subtipo}**\n"
            f"Data/Hora: **{quando.strftime('%d/%m/%Y %H:%M')}**\n"
            f"Localização: **{self.localizacao or 'não disponível'}**\n"
            f"Arquivo: `{nome}`{linha_drive}",
            file=discord.File(caminho),
        )
        # Aviso no canal de log de tickets (definido via /definir_log)
        embed = discord.Embed(
            title="✅ Ticket finalizado",
            description=(f"Colaborador: {self.autor.mention}\n"
                         f"Categoria: **{categoria}** • Tipo: **{subtipo}**\n"
                         f"Data/Hora: **{quando.strftime('%d/%m/%Y %H:%M')}**\n"
                         f"Arquivo: `{nome}`"),
            color=0x2ecc71,
        )
        if link_drive:
            embed.add_field(
                name="☁️ Google Drive",
                value=f"[Abrir PDF no Google Drive]({link_drive})",
                inline=False,
            )
        embed.timestamp = discord.utils.utcnow()
        await logs.enviar(self.thread.guild, "ticket", embed)

        # Fecha a thread após alguns segundos
        await asyncio.sleep(2)
        try:
            await self.thread.edit(archived=True, locked=True)
        except discord.HTTPException:
            pass
