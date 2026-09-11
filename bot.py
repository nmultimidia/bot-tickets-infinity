"""
Ponto de entrada do bot.

- Posta um painel com o botão "Abrir Ticket" (comando /painel ou canal fixo).
- Ao clicar, cria uma thread privada e inicia o fluxo de perguntas.
- Comandos administrativos: /painel, /listar, /fechar, /limpar.
"""
import re
import unicodedata
from datetime import datetime, timedelta

import discord
from discord import app_commands
from discord.ext import commands

import config
import logs as logmod
from ticket import TicketFlow
import storage


def _slug(texto: str) -> str:
    """Remove acentos/espaços, só letras e números, minúsculo (para nome de thread)."""
    texto = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    return re.sub(r"[^A-Za-z0-9]+", "", texto).lower()


async def _convidar_staff(thread: discord.Thread, guild: discord.Guild):
    """Adiciona à thread privada todo mundo com cargo administrativo, para que
    a staff veja o ticket assim que ele é aberto (e não só quando é fechado)."""
    role_ids = set(config.ADMIN_ROLE_IDS)
    if not role_ids:
        return
    for membro in guild.members:
        if membro.bot:
            continue
        if role_ids & {r.id for r in membro.roles}:
            try:
                await thread.add_user(membro)
            except discord.HTTPException:
                pass

intents = discord.Intents.default()
intents.message_content = True  # necessário para ler textos/anexos no fluxo
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)


# --------------------------------------------------------------------------
# Painel com botão persistente
# --------------------------------------------------------------------------
class PainelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)  # persistente

    @discord.ui.button(label="Abrir Ticket", style=discord.ButtonStyle.success,
                       emoji="🎫", custom_id="abrir_ticket")
    async def abrir(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        canal = interaction.channel

        # cria uma thread privada para o ticket. Nome com data/hora para não
        # colidir quando o mesmo técnico abre mais de um ticket (ex:
        # ticket-guilherme180826_1321).
        sufixo = datetime.now().strftime("%d%m%y_%H%M")
        # garante que o sufixo (identificador de data/hora) sempre seja preservado
        # mesmo que o display_name seja muito longo: truncamos o slug para
        # caber dentro do limite de 90 caracteres.
        prefix = "ticket-"
        max_len = 90
        slug = _slug(interaction.user.display_name)
        avail = max_len - len(prefix) - len(sufixo)
        if avail < 1:
            # fallback razoável caso o sufixo já ocupe quase tudo
            nome = (prefix + sufixo)[:max_len]
        else:
            nome = (prefix + slug[:avail] + sufixo)[:max_len]
        try:
            thread = await canal.create_thread(
                name=nome,
                type=discord.ChannelType.private_thread,
                invitable=False,
            )
            await thread.add_user(interaction.user)
            await _convidar_staff(thread, interaction.guild)
        except (discord.HTTPException, AttributeError):
            # fallback: thread pública a partir de uma mensagem
            msg = await canal.send(f"Ticket de {interaction.user.mention}")
            thread = await msg.create_thread(name=nome)

        await interaction.followup.send(
            f"Seu ticket foi criado: {thread.mention}", ephemeral=True)

        embed = discord.Embed(
            title="🎫 Novo ticket criado",
            description=(f"Colaborador: {interaction.user.mention}\n"
                         f"Thread: {thread.mention}"),
            color=0x3498db,
        )
        embed.timestamp = discord.utils.utcnow()
        canal_log = await logmod.enviar(interaction.guild, "ticket", embed)
        # também notifica via menção aos cargos administrativos (se definidos),
        # assim a staff recebe alerta mesmo que por algum motivo não tenha sido
        # adicionada à thread privada imediatamente.
        if canal_log and config.ADMIN_ROLE_IDS:
            mentions = []
            for rid in config.ADMIN_ROLE_IDS:
                role = interaction.guild.get_role(rid)
                if role:
                    mentions.append(role.mention)
            if mentions:
                try:
                    await canal_log.send(" ".join(mentions))
                except Exception:
                    pass

        flow = TicketFlow(bot, thread, interaction.user)
        bot.loop.create_task(flow.run())


# --------------------------------------------------------------------------
# Permissão de admin
# --------------------------------------------------------------------------
def eh_admin(interaction: discord.Interaction) -> bool:
    perms = getattr(interaction.user, "guild_permissions", None)
    if perms and perms.administrator:
        return True
    ids = {r.id for r in getattr(interaction.user, "roles", [])}
    return bool(ids & set(config.ADMIN_ROLE_IDS))


# --------------------------------------------------------------------------
# Comandos
# --------------------------------------------------------------------------
@bot.tree.command(description="Publica o painel com o botão de abrir ticket.")
async def painel(interaction: discord.Interaction):
    if not eh_admin(interaction):
        await interaction.response.send_message("Sem permissão.", ephemeral=True)
        return
    embed = discord.Embed(
        title="Abertura de Chamados",
        description="Clique no botão abaixo para abrir um ticket e registrar seu atendimento.",
        color=0x2ecc71,
    )
    await interaction.channel.send(embed=embed, view=PainelView())
    await interaction.response.send_message("Painel publicado.", ephemeral=True)


@bot.tree.command(description="Lista chamados salvos (filtro opcional por categoria/mês).")
@app_commands.describe(categoria="Ex: Abertura de OS - UFMT", mes="Ex: Julho 2026")
async def listar(interaction: discord.Interaction, categoria: str = None, mes: str = None):
    if not eh_admin(interaction):
        await interaction.response.send_message("Sem permissão.", ephemeral=True)
        return
    arquivos = storage.listar_chamados(categoria, mes)
    if not arquivos:
        await interaction.response.send_message("Nenhum chamado encontrado.", ephemeral=True)
        return
    import os
    linhas = [f"• `{os.path.relpath(a, config.STORAGE_ROOT)}`" for a in arquivos[:25]]
    extra = f"\n… e mais {len(arquivos) - 25}." if len(arquivos) > 25 else ""
    await interaction.response.send_message(
        f"**{len(arquivos)} chamado(s):**\n" + "\n".join(linhas) + extra, ephemeral=True)


@bot.tree.command(description="Fecha (arquiva) o ticket atual.")
async def fechar(interaction: discord.Interaction):
    if not isinstance(interaction.channel, discord.Thread):
        await interaction.response.send_message(
            "Use este comando dentro de um ticket.", ephemeral=True)
        return
    await interaction.response.send_message("Fechando o ticket...", ephemeral=True)
    await interaction.channel.edit(archived=True, locked=True)


def _eh_thread_ticket(thread: discord.Thread) -> bool:
    return thread.name.startswith("ticket-")


async def _threads_arquivadas_de(canal: discord.TextChannel):
    """Junta as threads privadas e públicas arquivadas de um canal."""
    try:
        async for thread in canal.archived_threads(private=True, limit=None):
            yield thread
    except discord.Forbidden:
        pass
    async for thread in canal.archived_threads(private=False, limit=None):
        yield thread


class ConfirmarLimpeza(discord.ui.View):
    """Confirmação obrigatória: apagar a thread é irreversível."""

    def __init__(self, threads, autor_id: int):
        super().__init__(timeout=60)
        self.threads = threads
        self.autor_id = autor_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.autor_id:
            await interaction.response.send_message(
                "Só quem executou o comando pode confirmar.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Apagar", emoji="🗑️",
                       style=discord.ButtonStyle.danger)
    async def confirmar(self, interaction: discord.Interaction, _b):
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(
            content=f"Apagando {len(self.threads)} ticket(s) arquivado(s)...",
            view=self)

        apagados, erros = 0, 0
        for thread in self.threads:
            try:
                await thread.delete()
                apagados += 1
            except discord.HTTPException:
                erros += 1

        texto = f"✅ {apagados} ticket(s) arquivado(s) apagado(s)."
        if erros:
            texto += f" {erros} não pude apagar (verifique minhas permissões)."
        await interaction.edit_original_response(content=texto, view=None)
        self.stop()

    @discord.ui.button(label="Cancelar", style=discord.ButtonStyle.secondary)
    async def cancelar(self, interaction: discord.Interaction, _b):
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(
            content="Cancelado. Nenhum ticket foi apagado.", view=self)
        self.stop()


@bot.tree.command(
    description="Apaga os tickets já arquivados (fechados). Pede confirmação.")
@app_commands.describe(
    dias="Apagar só os arquivados há mais de X dias (0 = todos)")
async def limpar(interaction: discord.Interaction, dias: int = 0):
    if not eh_admin(interaction):
        await interaction.response.send_message("Sem permissão.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)

    limite = None
    if dias > 0:
        limite = discord.utils.utcnow() - timedelta(days=dias)

    alvos = []
    for canal in interaction.guild.text_channels:
        async for thread in _threads_arquivadas_de(canal):
            if not _eh_thread_ticket(thread):
                continue
            if limite and thread.archive_timestamp and thread.archive_timestamp > limite:
                continue
            alvos.append(thread)

    if not alvos:
        recorte = f" com mais de {dias} dia(s)" if dias else ""
        await interaction.followup.send(
            f"Nenhum ticket arquivado{recorte} para apagar.", ephemeral=True)
        return

    amostra = "\n".join(f"• {t.name}" for t in alvos[:10])
    if len(alvos) > 10:
        amostra += f"\n• ... e mais {len(alvos) - 10}"
    recorte = f" sem movimento há mais de {dias} dia(s)" if dias else ""

    await interaction.followup.send(
        f"⚠️ Vou apagar **{len(alvos)} ticket(s) arquivado(s)**{recorte}.\n"
        "As threads e todo o histórico da conversa serão **apagados de vez** "
        "(os PDFs já salvos continuam intactos).\n\n"
        f"{amostra}\n\nConfirma?",
        view=ConfirmarLimpeza(alvos, interaction.user.id),
        ephemeral=True)


# --------------------------------------------------------------------------
# Configuração dos canais de log (/definir_log, /ver_logs)
# --------------------------------------------------------------------------
_TIPO_CHOICES = [
    app_commands.Choice(name=rotulo, value=tipo)
    for tipo, (rotulo, _slug) in logmod.LOG_TIPOS.items()
]


@bot.tree.command(description="Define ESTE canal como destino de um tipo de log.")
@app_commands.describe(tipo="Qual tipo de log deve cair neste canal")
@app_commands.choices(tipo=_TIPO_CHOICES)
async def definir_log(interaction: discord.Interaction, tipo: app_commands.Choice[str]):
    if not eh_admin(interaction):
        await interaction.response.send_message("Sem permissão.", ephemeral=True)
        return
    logmod.definir(interaction.guild_id, tipo.value, interaction.channel_id)
    await interaction.response.send_message(
        f"✅ Logs de **{tipo.name}** agora vão para {interaction.channel.mention}.",
        ephemeral=True)


@bot.tree.command(description="Mostra qual canal recebe cada tipo de log.")
async def ver_logs(interaction: discord.Interaction):
    if not eh_admin(interaction):
        await interaction.response.send_message("Sem permissão.", ephemeral=True)
        return
    linhas = logmod.configuracao_atual(interaction.guild)
    await interaction.response.send_message(
        "\n".join(f"• **{r}**: {c}" for r, c in linhas), ephemeral=True)


# --------------------------------------------------------------------------
# Logs de mensagens e membros (opcional — MSG_LOGS_ENABLED=true)
# --------------------------------------------------------------------------
def _resumo(texto: str, limite: int = 1000) -> str:
    texto = texto or "*(sem texto — possivelmente apenas anexo/embed)*"
    return texto if len(texto) <= limite else texto[:limite] + "…"


def _eh_canal_de_log(channel) -> bool:
    """Evita registrar eventos ocorridos nos próprios canais de log."""
    return any(logmod.canal_de_log(channel.guild, t) == channel
               for t in logmod.LOG_TIPOS)


@bot.event
async def on_message_delete(message: discord.Message):
    if not config.MSG_LOGS_ENABLED or message.author.bot or message.guild is None:
        return
    if _eh_canal_de_log(message.channel):
        return
    embed = discord.Embed(
        title="🗑️ Mensagem apagada",
        description=(f"Autor: {message.author.mention}\n"
                     f"Canal: {message.channel.mention}\n\n"
                     f"**Conteúdo:**\n{_resumo(message.content)}"),
        color=0xe74c3c,
    )
    if message.attachments:
        embed.add_field(
            name="Anexos",
            value="\n".join(a.filename for a in message.attachments[:10]))
    embed.timestamp = discord.utils.utcnow()
    await logmod.enviar(message.guild, "msg_deletada", embed)


@bot.event
async def on_message_edit(antes: discord.Message, depois: discord.Message):
    if not config.MSG_LOGS_ENABLED or antes.author.bot or antes.guild is None:
        return
    if antes.content == depois.content:
        return  # edições de embed/pin, sem mudança de texto
    if _eh_canal_de_log(antes.channel):
        return
    embed = discord.Embed(
        title="✏️ Mensagem editada",
        description=(f"Autor: {antes.author.mention}\n"
                     f"Canal: {antes.channel.mention} — "
                     f"[ir para a mensagem]({depois.jump_url})\n\n"
                     f"**Antes:**\n{_resumo(antes.content, 500)}\n\n"
                     f"**Depois:**\n{_resumo(depois.content, 500)}"),
        color=0xf39c12,
    )
    embed.timestamp = discord.utils.utcnow()
    await logmod.enviar(antes.guild, "msg_editada", embed)


@bot.event
async def on_member_join(member: discord.Member):
    if not config.MSG_LOGS_ENABLED:
        return
    embed = discord.Embed(
        title="📥 Membro entrou",
        description=(f"{member.mention} (`{member}`)\n"
                     f"Conta criada em: "
                     f"{member.created_at.strftime('%d/%m/%Y %H:%M')}"),
        color=0x2ecc71,
    )
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.timestamp = discord.utils.utcnow()
    await logmod.enviar(member.guild, "entrada", embed)


@bot.event
async def on_member_remove(member: discord.Member):
    if not config.MSG_LOGS_ENABLED:
        return
    embed = discord.Embed(
        title="📤 Membro saiu",
        description=f"{member.mention} (`{member}`)",
        color=0x95a5a6,
    )
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.timestamp = discord.utils.utcnow()
    await logmod.enviar(member.guild, "saida", embed)


# --------------------------------------------------------------------------
# Ciclo de vida
# --------------------------------------------------------------------------
@bot.event
async def setup_hook():
    bot.add_view(PainelView())     # reativa o botão após reinício
    await bot.tree.sync()


@bot.event
async def on_ready():
    print(f"Bot online como {bot.user} (id: {bot.user.id})")
    if config.PANEL_CHANNEL_ID:
        canal = bot.get_channel(config.PANEL_CHANNEL_ID)
        if canal:
            embed = discord.Embed(
                title="Abertura de Chamados",
                description="Clique no botão abaixo para abrir um ticket.",
                color=0x2ecc71,
            )
            await canal.send(embed=embed, view=PainelView())


if __name__ == "__main__":
    if not config.DISCORD_TOKEN:
        raise SystemExit("Defina DISCORD_TOKEN no arquivo .env")
    bot.run(config.DISCORD_TOKEN)
