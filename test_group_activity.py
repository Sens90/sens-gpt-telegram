"""Regression tests for activity before group routing and early returns."""
import os
import unittest
from datetime import datetime, timezone, timedelta
from unittest.mock import Mock, AsyncMock, patch
from telegram import Update, User
from telegram.ext import Application, MessageHandler, filters

os.environ.setdefault('TELEGRAM_TOKEN', 'test-token')
os.environ.setdefault('GEMINI_API_KEY', 'test-key')
os.environ.setdefault('SUPABASE_URL', 'https://example.supabase.co')
os.environ.setdefault('SUPABASE_SERVICE_ROLE_KEY', 'test-role-key')
with patch('google.genai.Client', return_value=Mock()):
    import app


class GroupActivityTests(unittest.IsolatedAsyncioTestCase):
    def application(self):
        application = Application.builder().token('123:ABC').build()
        application.bot._bot_user = User(123, 'Sens', True, username='SensGPT_TitaniAbusiviBot')
        application._initialized = True
        app.install_group_activity_handler(application)
        return application

    def update(self, application, content, private=False):
        message = {'message_id': 42, 'date': int(datetime.now(timezone.utc).timestamp()),
                   'chat': {'id': 456 if private else -100123,
                            'type': 'private' if private else 'supergroup'},
                   'from': {'id': 456, 'first_name': 'Member', 'is_bot': False}}
        message.update(content)
        return Update.de_json({'update_id': 1, 'message': message}, application.bot)

    async def test_plain_group_message_is_saved_before_silent_return(self):
        application = self.application()
        application.add_handler(MessageHandler(filters.TEXT, app.answer))
        update = self.update(application, {'text': 'Buongiorno a tutti'})
        order = []
        with (patch.object(app.community, 'track_activity', side_effect=lambda m: order.append('activity') or True),
              patch.object(app, 'census_telegram_member'),
              patch.object(app, 'save_user_conversation_memory', side_effect=lambda *a: order.append('memory')),
              patch.object(app.community, 'handle_command', new=AsyncMock()) as command):
            await application.process_update(update)
        self.assertEqual(order, ['activity', 'memory'])
        command.assert_not_awaited()

    async def test_command_is_saved_even_when_private_delivery_cannot_start(self):
        application = self.application()
        application.add_handler(MessageHandler(filters.TEXT, app.answer))
        update = self.update(application, {'text': 'Classifica oggi'})
        order = []
        async def no_private(*args):
            order.append('private')
            return None
        with (patch.object(app.community, 'track_activity', side_effect=lambda m: order.append('activity') or True),
              patch.object(app, 'census_telegram_member'),
              patch.object(app, '_private_group_command', new=AsyncMock(side_effect=no_private))):
            await application.process_update(update)
        self.assertEqual(order, ['activity', 'private'])

    async def test_media_and_slash_commands_are_counted_once(self):
        for content in ({'text': '/start', 'entities': [{'type': 'bot_command', 'offset': 0, 'length': 6}]},
                        {'photo': [{'file_id': 'p', 'file_unique_id': 'p', 'width': 10, 'height': 10}]},
                        {'voice': {'file_id': 'v', 'file_unique_id': 'v', 'duration': 1}},
                        {'sticker': {'file_id': 's', 'file_unique_id': 's', 'width': 10, 'height': 10,
                                     'is_animated': False, 'is_video': False, 'type': 'regular'}}):
            with self.subTest(content=content):
                application = self.application()
                with (patch.object(app.community, 'track_activity', return_value=True) as track,
                      patch.object(app, 'census_telegram_member') as census):
                    await application.process_update(self.update(application, content))
                track.assert_called_once()
                census.assert_called_once()

    async def test_private_messages_service_events_and_bots_do_not_reset_activity(self):
        for content, private in (({'text': 'Ciao'}, True),
                                 ({'new_chat_members': [{'id': 789, 'first_name': 'New', 'is_bot': False}]}, False),
                                 ({'text': 'Bot', 'from': {'id': 123, 'first_name': 'Bot', 'is_bot': True}}, False),
                                 ({'text': 'Anonymous', 'sender_chat': {'id': -100123, 'type': 'supergroup'}}, False)):
            with self.subTest(content=content):
                application = self.application()
                with (patch.object(app.community, 'track_activity') as track,
                      patch.object(app, 'census_telegram_member') as census):
                    await application.process_update(self.update(application, content, private))
                track.assert_not_called()
                census.assert_not_called()

    def test_activity_clears_old_warning_and_removes_inactivity(self):
        now = datetime.now(timezone.utc)
        application = self.application()
        message = self.update(application, {'text': 'Ciao'}).effective_message
        member = {'telegram_user_id': 456, 'last_seen_at': (now-timedelta(days=12)).isoformat(),
                  'last_warning_at': (now-timedelta(hours=1)).isoformat()}
        def persist(table, payload, **kwargs):
            if table == 'community_members':
                member.update(payload)
        with (patch.object(app.community, '_post', side_effect=persist),
              patch.object(app.community, 'members', return_value=[member]),
              patch.object(app.community, 'get_settings', return_value={'inactivity_warn_days': 10, 'inactivity_kick_days': 30})):
            self.assertEqual(len(app.community.inactivity_rows(-100123)), 1)
            self.assertTrue(app.community.track_activity(message))
            self.assertIsNone(member['last_warning_at'])
            self.assertEqual(app.community.inactivity_rows(-100123), [])


class PresenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_only_notice_owner_can_confirm(self):
        from types import SimpleNamespace
        query = SimpleNamespace(from_user=User(42, "Andre", False), data="presence:-1001:99", answer=AsyncMock())
        context = SimpleNamespace(bot=SimpleNamespace(get_chat_member=AsyncMock()))
        with patch.object(app.community, "track_activity") as track:
            await app.confirm_group_presence(SimpleNamespace(callback_query=query), context)
        track.assert_not_called()
        context.bot.get_chat_member.assert_not_awaited()

    async def test_success_records_original_group_not_private_chat(self):
        from types import SimpleNamespace
        query = SimpleNamespace(from_user=User(42, "Andre", False), data="presence:-1001:42", answer=AsyncMock())
        context = SimpleNamespace(bot=SimpleNamespace(get_chat_member=AsyncMock(return_value=SimpleNamespace(status="member"))))
        with patch.object(app.community, "track_activity", return_value=True) as track, patch.object(app, "census_telegram_member"):
            await app.confirm_group_presence(SimpleNamespace(callback_query=query), context)
        self.assertEqual(track.call_args.args[0].chat_id, -1001)
        self.assertEqual(track.call_args.args[0].from_user.id, 42)
        self.assertIn("azzerato", query.answer.call_args.args[0])

    async def test_failed_save_does_not_confirm(self):
        from types import SimpleNamespace
        query = SimpleNamespace(from_user=User(42, "Andre", False), data="presence:-1001:42", answer=AsyncMock())
        context = SimpleNamespace(bot=SimpleNamespace(get_chat_member=AsyncMock(return_value=SimpleNamespace(status="member"))))
        with patch.object(app.community, "track_activity", return_value=False), patch.object(app, "census_telegram_member") as census:
            await app.confirm_group_presence(SimpleNamespace(callback_query=query), context)
        census.assert_not_called()
        self.assertIn("non riuscito", query.answer.call_args.args[0])
