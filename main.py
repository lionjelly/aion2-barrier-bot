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
 
ADMIN_PANEL_TITLE = "⚙️ 장벽봇 관리자 패널"
INFO_PANEL_TITLE = "📌 장벽봇 안내패널"
BACKUP_TITLE = "💾 장벽봇 일정 백업"
 
# 예전 버전 제목 (정리 + 복원에 사용)
OLD_ADMIN_TITLES = {
    "⚙️ 장벽봇 일정 관리 패널 · 시간 수정 가능",
    "🛡️ 장벽봇 · 주간 콘텐츠 시간표",
    "🛡️ 장벽봇 · 보스 & 콘텐츠 시간표",
    "🛡️ 장벽봇 패널",
}
ALL_INFO_TITLES = {INFO_PANEL_TITLE, "📌 장벽봇 안내 · 누구나 사용 가능"}
 
SCHEDULE_FILE = "schedule.json"
BACKUP_FILENAME = "장벽봇_일정백업.json"
DAILY_SUMMARY_TIME = "19:00"   # 매일 '오늘의 주요 일정' 요약 (끄려면 None)
ADMIN_PANEL_MINUTES = 14       # 관리자 패널(본인만 보이는 창) 버튼 사용 가능 시간
 
DAY_NAMES = ["월", "화", "수", "목", "금", "토", "일"]
 
# 안내패널 "문의" 표시 + 백업을 보관할 관리자 DM
panel_admin: dict = {"id": None, "name": None}
ADMIN_URL_RE = re.compile(r"discord\.com/users/(\d+)")
 
# ─────────────────────────────────────────
# 콘텐츠 기본 시간표 (2026-10-01 백업 기준)
# days: 0=월 1=화 2=수 3=목 4=금 5=토 6=일
# important: True면 '오늘의 주요 일정'(안내패널·19시 요약)에 표시
# repeat_hours: 값이 있으면 기준 시간 하나만 입력해도 그 간격으로 자동 반복
# comment: 알림에 함께 나가는 안내 문구
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
        "times": ["22:00"], "before": 10, "important": True,
        "comment": "어비스 균열이 곧 열립니다! 균열을 봉쇄하고 보상을 획득하실 수 있도록 "
                   "미리 접속해서 준비해 주세요.",
    },
    "abyss_boss": {
        "name": "어비스보스", "emoji": "👹", "days": [2, 5],
        "times": ["21:45"], "before": 10, "important": True,
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
        "times": ["21:20"], "before": 10, "important": True,
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
info_panels: set[tuple[int, int]] = set()   # (channel_id, message_id)
backup_ref: dict = {"channel": None, "message": None}
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
    if re.fullmatch(r"\d{1,2}", t):
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
    if c["before"] > 0:
        head = (f"📢 **[{c['emoji']} {c['name']}]** 시작 **{c['before']}분 전** 안내드립니다!\n"
                f"⏰ 시작 시간: **{start_time}**")
    else:
        head = f"📢 **[{c['emoji']} {c['name']}]** 지금 시작합니다! (⏰ {start_time})"
    comment = (c.get("comment") or "").strip()
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
    for key, c in DEFAULT_CONTENTS.items():
        if key not in data:
            result[key] = clean_one(key, c)
    for key, c in data.items():
        cleaned = clean_one(key, c)
        if cleaned:
            result[key] = cleaned
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
 
 
def build_backup_embed() -> discord.Embed:
    now = datetime.now(KST).strftime("%Y-%m-%d %H:%M")
    return discord.Embed(
        title=BACKUP_TITLE,
        description="일정을 수정할 때마다 자동으로 갱신됩니다.\n"
                    "봇이 재배포·재시작될 때 이 파일로 일정을 복원하니 **지우지 마세요.**",
        color=discord.Color.dark_grey(),
    ).set_footer(text=f"마지막 백업: {now} (KST)")
 
 
async def save_backup_to_dm(fallback_user=None):
    """수정한 일정을 관리자 DM에 백업 (메시지 하나를 계속 갱신)"""
    if not panel_admin["id"] and fallback_user is not None:
        panel_admin.update(id=fallback_user.id, name=fallback_user.display_name)
    if not panel_admin["id"]:
        return
    try:
        if backup_ref["message"]:
            msg = bot.get_partial_messageable(backup_ref["channel"]).get_partial_message(backup_ref["message"])
            try:
                await msg.edit(embed=build_backup_embed(), attachments=[backup_file()])
                return
            except discord.NotFound:
                pass
        user = await bot.fetch_user(panel_admin["id"])
        dm = user.dm_channel or await user.create_dm()
        new = await dm.send(embed=build_backup_embed(), file=backup_file())
        backup_ref.update(channel=dm.id, message=new.id)
    except Exception as e:
        logger.warning(f"DM 백업 실패: {e}")
 
 
async def read_backup(msg: discord.Message):
    for att in msg.attachments:
        if att.filename == BACKUP_FILENAME:
            return json.loads(await att.read()) or None
    return None
 
 
async def restore_schedule():
    """
    시작 시 복원 순서: 1) schedule.json  2) 관리자 DM 백업  3) (예전 버전) 채널 관리자 패널 백업  4) 기본 시간표
    + 예전 버전의 관리자 패널(채널/DM)은 정리
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
 
    old_admin_msgs = []
    channel_backup = None
 
    # 1) 서버 채널: 안내패널 위치 + 관리자 정보, 예전 관리자 패널
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
                emb = msg.embeds[0]
                if emb.title in ALL_INFO_TITLES:
                    info_panels.add((ch.id, msg.id))
                    m = ADMIN_URL_RE.search((emb.author.url or "") if emb.author else "")
                    if m and not panel_admin["id"]:
                        panel_admin["id"] = int(m.group(1))
                        panel_admin["name"] = (emb.author.name or "").replace("문의: ", "").replace(" (관리자)", "")
                elif emb.title in OLD_ADMIN_TITLES:
                    old_admin_msgs.append(msg)
                    if channel_backup is None:
                        channel_backup = await read_backup(msg)
        except Exception as e:
            logger.warning(f"#{ch.name} 패널 검색 실패: {e}")
 
    # 2) 관리자 DM 백업
    dm_backup = None
    candidates = [panel_admin["id"]] + [g.owner_id for g in bot.guilds]
    for uid in dict.fromkeys(u for u in candidates if u):
        try:
            user = await bot.fetch_user(uid)
            dm = user.dm_channel or await user.create_dm()
            async for msg in dm.history(limit=30):
                if not (msg.author.id == bot.user.id and msg.embeds):
                    continue
                title = msg.embeds[0].title
                if title == BACKUP_TITLE and backup_ref["message"] is None:
                    backup_ref.update(channel=dm.id, message=msg.id)
                    if dm_backup is None:
                        dm_backup = await read_backup(msg)
                elif title in OLD_ADMIN_TITLES:
                    old_admin_msgs.append(msg)
                    if dm_backup is None:
                        dm_backup = await read_backup(msg)
            if dm_backup and not panel_admin["id"]:
                panel_admin.update(id=user.id, name=user.display_name)
        except Exception as e:
            logger.warning(f"관리자 DM 확인 실패 ({uid}): {e}")
 
    if loaded is None:
        loaded = dm_backup or channel_backup
        if dm_backup:
            logger.info("[일정] 관리자 DM 백업에서 복원")
        elif channel_backup:
            logger.info("[일정] 예전 관리자 패널 백업에서 복원")
        else:
            loaded = {}
            logger.info("[일정] 기본 시간표 사용")
 
    contents = sanitize_contents(loaded)
    save_local()
    schedule_loaded = True
 
    # 새 방식 백업을 먼저 만든 뒤, 예전 관리자 패널 정리
    await save_backup_to_dm()
    for msg in old_admin_msgs:
        try:
            await msg.delete()
        except Exception:
            pass
    logger.info(f"[패널] 안내패널 {len(info_panels)}개 확인, 예전 관리자 패널 {len(old_admin_msgs)}개 정리")
 
 
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
    return ", ".join(f"{c['emoji']} {c['name']}" for c in contents.values() if not c["important"])
 
 
def today_value() -> str:
    now = datetime.now(KST)
    today = today_lines(now.weekday())
    value = "\n".join(today) if today else "오늘은 예정된 주요 콘텐츠가 없습니다."
    extra = daily_only_names()
    if extra:
        value += f"\n\n※ {extra}는 매일 제시간에 따로 알려드립니다."
    return value[:1024]
 
 
def build_admin_panel_embed() -> discord.Embed:
    now = datetime.now(KST)
    lines = []
    for c in contents.values():
        status = f"🔔 {c['before']}분 전 알림" if c["enabled"] else "🔕 알림 꺼짐"
        repeat = f" ({c['repeat_hours']}시간 간격)" if c.get("repeat_hours") else ""
        lines.append(f"{c['emoji']} **{c['name']}** — {days_text(c['days'])}{repeat}\n"
                     f"┗ ⏰ {' · '.join(c['times'])}  |  {status}")
    embed = discord.Embed(
        title=ADMIN_PANEL_TITLE,
        description="**⚙️ 일정 수정** · 시간·요일·알림 시기·안내 문구 변경\n"
                    "**🐲 아그로 젠 시간 수정** · 점검 후 첫 젠 시간만 입력\n\n"
                    + ("\n".join(lines) or "등록된 콘텐츠가 없습니다."),
        color=discord.Color.purple(),
    )
    embed.set_footer(text=f"마지막 새로고침: {now.strftime('%H:%M:%S')} · "
                          f"버튼은 {ADMIN_PANEL_MINUTES}분간 사용 가능 · 수정 내용은 DM으로 자동 백업")
    return embed
 
 
def build_info_panel_embed() -> discord.Embed:
    now = datetime.now(KST)
    embed = discord.Embed(
        title=INFO_PANEL_TITLE,
        description=("📅 **오늘의 주요 일정** · 오늘 예정된 보스/콘텐츠 시간\n"
                     "📢 **공지사항** · 아이온2 공식 공지 최신 5개\n"
                     "🚀 **업데이트** · 최신 패치노트 5개\n"
                     "🏠 **CM 아지트** · 업데이트 뉴스 등 CM 소식 5개\n"
                     "❓ **도움말** · 장벽봇 사용법"),
        color=discord.Color.teal(),
    )
    if panel_admin["id"]:
        embed.set_author(name=f"문의: {panel_admin['name'] or '관리자'} (관리자)",
                         url=f"https://discord.com/users/{panel_admin['id']}")
    embed.add_field(name=f"📅 오늘({DAY_NAMES[now.weekday()]}) 주요 일정", value=today_value(), inline=False)
    embed.set_footer(text=f"마지막 갱신: {now.strftime('%Y-%m-%d %H:%M')} (KST)")
    return embed
 
 
def build_today_embed(title_prefix: str = "📅 오늘의 주요 일정") -> discord.Embed:
    now = datetime.now(KST)
    return discord.Embed(
        title=f"{title_prefix} · {now.strftime('%m월 %d일')} ({DAY_NAMES[now.weekday()]})",
        description=today_value(),
        color=discord.Color.gold(),
    )
 
 
def build_editor_embed(key: str) -> discord.Embed:
    c = contents[key]
    embed = discord.Embed(
        title=f"⚙️ {c['emoji']} {c['name']} 설정",
        description=(
            "**1단계** 아래 메뉴에서 알림 **요일** 선택\n"
            "**2단계** **⏰ 시간·알림·문구 수정** 버튼으로 나머지 입력\n"
            "바꾸는 즉시 **자동 저장**되고 안내패널에도 반영됩니다."
        ),
        color=discord.Color.green() if c["enabled"] else discord.Color.dark_grey(),
    )
    repeat = f" ({c['repeat_hours']}시간 간격 자동)" if c.get("repeat_hours") else ""
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
 
 
def build_help_embed(is_admin: bool = False) -> discord.Embed:
    embed = discord.Embed(
        title="🤖 장벽봇 도움말",
        description="아이온2 일정과 공식 소식을 **24시간 자동으로** 알려 드려요.",
        color=discord.Color.blue(),
    )
    embed.add_field(
        name="🔔 자동 알림",
        value=(f"• 보스·콘텐츠 **시작 전** 알림\n"
               f"• 매일 **{DAILY_SUMMARY_TIME or '정해진 시간'}** 오늘의 주요 일정\n"
               f"• 공지 · 업데이트 · CM 아지트 **새 글**\n"
               f"→ 모두 `{AUTO_CHANNEL_NAME}` 채널로 보내 드려요."),
        inline=False,
    )
    embed.add_field(
        name="📌 안내패널",
        value="채널 맨 아래 패널의 버튼을 누르면\n**나에게만 보이는 창**으로 열려요.",
        inline=True,
    )
    embed.add_field(
        name="⌨️ 명령어",
        value="`/일정` 오늘의 주요 일정\n`/도움말` 이 안내",
        inline=True,
    )
    embed.add_field(
        name="❓ 문제가 있나요?",
        value=("• 알림이 안 와요 → 채널 알림을 **모든 메시지**로\n"
               "• 버튼·명령어가 안 돼요 → **디스코드 재시작**\n"
               "• 시간이 실제와 달라요 → **관리자에게 문의**"),
        inline=False,
    )
    if is_admin:
        embed.add_field(
            name="🔒 관리자 (관리자에게만 보여요)",
            value=("`/관리자패널` 일정 수정 · 아그로 젠 시간 변경\n"
                   "`/안내패널` 지금 채널에 안내패널 설치\n"
                   "수정한 일정은 **DM으로 자동 백업**돼요. (지우지 마세요)"),
            inline=False,
        )
    return embed
 
 
# ─────────────────────────────────────────
# 변경 저장 + 패널 갱신
# ─────────────────────────────────────────
async def refresh_info_panels():
    for ch_id, msg_id in list(info_panels):
        try:
            msg = bot.get_partial_messageable(ch_id).get_partial_message(msg_id)
            await msg.edit(embed=build_info_panel_embed(), view=InfoPanelView())
        except discord.NotFound:
            info_panels.discard((ch_id, msg_id))
        except Exception as e:
            logger.warning(f"안내패널 갱신 실패 ({ch_id}/{msg_id}): {e}")
 
 
async def commit_changes(origin: discord.Interaction | None = None, editor=None):
    """저장 → DM 백업 → 안내패널 갱신 → 열려 있는 관리자 패널 갱신"""
    save_local()
    await save_backup_to_dm(fallback_user=editor)
    await refresh_info_panels()
    if origin is not None:
        try:
            await origin.edit_original_response(embed=build_admin_panel_embed())
        except Exception:
            pass   # 관리자 패널 창을 닫았거나 시간이 지난 경우
 
 
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
# 일정 수정: 콘텐츠 선택
# ─────────────────────────────────────────
SELECT_GUIDE = "⚙️ **일정 수정** · 아래 메뉴에서 수정하실 콘텐츠를 선택해 주세요."
 
 
class ContentSelect(discord.ui.Select):
    def __init__(self, origin):
        self.origin = origin
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
            content=None, embed=build_editor_embed(key), view=ContentEditView(key, self.origin))
 
 
class ContentSelectView(discord.ui.View):
    def __init__(self, origin):
        super().__init__(timeout=600)
        self.add_item(ContentSelect(origin))
 
 
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
            embed=build_editor_embed(self.key), view=ContentEditView(self.key, view.origin))
        await commit_changes(view.origin, interaction.user)
 
 
# ─────────────────────────────────────────
# 일정 수정: 시간 · 알림 시기 · 안내 문구 팝업
# ─────────────────────────────────────────
class ContentTimeModal(discord.ui.Modal):
    def __init__(self, key: str, origin, from_panel: bool = False):
        c = contents[key]
        super().__init__(title=f"{c['name']} 시간·알림 수정"[:45])
        self.key = key
        self.origin = origin
        self.from_panel = from_panel
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
                embed=build_editor_embed(self.key), view=ContentEditView(self.key, self.origin))
        await commit_changes(self.origin, interaction.user)
 
    async def on_error(self, interaction: discord.Interaction, error: Exception):
        logger.error(f"시간 수정 팝업 오류: {error}")
        await send_error(interaction)
 
 
# ─────────────────────────────────────────
# 일정 수정 화면 (요일 메뉴 + 버튼)
# ─────────────────────────────────────────
class ContentEditView(discord.ui.View):
    def __init__(self, key: str, origin):
        super().__init__(timeout=600)
        self.key = key
        self.origin = origin
        c = contents[key]
        self.add_item(DaySelect(key))
        self.toggle_button.label = "알림 끄기" if c["enabled"] else "알림 켜기"
        self.toggle_button.emoji = "🔕" if c["enabled"] else "🔔"
        self.important_button.label = "주요 일정에서 빼기" if c["important"] else "주요 일정에 넣기"
 
    async def _refresh(self, interaction: discord.Interaction):
        await interaction.response.edit_message(
            embed=build_editor_embed(self.key), view=ContentEditView(self.key, self.origin))
        await commit_changes(self.origin, interaction.user)
 
    @discord.ui.button(label="2단계: 시간·알림·문구 수정", emoji="⏰", style=discord.ButtonStyle.success, row=1)
    async def time_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(ContentTimeModal(self.key, self.origin))
 
    @discord.ui.button(label="알림 끄기", style=discord.ButtonStyle.secondary, row=2)
    async def toggle_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        c = contents[self.key]
        c["enabled"] = not c["enabled"]
        logger.info(f"[일정 수정] {c['name']} 알림 {'켜짐' if c['enabled'] else '꺼짐'} (by {interaction.user})")
        await self._refresh(interaction)
 
    @discord.ui.button(label="주요 일정에서 빼기", emoji="📌", style=discord.ButtonStyle.secondary, row=2)
    async def important_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        contents[self.key]["important"] = not contents[self.key]["important"]
        await self._refresh(interaction)
 
    @discord.ui.button(label="안내 문구 기본값으로", emoji="♻️", style=discord.ButtonStyle.secondary, row=2)
    async def reset_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        contents[self.key]["comment"] = DEFAULT_CONTENTS.get(self.key, {}).get(
            "comment", "미리 접속해서 준비해 주세요!")
        await self._refresh(interaction)
 
    @discord.ui.button(label="다른 콘텐츠 선택", emoji="↩️", style=discord.ButtonStyle.primary, row=3)
    async def back_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(
            content=SELECT_GUIDE, embed=None, view=ContentSelectView(self.origin))
 
    async def on_error(self, interaction: discord.Interaction, error: Exception, item):
        logger.error(f"일정 편집 오류: {error}")
        await send_error(interaction)
 
 
# ─────────────────────────────────────────
# 관리자 패널 (본인만 보이는 창, /관리자패널)
# ─────────────────────────────────────────
class AdminPanelView(discord.ui.View):
    def __init__(self, origin: discord.Interaction):
        super().__init__(timeout=ADMIN_PANEL_MINUTES * 60)
        self.origin = origin
 
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if is_manager(interaction):
            return True
        await interaction.response.send_message("🔒 관리자만 사용할 수 있습니다.", ephemeral=True)
        return False
 
    @discord.ui.button(label="일정 새로고침", emoji="🔄", style=discord.ButtonStyle.primary)
    async def refresh_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(embed=build_admin_panel_embed(), view=self)
        await refresh_info_panels()
 
    @discord.ui.button(label="일정 수정", emoji="⚙️", style=discord.ButtonStyle.success)
    async def edit_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(
            SELECT_GUIDE, view=ContentSelectView(self.origin), ephemeral=True)
 
    @discord.ui.button(label="아그로 젠 시간 수정", emoji="🐲", style=discord.ButtonStyle.danger)
    async def agro_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if "agro" not in contents:
            await interaction.response.send_message("⚠️ 아그로 일정을 찾을 수 없습니다.", ephemeral=True)
            return
        await interaction.response.send_modal(ContentTimeModal("agro", self.origin, from_panel=True))
 
    async def on_error(self, interaction: discord.Interaction, error: Exception, item):
        logger.error(f"관리자 패널 오류: {error}")
        await send_error(interaction)
 
 
# ─────────────────────────────────────────
# 안내패널 (누구나 사용, 결과는 누른 사람에게만 보임 / 재시작 후에도 동작)
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
 
    @discord.ui.button(label="도움말", emoji="❓", row=1,
                       style=discord.ButtonStyle.success, custom_id="btn_help")
    async def help_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(
            embed=build_help_embed(is_manager(interaction)), ephemeral=True)
 
    async def on_error(self, interaction: discord.Interaction, error: Exception, item):
        logger.error(f"안내패널 오류 ({getattr(item, 'custom_id', item)}): {error}")
        await send_error(interaction)
 
 
# ─────────────────────────────────────────
# 봇 본체
# ─────────────────────────────────────────
class BarrierBot(commands.Bot):
    async def setup_hook(self):
        self.add_view(InfoPanelView())   # 재시작 후에도 안내패널 버튼 동작
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
# 콘텐츠 알림 (1분 주기, KST) — 자정을 넘는 'N분 전'도 처리
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
            await refresh_info_panels()   # 날짜가 바뀌면 '오늘 일정' 갱신
 
        if DAILY_SUMMARY_TIME and now.strftime("%H:%M") == DAILY_SUMMARY_TIME:
            await send_to_targets(embed=build_today_embed("📝 오늘의 숙제"))
    except Exception as e:
        logger.error(f"일정 알림 루프 예외 (다음 주기에 재시도): {e}")
 
 
@check_schedule_alerts.before_loop
async def before_schedule_alerts():
    await bot.wait_until_ready()
 
 
@bot.event
async def on_ready():
    logger.info(f"봇 로그인 완료: {bot.user} (ID: {bot.user.id}) / 서버 {len(bot.guilds)}개")
    first = not schedule_loaded
    await restore_schedule()
    if first:
        await refresh_info_panels()
    if not check_website_updates.is_running():
        check_website_updates.start()
    if not check_schedule_alerts.is_running():
        check_schedule_alerts.start()
 
 
# ─────────────────────────────────────────
# 안내패널 고정 + 항상 맨 아래 유지
# ─────────────────────────────────────────
pin_warned = False
info_bump_tasks: dict[int, asyncio.Task] = {}
info_bump_lock = asyncio.Lock()
 
 
async def try_pin(msg: discord.Message):
    global pin_warned
    try:
        await msg.pin(reason="장벽봇 안내패널 고정")
    except discord.Forbidden:
        if not pin_warned:
            logger.warning("안내패널 고정 실패: 봇에 '메시지 고정/메시지 관리' 권한이 필요합니다.")
            pin_warned = True
    except Exception as e:
        logger.warning(f"안내패널 고정 실패: {e}")
 
 
async def send_info_panel(channel) -> discord.Message:
    msg = await channel.send(embed=build_info_panel_embed(), view=InfoPanelView())
    info_panels.add((channel.id, msg.id))
    await try_pin(msg)
    return msg
 
 
async def bump_info_panel(channel_id: int):
    """채널에 새 메시지가 올라오면 5초 뒤 안내패널을 맨 아래로 다시 올림"""
    try:
        await asyncio.sleep(5)
        async with info_bump_lock:
            old = [k for k in info_panels if k[0] == channel_id]
            ch = bot.get_channel(channel_id)
            if not old or ch is None:
                return
            await send_info_panel(ch)
            for key in old:
                info_panels.discard(key)
                try:
                    await ch.get_partial_message(key[1]).delete()
                except discord.NotFound:
                    pass
    except asyncio.CancelledError:
        pass
    except Exception as e:
        logger.warning(f"안내패널 맨 아래 이동 실패: {e}")
 
 
@bot.event
async def on_message(message: discord.Message):
    if message.guild is None:
        return
    if message.type == discord.MessageType.pins_add and message.author.id == bot.user.id:
        try:
            await message.delete()   # "메시지를 고정했습니다" 안내 삭제
        except Exception:
            pass
        return
    if (message.author.id == bot.user.id and message.embeds
            and message.embeds[0].title in ALL_INFO_TITLES):
        return
    if any(k[0] == message.channel.id for k in info_panels):
        task = info_bump_tasks.get(message.channel.id)
        if task and not task.done():
            task.cancel()
        info_bump_tasks[message.channel.id] = asyncio.create_task(bump_info_panel(message.channel.id))
 
 
# ─────────────────────────────────────────
# 슬래시 명령어
# ─────────────────────────────────────────
@bot.tree.command(name="관리자패널", description="일정 수정용 관리자 패널을 엽니다. (본인만 보여요)")
@app_commands.guild_only()
@app_commands.default_permissions(manage_guild=True)
async def admin_panel_command(interaction: discord.Interaction):
    await interaction.response.send_message(
        embed=build_admin_panel_embed(), view=AdminPanelView(interaction), ephemeral=True)
 
 
@bot.tree.command(name="안내패널", description="이 채널에 누구나 쓰는 장벽봇 안내패널을 설치하고 고정합니다.")
@app_commands.guild_only()
@app_commands.default_permissions(manage_guild=True)
async def info_panel_command(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True, thinking=True)
    channel = interaction.channel
 
    removed = 0
    try:
        async for msg in channel.history(limit=50):
            if (msg.author.id == bot.user.id and msg.embeds
                    and msg.embeds[0].title in ALL_INFO_TITLES | OLD_ADMIN_TITLES):
                await msg.delete()
                info_panels.discard((channel.id, msg.id))
                removed += 1
    except discord.Forbidden:
        pass
 
    if not panel_admin["id"]:
        panel_admin.update(id=interaction.user.id, name=interaction.user.display_name)
 
    try:
        await send_info_panel(channel)
    except discord.Forbidden:
        await interaction.followup.send(
            "⚠️ 이 채널에 메시지를 보낼 권한이 없습니다. 봇 권한을 확인해 주세요.", ephemeral=True)
        return
 
    await save_backup_to_dm()
    note = f" (이전 패널 {removed}개 정리)" if removed else ""
    await interaction.followup.send(
        f"✅ **장벽봇 안내패널**을 설치하고 📌 고정했습니다.{note}\n"
        "새 메시지가 올라와도 안내패널은 항상 맨 아래로 다시 올라옵니다.", ephemeral=True)
 
 
@bot.tree.command(name="일정", description="오늘의 주요 일정을 확인합니다.")
async def schedule_command(interaction: discord.Interaction):
    await interaction.response.send_message(embed=build_today_embed(), ephemeral=True)
 
 
@bot.tree.command(name="도움말", description="장벽봇 사용 방법을 확인합니다.")
async def help_command(interaction: discord.Interaction):
    await interaction.response.send_message(embed=build_help_embed(is_manager(interaction)), ephemeral=True)
 
 
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
