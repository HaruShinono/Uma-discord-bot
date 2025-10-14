# Shinono Uma Race - Discord Bot

A feature-rich horse racing simulation and betting bot for Discord, inspired by the popular game **[Umamusume: Pretty Derby](https://en.wikipedia.org/wiki/Umamusume:_Pretty_Derby)**. This bot allows users to bet on horse races, earn currency, and compete on a server-wide leaderboard.

> **⚠️ Important Language Notice**
> Please be aware that all user-facing text, including command descriptions, bot responses, and UI elements, are written entirely in **Vietnamese**. This project was originally developed for a Vietnamese-speaking community.

This repository contains two versions of the bot:
*   `umamain.py`: The full-featured version with scheduled special events, advanced admin commands, and more complex UI elements.
*   `umasimple.py`: A simplified, core version that is easier to run and manage, with no admin commands or scheduled tasks.

---

## Features

- **Economy System**: Users start with a balance, can earn daily rewards (`/umadaily`), check balances (`/umabalance`), view the top 10 richest users (`/umatop`), and transfer currency (`/umagive`).
- **Live Race Simulation**: Watch races unfold in real-time as the bot edits a single message to show horse positions, progress bars, and fluctuating stats.
- **Randomized Horse Stats**: Each horse gets a unique set of stats (Speed, Power, Stamina, etc.) for every race, making each event unpredictable.
- **Special Events**: Special, powerful horses like "Vedal" and "Sonic" can randomly appear in races, spicing up the competition. The `umamain.py` version also includes daily scheduled special races with high stakes.
- **Persistent Data**: User balances and other important data are saved locally in `.csv` files.
- **Admin Controls (`umamain.py` only)**: A suite of admin commands to manage the bot, set channels, adjust user balances, and control events.

---

## Prerequisites

- Python 3.8 or newer.
- A Discord Bot Application (and its token). You can create one on the [Discord Developer Portal](https://discord.com/developers/applications).
- A Discord server where you have administrative permissions to add custom emojis.

---

## Installation & Setup

Follow these steps to get the bot running on your server.

### 1. Clone the Repository

```bash
git clone https://github.com/HaruShinono/Uma-discord-bot.git
cd Uma-discord-bot
```

### 2. Install Dependencies

Install the required Python libraries using the `requirements.txt` file.

```bash
pip install -r requirements.txt
```

### 3. CRITICAL: Set Up Custom Emojis

The bot's code uses hardcoded custom emoji IDs. You **must** upload the horse emojis to your own server and update the code with your new emoji IDs.

1.  **Upload Emojis**: Upload the necessary horse images as custom emojis to your Discord server.
2.  **Get New Emoji IDs**: In your Discord client, type `\:your_emoji_name:` (e.g., `\:gold_ship:`) and send the message. Discord will display the full emoji ID.
    - It will look like this: `<:gold_ship:1427257561802997841>`
3.  **Update the Code**: Open the `.py` file you intend to run (`umamain.py` or `umasimple.py`) and find the `HORSE_POOL` and `SPECIAL_HORSES_POOL` lists. Replace the emoji strings with the new ones from your server.

    **Example (before):**
    ```python
    HORSE_POOL = [
        {"emoji": "<:gold_ship:1427257561802997841>", "name": "Gold Ship"},
        ...
    ]
    ```
    **Example (after updating with your server's emoji):**
    ```python
    HORSE_POOL = [
        {"emoji": "<:gold_ship:987654321098765432>", "name": "Gold Ship"}, # Your new ID here
        ...
    ]
    ```

### 4. Configure Environment Variables

Create a file named `.env` in the root of the project directory. This file will store your secret credentials.

Copy the following format into your `.env` file and fill in the values:

```
# Your bot's unique token from the Discord Developer Portal
DISCORD_TOKEN="YOUR_DISCORD_BOT_TOKEN_HERE"

# (Required for umamain.py) A password for using admin commands
ADMIN_PASSWORD="CHOOSE_A_STRONG_PASSWORD"
```

---

## Running the Bot

Choose which version of the bot you want to run and execute the corresponding command from your terminal.

**To run the full-featured version:**

```bash
python umamain.py
```

**To run the simplified version:**

```bash
python umasimple.py
```

Once running, you can use the `/` commands in your Discord server (e.g., `/umarace`, `/umabalance`). For `umamain.py`, remember to use the admin commands to set the race channels first!

---

## Contact & Support

If you encounter any bugs, have questions, or want to suggest a new feature, please feel free to reach out.

-   **GitHub Issues**: For bug reports and feature requests, please [open an issue](https://HaruShinono/Uma-discord-bot/your-repo-name/issues) on this repository.
-   **Discord**: You can contact me directly at `harushinono`.

---

## Credits
- This project is heavily inspired by **Umamusume: Pretty Derby**.
- Developed by HaruShinono.
