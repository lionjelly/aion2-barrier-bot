import io
import os
import re
import json
import asyncio
import logging
from datetime import datetime
from zoneinfo import ZoneInfo
 
import aiohttp
from aiohttp import web
import discord
from discord import app_commands
from discord.ext import commands, tasks
 
# ─────────────────────────────────────────
# 기본 설정
# ─────────────────────────────────────────
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("barrier_bot")
 
TOKEN = os.environ.get("DISCORD_TOKEN")
PORT = int(os.environ.get("PORT", 10000))
KST = ZoneInfo(os.environ.get("TZ", "Asia/Seoul"))
AUTO_CHANNEL_NAME = "🤖｜장벽봇"
PANEL_TITLE = "🛡️ 장벽봇 · 주간 콘텐츠 시간표"
SCHEDULE_FILE = "schedule.json"
BACKUP_FILENAME = "장벽봇_일정백업.json"   # 패널에 붙는 백업 파일 (재배포 후 복원용)
DAILY_SUMMARY_TIME = "19:00"              # 매일 오늘 일정 요약 알림 (끄려면 None)
 
DAY_NAMES = ["월", "화", "수", "목", "금", "토", "일"]
DEFAULT_TEMPLATE = "📢 **[{이름}]** {분}분 후 시작합니다! ({시간} 시작)"
 
# ─────────────────────────────────────────
# 콘텐츠 기본 시간표 (주간 콘텐츠 일정 이미지 기준, 어비스균열 21:20 반영)
# days: 0=월 1=화 2=수 3=목 4=금 5=토 6=일
# ─────────────────────────────────────────
DEFAULT_CONTENTS = {
    "abyss_rift": {"name": "어비스균열", "emoji": "🌀", "days": [1, 3],
                   "times": ["21:20"], "before": 10},
    "abyss_boss": {"name": "어비스보스", "emoji": "👹", "days": [2, 5],
                   "times": ["22:30"], "before": 10},
    "nahma":      {"name": "나흐마", "emoji": "🗡️", "days": [4, 6],
                   "times": ["22:00"], "before": 10},
    "atijaeng":   {"name": "아티쟁", "emoji": "🏰", "days": [2, 5],
                   "times": ["22:00"], "before": 10},
    "conquest":   {"name": "쟁탈전", "emoji": "⚔️", "days": [0, 3, 5],
                   "times": ["20:00", "23:00"], "before": 10},
    "kaira":      {"name": "카이라", "emoji": "👑", "days": [0, 1, 2, 3, 4, 5, 6],
                   "times": ["01:00", "05:00", "09:00", "13:00", "17:00", "21:00"], "before": 5},
}
for _c in DEFAULT_CONTENTS.values():
    _c.setdefault("enabled", True)
    _c.setdefault("message", DEFAULT_TEMPLATE)
 
contents: dict[str, dict] = {}
schedule_loaded = False
panel_messages: set[tuple[int, int]] = set()   # (channel_id, message_id)
last_alert_key = ""
 
# ─────────────────────────────────────────
# PlayNC 게시판 (페이지가 실제로 쓰는 API 직접 호출)
# ─────────────────────────────────────────
API_BASE = "https://api-community.plaync.com/aion2/board"
BOARDS = {
    "notice": {
        "api_key": "notice_ko",
        "list_url": "https://aion2.plaync.com/ko-kr/board/notice/list",
        "view_url": "https://aion2.plaync.com/ko-kr/board/notice/view?articleId={id}",
        "label": "공지사항",
        "alert_title": "📢 [공지사항] 새 글이 등록되었습니다!",
        "color": discord.Color.blue(),
    },
    "update": {
        "api_key": "update_ko",
        "list_url": "https://aion2.plaync.com/ko-kr/board/update/list",
        "view_url": "https://aion2.plaync.com/ko-kr/board/update/view?articleId={id}",
        "label": "업데이트",
        "alert_title": "🚀 [업데이트] 새 패치노트가 등록되었습니다!",
        "color": discord.Color.green(),
    },
}
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
    "Origin": "https://aion2.plaync.com",
    "Referer": "https://aion2.plaync.com/",
}
seen_ids = {"notice": None, "update": None}
 
 
# ─────────────────────────────────────────
# 시간/요일 도우미
# ─────────────────────────────────────────
TIME_RE = re.compile(r"^(\d{1,2})\s*[:：]\s*(\d{2})$")
 
 
def normalize_time(text: str):
    m = TIME_RE.match(text.strip())
    if not m:
        return None
    h, mi = int(m.group(1)), int(m.group(2))
    if h > 23 or mi > 59:
        return None
    return f"{h:02d}:{mi:02d}"
 
 
def to_minutes(t: str) -> int:
    h, m = t.split(":")
    return int(h) * 60 + int(m)
 
 
def days_text(days: list[int]) -> str:
    if len(days) == 7:
        return "매일"
    if sorted(days) == [0, 1, 2, 3, 4]:
        return "평일"
    if sorted(days) == [5, 6]:
        return "주말"
    return "·".join(DAY_NAMES[d] for d in sorted(days))
 
 
def render_message(c: dict, start_time: str) -> str:
    return (c["message"]
            .replace("{이름}", c["name"])
            .replace("{시간}", start_time)
            .replace("{분}", str(c["before"])))
 
 
def sanitize_contents(data: dict) -> dict:
    """불러온 데이터가 깨져 있어도 봇이 죽지 않도록 정리"""
    result = {}
    for key, c in data.items():
        try:
            times = sorted({t for t in (normalize_time(x) for x in c["times"]) if t})
            days = sorted({int(d) for d in c["days"] if 0 <= int(d) <= 6})
            if not times or not days:
                continue
            result[key] = {
                "name": str(c["name"]),
                "emoji": str(c.get("emoji", "📌")),
                "days": days,
                "times": times,
                "before": max(0, min(180, int(c.get("before", 10)))),
                "enabled": bool(c.get("enabled", True)),
                "message": str(c.get("message") or DEFAULT_TEMPLATE),
            }
        except Exception:
            continue
    return result
 
 
# ─────────────────────────────────────────
# 저장 / 복원
# ─────────────────────────────────────────
def contents_json() -> str:
    return json.dumps(contents, ensure_ascii=False, indent=2)
 
 
def backup_file() -> discord.File:
    return discord.File(io.BytesIO(contents_json().encode("utf-8")), filename=BACKUP_FILENAME)
 
 
def save_local():
    try:
        with open(SCHEDULE_FILE, "w", encoding="utf-8") as f:
            f.write(contents_json())
    except Exception as e:
        logger.warning(f"일정 파일 저장 실패: {e}")
 
 
async def restore_schedule():
    """
    시작 시 복원 순서: 1) schedule.json  2) 알림 채널 패널에 붙은 백업 파일  3) 기본 시간표
    (Render 무료 플랜은 재배포하면 파일이 지워지므로 2번이 실제 백업 역할)
    """
    global contents, schedule_loaded
    if schedule_loaded:
        return
 
    loaded = None
    try:
        with open(SCHEDULE_FILE, encoding="utf-8") as f:
            loaded = sanitize_contents(json.load(f)) or None
        if loaded:
            logger.info("[일정] 로컬 파일에서 복원")
    except FileNotFoundError:
        pass
    except Exception as e:
        logger.warning(f"일정 파일 읽기 실패: {e}")
 
    for ch in get_target_channels():
        try:
            async for msg in ch.history(limit=50):
                if not (msg.author.id == bot.user.id and msg.embeds
                        and msg.embeds[0].title == PANEL_TITLE):
                    continue
                panel_messages.add((ch.id, msg.id))
                if loaded is None:
                    for att in msg.attachments:
                        if att.filename == BACKUP_FILENAME:
                            data = sanitize_contents(json.loads(await att.read()))
                            if data:
                                loaded = data
                                logger.info(f"[일정] #{ch.name} 패널 백업에서 복원")
        except Exception as e:
            logger.warning(f"#{ch.name} 패널 검색 실패: {e}")
 
    if loaded is None:
        loaded = json.loads(json.dumps(DEFAULT_CONTENTS))
        logger.info("[일정] 기본 시간표 사용")
 
    contents = loaded
    save_local()
    schedule_loaded = True
 
 
# ─────────────────────────────────────────
# 임베드
# ─────────────────────────────────────────
def today_lines(weekday: int) -> list[str]:
    items = []
    for c in contents.values():
        if weekday in c["days"]:
            for t in c["times"]:
                items.append((t, f"`{t}` {c['emoji']} {c['name']}" + ("" if c["enabled"] else " (알림 꺼짐)")))
    return [line for _, line in sorted(items)]
 
 
def build_panel_embed() -> discord.Embed:
    now = datetime.now(KST)
    lines = []
    for c in contents.values():
        status = f"🔔 {c['before']}분 전" if c["enabled"] else "🔕 알림 꺼짐"
        lines.append(f"{c['emoji']} **{c['name']}** — {days_text(c['days'])}\n"
                     f"┗ ⏰ {' · '.join(c['times'])}  |  {status}")
    embed = discord.Embed(
        title=PANEL_TITLE,
        description="\n".join(lines) or "등록된 콘텐츠가 없습니다.",
        color=discord.Color.purple(),
    )
    today = today_lines(now.weekday())
    embed.add_field(
        name=f"📅 오늘({DAY_NAMES[now.weekday()]}) 일정",
        value="\n".join(today)[:1024] if today else "오늘은 예정된 콘텐츠가 없습니다.",
        inline=False,
    )
    embed.set_footer(text=f"마지막 새로고침: {now.strftime('%Y-%m-%d %H:%M:%S')} (KST) · 첨부 파일은 일정 백업이니 지우지 마세요")
    return embed
 
 
def build_today_embed(title_prefix: str = "📅 [오늘의 일정]") -> discord.Embed:
    now = datetime.now(KST)
    today = today_lines(now.weekday())
    embed = discord.Embed(
        title=f"{title_prefix} {now.strftime('%m월 %d일')} ({DAY_NAMES[now.weekday()]})",
        description="\n".join(today) if today else "오늘은 예정된 콘텐츠가 없습니다.",
        color=discord.Color.gold(),
    )
    return embed
 
 
def build_editor_embed(key: str) -> discord.Embed:
    c = contents[key]
    embed = discord.Embed(
        title=f"⚙️ {c['emoji']} {c['name']} 설정",
        description="아래 메뉴/버튼으로 바로 수정하면 **즉시 저장**되고 패널에도 반영됩니다.",
        color=discord.Color.green() if c["enabled"] else discord.Color.dark_grey(),
    )
    embed.add_field(name="① 요일", value=days_text(c["days"]), inline=True)
    embed.add_field(name="② 시작 시간", value=" · ".join(c["times"]), inline=True)
    embed.add_field(name="③ 알림 시기", value=f"{c['before']}분 전", inline=True)
    embed.add_field(name="④ 알림 상태", value="🔔 켜짐" if c["enabled"] else "🔕 꺼짐", inline=True)
    embed.add_field(name="⑤ 알림 멘트 (원문)", value=c["message"][:1024], inline=False)
    embed.add_field(name="미리보기", value=render_message(c, c["times"][0])[:1024], inline=False)
    embed.set_footer(text="멘트에 {이름} {시간} {분} 을 넣으면 자동으로 바뀌어 들어갑니다.")
    return embed
 
 
async def build_latest_embed(board: str) -> discord.Embed:
    info = BOARDS[board]
    async with aiohttp.ClientSession() as session:
        posts = await fetch_board_posts(session, board, size=5)
    embed = discord.Embed(title=f"📋 최근 {info['label']}", color=info["color"])
    embed.description = ("\n".join(f"• [{p['title']}]({p['url']})" for p in posts) if posts
                         else "지금은 게시글을 불러올 수 없습니다. 잠시 후 다시 시도해 주세요.")
    embed.add_field(name="게시판 바로가기", value=f"[{info['label']} 전체 보기]({info['list_url']})")
    return embed
 
 
def build_help_embed() -> discord.Embed:
    embed = discord.Embed(
        title="🤖 장벽봇 도움말",
        description=f"24시간 자동으로 아이온2 알림을 `{AUTO_CHANNEL_NAME}` 채널에 전송합니다.",
        color=discord.Color.blue(),
    )
    embed.add_field(
        name="명령어",
        value="`/패널생성` - 이 채널에 시간표 패널 설치 (관리자)\n"
              "`/일정` - 오늘의 일정\n"
              "`/도움말` - 봇 안내",
        inline=False,
    )
    return embed
 
 
# ─────────────────────────────────────────
# 변경 저장 + 모든 패널 갱신
# ─────────────────────────────────────────
async def commit_changes(panel_message: discord.Message | None = None):
    save_local()
    if panel_message:
        panel_messages.add((panel_message.channel.id, panel_message.id))
    for ch_id, msg_id in list(panel_messages):
        try:
            ch = bot.get_channel(ch_id) or await bot.fetch_channel(ch_id)
            msg = ch.get_partial_message(msg_id)
            await msg.edit(embed=build_panel_embed(), view=PanelView(), attachments=[backup_file()])
        except discord.NotFound:
            panel_messages.discard((ch_id, msg_id))
        except Exception as e:
            logger.warning(f"패널 갱신 실패 ({ch_id}/{msg_id}): {e}")
 
 
def is_manager(interaction: discord.Interaction) -> bool:
    perms = getattr(interaction.user, "guild_permissions", None)
    return bool(perms and perms.manage_guild)
 
 
async def send_error(interaction: discord.Interaction,
                     msg: str = "⚠️ 처리 중 오류가 발생했습니다. 잠시 후 다시 시도해 주세요."):
    try:
        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)
    except Exception:
        pass
 
 
# ─────────────────────────────────────────
# 일정 수정: ① 콘텐츠 선택
# ─────────────────────────────────────────
class ContentSelect(discord.ui.Select):
    def __init__(self, panel_message):
        self.panel_message = panel_message
        options = [
            discord.SelectOption(
                label=c["name"], value=key, emoji=c["emoji"],
                description=f"{days_text(c['days'])} · {', '.join(c['times'])}"[:100],
            )
            for key, c in contents.items()
        ]
        super().__init__(placeholder="수정할 콘텐츠를 선택하세요", options=options[:25])
 
    async def callback(self, interaction: discord.Interaction):
        key = self.values[0]
        await interaction.response.edit_message(
            content=None, embed=build_editor_embed(key),
            view=ContentEditView(key, self.panel_message))
 
 
class ContentSelectView(discord.ui.View):
    def __init__(self, panel_message):
        super().__init__(timeout=600)
        self.add_item(ContentSelect(panel_message))
 
 
# ─────────────────────────────────────────
# 일정 수정: ② 요일 선택 / ③ 시간·알림시기·멘트 팝업 / 알림 켜기·끄기
# ─────────────────────────────────────────
class DaySelect(discord.ui.Select):
    def __init__(self, key: str):
        self.key = key
        current = contents[key]["days"]
        options = [discord.SelectOption(label=f"{name}요일", value=str(i), default=i in current)
                   for i, name in enumerate(DAY_NAMES)]
        super().__init__(placeholder="① 알림 요일 선택 (여러 개 가능)",
                         min_values=1, max_values=7, options=options, row=0)
 
    async def callback(self, interaction: discord.Interaction):
        view: "ContentEditView" = self.view
        contents[self.key]["days"] = sorted(int(v) for v in self.values)
        logger.info(f"[일정 수정] {contents[self.key]['name']} 요일 → {days_text(contents[self.key]['days'])} (by {interaction.user})")
        await interaction.response.edit_message(
            embed=build_editor_embed(self.key), view=ContentEditView(self.key, view.panel_message))
        await commit_changes(view.panel_message)
 
 
class ContentTimeModal(discord.ui.Modal):
    def __init__(self, key: str, panel_message):
        c = contents[key]
        super().__init__(title=f"{c['name']} 시간·알림 수정"[:45])
        self.key = key
        self.panel_message = panel_message
 
        self.times_input = discord.ui.TextInput(
            label="② 시작 시간 (여러 개는 쉼표로 구분)",
            placeholder="20:00, 23:00",
            default=", ".join(c["times"]),
            max_length=100, required=True,
        )
        self.before_input = discord.ui.TextInput(
            label="③ 몇 분 전에 알림? (0~180)",
            placeholder="10",
            default=str(c["before"]),
            max_length=3, required=True,
        )
        self.message_input = discord.ui.TextInput(
            label="④ 알림 멘트 ({이름} {시간} {분} 사용 가능)",
            style=discord.TextStyle.paragraph,
            placeholder=DEFAULT_TEMPLATE,
            default=c["message"],
            max_length=300, required=True,
        )
        self.add_item(self.times_input)
        self.add_item(self.before_input)
        self.add_item(self.message_input)
 
    async def on_submit(self, interaction: discord.Interaction):
        raw_times = [x for x in re.split(r"[,\s/]+", self.times_input.value) if x]
        times = [normalize_time(x) for x in raw_times]
        if not times or None in times:
            bad = ", ".join(x for x, t in zip(raw_times, times) if t is None) or "(비어 있음)"
            await interaction.response.send_message(
                f"⚠️ 시간 형식이 잘못되었습니다: `{bad}`\n`21:20` 처럼 00:00~23:59로 입력해 주세요.",
                ephemeral=True)
            return
 
        try:
            before = int(self.before_input.value.strip())
            if not 0 <= before <= 180:
                raise ValueError
        except ValueError:
            await interaction.response.send_message(
                "⚠️ 알림 시기는 0~180 사이 숫자로 입력해 주세요. (예: `10`)", ephemeral=True)
            return
 
        c = contents[self.key]
        c["times"] = sorted(set(times))
        c["before"] = before
        c["message"] = self.message_input.value.strip()
        logger.info(f"[일정 수정] {c['name']} 시간 {c['times']} / {before}분 전 (by {interaction.user})")
 
        await interaction.response.edit_message(
            embed=build_editor_embed(self.key), view=ContentEditView(self.key, self.panel_message))
        await commit_changes(self.panel_message)
 
    async def on_error(self, interaction: discord.Interaction, error: Exception):
        logger.error(f"시간 수정 팝업 오류: {error}")
        await send_error(interaction)
 
 
class ContentEditView(discord.ui.View):
    def __init__(self, key: str, panel_message):
        super().__init__(timeout=600)
        self.key = key
        self.panel_message = panel_message
        self.add_item(DaySelect(key))
        self.toggle_button.label = "알림 끄기" if contents[key]["enabled"] else "알림 켜기"
        self.toggle_button.emoji = "🔕" if contents[key]["enabled"] else "🔔"
 
    @discord.ui.button(label="시간·알림·멘트 수정", emoji="⏰", style=discord.ButtonStyle.success, row=1)
    async def time_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(ContentTimeModal(self.key, self.panel_message))
 
    @discord.ui.button(label="알림 끄기", style=discord.ButtonStyle.secondary, row=1)
    async def toggle_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        c = contents[self.key]
        c["enabled"] = not c["enabled"]
        logger.info(f"[일정 수정] {c['name']} 알림 {'켜짐' if c['enabled'] else '꺼짐'} (by {interaction.user})")
        await interaction.response.edit_message(
            embed=build_editor_embed(self.key), view=ContentEditView(self.key, self.panel_message))
        await commit_changes(self.panel_message)
 
    @discord.ui.button(label="멘트 기본값으로", emoji="♻️", style=discord.ButtonStyle.secondary, row=1)
    async def reset_message_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        contents[self.key]["message"] = DEFAULT_TEMPLATE
        await interaction.response.edit_message(
            embed=build_editor_embed(self.key), view=ContentEditView(self.key, self.panel_message))
        await commit_changes(self.panel_message)
 
    @discord.ui.button(label="다른 콘텐츠 선택", emoji="↩️", style=discord.ButtonStyle.primary, row=2)
    async def back_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(
            content="수정할 콘텐츠를 선택하세요.", embed=None,
            view=ContentSelectView(self.panel_message))
 
    async def on_error(self, interaction: discord.Interaction, error: Exception, item):
        logger.error(f"일정 편집 오류: {error}")
        await send_error(interaction)
 
 
# ─────────────────────────────────────────
# 메인 패널 (Persistent View: timeout=None + custom_id)
# ─────────────────────────────────────────
class PanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
 
    @discord.ui.button(label="일정 새로고침", emoji="🔄", row=0,
                       style=discord.ButtonStyle.primary, custom_id="btn_refresh_schedule")
    async def refresh_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        panel_messages.add((interaction.channel_id, interaction.message.id))
        await interaction.response.edit_message(
            embed=build_panel_embed(), view=self, attachments=[backup_file()])
 
    @discord.ui.button(label="일정 수정", emoji="⚙️", row=0,
                       style=discord.ButtonStyle.success, custom_id="btn_edit_schedule")
    async def edit_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_manager(interaction):
            await interaction.response.send_message(
                "⚠️ 일정 수정은 **서버 관리 권한**이 있는 사람만 할 수 있습니다.", ephemeral=True)
            return
        panel_messages.add((interaction.channel_id, interaction.message.id))
        await interaction.response.send_message(
            "수정할 콘텐츠를 선택하세요.", view=ContentSelectView(interaction.message), ephemeral=True)
 
    @discord.ui.button(label="오늘의 일정", emoji="📅", row=1,
                       style=discord.ButtonStyle.secondary, custom_id="btn_today_schedule")
    async def today_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(embed=build_today_embed(), ephemeral=True)
 
    @discord.ui.button(label="최근 공지사항", emoji="📢", row=1,
                       style=discord.ButtonStyle.secondary, custom_id="btn_latest_notice")
    async def notice_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True, thinking=True)
        await interaction.followup.send(embed=await build_latest_embed("notice"), ephemeral=True)
 
    @discord.ui.button(label="최근 업데이트", emoji="🚀", row=1,
                       style=discord.ButtonStyle.secondary, custom_id="btn_latest_update")
    async def update_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True, thinking=True)
        await interaction.followup.send(embed=await build_latest_embed("update"), ephemeral=True)
 
    async def on_error(self, interaction: discord.Interaction, error: Exception, item):
        logger.error(f"패널 버튼 오류 ({getattr(item, 'custom_id', item)}): {error}")
        await send_error(interaction)
 
 
# ─────────────────────────────────────────
# 봇 본체
# ─────────────────────────────────────────
class BarrierBot(commands.Bot):
    async def setup_hook(self):
        self.add_view(PanelView())   # 재시작 후에도 기존 패널 버튼 동작
        await asyncio.sleep(3)
        try:
            synced = await self.tree.sync()
            logger.info(f"슬래시 명령어 {len(synced)}개 동기화: {[c.name for c in synced]}")
        except discord.HTTPException as e:
            logger.warning(f"명령어 동기화 실패 (봇 구동은 유지됨): {e}")
        except Exception as e:
            logger.error(f"명령어 동기화 중 예외: {e}")
 
 
intents = discord.Intents.default()
bot = BarrierBot(command_prefix="!", intents=intents)
 
 
def get_target_channels():
    return [ch for guild in bot.guilds for ch in guild.text_channels
            if ch.name == AUTO_CHANNEL_NAME]
 
 
async def send_to_targets(**kwargs):
    channels = get_target_channels()
    if not channels:
        logger.warning(f"'{AUTO_CHANNEL_NAME}' 채널을 찾을 수 없습니다.")
    for ch in channels:
        try:
            await ch.send(**kwargs)
        except discord.Forbidden:
            logger.warning(f"#{ch.name} ({ch.guild.name}) 채널에 메시지 권한이 없습니다.")
        except Exception as e:
            logger.error(f"#{ch.name} 전송 실패: {e}")
 
 
# ─────────────────────────────────────────
# 게시판 API 호출 + 신규 글 감시 (3분 주기)
# ─────────────────────────────────────────
async def fetch_board_posts(session: aiohttp.ClientSession, board: str, size: int = 10):
    info = BOARDS[board]
    url = (f"{API_BASE}/{info['api_key']}/article/search/moreArticle"
           f"?isVote=true&moreSize={size}&moreDirection=BEFORE&previousArticleId=0")
    try:
        async with session.get(url, headers=HEADERS,
                               timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status != 200:
                logger.warning(f"{info['label']} 요청 실패: HTTP {resp.status}")
                return None
            data = await resp.json(content_type=None)
    except Exception as e:
        logger.error(f"{info['label']} 불러오기 예외: {e}")
        return None
    return [
        {"id": it["id"], "title": it.get("title") or "새 게시글",
         "url": info["view_url"].format(id=it["id"])}
        for it in data.get("contentList", []) if it.get("id")
    ]
 
 
@tasks.loop(minutes=3)
async def check_website_updates():
    try:
        async with aiohttp.ClientSession() as session:
            for board, info in BOARDS.items():
                posts = await fetch_board_posts(session, board, size=10)
                if not posts:
                    continue
                current_ids = {p["id"] for p in posts}
                if seen_ids[board] is None:
                    seen_ids[board] = current_ids
                    logger.info(f"[초기화] {info['label']} 기준글: {posts[0]['title']}")
                    continue
                for p in reversed([p for p in posts if p["id"] not in seen_ids[board]]):
                    embed = discord.Embed(title=info["alert_title"],
                                          description=f"**[{p['title']}]({p['url']})**",
                                          color=info["color"], timestamp=datetime.now(KST))
                    await send_to_targets(embed=embed)
                    logger.info(f"[알림] {info['label']} 새 글: {p['title']}")
                seen_ids[board] |= current_ids
    except Exception as e:
        logger.error(f"게시글 감시 루프 예외 (다음 주기에 재시도): {e}")
 
 
@check_website_updates.before_loop
async def before_website_updates():
    await bot.wait_until_ready()
 
 
# ─────────────────────────────────────────
# 콘텐츠 알림 (1분 주기, KST)
# 요일+시작시간에서 'N분 전'을 계산 → 자정을 넘는 경우(예: 00:05 시작 10분 전)도 처리
# ─────────────────────────────────────────
WEEK_MIN = 7 * 24 * 60
 
 
def due_messages(now: datetime) -> list[str]:
    now_wm = now.weekday() * 1440 + now.hour * 60 + now.minute
    due = []
    for c in contents.values():
        if not c["enabled"]:
            continue
        for d in c["days"]:
            for t in c["times"]:
                alert_wm = (d * 1440 + to_minutes(t) - c["before"]) % WEEK_MIN
                if alert_wm == now_wm:
                    due.append(render_message(c, t))
    return due
 
 
@tasks.loop(minutes=1)
async def check_schedule_alerts():
    global last_alert_key
    try:
        now = datetime.now(KST)
        key = now.strftime("%Y-%m-%d %H:%M")
        if key == last_alert_key:
            return
        last_alert_key = key
 
        for msg in due_messages(now):
            await send_to_targets(content=msg)
 
        if DAILY_SUMMARY_TIME and now.strftime("%H:%M") == DAILY_SUMMARY_TIME:
            await send_to_targets(embed=build_today_embed("📝 [오늘의 숙제 요약]"))
    except Exception as e:
        logger.error(f"일정 알림 루프 예외 (다음 주기에 재시도): {e}")
 
 
@check_schedule_alerts.before_loop
async def before_schedule_alerts():
    await bot.wait_until_ready()
 
 
@bot.event
async def on_ready():
    logger.info(f"봇 로그인 완료: {bot.user} (ID: {bot.user.id}) / 서버 {len(bot.guilds)}개")
    await restore_schedule()
    if not check_website_updates.is_running():
        check_website_updates.start()
    if not check_schedule_alerts.is_running():
        check_schedule_alerts.start()
 
 
# ─────────────────────────────────────────
# 슬래시 명령어
# ─────────────────────────────────────────
@bot.tree.command(name="패널생성", description="이 채널에 주간 콘텐츠 시간표 패널을 설치합니다. (관리자 전용)")
@app_commands.guild_only()
@app_commands.default_permissions(manage_guild=True)
async def create_panel(interaction: discord.Interaction):
    channel = interaction.channel
    await interaction.response.defer(ephemeral=True, thinking=True)
 
    removed = 0
    try:
        async for msg in channel.history(limit=50):
            if (msg.author.id == bot.user.id and msg.embeds
                    and msg.embeds[0].title in (PANEL_TITLE, "🛡️ 장벽봇 · 보스 & 콘텐츠 시간표", "🛡️ 장벽봇 패널")):
                await msg.delete()
                panel_messages.discard((channel.id, msg.id))
                removed += 1
    except discord.Forbidden:
        pass
 
    try:
        new_msg = await channel.send(embed=build_panel_embed(), view=PanelView(), file=backup_file())
        panel_messages.add((channel.id, new_msg.id))
    except discord.Forbidden:
        await interaction.followup.send(
            "⚠️ 이 채널에 메시지(또는 파일)를 보낼 권한이 없습니다. 봇 권한을 확인해 주세요.", ephemeral=True)
        return
 
    note = f" (이전 패널 {removed}개 정리)" if removed else ""
    tip = ""
    if channel.name != AUTO_CHANNEL_NAME:
        tip = (f"\n💡 패널을 `{AUTO_CHANNEL_NAME}` 채널에 설치해야 "
               "봇이 재시작돼도 수정한 일정이 복원됩니다.")
    await interaction.followup.send(f"✅ 패널을 설치했습니다.{note}{tip}", ephemeral=True)
 
 
@bot.tree.command(name="일정", description="오늘의 콘텐츠 일정을 확인합니다.")
async def schedule_command(interaction: discord.Interaction):
    await interaction.response.send_message(embed=build_today_embed(), ephemeral=True)
 
 
@bot.tree.command(name="도움말", description="장벽봇 사용 방법을 확인합니다.")
async def help_command(interaction: discord.Interaction):
    await interaction.response.send_message(embed=build_help_embed(), ephemeral=True)
 
 
@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.MissingPermissions):
        await send_error(interaction, "⚠️ 이 명령어는 서버 관리 권한이 있는 사람만 쓸 수 있습니다.")
    else:
        logger.error(f"명령어 오류: {error}")
        await send_error(interaction, "⚠️ 명령어 처리 중 오류가 발생했습니다.")
 
 
# ─────────────────────────────────────────
# 호스팅용 웹서버
# ─────────────────────────────────────────
async def start_web_server():
    app = web.Application()
 
    async def health(request):
        return web.Response(text=f"Bot Alive ({'ready' if bot.is_ready() else 'starting'})")
 
    app.router.add_get("/", health)   # GET 등록 시 HEAD도 자동 처리됨
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", PORT).start()
    logger.info(f"웹 서버 시작: 0.0.0.0:{PORT}")
 
 
async def diagnose_discord():
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get("https://discord.com/api/v10/gateway",
                             timeout=aiohttp.ClientTimeout(total=10)) as r:
                logger.info(f"[진단] Discord 접속 status={r.status}")
    except Exception as e:
        logger.error(f"[진단] Discord 접속 실패: {e}")
 
 
async def main():
    if not TOKEN:
        logger.error("DISCORD_TOKEN 환경변수가 없습니다.")
        return
    await start_web_server()
    await diagnose_discord()
    try:
        async with bot:
            await bot.start(TOKEN)
    except discord.HTTPException as e:
        if e.status == 429:
            logger.error("Discord 429 (IP 차단 추정) → 재시작 폭주 방지를 위해 1시간 대기 후 종료")
            await asyncio.sleep(3600)
        raise
 
 
if __name__ == "__main__":
    asyncio.run(main())
