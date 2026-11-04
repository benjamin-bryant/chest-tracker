<p align="center">
  <img src="docs/images/banner.png" alt="Chest Tracker: a scoreboard for every Fortnite chest your group really opens" width="100%">
</p>

<p align="center">
  <a href="../../releases/latest/download/Chest-Tracker.exe"><b>Download for Windows</b></a>
  &nbsp;&nbsp;|&nbsp;&nbsp;
  <a href="#how-a-chest-gets-counted">How counting works</a>
  &nbsp;&nbsp;|&nbsp;&nbsp;
  <a href="#set-up-the-discord-bot">Set up the Discord bot</a>
</p>

<p align="center">
  <a href="https://github.com/Xxoris190/chest-tracker/actions/workflows/tests.yml"><img alt="Tests" src="https://github.com/Xxoris190/chest-tracker/actions/workflows/tests.yml/badge.svg"></a>
  <img alt="Windows 10 and 11" src="https://img.shields.io/badge/Windows-10%20%7C%2011-2A1C3A?style=flat-square">
  <img alt="Python 3.12" src="https://img.shields.io/badge/Python-3.12-2A1C3A?style=flat-square">
  <img alt="MIT license" src="https://img.shields.io/badge/License-MIT-F4B42A?style=flat-square">
</p>

Chest Tracker counts the Fortnite chests you and your friends open and keeps a shared leaderboard in Discord. It's a single download with nothing to install, and it runs quietly in your system tray while you play.

<p align="center">
  <img src="docs/images/demo.gif" alt="The app's chest lifting its lid during a search, slamming shut on a cancel, then bursting with coins when a chest counts" width="85%">
</p>

<sub>Screenshots and this animation show sample data.</sub>

## What it does

- **Counts real searches.** Cancelled searches and chests you only walk past don't count.
- **Shows it live.** The chest on screen lifts its lid during a search, bursts open when a chest counts, and slams shut on a cancel.
- **Keeps a group leaderboard.** A Discord bot totals everyone's chests and answers `/chests` and `/leaderboard`.
- **Doesn't lose chests.** If Discord can't be reached, chests are saved and sent later, and the bot catches up after being offline.
- **Works with your binds.** Use any key, a mouse button, or the scroll wheel to interact.
- **Finds your screen.** It follows Fortnite to whichever monitor it's on.

## Get started

1. Download [`Chest-Tracker.exe`](../../releases/latest/download/Chest-Tracker.exe) and double-click it.
2. If Windows shows "Windows protected your PC", select **More info**, then **Run anyway**. It appears because the app isn't signed with a paid certificate.
3. Open **Setup** and go through the four steps:
   1. **Your name:** how you appear on the leaderboard.
   2. **Discord channel link:** the webhook URL your group shares. In Discord, open the channel's **Edit Channel > Integrations > Webhooks**.
   3. **Interact key:** click the button, then press the key or scroll the wheel the way you open chests.
   4. **Chest prompt:** set Fortnite to **Windowed Fullscreen**, select **Set up prompt**, look at a chest, and press **F8**. Drag a box around the prompt to zoom in, then drag a tight box around only the word **SEARCH**.
4. Play. The app starts watching whenever Fortnite is the active window.

Closing the window keeps it counting from the system tray. To exit, right-click the tray icon and select **Quit**.

<p align="center">
  <img src="docs/images/setup.png" alt="The Setup page with four completed steps and a live prompt match bar" width="49%">
  <img src="docs/images/group.png" alt="The Group page with a leaderboard of four sample players" width="49%">
</p>

## How a chest gets counted

Fortnite has no official way to report chest opens, so the app works it out from the screen. A chest counts about 3 seconds after you search it, once all of these are true:

1. The SEARCH prompt was on screen and you used your interact key.
2. You kept aiming at the chest for the first second.
3. The prompt stayed gone for the full 3 seconds.

If the prompt comes back, or you look away during that first second, the search is cancelled. Repeating that doesn't add anything.

Counted one by mistake? Press **F10** in game, or select **Remove last chest**. Players can't add chests by hand, but someone with Manage Server can use `/add` in Discord.

### Known limits

The app can't see inside the game, so these rules are strong evidence rather than proof.

- Holding your aim past the first second and then cancelling could still count.
- Opening a second chest within 3 seconds can make the first one read as cancelled.
- The prompt only says SEARCH, so anything else with the same prompt, possibly including ammo boxes, can count.
- Anyone with the webhook URL could post to the channel directly, so only share it with people you trust.
- Controllers aren't supported yet.

## Set up the Discord bot

Only one person in your group needs to do this. Everyone else only needs the shared webhook URL.

| Discord command | What it does |
| --- | --- |
| `/chests` | Everyone's total and today's count. Add a player name to see just theirs. |
| `/leaderboard` | Top 10 players |
| `/add` | Corrects a player's count (Manage Server only) |
| `/reset` | Sets a player's count to 0 (Manage Server only) |

1. Go to the [Discord Developer Portal](https://discord.com/developers/applications), select **New Application**, then open **Bot**.
2. Select **Reset Token** and copy the token. Keep it secret, because anyone who has it controls your bot.
3. On the same page, turn on **Message Content Intent**.
4. Under **OAuth2 > URL Generator**, tick the scopes `bot` and `applications.commands`, and the permissions **View Channels**, **Read Message History** and **Send Messages**. Open the generated link and add the bot to your server.
5. Create a channel just for this, such as `#chest-log`, and make sure the bot can see it.
6. In Discord, turn on **User Settings > Advanced > Developer Mode**, then right-click the channel and select **Copy Channel ID**.
7. In the app's **Group** page, select **Show bot connection**, paste the token and channel ID, and select **Start bot**.

## Privacy and anti-cheat

**What the app does:** it takes screenshots while Fortnite is the active window and reads which keys and scroll input you use. It doesn't touch Fortnite's memory or files, and it doesn't press anything for you.

**What it sends:** only your player name and chest count, to the Discord channel you choose. Screenshots stay in memory and are thrown away. The only image saved is the small prompt picture you select, on your own PC.

**Where your data lives:** your settings, webhook URL and bot token are in `%APPDATA%\ChestTracker`, not in the `.exe`.

> [!WARNING]
> This is much lower risk than anything that modifies the game, but nobody can promise it's allowed under Epic's rules. Use it at your own risk.

## Troubleshooting

**Chests aren't being counted.** Open **Setup > Chest prompt** and watch **Live match** while you look at a chest in Fortnite.

- **The bar stays below the marker:** set up the prompt again. You can save several, such as one in a dark area. Remove old ones after changing resolution or HUD scale.
- **The status says "Can't see the game":** use Windowed Fullscreen, and try turning HDR off.
- **The bar passes but nothing counts:** check that **Interact key** matches your Fortnite binding.

**Real chests show "Search cancelled".** Keep aiming at the chest for a second after searching.

**It counts when no prompt is showing.** Drag the **Live match** marker up to about 0.85 or 0.9.

**Something else is wrong.** Check the log at `%APPDATA%\ChestTracker\chest-tracker.log`. It's useful to include in a bug report, and it doesn't contain your token or webhook URL.

## Build from source

You need Windows and Python 3.12.

```bash
python -m venv .venv-app
```

```bash
.venv-app\Scripts\python.exe -m pip install -r requirements-dev.txt
```

Run the app:

```bash
.venv-app\Scripts\python.exe app.py
```

Run the tests:

```bash
.venv-app\Scripts\python.exe -m pytest
```

Build `Chest Tracker.exe`:

```bash
powershell -ExecutionPolicy Bypass -File build.ps1
```

| File | What it holds |
| --- | --- |
| `detection.py` | The rules that decide whether a search counts |
| `watcher.py` | Screen capture, input checks, and sending to Discord |
| `wheel.py` | Scroll wheel detection through Windows raw input |
| `bot.py`, `store.py` | The Discord bot and its database |
| `ui.py`, `widgets.py` | The app window and the animated chest |

## License

[MIT](LICENSE)

Chest Tracker is an unofficial fan project. It isn't affiliated with, endorsed by, or sponsored by Epic Games. Fortnite is a trademark of Epic Games, Inc.
