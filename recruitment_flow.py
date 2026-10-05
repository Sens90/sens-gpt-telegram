"""Private recruitment, using the existing server-only recruitment table."""
import asyncio
import json
import os
import re
from datetime import datetime, timedelta, timezone
from telegram import ReplyKeyboardMarkup, ReplyKeyboardRemove

CLUBS = ('TITANI ABUSIVI', 'TAMARRI ABUSIVI', 'TORNADI ABUSIVI', 'TALENTI ABUSIVI')
CODE = re.compile(r'CAND-([1-9][0-9]*)', re.I)

class RecruitmentFlow:
    def __init__(self, community):
        self.community = community
        self.locks = {}

    def destination(self):
        configured = os.getenv('RECRUITMENT_COMMUNITY_CHAT_ID', '').strip()
        if configured:
            return int(configured)
        rows = self.community._get('community_settings', {'select': 'chat_id', 'limit': 2})
        if len(rows) == 1 and int(rows[0]['chat_id']) < 0:
            return int(rows[0]['chat_id'])
        raise ValueError('Recruitment community destination must be configured')

    async def begin(self, message, context):
        context.user_data['candidate_stage'] = 'tag'
        context.user_data.pop('candidate_player', None)
        await message.reply_text('Benvenuto nel reclutamento dei club Abusivi!\nInviami il tuo tag Brawl Stars completo, per esempio #2LVRCLV8LV.\nIl bot leggerà il profilo e assocerà la candidatura al tuo ID Telegram. Scrivi annulla per interrompere.', reply_markup=ReplyKeyboardRemove())

    async def handle(self, message, context):
        if not message.from_user:
            return False
        text = (message.text or '').strip()
        if await self.staff_command(message, context, text):
            return True
        private = getattr(message.chat, 'type', None) == 'private'
        if not private:
            if text.casefold() in ('reclutamento', 'candidati'):
                username = context.bot.username
                await message.reply_text(f'Per candidarti apri il bot in privato: https://t.me/{username}?start=reclutamento')
                return True
            return False
        if text.casefold() in ('reclutamento', 'candidati'):
            await self.begin(message, context)
            return True
        stage = context.user_data.get('candidate_stage')
        if not stage:
            return False
        if text.casefold() in ('annulla', '/annulla'):
            context.user_data.pop('candidate_stage', None)
            context.user_data.pop('candidate_player', None)
            await message.reply_text('Candidatura interrotta.', reply_markup=ReplyKeyboardRemove())
            return True
        if stage == 'tag':
            if not re.fullmatch(r'#?[0289PYLQGRJCUV]{3,15}', text, re.I):
                await message.reply_text('Tag non valido. Copialo dal tuo profilo Brawl Stars e riprova.')
                return True
            tag = text.lstrip('#').upper()
            try:
                player = await asyncio.to_thread(self.community.player_fetcher, tag)
            except Exception:
                player = None
            if not player or str(player.get('tag', '')).lstrip('#').upper() != tag:
                await message.reply_text('Non riesco a recuperare questo profilo. Controlla il tag o riprova tra poco.')
                return True
            # Store only the profile information used by recruitment.
            club = player.get('club_name') or player.get('club')
            if isinstance(club, dict):
                club = club.get('name')
            summary = {'tag': tag, 'name': player.get('name'), 'trophies': player.get('trophies'), 'club': club, 'brawlers': player.get('brawlers'), 'ranked_current': player.get('ranked_current'), 'victories_3v3': player.get('3vs3Victories', player.get('wins_3v3', player.get('victories_3v3')))}
            context.user_data['candidate_player'] = summary
            context.user_data['candidate_stage'] = 'club'
            await message.reply_text(f"Profilo trovato: {summary['name']}\nTag: #{tag}\nTrofei: {summary['trophies']}\nClub attuale: {club or 'Non disponibile'}\n\nScegli il club per cui vuoi candidarti:", reply_markup=ReplyKeyboardMarkup([[c] for c in CLUBS], one_time_keyboard=True, resize_keyboard=True))
            return True
        if stage == 'club':
            club = text.upper()
            if club not in CLUBS:
                await message.reply_text('Scegli uno dei quattro club usando i pulsanti.')
                return True
            player = context.user_data.get('candidate_player')
            if not player:
                await self.begin(message, context)
                return True
            lock = self.locks.setdefault(message.from_user.id, asyncio.Lock())
            async with lock:
                # A second update can arrive while the first is being saved.
                if context.user_data.get('candidate_stage') != 'club':
                    return True
                try:
                    destination = await asyncio.to_thread(self.destination)
                    params = {'select': '*', 'chat_id': f'eq.{destination}', 'telegram_user_id': f'eq.{message.from_user.id}', 'status': 'eq.pending', 'order': 'created_at.desc', 'limit': 1}
                    pending = await asyncio.to_thread(self.community._get, 'community_recruitments', params)
                    if pending:
                        row = pending[0]
                        label = 'Hai già una candidatura in attesa.'
                    else:
                        payload = {'chat_id': destination, 'telegram_user_id': message.from_user.id, 'telegram_username': message.from_user.username, 'display_name': message.from_user.full_name, 'player_tag': player['tag'], 'ranked': player.get('ranked_current'), 'status': 'pending', 'notes': json.dumps({'version': 1, 'requested_club': club, 'profile': player, 'ownership_verified': False}, ensure_ascii=False)}
                        rows = await asyncio.to_thread(self.community._post, 'community_recruitments', payload)
                        if not rows or not rows[0].get('id'):
                            raise ValueError('Recruitment was not persisted')
                        row = rows[0]
                        label = 'Candidatura registrata.'
                except Exception:
                    await message.reply_text('Non riesco a salvare la candidatura adesso. Riprova scegliendo il club; non ti confermo una richiesta non salvata.')
                    return True
                context.user_data.pop('candidate_stage', None)
                context.user_data.pop('candidate_player', None)
                await message.reply_text(f"{label}\nCodice: CAND-{row['id']}\nLo staff verificherà che il profilo ti appartenga e valuterà l'ammissione. Riceverai qui gli aggiornamenti.", reply_markup=ReplyKeyboardRemove())
            return True
        return False

    async def staff_command(self, message, context, text):
        command = re.fullmatch(r'(approva|rifiuta)\s+(CAND-[1-9][0-9]*)(?:\s+(verificato))?', text, re.I)
        if text.casefold() != 'candidature' and not command:
            return False
        try:
            destination = await asyncio.to_thread(self.destination)
        except Exception:
            await message.reply_text('Destinazione reclutamento non configurata.')
            return True
        if not await self.community.is_admin(context, destination, message.from_user.id):
            await message.reply_text('Questo comando è riservato agli amministratori della Community.')
            return True
        if not command:
            rows = await asyncio.to_thread(self.community._get, 'community_recruitments', {'select': '*', 'chat_id': f'eq.{destination}', 'status': 'in.(pending,approved_delivery_pending)', 'order': 'created_at.desc', 'limit': 20})
            lines = ['CANDIDATURE IN ATTESA']
            for row in rows:
                try:
                    detail = json.loads(row.get('notes') or '{}')
                except (ValueError, TypeError):
                    detail = {}
                profile = detail.get('profile', {}) if isinstance(detail, dict) else {}
                club = detail.get('requested_club', 'Non indicato') if isinstance(detail, dict) else 'Non indicato'
                lines.append(f"CAND-{row['id']} | {profile.get('name') or row.get('display_name')} | #{row['player_tag']} | {club} | Trofei: {profile.get('trophies', 'n/d')} | ID Telegram: {row['telegram_user_id']}")
            lines.append("Prima di approvare verifica che il candidato possieda il profilo. Poi: approva CAND-numero verificato. Per rifiutare: rifiuta CAND-numero.")
            # Candidate details are delivered privately to the verified administrator.
            try:
                await context.bot.send_message(chat_id=message.from_user.id, text='\n\n'.join(lines))
            except Exception:
                await message.reply_text('Apri prima il bot in privato, poi ripeti candidature.')
            return True
        action, code, ownership = command.groups()
        if action.casefold() == 'approva' and not ownership:
            await message.reply_text('Dopo aver verificato il possesso del profilo usa: approva CAND-numero verificato.')
            return True
        candidate_id = int(CODE.fullmatch(code).group(1))
        rows = await asyncio.to_thread(self.community._get, 'community_recruitments', {'select': '*', 'id': f'eq.{candidate_id}', 'chat_id': f'eq.{destination}', 'limit': 1})
        if not rows:
            await message.reply_text('Codice candidatura non trovato.')
            return True
        row = rows[0]
        lock = self.locks.setdefault(row['telegram_user_id'], asyncio.Lock())
        async with lock:
            # Reload inside the lock: competing approvals must not send two invitations.
            rows = await asyncio.to_thread(self.community._get, 'community_recruitments', {'select': '*', 'id': f'eq.{candidate_id}', 'chat_id': f'eq.{destination}', 'limit': 1})
            row = rows[0]
            if row['status'] not in ('pending', 'approved_delivery_pending', 'rejected_delivery_pending'):
                await message.reply_text('Questa candidatura è già stata gestita.')
                return True
            if row['status'] == 'rejected_delivery_pending' and action.casefold() == 'approva':
                await message.reply_text('Il rifiuto è già registrato. Ripeti rifiuta per ritentare la comunicazione.')
                return True
            if row['status'] == 'approved_delivery_pending' and action.casefold() == 'rifiuta':
                await message.reply_text('L’approvazione è già registrata. Ripeti approva per ritentare la consegna.')
                return True
            state = 'approved' if action.casefold() == 'approva' else 'rejected'
            # Record the staff decision before any Telegram side effect.
            changed = await asyncio.to_thread(self.community._patch, 'community_recruitments', {'status': state + '_delivery_pending'}, {'id': f'eq.{candidate_id}', 'chat_id': f'eq.{destination}', 'status': f'eq.{row["status"]}'}, 'return=representation')
            if not changed:
                await message.reply_text('La candidatura è stata gestita da un altro amministratore.')
                return True
            invite = None
            try:
                if state == 'approved':
                    # Join requests require staff verification of the Telegram ID,
                    # so forwarding the invitation cannot admit another person.
                    invite = await context.bot.create_chat_invite_link(chat_id=destination, name=code.upper(), expire_date=datetime.now(timezone.utc) + timedelta(hours=24), creates_join_request=True)
                    body = f"Candidatura {code.upper()} approvata!\nRichiedi l'ingresso nella Community: {invite.invite_link}\nIl bot confermerà automaticamente il tuo ID Telegram. Dopo l'ingresso scrivi: registrami #{row['player_tag']}"
                else:
                    body = f'Candidatura {code.upper()}: lo staff non ha approvato la richiesta.'
                await context.bot.send_message(chat_id=row['telegram_user_id'], text=body)
            except Exception:
                if invite:
                    try:
                        await context.bot.revoke_chat_invite_link(chat_id=destination, invite_link=invite.invite_link)
                    except Exception:
                        pass
                await message.reply_text('Decisione salvata, ma consegna non riuscita. Ripeti lo stesso comando per ritentare.')
                return True
            await asyncio.to_thread(self.community._patch, 'community_recruitments', {'status': state}, {'id': f'eq.{candidate_id}', 'chat_id': f'eq.{destination}', 'status': f'eq.{state}_delivery_pending'})
            await message.reply_text(f'{code.upper()}: decisione comunicata in privato al candidato.')
        return True

    async def join_request(self, update, context):
        request = update.chat_join_request
        link = getattr(request, 'invite_link', None)
        code = CODE.fullmatch(str(getattr(link, 'name', '') or ''))
        if not request or not code:
            return
        try:
            destination = await asyncio.to_thread(self.destination)
            if request.chat.id != destination:
                return
            rows = await asyncio.to_thread(self.community._get, 'community_recruitments', {'select': 'id,telegram_user_id,status', 'id': f'eq.{code.group(1)}', 'chat_id': f'eq.{destination}', 'status': 'eq.approved', 'limit': 1})
            if not rows or int(rows[0]['telegram_user_id']) != request.from_user.id:
                return
            await context.bot.approve_chat_join_request(chat_id=destination, user_id=request.from_user.id)
            await asyncio.to_thread(self.community._patch, 'community_recruitments', {'status': 'joined'}, {'id': f'eq.{code.group(1)}', 'chat_id': f'eq.{destination}', 'status': 'eq.approved'})
            await context.bot.revoke_chat_invite_link(chat_id=destination, invite_link=link.invite_link)
        except Exception:
            # Leave the request to staff if Telegram permissions or DB access fail.
            return
