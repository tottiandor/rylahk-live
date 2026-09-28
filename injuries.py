#!/usr/bin/env python3
"""Injury and availability feed for players on the league's rosters.

Two sources, checked every run (GitHub runs it every ~10 minutes):
  Fantrax - its injury flags (game-time decision, out, suspended) and RotoWire news.
            The fast one: it had Gakpo and Dorgu hours before FPL.
  FPL     - the official status and chance of playing; slower, lands after the club confirms.

FPL has its own player ids, so each Fantrax player is matched to one FPL player by club
and name. Matches are kept in state/idmap.json; anything it cannot match is listed in the
first message and can be fixed by hand in idmap_manual.json ({"fantraxId": fplId}).

The first run only records what is there and says so; after that, only changes are sent.

    python injuries.py          check now
    python injuries.py --dry    print instead of sending
"""
import json, os, sys
from common import (CFG, HERE, fpl, roster, fantrax, telegram, esc, log, norm, now,
                    load_state, save_state, commit_state, leagues)
import rotowire, fotmob

# Fantrax icon types: 32 "expected to play", 35 "on the trade block" - never injury news.
# 14 is injury news; 8 and 9 are general news, kept only when about fitness.
# Anything else (1 game-time decision, 30 out, 6 suspended...) is a status flag.
NOT_STATUS = {'8', '9', '14', '32', '35'}
FIT_WORDS = ('injur', 'train', 'fitness', 'doubt', 'knock', 'miss', 'strain', 'hamstring',
             'muscle', ' ill', 'forced off', 'withdr', 'scan', 'surgery', 'return', 'available',
             'suspen', 'sidelined', 'questionable', 'uncertain', 'setback', 'absence', 'absent')
STATUS = {'a': 'available', 'd': 'doubtful', 'i': 'injured', 's': 'suspended',
          'u': 'unavailable', 'n': 'not eligible'}


def league_rosters(lg, scope):
    """The players to watch, with the team each is on. Only the teams the scope needs are
    read: big leagues have 144 teams. In leagues split into divisions the same player is
    on one team per division, so your own team always wins the tie."""
    me = roster(lg, lg['myTeamId'])
    want = {'own': [lg['myTeamId']], 'mine': [lg['myTeamId'], me['opp']]}.get(scope, list(me['teams']))
    out = {}
    for tid in reversed([t for t in want if t]):
        r = me if tid == lg['myTeamId'] else roster(lg, tid)
        for pid, p in r['players'].items():
            out[pid] = dict(p, team=tid)
    return out, me


def match_fpl(players, boot, idmap, manual):
    alias = CFG.get('fplClubAlias', {})
    club_id = {t['short_name']: t['id'] for t in boot['teams']}
    by_club = {}
    for e in boot['elements']:
        by_club.setdefault(e['team'], []).append(e)
    unmatched = []
    for pid, p in players.items():
        if pid in manual:
            idmap[pid] = manual[pid]
            continue
        if pid in idmap:
            continue
        cands = by_club.get(club_id.get(alias.get(p['club'], p['club'])), [])
        full = norm(p['name'])
        hit = [e for e in cands if norm(e['first_name'] + ' ' + e['second_name']) == full]
        if not hit:
            last = full.split()[-1] if full else ''
            hit = [e for e in cands if norm(e['second_name']).split()[-1:] == [last]]
        if not hit:
            hit = [e for e in cands if norm(e['web_name']) in full or full in norm(e['web_name'])]
        if len(hit) == 1:
            idmap[pid] = hit[0]['id']
        else:
            unmatched.append('%s (%s)' % (p['name'], p['club']))
    return unmatched


def fpl_view(e):
    return {'status': e['status'], 'chance': e['chance_of_playing_next_round'], 'news': e['news']}


def fx_view(p):
    status = sorted(t for k, t in p['icons'] if k not in NOT_STATUS)
    news = sorted(t for k, t in p['icons'] if k == '14' or (k in ('8', '9') and any(w in t.lower() for w in FIT_WORDS)))
    return {'status': status, 'news': news}


def main(dry):
    st = load_state('injuries.json', {})
    manual_p = os.path.join(HERE, 'idmap_manual.json')
    manual = json.load(open(manual_p, encoding='utf-8')) if os.path.exists(manual_p) else {}
    boot = fpl('bootstrap-static/')
    els = {e['id']: e for e in boot['elements']}
    try:
        rw_items = rotowire.latest()
    except Exception as e:
        log('RotoWire unavailable:', e)
        rw_items = None
    for lg in leagues():
        s = st.setdefault(lg['key'], {'fpl': {}, 'fx': {}, 'idmap': {}, 'ready': False})
        players, me = league_rosters(lg, CFG.get('injuryScope', 'own'))
        mine, opp = lg['myTeamId'], me['opp']
        unmatched = match_fpl(players, boot, s['idmap'], manual)
        pre = '[%s] ' % lg['name'] if lg.get('shared') else ''
        msgs = []
        lag = s.setdefault('lag', {})       # which source showed a player's story first
        stamp = now().strftime('%Y-%m-%d %H:%M')

        def heads(pid, p):
            owner = me['teams'].get(p['team'], '')
            tag = '🟢 yours' if p['team'] == mine else '🔴 opponent' if p['team'] == opp else esc(owner)
            return '<b>%s</b> (%s, %s) · %s' % (esc(p['name']), p['club'], p['pos'], tag)

        # RotoWire first: it is where Fantrax's news comes from
        if rw_items is not None:
            rw = s.setdefault('rw', {'seen': None, 'map': {}})
            first = rw['seen'] is None
            seen = set(rw['seen'] or [])
            for it in reversed(rw_items):          # oldest first, so messages arrive in order
                if it['id'] in seen:
                    continue
                seen.add(it['id'])
                pid = rotowire.match(it, players, rw['map'])
                if first or not pid:
                    continue
                inj = ' [%s]' % esc(it['inj']) if it['inj'] else ''
                msgs.append('📡 %s\nRotoWire: <b>%s</b>%s\n%s' % (heads(pid, players[pid]), esc(it['headline']),
                                                                inj, esc(it['news'])))
                lag[pid] = {'what': it['headline'], 'rw': stamp}
                s.setdefault('rwSent', {})[pid] = now().timestamp()
            rw['seen'] = sorted(seen, key=int)[-200:]
        for pid, p in players.items():
            head = heads(pid, p)
            # FPL
            fid = s['idmap'].get(pid)
            if fid in els:
                new, old = fpl_view(els[fid]), s['fpl'].get(pid)
                if s['ready'] and old is not None and new != old:
                    if new['status'] == 'a' and not new['news']:
                        msgs.append('✅ %s\nFPL: back to available (was: %s)' % (head, esc(old['news'] or STATUS.get(old['status']))))
                    else:
                        ch = '' if new['chance'] is None or '%' in new['news'] else ' · %s%% to play' % new['chance']
                        icon = '🚑' if new['status'] in 'iu' else '🟧' if new['status'] == 'd' else '🟥'
                        msgs.append('%s %s\nFPL: %s%s' % (icon, head, esc(new['news'] or STATUS.get(new['status'])), ch))
                s['fpl'][pid] = new
            # Fantrax
            new, old = fx_view(p), s['fx'].get(pid)
            if isinstance(old, list):          # state from before news was tracked
                old = {'status': old, 'news': new['news']}
            if s['ready'] and old is not None and new != old:
                fresh = [n for n in new['news'] if n not in old['news']]
                if fresh:
                    e = lag.get(pid)
                    if e and 'rw' in e and 'fx' not in e:
                        e['fx'] = stamp
                    else:
                        lag[pid] = {'what': fresh[0][:60], 'fx': stamp}
                # RotoWire ran the story first (by ~1h44 on Kostoulas): Fantrax only
                # speaks when RotoWire has said nothing about this player for 12 hours
                covered = now().timestamp() - s.get('rwSent', {}).get(pid, 0) < 12 * 3600
                if (new['status'] != old['status'] or fresh) and not covered:
                    if new['status']:
                        body = '🩹 %s\nFantrax: %s' % (head, esc(' / '.join(new['status'])))
                    elif old['status']:
                        body = '✅ %s\nFantrax: injury flag cleared' % head
                    else:
                        body = '📰 %s' % head
                    body += ''.join('\n📰 %s' % esc(n) for n in fresh)
                    msgs.append(body)
            s['fx'][pid] = new
        # players who left every roster are forgotten, so a re-signing starts clean
        for k in ('fpl', 'fx'):
            for pid in list(s[k]):
                if pid not in players:
                    del s[k][pid]
        if not s['ready']:
            s['ready'] = True
            txt = '%s🩺 <b>Injury watch is on</b> for %d players.' % (pre, len(players))
            flagged = [(p, fx_view(p)) for p in players.values()]
            flagged = [(p, v) for p, v in flagged if v['status']]
            if flagged:
                txt += '\nFlagged right now:'
                for p, v in flagged:
                    txt += '\n🩹 <b>%s</b> (%s): %s' % (esc(p['name']), p['club'], esc(' / '.join(v['status'])))
            if unmatched:
                txt += '\nNot matched to FPL (Fantrax flags still work): ' + esc(', '.join(sorted(unmatched)))
            telegram(txt, dry, lg.get('chatId'))
        for m in msgs:
            telegram(pre + m, dry, lg.get('chatId'))
        log(lg['name'], len(players), 'players,', len(msgs), 'changes,', len(unmatched), 'unmatched')
    save_state('injuries.json', st)
    # FotMob line-ups and injury substitutions - but during Premier League games live.py
    # does it every few minutes, and two runs at once would double the messages
    import live
    if not live.window_open(live.fixtures(), now()):
        try:
            fotmob.run(dry)
        except Exception as e:
            log('FotMob check failed:', e)
    commit_state('injury state')


if __name__ == '__main__':
    main('--dry' in sys.argv)
