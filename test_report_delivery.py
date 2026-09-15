"""Exercise delivery failures without credentials or sending Telegram messages."""
import ast
import io
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock


class TelegramError(Exception):
    pass


class NetworkError(TelegramError):
    pass


class TimedOut(NetworkError):
    pass


class BadRequest(NetworkError):
    pass


class RetryAfter(TelegramError):
    def __init__(self, value):
        self.retry_after = value


SOURCE = ast.parse(Path(__file__).with_name('app.py').read_text())
NAMES = {'telegram_report_send', 'deliver_live_map_report', 'telegram_text_chunks'}
HELPERS = ast.Module(body=[n for n in SOURCE.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name in NAMES], type_ignores=[])


class ReportDeliveryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.sleep = AsyncMock()
        self.scope = dict(io=io, asyncio=SimpleNamespace(sleep=self.sleep),
                          TelegramError=TelegramError, NetworkError=NetworkError,
                          TimedOut=TimedOut, BadRequest=BadRequest, RetryAfter=RetryAfter,
                          report_csv=lambda report: b'full csv payload')
        exec(compile(HELPERS, 'app.py', 'exec'), self.scope)

    async def test_timeout_retries_only_failed_part_then_finishes(self):
        order = []
        attempted = []

        async def text(value, **kwargs):
            attempted.append(value)
            if len(attempted) == 2:
                raise TimedOut()
            order.append(value)
            self.assertEqual(kwargs['read_timeout'], 30)

        async def document(**kwargs):
            order.append('CSV')

        message = SimpleNamespace(reply_text=text, reply_document=document)
        await self.scope['deliver_live_map_report'](message, {'maps': [1]}, 'x' * 8000)
        self.assertEqual(order[0], 'CSV')
        self.assertEqual([x.split('\n')[0] for x in order[1:]], ['Parte 1/3', 'Parte 2/3', 'Parte 3/3'])
        self.assertEqual(attempted[1], attempted[2])
        self.assertEqual(len(attempted), 4)

    async def test_failed_part_does_not_block_remaining_parts(self):
        attempts = []

        async def text(value, **kwargs):
            attempts.append(value)
            if value.startswith('Parte 1/'):
                raise TimedOut()

        message = SimpleNamespace(reply_text=text, reply_document=AsyncMock())
        await self.scope['deliver_live_map_report'](message, {'maps': [1]}, 'x' * 5000)
        self.assertEqual(sum(v.startswith('Parte 1/') for v in attempts), 3)
        self.assertTrue(any(v.startswith('Parte 2/') for v in attempts))
        self.assertIn('non ha confermato', attempts[-1])

    async def test_upload_retry_recreates_full_stream_and_failure_keeps_text(self):
        payloads = []

        async def document(**kwargs):
            payloads.append(kwargs['document'].read())
            raise TimedOut()

        message = SimpleNamespace(reply_text=AsyncMock(), reply_document=document)
        await self.scope['deliver_live_map_report'](message, {'maps': [1]}, 'report')
        self.assertEqual(payloads, [b'full csv payload'] * 3)
        self.assertTrue(message.reply_text.call_args_list[0].args[0].startswith('Parte 1/1'))

    async def test_rate_limit_wait_and_permanent_failure(self):
        for duration in (4, timedelta(seconds=4)):
            operation = AsyncMock(side_effect=[RetryAfter(duration), None])
            self.assertTrue(await self.scope['telegram_report_send'](operation, 'part'))
            self.sleep.assert_awaited_with(5)
        operation = AsyncMock(side_effect=BadRequest())
        self.assertFalse(await self.scope['telegram_report_send'](operation, 'part'))
        self.assertEqual(operation.await_count, 1)


if __name__ == '__main__':
    unittest.main()
