# bot.py
# Discord Horse Race - with admin commands (topup, event, setracechannel)
# Requires: discord.py, python-dotenv

import discord
from discord import app_commands
from discord.ext import commands
import asyncio, random, csv, os
from datetime import datetime, timezone
from dotenv import load_dotenv
from typing import Dict, List, Optional

# === global race lock dictionary (ngăn nhiều đua cùng lúc) ===
race_locks = {}

# === load token and admin password ===
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD")

if not TOKEN:
    raise RuntimeError("Set DISCORD_TOKEN in .env")
if not ADMIN_PASSWORD:
    raise RuntimeError("Set ADMIN_PASSWORD in .env")

# === bot setup ===
INTENTS = discord.Intents.default()
INTENTS.message_content = True
bot = commands.Bot(command_prefix="!", intents=INTENTS)

# === config constants ===
BALANCE_FILE = "balances.csv"
DAILY_FILE = "daily.csv"
HORSE_HISTORY_FILE = "horse_history.csv"
RACE_CHANNEL_FILE = "race_channel.csv"  # new file for allowed channel id

START_BALANCE = 10000
MIN_BET = 200

# === simple file helpers ===
def load_csv_map(filepath: str) -> Dict[str, str]:
    if not os.path.exists(filepath):
        return {}
    out = {}
    with open(filepath, newline="", encoding="utf-8") as f:
        for row in csv.reader(f):
            if len(row) >= 2:
                out[row[0]] = row[1]
    return out

def save_csv_map(filepath: str, d: Dict[str, str]):
    with open(filepath, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        for k, v in d.items():
            w.writerow([k, v])

# === balances ===
def load_balances() -> Dict[str, int]:
    raw = load_csv_map(BALANCE_FILE)
    out = {}
    for k, v in raw.items():
        try:
            out[k] = int(v)
        except:
            out[k] = START_BALANCE
    return out

def save_balances(b: Dict[str, int]):
    raw = {k: str(v) for k, v in b.items()}
    save_csv_map(BALANCE_FILE, raw)

def get_balance(uid: str) -> int:
    b = load_balances()
    if uid not in b:
        b[uid] = START_BALANCE
        save_balances(b)
    return b[uid]

def change_balance(uid: str, delta: int) -> int:
    b = load_balances()
    cur = b.get(uid, START_BALANCE)
    cur += delta
    if cur < 0:
        cur = 0
    b[uid] = cur
    save_balances(b)
    return cur

# === race channel management ===
def load_race_channel() -> Optional[int]:
    data = load_csv_map(RACE_CHANNEL_FILE)
    if "channel_id" in data:
        try:
            return int(data["channel_id"])
        except:
            return None
    return None

def save_race_channel(cid: int):
    save_csv_map(RACE_CHANNEL_FILE, {"channel_id": str(cid)})

# === special horse event memory ===
current_special_event: List[str] = []  # e.g. ["Vedal", "Sonic"]

# === on_ready ===
@bot.event
async def on_ready():
    try:
        await bot.tree.sync()
    except Exception as e:
        print(f"Error syncing commands: {e}")
    print(f"✅ Logged in as {bot.user} — slash commands synced.")

# === /topup ===
@bot.tree.command(name="topup", description="Nạp xu cho người chơi (admin only)")
@app_commands.describe(password="Mật khẩu admin", user="Người nhận", amount="Số xu cần thêm")
async def topup_cmd(interaction: discord.Interaction, password: str, user: discord.User, amount: int):
    if password != ADMIN_PASSWORD:
        await interaction.response.send_message("❌ Sai mật khẩu admin.", ephemeral=True)
        return
    if amount <= 0:
        await interaction.response.send_message("❌ Số tiền phải lớn hơn 0.", ephemeral=True)
        return
    new_bal = change_balance(str(user.id), amount)
    await interaction.response.send_message(f"✅ Đã nạp **{amount} xu** cho {user.mention}. Tổng: **{new_bal} xu**")

# === /setracechannel ===
@bot.tree.command(name="setracechannel", description="Chỉ định kênh được phép đua ngựa (admin only)")
@app_commands.describe(password="Mật khẩu admin", channel="Kênh được phép đua")
async def setracechannel_cmd(interaction: discord.Interaction, password: str, channel: discord.TextChannel):
    if password != ADMIN_PASSWORD:
        await interaction.response.send_message("❌ Sai mật khẩu admin.", ephemeral=True)
        return
    save_race_channel(channel.id)
    await interaction.response.send_message(f"✅ Đã đặt kênh đua ngựa: {channel.mention}")

# === /event ===
@bot.tree.command(name="event", description="Kích hoạt ngựa đặc biệt cho cuộc đua sắp tới (admin only)")
@app_commands.describe(password="Mật khẩu admin", horse="Tên ngựa đặc biệt (Vedal, Sonic, all)")
async def event_cmd(interaction: discord.Interaction, password: str, horse: str):
    if password != ADMIN_PASSWORD:
        await interaction.response.send_message("❌ Sai mật khẩu admin.", ephemeral=True)
        return
    horse = horse.lower()
    global current_special_event
    if horse == "all":
        current_special_event = ["Vedal", "Sonic"]
    elif horse in ["vedal", "sonic"]:
        current_special_event = [horse.capitalize()]
    else:
        await interaction.response.send_message("❌ Ngựa không hợp lệ. Dùng: vedal, sonic, all", ephemeral=True)
        return
    await interaction.response.send_message(f"🌟 Đã kích hoạt event: **{', '.join(current_special_event)}** cho cuộc đua kế tiếp!")

# === /umarace ===
@bot.tree.command(name="umarace", description="Tham gia đua ngựa và đặt cược xu (>=200).")
@app_commands.describe(bet="Số xu bạn muốn cược (>=200)")
async def umarace(interaction: discord.Interaction, bet: int):
    # === kiểm tra kênh đua ===
    allowed = load_race_channel()
    if not allowed:
        await interaction.response.send_message("⚠️ Chưa đặt kênh đua ngựa. Hãy dùng /setracechannel để cấu hình trước.", ephemeral=True)
        return
    if interaction.channel_id != allowed:
        await interaction.response.send_message("❌ Bạn chỉ có thể đua ngựa trong kênh được chỉ định.", ephemeral=True)
        return

    uid = str(interaction.user.id)
    bal = get_balance(uid)
    if bet < MIN_BET:
        await interaction.response.send_message(f"⚠️ Mức cược tối thiểu là **{MIN_BET} xu**.", ephemeral=True)
        return
    if bet > bal:
        await interaction.response.send_message(f"❌ Bạn không đủ xu để cược! (Hiện có: {bal} xu)", ephemeral=True)
        return

    cid = interaction.channel_id
    if race_locks.get(cid):
        await interaction.response.send_message("❌ Đã có cuộc đua đang diễn ra trong kênh này. Vui lòng chờ.", ephemeral=True)
        return

    # --- special horses event (từ /event) ---
    from copy import deepcopy
    HORSE_POOL_BASE = [
        {"emoji": "<:gold_ship:1427257561802997841>", "name": "Gold Ship"},
        {"emoji": "<:haru_urara:1427257575996391497>", "name": "Haru Urara"},
        {"emoji": "<:kitasan_black:1427257395247185960>", "name": "Kitasan Black"},
        {"emoji": "<:oguri_cap:1427257240062132374>", "name": "Oguri Cap"},
        {"emoji": "<:satono_diamond:1427257543603781672>", "name": "Satono Diamond"},
        {"emoji": "<:special_week:1427257295812952164>", "name": "Special Week"},
        {"emoji": "<:tamamo_cross:1427257518538887240>", "name": "Tamamo Cross"},
        {"emoji": "<:tokai_teio:1427257361059414037>", "name": "Tokai Teio"},
    ]
    SPECIAL_HORSES_POOL = [
        {"emoji": "<:vedalNom:1427276755194085457>", "name": "Vedal", "special_type": "vedal"},
        {"emoji": "<:sonic:1427276770184527873>", "name": "Sonic", "special_type": "sonic"},
    ]

    race_participants = deepcopy(HORSE_POOL_BASE)
    special_message = ""

    # nếu admin /event kích hoạt, thêm ngựa đó
    if current_special_event:
        for h in SPECIAL_HORSES_POOL:
            if h["name"] in current_special_event:
                race_participants.append(h)
        special_message = f"🌟 **Sự kiện đặc biệt**: {', '.join(current_special_event)} đã tham gia cuộc đua!"
        current_special_event.clear()  # reset sau khi dùng

    else:
        # nếu không có event admin thì roll random như trước
        roll = random.random()
        if roll < 0.05:
            race_participants.append(SPECIAL_HORSES_POOL[0])
            race_participants.append(SPECIAL_HORSES_POOL[1])
            special_message = "💥 **Sự kiện đặc biệt: Vedal và Sonic đã tham gia cuộc đua!**"
        elif roll < 0.15:
            chosen_special = random.choice(SPECIAL_HORSES_POOL)
            race_participants.append(chosen_special)
            special_message = f"🌟 **Sự kiện đặc biệt: {chosen_special['name']} đã xuất hiện!**"

    options = [discord.SelectOption(label=h["name"], value=h["name"], emoji=h["emoji"]) for h in HORSE_POOL_BASE]

    # giữ nguyên toàn bộ logic gốc của bạn bên dưới
    class HorseSelect(discord.ui.Select):
        def __init__(self):
            super().__init__(placeholder="Chọn ngựa để cược...", min_values=1, max_values=1, options=options)

        async def callback(self, select_inter: discord.Interaction):
            chosen_name = self.values[0]
            chosen = next(x for x in HORSE_POOL_BASE if x["name"] == chosen_name)
            new_bal_after_bet = change_balance(uid, -bet)

            def init_stats():
                return {
                    "speed": random.randint(200, 1200),
                    "power": random.randint(200, 1200),
                    "stamina": random.randint(200, 1200),
                    "agility": random.randint(200, 1200),
                    "focus": random.randint(200, 1200)
                }

            def get_special_horse_stats(t):
                if t == "vedal":
                    return {"speed":1,"power":1,"stamina":1,"agility":1,"focus":1}
                elif t == "sonic":
                    return {"speed":9999,"power":9999,"stamina":9999,"agility":9999,"focus":9999}
                return init_stats()

            initial_stats_for_race = {}
            for p in race_participants:
                if "special_type" in p:
                    initial_stats_for_race[p["name"]] = get_special_horse_stats(p["special_type"])
                else:
                    initial_stats_for_race[p["name"]] = init_stats()

            s_ch = initial_stats_for_race[chosen["name"]]
            odds_display = round(random.uniform(2.5, 4.0), 1)

            header_lines = []
            header_lines.append("🏇 CUỘC ĐUA SẮP BẮT ĐẦU!")
            header_lines.append("")
            if special_message:
                header_lines.append(f"{special_message}\n")
            header_lines.append(f"Bạn chọn: {chosen['emoji']} **{chosen['name']}**")
            header_lines.append(f"Tỷ lệ cược (tham khảo hiển thị): **x{odds_display}**")
            header_lines.append(f"Chỉ số: 🏃‍♀️{s_ch['speed']} 💪{s_ch['power']} ⚡{s_ch['stamina']} 💃{s_ch['agility']} 💡{s_ch['focus']}")
            header_lines.append(f"Cược: **{bet} xu** → Có thể thắng: **{int(round(bet*odds_display))} xu**")
            header_lines.append("")
            header_lines.append("━━━━━━━━━━━━━━━━━━━━🏁")
            header_lines.append("Đang khởi động...")
            header_lines.append("")
            header_lines.append(f"Số dư sau khi cược: {new_bal_after_bet} xu — {datetime.now(timezone.utc).astimezone().strftime('%c')}")
            initial_race_content = "\n".join(header_lines)

            await select_inter.response.edit_message(content=initial_race_content, view=None)
            msg = await select_inter.original_response()
            race_locks[cid] = True
            try:
                await run_race(select_inter, uid, bet, chosen, race_participants, msg, new_bal_after_bet, initial_stats_for_race, odds_display, special_message)
            finally:
                race_locks[cid] = False

    view = discord.ui.View(timeout=30)
    view.add_item(HorseSelect())
    await interaction.response.send_message("🎲 Vui lòng chọn ngựa để đặt cược (dropdown):", view=view)

# === /balance ===
@bot.tree.command(name="balance", description="Xem số xu hiện tại của bạn hoặc người khác")
@app_commands.describe(user="Người muốn xem (tùy chọn)")
async def balance_cmd(interaction: discord.Interaction, user: Optional[discord.User] = None):
    target = user or interaction.user
    bal = get_balance(str(target.id))
    await interaction.response.send_message(f"💰 **{target.display_name}** hiện có **{bal} xu**")


# === /top ===
@bot.tree.command(name="top", description="Xem top 10 người giàu nhất")
async def top_cmd(interaction: discord.Interaction):
    b = load_balances()
    if not b:
        await interaction.response.send_message("Chưa có người chơi nào.")
        return
    items = sorted(b.items(), key=lambda kv: kv[1], reverse=True)[:10]

    embed = discord.Embed(
        title="🏆 Top 10 Người Giàu Nhất 🏆",
        description="Những tay chơi cừ khôi nhất!",
        color=discord.Color.gold()
    )

    for i, (uid, coins) in enumerate(items, start=1):
        user_obj = bot.get_user(int(uid))  # Try to get user object
        username = user_obj.display_name if user_obj else f"Người dùng không rõ ({uid})"
        embed.add_field(name=f"{i}. {username}", value=f"**{coins} xu**", inline=False)

    await interaction.response.send_message(embed=embed)


# === /daily ===
@bot.tree.command(name="daily", description="Nhận thưởng hàng ngày (1-2000 xu, 1 lần/ngày)")
async def daily_cmd(interaction: discord.Interaction):
    uid = str(interaction.user.id)
    daily = load_daily()
    today = datetime.now(timezone.utc).date().isoformat()
    last = daily.get(uid)
    if last == today:
        await interaction.response.send_message("❌ Bạn đã nhận thưởng hôm nay rồi.")
        return
    reward = random.randint(1, 2000)
    new = change_balance(uid, reward)
    daily[uid] = today
    save_daily(daily)
    await interaction.response.send_message(f"🎁 Bạn nhận được **{reward} xu**! Tổng: **{new} xu**")


# === /give ===
@bot.tree.command(name="give", description="Chuyển xu cho người khác")
@app_commands.describe(user="Người nhận", amount="Số xu muốn chuyển")
async def give_cmd(interaction: discord.Interaction, user: discord.User, amount: int):
    sender = str(interaction.user.id)
    receiver = str(user.id)
    if amount <= 0:
        await interaction.response.send_message("❌ Số tiền không hợp lệ.", ephemeral=True)
        return
    if sender == receiver:
        await interaction.response.send_message("❌ Bạn không thể tự chuyển xu cho mình.", ephemeral=True)
        return
    if get_balance(sender) < amount:
        await interaction.response.send_message("❌ Bạn không đủ xu để chuyển.", ephemeral=True)
        return
    change_balance(sender, -amount)
    change_balance(receiver, amount)
    await interaction.response.send_message(
        f"💸 Bạn đã chuyển **{amount} xu** cho {user.mention}. Tổng bạn còn: **{get_balance(sender)} xu**")


# === run bot ===
bot.run(TOKEN)

