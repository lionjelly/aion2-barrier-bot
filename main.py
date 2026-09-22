import discord
from discord.ext import commands, tasks
from discord import app_commands
from discord.ui import Button, View, Modal, TextInput
import json
import os
import aiohttp
from aiohttp import web
from bs4 import BeautifulSoup
from datetime import datetime, timedelta

CONFIG_FILE = 'config.json'
AUTO_CHANNEL_NAME = "장벽봇"

DAY_NAMES = ["월", "화", "수", "목", "금", "토", "일"]

NAME_MAP = {
    "아그로": "agro",
    "카이라": "kaira",
    "어비스균열": "abyss_rift", "균열": "abyss_rift",
    "어비스보스": "abyss_boss", "어보": "abyss_boss",
    "나흐마": "nahma",
    "아티쟁": "arti", "아티": "arti",
    "쟁탈전": "battle", "쟁탈": "battle"
}

DISPLAY_NAMES = {
    "shugo": "슈고",
    "hourly": "정각",
    "agro": "아그로",
    "kaira": "카이라",
    "abyss_rift": "어비스균열",
    "abyss_boss": "어비스보스",
    "nahma": "나흐마",
    "arti": "아티쟁",
    "battle": "쟁탈전",
    "daily_summary": "오늘의숙제",
    "notice": "공지사항",
    "update": "업데이트"
}

DEFAULT_CONFIG = {
    "channels": {},
    "toggles": {
        "shugo": True,
        "hourly": True,
        "agro": True,
        "kaira": True,
        "abyss_rift": True,
        "abyss_boss": True,
        "nahma": True,
        "arti": True,
        "battle": True,
        "daily_summary": True,
        "notice": True,
        "update": True
    },
    "schedules": {
        "agro": {"name": "아그로", "days": [0, 1, 2, 3, 4, 5, 6], "times": ["01:50", "13:50"], "lead": 10},
        "kaira": {"name": "카이라", "days": [0, 1, 2, 3, 4, 5, 6], "times": ["01:00", "05:00", "09:00", "13:00", "17:00", "21:00"], "lead": 5},
        "abyss_rift": {"name": "어비스균열", "days": [1, 3], "times": ["22:00"], "lead": 10},
        "abyss_boss": {"name": "어비스보스", "days": [2, 5], "times": ["22:30"], "lead": 10},
        "nahma": {"name": "나흐마", "days": [4, 6], "times": ["22:00"], "lead": 10},
        "arti": {"name": "아티쟁", "days": [2, 5], "times": ["22:00"], "lead": 10},
        "battle": {"name": "쟁탈전", "days": [0, 3, 5], "times": ["20:00", "23:00"], "lead": 10}
    },
    "last_notice_id": "",
    "last_update_id": ""
}

sent_alerts = set()

def load_config():
    if not os.path.exists(CONFIG_FILE):
        save_config(DEFAULT_CONFIG)
        return DEFAULT_CONFIG
    with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
        data = json.load(f)
        if "channels" not in data or not isinstance(data["channels"], dict):
            data["channels"] = {}
        for key in DEFAULT_CONFIG["toggles"]:
            if key not in data.get("toggles", {}):
                data.setdefault("toggles", {})[key] = True
        for key in DEFAULT_CONFIG["schedules"]:
            if key not in data.get("schedules", {}):
                data.setdefault("schedules", {})[key] = DEFAULT_CONFIG["schedules"][key]
        return data

def save_config(config_data):
    with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
        json.dump(config_data, f, ensure_ascii=False, indent=4)

config = load_config()

intents = discord.Intents.default()
bot = commands.Bot(command_prefix=[], intents=intents)

def create_welcome_guide_embed():
    embed = discord.Embed(
        title="🛡️ [장벽봇] 채널 이용 및 기능 안내",
        description="안녕하세요! 이 채널은 아이온2 레기온원들을 위해 **보스 젠 타임, 주요 컨텐츠, 공식 홈페이지 공지사항/업데이트**를 실시간으로 자동 알림해주는 공간입니다.",
        color=discord.Color.teal()
    )
    embed.add_field(
        name="🔔 자동 알림 기능 안내",
        value="• **오늘의 숙제**: 매일 19:00 오늘 진행되는 주요 주간 컨텐츠 요약 브리핑\n"
              "• **보스 및 주요 컨텐츠**: 카이라, 아그로, 어비스, 나흐마, 쟁탈전 등 설정된 시간전 사전 알림 발송\n"
              "• **슈고페스타 / 정각**: 매시 55분 슈고페스타 알림 및 정각 알림 발송\n"
              "• **홈페이지 새 글 감지**: 공식 공지사항 및 업데이트 노트 등록 시 5분 이내 자동 알림",
        inline=False
    )
    embed.add_field(
        name="💬 누구나 사용 가능한 명령어",
        value="• `/일정`: 오늘 진행되는 전체 컨텐츠 및 보스 일정 확인 (나에게만 보임)\n"
              "• `/도움말`: 봇 전체 기능 가이드 확인 (나에게만 보임)",
        inline=False
    )
    embed.add_field(
        name="🛠️ 관리자 전용 명령어",
        value="• `/패널`: 각 알림 스위치 ON/OFF 및 보스/컨텐츠 시간·주기 변경 패널 띄우기 (나에게만 보임)",
        inline=False
    )
    embed.set_footer(text="📌 이 안내문은 상단 핀(Pin)에 고정되어 있어 언제든 다시 확인하실 수 있습니다.")
    return embed

async def ensure_guide_message(channel):
    try:
        pins = await channel.pins()
        for pin in pins:
            if pin.author == bot.user and pin.embeds and "장벽봇" in pin.embeds[0].title:
                return

        async for msg in channel.history(limit=20):
            if msg.author == bot.user and msg.embeds and "장벽봇" in msg.embeds[0].title:
                return

        embed = create_welcome_guide_embed()
        msg = await channel.send(embed=embed)
        try:
            await msg.pin()
        except Exception:
            pass
    except Exception as e:
        print(f"가이드 메시지 생성 중 오류: {e}")

async def get_or_create_channel(guild):
    channel = None
    guild_id_str = str(guild.id)
    saved_channels = config.get("channels", {})
    saved_channel_id = saved_channels.get(guild_id_str)

    if saved_channel_id:
        channel = guild.get_channel(saved_channel_id)

    if not channel:
        channel = discord.utils.get(guild.text_channels, name=AUTO_CHANNEL_NAME)
        if channel:
            config.setdefault("channels", {})[guild_id_str] = channel.id
            save_config(config)

    if not channel:
        try:
            channel = await guild.create_text_channel(
                name=AUTO_CHANNEL_NAME,
                topic="아이온2 보스 및 컨텐츠 자동 알림 채널입니다."
            )
            config.setdefault("channels", {})[guild_id_str] = channel.id
            save_config(config)
        except Exception as e:
            print(f"[{guild.name}] 채널 생성 오류: {e}")
            return None

    if channel:
        await ensure_guide_message(channel)
    return channel

def create_panel_embed():
    embed = discord.Embed(title="🛠️ 알림 컨트롤 패널 (관리자 전용)", color=discord.Color.blue())
    embed.add_field(name="📢 알림 채널 안내", value=f"• 설정된 알림 전용 채널로 자동 등록됩니다.", inline=False)
    
    status_lines = []
    for key, disp_name in DISPLAY_NAMES.items():
        is_on = config["toggles"].get(key, True)
        status = "🟢 **ON**" if is_on else "🔴 **OFF**"
        
        if key in config["schedules"]:
            sched = config["schedules"][key]
            times = ", ".join(sched["times"])
            lead = sched["lead"]
            
            days = sched.get("days", [])
            days_str = "매일" if len(days) == 7 else ", ".join([DAY_NAMES[d] for d in sorted(days)])
            status_lines.append(f"• **{disp_name}**: {status} `({days_str} | {times} / {lead}분 전)`")
        else:
            status_lines.append(f"• **{disp_name}**: {status}")

    embed.add_field(name="🎛️ 현재 스위치 및 일정 현황", value="\n".join(status_lines), inline=False)
    embed.add_field(name="💡 도움말", value="일반 명령어: `/일정`, `/도움말`", inline=False)
    return embed

def calculate_repeated_times(base_time_str, interval_hrs):
    try:
        base_dt = datetime.strptime(base_time_str, "%H:%M")
        base_minutes = base_dt.hour * 60 + base_dt.minute
        interval_minutes = interval_hrs * 60
        
        start_min = base_minutes % interval_minutes
        times = []
        curr = start_min
        while curr < 24 * 60:
            h = curr // 60
            m = curr % 60
            times.append(f"{h:02d}:{m:02d}")
            curr += interval_minutes
        return sorted(list(set(times)))
    except Exception:
        return [base_time_str]

class EditTimeModal(Modal, title="컨텐츠 알림 시간/주기 변경"):
    def __init__(self, parent_interaction: discord.Interaction, parent_view: View):
        super().__init__()
        self.parent_interaction = parent_interaction
        self.parent_view = parent_view

    content_name = TextInput(
        label="1. 컨텐츠 이름",
        placeholder="예 : 아그로, 카이라, 어비스균열, 어비스보스, 나흐마, 아티쟁, 쟁탈전",
        required=True
    )
    new_times = TextInput(
        label="2. 알람시간 (HH:MM)",
        placeholder="예: 13:50 (여러 시각 지정 시: 20:00, 23:00)",
        required=True
    )
    interval_hours = TextInput(
        label="3. 알람 주기 (몇 시간 주기로 울리면 되나요?)",
        placeholder="예: 12 (반복 없으면 0)",
        required=False
    )
    lead_time = TextInput(
        label="4. 사전 알림 (몇 분 전 알림을 울리면 되나요?)",
        placeholder="예: 10 (몇 분 전 알림)",
        required=True
    )

    async def on_submit(self, interaction: discord.Interaction):
        input_name = self.content_name.value.strip().replace(" ", "")
        raw_time_input = self.new_times.value.strip()
        
        interval_val = 0
        if self.interval_hours.value and self.interval_hours.value.strip().isdigit():
            interval_val = int(self.interval_hours.value.strip())

        lead_str = self.lead_time.value.strip() if self.lead_time.value else "10"
        try:
            lead_val = int(lead_str)
        except ValueError:
            await interaction.response.send_message("❌ 사전 알림은 숫자만 입력해 주세요.", ephemeral=True)
            return

        key = NAME_MAP.get(input_name)
        if key and key in config["schedules"]:
            if interval_val > 0:
                first_time = raw_time_input.split(',')[0].strip()
                final_times = calculate_repeated_times(first_time, interval_val)
            else:
                final_times = [t.strip() for t in raw_time_input.split(',')]

            config["schedules"][key]["times"] = final_times
            config["schedules"][key]["lead"] = lead_val
            save_config(config)
            
            disp_name = DISPLAY_NAMES[key]
            interval_desc = f"{interval_val}시간 주기" if interval_val > 0 else "단일/지정 시각"
            
            try:
                self.parent_view.update_buttons()
                new_embed = create_panel_embed()
                await self.parent_interaction.edit_original_response(embed=new_embed, view=self.parent_view)
            except Exception as e:
                print(f"패널 자동 갱신 중 예외 발생: {e}")

            await interaction.response.send_message(
                f"✅ **{disp_name}** 알림 설정이 변경되었습니다!\n"
                f"• **설정 방식**: `{interval_desc}`\n"
                f"• **알림 시각**: `{', '.join(final_times)}` \n"
                f"• **사전 알림**: `{lead_val}분 전`",
                ephemeral=True
            )
        else:
            await interaction.response.send_message(
                "❌ 올바른 컨텐츠 이름을 입력해 주세요.\n"
                "📌 **설정 가능 목록**: `아그로`, `카이라`, `어비스균열`, `어비스보스`, `나흐마`, `아티쟁`, `쟁탈전`",
                ephemeral=True
            )

class ControlPanelView(View):
    def __init__(self):
        super().__init__(timeout=None)
        self.update_buttons()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("❌ 이 기능은 서버 관리자만 사용할 수 있습니다.", ephemeral=True)
            return False
        return True

    def update_buttons(self):
        self.clear_items()
        
        count = 0
        for key, disp_name in DISPLAY_NAMES.items():
            is_on = config["toggles"].get(key, True)
            style = discord.ButtonStyle.blurple if is_on else discord.ButtonStyle.secondary
            state_str = "ON" if is_on else "OFF"
            
            button = Button(label=f"{disp_name} {state_str}", style=style, row=count // 3)
            button.callback = self.make_callback(key)
            self.add_item(button)
            count += 1

        edit_btn = Button(label="⚙️ 시간 설정", style=discord.ButtonStyle.gray, row=4)
        edit_btn.callback = self.open_edit_modal
        self.add_item(edit_btn)

        refresh_btn = Button(label="🔄 패널 갱신", style=discord.ButtonStyle.gray, row=4)
        refresh_btn.callback = self.refresh_panel
        self.add_item(refresh_btn)

    def make_callback(self, key):
        async def callback(interaction: discord.Interaction):
            config["toggles"][key] = not config["toggles"].get(key, True)
            save_config(config)
            self.update_buttons()
            embed = create_panel_embed()
            await interaction.response.edit_message(embed=embed, view=self)
        return callback

    async def open_edit_modal(self, interaction: discord.Interaction):
        await interaction.response.send_modal(EditTimeModal(parent_interaction=interaction, parent_view=self))

    async def refresh_panel(self, interaction: discord.Interaction):
        try:
            self.update_buttons()
            embed = create_panel_embed()
            await interaction.response.edit_message(embed=embed, view=self)
        except Exception as e:
            print(f"패널 수동 갱신 중 오류: {e}")

@tasks.loop(seconds=10)
async def schedule_checker():
    try:
        now = datetime.now()
        now_minute_str = now.strftime("%Y-%m-%d %H:%M")

        if config["toggles"].get("daily_summary", True) and now.hour == 19 and now.minute == 0:
            daily_summary_id = f"daily_summary_{now.strftime('%Y%m%d')}"
            if daily_summary_id not in sent_alerts:
                sent_alerts.add(daily_summary_id)
                
                today_kr = DAY_NAMES[now.weekday()] + "요일"
                date_str = now.strftime("%Y년 %m월 %d일")
                
                embed = discord.Embed(
                    title=f"📝 [오늘의 숙제] {date_str} ({today_kr})",
                    description="아이온2 레기온원 여러분! 오늘 저녁 진행되는 주요 주간 컨텐츠 일정입니다.",
                    color=discord.Color.gold()
                )
                
                today_schedules = []
                for key, sched in config["schedules"].items():
                    if key in ["kaira", "agro"]:
                        continue
                    if now.weekday() in sched["days"]:
                        times_str = ", ".join(sched["times"])
                        today_schedules.append(f"• **{sched['name']}**: `{times_str}` ({sched['lead']}분 전 알림)")
                
                if today_schedules:
                    embed.add_field(name="⚔️ 오늘 예정된 주요 주간 컨텐츠", value="\n".join(today_schedules), inline=False)
                else:
                    embed.add_field(name="⚔️ 오늘 예정된 주요 주간 컨텐츠", value="오늘 예정된 주요 주간 컨텐츠가 없습니다.", inline=False)
                
                embed.set_footer(text="장벽봇 | 매일 19시 자동 일일 요약")
                
                for guild in bot.guilds:
                    channel = await get_or_create_channel(guild)
                    if channel:
                        await channel.send(embed=embed)

        if config["toggles"].get("shugo") and now.minute == 55:
            alert_id = f"shugo_{now.strftime('%Y%m%d_%H55')}"
            if alert_id not in sent_alerts:
                sent_alerts.add(alert_id)
                next_hour_str = (now + timedelta(minutes=5)).strftime("%H:00")
                for guild in bot.guilds:
                    channel = await get_or_create_channel(guild)
                    if channel:
                        await channel.send(f"🔔 **[슈고페스타]** 5분 후에 시작됩니다! ({next_hour_str} 시작)")

        if config["toggles"].get("hourly") and now.minute == 0:
            alert_id = f"hourly_{now.strftime('%Y%m%d_%H00')}"
            if alert_id not in sent_alerts:
                sent_alerts.add(alert_id)
                for guild in bot.guilds:
                    channel = await get_or_create_channel(guild)
                    if channel:
                        await channel.send(f"⏰ **[정각 알림]** 현재 {now.hour}시 정각입니다.")

        for key, sched in config["schedules"].items():
            if not config["toggles"].get(key, True):
                continue

            for day_offset in [0, 1]:
                check_date = now.date() + timedelta(days=day_offset)
                if check_date.weekday() in sched["days"]:
                    for t_str in sched["times"]:
                        try:
                            h, m = map(int, t_str.split(":"))
                            event_dt = datetime(check_date.year, check_date.month, check_date.day, h, m)
                            alert_dt = event_dt - timedelta(minutes=sched["lead"])

                            if alert_dt.strftime("%Y-%m-%d %H:%M") == now_minute_str:
                                alert_id = f"{key}_{event_dt.strftime('%Y%m%d_%H%M')}"
                                if alert_id not in sent_alerts:
                                    sent_alerts.add(alert_id)
                                    for guild in bot.guilds:
                                        channel = await get_or_create_channel(guild)
                                        if channel:
                                            await channel.send(f"📢 **[{sched['name']}]** {sched['lead']}분 후에 시작됩니다! ({t_str} 시작)")
                        except Exception as e:
                            print(f"시간 계산 오류 ({key}): {e}")

    except Exception as e:
        print(f"스케줄러 오류: {e}")

async def check_board_posts(url, config_key, category_title, embed_color):
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8"
    }
    try:
        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.get(url, timeout=10) as resp:
                if resp.status != 200:
                    print(f"[{category_title}] HTTP 접근 실패 (상태 코드: {resp.status})")
                    return
                
                html = await resp.text()
                soup = BeautifulSoup(html, 'html.parser')

                latest_title = None
                latest_url = None

                next_data = soup.find('script', id='__NEXT_DATA__')
                if next_data:
                    try:
                        data = json.loads(next_data.string)
                        page_props = data.get('props', {}).get('pageProps', {})
                        posts_data = page_props.get('posts') or page_props.get('list') or page_props.get('articleList') or []
                        
                        if posts_data and isinstance(posts_data, list):
                            first_post = posts_data[0]
                            latest_title = first_post.get('title') or first_post.get('subject')
                            article_id = first_post.get('id') or first_post.get('articleId')
                            if article_id:
                                base_board_url = url.split('/list')[0]
                                latest_url = f"{base_board_url}/view?articleId={article_id}"
                    except Exception as e:
                        print(f"[{category_title}] JSON 데이터 파싱 스킵: {e}")

                if not latest_url:
                    all_links = soup.find_all('a', href=True)
                    valid_posts = []

                    for a in all_links:
                        href = a['href']
                        if ('/board/' in href or '/article/' in href) and not href.endswith('/list') and '/list?' not in href:
                            text = a.get_text(strip=True)
                            if text and len(text) > 2:
                                full_href = href if href.startswith("http") else "https://aion2.plaync.com" + href
                                valid_posts.append((text, full_href))

                    if valid_posts:
                        latest_title, latest_url = valid_posts[0]

                if not latest_url:
                    print(f"[{category_title}] 유효한 최신 게시글을 찾지 못했습니다.")
                    return

                print(f"[{category_title}] 현재 최신글 감지: {latest_title} ({latest_url})")

                last_id = config.get(config_key, "")

                if not last_id:
                    config[config_key] = latest_url
                    save_config(config)
                    print(f"[{category_title}] 최초 기준점 저장 완료: {latest_url}")
                    return

                if latest_url != last_id:
                    config[config_key] = latest_url
                    save_config(config)

                    embed = discord.Embed(
                        title=f"{category_title} 새로운 글이 등록되었습니다!",
                        description=f"**[{latest_title}]({latest_url})**",
                        color=embed_color
                    )
                    embed.set_footer(text="아이온2 공식 홈페이지")

                    for guild in bot.guilds:
                        channel = await get_or_create_channel(guild)
                        if channel:
                            await channel.send(embed=embed)
                    print(f"[{category_title}] 🔔 새 글 알림 발송 완료!")

    except Exception as e:
        print(f"[{category_title}] 크롤링 중 오류 발생: {e}")

@tasks.loop(minutes=5)
async def web_notice_checker():
    if config["toggles"].get("notice", True):
        await check_board_posts(
            url="https://aion2.plaync.com/ko-kr/board/notice/list",
            config_key="last_notice_id",
            category_title="📢 [공지사항]",
            embed_color=discord.Color.red()
        )

    if config["toggles"].get("update", True):
        await check_board_posts(
            url="https://aion2.plaync.com/ko-kr/board/update/list",
            config_key="last_update_id",
            category_title="📝 [업데이트 노트]",
            embed_color=discord.Color.blue()
        )

@bot.event
async def on_ready():
    print(f'성공적으로 로그인했습니다: {bot.user}')
    
    # Render 무료 포트 응답용 서버 시작
    app = web.Application()
    app.router.add_get('/', lambda r: web.Response(text="Bot Alive"))
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 10000))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    
    try:
        synced = await bot.tree.sync()
        print(f"전역 슬래시 명령어 {len(synced)}개 동기화 완료.")
    except Exception as e:
        print(f"슬래시 동기화 실패: {e}")

    for guild in bot.guilds:
        await get_or_create_channel(guild)
    
    if not schedule_checker.is_running():
        schedule_checker.start()

    if not web_notice_checker.is_running():
        web_notice_checker.start()

@bot.tree.command(name="패널", description="알림 컨트롤 패널을 출력합니다. (관리자 전용)")
@app_commands.checks.has_permissions(administrator=True)
async def command_panel(interaction: discord.Interaction):
    embed = create_panel_embed()
    view = ControlPanelView()
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

@command_panel.error
async def command_panel_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message("❌ `/패널` 명령어는 서버 관리자만 사용할 수 있습니다.", ephemeral=True)

@bot.tree.command(name="도움말", description="장벽봇 사용 도움말을 확인합니다.")
async def command_help(interaction: discord.Interaction):
    embed = discord.Embed(
        title="📖 장벽봇 도움말 및 기능 안내",
        description="아이온2 레기온원들을 위한 자동 알림 봇 사용 안내입니다.",
        color=discord.Color.green()
    )
    embed.add_field(
        name="🛠 주요 명령어",
        value="• `/일정`: 오늘 예정된 아이온2 컨텐츠 일정 확인 (나에게만 보임)\n"
              "• `/도움말`: 봇 도움말 보기 (나에게만 보임)\n"
              "• `/패널`: 알림 ON/OFF 컨트롤 패널 (관리자 전용 / 나에게만 보임)",
        inline=False
    )
    embed.add_field(
        name="⚙️ 기능 안내",
        value="• **자동 채널 인식**: 설정된 전용 채널로 자동 알림이 발송됩니다.\n"
              "• **오늘의 숙제**: 매일 19시 오늘 진행되는 주요 주간 컨텐츠를 자동 브리핑합니다.\n"
              "• **홈페이지 감지**: 공지사항 및 업데이트 노트를 5분 간격으로 자동 확인합니다.",
        inline=False
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="일정", description="오늘 진행되는 컨텐츠 일정을 확인합니다.")
async def command_schedule(interaction: discord.Interaction):
    now = datetime.now()
    today_kr = DAY_NAMES[now.weekday()] + "요일"
    
    embed = discord.Embed(
        title=f"📅 오늘({today_kr}) 컨텐츠 일정 요약",
        color=discord.Color.gold()
    )
    
    agro_info = config["schedules"].get("agro", {})
    agro_times = ", ".join(agro_info.get("times", []))
    agro_lead = agro_info.get("lead", 10)

    kaira_info = config["schedules"].get("kaira", {})
    kaira_times = ", ".join(kaira_info.get("times", []))
    kaira_lead = kaira_info.get("lead", 5)
    
    embed.add_field(
        name="🔄 상시 및 주기 컨텐츠",
        value=f"• **아그로**: `{agro_times}` ({agro_lead}분 전 알림)\n"
              f"• **카이라**: `{kaira_times}` ({kaira_lead}분 전 알림)",
        inline=False
    )
    
    today_schedules = []
    for key, sched in config["schedules"].items():
        if key in ["kaira", "agro"]:
            continue
        if now.weekday() in sched["days"]:
            times_str = ", ".join(sched["times"])
            today_schedules.append(f"• **{sched['name']}**: `{times_str}` ({sched['lead']}분 전 알림)")
    
    if today_schedules:
        embed.add_field(name=f"⚔️ 오늘({today_kr}) 예정된 주요 주간 컨텐츠", value="\n".join(today_schedules), inline=False)
    else:
        embed.add_field(name=f"⚔️ 오늘({today_kr}) 예정된 주요 주간 컨텐츠", value="오늘 예정된 주요 주간 컨텐츠가 없습니다.", inline=False)
        
    await interaction.response.send_message(embed=embed, ephemeral=True)

TOKEN = os.environ.get("DISCORD_TOKEN")
bot.run(TOKEN)
