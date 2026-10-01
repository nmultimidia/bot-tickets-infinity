import io
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from PIL import Image

import config
import gdrive
import logs
import storage
import ticket
from pdf_generator import gerar_pdf


def foto():
    buf = io.BytesIO()
    Image.new('RGB', (40, 80), 'blue').save(buf, 'JPEG')
    return buf.getvalue()


class Anexo:
    def __init__(self, filename, dados, content_type=None, falhas=0):
        self.filename = filename
        self.content_type = content_type
        self.dados = dados
        self.falhas = falhas

    async def save(self, destino, use_cached=False):
        if self.falhas:
            self.falhas -= 1
            raise OSError('CDN indisponível')
        Path(destino).write_bytes(self.dados)


def pessoa(pid, nome, bot=False):
    return SimpleNamespace(id=pid, display_name=nome, bot=bot, mention=f'<@{pid}>')


def mensagem(autor, texto='', anexos=()):
    return SimpleNamespace(author=autor, content=texto, attachments=list(anexos),
                           created_at=datetime(2026, 9, 29, 22, 0, tzinfo=timezone.utc))


class CanalFalso:
    def __init__(self, mensagens, topic=None, membros=()):
        self.id = 10
        self.name = 'ticket-guilherme290926_2100'
        self.mention = '#ticket'
        self.topic = topic
        self.overwrites = {}
        self.created_at = datetime(2026, 9, 29, 21, 0, tzinfo=timezone.utc)
        self.mensagens = mensagens
        self.send = AsyncMock()
        self.edit = AsyncMock()
        membros = {m.id: m for m in membros}
        self.guild = SimpleNamespace(get_member=membros.get, filesize_limit=8 * 1024 * 1024)

    async def history(self, limit=None, oldest_first=False):
        for m in self.mensagens:
            yield m


class PedidoDeFimTests(unittest.TestCase):
    def test_aceita_variacoes_de_pronto(self):
        for texto in ('pronto', 'Pronto.', 'PRONTO!!', ' pronto ', 'tá pronto',
                      'pronto ✅', 'Finalizado', 'concluído'):
            self.assertTrue(ticket.eh_pedido_de_fim(texto), texto)

    def test_ignora_frases_negacoes_e_ok(self):
        for texto in ('', 'ok', 'ainda não está pronto', 'não pronto',
                      'pronto o cabo foi trocado no rack'):
            self.assertFalse(ticket.eh_pedido_de_fim(texto), texto)

    def test_ok_vale_no_fluxo_com_perguntas(self):
        self.assertTrue(ticket.eh_pedido_de_fim('ok', ticket.PALAVRAS_FIM))


class FinalizarTicketTests(unittest.TestCase):
    def test_aceita_a_frase_em_qualquer_caixa(self):
        for texto in ('FINALIZAR TICKET', 'finalizar ticket', ' Finalizar ticket! ',
                      'finalizar  ticket.'):
            self.assertTrue(ticket.eh_finalizar_ticket(texto), texto)

    def test_pronto_e_outras_frases_nao_travam(self):
        for texto in ('', 'pronto', 'finalizar', 'ticket', 'finalizado',
                      'não finalizar ticket', 'finalizar ticket amanhã'):
            self.assertFalse(ticket.eh_finalizar_ticket(texto), texto)


class TratarMensagemTests(unittest.IsolatedAsyncioTestCase):
    async def _tratar(self, texto):
        canal = SimpleNamespace(name='ticket-x', id=1)
        msg = SimpleNamespace(author=pessoa(2, 'Tec'), channel=canal, content=texto)
        antigos = ticket.marcar_pronto, ticket._eh_canal_ticket, config.FLUXO_TICKET_LIVRE
        ticket.marcar_pronto = AsyncMock()
        ticket._eh_canal_ticket = lambda c: True
        config.FLUXO_TICKET_LIVRE = True
        try:
            await ticket.tratar_mensagem(None, msg)
            return ticket.marcar_pronto
        finally:
            ticket.marcar_pronto, ticket._eh_canal_ticket, config.FLUXO_TICKET_LIVRE = antigos

    async def test_so_finalizar_ticket_trava(self):
        (await self._tratar('FINALIZAR TICKET')).assert_awaited_once()
        (await self._tratar('pronto')).assert_not_awaited()


class TopicoTests(unittest.TestCase):
    def test_ida_e_volta(self):
        topico = ticket.montar_topico(autor=2, categoria='Abertura de OS - UFMT',
                                      tipo='OS Instalação')
        canal = SimpleNamespace(topic=topico)
        self.assertEqual(ticket.ler_topico(canal), {
            'autor': '2', 'categoria': 'Abertura de OS - UFMT', 'tipo': 'OS Instalação'})

    def test_sem_topico(self):
        self.assertEqual(ticket.ler_topico(SimpleNamespace(topic=None)), {})


class StorageTests(unittest.TestCase):
    def test_nao_sobrescreve_relatorio_existente(self):
        with tempfile.TemporaryDirectory() as raiz:
            antigo = storage.STORAGE_ROOT
            storage.STORAGE_ROOT = raiz
            try:
                quando = datetime(2026, 9, 29, 21, 0)
                primeiro = storage.montar_caminho('Cat', 'Tipo', 'Joao', quando)
                Path(primeiro).write_bytes(b'pdf')
                segundo = storage.montar_caminho('Cat', 'Tipo', 'Joao', quando)
            finally:
                storage.STORAGE_ROOT = antigo
            self.assertNotEqual(primeiro, segundo)
            self.assertTrue(segundo.endswith('_2.pdf'))

    def test_sem_tipo_de_servico(self):
        quando = datetime(2026, 9, 29, 21, 5)
        self.assertEqual(storage.componentes_pasta('Abertura de OS - GCT', None, quando),
                         ['Abertura de OS - GCT', 'Setembro 2026', '29'])
        with tempfile.TemporaryDirectory() as raiz:
            antigo = storage.STORAGE_ROOT
            storage.STORAGE_ROOT = raiz
            try:
                caminho = storage.montar_caminho('Abertura de OS - GCT', None, 'João', quando)
            finally:
                storage.STORAGE_ROOT = antigo
        self.assertEqual(Path(caminho).name, '29_2105_Joao.pdf')

    def test_nome_anexo_seguro(self):
        self.assertEqual(storage.nome_anexo(3, '../a:b?.jpg'), '003_a_b_.jpg')


class PdfTests(unittest.TestCase):
    def test_texto_com_simbolos_nao_quebra_pdf(self):
        with tempfile.TemporaryDirectory() as pasta:
            caminho = os.path.join(pasta, 'r.pdf')
            gerar_pdf(caminho, meta=dict(colaborador='A & B', data_hora=datetime.now(),
                                         categoria='C<1>', subtipo='T'),
                      respostas=[dict(label='x < y', type='text', valor='potência < -20dBm & ok')],
                      transcricao=[('Tec <x>', 'a < b\nc & d')])
            self.assertTrue(os.path.getsize(caminho) > 0)


class HistoricoTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tecnico = pessoa(2, 'Tecnico')
        self.colega = pessoa(3, 'Colega')
        self.bot_user = pessoa(99, 'Bot', bot=True)

    async def test_coleta_fotos_de_todos_inclusive_com_pronto(self):
        canal = CanalFalso([
            mensagem(self.bot_user, 'Olá!'),
            mensagem(self.tecnico, 'cabo trocado', [Anexo('a.jpg', foto(), 'image/jpeg')]),
            mensagem(self.colega, '', [Anexo('b.png', foto()), Anexo('laudo.pdf', b'%PDF')]),
            mensagem(self.tecnico, 'Pronto.', [Anexo('c.jpg', foto(), falhas=1)]),
        ])
        with tempfile.TemporaryDirectory() as pasta:
            h = await ticket.coletar_historico(canal, self.bot_user, 2, pasta)
            salvos = sorted(os.listdir(pasta))
        self.assertEqual(len(h.imagens), 3)
        self.assertEqual(h.outros, ['laudo.pdf'])
        self.assertEqual(h.textos, ['cabo trocado'])
        self.assertEqual(h.falhas, [])
        self.assertEqual(salvos, ['001_a.jpg', '002_b.png', '003_laudo.pdf', '004_c.jpg'])
        self.assertEqual(h.transcricao[0], ('Bot', 'Olá!'))

    async def test_registra_falha_de_download(self):
        canal = CanalFalso([mensagem(self.tecnico, '', [Anexo('a.jpg', foto(), falhas=5)])])
        ticket._salvar_anexo.__defaults__ = (1,)   # sem esperar retries
        try:
            with tempfile.TemporaryDirectory() as pasta:
                h = await ticket.coletar_historico(canal, self.bot_user, 2, pasta)
        finally:
            ticket._salvar_anexo.__defaults__ = (3,)
        self.assertEqual(len(h.falhas), 1)
        self.assertIn('a.jpg', h.falhas[0])

    async def test_finaliza_a_partir_do_historico(self):
        canal = CanalFalso(
            [mensagem(self.tecnico, 'material: 10m cabo <cat6>', [Anexo('a.jpg', foto())]),
             mensagem(self.tecnico, 'FINALIZAR TICKET')],
            topic=ticket.montar_topico(autor=2, categoria='Abertura de OS - GCT'),
            membros=[self.tecnico])
        bot = SimpleNamespace(user=self.bot_user)
        envio_log = AsyncMock()
        with tempfile.TemporaryDirectory() as raiz:
            antigos = storage.STORAGE_ROOT, config.STORAGE_ROOT, logs.enviar, gdrive.GDRIVE_ENABLED
            storage.STORAGE_ROOT = config.STORAGE_ROOT = raiz
            logs.enviar = envio_log
            gdrive.GDRIVE_ENABLED = False
            try:
                ok = await ticket.finalizar_ticket_livre(bot, canal)
            finally:
                storage.STORAGE_ROOT, config.STORAGE_ROOT, logs.enviar, gdrive.GDRIVE_ENABLED = antigos
            pdfs = list(Path(raiz).rglob('*.pdf'))
            originais = list(Path(raiz).rglob('*_arquivos/*'))
            self.assertTrue(ok)
            self.assertEqual(len(pdfs), 1)
            self.assertIn(b'/Subtype /Image', pdfs[0].read_bytes())
            self.assertEqual([p.name for p in originais], ['001_a.jpg'])
            self.assertEqual(pdfs[0].relative_to(raiz).parts[:2],
                             ('Abertura de OS - GCT', 'Setembro 2026'))
            for c in canal.send.await_args_list:   # o send real fecha o arquivo
                if 'file' in c.kwargs:
                    c.kwargs['file'].close()
        textos = ' '.join(str(c.args[0]) for c in canal.send.await_args_list if c.args)
        self.assertIn('Chamado registrado!', textos)
        envio_log.assert_awaited()

    async def test_sem_categoria_pede_comando(self):
        canal = CanalFalso([mensagem(self.tecnico, 'pronto')])
        ok = await ticket.finalizar_ticket_livre(SimpleNamespace(user=self.bot_user), canal)
        self.assertFalse(ok)
        self.assertIn('/gerar_relatorio', canal.send.await_args.args[0])


class ExplicarErroDriveTests(unittest.TestCase):
    def test_quota_de_conta_de_servico(self):
        msg = gdrive.explicar_erro(Exception('Service Accounts do not have storage quota.'))
        self.assertIn('GDRIVE_AUTH=oauth', msg)

    def test_token_expirado(self):
        msg = gdrive.explicar_erro(Exception("('invalid_grant: Token has been expired or revoked.')"))
        self.assertIn('authorize_drive.py', msg)
