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
