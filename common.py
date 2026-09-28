"""Shared plumbing: config, state files, Fantrax reads, FPL reads, Telegram, git.

Every Fantrax call here is a read. Nothing sets a line-up, makes a claim or changes a
setting, and nothing ever should. The league has to be publicly viewable, because the
bot runs on GitHub's servers with no Fantrax login.
"""
import json, os, re, subprocess, sys, time, urllib.request, urllib.error, unicodedata
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
CFG = json.load(open(os.path.join(HERE, 'config.json'), encoding='utf-8'))
STATE_DIR = os.path.join(HERE, 'state')
UA = 'Mozilla/5.0 (rylahk-live; read-only)'

try:
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo(CFG.get('timezone', 'Europe/Budapest'))
except Exception:                       # Windows without tzdata: fall back to UTC
    TZ = timezone.utc


def now():
    return datetime.now(timezone.utc)


def local_hm(dt=None):
    return (dt or now()).astimezone(TZ).strftime('%H:%M')


def log(*a):
    print(now().strftime('%H:%M:%S'), *a, flush=True)


# ---------- state ----------

def load_state(name, default):
    p = os.path.join(STATE_DIR, name)
    if os.path.exists(p):
        with open(p, encoding='utf-8') as f:
            return json.load(f)
    return default


def save_state(name, obj):
    os.makedirs(STATE_DIR, exist_ok=True)
    p = os.path.join(STATE_DIR, name)
    tmp = p + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(obj, f, ensure_ascii=False, indent=1, sort_keys=True)
    os.replace(tmp, p)


def commit_state(msg='state'):
    """On GitHub only (COMMIT_STATE=1): save state/ back to the repo so the next run
    carries on from here instead of alerting everything again."""
    if os.environ.get('COMMIT_STATE') != '1':
        return
    def git(*a):
        return subprocess.run(['git', *a], cwd=HERE, capture_output=True, text=True)
    git('add', 'state')
    if git('diff', '--cached', '--quiet').returncode == 0:
        return
    git('commit', '-q', '-m', msg)
    for _ in range(4):
        if git('push', '-q').returncode == 0:
            return
        # another workflow pushed meanwhile; replay ours on top, and on a clash in a
        # state file keep this run's version (-X theirs = the commit being replayed)
        if git('pull', '-q', '--rebase', '-X', 'theirs').returncode:
            git('rebase', '--abort')
        time.sleep(3)
    log('WARNING: could not push state')


# ---------- http ----------

def _open(req, tries=4):
    last = None
    for i in range(tries):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read().decode('utf-8'))
        except Exception as e:
            last = e
            time.sleep(3 * (i + 1))
    # the Telegram URL carries the bot token: never let it reach a log
    url = re.sub(r'/bot[^/]+/', '/bot<token>/', req.full_url)
    raise RuntimeError('request failed: %s (%s)' % (url, last))


def fantrax(method, league_id, ok=None, **data):
    """One read against Fantrax's own page API. A rate-limited answer comes back empty
    rather than as an error, so it is only accepted once `ok` says it holds the goods."""
    body = json.dumps({'msgs': [{'method': method, 'data': dict(leagueId=league_id, **data)}],
                       'uiv': 3}).encode()
    for i in range(4):
        req = urllib.request.Request('https://www.fantrax.com/fxpa/req?leagueId=' + league_id,
                                     data=body, headers={'Content-Type': 'application/json',
                                                         'User-Agent': UA})
        j = _open(req)
        if (j.get('pageError') or {}).get('code'):
            raise RuntimeError('Fantrax said %s - is the league still public?' % j['pageError'])
        d = (j.get('responses') or [{}])[0].get('data')
        if d and (not ok or ok(d)):
            time.sleep(0.4)
            return d
        time.sleep(2 * (i + 1))
    raise RuntimeError('Fantrax %s gave nothing back' % method)


def fpl(path):
    req = urllib.request.Request('https://fantasy.premierleague.com/api/' + path,
                                 headers={'User-Agent': UA})
    return _open(req)


# ---------- Fantrax helpers ----------

def roster(league, team_id, period=None):
    """Names, clubs, positions and slot of one fantasy team, plus its opponent and the
    round Fantrax is showing (the current one when period is None)."""
    kw = dict(teamId=team_id)
    if period is not None:
        kw.update(period=period, view='STATS', seasonOrProjection='SEASON_%d_BY_PERIOD' % league['seasonId'],
                  timeframeTypeCode='BY_PERIOD')
    d = fantrax('getTeamRosterInfo', league['leagueId'], ok=lambda d: d.get('tables'), **kw)
    players = {}
    labels = {}
    for tb in d['tables']:
        for c in (tb.get('header') or {}).get('cells', []):
            if c.get('scipId'):
                labels[c['scipId']] = c.get('shortName') or c.get('name')
        for row in tb.get('rows', []):
            sc = row.get('scorer')
            if not sc or not sc.get('scorerId'):
                continue                  # an empty slot
            players[sc['scorerId']] = {
                'name': sc.get('name'), 'short': sc.get('shortName') or sc.get('name'),
                'club': sc.get('teamShortName'), 'pos': sc.get('posShortNames'),
                'slot': row.get('statusId'),
                'icons': [[str(i.get('typeId')), i.get('tooltip', '')] for i in sc.get('icons', [])]}
    teams = {t['id']: t['name'] for t in d.get('fantasyTeams', [])}
    opp = (d.get('periodOppnentTeamIds') or [None])[0]
    period = (d.get('displayedSelections') or {}).get('displayedPeriod', period)
    return {'players': players, 'labels': labels, 'teams': teams, 'opp': opp, 'period': period}


def live_scores(league, period):
    """Every team's active players for one round: per player, per stat id, [count, points]."""
    d = fantrax('getLiveScoringStats', league['leagueId'],
                ok=lambda d: (d.get('statsPerTeam') or {}).get('allTeamsStats'),
                period=period, teamId='ALL', view='MATCHUP')
    out = {}
    for tid, t in d['statsPerTeam']['allTeamsStats'].items():
        a = t.get('ACTIVE') or {}
        pl = {}
        for pid, v in (a.get('statsMap') or {}).items():
            if pid.startswith('_'):
                continue
            pl[pid] = {x['scipId']: [x.get('av') or 0, x.get('fpts') or 0] for x in v.get('object2', [])}
        out[tid] = {'players': pl, 'total': a.get('totalFpts'),
                    'games': a.get('gameStatusMap') or {}}
    return out, bool(d.get('allEventsFinished'))


# ---------- Telegram ----------

def telegram(text, dry=False, chat=None):
    """Send to `chat` (a league's own chat), or to Totti's private chat by default."""
    if dry or not os.environ.get('TELEGRAM_BOT_TOKEN'):
        print('\n----- telegram -----\n' + text + '\n--------------------', flush=True)
        return
    tok = os.environ['TELEGRAM_BOT_TOKEN']
    chat = chat or os.environ['TELEGRAM_CHAT_ID']
    body = json.dumps({'chat_id': chat, 'text': text, 'parse_mode': 'HTML',
                       'disable_web_page_preview': True}).encode()
    req = urllib.request.Request('https://api.telegram.org/bot%s/sendMessage' % tok, data=body,
                                 headers={'Content-Type': 'application/json'})
    try:
        _open(req, tries=3)
    except RuntimeError as e:
        # never print the URL: it carries the token
        log('telegram send failed:', str(e).split('(')[-1])


def tg_api(method, **data):
    tok = os.environ['TELEGRAM_BOT_TOKEN']
    req = urllib.request.Request('https://api.telegram.org/bot%s/%s' % (tok, method),
                                 data=json.dumps(data).encode(),
                                 headers={'Content-Type': 'application/json'})
    return _open(req, tries=2)


def leagues():
    """The leagues to watch: config.json's, plus those added from Telegram with /add
    (state/leagues.json). An /add of a league already in config.json only moves its chat
    or changes the team, so its memory in state/ carries on."""
    out = [dict(lg) for lg in CFG['leagues']]
    for a in load_state('leagues.json', {}).get('leagues', []):
        same = [lg for lg in out if lg['leagueId'] == a['leagueId']]
        if same:
            same[0].update({k: a[k] for k in ('chatId', 'myTeamId', 'seasonId') if k in a})
        else:
            out.append(dict(a))
    chats = [lg.get('chatId') for lg in out]
    for lg in out:
        lg['shared'] = chats.count(lg.get('chatId')) > 1
    return out


def esc(s):
    return str(s).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


def norm(s):
    s = unicodedata.normalize('NFKD', s or '').encode('ascii', 'ignore').decode()
    return ' '.join(s.lower().replace('-', ' ').replace("'", '').replace('.', '').split())
