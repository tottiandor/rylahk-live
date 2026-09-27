# rylahk-live

Telegram feed for a Fantrax league, run by GitHub Actions: nothing needs to be on at home.

- **Live scores** (`live.py`, every 15 min, stays on during games): every change for your team
  and this round's opponent. Goals, assists, cards, penalties, own goals, clean sheets and
  goals conceded arrive instantly; everything else comes as a digest every 15 minutes and at
  every full time. Each message carries the matchup score, summed from Fantrax's own points.
- **Injuries** (`injuries.py`, every 10 min): FPL status/news and Fantrax injury flags for
  every player on a roster in the league.

Every Fantrax call is a read, with no login: the league must stay publicly viewable on Fantrax.

## Setup (once)
1. Send your bot any message in Telegram.
2. `python setup_telegram.py`: paste the token when asked. It saves the token and your chat
   id as hidden GitHub secrets and sends a test message.

## Test
Actions tab → **live** → Run workflow → replay `2` (tick *send* to get it in Telegram, marked TEST).

## Settings (`config.json`)
| | |
|---|---|
| `leagues` | leagues to watch: `leagueId`, `seasonId`, `myTeamId`. Add a block for each extra league |
| `instant` | categories sent at once; the rest go in the digest |
| `digestMinutes` | how often the digest goes out |
| `injuryScope` | `league` (every rostered player) or `mine` (you and this round's opponent) |

`state/` is the bot's memory (what it has already told you). It is committed by the bot; delete a
file there to make it start fresh.
