#!/usr/bin/env python3
"""Match-day alerts from FotMob for your own players, in every league's chat:

  📋 line-ups  - once a line-up is confirmed (about an hour before kickoff): starts, on the
                 bench, or not in the squad (with FotMob's reason, e.g. injury and return date)
  🚑 injuries  - a substitution FotMob flags as an injury ("injuredPlayerOut"), within minutes

Covers the Premier League, the European cups, the domestic cups and men's national teams,
so an international break is watched too. It only opens a match from 75 minutes before
kickoff to 150 minutes after, and ignores FotMob's pre-match guess of the line-up
("lastStarting11"): only a confirmed ("standard") line-up is reported.

FotMob's match data is its own site's feed, not a published API: it can change without
notice. Everything here is a read.

Run by live.py during Premier League games and by injuries.py the rest of the time.
    python fotmob.py --dry      check now, print instead of sending
"""
import json, sys, urllib.request
from datetime import datetime, timedelta, timezone
from common import (CFG, UA, _open, roster, telegram, esc, log, norm, now, local_hm,
                    load_state, save_state, leagues)

COMPS = {47, 42, 73, 10216, 132, 133}   # Premier League, UCL, UEL, UECL, FA Cup, League Cup
CLUB_FRIENDLIES = 489
BEFORE, AFTER = timedelta(minutes=75), timedelta(minutes=150)
CLUBS = CFG.get('fotmobClubs', {})


def get(path):
    return _open(urllib.request.Request('https://www.fotmob.com/api/' + path,
                                        headers={'User-Agent': UA}), tries=2)


def wanted(lg):
    if lg.get('primaryId') in COMPS:
        return True
    return (lg.get('ccode') == 'INT' and lg.get('primaryId') != CLUB_FRIENDLIES
            and 'women' not in (lg.get('name') or '').lower())


def matches_in_window(t):
    days = sorted({(t - AFTER).strftime('%Y%m%d'), (t + BEFORE).strftime('%Y%m%d')})
    out = {}
    for day in days:
        for lg in get('data/matches?date=' + day).get('leagues', []):
            if not wanted(lg):
                continue
            for m in lg.get('matches', []):
                st = m.get('status') or {}
                if st.get('cancelled') or not st.get('utcTime'):
                    continue
                k = datetime.fromisoformat(st['utcTime'].replace('Z', '+00:00'))
                if k - BEFORE <= t <= k + AFTER:
                    out[m['id']] = {'id': m['id'], 'home': m['home']['name'], 'away': m['away']['name'],
                                    'kickoff': k, 'club': lg.get('primaryId') in COMPS}
    return list(out.values())


def same_club(fotmob_team, fantrax_code):
    want = norm(CLUBS.get(fantrax_code, fantrax_code))
    have = norm(fotmob_team or '')
    return bool(want and have) and (want in have or have in want)


def same_player(person, p):
    """Same name (full, or surname) AND the same club: line-up entries carry the player's
    club, which keeps two players with one name apart."""
    a, b = norm(person.get('name')), norm(p['name'])
    if not a or not b or not (a == b or a.split()[-1] == b.split()[-1]):
        return False
    return same_club(person.get('primaryTeamName'), p['club'])


def lineup_people(content):
    """[(role, person, team name)] from a match's line-up, and whether it is confirmed.
    Before confirmation FotMob shows the last starting XI: good for knowing who is who
    (their FotMob ids), never for saying who plays."""
    lu = content.get('lineup') or {}
    out = []
    for side in ('homeTeam', 'awayTeam'):
        t = lu.get(side) or {}
        for role in ('starters', 'subs', 'unavailable'):
            for person in t.get(role) or []:
                out.append((role, person, t.get('name')))
    return out, lu.get('lineupType') == 'standard'


def run(dry=False):
    t = now()
    try:
        ms = matches_in_window(t)
    except Exception as e:
        return log('FotMob unavailable:', e)
    if not ms:
        return log('FotMob: no match in its window')
    st = load_state('fotmob.json', {'sent': []})
    sent = set(st['sent'])
    own = []                       # (league, {pid: player}) - your team in each league
    for lg in leagues():
        try:
            own.append((lg, roster(lg, lg['myTeamId'])['players']))
        except Exception as e:
            log(lg['name'], 'roster unavailable:', e)
    for m in ms:
        try:
            content = get('data/matchDetails?matchId=%s' % m['id']).get('content') or {}
        except Exception as e:
            log('FotMob match', m['id'], 'unavailable:', e)
            continue
        title = '%s v %s' % (m['home'], m['away'])
        people, confirmed = lineup_people(content)
        events = ((content.get('matchFacts') or {}).get('events') or {}).get('events') or []
        for lg, players in own:
            chat = lg.get('chatId')
            pre = '[%s] ' % lg['name'] if lg.get('shared') else ''
            # your players' FotMob ids in this match (substitution events carry no club)
            fid = {}
            for pid, p in players.items():
                for _, person, _ in people:
                    if same_player(person, p) and person.get('id'):
                        fid[str(person['id'])] = pid
            # line-ups: one message per match and league
            key = '%s|%s|lineup' % (m['id'], lg['leagueId'])
            if confirmed and key not in sent:
                lines = []
                for pid, p in players.items():
                    hit = [(role, person) for role, person, _ in people if same_player(person, p)]
                    if hit:
                        role, person = hit[0]
                        if role == 'starters':
                            lines.append('✅ %s starts' % esc(p['name']))
                        elif role == 'subs':
                            lines.append('🪑 %s on the bench' % esc(p['name']))
                        else:
                            u = person.get('unavailability') or {}
                            why = ', '.join(x for x in (u.get('type'), u.get('expectedReturn') and 'back ' + u['expectedReturn']) if x)
                            lines.append('❌ %s not available%s' % (esc(p['name']), ' (%s)' % esc(why) if why else ''))
                    elif m['club'] and any(same_club(x, p['club']) for x in (m['home'], m['away'])):
                        lines.append('❌ %s not in the squad' % esc(p['name']))
                sent.add(key)
                lines = list(dict.fromkeys(lines))
                if lines:
                    telegram('%s📋 <b>Line-ups: %s</b> (%s)\n%s' % (pre, esc(title), local_hm(m['kickoff']),
                                                                 '\n'.join(lines)), dry, chat)
            # injury substitutions
            for e in events:
                if e.get('type') != 'Substitution' or not e.get('injuredPlayerOut'):
                    continue
                swap = e.get('swap') or []
                off = swap[1] if len(swap) > 1 else {}
                pid = fid.get(str(off.get('id')))
                if not pid:
                    continue
                key = '%s|%s|inj|%s' % (m['id'], lg['leagueId'], off.get('id'))
                if key in sent:
                    continue
                sent.add(key)
                telegram("%s🚑 <b>%s forced off injured</b>, %s'\n<i>%s</i>" % (
                    pre, esc(players[pid]['name']), e.get('timeStr', e.get('time')), esc(title)), dry, chat)
    st['sent'] = sorted(sent)[-2000:]
    save_state('fotmob.json', st)
    log('FotMob: %d match(es) checked' % len(ms))


if __name__ == '__main__':
    run('--dry' in sys.argv)
