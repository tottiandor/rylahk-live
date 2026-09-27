# rylahk-live

Telegram feed for Totti's Fantrax leagues (live matchup scoring + injury news), running on
GitHub Actions in the public repo `tottiandor/rylahk-live`. Sister project of `../TottiHL`
(league history site) and `../gw-scout`; same Fantrax page API (`/fxpa/req`).

Rules:
- Read-only against Fantrax. Never set a line-up, make a claim, change a roster or a league setting.
- No Fantrax login: it works because the league is publicly viewable. If Fantrax starts
  answering with a pageError, that is the first thing to check (ask Totti, don't change it).
- The Telegram token lives only in GitHub secrets. Never write it to a file or print it.
  Totti sets it with `python setup_telegram.py` in his own terminal.
- The repo is public: nothing private (e-mails, recipients) goes in it.
- Join Fantrax and FPL players on ids (`state/injuries.json` idmap, `idmap_manual.json`), never
  on names at alert time.

Environment: Windows, `python`, `PYTHONUTF8=1`. Standard library only.

Test without a live game: `python live.py --replay 2` prints a round replayed as if live.
