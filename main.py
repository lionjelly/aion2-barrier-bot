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
PANEL_TITLE = "⚙️ 장벽봇 일정 관리 패널 · 시간 수정 가능"
# 예전 버전 패널 제목 (재설치 시 정리 + 백업 복원에 사용)
LEGACY_PANEL_TITLES = {
    "🛡️ 장벽봇 · 주간 콘텐츠 시간표",
    "🛡️ 장벽봇 · 보스 & 콘텐츠 시간표",
    "🛡️ 장벽봇 패널",
}
ALL_PANEL_TITLES = LEGACY_PANEL_TITLES | {PANEL_TITLE}
INFO_PANEL_TITLE = "📌 장벽봇 안내 · 누구나 사용 가능"
 
SCHEDULE_FILE = "schedule.json"
BACKUP_FILENAME = "장벽봇_일정백업.json"   # 패널에 붙는 백업 파일 (재배포 후 복원용)
DAILY_SUMMARY_TIME = "19:00"              # 매일 '오늘의 주요 일정' 요약 (끄려면 None)
 
DAY_NAMES = ["월", "화", "수", "목", "금", "토", "일"]
 
# ─────────────────────────────────────────
# 콘텐츠 기본 시간표
# days: 0=월 1=화 2=수 3=목 4=금 5=토 6=일
# important: True면 '오늘의 주요 일정'(패널·19시 요약)에 표시
# repeat_hours: 값이 있으면 기준 시간 하나만 입력해도 그 간격으로 자동 반복
# comment: 알림에 함께 나가는 안내 문구 (패널에서 수정 가능)
# ─────────────────────────────────────────
DEFAULT_CONTENTS = {
    "agro": {
        "name": "아그로", "emoji": "🐲", "days": [0, 1, 2, 3, 4, 5, 6],
        "times": ["05:00", "17:00"], "before": 10, "important": True, "repeat_hours": 12,
        "comment": "필드 보스 아그로가 곧 등장합니다! 미리 위치로 이동해서 파티를 꾸려 주세요. "
                   "점검 이후에는 젠 시간이 바뀔 수 있으니 패널 시간표도 함께 확인 부탁드립니다. 🙏",
    },
    "abyss_rift": {
        "name": "어비스균열", "emoji": "🌀", "days": [1, 3],
        "times": ["21:20"], "before": 10, "important": True,
        "comment": "어비스 균열이 곧 열립니다! 균열을 봉쇄하고 보상을 획득하실 수 있도록 "
                   "미리 접속해서 준비해 주세요.",
    },
    "abyss_boss": {
        "name": "어비스보스", "emoji": "👹", "days": [2, 5],
        "times": ["22:30"], "before": 10, "important": True,
        "comment": "강력한 어비스 보스가 곧 등장합니다! 물약과 버프를 미리 챙기시고 "
                   "레기온원분들과 함께 처치해 주세요.",
    },
    "nahma": {
        "name": "나흐마", "emoji": "🗡️", "days": [4, 6],
        "times": ["22:00"], "before": 10, "important": True,
        "comment": "나흐마의 힘을 두고 경쟁이 곧 시작됩니다! 미리 접속해서 파티와 장비를 점검해 주세요.",
    },
    "atijaeng": {
        "name": "아티쟁", "emoji": "🏰", "days": [2, 5],
        "times": ["22:00"], "before": 10, "important": True,
        "comment": "아티팩트를 차지하기 위한 전투가 곧 시작됩니다! 레기온 집결 장소로 미리 모여 주세요.",
    },
    "conquest": {
        "name": "쟁탈전", "emoji": "⚔️", "days": [0, 3, 5],
        "times": ["20:00", "23:00"], "before": 10, "important": True,
        "comment": "영지를 차지하기 위한 대규모 전투가 곧 시작됩니다! "
                   "레기온원 여러분은 미리 접속해서 집결해 주세요.",
    },
    "kaira": {
        "name": "카이라", "emoji": "👑", "days": [0, 1, 2, 3, 4, 5, 6],
        "times": ["01:00", "05:00", "09:00", "13:00", "17:00", "21:00"], "before": 5,
        "important": False,
        "comment": "카이라에 도전하실 시간입니다! 참여하실 분은 미리 접속해서 준비해 주세요.",
    },
}
 
contents: dict[str, dict] = {}
schedule_loaded = False
# 설치된 패널 위치 기억: (channel_id, message_id) → "admin"(관리자 패널) / "info"(안내 패널)
panel_messages: dict[tuple[int, int], str] = {}
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
    "cm_story": {
        "api_key": "cm_story_ko",
        "list_url": "https://aion2.plaync.com/ko-kr/board/cm_story/list",
        "view_url": "https://aion2.plaync.com/ko-kr/board/cm_story/view?articleId={id}",
        "label": "CM 아지트",
        "alert_title": "🏠 [CM 아지트] 새 소식이 등록되었습니다!",
        "color": discord.Color.orange(),
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
seen_ids = {key: None for key in BOARDS}
 
 
# ─────────────────────────────────────────
# 시간/요일 도우미
# ─────────────────────────────────────────
TIME_RE = re.compile(r"^(\d{1,2})\s*[:：시]\s*(\d{1,2})?\s*분?$")
 
 
def normalize_time(text: str):
    """'9:05', '09:05', '21시', '21시 20분' → 'HH:MM' / 잘못된 값은 None"""
    t = text.strip().replace(" ", "")
    if re.fullmatch(r"\d{1,2}", t):          # '21' → 21:00
        t = t + ":00"
    m = TIME_RE.match(t)
    if not m:
        return None
    h, mi = int(m.group(1)), int(m.group(2) or 0)
    if h > 23 or mi > 59:
        return None
    return f"{h:02d}:{mi:02d}"
 
 
def to_minutes(t: str) -> int:
    h, m = t.split(":")
    return int(h) * 60 + int(m)
 
 
def from_minutes(total: int) -> str:
    total %= 1440
    return f"{total // 60:02d}:{total % 60:02d}"
 
 
def expand_repeat(base: str, hours: int) -> list[str]:
    """기준 시간 하나 → 간격대로 하루 전체 시간 목록 (예: 05:00, 12시간 → 05:00, 17:00)"""
    step = hours * 60
    count = max(1, 1440 // step)
    return sorted({from_minutes(to_minutes(base) + step * i) for i in range(count)})
 
 
def days_text(days: list[int]) -> str:
    if len(days) == 7:
        return "매일"
    if sorted(days) == [0, 1, 2, 3, 4]:
        return "평일"
    if sorted(days) == [5, 6]:
        return "주말"
    return "·".join(DAY_NAMES[d] for d in sorted(days))
 
 
def render_alert(c: dict, start_time: str) -> str:
    """실제로 채널에 나가는 알림 문장"""
    if c["before"] > 0:
        head = (f"📢 **[{c['emoji']} {c['name']}]** 시작 **{c['before']}분 전** 안내드립니다!\n"
                f"⏰ 시작 시간: **{start_time}**")
    else:
        head = f"📢 **[{c['emoji']} {c['name']}]** 지금 시작합니다! (⏰ {start_time})"
    comment = (c.get("comment") or "").strip()
    # 예전 버전 멘트에 남아 있을 수 있는 {이름} {시간} {분}도 자동 변환
    comment = (comment.replace("{이름}", c["name"])
                      .replace("{시간}", start_time)
                      .replace("{분}", str(c["before"])))
    return f"{head}\n💬 {comment}" if comment else head
 
 
# ─────────────────────────────────────────
# 데이터 정리 / 저장 / 복원
# ─────────────────────────────────────────
def clean_one(key: str, c: dict) -> dict | None:
    base = DEFAULT_CONTENTS.get(key, {})
    try:
        times = sorted({t for t in (normalize_time(x) for x in c["times"]) if t})
        days = sorted({int(d) for d in c["days"] if 0 <= int(d) <= 6})
        if not times or not days:
            return None
 
        comment = c.get("comment")
        if comment is None:
            # 예전 버전 데이터(message 필드) 변환: 기본 템플릿이었으면 새 기본 문구로 교체
            old = str(c.get("message") or "")
            comment = base.get("comment", "") if (not old or "{이름}" in old) else old
 
        return {
            "name": str(c.get("name") or base.get("name", key)),
            "emoji": str(c.get("emoji") or base.get("emoji", "📌")),
            "days": days,
            "times": times,
            "before": max(0, min(180, int(c.get("before", base.get("before", 10))))),
            "enabled": bool(c.get("enabled", True)),
            "important": bool(c.get("important", base.get("important", True))),
            "repeat_hours": int(c.get("repeat_hours", base.get("repeat_hours", 0)) or 0),
            "comment": str(comment),
        }
    except Exception:
        return None
 
 
def sanitize_contents(data: dict) -> dict:
    result = {}
    # 새로 추가된 기본 콘텐츠(예: 아그로)가 없으면 맨 앞에 자동 추가
    for key, c in DEFAULT_CONTENTS.items():
        if key not in data:
            result[key] = clean_one(key, c)
    for key, c in data.items():
        cleaned = clean_one(key, c)
        if cleaned:
            result[key] = cleaned
    # 기본 순서 유지 (기본 콘텐츠 → 직접 추가한 콘텐츠)
    order = list(DEFAULT_CONTENTS) + [k for k in result if k not in DEFAULT_CONTENTS]
    return {k: result[k] for k in order if k in result}
 
 
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
    시작 시 복원 순서: 1) schedule.json  2) 관리자 패널에 붙은 백업 파일  3) 기본 시간표
    (Render 무료 플랜은 재배포하면 파일이 지워지므로 2번이 실제 백업 역할)
    관리자 패널을 별도 관리자 채널에 둬도 찾을 수 있도록 봇이 볼 수 있는 채널을 모두 확인
    """
    global contents, schedule_loaded
    if schedule_loaded:
        return
 
    loaded = None
    try:
        with open(SCHEDULE_FILE, encoding="utf-8") as f:
            loaded = json.load(f) or None
        if loaded:
            logger.info("[일정] 로컬 파일에서 복원")
    except FileNotFoundError:
        pass
    except Exception as e:
        logger.warning(f"일정 파일 읽기 실패: {e}")
 
    # 알림 채널을 먼저, 그다음 나머지 채널
    targets = get_target_channels()
    others = [ch for g in bot.guilds for ch in g.text_channels if ch not in targets]
    for ch in targets + others:
        perms = ch.permissions_for(ch.guild.me)
        if not (perms.view_channel and perms.read_message_history):
            continue
        try:
            async for msg in ch.history(limit=30):
                if not (msg.author.id == bot.user.id and msg.embeds):
                    continue
                title = msg.embeds[0].title
                if title == INFO_PANEL_TITLE:
                    panel_messages[(ch.id, msg.id)] = "info"
                elif title in ALL_PANEL_TITLES:
                    panel_messages[(ch.id, msg.id)] = "admin"
                    if loaded is None:
                        for att in msg.attachments:
                            if att.filename == BACKUP_FILENAME:
                                data = json.loads(await att.read())
                                if data:
                                    loaded = data
                                    logger.info(f"[일정] #{ch.name} 패널 백업에서 복원")
        except Exception as e:
            logger.warning(f"#{ch.name} 패널 검색 실패: {e}")
 
    if loaded is None:
        loaded = {}
        logger.info("[일정] 기본 시간표 사용")
 
    contents = sanitize_contents(loaded)
    save_local()
    schedule_loaded = True
    logger.info(f"[패널] 관리자 패널 {list(panel_messages.values()).count('admin')}개, "
                f"안내 패널 {list(panel_messages.values()).count('info')}개 확인")
 
 
# ─────────────────────────────────────────
# 임베드
# ─────────────────────────────────────────
def today_lines(weekday: int, important_only: bool = True) -> list[str]:
    items = []
    for c in contents.values():
        if important_only and not c["important"]:
            continue
        if weekday in c["days"]:
            for t in c["times"]:
                off = "" if c["enabled"] else " (알림 꺼짐)"
                items.append((t, f"`{t}` {c['emoji']} {c['name']}{off}"))
    return [line for _, line in sorted(items)]
 
 
def daily_only_names() -> str:
    names = [f"{c['emoji']} {c['name']}" for c in contents.values() if not c["important"]]
    return ", ".join(names)
 
 
def build_panel_embed() -> discord.Embed:
    now = datetime.now(KST)
    guide = ("> 🔒 **관리자 전용 패널**입니다. (서버 관리 권한 필요)\n"
             "> **⚙️ 일정 수정** 버튼으로 시간·요일·알림 시기·안내 문구를 바꿀 수 있습니다.\n"
             "> 점검 후 아그로 젠 시간은 **🐲 아그로 젠 시간 수정** 버튼으로 바로 바꿔 주세요.\n"
             "> 수정 내용은 모두가 보는 **📌 안내 패널**에도 자동 반영됩니다.\n")
    lines = []
    for c in contents.values():
        status = f"🔔 {c['before']}분 전 알림" if c["enabled"] else "🔕 알림 꺼짐"
        repeat = f" ({c['repeat_hours']}시간 간격)" if c.get("repeat_hours") else ""
        lines.append(f"{c['emoji']} **{c['name']}** — {days_text(c['days'])}{repeat}\n"
                     f"┗ ⏰ {' · '.join(c['times'])}  |  {status}")
    embed = discord.Embed(
        title=PANEL_TITLE,
        description=guide + "\n" + ("\n".join(lines) or "등록된 콘텐츠가 없습니다."),
        color=discord.Color.purple(),
    )
    today = today_lines(now.weekday())
    extra = daily_only_names()
    value = "\n".join(today) if today else "오늘은 예정된 주요 콘텐츠가 없습니다."
    if extra:
        value += f"\n\n※ {extra}는 주요 일정에서 제외되며, 매일 제시간에 따로 알려드립니다."
    embed.add_field(name=f"📅 오늘({DAY_NAMES[now.weekday()]}) 주요 일정", value=value[:1024], inline=False)
    embed.set_footer(text=f"마지막 새로고침: {now.strftime('%Y-%m-%d %H:%M:%S')} (KST) · "
                          "첨부 파일은 일정 백업이니 지우지 마세요")
    return embed
 
 
def build_info_panel_embed() -> discord.Embed:
    """모두가 보는 안내 패널"""
    now = datetime.now(KST)
    embed = discord.Embed(
        title=INFO_PANEL_TITLE,
        description=("레기온원 여러분, 반갑습니다! 👋\n"
                     "아래 버튼을 누르시면 **나에게만 보이는 창**으로 바로 확인하실 수 있습니다.\n\n"
                     "📅 **오늘의 주요 일정** · 오늘 예정된 보스/콘텐츠 시간\n"
                     "📢 **공지사항** · 아이온2 공식 공지 최신 5개\n"
                     "🚀 **업데이트** · 최신 패치노트 5개\n"
                     "🏠 **CM 아지트** · 업데이트 뉴스 등 CM 소식 5개\n\n"
                     f"새 글과 콘텐츠 알림은 `{AUTO_CHANNEL_NAME}` 채널로 자동으로 보내 드립니다."),
        color=discord.Color.teal(),
    )
    today = today_lines(now.weekday())
    value = "\n".join(today) if today else "오늘은 예정된 주요 콘텐츠가 없습니다."
    extra = daily_only_names()
    if extra:
        value += f"\n\n※ {extra}는 매일 제시간에 따로 알려드립니다."
    embed.add_field(name=f"📅 오늘({DAY_NAMES[now.weekday()]}) 주요 일정", value=value[:1024], inline=False)
    embed.set_footer(text=f"마지막 갱신: {now.strftime('%Y-%m-%d %H:%M')} (KST) · 일정이 바뀌면 자동으로 갱신됩니다")
    return embed
 
 
def build_today_embed(title_prefix: str = "📅 [오늘의 주요 일정]") -> discord.Embed:
    now = datetime.now(KST)
    today = today_lines(now.weekday())
    desc = "\n".join(today) if today else "오늘은 예정된 주요 콘텐츠가 없습니다."
    extra = daily_only_names()
    if extra:
        desc += f"\n\n※ {extra}는 매일 제시간에 따로 알려드립니다."
    return discord.Embed(
        title=f"{title_prefix} {now.strftime('%m월 %d일')} ({DAY_NAMES[now.weekday()]})",
        description="레기온원 여러분, 오늘 예정된 주요 콘텐츠를 안내해 드립니다!\n\n" + desc,
        color=discord.Color.gold(),
    )
 
 
def build_editor_embed(key: str) -> discord.Embed:
    c = contents[key]
    embed = discord.Embed(
        title=f"⚙️ {c['emoji']} {c['name']} 설정",
        description=(
            "아래 순서대로 수정해 주세요. 바꾸는 즉시 **자동 저장**되고 패널에도 바로 반영됩니다.\n\n"
            "**1단계** 아래 메뉴에서 알림을 보낼 **요일**을 골라 주세요.\n"
            "**2단계** **⏰ 시간·알림·문구 수정** 버튼을 눌러 시작 시간, 알림 시기, 안내 문구를 입력해 주세요.\n"
            "**3단계** 맨 아래 **알림 미리보기**에서 실제로 나갈 알림을 확인해 주세요."
        ),
        color=discord.Color.green() if c["enabled"] else discord.Color.dark_grey(),
    )
    repeat = f" ({c['repeat_hours']}시간 간격 자동 반복)" if c.get("repeat_hours") else ""
    embed.add_field(name="📆 요일", value=days_text(c["days"]), inline=True)
    embed.add_field(name="⏰ 시작 시간", value=" · ".join(c["times"]) + repeat, inline=True)
    embed.add_field(name="🔔 알림 시기", value=f"시작 {c['before']}분 전", inline=True)
    embed.add_field(name="알림 상태", value="🔔 켜짐" if c["enabled"] else "🔕 꺼짐", inline=True)
    embed.add_field(name="주요 일정 표시", value="📌 표시함" if c["important"] else "➖ 표시 안 함", inline=True)
    embed.add_field(name="💬 알림 미리보기", value=render_alert(c, c["times"][0])[:1024], inline=False)
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
        description=f"아이온2 콘텐츠 알림과 공지·업데이트·CM 아지트 소식을 "
                    f"`{AUTO_CHANNEL_NAME}` 채널로 24시간 자동 전송해 드립니다.",
        color=discord.Color.blue(),
    )
    embed.add_field(
        name="명령어",
        value="`/안내패널생성` - 누구나 쓰는 안내 패널 설치 (관리자)\n"
              "`/패널생성` - 일정 관리 패널 설치 (관리자)\n"
              "`/일정` - 오늘의 주요 일정\n"
              "`/도움말` - 봇 안내",
        inline=False,
    )
    return embed
 
 
# ─────────────────────────────────────────
# 변경 저장 + 모든 패널 갱신
# ─────────────────────────────────────────
async def refresh_all_panels():
    for (ch_id, msg_id), kind in list(panel_messages.items()):
        try:
            ch = bot.get_channel(ch_id) or await bot.fetch_channel(ch_id)
            msg = ch.get_partial_message(msg_id)
            if kind == "admin":
                await msg.edit(embed=build_panel_embed(), view=PanelView(), attachments=[backup_file()])
            else:
                await msg.edit(embed=build_info_panel_embed(), view=InfoPanelView())
        except discord.NotFound:
            panel_messages.pop((ch_id, msg_id), None)
        except Exception as e:
            logger.warning(f"패널 갱신 실패 ({ch_id}/{msg_id}): {e}")
 
 
async def commit_changes(panel_message: discord.Message | None = None):
    save_local()
    if panel_message:
        panel_messages[(panel_message.channel.id, panel_message.id)] = "admin"
    await refresh_all_panels()
 
 
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
 
 
NO_PERMISSION_MSG = "⚠️ 일정 수정은 **서버 관리 권한**이 있는 분만 하실 수 있습니다."
 
 
# ─────────────────────────────────────────
# 일정 수정: 콘텐츠 선택
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
        super().__init__(placeholder="👉 수정하실 콘텐츠를 선택해 주세요", options=options[:25])
 
    async def callback(self, interaction: discord.Interaction):
        key = self.values[0]
        await interaction.response.edit_message(
            content=None, embed=build_editor_embed(key),
            view=ContentEditView(key, self.panel_message))
 
 
class ContentSelectView(discord.ui.View):
    def __init__(self, panel_message):
        super().__init__(timeout=600)
        self.add_item(ContentSelect(panel_message))
 
 
SELECT_GUIDE = ("⚙️ **일정 수정**\n"
                "아래 메뉴에서 수정하실 콘텐츠를 선택해 주세요.\n"
                "선택하시면 요일 · 시작 시간 · 알림 시기 · 안내 문구를 차례대로 바꾸실 수 있습니다.")
 
 
# ─────────────────────────────────────────
# 일정 수정: 요일 선택
# ─────────────────────────────────────────
class DaySelect(discord.ui.Select):
    def __init__(self, key: str):
        self.key = key
        current = contents[key]["days"]
        options = [discord.SelectOption(label=f"{name}요일", value=str(i), default=i in current)
                   for i, name in enumerate(DAY_NAMES)]
        super().__init__(placeholder="📆 1단계: 알림을 보낼 요일을 골라 주세요 (여러 개 선택 가능)",
                         min_values=1, max_values=7, options=options, row=0)
 
    async def callback(self, interaction: discord.Interaction):
        view: "ContentEditView" = self.view
        c = contents[self.key]
        c["days"] = sorted(int(v) for v in self.values)
        logger.info(f"[일정 수정] {c['name']} 요일 → {days_text(c['days'])} (by {interaction.user})")
        await interaction.response.edit_message(
            embed=build_editor_embed(self.key), view=ContentEditView(self.key, view.panel_message))
        await commit_changes(view.panel_message)
 
 
# ─────────────────────────────────────────
# 일정 수정: 시간 · 알림 시기 · 안내 문구 팝업
# ─────────────────────────────────────────
class ContentTimeModal(discord.ui.Modal):
    def __init__(self, key: str, panel_message, from_panel: bool = False):
        c = contents[key]
        super().__init__(title=f"{c['name']} 시간·알림 수정"[:45])
        self.key = key
        self.panel_message = panel_message
        self.from_panel = from_panel   # 패널의 바로가기 버튼에서 열었는지
        repeat = c.get("repeat_hours") or 0
 
        if repeat:
            self.times_input = discord.ui.TextInput(
                label=f"기준 젠 시간 ({repeat}시간 간격으로 자동 계산돼요)"[:45],
                placeholder="예) 05:00 → 05:00, 17:00 으로 자동 등록",
                default=c["times"][0], max_length=10, required=True,
            )
        else:
            self.times_input = discord.ui.TextInput(
                label="시작 시간 (여러 개면 쉼표로 구분해 주세요)",
                placeholder="예) 20:00, 23:00",
                default=", ".join(c["times"]), max_length=100, required=True,
            )
        self.before_input = discord.ui.TextInput(
            label="몇 분 전에 알려드릴까요? (숫자만, 0~180)",
            placeholder="예) 10  → 시작 10분 전에 알림 / 0 → 시작 시간에 알림",
            default=str(c["before"]), max_length=3, required=True,
        )
        self.comment_input = discord.ui.TextInput(
            label="알림과 함께 보낼 안내 문구 (자유롭게 작성)",
            style=discord.TextStyle.paragraph,
            placeholder="예) 미리 접속해서 파티를 꾸려 주세요! 집결 장소는 ○○입니다.\n"
                        "※ 콘텐츠 이름·시작 시간은 알림 윗줄에 자동으로 들어갑니다.",
            default=c["comment"], max_length=300, required=False,
        )
        self.add_item(self.times_input)
        self.add_item(self.before_input)
        self.add_item(self.comment_input)
 
    async def on_submit(self, interaction: discord.Interaction):
        c = contents[self.key]
        repeat = c.get("repeat_hours") or 0
 
        raw_times = [x for x in re.split(r"[,，/\n]+", self.times_input.value) if x.strip()]
        times = [normalize_time(x) for x in raw_times]
        if not times or None in times:
            bad = ", ".join(x.strip() for x, t in zip(raw_times, times) if t is None) or "(비어 있음)"
            await interaction.response.send_message(
                f"⚠️ 시간 형식을 확인해 주세요: `{bad}`\n"
                "`21:20` 처럼 **00:00 ~ 23:59** 사이로 입력해 주시면 됩니다.", ephemeral=True)
            return
        if repeat:
            times = expand_repeat(times[0], repeat)
 
        try:
            before = int(self.before_input.value.strip())
            if not 0 <= before <= 180:
                raise ValueError
        except ValueError:
            await interaction.response.send_message(
                "⚠️ 알림 시기는 **0 ~ 180 사이 숫자**로만 입력해 주세요. (예: `10`)", ephemeral=True)
            return
 
        c["times"] = sorted(set(times))
        c["before"] = before
        c["comment"] = self.comment_input.value.strip()
        logger.info(f"[일정 수정] {c['name']} 시간 {c['times']} / {before}분 전 (by {interaction.user})")
 
        if self.from_panel:
            await interaction.response.send_message(
                f"✅ **{c['name']}** 일정이 저장되었습니다.\n"
                f"⏰ {' · '.join(c['times'])}  |  🔔 시작 {before}분 전 알림\n\n"
                f"**알림 미리보기**\n{render_alert(c, c['times'][0])}", ephemeral=True)
        else:
            await interaction.response.edit_message(
                embed=build_editor_embed(self.key), view=ContentEditView(self.key, self.panel_message))
        await commit_changes(self.panel_message)
 
    async def on_error(self, interaction: discord.Interaction, error: Exception):
        logger.error(f"시간 수정 팝업 오류: {error}")
        await send_error(interaction)
 
 
# ─────────────────────────────────────────
# 일정 수정 화면 (요일 메뉴 + 버튼)
# ─────────────────────────────────────────
class ContentEditView(discord.ui.View):
    def __init__(self, key: str, panel_message):
        super().__init__(timeout=600)
        self.key = key
        self.panel_message = panel_message
        c = contents[key]
        self.add_item(DaySelect(key))
        self.toggle_button.label = "알림 끄기" if c["enabled"] else "알림 켜기"
        self.toggle_button.emoji = "🔕" if c["enabled"] else "🔔"
        self.important_button.label = "주요 일정에서 빼기" if c["important"] else "주요 일정에 넣기"
 
    @discord.ui.button(label="2단계: 시간·알림·문구 수정", emoji="⏰", style=discord.ButtonStyle.success, row=1)
    async def time_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(ContentTimeModal(self.key, self.panel_message))
 
    @discord.ui.button(label="알림 끄기", style=discord.ButtonStyle.secondary, row=2)
    async def toggle_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        c = contents[self.key]
        c["enabled"] = not c["enabled"]
        logger.info(f"[일정 수정] {c['name']} 알림 {'켜짐' if c['enabled'] else '꺼짐'} (by {interaction.user})")
        await interaction.response.edit_message(
            embed=build_editor_embed(self.key), view=ContentEditView(self.key, self.panel_message))
        await commit_changes(self.panel_message)
 
    @discord.ui.button(label="주요 일정에서 빼기", emoji="📌", style=discord.ButtonStyle.secondary, row=2)
    async def important_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        c = contents[self.key]
        c["important"] = not c["important"]
        await interaction.response.edit_message(
            embed=build_editor_embed(self.key), view=ContentEditView(self.key, self.panel_message))
        await commit_changes(self.panel_message)
 
    @discord.ui.button(label="안내 문구 기본값으로", emoji="♻️", style=discord.ButtonStyle.secondary, row=2)
    async def reset_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        contents[self.key]["comment"] = DEFAULT_CONTENTS.get(self.key, {}).get(
            "comment", "미리 접속해서 준비해 주세요!")
        await interaction.response.edit_message(
            embed=build_editor_embed(self.key), view=ContentEditView(self.key, self.panel_message))
        await commit_changes(self.panel_message)
 
    @discord.ui.button(label="다른 콘텐츠 선택", emoji="↩️", style=discord.ButtonStyle.primary, row=3)
    async def back_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(
            content=SELECT_GUIDE, embed=None, view=ContentSelectView(self.panel_message))
 
    async def on_error(self, interaction: discord.Interaction, error: Exception, item):
        logger.error(f"일정 편집 오류: {error}")
        await send_error(interaction)
 
 
# ─────────────────────────────────────────
# 관리자 패널 (Persistent View: timeout=None + custom_id) — 서버 관리 권한 필요
# ─────────────────────────────────────────
class PanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
 
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        # 이 패널의 모든 버튼은 관리자만 사용 가능
        if is_manager(interaction):
            return True
        await interaction.response.send_message(
            "🔒 이 패널은 **관리자 전용**입니다.\n"
            "일정·공지사항·업데이트·CM 아지트는 **📌 장벽봇 안내** 패널에서 누구나 확인하실 수 있습니다.",
            ephemeral=True)
        return False
 
    @discord.ui.button(label="일정 새로고침", emoji="🔄", row=0,
                       style=discord.ButtonStyle.primary, custom_id="btn_refresh_schedule")
    async def refresh_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        panel_messages[(interaction.channel_id, interaction.message.id)] = "admin"
        await interaction.response.edit_message(
            embed=build_panel_embed(), view=self, attachments=[backup_file()])
        await refresh_all_panels()   # 안내 패널도 함께 최신화
 
    @discord.ui.button(label="일정 수정", emoji="⚙️", row=0,
                       style=discord.ButtonStyle.success, custom_id="btn_edit_schedule")
    async def edit_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        panel_messages[(interaction.channel_id, interaction.message.id)] = "admin"
        await interaction.response.send_message(
            SELECT_GUIDE, view=ContentSelectView(interaction.message), ephemeral=True)
 
    @discord.ui.button(label="아그로 젠 시간 수정", emoji="🐲", row=0,
                       style=discord.ButtonStyle.danger, custom_id="btn_quick_agro")
    async def agro_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if "agro" not in contents:
            await interaction.response.send_message("⚠️ 아그로 일정을 찾을 수 없습니다.", ephemeral=True)
            return
        panel_messages[(interaction.channel_id, interaction.message.id)] = "admin"
        await interaction.response.send_modal(
            ContentTimeModal("agro", interaction.message, from_panel=True))
 
    async def on_error(self, interaction: discord.Interaction, error: Exception, item):
        logger.error(f"관리자 패널 오류 ({getattr(item, 'custom_id', item)}): {error}")
        await send_error(interaction)
 
 
# ─────────────────────────────────────────
# 안내 패널 (누구나 사용, 결과는 누른 사람에게만 보임)
# ─────────────────────────────────────────
class InfoPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
 
    @discord.ui.button(label="오늘의 주요 일정", emoji="📅", row=0,
                       style=discord.ButtonStyle.primary, custom_id="btn_today_schedule")
    async def today_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(embed=build_today_embed(), ephemeral=True)
 
    @discord.ui.button(label="공지사항", emoji="📢", row=0,
                       style=discord.ButtonStyle.secondary, custom_id="btn_latest_notice")
    async def notice_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True, thinking=True)
        await interaction.followup.send(embed=await build_latest_embed("notice"), ephemeral=True)
 
    @discord.ui.button(label="업데이트", emoji="🚀", row=1,
                       style=discord.ButtonStyle.secondary, custom_id="btn_latest_update")
    async def update_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True, thinking=True)
        await interaction.followup.send(embed=await build_latest_embed("update"), ephemeral=True)
 
    @discord.ui.button(label="CM 아지트", emoji="🏠", row=1,
                       style=discord.ButtonStyle.secondary, custom_id="btn_latest_cm_story")
    async def cm_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True, thinking=True)
        await interaction.followup.send(embed=await build_latest_embed("cm_story"), ephemeral=True)
 
    async def on_error(self, interaction: discord.Interaction, error: Exception, item):
        logger.error(f"안내 패널 오류 ({getattr(item, 'custom_id', item)}): {error}")
        await send_error(interaction)
 
 
# ─────────────────────────────────────────
# 봇 본체
# ─────────────────────────────────────────
class BarrierBot(commands.Bot):
    async def setup_hook(self):
        # 재시작 후에도 기존 패널 버튼이 동작하도록 등록 (관리자 패널 + 안내 패널)
        self.add_view(PanelView())
        self.add_view(InfoPanelView())
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
# 요일+시작시간에서 'N분 전'을 계산 → 자정을 넘는 경우도 처리
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
                if (d * 1440 + to_minutes(t) - c["before"]) % WEEK_MIN == now_wm:
                    due.append(render_alert(c, t))
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
 
        if now.strftime("%H:%M") == "00:00":
            await refresh_all_panels()   # 날짜가 바뀌면 패널의 '오늘 일정'도 갱신
 
        if DAILY_SUMMARY_TIME and now.strftime("%H:%M") == DAILY_SUMMARY_TIME:
            await send_to_targets(embed=build_today_embed("📝 [오늘의 숙제] 주요 일정"))
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
async def install_panel(interaction: discord.Interaction, kind: str):
    """kind: 'admin'(관리자 패널) / 'info'(안내 패널). 같은 채널의 같은 종류 패널은 정리 후 새로 설치"""
    channel = interaction.channel
    await interaction.response.defer(ephemeral=True, thinking=True)
    titles = ALL_PANEL_TITLES if kind == "admin" else {INFO_PANEL_TITLE}
 
    removed = 0
    try:
        async for msg in channel.history(limit=50):
            if (msg.author.id == bot.user.id and msg.embeds
                    and msg.embeds[0].title in titles):
                await msg.delete()
                panel_messages.pop((channel.id, msg.id), None)
                removed += 1
    except discord.Forbidden:
        pass
 
    try:
        if kind == "admin":
            new_msg = await channel.send(embed=build_panel_embed(), view=PanelView(), file=backup_file())
        else:
            new_msg = await channel.send(embed=build_info_panel_embed(), view=InfoPanelView())
        panel_messages[(channel.id, new_msg.id)] = kind
    except discord.Forbidden:
        await interaction.followup.send(
            "⚠️ 이 채널에 메시지(또는 파일)를 보낼 권한이 없습니다. 봇 권한을 확인해 주세요.", ephemeral=True)
        return
 
    note = f" (이전 패널 {removed}개 정리)" if removed else ""
    if kind == "admin":
        msg = (f"✅ **관리자 패널**을 설치했습니다.{note}\n"
               "💡 관리자 패널은 관리자만 볼 수 있는 채널에 두시는 것을 추천드립니다. "
               "(버튼은 어디에 있든 관리자만 사용할 수 있습니다)\n"
               "⚠️ 패널에 붙은 백업 파일은 지우지 마세요. 재배포 후 일정 복원에 사용됩니다.")
    else:
        msg = f"✅ 누구나 사용하는 **안내 패널**을 설치했습니다.{note}"
    await interaction.followup.send(msg, ephemeral=True)
 
 
@bot.tree.command(name="패널생성", description="이 채널에 일정 관리 패널(관리자 전용)을 설치합니다.")
@app_commands.guild_only()
@app_commands.default_permissions(manage_guild=True)
async def create_panel(interaction: discord.Interaction):
    await install_panel(interaction, "admin")
 
 
@bot.tree.command(name="안내패널생성", description="이 채널에 누구나 쓰는 안내 패널(일정·공지·업데이트·CM 아지트)을 설치합니다.")
@app_commands.guild_only()
@app_commands.default_permissions(manage_guild=True)
async def create_info_panel(interaction: discord.Interaction):
    await install_panel(interaction, "info")
 
 
@bot.tree.command(name="일정", description="오늘의 주요 일정을 확인합니다.")
async def schedule_command(interaction: discord.Interaction):
    await interaction.response.send_message(embed=build_today_embed(), ephemeral=True)
 
 
@bot.tree.command(name="도움말", description="장벽봇 사용 방법을 확인합니다.")
async def help_command(interaction: discord.Interaction):
    await interaction.response.send_message(embed=build_help_embed(), ephemeral=True)
 
 
@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.MissingPermissions):
        await send_error(interaction, "⚠️ 이 명령어는 서버 관리 권한이 있는 분만 쓰실 수 있습니다.")
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
