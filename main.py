import discord
from discord import app_commands
from discord.ext import commands, tasks
import asyncio, random, csv, os
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
    logger.warning("ADMIN_PASSWORD not set in .env. Admin commands will not work without a password.")

# Bot setup
INTENTS = discord.Intents.default()
INTENTS.message_content = True
INTENTS.members = True  # For getting member info for /top command
bot = commands.Bot(command_prefix="!", intents=INTENTS)

# Define UTC+7 timezone
VN_TZ = timezone(timedelta(hours=7))

# Config constants
BALANCE_FILE = "balances.csv"
DAILY_FILE = "daily.csv"
RACE_CHANNEL_FILE = "race_channel.csv"
SPECIAL_RACE_CHANNEL_FILE = "special_race_channel.csv"
HORSE_HISTORY_FILE = "horse_history.csv"

START_BALANCE = 10000
MIN_BET = 200
MIN_SPECIAL_BET = 2000  # New minimum for special races

RACE_DISTANCE = 80
SPECIAL_RACE_DISTANCE = 300
BAR_LENGTH = 20
TICK_SECONDS = 1
STAT_UPDATE_SECONDS = 3
STAT_MIN = 200
STAT_MAX = 1200
STAT_DELTA_MIN = 50
STAT_DELTA_MAX = 100

PAYOUTS = {1: 2.7, 2: 2.0, 3: 1.5}
SPECIAL_RACE_PAYOUT_MULTIPLIER = 5.0

# Dynamic Config (can be changed by admin commands)
cooldown_seconds = 30
race_open_time = time(6, 0, tzinfo=VN_TZ)
race_start_time = time(18, 30, tzinfo=VN_TZ)

# Horse pool
HORSE_POOL = [
    {"emoji": "<:gold_ship:1427257561802997841>", "name": "Gold Ship"},
    {"emoji": "<:haru_urara:1427257575996391497>", "name": "Haru Urara"},
    {"emoji": "<:kitasan_black:1427257395247185960>", "name": "Kitasan Black"},
    {"emoji": "<:oguri_cap:1427257240062132374>", "name": "Oguri Cap"},
    {"emoji": "<:satono_diamond:1427257543603781672>", "name": "Satono Diamond"},
    {"emoji": "<:special_week:1427257295812952164>", "name": "Special Week"},
    {"emoji": "<:tamamo_cross:1427257518538887240>", "name": "Tamamo Cross"},
    {"emoji": "<:tokai_teio:1427257361059414037>", "name": "Tokai Teio"},
]

# Special Horses Pool
SPECIAL_HORSES_POOL = [
    {"emoji": "<:vedalNom:1427276755194085457>", "name": "Vedal", "special_type": "vedal"},
    {"emoji": "<:sonic:1427276770184527873>", "name": "Sonic", "special_type": "sonic"},
]

# Global state
system_active = True
special_event_active = {"vedal": False, "sonic": False}
special_race_bets: Dict[str, Dict[str, Any]] = {}
special_race_open_for_bets = False
special_race_message: Optional[discord.Message] = None
current_special_race_task: Optional[asyncio.Task] = None
race_locks: Dict[int, bool] = {}
user_last_race: Dict[str, datetime] = {}


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
                except ValueError:
                    logger.warning(f"Could not convert value '{row[1]}' in {filepath}")
                    out[row[0]] = None
            elif len(row) == 1:
                out[row[0]] = None
    return out


def save_csv_map(filepath: str, d: Dict[str, Any]):
    with open(filepath, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        for k, v in d.items():
            writer.writerow([k, v])


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


# --- Balances ---
def load_balances() -> Dict[str, int]:
    raw_data = load_csv_full(BALANCE_FILE)
    out = {}
    for row in raw_data:
        if len(row) >= 3:
            try:
                out[row[0]] = int(row[2])
            except ValueError:
                out[row[0]] = START_BALANCE
        elif len(row) == 2:
            try:
                out[row[0]] = int(row[1])
            except ValueError:
                out[row[0]] = START_BALANCE
    return out


def save_balances(bal: Dict[str, int]):
    data_to_save = []
    for uid, coins in bal.items():
        user_obj = bot.get_user(int(uid))
        username = user_obj.name if user_obj else "UnknownUser"
        data_to_save.append([uid, username, str(coins)])
    save_csv_full(BALANCE_FILE, data_to_save)


def get_balance(uid: str) -> int:
    b = load_balances()
    if uid not in b:
        b[uid] = START_BALANCE
        save_balances(b)
        logger.info(f"New user {uid} initialized with {START_BALANCE} coins.")
    return b.get(uid, START_BALANCE)


def change_balance(uid: str, delta: int) -> int:
    b = load_balances()
    cur = b.get(uid, START_BALANCE)
    new_bal = max(0, cur + delta)
    b[uid] = new_bal
    save_balances(b)
    logger.info(f"User {uid} balance changed by {delta}. New balance: {new_bal}")
    return new_bal


# --- Daily rewards ---
def load_daily() -> Dict[str, str]:
    return load_csv_map(DAILY_FILE)


def save_daily(d: Dict[str, str]):
    save_csv_map(DAILY_FILE, d)


# --- Race Channel Configuration ---
def get_race_channel_id() -> Optional[int]:
    data = load_csv_map(RACE_CHANNEL_FILE)
    channel_id_str = data.get("main_race_channel")
    return int(channel_id_str) if channel_id_str else None


def set_race_channel_id(channel_id: int):
    save_csv_map(RACE_CHANNEL_FILE, {"main_race_channel": str(channel_id)})
    logger.info(f"Main race channel set to {channel_id}")


def get_special_race_channel_id() -> Optional[int]:
    data = load_csv_map(SPECIAL_RACE_CHANNEL_FILE)
    channel_id_str = data.get("special_race_channel")
    return int(channel_id_str) if channel_id_str else None


def set_special_race_channel_id(channel_id: int):
    save_csv_map(SPECIAL_RACE_CHANNEL_FILE, {"special_race_channel": str(channel_id)})
    logger.info(f"Special race channel set to {channel_id}")


# --- Horse history ---
def load_history() -> Dict[str, int]:
    return load_csv_map(HORSE_HISTORY_FILE, int)


def save_history(hist: Dict[str, int]):
    raw = {k: str(v) for k, v in hist.items()}
    save_csv_map(HORSE_HISTORY_FILE, raw)


# --- Stats and movement helpers ---
def init_stats() -> Dict[str, int]:
    return {"speed": random.randint(STAT_MIN, STAT_MAX), "power": random.randint(STAT_MIN, STAT_MAX),
            "stamina": random.randint(STAT_MIN, STAT_MAX), "agility": random.randint(STAT_MIN, STAT_MAX),
            "focus": random.randint(STAT_MIN, STAT_MAX)}


def get_special_horse_stats(special_type: str) -> Dict[str, int]:
    if special_type == "vedal": return {"speed": 1, "power": 1, "stamina": 1, "agility": 1, "focus": 1}
    if special_type == "sonic": return {"speed": 9999, "power": 9999, "stamina": 9999, "agility": 9999, "focus": 9999}
    return init_stats()


def clamp_stat(v: int) -> int:
    return max(STAT_MIN, min(STAT_MAX, v))


def stats_to_move_bonus(stats: Dict[str, int], horse_name: str, is_special_race: bool) -> float:
    if horse_name == "Vedal": return 0
    if horse_name == "Sonic":
        distance = SPECIAL_RACE_DISTANCE if is_special_race else RACE_DISTANCE
        return distance - random.randint(1, 3)

    s = stats["speed"] * 0.35 + stats["power"] * 0.3 + stats["stamina"] * 0.2 + stats["agility"] * 0.1 + stats[
        "focus"] * 0.05
    s_min, s_max = float(STAT_MIN), float(STAT_MAX)
    frac = (s - s_min) / (s_max - s_min) if s_max != s_min else 0.0
    return frac * 20.0 if is_special_race else frac * 4.0


def render_bar(pos: int, total_distance: int) -> tuple[str, int]:
    frac = max(0.0, min(pos / total_distance, 1.0))
    filled = int(round(frac * BAR_LENGTH))
    bar = "█" * filled + "░" * (BAR_LENGTH - filled)
    return bar, int(round(frac * 100))


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


def is_admin(password: str) -> bool:
    return password == ADMIN_PASSWORD


# --- UI Components for Special Race ---
class BetAmountModal(discord.ui.Modal, title="Đặt Cược Cho Ngựa Đặc Biệt"):
    def __init__(self, chosen_horse_name: str):
        super().__init__()
        self.chosen_horse_name = chosen_horse_name

    bet_amount = discord.ui.TextInput(
        label="Nhập số xu bạn muốn cược",
        placeholder=f"Tối thiểu {MIN_SPECIAL_BET} xu...",
        style=discord.TextStyle.short,
        required=True
    )

    async def on_submit(self, interaction: discord.Interaction):
        uid = str(interaction.user.id)
        try:
            amount = int(self.bet_amount.value)
        except ValueError:
            await interaction.response.send_message("❌ Số tiền cược phải là một con số.", ephemeral=True)
            return

        if amount < MIN_SPECIAL_BET:
            await interaction.response.send_message(f"❌ Bạn phải cược ít nhất {MIN_SPECIAL_BET} xu.", ephemeral=True)
            return

        bal = get_balance(uid)
        if bal < amount:
            await interaction.response.send_message(f"❌ Bạn không đủ xu để cược! (Hiện có: {bal} xu)", ephemeral=True)
            return

        change_balance(uid, -amount)
        special_race_bets[uid] = {"horse_name": self.chosen_horse_name, "bet_amount": amount,
                                  "username": interaction.user.display_name}
        await interaction.response.send_message(
            f"✅ Bạn đã cược **{amount} xu** vào **{self.chosen_horse_name}**! (Còn lại: {get_balance(uid)} xu)",
            ephemeral=True)
        logger.info(f"User {uid} bet {amount} on {self.chosen_horse_name} for special race.")


class SpecialHorseSelect(discord.ui.Select):
    def __init__(self):
        bet_options = [discord.SelectOption(label=h["name"], value=h["name"], emoji=h["emoji"]) for h in HORSE_POOL]
        super().__init__(placeholder="Chọn ngựa để cược (1 lần/ngày)...", min_values=1, max_values=1,
                         options=bet_options)

    async def callback(self, select_inter: discord.Interaction):
        uid = str(select_inter.user.id)
        if not special_race_open_for_bets:
            await select_inter.response.send_message("❌ Cửa cược đã đóng.", ephemeral=True)
            return
        if uid in special_race_bets:
            await select_inter.response.send_message("❌ Bạn đã cược trong cuộc đua đặc biệt hôm nay rồi!",
                                                     ephemeral=True)
            return

        chosen_name = self.values[0]
        modal = BetAmountModal(chosen_horse_name=chosen_name)
        await select_inter.response.send_modal(modal)


# --- Bot Events & Tasks ---
@bot.event
async def on_ready():
    logger.info(f"✅ Logged in as {bot.user}.")
    try:
        await bot.tree.sync()
        logger.info("Slash commands synced successfully.")
    except Exception as e:
        logger.error(f"Error syncing slash commands: {e}")
    special_race_scheduler.start()
    daily_reset_scheduler.start()
    logger.info("Scheduled tasks started.")


@tasks.loop(time=time(0, 0, tzinfo=VN_TZ))
async def daily_reset_scheduler():
    global special_race_bets, special_event_active, special_race_open_for_bets
    logger.info("Daily reset initiated.")
    save_daily({})
    logger.info("Daily rewards reset.")
    special_race_bets = {}
    special_event_active = {"vedal": False, "sonic": False}
    special_race_open_for_bets = False
    logger.info("Special race bets and event states reset.")


@tasks.loop(minutes=1)
async def special_race_scheduler():
    global special_race_open_for_bets, special_race_message, current_special_race_task
    now_vn = datetime.now(VN_TZ)
    current_time = now_vn.timetz()

    # Open betting window
    if race_open_time <= current_time < race_start_time and not special_race_open_for_bets:
        special_race_open_for_bets = True
        logger.info(f"Special race betting is now OPEN at {current_time}.")
        special_channel_id = get_special_race_channel_id()
        if special_channel_id and (channel := bot.get_channel(special_channel_id)) and isinstance(channel,
                                                                                                  discord.TextChannel):
            view = discord.ui.View(timeout=None).add_item(SpecialHorseSelect())
            embed = discord.Embed(title="🌟 CỬỢC ĐUA NGỰA ĐẶC BIỆT ĐÃ MỞ! 🌟",
                                  description=f"Thời gian mở cửa cược: **{race_open_time:%H:%M} (UTC+7)**\nThời gian đua: **{race_start_time:%H:%M} (UTC+7)**\n\nMỗi người chỉ được cược **1 lần/ngày**.\nCược tối thiểu **{MIN_SPECIAL_BET} xu**.\nĐoán đúng ngựa thắng sẽ nhận **x{int(SPECIAL_RACE_PAYOUT_MULTIPLIER)}** số tiền cược!",
                                  color=discord.Color.blue())

            # Optional: Add an image of the horses
            # embed.set_image(url="https://your-image-url-here.png")

            embed.set_footer(text=f"Cửa cược sẽ đóng vào lúc {race_start_time:%H:%M} (UTC+7).")
            special_race_message = await channel.send(embed=embed, view=view)

    # Start the race
    if current_time >= race_start_time and special_race_open_for_bets:
        if current_special_race_task is None or current_special_race_task.done():
            logger.info(f"Special race starting at {current_time}.")
            special_race_open_for_bets = False
            if special_race_message:
                try:
                    await special_race_message.edit(view=None)
                except Exception as e:
                    logger.error(f"Error closing special race bets: {e}")

            if special_channel_id := get_special_race_channel_id():
                if (channel := bot.get_channel(special_channel_id)) and isinstance(channel, discord.TextChannel):
                    current_special_race_task = asyncio.create_task(run_special_race(channel, special_race_bets))
                else:
                    logger.error(f"Special race channel {special_channel_id} is not a valid text channel.")
            else:
                logger.warning("Special race channel not set, cannot run race.")


@special_race_scheduler.before_loop
@daily_reset_scheduler.before_loop
async def before_scheduled_tasks():
    await bot.wait_until_ready()


# --- Race Engines ---
async def run_race(interaction: discord.Interaction, uid: str, bet: int, chosen: dict, participants: List[dict],
                   msg: discord.Message, initial_balance_after_bet: int,
                   initial_stats: Dict[str, Dict[str, int]], odds_display: float, special_event_message: str,
                   is_special_race: bool = False):
    global special_race_bets
    total_distance = SPECIAL_RACE_DISTANCE if is_special_race else RACE_DISTANCE
    stats = initial_stats
    positions = {p["name"]: 0 for p in participants}
    last_stat_delta = {p["name"]: 0 for p in participants}
    finished: List[str] = []
    final_bars: Dict[str, tuple] = {}
    ticks = 0

    while True:
        await asyncio.sleep(TICK_SECONDS)
        ticks += 1

        for p in participants:
            name = p["name"]
            if name in finished: continue

            fell, move = False, 0
            if name == "Vedal":
                move = 1
            elif name == "Sonic":
                move = total_distance - positions[name] if positions[name] < total_distance - 1 else 1
            else:
                base = random.randint(1, 6)
                bonus = stats_to_move_bonus(stats[name], name, is_special_race)
                move = base + int(round(bonus))
                if check_fall(move):
                    loss = max(1, int(total_distance * random.randint(5, 10) / 100))
                    positions[name] = max(0, positions[name] - loss)
                    fell, move = True, 0
                    last_stat_delta[name] = -random.randint(STAT_DELTA_MIN, STAT_DELTA_MAX)
                elif stats[name]["stamina"] < (STAT_MIN + (STAT_MAX - STAT_MIN) * 0.25) and random.random() < 0.08:
                    last_stat_delta[name] = -random.randint(STAT_DELTA_MIN, STAT_DELTA_MAX)
                    continue

            positions[name] += move
            if positions[name] >= total_distance:
                positions[name] = total_distance
                if name not in finished:
                    finished.append(name)
                    final_bars[name] = render_bar(positions[name], total_distance)

        if ticks % STAT_UPDATE_SECONDS == 0:
            for p in participants:
                if "special_type" not in p:
                    name = p["name"]
                    key = random.choice(list(stats[name].keys()))
                    delta = random.choice([-1, 1]) * random.randint(STAT_DELTA_MIN, STAT_DELTA_MAX)
                    old, stats[name][key] = stats[name][key], clamp_stat(stats[name][key] + delta)
                    last_stat_delta[name] = stats[name][key] - old
                else:
                    last_stat_delta[p["name"]] = 0

        lines = []
        for p in participants:
            name = p["name"]
            bar, _ = final_bars.get(name, render_bar(positions[name], total_distance))
            mood = "✨" if "special_type" in p else mood_emoji_from_delta(last_stat_delta.get(name, 0))
            s = stats[name]
            lines.append(f"{p['emoji']} {bar} ({min(positions[name], total_distance)}) {mood}")
            lines.append(f"    🏃‍♀️{s['speed']} 💪{s['power']} ⚡{s['stamina']} 💃{s['agility']} 💡{s['focus']}")

        header_lines = [f"🏇 CUỘC ĐUA ĐANG DIỄN RA!" if not is_special_race else f"✨ CUỘC ĐUA ĐẶC BIỆT ĐANG DIỄN RA! ✨",
                        ""]
        if special_event_message: header_lines.append(f"{special_event_message}\n")
        if not is_special_race:
            s_ch = stats[chosen['name']]
            header_lines.extend([
                f"Bạn chọn: {chosen['emoji']} **{chosen['name']}**",
                f"Tỷ lệ cược (tham khảo): **x{odds_display}**",
                f"Chỉ số (hiện tại): 🏃‍♀️{s_ch['speed']} 💪{s_ch['power']} ⚡{s_ch['stamina']} 💃{s_ch['agility']} 💡{s_ch['focus']}",
                f"Cược: **{bet} xu** → Có thể thắng (tham khảo): **{int(round(bet * odds_display))} xu**"
            ])
        else:
            header_lines.append(f"Cược thưởng **x{SPECIAL_RACE_PAYOUT_MULTIPLIER}** nếu thắng!")

        header_lines.extend(["", "━━━━━━━━━━━━━━━━━━━━🏁", *lines, ""])
        if not is_special_race:
            header_lines.append(f"Số dư sau khi cược: {initial_balance_after_bet} xu")
        else:
            header_lines.append(f"Người chơi đã cược: {len(special_race_bets)}")
        header_lines.append(f"Tick: {ticks} — {datetime.now(VN_TZ):%c}")

        try:
            await msg.edit(content="\n".join(header_lines))
        except discord.errors.NotFound:
            break
        except Exception as e:
            logger.error(f"Error editing race message: {e}")

        if len(finished) >= 3 or (len(participants) < 3 and len(finished) == len(participants)):
            break

    # Finalize results
    top3 = finished[:3]
    if not is_special_race and top3:
        history = load_history()
        for horse_name in top3:
            if not any(sh['name'] == horse_name for sh in SPECIAL_HORSES_POOL):
                history[horse_name] = history.get(horse_name, 0) + 1
        save_history(history)

    outcome_line, new_bal_final = "", get_balance(uid)
    if not is_special_race:
        place = top3.index(chosen["name"]) + 1 if chosen["name"] in top3 else 0
        if place in PAYOUTS:
            reward = int(round(bet * PAYOUTS[place]))
            new_bal_final = change_balance(uid, reward)
            outcome_line = f"✅ **THẮNG HẠNG {place}!** +{reward} xu"
        else:
            outcome_line = f"❌ **THUA!** -{bet} xu"

    final_lines = [f"🏇 CUỘC ĐUA KẾT THÚC!" if not is_special_race else f"✨ CUỘC ĐUA ĐẶC BIỆT KẾT THÚC! ✨", ""]
    if special_event_message: final_lines.append(f"{special_event_message}\n")
    final_lines.extend(["", "━━━━━━━━━━━━━━━━━━━━🏁", *lines, "", "**━━━━━ KẾT QUẢ ━━━━━**"])
    medals = ["🥇", "🥈", "🥉"]
    for i in range(3):
        if i < len(top3):
            horse = next(h for h in participants if h["name"] == top3[i])
            final_lines.append(f"{medals[i]} **{i + 1}:** {horse['emoji']} {horse['name']}")
        else:
            final_lines.append(f"{medals[i]} —")
    final_lines.append("")

    if is_special_race:
        winner_name = top3[0] if top3 else "Không có"

        # Calculate winnings and prepare winner list
        winners_info = []
        for user_id, info in special_race_bets.items():
            if info["horse_name"] == winner_name:
                payout = int(info['bet_amount'] * SPECIAL_RACE_PAYOUT_MULTIPLIER)
                change_balance(user_id, payout)
                winners_info.append(f"<@{user_id}> đã thắng **{payout} xu**!")

        if winners_info:
            final_lines.append("\n**🎉 NHỮNG NGƯỜI CHIẾN THẮNG:**")
            final_lines.extend(winners_info)
        else:
            final_lines.append("\n**Không có ai đoán đúng ngựa thắng hôm nay.**")

        special_race_bets = {}  # Clear bets for the next day
    else:
        final_lines.append(outcome_line)
        final_lines.append(f"💰 Tổng xu: **{new_bal_final} xu**")

    try:
        await msg.edit(content="\n".join(final_lines))
    except Exception as e:
        logger.error(f"Error editing final race message: {e}")


async def run_special_race(channel: discord.TextChannel, bets: Dict[str, Dict[str, Any]]):
    global special_race_message
    if special_race_message:
        try:
            await special_race_message.delete()
        except Exception:
            pass
        special_race_message = None

    participants = HORSE_POOL + SPECIAL_HORSES_POOL
    stats = {p["name"]: get_special_horse_stats(p["special_type"]) if "special_type" in p else init_stats() for p in
             participants}

    initial_message = await channel.send("✨ Chuẩn bị cho cuộc đua đặc biệt! Các ngựa đang khởi động...")

    class DummyInteraction:
        def __init__(self, ch: discord.TextChannel): self.channel, self.user = ch, bot.user

        async def original_response(self): return initial_message

    await run_race(
        DummyInteraction(channel), "special_race_bot", 0, {}, participants,
        initial_message, 0, stats, SPECIAL_RACE_PAYOUT_MULTIPLIER,
        "🌟 **CUỘC ĐUA ĐẶC BIỆT HÔM NAY!** Cực kỳ gay cấn!", is_special_race=True
    )


# --- Autocomplete function for the /umarace command ---
async def horse_autocomplete(
        interaction: discord.Interaction,
        current: str,
) -> List[app_commands.Choice[str]]:
    """An autocomplete function that suggests horse names based on user input."""
    # Get all available horse names from the pool
    horse_names = [horse['name'] for horse in HORSE_POOL]

    # Filter the list of horse names based on the current input, case-insensitively
    filtered_choices = [
        horse for horse in horse_names if current.lower() in horse.lower()
    ]

    # Create a list of Choice objects from the filtered list and return up to 25 choices
    return [
        app_commands.Choice(name=choice, value=choice)
        for choice in filtered_choices[:25]
    ]


# --- Bot Commands (User)---
@bot.tree.command(name="umarace", description="Tham gia đua ngựa và đặt cược xu (>=200).")
@app_commands.describe(horse="Tên ngựa bạn muốn cược (ví dụ: Gold Ship)", bet="Số xu bạn muốn cược (>=200)")
@app_commands.autocomplete(horse=horse_autocomplete)
async def umarace(interaction: discord.Interaction, horse: str, bet: int):
    uid, cid = str(interaction.user.id), interaction.channel_id
    if not system_active:
        await interaction.response.send_message("❌ Hệ thống đua ngựa đang tạm tắt. Vui lòng thử lại sau.",
                                                ephemeral=True)
        return
    if not (main_race_channel_id := get_race_channel_id()):
        await interaction.response.send_message("❌ Kênh đua ngựa chưa được cài đặt.", ephemeral=True)
        return
    if cid != main_race_channel_id:
        await interaction.response.send_message(f"❌ Vui lòng sử dụng lệnh này trong <#{main_race_channel_id}>.",
                                                ephemeral=True)
        return
    now = datetime.now()
    if uid in user_last_race and (now - user_last_race[uid]).total_seconds() < cooldown_seconds:
        remaining = int(cooldown_seconds - (now - user_last_race[uid]).total_seconds())
        await interaction.response.send_message(f"⏳ Vui lòng chờ {remaining} giây.", ephemeral=True)
        return
    if race_locks.get(cid):
        await interaction.response.send_message("❌ Đã có cuộc đua đang diễn ra. Vui lòng chờ.", ephemeral=True)
        return
    bal = get_balance(uid)
    if bet < MIN_BET:
        await interaction.response.send_message(f"⚠️ Mức cược tối thiểu là {MIN_BET} xu.", ephemeral=True)
        return
    if bet > bal:
        await interaction.response.send_message(f"❌ Bạn không đủ xu để cược! (Hiện có: {bal} xu)", ephemeral=True)
        return
    if not (chosen_horse_data := next((h for h in HORSE_POOL if h["name"].lower() == horse.lower()), None)):
        await interaction.response.send_message(f"❌ Ngựa **{horse}** không tồn tại.", ephemeral=True)
        return

    participants = HORSE_POOL.copy()
    special_message = ""
    roll = random.random()
    if special_event_active["vedal"] and special_event_active["sonic"]:
        participants.extend(SPECIAL_HORSES_POOL)
        special_message = "💥 **Sự kiện đặc biệt: Vedal và Sonic đã tham gia!**"
    elif special_event_active["vedal"]:
        participants.append(SPECIAL_HORSES_POOL[0])
        special_message = "🌟 **Sự kiện đặc biệt: Vedal đã xuất hiện!**"
    elif special_event_active["sonic"]:
        participants.append(SPECIAL_HORSES_POOL[1])
        special_message = "🌟 **Sự kiện đặc biệt: Sonic đã xuất hiện!**"
    elif roll < 0.05:
        participants.extend(SPECIAL_HORSES_POOL)
        special_message = "💥 **Sự kiện ngẫu nhiên: Vedal và Sonic đã tham gia!**"
    elif roll < 0.15:
        chosen_special = random.choice(SPECIAL_HORSES_POOL)
        participants.append(chosen_special)
        special_message = f"🌟 **Sự kiện ngẫu nhiên: {chosen_special['name']} đã xuất hiện!**"

    new_bal_after_bet = change_balance(uid, -bet)
    user_last_race[uid] = now

    initial_stats = {p["name"]: get_special_horse_stats(p["special_type"]) if "special_type" in p else init_stats() for
                     p in participants}
    s_ch = initial_stats[chosen_horse_data["name"]]
    odds_display = round(random.uniform(2.5, 4.0), 1)

    header = [f"🏇 CUỘC ĐUA SẮP BẮT ĐẦU!", ""]
    if special_message: header.append(f"{special_message}\n")
    header.extend([
        f"Bạn chọn: {chosen_horse_data['emoji']} **{chosen_horse_data['name']}**",
        f"Tỷ lệ cược (tham khảo): **x{odds_display}**",
        f"Chỉ số: 🏃‍♀️{s_ch['speed']} 💪{s_ch['power']} ⚡{s_ch['stamina']} 💃{s_ch['agility']} 💡{s_ch['focus']}",
        f"Cược: **{bet} xu** → Thắng (tham khảo): **{int(round(bet * odds_display))} xu**",
        "", "━━━━━━━━━━━━━━━━━━━━🏁", "Đang khởi động...", "",
        f"Số dư sau khi cược: {new_bal_after_bet} xu — {datetime.now(VN_TZ):%c}"
    ])

    await interaction.response.send_message("\n".join(header))
    msg = await interaction.original_response()

    race_locks[cid] = True
    try:
        await run_race(interaction, uid, bet, chosen_horse_data, participants, msg, new_bal_after_bet,
                       initial_stats, odds_display, special_message, is_special_race=False)
    finally:
        race_locks[cid] = False


@bot.tree.command(name="umabalance", description="Xem số xu hiện tại của bạn hoặc người khác")
@app_commands.describe(user="Người muốn xem (tùy chọn)")
async def balance_cmd(interaction: discord.Interaction, user: Optional[discord.User] = None):
    target = user or interaction.user
    await interaction.response.send_message(f"💰 {target.display_name} hiện có **{get_balance(str(target.id))} xu**")


@bot.tree.command(name="umatop", description="Xem top 10 người giàu nhất")
async def top_cmd(interaction: discord.Interaction):
    balances = load_balances()
    if not balances:
        await interaction.response.send_message("Chưa có người chơi nào.")
        return

    top_users = sorted(balances.items(), key=lambda item: item[1], reverse=True)[:10]
    embed = discord.Embed(title="🏆 Top 10 Người Giàu Nhất 🏆", color=discord.Color.gold())
    for i, (uid, coins) in enumerate(top_users, 1):
        user_obj = bot.get_user(int(uid))
        username = user_obj.display_name if user_obj else f"Người dùng không rõ ({uid})"
        embed.add_field(name=f"{i}. {username}", value=f"**{coins} xu**", inline=False)
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="umadaily", description="Nhận thưởng hàng ngày (800-1500 xu, 1 lần/ngày)")
async def daily_cmd(interaction: discord.Interaction):
    uid = str(interaction.user.id)
    daily = load_daily()
    today = datetime.now(VN_TZ).date().isoformat()
    if daily.get(uid) == today:
        await interaction.response.send_message("❌ Bạn đã nhận thưởng hôm nay rồi.", ephemeral=True)
        return
    reward = random.randint(800, 1500)
    new_bal = change_balance(uid, reward)
    daily[uid] = today
    save_daily(daily)
    await interaction.response.send_message(f"🎁 Bạn nhận được **{reward} xu**! Tổng cộng: **{new_bal} xu**")


@bot.tree.command(name="umagive", description="Chuyển xu cho người khác")
@app_commands.describe(user="Người nhận", amount="Số xu muốn chuyển")
async def give_cmd(interaction: discord.Interaction, user: discord.User, amount: int):
    sender, receiver = str(interaction.user.id), str(user.id)
    if amount <= 0:
        await interaction.response.send_message("❌ Số tiền không hợp lệ.", ephemeral=True)
        return
    if sender == receiver:
        await interaction.response.send_message("❌ Bạn không thể tự chuyển xu cho mình.", ephemeral=True)
        return
    sender_balance = get_balance(sender)
    if sender_balance < amount:
        await interaction.response.send_message(f"❌ Bạn không đủ xu để chuyển.", ephemeral=True)
        return
    change_balance(sender, -amount)
    change_balance(receiver, amount)
    await interaction.response.send_message(
        f"💸 Bạn đã chuyển **{amount} xu** cho {user.mention}. Bạn còn: **{get_balance(sender)} xu**")


# --- Advanced Help Command UI ---

class HelpView(discord.ui.View):
    def __init__(self, interaction: discord.Interaction):
        super().__init__(timeout=180)  # View will time out after 3 minutes
        self.interaction = interaction
        self.add_item(HelpSelect())

    # Optional: Disable the view and components when it times out
    async def on_timeout(self):
        try:
            # Get the original message
            original_message = await self.interaction.original_response()
            # Remove the view (buttons, select menus)
            await original_message.edit(view=None)
        except discord.errors.NotFound:
            # The message might have been deleted
            pass

class HelpSelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label="Trang Chính", description="Giới thiệu tổng quan về bot", emoji="🏠", value="main"),
            discord.SelectOption(label="Lệnh Người Dùng", description="Các lệnh về tiền tệ và thông tin chung", emoji="👤", value="user"),
            discord.SelectOption(label="Thông Tin Đua Ngựa", description="Chi tiết về cách tham gia đua ngựa", emoji="🏇", value="race"),
            discord.SelectOption(label="Lệnh Admin", description="Các lệnh dành riêng cho quản trị viên", emoji="👑", value="admin")
        ]
        super().__init__(placeholder="Chọn một danh mục để xem...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        # Defer the response to prevent "This interaction failed"
        await interaction.response.defer()

        choice = self.values[0]
        embed = None

        if choice == "main":
            embed = discord.Embed(
                title="🏠 Hướng Dẫn Bot Đua Ngựa Shinono Uma Race",
                description="Chào mừng bạn đến với Shinono Uma Race, nơi nhà cái ko đến từ Campuchia mà từ chính server của bạn! Bot cho phép bạn tham gia các cuộc đua ngựa, đặt cược và làm giàu.\n\n"
                            "Sử dụng menu thả xuống bên dưới để khám phá các tính năng và lệnh có sẵn.",
                color=discord.Color.blue()
            )
            embed.add_field(
                name="📜 Nguyên Tắc Cơ Bản",
                value="1. **Nhận xu hàng ngày** với `/umadaily`.\n"
                      "2. **Đặt cược** vào các chú ngựa trong cuộc đua thường với `/umarace`.\n"
                      "3. **Tham gia sự kiện đặc biệt** hàng ngày để có cơ hội thắng lớn.\n"
                      "4. **Theo dõi tài sản** của bạn và của người khác với `/umabalance` và `/umatop`.",
                inline=False
            )
            # --- CREDIT SECTION ADDED HERE ---
            embed.add_field(
                name="⭐ Credit/Contact:",
                value="**Người viết:** Haru Shinono\n"
                      "**GitHub:** [HaruShinono](https://github.com/HaruShinono)\n"
                      "**Discord:** harushinono",
                inline=False
            )
            embed.set_footer(text="Chọn một mục khác trong menu để xem chi tiết.")

        elif choice == "user":
            embed = discord.Embed(title="👤 Lệnh Người Dùng Chung", color=discord.Color.green())
            embed.add_field(name="</umabalance:0>", value="**Mô tả:** Xem số xu hiện tại của bạn hoặc của người khác.\n**Tham số:** `user` (tùy chọn) - Tag người bạn muốn xem.", inline=False)
            embed.add_field(name="</umatop:0>", value="**Mô tả:** Hiển thị bảng xếp hạng 10 người chơi giàu nhất.", inline=False)
            embed.add_field(name="</umadaily:0>", value=f"**Mô tả:** Nhận thưởng xu miễn phí hàng ngày (reset lúc 00:00 UTC+7).\n**Phần thưởng:** {800}-{1500} xu.", inline=False)
            embed.add_field(name="</umagive:0>", value="**Mô tả:** Chuyển xu cho một người chơi khác.\n**Tham số:** `user` (người nhận), `amount` (số xu).", inline=False)

        elif choice == "race":
            embed = discord.Embed(title="🏇 Lệnh & Thông Tin Đua Ngựa", color=discord.Color.purple())
            embed.add_field(
                name="</umarace:0>",
                value=f"**Mô tả:** Bắt đầu một cuộc đua ngựa thường và đặt cược.\n"
                      f"**Tham số:**\n"
                      f"- `horse`: Tên ngựa (có gợi ý khi gõ).\n"
                      f"- `bet`: Số xu cược.\n"
                      f"**Lưu ý:** Cược tối thiểu `{MIN_BET}` xu, cooldown `{cooldown_seconds}` giây.",
                inline=False
            )
            embed.add_field(
                name="✨ Đua Ngựa Đặc Biệt Hàng Ngày",
                value=f"**Mô tả:** Một sự kiện đua đặc biệt diễn ra mỗi ngày với phần thưởng lớn.\n"
                      f"**Cách tham gia:**\n"
                      f"1. Bot sẽ thông báo khi mở cược tại kênh đặc biệt.\n"
                      f"2. Sử dụng menu thả xuống để chọn ngựa.\n"
                      f"3. Một cửa sổ pop-up sẽ hiện ra để bạn nhập số tiền cược.\n"
                      f"**Thông tin:**\n"
                      f"- Mở cược: `{race_open_time:%H:%M}` (UTC+7)\n"
                      f"- Bắt đầu đua: `{race_start_time:%H:%M}` (UTC+7)\n"
                      f"- Cược tối thiểu: `{MIN_SPECIAL_BET}` xu.\n"
                      f"- Thắng nhận: **x{int(SPECIAL_RACE_PAYOUT_MULTIPLIER)}** tiền cược.",
                inline=False
            )

        elif choice == "admin":
            embed = discord.Embed(title="👑 Lệnh Dành Cho Quản Trị Viên", description="Các lệnh này yêu cầu mật khẩu Admin để sử dụng.", color=discord.Color.red())
            embed.add_field(name="</umatopup:0>", value="**Mô tả:** Nạp hoặc trừ xu của người chơi.\n**Tham số:** `user`, `amount` (có thể âm).", inline=False)
            embed.add_field(name="</umasetracechannel:0>", value="**Mô tả:** Cài đặt kênh cho các cuộc đua thường.", inline=False)
            embed.add_field(name="</umasetspecialracechannel:0>", value="**Mô tả:** Cài đặt kênh cho cuộc đua đặc biệt hàng ngày.", inline=False)
            embed.add_field(name="</umasetracehours:0>", value="**Mô tả:** Đặt lại giờ mở cược và giờ đua cho sự kiện đặc biệt (UTC+7).", inline=False)
            embed.add_field(name="</umasetcooldown:0>", value="**Mô tả:** Thay đổi thời gian chờ giữa các lần đua thường.", inline=False)
            embed.add_field(name="</umastatus:0>", value="**Mô tả:** Kiểm tra trạng thái và cấu hình hiện tại của bot.", inline=False)
            embed.add_field(name="</umaevent:0>", value="**Mô tả:** Quản lý các sự kiện và trạng thái hoạt động của bot.\n**Các chế độ:** Bật/tắt sự kiện Vedal/Sonic, bắt đầu/dừng các lịch trình, bật/tắt toàn bộ hệ thống.", inline=False)

        # Edit the original message with the new embed
        if embed:
            await interaction.edit_original_response(embed=embed)


@bot.tree.command(name="umahelp", description="Xem hướng dẫn sử dụng bot một cách chi tiết.")
async def help_cmd(interaction: discord.Interaction):
    initial_embed = discord.Embed(
        title="🏠 Hướng Dẫn Bot Đua Ngựa Shinono Uma Race",
        description="Chào mừng bạn đến với Shinono Uma Race! Bot cho phép bạn tham gia các cuộc đua ngựa, đặt cược và làm giàu.\n\n"
                    "Sử dụng menu thả xuống bên dưới để khám phá các tính năng và lệnh có sẵn.",
        color=discord.Color.blue()
    )
    initial_embed.add_field(
        name="📜 Nguyên Tắc Cơ Bản",
        value="1. **Nhận xu hàng ngày** với `/umadaily`.\n"
              "2. **Đặt cược** vào các chú ngựa trong cuộc đua thường với `/umarace`.\n"
              "3. **Tham gia sự kiện đặc biệt** hàng ngày để có cơ hội thắng lớn.\n"
              "4. **Theo dõi tài sản** của bạn và của người khác với `/umabalance` và `/umatop`.",
        inline=False
    )
    # --- CREDIT SECTION ADDED HERE ---
    initial_embed.add_field(
        name="⭐ Tác giả",
        value="**Người viết:** Haru Shinono\n"
              "**GitHub:** [HaruShinono](https://github.com/HaruShinono)\n"
              "**Discord:** harushinono",
        inline=False
    )
    initial_embed.set_footer(text="Chọn một mục trong menu để bắt đầu.")
    initial_embed.set_thumbnail(url=interaction.client.user.display_avatar.url) # Adds bot's avatar as a thumbnail

    # Send the initial message with the view
    await interaction.response.send_message(embed=initial_embed, view=HelpView(interaction=interaction), ephemeral=False)

# --- Admin Commands ---
@bot.tree.command(name="umatopup", description="[ADMIN] Nạp xu thủ công cho người chơi.")
@app_commands.describe(password="Mật khẩu Admin", user="Người muốn nạp xu", amount="Số xu muốn nạp (có thể âm)")
async def topup_cmd(interaction: discord.Interaction, password: str, user: discord.User, amount: int):
    if not is_admin(password):
        await interaction.response.send_message("❌ Mật khẩu Admin không đúng.", ephemeral=True)
        return
    new_bal = change_balance(str(user.id), amount)
    await interaction.response.send_message(
        f"✅ Đã nạp/trừ **{amount} xu** cho {user.mention}. Số dư mới: **{new_bal} xu**", ephemeral=True)


@bot.tree.command(name="umasetracechannel", description="[ADMIN] Đặt kênh cho các cuộc đua ngựa thường.")
@app_commands.describe(password="Mật khẩu Admin", channel="Kênh dùng để đua ngựa thường")
async def set_race_channel_cmd(interaction: discord.Interaction, password: str, channel: discord.TextChannel):
    if not is_admin(password):
        await interaction.response.send_message("❌ Mật khẩu Admin không đúng.", ephemeral=True)
        return
    set_race_channel_id(channel.id)
    await interaction.response.send_message(f"✅ Đã đặt {channel.mention} làm kênh đua ngựa thường.", ephemeral=True)


@bot.tree.command(name="umasetspecialracechannel", description="[ADMIN] Đặt kênh cho cuộc đua đặc biệt.")
@app_commands.describe(password="Mật khẩu Admin", channel="Kênh dùng cho cuộc đua đặc biệt")
async def set_special_race_channel_cmd(interaction: discord.Interaction, password: str, channel: discord.TextChannel):
    if not is_admin(password):
        await interaction.response.send_message("❌ Mật khẩu Admin không đúng.", ephemeral=True)
        return
    set_special_race_channel_id(channel.id)
    await interaction.response.send_message(f"✅ Đã đặt {channel.mention} làm kênh đua ngựa đặc biệt.", ephemeral=True)


@bot.tree.command(name="umaevent", description="[ADMIN] Quản lý sự kiện và hệ thống đua ngựa.")
@app_commands.describe(password="Mật khẩu Admin", mode="Chế độ sự kiện hoặc hành động")
@app_commands.choices(mode=[
    app_commands.Choice(name="Bật sự kiện Vedal", value="vedal"),
    app_commands.Choice(name="Bật sự kiện Sonic", value="sonic"),
    app_commands.Choice(name="Bật sự kiện cả hai", value="all"),
    app_commands.Choice(name="Tắt sự kiện ngựa đặc biệt", value="off_special_horses"),
    app_commands.Choice(name="Mở cược đặc biệt ngay", value="SetSpecial"),
    app_commands.Choice(name="Bắt đầu đua đặc biệt ngay", value="GoSpecial"),
    app_commands.Choice(name="Bật lịch đua đặc biệt", value="SpecialOn"),
    app_commands.Choice(name="Tắt lịch đua đặc biệt", value="SpecialOff"),
    app_commands.Choice(name="Tắt toàn bộ hệ thống", value="TurnOff"),
    app_commands.Choice(name="Bật toàn bộ hệ thống", value="TurnOn"),
])
async def event_cmd(interaction: discord.Interaction, password: str, mode: app_commands.Choice[str]):
    global special_event_active, special_race_open_for_bets, special_race_message, system_active, current_special_race_task
    if not is_admin(password):
        await interaction.response.send_message("❌ Mật khẩu Admin không đúng.", ephemeral=True)
        return

    mode_value = mode.value
    msg = "❌ Lệnh không xác định."

    if mode_value == "vedal":
        special_event_active = {"vedal": True, "sonic": False};
        msg = "✅ Đã bật sự kiện Vedal."
    elif mode_value == "sonic":
        special_event_active = {"vedal": False, "sonic": True};
        msg = "✅ Đã bật sự kiện Sonic."
    elif mode_value == "all":
        special_event_active = {"vedal": True, "sonic": True};
        msg = "✅ Đã bật sự kiện cả Vedal và Sonic."
    elif mode_value == "off_special_horses":
        special_event_active = {"vedal": False, "sonic": False};
        msg = "✅ Đã tắt các sự kiện ngựa đặc biệt."
    elif mode_value == "SetSpecial":
        if not special_race_open_for_bets:
            # Manually trigger the bet opening part of the scheduler
            special_race_scheduler.cancel()
            await special_race_scheduler.coro(special_race_scheduler)  # Run the task once
            special_race_scheduler.start()
            msg = "✅ Đã mở cược đua đặc biệt ngay lập tức."
        else:
            msg = "⚠️ Cửa cược đua đặc biệt đã mở rồi."
    elif mode_value == "GoSpecial":
        if current_special_race_task and not current_special_race_task.done():
            msg = "❌ Đua đặc biệt đã hoặc đang diễn ra."
        elif not (channel_id := get_special_race_channel_id()) or not (channel := bot.get_channel(channel_id)):
            msg = "❌ Kênh đua đặc biệt chưa được đặt hoặc không hợp lệ."
        else:
            await interaction.response.send_message("✅ Đang khởi chạy cuộc đua đặc biệt...", ephemeral=True)
            current_special_race_task = asyncio.create_task(run_special_race(channel, special_race_bets))
            return  # Avoid sending another response
    elif mode_value == "SpecialOn":
        if not special_race_scheduler.is_running():
            special_race_scheduler.start();
            msg = "✅ Đã bật lịch đua đặc biệt hàng ngày."
        else:
            msg = "⚠️ Lịch đua đặc biệt đã được bật sẵn."
    elif mode_value == "SpecialOff":
        if special_race_scheduler.is_running():
            special_race_scheduler.cancel();
            msg = "✅ Đã tắt lịch đua đặc biệt hàng ngày."
        else:
            msg = "⚠️ Lịch đua đặc biệt đã được tắt sẵn."
    elif mode_value == "TurnOff":
        system_active = False
        if special_race_scheduler.is_running(): special_race_scheduler.cancel()
        if daily_reset_scheduler.is_running(): daily_reset_scheduler.cancel()
        msg = "🔴 ĐÃ TẮT TOÀN BỘ HỆ THỐNG ĐUA NGỰA."
    elif mode_value == "TurnOn":
        system_active = True
        if not special_race_scheduler.is_running(): special_race_scheduler.start()
        if not daily_reset_scheduler.is_running(): daily_reset_scheduler.start()
        msg = "🟢 ĐÃ BẬT LẠI TOÀN BỘ HỆ THỐNG ĐUA NGỰA."

    await interaction.response.send_message(msg, ephemeral=True)


@bot.tree.command(name="umasetracehours", description="[ADMIN] Đặt lại giờ đua đặc biệt (UTC+7).")
@app_commands.describe(password="Mật khẩu Admin", open_hour="Giờ mở cược", open_minute="Phút mở", start_hour="Giờ đua",
                       start_minute="Phút đua")
async def set_race_hours_cmd(interaction: discord.Interaction, password: str, open_hour: int, open_minute: int,
                             start_hour: int, start_minute: int):
    global race_open_time, race_start_time
    if not is_admin(password):
        await interaction.response.send_message("❌ Mật khẩu Admin không đúng.", ephemeral=True)
        return
    try:
        race_open_time = time(open_hour, open_minute, tzinfo=VN_TZ)
        race_start_time = time(start_hour, start_minute, tzinfo=VN_TZ)
        special_race_scheduler.restart()
        await interaction.response.send_message(
            f"✅ Đã cập nhật giờ. Mở cược: **{race_open_time:%H:%M}**, Bắt đầu: **{race_start_time:%H:%M} (UTC+7)**",
            ephemeral=True)
    except ValueError:
        await interaction.response.send_message("❌ Giờ hoặc phút không hợp lệ.", ephemeral=True)


@bot.tree.command(name="umasetcooldown", description="[ADMIN] Thay đổi cooldown đua thường (giây).")
@app_commands.describe(password="Mật khẩu Admin", seconds="Thời gian cooldown mới")
async def set_cooldown_cmd(interaction: discord.Interaction, password: str, seconds: int):
    global cooldown_seconds
    if not is_admin(password):
        await interaction.response.send_message("❌ Mật khẩu Admin không đúng.", ephemeral=True)
        return
    if seconds < 0:
        await interaction.response.send_message("❌ Cooldown không thể âm.", ephemeral=True)
        return
    cooldown_seconds = seconds
    await interaction.response.send_message(f"✅ Đã đặt cooldown là **{cooldown_seconds} giây**.", ephemeral=True)


@bot.tree.command(name="umastatus", description="[ADMIN] Hiển thị trạng thái và cấu hình hiện tại của bot.")
@app_commands.describe(password="Mật khẩu Admin")
async def status_cmd(interaction: discord.Interaction, password: str):
    if not is_admin(password):
        await interaction.response.send_message("❌ Mật khẩu Admin không đúng.", ephemeral=True)
        return

    embed = discord.Embed(title="📊 Trạng Thái Bot Đua Ngựa", color=discord.Color.orange(),
                          timestamp=datetime.now(VN_TZ))

    # System status
    status_text = "🟢 Đang hoạt động" if system_active else "🔴 Đã tắt"
    embed.add_field(name="Hệ thống", value=status_text, inline=False)

    # Channel config
    race_channel_id = get_race_channel_id()
    special_channel_id = get_special_race_channel_id()
    race_ch_text = f"<#{race_channel_id}>" if race_channel_id else "Chưa đặt"
    special_ch_text = f"<#{special_channel_id}>" if special_channel_id else "Chưa đặt"
    embed.add_field(name="Kênh Đua", value=f"Thường: {race_ch_text}\nĐặc biệt: {special_ch_text}", inline=False)

    # Event status
    event_text = "Không có"
    if special_event_active["vedal"] and special_event_active["sonic"]:
        event_text = "Vedal và Sonic"
    elif special_event_active["vedal"]:
        event_text = "Vedal"
    elif special_event_active["sonic"]:
        event_text = "Sonic"
    embed.add_field(name="Sự kiện ngựa đặc biệt", value=event_text, inline=True)

    # Cooldown
    embed.add_field(name="Cooldown đua thường", value=f"{cooldown_seconds} giây", inline=True)

    # Special race scheduler
    scheduler_status = "Đang chạy" if special_race_scheduler.is_running() else "Đã tắt"
    embed.add_field(name="Lịch đua đặc biệt",
                    value=f"Trạng thái: **{scheduler_status}**\nMở cược: `{race_open_time:%H:%M}`\nBắt đầu: `{race_start_time:%H:%M}`",
                    inline=False)

    await interaction.response.send_message(embed=embed, ephemeral=True)


# --- Run bot ---
if __name__ == "__main__":
    bot.run(TOKEN)
