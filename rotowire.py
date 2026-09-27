"""RotoWire's public Premier League news page - the source Fantrax's own news comes from.

One page, the latest 25 items, read at most once per injury run (robots.txt allows /soccer/).
Each item: RotoWire's news id, its player id and name, club code, headline, full text and
injury tag (GTD, OUT...). Only the date is on the page; the news id tells new from old.
"""
import html, re, urllib.request
from common import UA, norm

URL = 'https://www.rotowire.com/soccer/news.php?league=EPL'


def _text(s):
    return html.unescape(re.sub(r'<[^>]+>', '', s or '')).strip()


def latest():
    req = urllib.request.Request(URL, headers={'User-Agent': UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        page = r.read().decode('utf-8', 'ignore')
    items = []
    for blk in page.split('<div class="news-update ')[1:]:
        pl = re.search(r'href="/soccer/player/([^"]+)">([^<]+)</a>', blk)
        hd = re.search(r'news-update__headline" href="[^"]*-(\d+)">([^<]*)</a>', blk)
        if not pl or not hd:
            continue
        club = re.search(r'news-update__logo"[^>]*alt="([^"]*)"', blk)
        inj = re.search(r'news-update__inj">([^<]*)<', blk)
        news = re.search(r'news-update__news">(.*?)</div>', blk, re.S)
        items.append({'id': hd.group(1), 'player': pl.group(1), 'name': _text(pl.group(2)),
                      'club': club.group(1) if club else '', 'headline': _text(hd.group(2)),
                      'news': _text(news.group(1) if news else ''), 'inj': _text(inj.group(1) if inj else '')})
    return items


def match(item, players, rwmap):
    """The Fantrax id of this item's player among `players`, or None. RotoWire's player id
    is remembered once matched, so a name is only compared the first time."""
    if item['player'] in rwmap:
        pid = rwmap[item['player']]
        return pid if pid in players else None
    n = norm(item['name'])
    hit = [pid for pid, p in players.items() if norm(p['name']) == n]
    if not hit:
        last = n.split()[-1] if n else ''
        hit = [pid for pid, p in players.items()
               if norm(p['name']).split()[-1:] == [last] and p['club'] == item['club']]
    if len(hit) == 1:
        rwmap[item['player']] = hit[0]
        return hit[0]
    return None
