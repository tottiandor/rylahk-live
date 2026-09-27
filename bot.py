#!/usr/bin/env python3
"""Telegram commands: add a league to a chat without touching any file.

In the chat (a group with the bot in it, or the private chat) that should get a league:
  /add <Fantrax league link>   the bot lists the league's teams, numbered
  /team <number>               picks your team; that league's alerts now go to this chat
  /list                        what this chat receives
  /remove                      stop sending any league to this chat

Only Totti's own Telegram account is obeyed (TELEGRAM_CHAT_ID, his private chat, is also
his user id). GitHub runs this every 5 minutes, so an answer can take a few minutes.
Added leagues live in state/leagues.json. Every Fantrax call is a read.
"""
import os, re
from common import (fantrax, tg_api, log, esc, load_state, save_state, commit_state, leagues)

HELP = ('Commands:\n'
        '/add &lt;Fantrax league link&gt; – send that league to this chat\n'
        '/team &lt;number&gt; – pick your team after /add\n'
        '/list – leagues sent to this chat\n'
        '/remove – stop sending leagues to this chat')


def reply(chat, text):
    try:
        tg_api('sendMessage', chat_id=chat, text=text, parse_mode='HTML',
               disable_web_page_preview=True)
    except Exception as e:
        log('reply failed:', e)


def league_teams(league_id):
    info = fantrax('getFantasyLeagueInfo', league_id, ok=lambda d: d.get('fantasySettings'))
    fs = info['fantasySettings']
    anyteam = fantrax('getTeamRosterInfo', league_id, ok=lambda d: d.get('fantasyTeams'))
    teams = sorted(([t['id'], t['name']] for t in anyteam['fantasyTeams']), key=lambda t: t[1].lower())
    return fs.get('leagueName'), int(fs['season']['id']), teams


def handle(chat, text, reg, pending):
    cmd, _, arg = text.strip().partition(' ')
    cmd = cmd.split('@')[0].lower()
    arg = arg.strip()
    key = str(chat)
    if cmd in ('/start', '/help'):
        return reply(chat, HELP)
    if cmd == '/add':
        m = re.search(r'fantrax\.com/fantasy/league/([a-z0-9]+)', arg) or re.fullmatch(r'([a-z0-9]{12,20})', arg)
        if not m:
            return reply(chat, 'Send it like this:\n/add https://www.fantrax.com/fantasy/league/…/home')
        lid = m.group(1)
        try:
            name, season, teams = league_teams(lid)
        except Exception as e:
            log('add failed:', e)
            return reply(chat, "I can't read that league. The bot has no Fantrax login, so the league "
                               "must be viewable by the public (a commissioner setting on Fantrax).")
        pending[key] = {'leagueId': lid, 'seasonId': season, 'name': name, 'teams': teams}
        lines = ['<b>%s</b> – which team is yours? Reply /team &lt;number&gt;' % esc(name)]
        lines += ['%d. %s' % (i + 1, esc(t[1])) for i, t in enumerate(teams)]
        return reply(chat, '\n'.join(lines))
    if cmd == '/team':
        p = pending.get(key)
        if not p:
            return reply(chat, 'First /add a league in this chat.')
        if not arg.isdigit() or not 1 <= int(arg) <= len(p['teams']):
            return reply(chat, 'Pick a number from the list, e.g. /team 3')
        tid, tname = p['teams'][int(arg) - 1]
        reg['leagues'] = [lg for lg in reg['leagues'] if lg['leagueId'] != p['leagueId']]
        short = re.sub(r'[^A-Za-z0-9]+', ' ', p['name']).strip()
        reg['leagues'].append({'key': p['leagueId'], 'name': short[:24], 'leagueId': p['leagueId'],
                               'seasonId': p['seasonId'], 'myTeamId': tid, 'chatId': chat})
        del pending[key]
        return reply(chat, '✅ <b>%s</b> → this chat, as <b>%s</b>.\nLive scores during games, and injury news '
                           'for your players (first check within ~10 minutes).' % (esc(p['name']), esc(tname)))
    if cmd == '/list':
        mine = [lg for lg in leagues() if str(lg.get('chatId') or os.environ.get('TELEGRAM_CHAT_ID')) == key]
        return reply(chat, '\n'.join('• %s' % esc(lg['name']) for lg in mine) or 'No league is sent to this chat.')
    if cmd == '/remove':
        before = len(reg['leagues'])
        reg['leagues'] = [lg for lg in reg['leagues'] if str(lg.get('chatId')) != key]
        return reply(chat, 'Removed %d league(s) from this chat.' % (before - len(reg['leagues']))
                     + ('' if before > len(reg['leagues']) else ' (Leagues set in config.json stay; ask Claude.)'))


def main():
    if not os.environ.get('TELEGRAM_BOT_TOKEN'):
        return log('Telegram not connected')
    owner = int(os.environ['TELEGRAM_CHAT_ID'])
    st = load_state('bot.json', {'offset': 0, 'pending': {}})
    reg = load_state('leagues.json', {'leagues': []})
    ups = tg_api('getUpdates', offset=st['offset'], timeout=0,
                 allowed_updates=['message']).get('result', [])
    for u in ups:
        st['offset'] = u['update_id'] + 1
        m = u.get('message') or {}
        text = m.get('text') or ''
        if not text.startswith('/'):
            continue
        if (m.get('from') or {}).get('id') != owner:
            log('ignored a command from someone else')
            continue
        log('command:', text.split()[0])
        handle(m['chat']['id'], text, reg, st['pending'])
    save_state('bot.json', st)
    save_state('leagues.json', reg)
    commit_state('bot state')


if __name__ == '__main__':
    main()
