#!/usr/bin/env python3
"""
fetch_trends.py — собирает актуальные темы обсуждения после выхода серии аниме
через Google Gemini (с инструментом Google Search / grounding) и публикует их
как Markdown-страницы для MkDocs.

Структура страниц:
    docs/anime/<slug>/index.md      — страница тайтла (с расписанием и списком серий)
    docs/anime/<slug>/ep-NN.md      — страница обсуждения серии N

Использование:
    # Сгенерировать страницы для всех серий, вышедших за последние дни (для cron):
    python scripts/fetch_trends.py --all-due
    # Принудительно обновить конкретную серию:
    python scripts/fetch_trends.py --anime frieren --episode 14
    # Тест без ключа Gemini (шаблонная страница):
    python scripts/fetch_trends.py --anime frieren --episode 1 --dry-run
    # С коммитом и пушем (для CI):
    python scripts/fetch_trends.py --all-due --commit

Переменные окружения:
    GEMINI_API_KEY  — ключ из https://aistudio.google.com/apikey (нужен, кроме --dry-run)
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import re
import subprocess
import sys
import textwrap
import time
from dataclasses import dataclass
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover
    sys.exit("Не хватает pyyaml: pip install -r scripts/requirements.txt")

ROOT = Path(__file__).resolve().parents[1]
SCHEDULE = ROOT / "schedule.yaml"
DOCS = ROOT / "docs"
ANIME_DIR = DOCS / "anime"

WEEKDAYS = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
}
RU_WEEKDAYS = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]
RU_WEEKDAYS_PREP = ["понедельникам", "вторникам", "средам", "четвергам", "пятницам", "субботам", "воскресеньям"]


# --------------------------------------------------------------------------- #
#  Утилиты
# --------------------------------------------------------------------------- #
def load_schedule() -> dict:
    with SCHEDULE.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if not data or "anime" not in data:
        sys.exit(f"В {SCHEDULE} нет списка anime.")
    return data


def anime_page_path(slug: str) -> Path:
    return ANIME_DIR / slug / "index.md"


def page_path(anime: dict, episode: int) -> Path:
    return ANIME_DIR / anime["slug"] / f"ep-{episode:02d}.md"


def air_date_for_episode(anime: dict, episode: int) -> dt.date:
    start = dt.date.fromisoformat(str(anime["start_date"]))
    return start + dt.timedelta(weeks=episode - 1)


def latest_aired_episode(anime: dict, today: dt.date) -> int:
    start = dt.date.fromisoformat(str(anime["start_date"]))
    if today < start:
        return 0
    weeks_passed = (today - start).days // 7
    return min(weeks_passed + 1, int(anime.get("episodes", weeks_passed + 1)))


def _front_matter_tags(anime: dict) -> str:
    tags = anime.get("tags", [])
    if not tags:
        return ""
    body = "\n".join(f"  - {t}" for t in tags)
    return f"---\ntags:\n{body}\n---\n\n"


def _tags_plain(anime: dict) -> str:
    return " · ".join(f"#{t}" for t in anime.get("tags", [])) or "—"


def replace_block(text: str, marker: str, new_inner: str) -> str:
    """Заменяет содержимое между маркерами <!-- MARKER -->...<!-- /MARKER -->."""
    pattern = re.compile(
        r"(<!--\s*" + re.escape(marker) + r"\s*-->)(.*?)(<!--\s*/" + re.escape(marker) + r"\s*-->)",
        re.DOTALL,
    )
    new, n = pattern.subn(
        lambda m: f"{m.group(1)}\n{new_inner.strip()}\n{m.group(3)}", text
    )
    if n == 0:
        new = text.rstrip() + f"\n\n<!-- {marker} -->\n{new_inner.strip()}\n<!-- /{marker} -->\n"
    return new


# --------------------------------------------------------------------------- #
#  Gemini + Google Search
# --------------------------------------------------------------------------- #
# Базовый промпт — общие/международные тренды. Используется всегда.
PROMPT_BASE = """\
Ты — редактор аниме-вики. Найди в интернете и подробно, вдохновляюще опиши, ЧТО
ИМЕННО СЕЙЧАС активно обсуждают зрители и сообщества по свежей серии указанного
аниме. Эта вики — источник вдохновения для читателя, поэтому пиши живо, образно,
с деталями и настроением, но без выдумок.

Аниме: {title} (яп. {title_jp}; другие названия: {aliases}).
Сезон: {season}. Номер серии: {episode}. Дата выхода серии: {air_date}.

Используй веб-поиск, чтобы найти СВЕЖИЕ обсуждения (последние несколько дней):
рецензии, посты на Reddit/форумах, видео-разборы, теории, мемы, споры. Опирайся
на реальные публикации, не выдумывай факты.

Оформи ответ СТРОГО в формате Markdown на русском языке со следующими разделами:

## 🔥 Главные темы обсуждения
- 4–7 ключевых тем, которые больше всего обсуждают (по одному пункту на тему,
  с ёмкой поясняющей фразой, передающей суть дискуссии).

## 💬 Реакции зрителей
- Что вызвало самую бурную реакцию (восторг, шок, слёзы и т.п.). Опиши накал
  эмоций ярко и конкретно.

## 🧩 Теории и догадки
- Популярные теории и предположения о сюжете/персонажах, даже самые смелые.

## 🎭 Запомнившиеся моменты
- Яркие сцены, цитаты, повороты сюжета — то, что заставляет пересматривать.

## 😂 Мемы и шутки
- Мемы, родившиеся вокруг этой серии, с пояснением контекста шутки (если есть).

## ⚡ Спорные моменты
- Разногласия в сообществе, критика, защищаемые и оспариваемые мнения (если есть).

## 🎬 Производство и анимация
- Заметки о качестве анимации, режиссуре, саундтреке, ключевых аниматорах (если
  обсуждают).
"""

# Расширение — локальные тренды по JP и KOR фандомам. Добавляется к базовому,
# если в schedule.yaml стоит include_local_trends: true (по умолчанию) либо
# у конкретного тайтла local_trends != false.
PROMPT_LOCAL = """
## 🇯🇵 Тренды в японском фандоме
Проведи ОТДЕЛЬНЫЙ веб-поиск на японском языке по ключевым словам ромадзи/кандзи
({title_jp}). Ищи свежие обсуждения (последние несколько дней) в японских
сообществах: 5ch (旧2ちゃんねる), японский X/Twitter, Togetter,Peing, Ассоль,
note, KAI-YOU, Аниме! Аниме!, Gigazine. Опиши, ЧТО ИМЕННО живо обсуждают японские
зрители — от реакций до инсайдов и споров. 3–6 пунктов с пояснениями.

## 🇰🇷 Тренды в корейском фандоме
Проведи ОТДЕЛЬНЫЙ веб-поиск на корейском языке (используй название на хангыле
или ромадзи + «애니메이션», 「리뷰」). Ищи свежие обсуждения (последние несколько
дней) в корейских сообществах: DC Inside (디시인사이드), Arca.live, FM Korea,
Namu wiki, корейский X/Twitter, Мани아 비평. Опиши, ЧТО ИМЕННО обсуждают корейские
зрители — реакция, теории, мемы, локальные споры. 3–6 пунктов с пояснениями.
"""

PROMPT_FOOTER = """
Не добавляй раздел «Источники» — я добавлю его сам из найденных ссылок.
Пиши живо, образно, но нейтрально в оценках, без спойлеров-в-заголовках. Если по
какому-то разделу нет информации — коротко и изящно отметь, что обсуждений по
теме пока мало, не оставляй раздел пустым.
"""

DEFAULT_PROMPT = PROMPT_BASE + PROMPT_FOOTER
LOCAL_PROMPT = PROMPT_BASE + PROMPT_LOCAL + PROMPT_FOOTER


@dataclass
class TrendResult:
    text: str
    sources: list[dict]  # [{"uri":..., "title":...}]


def build_prompt(anime: dict, episode: int, air_date: dt.date, *, include_local: bool) -> str:
    tmpl = LOCAL_PROMPT if include_local else DEFAULT_PROMPT
    return tmpl.format(
        title=anime["title"],
        title_jp=anime.get("title_jp", "—"),
        aliases=", ".join(anime.get("aliases", [])) or "—",
        season=anime.get("season", 1),
        episode=episode,
        air_date=air_date.isoformat(),
    )


def fetch_trends(anime: dict, episode: int, air_date: dt.date, *, model: str, max_output_tokens: int = 1200, include_local: bool = False) -> TrendResult:
    """Реальный запрос к Gemini с Google Search. Требует GEMINI_API_KEY."""
    try:
        from google import genai
        from google.genai.types import GenerateContentConfig, GoogleSearch, Tool
    except ImportError:
        sys.exit("Не установлен google-genai: pip install -r scripts/requirements.txt")

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        sys.exit("Нет GEMINI_API_KEY в окружении (получить: https://aistudio.google.com/apikey).")

    client = genai.Client(api_key=api_key)
    search_tool = Tool(google_search=GoogleSearch())
    prompt = build_prompt(anime, episode, air_date, include_local=include_local)

    def _is_rate_limited(err: Exception) -> bool:
        """429 / RESOURCE_EXHAUSTED — нужно длинное ожидание, не короткий retry."""
        msg = str(err).lower()
        return any(k in msg for k in ("429", "resource_exhausted", "rate limit", "quota"))

    last_err = None
    for attempt in range(1, 6):  # до 5 попыток
        try:
            response = client.models.generate_content(
                model=model,
                contents=prompt,
                config=GenerateContentConfig(
                    tools=[search_tool], temperature=0.4, max_output_tokens=max_output_tokens
                ),
            )
            break
        except Exception as e:  # noqa: BLE001
            last_err = e
            if attempt < 5:
                if _is_rate_limited(e):
                    wait = 60  # rate-limit: ждём минуту на восстановление квоты
                    print(f"  ⏳ rate-limit на {anime['slug']} ep{episode}, жду {wait}с (попытка {attempt}/5)…")
                else:
                    wait = 2 ** attempt  # 2,4,8,16 сек для прочих ошибок
                time.sleep(wait)
                continue
            raise
    else:
        raise SystemExit(f"Gemini запрос не удался: {last_err}")

    text = (response.text or "").strip()
    sources = []
    try:
        meta = response.candidates[0].grounding_metadata
        for chunk in getattr(meta, "grounding_chunks", []) or []:
            web = getattr(chunk, "web", None)
            if web and getattr(web, "uri", None):
                sources.append({"uri": web.uri, "title": getattr(web, "title", web.uri) or web.uri})
    except Exception:  # noqa: BLE001
        pass
    return TrendResult(text=text, sources=sources)


# --------------------------------------------------------------------------- #
#  Рендер страниц
# --------------------------------------------------------------------------- #
_EPISODE_TMPL = textwrap.dedent("""\
    {front_matter}# {title} — Серия {episode}

    ![](https://img.shields.io/badge/серия-{episode_badge}-deeppurple) ![](https://img.shields.io/badge/дата-{air}-amber)

    **{title_ru}** · {title_jp} · Сезон {season}

    **Теги:** {tags}

    ---

    {body}
    {sources_md}
    ---
    📺 [← Все серии](index.md) · [🏠 На главную](../../index.md)
    """)


def render_episode_page(anime: dict, episode: int, air_date: dt.date, result: TrendResult) -> str:
    sources_md = ""
    if result.sources:
        items = "\n".join(f"- [{s['title']}]({s['uri']})" for s in result.sources)
        sources_md = f"\n## 🔗 Источники\n\n{items}\n"

    return _EPISODE_TMPL.format(
        front_matter=_front_matter_tags(anime),
        title=anime["title"],
        episode=episode,
        episode_badge=f"{episode:02d}",
        air=air_date.isoformat(),
        title_ru=anime.get("title_ru", anime["title"]),
        title_jp=anime.get("title_jp", ""),
        season=anime.get("season", 1),
        tags=_tags_plain(anime),
        body=result.text or "",
        sources_md=sources_md,
    )


def render_dryrun_page(anime: dict, episode: int, air_date: dt.date) -> str:
    placeholder = TrendResult(
        text=(
            "## 🔥 Главные темы обсуждения\n\n"
            "- _Это шаблонная страница (dry-run без ключа Gemini)._ "
            "После настройки `GEMINI_API_KEY` здесь появятся реальные темы обсуждения.\n\n"
            "## 💬 Реакции зрителей\n\n"
            "- _ожидается после первого реального запуска._\n"
        ),
        sources=[],
    )
    return render_episode_page(anime, episode, air_date, placeholder)


_ANIME_PAGE_TMPL = textwrap.dedent("""\
    {front_matter}# {title}

    **{title_ru}** · {title_jp}

    !!! abstract "О чём"
        {synopsis}

    **Теги:** {tags}

    ---

    ## 📅 Расписание выхода

    | Параметр | Значение |
    |---|---|
    | Сезон | {season} |
    | День выхода | {weekday} |
    | Премьера сезона | {start} |
    | Ожидаемо серий | {episodes} |

    ---

    ## 📖 Серии и обсуждения

    <!-- AUTO-EPISODES -->
    *Страницы обсуждений появятся после выхода серий.*
    <!-- /AUTO-EPISODES -->
    """)


def ensure_anime_page(anime: dict) -> Path:
    """(Пере)создаёт страницу тайтла из расписания — держит её синхронной с schedule.yaml.
    Блок AUTO-EPISODES заполняется отдельно в update_anime_episodes_block()."""
    path = anime_page_path(anime["slug"])
    wd = str(anime.get("weekday", "")).lower()
    wd_idx = WEEKDAYS.get(wd)
    wd_ru = f"по {RU_WEEKDAYS_PREP[wd_idx]}" if wd_idx is not None else str(anime.get("weekday", "—"))
    content = _ANIME_PAGE_TMPL.format(
        front_matter=_front_matter_tags(anime),
        title=anime["title"],
        title_ru=anime.get("title_ru", ""),
        title_jp=anime.get("title_jp", ""),
        synopsis=(anime.get("synopsis") or "—").strip(),
        tags=_tags_plain(anime),
        season=anime.get("season", "—"),
        weekday=wd_ru,
        start=anime.get("start_date", "—"),
        episodes=anime.get("episodes", "—"),
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def update_anime_episodes_block(anime: dict) -> None:
    path = ensure_anime_page(anime)
    slug = anime["slug"]
    ep_dir = path.parent
    links = []
    if ep_dir.is_dir():
        eps = sorted(ep_dir.glob("ep-*.md"))
        for p in reversed(eps):
            num = int(re.search(r"ep-(\d+)", p.stem).group(1))
            links.append(f"- 📺 [Серия {num}](./{p.stem}.md)")
    inner = "\n".join(links) if links else "*Страницы обсуждений появятся после выхода серий.*"
    text = path.read_text(encoding="utf-8")
    path.write_text(replace_block(text, "AUTO-EPISODES", inner), encoding="utf-8")


def update_index(sched: dict, generated: list[tuple[dict, int]]) -> None:
    index = DOCS / "index.md"
    text = index.read_text(encoding="utf-8") if index.exists() else ""

    lines = []
    for a in sched["anime"]:
        wd = str(a.get("weekday", "")).lower()
        wd_idx = WEEKDAYS.get(wd)
        wd_ru = f"по {RU_WEEKDAYS_PREP[wd_idx]}" if wd_idx is not None else ""
        lines.append(
            f'- **[{a["title"]}](anime/{a["slug"]}/index.md)** — '
            f'{a.get("title_ru", "")} · {a.get("season", 1)} сезон · {wd_ru}'.rstrip()
        )
    text = replace_block(text, "AUTO-ANIME-LIST", "\n".join(lines))

    # «Свежие обновления» считаем с диска: последняя вышедшая серия каждого тайтла,
    # для которой есть страница. Так блок всегда отражает реальное состояние.
    recent_eps = []
    for a in sched["anime"]:
        edir = ANIME_DIR / a["slug"]
        if not edir.is_dir():
            continue
        nums = sorted(int(re.search(r"ep-(\d+)", p.stem).group(1)) for p in edir.glob("ep-*.md"))
        if nums:
            recent_eps.append((a, nums[-1]))
    recent_eps.sort(key=lambda x: air_date_for_episode(x[0], x[1]), reverse=True)
    recent_eps = recent_eps[:5]
    if recent_eps:
        recent = "\n".join(
            f'- 🆕 **{a["title"]}** — [Серия {ep}](anime/{a["slug"]}/ep-{ep:02d}.md)'
            for a, ep in recent_eps
        )
    else:
        recent = "_Пока нет обновлений. Они появятся после первого запуска генератора трендов._"
    text = replace_block(text, "AUTO-RECENT", recent)
    index.write_text(text, encoding="utf-8")


def update_anime_index(sched: dict) -> None:
    """Перегенерирует страницу «Все тайтлы» (docs/anime/index.md) из расписания."""
    today = dt.date.today()
    lines = []
    for a in sched["anime"]:
        latest = latest_aired_episode(a, today)
        latest_str = f"вышло серий: {latest}" if latest else "премьера скоро"
        lines.append(
            f"- **[{a['title']}](./{a['slug']}/index.md)** — "
            f"{a.get('title_ru', '')} · {a.get('season', 1)} сезон · {latest_str}".rstrip()
        )
    body = (
        "# 📺 Все тайтлы\n\n"
        "Отслеживаемые аниме и обсуждения серий. Полное расписание — на странице "
        "[Календарь](../calendar.md).\n\n"
        + "\n".join(lines) + "\n"
    )
    ANIME_DIR.mkdir(parents=True, exist_ok=True)
    (ANIME_DIR / "index.md").write_text(body, encoding="utf-8")


def update_calendar(sched: dict) -> None:
    rows = []
    today = dt.date.today()
    for a in sched["anime"]:
        wd = str(a.get("weekday", "")).lower()
        wd_idx = WEEKDAYS.get(wd)
        wd_ru = RU_WEEKDAYS[wd_idx].capitalize() if wd_idx is not None else a.get("weekday", "—")
        latest = latest_aired_episode(a, today)
        rows.append(
            f"| [{a['title']}](anime/{a['slug']}/index.md) | {wd_ru} | "
            f"{a.get('season', 1)} | {a.get('start_date', '—')} | "
            f"{a.get('episodes', '—')} | {latest or '—'} |"
        )
    body = textwrap.dedent("""\
        # 📅 Календарь выхода

        | Аниме | День | Сезон | Премьера | Серий | Последняя вышедшая |
        |---|---|---|---|---|---|
        """) + "\n".join(rows) + "\n"
    (DOCS / "calendar.md").write_text(body, encoding="utf-8")


# --------------------------------------------------------------------------- #
#  Главный цикл
# --------------------------------------------------------------------------- #
def process_episode(anime: dict, episode: int, *, model: str, dry_run: bool, max_output_tokens: int = 1200, include_local: bool = False) -> bool:
    air_date = air_date_for_episode(anime, episode)
    if dry_run:
        md = render_dryrun_page(anime, episode, air_date)
    else:
        try:
            result = fetch_trends(anime, episode, air_date, model=model, max_output_tokens=max_output_tokens, include_local=include_local)
        except Exception as e:  # noqa: BLE001 — rate limit, таймаут и т.п.: пропускаем, но не роняем весь прогон
            print(f"  ⚠️  Ошибка Gemini для {anime['slug']} ep{episode} ({type(e).__name__}) — пропускаю.")
            return False
        if not result.text.strip():
            print(f"  ⚠️  Пустой ответ Gemini для {anime['slug']} ep{episode} — пропускаю.")
            return False
        md = render_episode_page(anime, episode, air_date, result)
    path = page_path(anime, episode)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(md, encoding="utf-8")
    print(f"  ✅ {anime['slug']} → ep-{episode:02d}.md")
    return True


def git_commit_push(message: str) -> None:
    for cmd in (["git", "add", "-A", "docs"], ["git", "commit", "-m", message], ["git", "push"]):
        subprocess.run(cmd, cwd=ROOT, check=False)


def main() -> int:
    ap = argparse.ArgumentParser(description="Сбор трендов обсуждений аниме через Gemini + Google Search.")
    ap.add_argument("--anime", help="slug конкретного тайтла")
    ap.add_argument("--episode", type=int, help="номер серии (с --anime)")
    ap.add_argument("--all-due", action="store_true", help="обработать все свежевышедшие серии (для cron)")
    ap.add_argument("--since-days", type=int, default=3, help="окно «свежести» в днях для --all-due (по умолч. 3)")
    ap.add_argument("--dry-run", action="store_true", help="без запроса к Gemini (шаблонные страницы)")
    ap.add_argument("--commit", action="store_true", help="закоммитить и запушить изменения (для CI)")
    ap.add_argument("--force", action="store_true", help="перегенерировать даже существующие страницы (после смены промпта)")
    ap.add_argument("--update-only", action="store_true", help="только обновить агрегирующие страницы (без запроса к Gemini)")
    args = ap.parse_args()

    sched = load_schedule()
    model = sched.get("gemini_model", "gemini-2.5-flash")
    max_output_tokens = sched.get("gemini_max_output_tokens", 1200)
    local_default = sched.get("include_local_trends", True)

    def wants_local(a: dict) -> bool:
        # per-anime override имеет приоритет над глобальным значением
        if "local_trends" in a:
            return bool(a["local_trends"])
        return local_default

    by_slug = {a["slug"]: a for a in sched["anime"]}
    today = dt.date.today()

    for a in sched["anime"]:
        ensure_anime_page(a)

    generated: list[tuple[dict, int]] = []

    if args.anime:
        if args.anime not in by_slug:
            sys.exit(f"Неизвестный slug '{args.anime}'. Доступно: {list(by_slug)}")
        a = by_slug[args.anime]
        ep = args.episode or latest_aired_episode(a, today)
        if ep and process_episode(a, ep, model=model, dry_run=args.dry_run, max_output_tokens=max_output_tokens, include_local=wants_local(a)):
            generated.append((a, ep))
    elif args.all_due:
        inter_call_delay = sched.get("gemini_inter_call_delay", 6)  # сек между запросами (анти rate-limit)
        for a in sched["anime"]:
            latest = latest_aired_episode(a, today)
            if not latest:
                continue
            for ep in range(1, latest + 1):
                air = air_date_for_episode(a, ep)
                if (today - air).days > args.since_days:
                    continue
                if page_path(a, ep).exists() and not args.dry_run and not args.force:
                    continue
                if process_episode(a, ep, model=model, dry_run=args.dry_run, max_output_tokens=max_output_tokens, include_local=wants_local(a)):
                    generated.append((a, ep))
                # пауза между реальными запросами к Gemini, чтобы не словить rate limit
                if not args.dry_run and inter_call_delay > 0:
                    time.sleep(inter_call_delay)
    elif args.update_only:
        pass  # только агрегирующие страницы, без обращения к Gemini
    else:
        ap.error("Укажите --all-due, --anime [--episode] или --update-only.")

    for a in sched["anime"]:
        update_anime_episodes_block(a)
    update_index(sched, generated)
    update_calendar(sched)
    update_anime_index(sched)

    if not generated:
        print("ℹ️  Нет новых серий для обработки.")
        return 0

    if args.commit:
        git_commit_push(f"chore(wiki): автообновление трендов — {len(generated)} стр.")

    print(f"\nГотово. Обновлено страниц: {len(generated)}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
