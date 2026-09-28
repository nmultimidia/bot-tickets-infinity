import asyncio
import io
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from PIL import Image
from ticket import TicketFlow, _anexo_imagem
from pdf_generator import gerar_pdf


def foto():
    buf = io.BytesIO()
    Image.new('RGB', (40, 80), 'red').save(buf, 'JPEG')
    return buf.getvalue()


class FotosTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.flow = TicketFlow(Mock(), SimpleNamespace(id=1, send=AsyncMock()),
                               SimpleNamespace(id=2, display_name='Tecnico'))

    def mensagem(self, anexos=(), texto=''):
        return SimpleNamespace(author=SimpleNamespace(id=2), channel=SimpleNamespace(id=1),
                               attachments=anexos, content=texto)

    async def test_fotos_enviadas_durante_download_entram_no_pdf(self):
        dados = foto()
        segunda = SimpleNamespace(filename='segunda.JPG', content_type=None, read=AsyncMock(return_value=dados))

        async def baixar():
            await self.flow._receber_msg(self.mensagem([segunda]))
            await self.flow._receber_msg(self.mensagem(texto='pronto'))
            return dados

        primeira = SimpleNamespace(filename='primeira.jpg', content_type='application/octet-stream', read=baixar)
        await self.flow._receber_msg(self.mensagem([primeira]))
        imagens = await asyncio.wait_for(self.flow._coletar_fotos('Fotos', True), 2)
        self.assertEqual(len(imagens), 2)
        segunda.read.assert_awaited_once()
        with tempfile.TemporaryDirectory() as pasta:
            caminho = Path(pasta) / 'os.pdf'
            gerar_pdf(str(caminho), meta=dict(colaborador='Tecnico', data_hora=datetime.now(),
                      categoria='UFMT', subtipo='OS'),
                      respostas=[dict(label='Fotos', type='photo', imagens=imagens)], transcricao=[])
            self.assertIn(b'/Subtype /Image', caminho.read_bytes())

    async def test_arquivo_nao_avanca_etapa_de_foto(self):
        arquivo = SimpleNamespace(filename='arquivo.pdf', content_type='application/pdf')
        imagem = SimpleNamespace(filename='foto.png', content_type=None, read=AsyncMock(return_value=foto()))
        await self.flow._receber_msg(self.mensagem([arquivo]))
        await self.flow._receber_msg(self.mensagem([imagem]))
        imagens = await asyncio.wait_for(self.flow._coletar_fotos('Foto', False), 2)
        self.assertEqual(len(imagens), 1)

    async def test_ignora_outro_autor_e_outro_canal(self):
        msg = self.mensagem()
        msg.author.id = 3
        await self.flow._receber_msg(msg)
        msg.author.id = 2
        msg.channel.id = 4
        await self.flow._receber_msg(msg)
        self.assertTrue(self.flow._mensagens.empty())

    async def test_remove_listener_em_falha(self):
        self.flow._run_livre = AsyncMock(side_effect=RuntimeError('erro'))
        self.flow._run_com_perguntas = AsyncMock(side_effect=RuntimeError('erro'))
        with self.assertRaises(RuntimeError):
            await self.flow.run()
        self.flow.bot.add_listener.assert_called_once_with(self.flow._receber_msg, 'on_message')
        self.flow.bot.remove_listener.assert_called_once_with(self.flow._receber_msg, 'on_message')

    def test_identifica_fotos(self):
        for tipo in (None, 'application/octet-stream', 'image/jpeg'):
            self.assertTrue(_anexo_imagem(SimpleNamespace(filename='foto.JPG', content_type=tipo)))
        self.assertFalse(_anexo_imagem(SimpleNamespace(filename='arquivo.pdf', content_type=None)))
