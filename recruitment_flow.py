"""Private recruitment, using the existing server-only recruitment table."""
import asyncio
import json
import os
import re
from datetime import datetime, timedelta, timezone
from telegram import ReplyKeyboardMarkup, ReplyKeyboardRemove, InlineKeyboardButton, InlineKeyboardMarkup
from types import SimpleNamespace

CLUBS = ('TITANI ABUSIVI', 'TAMARRI ABUSIVI', 'TORNADI ABUSIVI', 'TALENTI ABUSIVI')
CODE = re.compile(r'CAND-([1-9][0-9]*)', re.I)

def profile_stats(profile):
    fields = [('trophies', 'Trofei'), ('brawlers', 'Brawler'), ('level', 'Livello esperienza'),
              ('victories_3v3', 'Vittorie 3 contro 3'), ('wins_solo', 'Vittorie solitario'),
              ('wins_duo', 'Vittorie duo'), ('ranked_current', 'Ranked attuale'),
              ('ranked_current_elo', 'Punti ranked'), ('ranked_season_peak', 'Ranked massimo stagionale'),
              ('ranked_career_peak', 'Ranked massimo carriera'), ('prestige', 'Prestigio'), ('fame', 'Fama'),
              ('gadgets_owned', 'Gadget'), ('star_powers_owned', 'Abilità stellari'),
              ('gears_owned', 'Equipaggiamenti'), ('hypercharges_owned', 'Hypercariche'),
              ('buffies_owned', 'Buffies'), ('skins_owned', 'Skin'),
              ('account_created_year', 'Anno creazione account'), ('estimated_hours', 'Ore stimate')]
    return '\n'.join(f'{label}: {profile.get(key) if profile.get(key) is not None else "Non disponibile"}' for key, label in fields)

class RecruitmentFlow:
    def __init__(self, community):
        self.community = community
        self.locks = {}

    @staticmethod
    def decision_buttons(candidate_id):
        return InlineKeyboardMarkup([[
            InlineKeyboardButton('✅ Approva', callback_data=f'recruit:approve:{candidate_id}'),
            InlineKeyboardButton('❌ Rifiuta', callback_data=f'recruit:reject:{candidate_id}'),
        ]])

    async def authorized_admin(self, context, destination, user_id):
        if await self.community.is_admin(context, destination, user_id):
            return True
        staff_chat = os.getenv('RECRUITMENT_STAFF_CHAT_ID', '').strip()
        return bool(staff_chat and await self.community.is_admin(context, int(staff_chat), user_id))

    async def decision_callback(self, update, context):
        query = update.callback_query
        match = re.fullmatch(r'recruit:(approve|reject|confirm_approve|confirm_reject|back):([1-9][0-9]*)', query.data or '')
        if not match or not query.message:
            await query.answer()
            return
        action, candidate_id = match.groups()
        try:
            destination = await asyncio.to_thread(self.destination)
            allowed = await self.authorized_admin(context, destination, query.from_user.id)
        except Exception:
            await query.answer('Verifica dei permessi non riuscita. Riprova.', show_alert=True)
            return
        if not allowed:
            await query.answer('Riservato agli amministratori di Direzione o della Community.', show_alert=True)
            return
        await query.answer()
        if action == 'back':
            await query.edit_message_reply_markup(reply_markup=self.decision_buttons(candidate_id))
            return
        if action in ('approve', 'reject'):
            label = '✅ Confermo: profilo verificato' if action == 'approve' else '❌ Confermo il rifiuto'
            await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(label, callback_data=f'recruit:confirm_{action}:{candidate_id}')],
                [InlineKeyboardButton('Indietro', callback_data=f'recruit:back:{candidate_id}')],
            ]))
            return
        proxy = SimpleNamespace(from_user=query.from_user, chat=query.message.chat,
                                chat_id=query.message.chat_id, reply_text=query.message.reply_text)
        command = f'approva cand {candidate_id} verificato' if action == 'confirm_approve' else f'rifiuta cand {candidate_id}'
        await self.staff_command(proxy, context, command)
        rows = await asyncio.to_thread(self.community._get, 'community_recruitments', {
            'select':'status', 'id':f'eq.{candidate_id}', 'chat_id':f'eq.{destination}', 'limit':1,
        })
        if rows and rows[0]['status'] in ('approved', 'rejected', 'joined'):
            await query.edit_message_reply_markup(reply_markup=None)
        else:
            await query.edit_message_reply_markup(reply_markup=self.decision_buttons(candidate_id))

    async def group_id_command(self, update, context):
        message = update.effective_message
        if message:
            await message.reply_text(f'ID gruppo: {message.chat_id}')

    async def candidates_command(self, update, context):
        if update.effective_message:
            await self.staff_command(update.effective_message, context, 'candidature')

    def destination(self):
        configured = os.getenv('RECRUITMENT_COMMUNITY_CHAT_ID', '').strip()
        if configured:
            return int(configured)
        rows = self.community._get('community_settings', {'select': 'chat_id', 'limit': 2})
        if len(rows) == 1 and int(rows[0]['chat_id']) < 0:
            return int(rows[0]['chat_id'])
        raise ValueError('Recruitment community destination must be configured')

    async def begin(self, message, context):
        if await self.registered_guard(message, context):
            return
        context.user_data['candidate_stage'] = 'tag'
        context.user_data.pop('candidate_player', None)
        await message.reply_text('Benvenuto nel reclutamento dei club Abusivi!\nInviami il tuo tag Brawl Stars completo, per esempio #2LVRCLV8LV.\nIl bot leggerà il profilo e assocerà la candidatura al tuo ID Telegram. Scrivi annulla per interrompere.', reply_markup=ReplyKeyboardRemove())

    async def registered_guard(self, message, context):
        try:
            rows = await asyncio.to_thread(self.community._get, 'community_members', {
                'select': 'player_tag', 'telegram_user_id': f'eq.{message.from_user.id}',
                'player_tag': 'not.is.null', 'limit': 1,
            })
        except Exception:
            await message.reply_text('Non riesco a verificare la tua registrazione. Riprova tra poco.')
            return True
        if not rows:
            return False
        context.user_data.pop('candidate_stage', None)
        context.user_data.pop('candidate_player', None)
        await message.reply_text('Il tuo ID Telegram è già registrato. Non puoi avviare una nuova candidatura o cambiare il tag da questo percorso. Per correggere il collegamento contatta lo staff.', reply_markup=ReplyKeyboardRemove())
        return True

    async def handle(self, message, context):
        if not message.from_user:
            return False
        text = (message.text or '').strip()
        username = getattr(context.bot, 'username', None)
        if username:
            text = re.sub(r'@' + re.escape(username) + r'\b', '', text, flags=re.I).strip()
        staff_chat = os.getenv('RECRUITMENT_STAFF_CHAT_ID', '').strip()
        if text.casefold() == 'candidati' and staff_chat and str(message.chat_id) == staff_chat:
            text = 'candidature'
        if text.casefold() == 'id gruppo' and getattr(message.chat, 'type', None) != 'private':
            await message.reply_text(f'ID gruppo: {message.chat_id}')
            return True
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
        if await self.registered_guard(message, context):
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
            for key in ('level', 'wins_solo', 'wins_duo', 'ranked_current_elo', 'ranked_season_peak', 'ranked_season_peak_elo', 'ranked_career_peak', 'ranked_career_peak_elo', 'prestige', 'fame', 'fame_tier', 'gadgets_owned', 'star_powers_owned', 'gears_owned', 'hypercharges_owned', 'buffies_owned', 'skins_owned', 'account_created_year', 'estimated_hours', 'power_levels', 'prestige_levels', 'brawler_trophies'):
                summary[key] = player.get(key)
            context.user_data['candidate_player'] = summary
            context.user_data['candidate_stage'] = 'club'
            await message.reply_text(f"Profilo trovato: {summary['name']}\nTag: #{tag}\n{profile_stats(summary)}\nClub attuale: {club or 'Non disponibile'}\n\nScegli il club per cui vuoi candidarti:", reply_markup=ReplyKeyboardMarkup([[c] for c in CLUBS], one_time_keyboard=True, resize_keyboard=True))
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
                await message.reply_text(f"{label}\nCodice: CAND {row['id']}\nLo staff verificherà che il profilo ti appartenga e valuterà l'ammissione. Riceverai qui gli aggiornamenti.", reply_markup=ReplyKeyboardRemove())
                staff_chat = os.getenv('RECRUITMENT_STAFF_CHAT_ID', '').strip()
                if not pending and staff_chat:
                    try:
                        await context.bot.send_message(chat_id=int(staff_chat), text=f"NUOVA CANDIDATURA — CAND {row['id']}\n{player.get('name')} | #{player['tag']}\nClub richiesto: {club}\nID Telegram: {message.from_user.id}\n{profile_stats(player)}", reply_markup=self.decision_buttons(row['id']))
                    except Exception as exc:
                        print('RECRUITMENT STAFF NOTIFICATION FAILED:', type(exc).__name__, flush=True)
            return True
        return False

    async def staff_command(self, message, context, text):
        command = re.fullmatch(r'(approva|rifiuta|annulla)\s+CAND[\s-]*([1-9][0-9]*)(?:\s+(verificato))?', text, re.I)
        if text.casefold() != 'candidature' and not command:
            return False
        try:
            destination = await asyncio.to_thread(self.destination)
        except Exception:
            await message.reply_text('Destinazione reclutamento non configurata.')
            return True
        if not await self.authorized_admin(context, destination, message.from_user.id):
            await message.reply_text('Questo comando è riservato agli amministratori di Direzione o della Community.')
            return True
        if not command:
            rows = await asyncio.to_thread(self.community._get, 'community_recruitments', {'select': '*', 'chat_id': f'eq.{destination}', 'status': 'in.(pending,approved_delivery_pending)', 'order': 'created_at.desc', 'limit': 20})
            lines = ['CANDIDATURE IN ATTESA']
            keyboards = {}
            if not rows:
                lines.append('Nessuna candidatura in attesa.')
            for row in rows:
                try:
                    detail = json.loads(row.get('notes') or '{}')
                except (ValueError, TypeError):
                    detail = {}
                profile = detail.get('profile', {}) if isinstance(detail, dict) else {}
                club = detail.get('requested_club', 'Non indicato') if isinstance(detail, dict) else 'Non indicato'
                lines.append(f"CAND {row['id']} | {profile.get('name') or row.get('display_name')} | #{row['player_tag']} | {club} | Trofei: {profile.get('trophies', 'n/d')} | ID Telegram: {row['telegram_user_id']}")
                lines[-1] += '\n' + profile_stats(profile)
                keyboards[len(lines)-1] = self.decision_buttons(row['id'])
            lines.append("Prima di approvare verifica che il candidato possieda il profilo. Poi: approva cand numero verificato. Per rifiutare: rifiuta cand numero.")
            # Only the configured staff group can receive candidate details in-group.
            staff_chat = os.getenv('RECRUITMENT_STAFF_CHAT_ID', '').strip()
            in_direction = bool(staff_chat and str(message.chat_id) == staff_chat and getattr(message.chat, 'type', None) in ('group', 'supergroup'))
            reply_chat = message.chat_id if in_direction else message.from_user.id
            try:
                for index, line in enumerate(lines):
                    await context.bot.send_message(chat_id=reply_chat, text=line, reply_markup=keyboards.get(index))
            except Exception:
                await message.reply_text('Non riesco a consegnare l’elenco. Controlla i permessi del bot in Direzione oppure apri il bot in privato e ripeti candidature.')
            return True
        action, code, ownership = command.groups()
        if action.casefold() == 'annulla':
            action = 'rifiuta'
        if action.casefold() == 'approva' and not ownership:
            await message.reply_text('Dopo aver verificato il possesso del profilo usa: approva cand numero verificato.')
            return True
        candidate_id = int(code)
        code = f'CAND-{candidate_id}'
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
