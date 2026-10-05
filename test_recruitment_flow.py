import ast
import unittest
from pathlib import Path
from types import SimpleNamespace as S
from unittest.mock import Mock, AsyncMock
from recruitment_flow import RecruitmentFlow, CLUBS

class RecruitmentTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.community = Mock()
        self.community.player_fetcher.return_value = {'tag':'#2LVRCLV8LV','name':'Player','trophies':12345,'club_name':'Old Club','wins_3v3':42,'brawlers':80}
        self.community._get.return_value = []
        self.community._post.return_value = [{'id':17}]
        self.community.is_admin = AsyncMock(return_value=False)
        self.flow = RecruitmentFlow(self.community)
        self.flow.destination = Mock(return_value=-100123)
        self.message = S(text='', chat=S(type='private'), chat_id=42, from_user=S(id=42, username=None, full_name='Player'), reply_text=AsyncMock())
        self.context = S(user_data={}, bot=S(username='SensGPT_TitaniAbusiviBot', send_message=AsyncMock(), approve_chat_join_request=AsyncMock(), revoke_chat_invite_link=AsyncMock()))

    async def submit(self):
        await self.flow.begin(self.message,self.context)
        self.message.text = '#2LVRCLV8LV'
        await self.flow.handle(self.message,self.context)
        self.message.text = CLUBS[2]
        await self.flow.handle(self.message,self.context)

    async def test_tag_club_and_persistent_code(self):
        await self.submit()
        payload = self.community._post.call_args.args[1]
        self.assertEqual(payload['telegram_user_id'],42)
        self.assertEqual(payload['player_tag'],'2LVRCLV8LV')
        self.assertIn('TORNADI ABUSIVI',payload['notes'])
        self.assertIn('42',payload['notes'])
        self.assertIn('CAND-17',self.message.reply_text.call_args.args[0])
        self.assertNotIn('candidate_stage',self.context.user_data)

    async def test_storage_failure_keeps_retry_and_no_false_confirmation(self):
        self.community._post.return_value = []
        await self.submit()
        self.assertEqual(self.context.user_data['candidate_stage'],'club')
        self.assertNotIn('CAND-',self.message.reply_text.call_args.args[0])

    async def test_duplicate_reuses_code_without_insert(self):
        self.community._get.return_value = [{'id':12}]
        await self.submit()
        self.community._post.assert_not_called()
        self.assertIn('CAND-12',self.message.reply_text.call_args.args[0])

    async def test_invalid_or_wrong_tag_does_not_advance(self):
        await self.flow.begin(self.message,self.context)
        self.message.text = 'garbage #2LVRCLV8LV'
        await self.flow.handle(self.message,self.context)
        self.community.player_fetcher.assert_not_called()
        self.message.text = '#2LVRCLV8LV'
        self.community.player_fetcher.return_value = {'tag':'#PYLQGR','name':'Wrong'}
        await self.flow.handle(self.message,self.context)
        self.assertEqual(self.context.user_data['candidate_stage'],'tag')

    async def test_stranger_cannot_approve(self):
        self.message.text = 'approva CAND-17 verificato'
        await self.flow.handle(self.message,self.context)
        self.community._patch.assert_not_called()
        self.context.bot.send_message.assert_not_awaited()

    async def test_invite_only_admits_linked_id(self):
        self.community._get.return_value=[{'id':17,'telegram_user_id':42,'status':'approved'}]
        request=S(chat=S(id=-100123),from_user=S(id=99),invite_link=S(name='CAND-17',invite_link='https://t.me/+test'))
        await self.flow.join_request(S(chat_join_request=request),self.context)
        self.context.bot.approve_chat_join_request.assert_not_awaited()
        request.from_user.id=42
        await self.flow.join_request(S(chat_join_request=request),self.context)
        self.context.bot.approve_chat_join_request.assert_awaited_once_with(chat_id=-100123,user_id=42)

    def test_start_and_messages_route_before_membership_gate(self):
        tree=ast.parse(Path('app.py').read_text())
        answer=next(n for n in tree.body if isinstance(n,ast.AsyncFunctionDef) and n.name=='answer')
        route=next(n.lineno for n in ast.walk(answer) if isinstance(n,ast.Attribute) and n.attr=='handle' and isinstance(n.value,ast.Name) and n.value.id=='recruitment')
        gate=next(n.lineno for n in ast.walk(answer) if isinstance(n,ast.Attribute) and n.attr=='get_registered_user')
        self.assertLess(route,gate)

if __name__=='__main__': unittest.main()
