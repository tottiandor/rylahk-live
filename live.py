#!/usr/bin/env python3
"""Live matchup feed: while Premier League games are on, read Fantrax's live scoring once
a minute and tell Telegram what changed for your team and this round's opponent.

Two tiers:
  instant - goals, assists, cards, penalties, own goals, clean sheets won or lost,
            goals conceded: one message each, with the matchup score
  digest  - everything else (key passes, tackles, clearances...), rolled up every
            digestMinutes and at every full time

    python live.py                 watch now if a game is on or about to start, else exit
    python live.py --replay 2      replay round 2 as if it were live, print the messages
    python live.py --replay 2 --send   ...and send them to Telegram (marked TEST)

The score is Fantrax's own points summed, so it cannot drift from what Fantrax shows.
Every Fantrax call is a read.
"""
import argparse, random, sys, time
from datetime import datetime, timedelta, timezone
import fotmob
from common import (CFG, fantrax, fpl, roster, live_scores, telegram, esc, log, now,
                    local_hm, load_state, save_state, commit_state, leagues)

NAMES = {'G': 'GOAL', 'AT': 'ASSIST', 'PKD': 'PENALTY WON', 'PKS': 'PENALTY SAVED',
         'PKM': 'PENALTY MISSED', 'YC': 'YELLOW CARD', 'RC': 'RED CARD', 'OG': 'OWN GOAL',
         'CS': 'CLEAN SHEET', 'GAO': 'GOAL CONCEDED', 'GA': 'GOAL CONCEDED'}
EMOJI = {'G': '⚽', 'AT': '🅰️', 'PKD': '🎯', 'PKS': '🧤', 'PKM': '❌', 'YC': '🟨', 'RC': '🟥',
         'OG': '🙈', 'CS': '🧱', 'GAO': '🥅', 'GA': '🥅'}
INSTANT = set(CFG['instant'])


def fmt(x):
    x = round(x, 2)
    return ('%+g' % x) if x else '0'


def num(x):
    return '%g' % round(x, 2)


def game_text(g):
    return (g or '').split('|')[0].strip()


def game_final(g):
    t = game_text(g)
    return t.endswith(' F') or t.endswith(' FT')


class Matchup:
    """One league's matchup for one round, and what has already been told."""

    def __init__(self, league, st, dry):
        self.lg, self.st, self.dry = league, st, dry

    # ----- setup -----
    def prepare(self, period=None):
        lg, st = self.lg, self.st
        me = roster(lg, lg['myTeamId'], period)
        period = me['period']
        if st.get('period') != period:
            st.clear()
            st.update(period=period, prev={}, digest={}, games={}, lastDigest=None,
                      started=False, finalSent=False, announced=False)
        st['me'], st['opp'] = lg['myTeamId'], me['opp']
        st['teamNames'] = me['teams']
        st['labels'] = me['labels']
        names = {pid: {k: p[k] for k in ('short', 'club', 'pos')} for pid, p in me['players'].items()}
        if me['opp']:
            op = roster(lg, me['opp'], period)
            st['labels'].update(op['labels'])
            names.update({pid: {k: p[k] for k in ('short', 'club', 'pos')} for pid, p in op['players'].items()})
        st['names'] = names
        return period

    def send(self, text):
        telegram(text, self.dry is True, self.lg.get('chatId'))

    def who(self, tid):
        return self.st['teamNames'].get(tid, tid)

    def pname(self, pid):
        p = self.st['names'].get(pid)
        return '%s (%s)' % (p['short'], p['club']) if p else pid

    def score_line(self, totals):
        me, opp = self.st['me'], self.st['opp']
        return '📊 <b>%s</b> %s – %s <b>%s</b>' % (esc(self.who(me)), num(totals.get(me, 0)),
                                                  num(totals.get(opp, 0)), esc(self.who(opp)))

    def tag(self, tid):
        return '🟢' if tid == self.st['me'] else '🔴'

    # ----- one poll -----
    def step(self, snap, all_done, clock):
        st, lg = self.st, self.lg
        pre = '[%s] ' % lg['name'] if lg.get('shared') else ''
        pre = ('🧪 TEST ' if self.dry == 'test' else '') + pre
        sides = [t for t in (st['me'], st['opp']) if t]
        totals = {t: sum(v[1] for p in snap.get(t, {}).get('players', {}).values() for v in p.values())
                  for t in sides}

        if not st['announced']:
            st['announced'] = True
            if any(totals.values()):
                self.send(pre + '🔔 <b>Round %s</b> – joined mid-round\n%s' % (st['period'], self.score_line(totals)))
            else:
                self.send(pre + '🔔 <b>Round %s is on</b>\n🟢 %s vs 🔴 %s' % (
                    st['period'], esc(self.who(st['me'])), esc(self.who(st['opp']))))
            # joining late: take what is there as the starting point, don't replay it
            if any(totals.values()) and not st['prev']:
                st['prev'] = {t: snap[t]['players'] for t in sides if t in snap}

        instant, fts = [], []
        for tid in sides:
            prev = st['prev'].setdefault(tid, {})
            dig = st['digest'].setdefault(tid, {})
            for pid, stats in snap.get(tid, {}).get('players', {}).items():
                if pid not in st['names']:
                    self.prepare(st['period'])
                old = prev.get(pid, {})
                evs = []
                for scip, (av, fp) in stats.items():
                    oav, ofp = old.get(scip, [0, 0])
                    dav, dfp = av - oav, fp - ofp
                    if abs(dfp) < 1e-9:
                        continue          # minutes, games played, anything worth nothing
                    cat = st['labels'].get(scip, scip)
                    # a midfielder's 1-point clean sheet is not worth its own ping
                    big = cat != 'CS' or abs(dfp) >= 2
                    if cat in INSTANT and big and (dav > 0 or cat == 'CS'):
                        evs.append((cat, dav, dfp))
                    else:
                        d = dig.setdefault(pid, {}).setdefault(cat, [0, 0])
                        d[0] += dav
                        d[1] += dfp
                prev[pid] = stats
                if evs:
                    instant.append((tid, pid, evs, (snap[tid]['games'] or {}).get(pid)))
                g = (snap[tid]['games'] or {}).get(pid)
                if g:
                    gid = g.split('|')[1] if '|' in g else game_text(g)
                    was = st['games'].get(gid)
                    st['games'][gid] = g
                    if game_final(g) and not (was and game_final(was)):
                        fts.append(game_text(g))
            if any(abs(v) > 1e-9 for v in totals.values()):
                st['started'] = True

        for tid, pid, evs, g in instant:
            lines = []
            for cat, dav, dfp in evs:
                if cat == 'CS' and dav < 0:
                    lines.append('💥 <b>CLEAN SHEET LOST</b> %s' % fmt(dfp))
                else:
                    n = ' x%d' % dav if dav > 1 else ''
                    lines.append('%s <b>%s</b>%s %s' % (EMOJI.get(cat, '•'), NAMES.get(cat, cat), n, fmt(dfp)))
            txt = '%s%s %s\n%s' % (pre, self.tag(tid), esc(self.pname(pid)), '\n'.join(lines))
            if g:
                txt += '\n<i>%s</i>' % esc(game_text(g))
            self.send(txt + '\n' + self.score_line(totals))

        last = st['lastDigest']
        due = last is None or clock - last >= CFG['digestMinutes'] * 60
        if st['lastDigest'] is None:
            st['lastDigest'] = clock
        has = any(any(abs(c[1]) > 1e-9 for p in d.values() for c in p.values()) for d in st['digest'].values())
        if has and (due or fts or all_done):
            self.flush(totals, fts, pre, clock)
        elif fts:
            self.send(pre + '\n'.join('🏁 FT %s' % esc(f) for f in fts) + '\n' + self.score_line(totals))

        if all_done and st['started'] and not st['finalSent']:
            st['finalSent'] = True
            a, b = totals.get(st['me'], 0), totals.get(st['opp'], 0)
            res = 'WIN 🏆' if a > b else 'LOSS' if a < b else 'DRAW'
            self.send(pre + '🔚 <b>Round %s – all games done: %s</b>\n%s\n<i>Fantrax can still correct stats for a day or so.</i>'
                     % (st['period'], res, self.score_line(totals)))

    def flush(self, totals, fts, pre, clock):
        st = self.st
        out = ['%s🧾 <b>Update %s</b>' % (pre, local_hm(datetime.fromtimestamp(clock, timezone.utc)))]
        for tid in (st['me'], st['opp']):
            d = st['digest'].get(tid) or {}
            rows, tot = [], 0
            for pid, cats in sorted(d.items(), key=lambda kv: -sum(c[1] for c in kv[1].values())):
                parts = []
                for cat, (n, pts) in sorted(cats.items(), key=lambda kv: -abs(kv[1][1])):
                    if abs(pts) < 1e-9:
                        continue
                    parts.append('%s%s %s' % ('' if n == 1 else ('%g ' % n), cat, fmt(pts)))
                    tot += pts
                if parts:
                    rows.append('• %s: %s' % (esc(self.pname(pid)), ', '.join(parts)))
            if rows:
                out.append('%s <b>%s</b> %s' % (self.tag(tid), esc(self.who(tid)), fmt(tot)))
                out.extend(rows)
        out += ['🏁 FT %s' % esc(f) for f in fts]
        out.append(self.score_line(totals))
        self.send('\n'.join(out))
        st['digest'] = {}
        st['lastDigest'] = clock


# ---------- when to watch ----------

def fixtures():
    try:
        return [f for f in fpl('fixtures/') if f.get('kickoff_time')]
    except Exception as e:
        log('FPL fixtures unavailable:', e)
        return None


def window_open(fx, t):
    if fx is None:
        return True                     # cannot tell: better to watch than miss
    b = timedelta(minutes=CFG['windowBeforeKickoffMinutes'])
    a = timedelta(minutes=CFG['windowAfterKickoffMinutes'])
    for f in fx:
        k = datetime.fromisoformat(f['kickoff_time'].replace('Z', '+00:00'))
        if k - b <= t <= k + a:
            return True
    return False


def watch():
    fx = fixtures()
    if not window_open(fx, now()):
        log('no game on or about to start; nothing to do')
        return
    state = load_state('live.json', {})
    ms = []
    for lg in leagues():
        m = Matchup(lg, state.setdefault(lg['key'], {}), dry=False)
        m.prepare()
        ms.append(m)
        log(lg['name'], 'round', m.st['period'], m.who(m.st['me']), 'vs', m.who(m.st['opp']))
    t0 = last_commit = time.time()
    last_fotmob = 0
    while True:
        for m in ms:
            try:
                snap, done = live_scores(m.lg, m.st['period'])
                m.step(snap, done, time.time())
            except Exception as e:
                log(m.lg['name'], 'poll failed:', e)
        if time.time() - last_fotmob > 120:        # line-ups and injury substitutions
            try:
                fotmob.run()
            except Exception as e:
                log('FotMob check failed:', e)
            last_fotmob = time.time()
        save_state('live.json', state)
        if time.time() - last_commit > 600:
            commit_state('live state')
            last_commit = time.time()
        if time.time() - t0 > CFG['maxRunMinutes'] * 60:
            log('run time is up; the next scheduled run carries on')
            break
        if not window_open(fx, now()):
            log('games over for now')
            break
        time.sleep(CFG['pollSeconds'])
    commit_state('live state')


# ---------- replay a finished round as if it were live ----------

def replay(period, steps, send, seed=7):
    lg = leagues()[0]
    m = Matchup(lg, {}, dry='test' if send else True)
    m.prepare(period)
    final, _ = live_scores(lg, period)
    sides = [m.st['me'], m.st['opp']]
    rnd = random.Random(seed)
    # every unit of every stat lands at a random minute; a game ends after its last event
    when = {}
    gend = {}
    for tid in sides:
        for pid, stats in final[tid]['players'].items():
            for scip, (av, fp) in stats.items():
                if abs(fp) < 1e-9 or av <= 0:
                    continue
                when[(tid, pid, scip)] = sorted(rnd.randint(1, steps - 2) for _ in range(int(round(av))))
                g = final[tid]['games'].get(pid, '')
                gend[g] = max(gend.get(g, 0), when[(tid, pid, scip)][-1] + 1)
    clock = 1_790_000_000
    for s in range(0, steps + 1):
        snap = {}
        for tid in sides:
            pl = {}
            for pid, stats in final[tid]['players'].items():
                cur = {}
                for scip, (av, fp) in stats.items():
                    ts = when.get((tid, pid, scip))
                    if ts is None:
                        cur[scip] = [0, 0]
                    else:
                        n = sum(1 for x in ts if x <= s)
                        cur[scip] = [n, fp * n / len(ts)]
                pl[pid] = cur
            games = {}
            for pid, g in final[tid]['games'].items():
                games[pid] = g if s >= gend.get(g, 0) else g.replace(' F|', " 60'|")
            snap[tid] = {'players': pl, 'games': games}
        m.step(snap, s == steps, clock)
        clock += 180                    # three minutes of football per step
    tot = {t: final[t]['total'] for t in sides}
    log('Fantrax final totals for comparison:', tot)


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--replay', type=int)
    ap.add_argument('--steps', type=int, default=40)
    ap.add_argument('--send', action='store_true')
    a = ap.parse_args()
    if a.replay:
        replay(a.replay, a.steps, a.send)
    else:
        watch()
