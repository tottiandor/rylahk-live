#!/usr/bin/env python3
"""Injury and availability feed for players on the league's rosters.

Two sources, checked every run (GitHub runs it every ~10 minutes):
  FPL     - the official Fantasy Premier League status, chance of playing and news line.
            Clubs' own updates land here within minutes of a press conference.
  Fantrax - its own injury flags (game-time decision, out...) and injury news.

FPL has its own player ids, so each Fantrax player is matched to one FPL player by club
and name. Matches are kept in state/idmap.json; anything it cannot match is listed in the
first message and can be fixed by hand in idmap_manual.json ({"fantraxId": fplId}).

The first run only records what is there and says so; after that, only changes are sent.

    python injuries.py          check now
    python injuries.py --dry    print instead of sending
"""
import json, os, sys
from common import (CFG, HERE, fpl, roster, fantrax, telegram, esc, log, norm,
                    load_state, save_state, commit_state)

ROUTINE_ICONS = {'8', '9', '32'}    # match reports and "expected to play": not injury news
STATUS = {'a': 'available', 'd': 'doubtful', 'i': 'injured', 's': 'suspended',
          'u': 'unavailable', 'n': 'not eligible'}


def league_rosters(lg):
    """Every player on every team in the league, with the team he is on."""
    me = roster(lg, lg['myTeamId'])
    out = {}
    for tid in me['teams']:
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
    return sorted(t for k, t in p['icons'] if k not in ROUTINE_ICONS)


def main(dry):
    st = load_state('injuries.json', {})
    manual_p = os.path.join(HERE, 'idmap_manual.json')
    manual = json.load(open(manual_p, encoding='utf-8')) if os.path.exists(manual_p) else {}
    boot = fpl('bootstrap-static/')
    els = {e['id']: e for e in boot['elements']}
    for lg in CFG['leagues']:
        s = st.setdefault(lg['key'], {'fpl': {}, 'fx': {}, 'idmap': {}, 'ready': False})
        players, me = league_rosters(lg)
        mine, opp = lg['myTeamId'], me['opp']
        if CFG.get('injuryScope') == 'mine':
            players = {k: v for k, v in players.items() if v['team'] in (mine, opp)}
        unmatched = match_fpl(players, boot, s['idmap'], manual)
        pre = '[%s] ' % lg['name'] if len(CFG['leagues']) > 1 else ''
        msgs = []
        for pid, p in players.items():
            owner = me['teams'].get(p['team'], '')
            tag = '🟢 yours' if p['team'] == mine else '🔴 opponent' if p['team'] == opp else esc(owner)
            head = '<b>%s</b> (%s, %s) · %s' % (esc(p['name']), p['club'], p['pos'], tag)
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
            if s['ready'] and old is not None and new != old:
                if new:
                    msgs.append('🩹 %s\nFantrax: %s' % (head, esc(' / '.join(new))))
                else:
                    msgs.append('✅ %s\nFantrax: injury flag cleared' % head)
            s['fx'][pid] = new
        # players who left every roster are forgotten, so a re-signing starts clean
        for k in ('fpl', 'fx'):
            for pid in list(s[k]):
                if pid not in players:
                    del s[k][pid]
        if not s['ready']:
            s['ready'] = True
            txt = '%s🩺 <b>Injury watch is on</b>: %d players tracked across the league.' % (pre, len(players))
            if unmatched:
                txt += '\nNot matched to FPL (Fantrax flags still work): ' + esc(', '.join(sorted(unmatched)))
            telegram(txt, dry)
        for m in msgs:
            telegram(pre + m, dry)
        log(lg['name'], len(players), 'players,', len(msgs), 'changes,', len(unmatched), 'unmatched')
    save_state('injuries.json', st)
    commit_state('injury state')


if __name__ == '__main__':
    main('--dry' in sys.argv)
