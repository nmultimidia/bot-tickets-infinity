from datetime import datetime

from bot import _nome_ticket, _thread_kwargs


def test_thread_usa_arquivamento_maximo_para_nao_sumir_da_aba_ativa():
    kwargs = _thread_kwargs()

    assert kwargs["auto_archive_duration"] == 10080
    assert kwargs["invitable"] is False


def test_nome_ticket_inclui_tecnico_data_e_hora():
    agora = datetime(2026, 8, 18, 13, 21)

    assert _nome_ticket("Guilherme", agora=agora) == "ticket-guilherme180826_1321"


def test_nome_ticket_adiciona_contador_em_colisao_no_mesmo_minuto():
    agora = datetime(2026, 8, 18, 13, 21)
    usados = {
        "ticket-guilherme180826_1321",
        "ticket-guilherme180826_1321-2",
    }

    assert _nome_ticket("Guilherme", usados, agora) == "ticket-guilherme180826_1321-3"


def test_nome_ticket_limita_tamanho_e_preserva_data_hora():
    agora = datetime(2026, 8, 18, 13, 21)

    nome = _nome_ticket("T" * 200, agora=agora)

    assert len(nome) == 90
    assert nome.endswith("180826_1321")


def _canal_texto(nome, topico=None):
    import discord
    canal = object.__new__(discord.TextChannel)
    canal.name = nome
    canal.topic = topico
    return canal


def test_ticket_renomeado_continua_reconhecido_pelo_topico():
    from bot import _eh_ticket_canal

    assert _eh_ticket_canal(_canal_texto("ticket-guilherme180826_1321"))
    assert _eh_ticket_canal(_canal_texto("suporte-cliente", "autor=123 | categoria=OS"))
    assert not _eh_ticket_canal(_canal_texto("geral", "Canal de conversa"))


class _Membro:
    def __init__(self, id, name, display_name, bot=False):
        self.id, self.name, self.display_name, self.bot = id, name, display_name, bot


class _Guild:
    def __init__(self, membros):
        self.members = membros

    def get_member(self, id):
        return next((m for m in self.members if m.id == id), None)


def test_autocomplete_busca_no_servidor_todo_e_ignora_bots():
    from bot import _sugestoes_de_membros

    guild = _Guild([_Membro(1, "ana", "Ana Paula"), _Membro(2, "joao", "joao"),
                    _Membro(3, "suporte", "Suporte Fibra", bot=True)])
    nomes = [c.name for c in _sugestoes_de_membros(guild, "")]

    assert nomes == ["Ana Paula (@ana)", "joao"]
    assert [c.value for c in _sugestoes_de_membros(guild, "ana")] == ["1"]


def test_resolver_membro_aceita_id_mencao_ou_nome():
    from bot import _resolver_membro

    ana = _Membro(1, "ana", "Ana Paula")
    guild = _Guild([ana])

    assert _resolver_membro(guild, "1") is ana
    assert _resolver_membro(guild, "<@1>") is ana
    assert _resolver_membro(guild, "@Ana Paula") is ana
    assert _resolver_membro(guild, "ninguem") is None


def test_finalizar_ticket_funciona_em_canal_renomeado():
    from ticket import _eh_canal_ticket

    assert _eh_canal_ticket(_canal_texto("obra-hospital-central", "autor=123 | categoria=OS"))
    assert not _eh_canal_ticket(_canal_texto("obra-hospital-central"))


def test_ticket_antigo_renomeado_sem_topico_e_reconhecido_pela_permissao():
    import discord
    from ticket import _eh_canal_ticket

    autor = object.__new__(discord.Member)
    autor._user = type("U", (), {"bot": False})()

    class CanalAntigo(discord.TextChannel):
        overwrites = {autor: discord.PermissionOverwrite(view_channel=True,
                                                         manage_channels=False)}

    canal = object.__new__(CanalAntigo)
    canal.name, canal.topic = "obra-hospital-central", None
    assert _eh_canal_ticket(canal)

    class CanalComum(discord.TextChannel):
        overwrites = {autor: discord.PermissionOverwrite(view_channel=True)}

    comum = object.__new__(CanalComum)
    comum.name, comum.topic = "geral", None
    assert not _eh_canal_ticket(comum)
