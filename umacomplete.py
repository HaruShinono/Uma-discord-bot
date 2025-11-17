import discord
from discord import app_commands
from discord.ext import commands, tasks
import asyncio, random, csv, os, json, hashlib
from datetime import datetime, time, timedelta, timezone
from dotenv import load_dotenv
from typing import Dict, List, Optional, Any
import logging

# --- Setup Logging ---
if not os.path.exists("logs"):
    os.makedirs("logs")

log_file_name = datetime.now().strftime("logs/umarace_%Y%m%d.log")
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(log_file_name, encoding="utf-8"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("umarace_bot")
# --- End Logging Setup ---

# Load token and admin password
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD")

if not TOKEN:
    logger.error("DISCORD_TOKEN not set in .env")
    raise RuntimeError("Set DISCORD_TOKEN in .env")
if not ADMIN_PASSWORD:
    logger.warning("ADMIN_PASSWORD not set in .env. Some admin commands will not work without a password.")

# Bot setup
INTENTS = discord.Intents.default()
INTENTS.message_content = True
INTENTS.members = True
bot = commands.Bot(command_prefix="!", intents=INTENTS, heartbeart_timeout=120, guild_ready_timeout=10)

# Define UTC+7 timezone
VN_TZ = timezone(timedelta(hours=7))

# Config constants
BALANCE_FILE = "balances.csv"
DAILY_FILE = "daily.csv"
SERVER_SETTINGS_FILE = "server_settings.json"  # NEW: Replaces individual channel files
HORSE_HISTORY_FILE = "horse_history.csv"
HORSE_FILE = "horses.csv"
SPECIAL_HORSE_FILE = "special_horses.csv"
RACE_STATE_FILE = "race_state.csv"

START_BALANCE = 20000
MIN_BET = 200
MIN_SPECIAL_BET = 2000

RACE_DISTANCE = 80
SPECIAL_RACE_DISTANCE = 1500
BAR_LENGTH = 20
TICK_SECONDS = 1
STAT_UPDATE_SECONDS = 3
STAT_MIN = 200
STAT_MAX = 1200
STAT_DELTA_MIN = 50
STAT_DELTA_MAX = 100
FALL_PENALTY_TICKS = 3

PAYOUTS = {1: 3.5, 2: 2.5, 3: 2.0}
SPECIAL_PAYOUTS = {1: 12.7, 2: 8.5, 3: 6.0}
SPECIAL_RACE_PAYOUT_MULTIPLIER = 12.7

# Stat Icons
ICON_SPEED = "<:speed:1427826371648032810>"
ICON_POWER = "<:power:1427826367390679151>"
ICON_STAMINA = "<:stamina:1427826369517064272>"
ICON_AGILITY = "<:guts:1427826364756791296>"
ICON_FOCUS = "<:wit:1427826362445725758>"


# --- File Helpers (CSV Loading) ---
def load_horses_from_csv(filepath: str, is_special: bool = False) -> List[Dict[str, str]]:
    if not os.path.exists(filepath):
        logger.warning(f"{filepath} not found. Creating it.")
        headers = ["emoji", "name", "special_type"] if is_special else ["emoji", "name"]
        with open(filepath, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(headers)
        return []

    horses = []
    try:
        with open(filepath, mode='r', newline='', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                required_keys = ["emoji", "name", "special_type"] if is_special else ["emoji", "name"]
                if all(key in row for key in required_keys):
                    horses.append(dict(row))
                else:
                    logger.warning(f"Skipping invalid row in {filepath}: {row}")
    except Exception as e:
        logger.error(f"Failed to load horses from {filepath}: {e}")
    return horses


# Load horse pools from CSV files
HORSE_POOL = load_horses_from_csv(HORSE_FILE, is_special=False)
SPECIAL_HORSES_POOL = load_horses_from_csv(SPECIAL_HORSE_FILE, is_special=True)

# Dynamic Config
cooldown_seconds = 30
race_open_time = time(6, 0, tzinfo=VN_TZ)
race_start_time = time(18, 30, tzinfo=VN_TZ)

# Global state
system_active = True
special_horses_enabled = True
special_race_bets: Dict[str, Dict[str, Any]] = {}
special_race_open_for_bets = False
special_race_messages: Dict[int, discord.Message] = {}  # MODIFIED: was special_race_message
current_special_race_tasks: List[asyncio.Task] = []  # MODIFIED: was current_special_race_task
special_race_embed_queue = asyncio.Queue()  # NEW: For syncing race display
race_locks: Dict[int, bool] = {}
user_last_race: Dict[str, datetime] = {}
special_race_has_run_today = False
special_race_participant_pool: List[Dict[str, Any]] = []


# --- NEW: Server Settings Management (JSON) ---
def load_server_settings() -> Dict[str, Dict[str, Any]]:
    if not os.path.exists(SERVER_SETTINGS_FILE):
        return {}
    try:
        with open(SERVER_SETTINGS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (json.JSONDecodeError, FileNotFoundError):
        logger.error(f"Could not load or parse {SERVER_SETTINGS_FILE}. Starting with empty settings.")
        return {}


def save_server_settings(settings: Dict[str, Dict[str, Any]]):
    with open(SERVER_SETTINGS_FILE, 'w', encoding='utf-8') as f:
        json.dump(settings, f, indent=4)


server_settings = load_server_settings()


# --- Authorization Helper ---
def is_authorized(interaction: discord.Interaction, password: str) -> bool:
    # 1. Check for master password
    if password == ADMIN_PASSWORD:
        return True

    # 2. Check for server-specific password
    if not interaction.guild:
        return False

    guild_id = str(interaction.guild.id)
    guild_config = server_settings.get(guild_id, {})
    stored_hash = guild_config.get("admin_password_hash")

    if not stored_hash:
        return False

    # Hash the provided password and compare
    password_hash = hashlib.sha256(password.encode('utf-8')).hexdigest()
    return password_hash == stored_hash


# --- File Helpers ---
def load_csv_map(filepath: str, default_value_type: type = str) -> Dict[str, Any]:
    if not os.path.exists(filepath):
        with open(filepath, 'w', newline='', encoding="utf-8") as f: pass
        return {}
    out = {}
    with open(filepath, newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        for row in reader:
            if len(row) >= 2:
                try:
                    out[row[0]] = default_value_type(row[1])
                except (ValueError, TypeError):
                    logger.warning(f"Could not convert value '{row[1]}' in {filepath} for key '{row[0]}'");
                    out[
                        row[0]] = None
            elif len(row) == 1:
                out[row[0]] = None
    return out


def save_csv_map(filepath: str, d: Dict[str, Any]):
    with open(filepath, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        for k, v in d.items(): writer.writerow([k, v])


def load_csv_full(filepath: str) -> List[List[str]]:
    if not os.path.exists(filepath):
        with open(filepath, 'w', newline='', encoding="utf-8") as f: pass
        return []
    with open(filepath, newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        return list(reader)


def save_csv_full(filepath: str, data: List[List[str]]):
    with open(filepath, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerows(data)


def load_race_state() -> Dict[str, bool]:
    if not os.path.exists(RACE_STATE_FILE): return {"has_run": False}
    with open(RACE_STATE_FILE, 'r', newline='', encoding='utf-8') as f:
        reader = csv.reader(f)
        for row in reader:
            if len(row) == 2 and row[0] == "special_race_has_run_today": return {"has_run": row[1] == "True"}
    return {"has_run": False}


def save_race_state(has_run: bool):
    with open(RACE_STATE_FILE, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(["special_race_has_run_today", str(has_run)])
    logger.info(f"Race state saved: special_race_has_run_today = {has_run}")


# --- Balances ---
def load_balances() -> Dict[str, int]:
    raw_data = load_csv_full(BALANCE_FILE)
    out = {}
    for row in raw_data:
        key, value_str = None, None
        if len(row) >= 3:
            key, value_str = row[0], row[2]
        elif len(row) == 2:
            key, value_str = row[0], row[1]
        if key and value_str:
            try:
                out[key] = int(value_str)
            except ValueError:
                logger.warning(f"Could not convert balance '{value_str}' for user ID '{key}' in {BALANCE_FILE}.")
    return out


def save_balances(bal: Dict[str, int]):
    data_to_save = []
    for uid, coins in bal.items():
        user = bot.get_user(int(uid))
        username = user.name if user else "UnknownUser"
        data_to_save.append([uid, username, str(coins)])
    save_csv_full(BALANCE_FILE, data_to_save)


def get_balance(uid: str) -> int:
    b = load_balances()
    if uid not in b:
        logger.info(f"New user {uid} detected. Initializing with {START_BALANCE} coins.")
        b[uid] = START_BALANCE
        save_balances(b)
    return b.get(uid, START_BALANCE)


def change_balance(uid: str, delta: int) -> int:
    b = load_balances()
    cur = b.get(uid, START_BALANCE)
    new_bal = max(0, cur + delta)
    b[uid] = new_bal
    save_balances(b)
    logger.info(f"User {uid} balance changed by {delta}. New balance: {new_bal}")
    return new_bal


# --- Daily rewards & Race Config ---
def load_daily() -> Dict[str, str]: return load_csv_map(DAILY_FILE)


def save_daily(d: Dict[str, str]): save_csv_map(DAILY_FILE, d)


# MODIFIED: Guild-aware channel settings
def get_race_channel_id(guild_id: int) -> Optional[int]:
    guild_config = server_settings.get(str(guild_id), {})
    return guild_config.get("race_channel")


def set_race_channel_id(guild_id: int, channel_id: int):
    guild_id_str = str(guild_id)
    if guild_id_str not in server_settings:
        server_settings[guild_id_str] = {}
    server_settings[guild_id_str]["race_channel"] = channel_id
    save_server_settings(server_settings)
    logger.info(f"Main race channel for guild {guild_id} set to {channel_id}")


def get_special_race_channel_id(guild_id: int) -> Optional[int]:
    guild_config = server_settings.get(str(guild_id), {})
    return guild_config.get("special_race_channel")


def set_special_race_channel_id(guild_id: int, channel_id: int):
    guild_id_str = str(guild_id)
    if guild_id_str not in server_settings:
        server_settings[guild_id_str] = {}
    server_settings[guild_id_str]["special_race_channel"] = channel_id
    save_server_settings(server_settings)
    logger.info(f"Special race channel for guild {guild_id} set to {channel_id}")


def get_all_special_race_channels() -> List[int]:
    channel_ids = []
    for guild_config in server_settings.values():
        if "special_race_channel" in guild_config:
            channel_ids.append(guild_config["special_race_channel"])
    return channel_ids


def load_history() -> Dict[str, int]: return load_csv_map(HORSE_HISTORY_FILE, int)


def save_history(hist: Dict[str, int]): save_csv_map(HORSE_HISTORY_FILE, hist)


# --- Stats and movement helpers ---
def init_stats() -> Dict[str, int]: return {"speed": random.randint(STAT_MIN, STAT_MAX),
                                            "power": random.randint(STAT_MIN, STAT_MAX),
                                            "stamina": random.randint(STAT_MIN, STAT_MAX),
                                            "agility": random.randint(STAT_MIN, STAT_MAX),
                                            "focus": random.randint(STAT_MIN, STAT_MAX)}


def get_special_horse_stats(special_type: str) -> Dict[str, int]:
    if special_type == "vedal": return {"speed": 1, "power": 1, "stamina": 1, "agility": 1, "focus": 1}
    if special_type == "sonic": return {"speed": 9999, "power": 9999, "stamina": 9999, "agility": 9999, "focus": 9999}
    return init_stats()


def determine_special_participants() -> List[Dict[str, str]]:
    if not SPECIAL_HORSES_POOL:
        return []

    roll = random.random()
    if roll < 0.002:
        return random.sample(SPECIAL_HORSES_POOL, k=min(3, len(SPECIAL_HORSES_POOL)))
    elif roll < 0.007:
        return random.sample(SPECIAL_HORSES_POOL, k=min(2, len(SPECIAL_HORSES_POOL)))
    elif roll < 0.017:
        return random.sample(SPECIAL_HORSES_POOL, k=1)

    return []


def clamp_stat(v: int) -> int: return max(STAT_MIN, min(STAT_MAX, v))


def stats_to_move_bonus(stats: Dict[str, int], horse_name: str, is_special_race: bool) -> float:
    special_horse = next((h for h in SPECIAL_HORSES_POOL if h['name'] == horse_name), None)
    if special_horse:
        if (stype := special_horse.get('special_type')) == "vedal": return 0
        if stype == "sonic": return (SPECIAL_RACE_DISTANCE if is_special_race else RACE_DISTANCE) - random.randint(1, 3)
    s = stats["speed"] * 0.35 + stats["power"] * 0.3 + stats["stamina"] * 0.2 + stats["agility"] * 0.1 + stats[
        "focus"] * 0.05
    s_min, s_max = float(STAT_MIN), float(STAT_MAX)
    frac = (s - s_min) / (s_max - s_min) if s_max != s_min else 0.0
    return frac * (20.0 if is_special_race else 4.0)


def render_bar(pos: int, total_distance: int) -> tuple[str, int]:
    frac = max(0.0, min(pos / total_distance, 1.0))
    filled = int(round(frac * BAR_LENGTH))
    return "█" * filled + "░" * (BAR_LENGTH - filled), int(round(frac * 100))


def mood_emoji_from_delta(delta: int) -> str:
    if delta >= 200: return "💨"
    if delta >= 100: return "💪"
    if delta <= -200: return "😫"
    if delta <= -100: return "😮‍💨"
    return "⚡"


def check_fall(move: int) -> bool:
    if move >= 10: return random.random() < 0.06
    if move >= 8: return random.random() < 0.03
    return random.random() < 0.005


# MODIFIED: Name changed for clarity, logic is now in `is_authorized`
def is_master_admin(password: str) -> bool: return password == ADMIN_PASSWORD


async def update_special_bet_message():
    # MODIFIED: Update all betting messages across all servers
    if not special_race_messages:
        return

    # Build the bet text once
    bet_lines = []
    if not special_race_bets:
        bet_lines.append("Chưa có ai đặt cược.")
    else:
        for user_id, bet_info in special_race_bets.items():
            user_mention = f"<@{user_id}>"
            line = f"{user_mention} đã cược **{bet_info['bet_amount']} xu** vào **{bet_info['horse_name']}**"
            bet_lines.append(line)
    bet_text = "\n".join(bet_lines)

    # Create a template embed
    base_embed = next(iter(special_race_messages.values())).embeds[0]
    field_updated = False
    for i, field in enumerate(base_embed.fields):
        if field.name == "👥 Người Đã Cược":
            base_embed.set_field_at(i, name="👥 Người Đã Cược", value=bet_text, inline=False)
            field_updated = True
            break
    if not field_updated:
        base_embed.add_field(name="👥 Người Đã Cược", value=bet_text, inline=False)

    # Update all messages
    for msg in special_race_messages.values():
        try:
            await msg.edit(embed=base_embed)
        except discord.HTTPException as e:
            logger.error(f"Failed to update special bet message in channel {msg.channel.id}: {e}")


# --- UI Components ---
class BetAmountModal(discord.ui.Modal, title="Đặt Cược Cho Ngựa Đặc Biệt"):
    def __init__(self, chosen_horse_name: str):
        super().__init__()
        self.chosen_horse_name = chosen_horse_name

    bet_amount = discord.ui.TextInput(label="Nhập số xu bạn muốn cược",
                                      placeholder=f"Tối thiểu {MIN_SPECIAL_BET} xu...", style=discord.TextStyle.short,
                                      required=True)

    async def on_submit(self, interaction: discord.Interaction):
        uid = str(interaction.user.id)
        try:
            amount = int(self.bet_amount.value)
        except ValueError:
            await interaction.response.send_message("❌ Số tiền cược phải là một con số.", ephemeral=True);
            return
        if amount < MIN_SPECIAL_BET: await interaction.response.send_message(
            f"❌ Bạn phải cược ít nhất {MIN_SPECIAL_BET} xu.", ephemeral=True); return
        if (bal := get_balance(uid)) < amount: await interaction.response.send_message(
            f"❌ Bạn không đủ xu! (Hiện có: {bal})", ephemeral=True); return

        change_balance(uid, -amount)
        special_race_bets[uid] = {"horse_name": self.chosen_horse_name, "bet_amount": amount,
                                  "username": interaction.user.display_name}

        await interaction.response.send_message(
            f"✅ Cược **{amount} xu** vào **{self.chosen_horse_name}**! (Còn lại: {get_balance(uid)})", ephemeral=True)
        logger.info(f"User {uid} bet {amount} on {self.chosen_horse_name} for special race.")
        await update_special_bet_message()


class SpecialHorseSelect(discord.ui.Select):
    def __init__(self):
        options = [discord.SelectOption(label=h["name"], value=h["name"], emoji=h["emoji"]) for h in
                   special_race_participant_pool]
        super().__init__(placeholder="Chọn ngựa từ top 12 để cược (1 lần/ngày)...", options=options)

    async def callback(self, select_inter: discord.Interaction):
        uid = str(select_inter.user.id)
        if not special_race_open_for_bets: await select_inter.response.send_message("❌ Cửa cược đã đóng.",
                                                                                    ephemeral=True); return
        if uid in special_race_bets: await select_inter.response.send_message("❌ Bạn đã cược hôm nay rồi!",
                                                                              ephemeral=True); return
        await select_inter.response.send_modal(BetAmountModal(self.values[0]))


# --- Bot Events & Tasks ---
@bot.event
async def on_ready():
    global HORSE_POOL, SPECIAL_HORSES_POOL, special_race_has_run_today, server_settings
    logger.info(f"✅ Logged in as {bot.user}.")
    HORSE_POOL = load_horses_from_csv(HORSE_FILE);
    SPECIAL_HORSES_POOL = load_horses_from_csv(SPECIAL_HORSE_FILE, is_special=True)
    server_settings = load_server_settings()  # Load server settings on startup
    logger.info(f"Loaded {len(HORSE_POOL)} regular and {len(SPECIAL_HORSES_POOL)} special horses.")
    logger.info(f"Loaded configurations for {len(server_settings)} guilds.")
    special_race_has_run_today = load_race_state().get("has_run", False)
    logger.info(f"Initial race state loaded: has_run_today={special_race_has_run_today}")
    try:
        synced = await bot.tree.sync()
        logger.info(f"Slash commands synced ({len(synced)} commands).")
    except Exception as e:
        logger.error(f"Error syncing slash commands: {e}")
    special_race_scheduler.start();
    daily_reset_scheduler.start()
    logger.info("Scheduled tasks started.")


@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return
    if bot.user in message.mentions:
        await message.reply("Tag cái l à???")
    await bot.process_commands(message)


@bot.event
async def on_tree_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message(
            "❌ Bạn không có quyền `Quản lý Server` (Manage Guild) để sử dụng lệnh này.", ephemeral=True)
    else:
        logger.error(f"Unhandled error in command tree: {error}", exc_info=True)
        try:
            if not interaction.response.is_done(): await interaction.response.send_message(
                "❌ Đã có lỗi xảy ra. Vui lòng thử lại sau.", ephemeral=True)
        except discord.errors.InteractionResponded:
            await interaction.followup.send("❌ Đã có lỗi xảy ra. Vui lòng thử lại sau.", ephemeral=True)


@tasks.loop(time=time(0, 0, tzinfo=VN_TZ))
async def daily_reset_scheduler():
    global special_race_bets, special_race_open_for_bets, special_race_has_run_today, special_race_participant_pool, special_race_messages
    logger.info("Daily reset initiated.")
    save_daily({});
    special_race_bets = {};
    special_race_open_for_bets = False
    special_race_has_run_today = False;
    special_race_participant_pool = []
    special_race_messages = {}
    save_race_state(False)
    logger.info("Daily rewards, special race bets, and race state have been reset.")


async def open_special_race_betting():
    global special_race_open_for_bets, special_race_messages, special_race_participant_pool

    history = load_history()
    if not history:
        logger.warning("Horse history is empty, cannot determine top horses for special race.")
        return False

    sorted_history = sorted(history.items(), key=lambda item: item[1], reverse=True)[:12]
    top_12_names = [item[0] for item in sorted_history]
    special_race_participant_pool = [h for h in HORSE_POOL if h['name'] in top_12_names]

    if not special_race_participant_pool:
        logger.warning(f"Could not find any of the top 12 horses in the main HORSE_POOL.")
        return False

    special_race_open_for_bets = True;
    logger.info("Special race betting is now OPEN.")

    # MODIFIED: Send to all configured channels
    channel_ids = get_all_special_race_channels()
    if not channel_ids:
        logger.warning("No special race channels configured on any server. Cannot open bets.");
        return False

    # Prepare embed template
    view = discord.ui.View(timeout=None).add_item(SpecialHorseSelect())
    embed = discord.Embed(title="🌟 CỬỢC ĐUA NGỰA ĐẶC BIỆT ĐÃ MỞ! 🌟",
                          description=f"Hôm nay, chỉ **12 ngựa có thành tích tốt nhất** sẽ tranh tài!\n\nThời gian mở cược: **{race_open_time:%H:%M}**\nThời gian đua: **{race_start_time:%H:%M} (UTC+7)**\n\n**Lưu ý:** Mỗi người cược **1 lần/ngày**.\nTối thiểu **{MIN_SPECIAL_BET} xu**.",
                          color=discord.Color.blue())

    top_horses_text = ""
    medals = ["🥇", "🥈", "🥉"]
    for i, (name, wins) in enumerate(sorted_history):
        horse_info = next((h for h in HORSE_POOL if h['name'] == name), None)
        emoji = horse_info['emoji'] if horse_info else '🏇'
        prefix = medals[i] if i < 3 else f"**{i + 1}.**"
        top_horses_text += f"{prefix} {emoji} {name} - **{wins}** trận thắng\n"
    embed.add_field(name="🏆 Bảng Vàng Danh Vọng 🏆", value=top_horses_text, inline=False)
    embed.add_field(name="👥 Người Đã Cược", value="Chưa có ai đặt cược.", inline=False)
    embed.set_footer(text=f"Cửa cược sẽ đóng lúc {race_start_time:%H:%M}.")

    sent_count = 0
    for ch_id in channel_ids:
        channel = bot.get_channel(ch_id)
        if channel:
            try:
                msg = await channel.send(embed=embed, view=view)
                special_race_messages[ch_id] = msg
                sent_count += 1
            except discord.HTTPException as e:
                logger.error(f"Failed to send special race betting message to channel {ch_id}: {e}")
        else:
            logger.warning(f"Could not find configured special race channel with ID {ch_id}")

    logger.info(f"Sent special race announcements to {sent_count}/{len(channel_ids)} channels.")
    return sent_count > 0


# NEW: Helper coroutine to update follower messages
async def follower_race_updater(msg_to_edit: discord.Message, timeout: int):
    # Race duration is unpredictable, but we can set a generous timeout
    # E.g., SPECIAL_RACE_DISTANCE / min_speed_per_tick * TICK_SECONDS + buffer
    # Let's use 15 minutes as a safe upper bound.
    race_timeout = 900
    start_time = asyncio.get_event_loop().time()
    logger.info(f"Follower task started for message {msg_to_edit.id} in channel {msg_to_edit.channel.id}")
    while asyncio.get_event_loop().time() - start_time < race_timeout:
        try:
            # Wait for a new embed from the leader queue
            embed = await asyncio.wait_for(special_race_embed_queue.get(), timeout=15)
            if embed is None:  # Sentinel value to signal end of race
                logger.info(f"Follower task for message {msg_to_edit.id} received end signal.")
                break
            await msg_to_edit.edit(embed=embed)
        except asyncio.TimeoutError:
            # If no new embed for a while, just continue waiting
            continue
        except discord.NotFound:
            logger.warning(f"Follower message {msg_to_edit.id} not found, stopping task.")
            break
        except Exception as e:
            logger.error(f"Error in follower task for message {msg_to_edit.id}: {e}")
            break


@tasks.loop(minutes=1)
async def special_race_scheduler():
    global special_race_open_for_bets, current_special_race_tasks, special_race_has_run_today
    now_vn, current_time = datetime.now(VN_TZ), datetime.now(VN_TZ).timetz()

    if race_open_time <= current_time < race_start_time and not special_race_open_for_bets and not special_race_has_run_today:
        await open_special_race_betting()

    is_past_start_time = (current_time.hour > race_start_time.hour or
                          (current_time.hour == race_start_time.hour and current_time.minute > race_start_time.minute))

    if is_past_start_time and special_race_open_for_bets and not special_race_has_run_today:
        logger.info(
            f"Current time ({current_time:%H:%M}) is past the scheduled race time ({race_start_time:%H:%M}). Waiting until midnight for reset.")
        return

    if current_time >= race_start_time and special_race_open_for_bets and not special_race_has_run_today:
        if not current_special_race_tasks or all(t.done() for t in current_special_race_tasks):
            logger.info(f"Special race starting at {current_time}.")
            special_race_open_for_bets = False
            special_race_has_run_today = True
            save_race_state(True)
            current_special_race_tasks.clear()

            # Disable views on all betting messages
            for msg in special_race_messages.values():
                try:
                    view = discord.ui.View.from_message(msg)
                    for item in view.children: item.disabled = True
                    await msg.edit(view=view)
                except (discord.HTTPException, IndexError) as e:
                    logger.warning(f"Could not disable view on special race message {msg.id}: {e}")

            # MODIFIED: Run one leader task and multiple follower tasks
            all_channels = get_all_special_race_channels()
            if not all_channels:
                logger.warning("Special race time reached, but no channels are configured. Aborting race.")
                return

            leader_ch_id = all_channels[0]
            follower_ch_ids = all_channels[1:]

            leader_ch = bot.get_channel(leader_ch_id)
            if not leader_ch:
                logger.error(f"Could not find leader channel {leader_ch_id}. Aborting special race.")
                return

            bets_copy = special_race_bets.copy()
            # Start leader task
            leader_task = asyncio.create_task(
                run_special_race(leader_ch, bets_copy, is_leader_task=True)
            )
            current_special_race_tasks.append(leader_task)

            # Start follower tasks
            for ch_id in follower_ch_ids:
                msg = special_race_messages.get(ch_id)
                if msg:
                    follower_task = asyncio.create_task(follower_race_updater(msg, timeout=900))
                    current_special_race_tasks.append(follower_task)
                else:
                    logger.warning(f"Could not find message for follower channel {ch_id} to start updater task.")


@special_race_scheduler.before_loop
@daily_reset_scheduler.before_loop
async def before_tasks(): await bot.wait_until_ready()


# --- Race Engines ---
async def run_race(interaction: discord.Interaction, uid: str, bet: int, chosen: dict, participants: List[dict],
                   msg: discord.Message, initial_balance_after_bet: int, initial_stats: Dict[str, Dict[str, int]],
                   odds_display: float, special_horses_in_race: List[dict], is_special_race: bool = False,
                   is_admin_invoked: bool = False, is_leader_task: bool = False):  # MODIFIED: Added is_leader_task
    total_distance = SPECIAL_RACE_DISTANCE if is_special_race else RACE_DISTANCE
    stats, positions = initial_stats.copy(), {p["name"]: 0 for p in participants}
    last_stat_delta, finished = {p["name"]: 0 for p in participants}, []
    fall_penalties: Dict[str, int] = {p["name"]: 0 for p in participants}
    ticks, special_event_message = 0, ""

    has_boosted = {p["name"]: False for p in participants}
    boost_active_this_tick = {p["name"]: False for p in participants}
    STAMINA_COST_FOR_BOOST = 250
    STAMINA_THRESHOLD_FOR_BOOST = 300
    BOOST_BONUS_MOVE = 12 if is_special_race else 10

    if special_horses_in_race:
        names = ", ".join([f"**{h['name']}**" for h in special_horses_in_race])
        special_event_message = f"🌟 **KHÁCH MỜI BẤT NGỜ:** {names} đã xuất hiện!"

    embed = None
    try:
        while True:
            await asyncio.sleep(TICK_SECONDS)
            ticks += 1

            for name in boost_active_this_tick: boost_active_this_tick[name] = False

            for p in participants:
                name = p["name"]
                if name in finished: continue
                if fall_penalties.get(name, 0) > 0:
                    fall_penalties[name] -= 1
                    last_stat_delta[name] = 0
                    continue

                move = 0
                is_special_horse_type = any(sh['name'] == name for sh in SPECIAL_HORSES_POOL)
                if is_special_horse_type:
                    special_type = next((sh.get('special_type') for sh in SPECIAL_HORSES_POOL if sh['name'] == name),
                                        None)
                    if special_type == "vedal":
                        move = 1
                    elif special_type == "sonic":
                        move = total_distance - positions[name] if positions[name] < total_distance - 1 else 1
                    else:
                        move = random.randint(1, 6) + int(
                            round(stats_to_move_bonus(stats[name], name, is_special_race)))
                else:
                    move = random.randint(1, 6) + int(round(stats_to_move_bonus(stats[name], name, is_special_race)))

                    if positions[name] > (total_distance * 0.7) and not has_boosted[name] and stats[name][
                        "stamina"] > STAMINA_THRESHOLD_FOR_BOOST:
                        boost_chance = 0.05 + (stats[name]["focus"] / STAT_MAX) * 0.20
                        if random.random() < boost_chance:
                            logger.info(f"Horse '{name}' activated Final Stretch Boost!")
                            move += BOOST_BONUS_MOVE
                            stats[name]["stamina"] -= STAMINA_COST_FOR_BOOST
                            has_boosted[name] = True
                            boost_active_this_tick[name] = True

                    if check_fall(move):
                        logger.info(f"Horse '{name}' has fallen and will miss {FALL_PENALTY_TICKS} turns.")
                        fall_penalties[name] = FALL_PENALTY_TICKS
                        move = 0
                        last_stat_delta[name] = -random.randint(STAT_DELTA_MIN, STAT_DELTA_MAX)
                    elif stats[name]["stamina"] < (STAT_MIN + (STAT_MAX - STAT_MIN) * 0.25) and random.random() < 0.08:
                        last_stat_delta[name] = -random.randint(STAT_DELTA_MIN, STAT_DELTA_MAX)
                        continue
                positions[name] += move
                if positions[name] >= total_distance:
                    positions[name] = total_distance
                    if name not in finished: finished.append(name)

            if ticks % STAT_UPDATE_SECONDS == 0:
                for p in participants:
                    if not any(sh['name'] == p['name'] for sh in SPECIAL_HORSES_POOL):
                        name = p["name"]
                        key = random.choice(list(stats[name].keys()))
                        delta = random.choice([-1, 1]) * random.randint(STAT_DELTA_MIN, STAT_DELTA_MAX)
                        if has_boosted[name] and delta > 0: delta = -delta
                        old = stats[name][key]
                        stats[name][key] = clamp_stat(stats[name][key] + delta)
                        last_stat_delta[name] = stats[name][key] - old
                    else:
                        last_stat_delta[p["name"]] = 0

            embed = discord.Embed(
                title=f"🏇 CUỘC ĐUA ĐANG DIỄN RA! 🏇" if not is_special_race else f"✨ CUỘC ĐUA ĐẶC BIỆT! ✨",
                color=discord.Color.blue())
            desc = f"{special_event_message}\n\n" if special_event_message else ""

            if is_admin_invoked and not is_special_race:
                desc += "Cuộc đua đặc biệt do Admin triệu hồi! Tất cả ngựa đều tham gia."
            elif not is_special_race:
                desc += f"Bạn chọn: {chosen['emoji']} **{chosen['name']}**\nCược: **{bet} xu**\nSố dư: **{initial_balance_after_bet} xu**"
            else:
                desc += f"Cuộc đua của 12 ngựa hàng đầu! ({len(special_race_bets)} người đã cược)"
            embed.description = desc

            horse_lines = []
            for p in participants:
                name, emoji = p["name"], p["emoji"]
                bar, _ = render_bar(positions[name], total_distance)
                mood = "😵" if fall_penalties.get(name, 0) > 0 else "🔥" if boost_active_this_tick.get(name,
                                                                                                     False) else "✨" if any(
                    sh['name'] == name for sh in SPECIAL_HORSES_POOL) else mood_emoji_from_delta(
                    last_stat_delta.get(name, 0))
                s = stats[name]
                line = f"{emoji} {bar} ({min(positions[name], total_distance)}m) {mood}\n> {ICON_SPEED} {s['speed']} | {ICON_POWER} {s['power']} | {ICON_STAMINA} {s['stamina']} | {ICON_AGILITY} {s['agility']} | {ICON_FOCUS} {s['focus']}"
                horse_lines.append(line)

            embed.clear_fields()
            current_field, field_count = "", 0
            for line in horse_lines:
                if len(current_field) + len(line) > 1024:
                    embed.add_field(name="Đoàn Đua" if field_count == 0 else "\u200b", value=current_field,
                                    inline=False)
                    current_field, field_count = "", field_count + 1
                current_field += line + "\n"
            if current_field: embed.add_field(name="Đoàn Đua" if field_count == 0 else "\u200b", value=current_field,
                                              inline=False)
            embed.set_footer(text=f"Tick: {ticks} — {datetime.now(VN_TZ):%H:%M:%S}")

            try:
                await msg.edit(content=None, embed=embed)
                # NEW: If leader, put embed in queue for followers
                if is_leader_task:
                    # Clear queue to prevent followers from lagging behind
                    while not special_race_embed_queue.empty():
                        special_race_embed_queue.get_nowait()
                    await special_race_embed_queue.put(embed)
            except (discord.HTTPException, discord.ConnectionError) as e:
                logger.error(f"Network error while editing race message, ending race prematurely: {e}")
                break
            if len(finished) >= 3 or (len(participants) < 3 and len(finished) == len(participants)): break

        top3 = finished[:3]
        if not is_special_race and top3:
            history = load_history()
            for name in top3:
                if not any(sh['name'] == name for sh in SPECIAL_HORSES_POOL): history[name] = history.get(name, 0) + 1
            save_history(history)

        outcome_line, new_bal_final = "", initial_balance_after_bet
        if not is_special_race and not is_admin_invoked:
            place = top3.index(chosen["name"]) + 1 if chosen["name"] in top3 else 0
            if place in PAYOUTS:
                reward = int(round(bet * PAYOUTS[place]))
                new_bal_final = change_balance(uid, reward)
                outcome_line = f"✅ **THẮNG HẠNG {place}!** +{reward} xu (Tỉ lệ x{PAYOUTS[place]})"
            else:
                outcome_line = f"❌ **THUA!** -{bet} xu"

        final_embed = embed
        if final_embed is None: final_embed = discord.Embed(title="Error",
                                                            description="An unexpected error occurred."); await msg.edit(
            embed=final_embed); return

        final_embed.title = f"🏁 CUỘC ĐUA KẾT THÚC! 🏁" if not is_special_race else f"✨ CUỘC ĐUA ĐẶC BIỆT KẾT THÚC! ✨"
        final_embed.color = discord.Color.gold()
        results_text, medals = "", ["🥇", "🥈", "🥉"]
        for i, name in enumerate(top3):
            horse = next((h for h in participants if h["name"] == name), None)
            if horse: results_text += f"{medals[i]} **{i + 1}:** {horse['emoji']} {horse['name']}\n"
        final_embed.add_field(name="🏆 KẾT QUẢ 🏆", value=results_text or "Không có ai về đích.", inline=False)

        if is_special_race:
            winners_by_place = {1: [], 2: [], 3: []}
            for i, horse_name in enumerate(top3):
                place = i + 1
                payout_multiplier = SPECIAL_PAYOUTS.get(place)
                if not payout_multiplier: continue
                for user_id, info in special_race_bets.items():
                    if info["horse_name"] == horse_name:
                        payout = int(info['bet_amount'] * payout_multiplier)
                        change_balance(user_id, payout)
                        winners_by_place[place].append(f"<@{user_id}> đã thắng **{payout} xu**!")

            all_winners_text = ""
            for place, winners_list in winners_by_place.items():
                if winners_list: all_winners_text += f"**{medals[place - 1]} Hạng {place} (x{SPECIAL_PAYOUTS[place]}):**\n" + "\n".join(
                    winners_list) + "\n\n"

            if all_winners_text:
                final_embed.add_field(name="🎉 NHỮNG NGƯỜI CHIẾN THẮNG 🎉", value=all_winners_text, inline=False)
            else:
                final_embed.add_field(name="😔 Không ai đoán đúng top 3 hôm nay.", value="\n", inline=False)
            special_race_bets.clear()
        elif not is_admin_invoked:
            final_embed.add_field(name="💰 Giao Dịch 💰",
                                  value=f"{outcome_line}\nTổng xu của bạn: **{new_bal_final} xu**",
                                  inline=False)

        final_embed.set_footer(text=f"Cuộc đua kết thúc lúc {datetime.now(VN_TZ):%c}")
        try:
            await msg.edit(content=None, embed=final_embed, view=None)
            if is_leader_task:
                await special_race_embed_queue.put(final_embed)  # Push final result to followers
        except (discord.HTTPException, discord.ConnectionError) as e:
            logger.error(f"Error editing final race message: {e}")
    finally:
        # NEW: Signal to followers that the race is over
        if is_leader_task:
            # Put sentinel values for all potential followers
            for _ in range(len(bot.guilds)):
                await special_race_embed_queue.put(None)
            logger.info("Leader task finished and sent end signals to followers.")


async def run_special_race(channel: discord.TextChannel, bets: Dict[str, Dict[str, Any]],
                           force_all_horses: bool = False, is_leader_task: bool = False):
    participants = HORSE_POOL + SPECIAL_HORSES_POOL if force_all_horses else special_race_participant_pool
    if not participants:
        logger.error("Attempted to run special race with no participants. Aborting.")
        await channel.send("❌ Lỗi: Không có ngựa nào để bắt đầu cuộc đua đặc biệt. Vui lòng liên hệ Admin.")
        return

    random.shuffle(participants)
    stats = {p["name"]: get_special_horse_stats(p.get("special_type", "")) for p in participants}

    # Use the existing message if this is the leader task, otherwise send a new one for manual runs
    msg = special_race_messages.get(channel.id)
    if msg is None:
        try:
            msg = await channel.send(
                embed=discord.Embed(title="✨ Cuộc đua đặc biệt sắp bắt đầu! Các ngựa đang khởi động...",
                                    color=discord.Color.blurple()))
        except discord.HTTPException as e:
            logger.error(f"Failed to send initial special race message, aborting: {e}")
            return

    class DummyInteraction:
        def __init__(self, ch: discord.TextChannel): self.channel, self.user = ch, bot.user

        async def original_response(self): return msg

    await run_race(DummyInteraction(channel), "special_race_bot", 0, {}, participants, msg, 0, stats,
                   SPECIAL_RACE_PAYOUT_MULTIPLIER, [], is_special_race=True, is_leader_task=is_leader_task)


async def horse_autocomplete(interaction: discord.Interaction, current: str) -> List[app_commands.Choice[str]]:
    return [app_commands.Choice(name=n, value=n) for n in [h['name'] for h in HORSE_POOL] if
            current.lower() in n.lower()][:25]


@bot.tree.command(name="umarace", description="Tham gia đua ngựa với 7 đối thủ ngẫu nhiên.")
@app_commands.describe(horse="Tên ngựa bạn muốn cược", bet="Số xu bạn muốn cược (>=200)")
@app_commands.autocomplete(horse=horse_autocomplete)
async def umarace(interaction: discord.Interaction, horse: str, bet: int):
    if not interaction.guild:
        await interaction.response.send_message("Lệnh này chỉ dùng được trong server.", ephemeral=True)
        return

    uid, cid, gid = str(interaction.user.id), interaction.channel_id, interaction.guild.id
    if not system_active: await interaction.response.send_message("❌ Hệ thống đang tạm tắt.", ephemeral=True); return

    # MODIFIED: Guild-aware channel check
    main_ch_id = get_race_channel_id(gid)
    if not main_ch_id: await interaction.response.send_message("❌ Kênh đua ngựa chưa được cài đặt cho server này.",
                                                               ephemeral=True); return
    if cid != main_ch_id: await interaction.response.send_message(f"❌ Vui lòng dùng lệnh này trong <#{main_ch_id}>.",
                                                                  ephemeral=True); return
    now = datetime.now(VN_TZ)
    if uid in user_last_race and (now - user_last_race[uid]).total_seconds() < cooldown_seconds:
        await interaction.response.send_message(
            f"⏳ Vui lòng chờ {int(cooldown_seconds - (now - user_last_race[uid]).total_seconds())} giây.",
            ephemeral=True);
        return
    if race_locks.get(cid): await interaction.response.send_message("❌ Đã có cuộc đua đang diễn ra. Chờ chút!",
                                                                    ephemeral=True); return
    bal = get_balance(uid)
    if bet < MIN_BET: await interaction.response.send_message(f"⚠️ Cược tối thiểu là {MIN_BET} xu.",
                                                              ephemeral=True); return
    if bet > bal: await interaction.response.send_message(f"❌ Bạn không đủ xu! (Hiện có: {bal})",
                                                          ephemeral=True); return
    chosen_horse = next((h for h in HORSE_POOL if h["name"].lower() == horse.lower()), None)
    if not chosen_horse: await interaction.response.send_message(f"❌ Ngựa **{horse}** không tồn tại.",
                                                                 ephemeral=True); return

    race_locks[cid] = True
    user_last_race[uid] = now
    try:
        other_horses = [h for h in HORSE_POOL if h['name'] != chosen_horse['name']]
        if len(other_horses) < 7:
            await interaction.response.send_message(
                "❌ Không đủ ngựa trong hệ thống để bắt đầu cuộc đua (cần ít nhất 8).", ephemeral=True)
            race_locks[cid] = False
            return

        participants = [chosen_horse] + random.sample(other_horses, 7)
        special_guests = determine_special_participants() if special_horses_enabled else []
        participants.extend(special_guests)
        random.shuffle(participants)

        new_bal = change_balance(uid, -bet)
        initial_stats = {p["name"]: get_special_horse_stats(p.get("special_type", "")) for p in participants}
        s_ch = initial_stats[chosen_horse["name"]]

        embed = discord.Embed(title="🏇 CUỘC ĐUA SẮP BẮT ĐẦU! 🏇", description="Các ngựa đang khởi động...",
                              color=discord.Color.green())
        embed.add_field(name="Thông Tin Cược Của Bạn",
                        value=f"Ngựa: {chosen_horse['emoji']} **{chosen_horse['name']}**\nCược: **{bet} xu**\nSố dư mới: **{new_bal} xu**",
                        inline=False)
        embed.add_field(name="Chỉ Số Ngựa Của Bạn",
                        value=f"{ICON_SPEED} {s_ch['speed']} | {ICON_POWER} {s_ch['power']} | {ICON_STAMINA} {s_ch['stamina']} | {ICON_AGILITY} {s_ch['agility']} | {ICON_FOCUS} {s_ch['focus']}",
                        inline=False)
        embed.set_footer(text=f"{datetime.now(VN_TZ):%c}")

        await interaction.response.send_message(embed=embed)
        msg = await interaction.original_response()
        await run_race(interaction, uid, bet, chosen_horse, participants, msg, new_bal, initial_stats, 0,
                       special_guests)
    finally:
        race_locks[cid] = False


# ... (các lệnh còn lại không thay đổi)
@bot.tree.command(name="umabalance", description="Xem số xu hiện tại của bạn hoặc người khác")
@app_commands.describe(user="Người muốn xem (tùy chọn)")
async def balance_cmd(interaction: discord.Interaction, user: Optional[discord.User] = None):
    target = user or interaction.user
    await interaction.response.send_message(f"💰 {target.display_name} hiện có **{get_balance(str(target.id))} xu**")


@bot.tree.command(name="umatop", description="Xem top 10 người giàu nhất")
async def top_cmd(interaction: discord.Interaction):
    balances = load_balances()
    if not balances: await interaction.response.send_message("Chưa có người chơi nào."); return
    top_users = sorted(balances.items(), key=lambda item: item[1], reverse=True)[:10]
    embed, description, medals = discord.Embed(title="🏆 Top 10 Người Giàu Nhất 🏆", color=discord.Color.gold()), [], [
        "🥇", "🥈", "🥉"]
    for i, (uid, coins) in enumerate(top_users):
        user, name = bot.get_user(int(uid)), f"Người Dùng Vô Danh ({uid[:6]}...)"
        if user: name = user.display_name
        prefix = medals[i] if i < 3 else f"**{i + 1}.**"
        description.append(f"{prefix} {name} - **{coins} xu**")
    embed.description = "\n".join(description)
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="umadaily", description="Nhận thưởng hàng ngày (800-1500 xu, 1 lần/ngày)")
async def daily_cmd(interaction: discord.Interaction):
    uid, (daily, today) = str(interaction.user.id), (load_daily(), datetime.now(VN_TZ).date().isoformat())
    if daily.get(uid) == today: await interaction.response.send_message("❌ Bạn đã nhận thưởng hôm nay rồi.",
                                                                        ephemeral=True); return
    reward = random.randint(800, 1500);
    new_bal = change_balance(uid, reward)
    daily[uid] = today;
    save_daily(daily)
    await interaction.response.send_message(f"🎁 Bạn nhận được **{reward} xu**! Tổng cộng: **{new_bal} xu**")


@bot.tree.command(name="umagive", description="Chuyển xu cho người khác")
@app_commands.describe(user="Người nhận", amount="Số xu muốn chuyển")
async def give_cmd(interaction: discord.Interaction, user: discord.User, amount: int):
    sender, receiver = str(interaction.user.id), str(user.id)
    if amount <= 0: await interaction.response.send_message("❌ Số tiền không hợp lệ.", ephemeral=True); return
    if sender == receiver: await interaction.response.send_message("❌ Không thể tự chuyển cho mình.",
                                                                   ephemeral=True); return
    if user.bot: await interaction.response.send_message("❌ Không thể chuyển xu cho bot.", ephemeral=True); return
    if (s_bal := get_balance(sender)) < amount: await interaction.response.send_message(
        f"❌ Bạn không đủ xu để chuyển. (Hiện có: {s_bal})", ephemeral=True); return
    change_balance(sender, -amount);
    change_balance(receiver, amount)
    await interaction.response.send_message(
        f"💸 Bạn đã chuyển **{amount} xu** cho {user.mention}. Bạn còn: **{get_balance(sender)} xu**")


class HelpView(discord.ui.View):
    def __init__(self, interaction: discord.Interaction):
        super().__init__(timeout=180);
        self.interaction = interaction;
        self.add_item(HelpSelect())

    async def on_timeout(self):
        try:
            original_message = await self.interaction.original_response()
            for item in self.children: item.disabled = True
            await original_message.edit(view=self)
        except discord.errors.NotFound:
            pass


class HelpSelect(discord.ui.Select):
    def __init__(self):
        super().__init__(placeholder="Chọn một danh mục để xem...", options=[
            discord.SelectOption(label="Trang Chính", emoji="🏠", value="main",
                                 description="Giới thiệu và thông tin chung"),
            discord.SelectOption(label="Lệnh Người Dùng", emoji="👤", value="user",
                                 description="Các lệnh về tiền tệ và xã hội"),
            discord.SelectOption(label="Thông Tin Đua Ngựa", emoji="🏇", value="race",
                                 description="Cách tham gia các cuộc đua"),
            discord.SelectOption(label="Lệnh Admin & Mod", emoji="👑", value="admin",  # MODIFIED
                                 description="Lệnh dành cho quản trị viên server")])

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer();
        await interaction.edit_original_response(embed=self.get_embed_for_choice(self.values[0]))

    def get_embed_for_choice(self, choice: str) -> discord.Embed:
        embed = discord.Embed(color=discord.Color.blue());
        if bot.user: embed.set_thumbnail(url=bot.user.display_avatar.url)
        if choice == "main":
            embed.title, embed.description = "🏠 Hướng Dẫn Bot Đua Ngựa Shinono Uma Race", "Chào mừng bạn đến với Shinono Uma Race! Sử dụng menu bên dưới để khám phá các tính năng."
            embed.add_field(name="📜 Nguyên Tắc Cơ Bản",
                            value="1. **Nhận xu hàng ngày** với `/umadaily`.\n2. **Đặt cược** vào ngựa yêu thích với `/umarace`.\n3. **Tham gia sự kiện đặc biệt** hàng ngày để thắng lớn.\n4. **Theo dõi tài sản** với `/umabalance` và `/umatop`.",
                            inline=False)
            embed.add_field(name="⭐ Tác giả",
                            value="**Người viết:** Haru Shinono\n**GitHub:** [HaruShinono](https://github.com/HaruShinono)\n**Discord:** harushinono",
                            inline=False).set_footer(text="Chọn mục khác trong menu để xem chi tiết.")
        elif choice == "user":
            embed.title, embed.color, embed.description = "👤 Lệnh Người Dùng Chung", discord.Color.green(), "Các lệnh giúp bạn quản lý tiền tệ và tương tác với người khác."
            embed.add_field(name="`/umabalance [user]`", value="Xem số xu của bạn hoặc người khác.",
                            inline=False).add_field(name="`/umatop`", value="Xem bảng xếp hạng 10 người giàu nhất.",
                                                    inline=False).add_field(name="`/umadaily`",
                                                                            value=f"Nhận thưởng **800-1500 xu** mỗi ngày. Reset lúc 00:00 (UTC+7).",
                                                                            inline=False).add_field(
                name="`/umagive <user> <amount>`", value="Chuyển xu cho người khác.", inline=False)
        elif choice == "race":
            embed.title, embed.color = "🏇 Lệnh & Thông Tin Đua Ngựa", discord.Color.purple()
            embed.add_field(name="`/umarace <horse> <bet>`",
                            value=f"Bắt đầu một cuộc đua thường (8 ngựa). Có thể có ngựa đặc biệt xuất hiện bất ngờ!\n**Cược tối thiểu:** `{MIN_BET}` xu.\n**Cooldown:** `{cooldown_seconds}` giây.",
                            inline=False).add_field(name="✨ Đua Ngựa Đặc Biệt Hàng Ngày",
                                                    value=f"Sự kiện đua của top 12 ngựa mạnh nhất. \n**Mở cược:** `{race_open_time:%H:%M}` | **Bắt đầu:** `{race_start_time:%H:%M}` (UTC+7).\n**Cược tối thiểu:** `{MIN_SPECIAL_BET}` xu.",
                                                    inline=False)
        elif choice == "admin":
            embed.title, embed.color, embed.description = "👑 Lệnh Dành Cho Quản Trị Viên & Mod", discord.Color.red(), "Lệnh cấp cao yêu cầu Mật khẩu Master (từ file .env). Lệnh cấp server yêu cầu Mật khẩu Server hoặc quyền `Manage Guild`."
            embed.add_field(name="--- Lệnh cho Mod Server (Cần Manage Guild) ---", value="\u200b", inline=False)
            embed.add_field(name="`/umasetserverpassword <new_password>`",
                            value="Tạo hoặc thay đổi mật khẩu quản trị cho server này. Dùng để thực hiện các lệnh cần mật khẩu bên dưới.",
                            inline=False)
            embed.add_field(name="--- Lệnh cho Mod Server (Cần Mật khẩu Server) ---", value="\u200b", inline=False)
            embed.add_field(name="`/umasetracechannel <channel> <password>`",
                            value="Đặt kênh cho các cuộc đua thường tại server này.", inline=False)
            embed.add_field(name="`/umasetspecialracechannel <channel> <password>`",
                            value="Đặt kênh cho cuộc đua đặc biệt hàng ngày tại server này.", inline=False)
            embed.add_field(name="--- Lệnh cho Admin Bot (Cần Mật khẩu Master) ---", value="\u200b", inline=False)
            embed.add_field(name="`/umatopup <user> <amount> <password>`", value="Nạp hoặc trừ xu của một người chơi.",
                            inline=False)
            embed.add_field(name="`/umasettracehours ... <password>`",
                            value="Đặt lại giờ mở cược và bắt đầu cuộc đua đặc biệt (toàn cục).", inline=False)
            embed.add_field(name="`/umasetcooldown <seconds> <password>`",
                            value="Thay đổi thời gian chờ giữa các cuộc đua thường (toàn cục).", inline=False)
            embed.add_field(name="`/umaevent <mode> <password>`",
                            value="Quản lý các sự kiện và trạng thái hệ thống (toàn cục).", inline=False)

        return embed


@bot.tree.command(name="umahelp", description="Xem hướng dẫn sử dụng bot chi tiết.")
async def help_cmd(interaction: discord.Interaction): await interaction.response.send_message(
    embed=HelpSelect().get_embed_for_choice("main"), view=HelpView(interaction), ephemeral=False)


# --- Admin & Mod Commands ---
@bot.tree.command(name="umasetserverpassword", description="[MOD] Đặt/thay đổi mật khẩu quản trị cho server này.")
@app_commands.describe(new_password="Mật khẩu mới. Để trống để xóa.")
@app_commands.checks.has_permissions(manage_guild=True)
async def set_server_password_cmd(interaction: discord.Interaction, new_password: Optional[str] = None):
    if not interaction.guild:
        await interaction.response.send_message("Lệnh này chỉ dùng được trong server.", ephemeral=True);
        return

    guild_id = str(interaction.guild.id)
    if guild_id not in server_settings:
        server_settings[guild_id] = {}

    if not new_password:
        if "admin_password_hash" in server_settings[guild_id]:
            del server_settings[guild_id]["admin_password_hash"]
            msg = "✅ Đã xóa mật khẩu quản trị của server này."
        else:
            msg = "ℹ️ Server này chưa có mật khẩu nào được đặt."
    else:
        hashed_password = hashlib.sha256(new_password.encode('utf-8')).hexdigest()
        server_settings[guild_id]["admin_password_hash"] = hashed_password
        msg = f"✅ Đã đặt mật khẩu quản trị cho server này. Mật khẩu của bạn là: `{new_password}`. Hãy cất nó cẩn thận!"

    save_server_settings(server_settings)
    await interaction.response.send_message(msg, ephemeral=True)


@bot.tree.command(name="umatopup", description="[ADMIN] Nạp xu thủ công.")
@app_commands.describe(password="Mật khẩu Admin (Master)", user="Người nhận", amount="Số xu (có thể âm)")
async def topup_cmd(interaction: discord.Interaction, password: str, user: discord.User, amount: int):
    if not is_master_admin(password): await interaction.response.send_message("❌ Mật khẩu Master không đúng.",
                                                                              ephemeral=True); return
    new_bal = change_balance(str(user.id), amount)
    await interaction.response.send_message(
        f"✅ Đã cập nhật **{amount} xu** cho {user.mention}. Số dư mới: **{new_bal}**", ephemeral=True)


@bot.tree.command(name="umasettracechannel", description="[MOD] Đặt kênh cho đua ngựa thường tại server này.")
@app_commands.describe(channel="Kênh đua ngựa thường", password="Mật khẩu Server hoặc Master")
async def set_race_channel_cmd(interaction: discord.Interaction, channel: discord.TextChannel, password: str):
    if not interaction.guild:
        await interaction.response.send_message("Lệnh này chỉ dùng được trong server.", ephemeral=True);
        return
    if not is_authorized(interaction, password):
        await interaction.response.send_message("❌ Mật khẩu không đúng.", ephemeral=True);
        return

    set_race_channel_id(interaction.guild.id, channel.id)
    await interaction.response.send_message(f"✅ Kênh đua thường của server đã được đặt thành {channel.mention}.",
                                            ephemeral=True)


@bot.tree.command(name="umasetspecialracechannel", description="[MOD] Đặt kênh cho đua ngựa đặc biệt tại server này.")
@app_commands.describe(channel="Kênh đua ngựa đặc biệt", password="Mật khẩu Server hoặc Master")
async def set_special_race_channel_cmd(interaction: discord.Interaction, channel: discord.TextChannel, password: str):
    if not interaction.guild:
        await interaction.response.send_message("Lệnh này chỉ dùng được trong server.", ephemeral=True);
        return
    if not is_authorized(interaction, password):
        await interaction.response.send_message("❌ Mật khẩu không đúng.", ephemeral=True);
        return

    set_special_race_channel_id(interaction.guild.id, channel.id)
    await interaction.response.send_message(f"✅ Kênh đua đặc biệt của server đã được đặt thành {channel.mention}.",
                                            ephemeral=True)


@bot.tree.command(name="umaevent", description="[ADMIN] Quản lý sự kiện và hệ thống (toàn cục).")
@app_commands.describe(password="Mật khẩu Admin (Master)", mode="Hành động muốn thực hiện")
@app_commands.choices(mode=[
    app_commands.Choice(name="🏇 Tắt/Bật Ngựa Đặc Biệt (Đua Thường)", value="toggle_special_horses"),
    app_commands.Choice(name="💥 Chạy Đua Thường (Tất cả ngựa, kênh đầu tiên)", value="ult_uma_race"),
    app_commands.Choice(name="✨ Chạy Đua Đặc Biệt (Tất cả ngựa, broadcast)", value="ult_spe_uma_race"),
    app_commands.Choice(name="🎟️ Mở Cược Đặc Biệt (Thủ công)", value="open_bets"),
    app_commands.Choice(name="▶️ Bắt Đầu Đua Đặc Biệt (Thủ công)", value="start_special"),
    app_commands.Choice(name="⏰ Bật Lịch Trình Tự Động", value="scheduler_on"),
    app_commands.Choice(name="🔕 Tắt Lịch Trình Tự Động", value="scheduler_off"),
    app_commands.Choice(name="🔴 Tắt Toàn Bộ Hệ Thống", value="system_off"),
    app_commands.Choice(name="🟢 Bật Toàn Bộ Hệ Thống", value="system_on"),
    app_commands.Choice(name="🔄 Reset Trạng Thái Đua Đặc Biệt (Khẩn cấp)", value="reset_race_state")])
async def event_cmd(interaction: discord.Interaction, password: str, mode: str):
    global special_horses_enabled, special_race_open_for_bets, system_active, current_special_race_tasks, special_race_has_run_today
    if not is_master_admin(password): await interaction.response.send_message("❌ Mật khẩu Master không đúng.",
                                                                              ephemeral=True); return

    msg = "✅ Lệnh đã được thực thi."
    if mode == "toggle_special_horses":
        special_horses_enabled = not special_horses_enabled;
        status = "BẬT" if special_horses_enabled else "TẮT"
        msg = f"✅ Đã {status} khả năng xuất hiện ngựa đặc biệt trong các cuộc đua thường."
    elif mode == "ult_uma_race":
        await interaction.response.defer(ephemeral=True)
        if not interaction.guild or not (ch_id := get_race_channel_id(interaction.guild.id)) or not (
        ch := bot.get_channel(ch_id)):
            await interaction.followup.send("❌ Không thể bắt đầu, kênh đua thường của server này chưa được đặt.");
            return

        participants = HORSE_POOL + SPECIAL_HORSES_POOL;
        random.shuffle(participants)
        initial_stats = {p["name"]: get_special_horse_stats(p.get("special_type", "")) for p in participants}
        embed = discord.Embed(title="💥 CUỘC ĐUA TỐI THƯỢNG (THƯỜNG) DO ADMIN TRIỆU HỒI! 💥",
                              description="Tất cả các ngựa đang khởi động...", color=discord.Color.red())

        try:
            race_msg = await ch.send(embed=embed)
            await interaction.followup.send("✅ Đã bắt đầu cuộc đua thường tối thượng!")

            class DummyInteraction:
                def __init__(self, ch_obj: discord.TextChannel): self.channel, self.user = ch_obj, bot.user

                async def original_response(self): return race_msg

            await run_race(DummyInteraction(ch), "admin_race", 0, {}, participants, race_msg, 0, initial_stats, 0,
                           SPECIAL_HORSES_POOL, is_special_race=False, is_admin_invoked=True)
        except discord.HTTPException as e:
            await interaction.followup.send(f"❌ Lỗi khi bắt đầu cuộc đua: {e}")
        return
    elif mode == "ult_spe_uma_race" or mode == "start_special":
        if any(not t.done() for t in current_special_race_tasks):
            msg = "❌ Đua đặc biệt đang diễn ra."
        else:
            channel_ids = get_all_special_race_channels()
            if not channel_ids:
                msg = "❌ Không có kênh đua đặc biệt nào được đặt."
            else:
                special_race_open_for_bets = False;
                special_race_has_run_today = True;
                save_race_state(True)
                await interaction.response.send_message(
                    f"✅ Đang khởi chạy cuộc đua đặc biệt {'tối thượng' if mode == 'ult_spe_uma_race' else ''}...",
                    ephemeral=True)

                # Manual start logic, similar to scheduler
                leader_ch = bot.get_channel(channel_ids[0])
                if not leader_ch:
                    msg = f"❌ Không tìm thấy kênh leader {channel_ids[0]}."
                else:
                    bets = {} if mode == "ult_spe_uma_race" else special_race_bets.copy()
                    force_all = mode == "ult_spe_uma_race"
                    current_special_race_tasks.clear()

                    leader_task = asyncio.create_task(
                        run_special_race(leader_ch, bets, force_all_horses=force_all, is_leader_task=True))
                    current_special_race_tasks.append(leader_task)

                    for ch_id in channel_ids[1:]:
                        msg_obj = special_race_messages.get(ch_id)
                        if not msg_obj:  # If race started manually, messages might not exist
                            ch = bot.get_channel(ch_id)
                            if ch: msg_obj = await ch.send("Chuẩn bị xem cuộc đua...")
                        if msg_obj:
                            follower_task = asyncio.create_task(follower_race_updater(msg_obj, timeout=900))
                            current_special_race_tasks.append(follower_task)
                    return
    elif mode == "open_bets":
        await interaction.response.defer(ephemeral=True)
        if not get_all_special_race_channels(): await interaction.followup.send(
            "❌ Không thể mở cược. Chưa có server nào đặt kênh đua đặc biệt."); return
        for msg_obj in special_race_messages.values():
            try:
                await msg_obj.delete()
            except discord.HTTPException:
                pass
        special_race_messages.clear();
        special_race_bets.clear()
        if await open_special_race_betting():
            special_race_has_run_today = False;
            save_race_state(False)
            msg = "✅ Đã dọn dẹp và mở lại cửa cược đặc biệt. Trạng thái 'đã chạy' của hôm nay đã được reset."
        else:
            msg = "❌ Đã xảy ra lỗi khi cố gắng mở cược."
        await interaction.followup.send(msg);
        return
    elif mode == "scheduler_on":
        if not special_race_scheduler.is_running():
            special_race_scheduler.start(); msg = "✅ Đã bật lịch trình."
        else:
            msg = "⚠️ Lịch trình đã được bật."
    elif mode == "scheduler_off":
        if special_race_scheduler.is_running():
            special_race_scheduler.cancel(); msg = "✅ Đã tắt lịch trình."
        else:
            msg = "⚠️ Lịch trình đã được tắt."
    elif mode == "system_off":
        system_active = False
        if special_race_scheduler.is_running(): special_race_scheduler.cancel()
        if daily_reset_scheduler.is_running(): daily_reset_scheduler.cancel()
        msg = "🔴 HỆ THỐNG ĐÃ TẮT."
    elif mode == "system_on":
        system_active = True
        if not special_race_scheduler.is_running(): special_race_scheduler.start()
        if not daily_reset_scheduler.is_running(): daily_reset_scheduler.start()
        msg = "🟢 HỆ THỐNG ĐÃ BẬT."
    elif mode == "reset_race_state":
        special_race_has_run_today = False;
        save_race_state(False)
        msg = "✅ Đã reset trạng thái cuộc đua đặc biệt. Lịch trình sẽ chạy lại nếu đang trong giờ."
    await interaction.response.send_message(msg, ephemeral=True)


@bot.tree.command(name="umasettracehours", description="[ADMIN] Đặt giờ đua đặc biệt (UTC+7, toàn cục).")
@app_commands.describe(password="Mật khẩu Admin (Master)", open_hour="Giờ mở (0-23)", open_minute="Phút mở (0-59)",
                       start_hour="Giờ đua (0-23)", start_minute="Phút đua (0-59)")
async def set_race_hours_cmd(interaction: discord.Interaction, password: str, open_hour: int, open_minute: int,
                             start_hour: int, start_minute: int):
    global race_open_time, race_start_time
    if not is_master_admin(password): await interaction.response.send_message("❌ Mật khẩu Master không đúng.",
                                                                              ephemeral=True); return
    try:
        new_open_time, new_start_time = time(open_hour, open_minute, tzinfo=VN_TZ), time(start_hour, start_minute,
                                                                                         tzinfo=VN_TZ)
        if new_open_time >= new_start_time: await interaction.response.send_message(
            "❌ Giờ mở cược phải trước giờ bắt đầu.", ephemeral=True); return
        race_open_time, race_start_time = new_open_time, new_start_time
        special_race_scheduler.restart()
        await interaction.response.send_message(
            f"✅ Giờ đã cập nhật. Mở cược: **{race_open_time:%H:%M}**, Bắt đầu: **{race_start_time:%H:%M} (UTC+7)**. Lịch trình đã được khởi động lại.",
            ephemeral=True)
    except ValueError:
        await interaction.response.send_message("❌ Giờ hoặc phút không hợp lệ.", ephemeral=True)


@bot.tree.command(name="umasetcooldown", description="[ADMIN] Thay đổi cooldown đua thường (giây, toàn cục).")
@app_commands.describe(password="Mật khẩu Admin (Master)", seconds="Thời gian cooldown (giây)")
async def set_cooldown_cmd(interaction: discord.Interaction, password: str, seconds: int):
    global cooldown_seconds
    if not is_master_admin(password): await interaction.response.send_message("❌ Mật khẩu Master không đúng.",
                                                                              ephemeral=True); return
    if seconds < 0: await interaction.response.send_message("❌ Cooldown không thể âm.", ephemeral=True); return
    cooldown_seconds = seconds
    await interaction.response.send_message(f"✅ Đã đặt cooldown là **{cooldown_seconds} giây**.", ephemeral=True)


@bot.tree.command(name="umastatus", description="Hiển thị trạng thái của bot.")
async def status_cmd(interaction: discord.Interaction):
    embed = discord.Embed(title="📊 Trạng Thái Bot Đua Ngựa", color=discord.Color.orange(),
                          timestamp=datetime.now(VN_TZ))
    embed.add_field(name="Hệ thống Toàn Cục", value="🟢 Đang hoạt động" if system_active else "🔴 Đã tắt", inline=False)

    # Server-specific status
    if interaction.guild:
        race_ch = get_race_channel_id(interaction.guild.id)
        special_ch = get_special_race_channel_id(interaction.guild.id)
        embed.add_field(name=f"Cấu hình Server '{interaction.guild.name}'",
                        value=f"Kênh đua thường: {f'<#{race_ch}>' if race_ch else 'Chưa đặt'}\nKênh đua đặc biệt: {f'<#{special_ch}>' if special_ch else 'Chưa đặt'}",
                        inline=False)

    embed.add_field(name="Ngựa Đặc Biệt (Đua thường)", value="🟢 Bật" if special_horses_enabled else "🔴 Tắt",
                    inline=True)
    embed.add_field(name="Cooldown đua thường", value=f"{cooldown_seconds} giây", inline=True)

    scheduler_status = "Đang chạy" if special_race_scheduler.is_running() else "Đã tắt"
    embed.add_field(name="Lịch trình đua đặc biệt (Toàn cục)",
                    value=f"Trạng thái: **{scheduler_status}**\nMở cược: `{race_open_time:%H:%M}`\nBắt đầu: `{race_start_time:%H:%M}`",
                    inline=False)

    race_today_status = "✅ Đã chạy" if special_race_has_run_today else "⏳ Chưa chạy"
    embed.add_field(name="Trạng thái đua đặc biệt hôm nay", value=race_today_status, inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=True)


# --- Run bot ---
if __name__ == "__main__":
    if TOKEN:
        async def runner():
            async with bot:
                await bot.start(TOKEN)


        try:
            asyncio.run(runner())
        except KeyboardInterrupt:
            print("Bot stopped by admin.")
        except Exception as e:
            logger.critical(f"Bot CRASHED with unhandled exception: {e}", exc_info=True)
    else:
        logger.error("No DISCORD_TOKEN found. Bot cannot start.")