#!/usr/bin/env python3
"""One-time: connect your Telegram bot to the GitHub repo.

Run it yourself in a terminal:   python setup_telegram.py

It takes the bot token from your clipboard (copy it first), finds your chat with
the bot, sends a test message, and stores both as hidden GitHub secrets. The token is
never written to a file.
Before running: open your bot in Telegram and send it any message, e.g. "hi".
"""
import json, subprocess, sys, urllib.request

REPO = 'tottiandor/rylahk-live'


def api(tok, method, data=None):
    req = urllib.request.Request('https://api.telegram.org/bot%s/%s' % (tok, method),
                                 data=json.dumps(data).encode() if data else None,
                                 headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode())


def from_clipboard():
    try:
        r = subprocess.run(['powershell', '-NoProfile', '-Command', 'Get-Clipboard'],
                           capture_output=True, text=True, timeout=15)
        s = r.stdout.strip()
        return s if ':' in s and ' ' not in s and len(s) > 30 else None
    except Exception:
        return None


tok = from_clipboard()
if tok and input('Found a bot token on the clipboard (ending ...%s). Use it? [Y/n] ' % tok[-4:]).strip().lower() in ('', 'y', 'yes'):
    pass
else:
    # visible on purpose: pasting into a hidden prompt fails in some Windows terminals
    tok = input('Paste the bot token (right-click to paste), then press Enter: ').strip()
try:
    me = api(tok, 'getMe')['result']
except Exception:
    sys.exit('That token did not work. Copy it again from BotFather.')
ups = api(tok, 'getUpdates').get('result', [])
chats = [u['message']['chat'] for u in ups if 'message' in u and u['message']['chat']['type'] == 'private']
if not chats:
    sys.exit('No message found. Open @%s in Telegram, send it "hi", then run this again.' % me['username'])
chat = chats[-1]['id']
api(tok, 'sendMessage', {'chat_id': chat, 'text': '✅ rylahk-live is connected. Live scores and injury news will arrive here.'})
for name, val in (('TELEGRAM_BOT_TOKEN', tok), ('TELEGRAM_CHAT_ID', str(chat))):
    r = subprocess.run(['gh', 'secret', 'set', name, '--repo', REPO], input=val, text=True,
                       capture_output=True)
    if r.returncode:
        sys.exit('Could not save %s on GitHub: %s' % (name, r.stderr.strip()))
print('Done. @%s sent you a test message, and both secrets are saved on GitHub.' % me['username'])
