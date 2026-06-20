#!/usr/bin/env python3
"""
fetch_trends.py — собирает актуальные темы обсуждения после выхода серии аниме
через Tavily (веб-поиск) + GLM/Z.AI (генерация) и публикует их как Markdown-страницы.

Стек:
    Tavily API  — поиск свежих обсуждений в интернете
    GLM (Z.AI)  — генерация структурированной страницы на основе найденного контекста
    MkDocs      — статический сайт

Структура страниц:
    docs/anime/<slug>/index.md      — страница тайтла (из schedule.yaml)
    docs/anime/<slug>/ep-NN.md      — страница обсуждения серии N

Использование:
    python scripts/fetch_trends.py --all-due              # всё свежее (для cron)
    python scripts/fetch_trends.py --anime <slug>          # последняя вышедшая серия
    python scripts/fetch_trends.py --anime <slug> --episode N
    python scripts/fetch_trends.py --anime <slug> --episode 1 --dry-run   # без API-ключей
    python scripts/fetch_trends.py --all-due --commit      # для CI (auto-commit)
    python scripts/fetch_trends.py --all-due --force       # перегенерировать даже существующие
    python scripts/fetch_trends.py --update-only           # только агрегирующие страницы

Переменные окружения:
    GLM_API_KEY     — ключ Z.AI / ZhipuAI (https://open.bigmodel.cn)
    TAVILY_API_KEY  — ключ Tavily (https://app.tavily.com)
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


def _front_matter(anime: dict, *, hide_nav: bool = False, nav_title: str | None = None) -> str:
    """Собирает YAML front-matter: теги + опционально hide:navigation + nav title."""
    parts = []
    tags = anime.get("tags", [])
    if tags:
        parts.append("tags:\n" + "\n".join(f"  - {t}" for t in tags))
    if hide_nav:
        parts.append("hide:\n  - navigation")
    if nav_title:
        # Экранируем кавычки в YAML строке
        parts.append(f'title: "{nav_title.replace(chr(34), chr(92)+chr(34))}"')
    if not parts:
        return ""
    return "---\n" + "\n".join(parts) + "\n---\n\n"


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
#  Tavily (поиск) + GLM (генерация)
# --------------------------------------------------------------------------- #
PROMPT_TEMPLATE = """\
Ты — редактор аниме-вики. На основе найденных веб-обсуждений свежей серии аниме
составь вдохновляющую страницу для вики на русском языке. Эта вики — источник
вдохновения для читателя, поэтому пиши живо, образно, с деталями и настроением,
но строго опирайся на предоставленные источники — не выдумывай факты.

Аниме: {title} (яп. {title_jp}; другие названия: {aliases}).
Сезон: {season}. Номер серии: {episode}. Дата выхода серии: {air_date}.

Ниже — свежие обсуждения из интернета, появившиеся после выхода этой серии.
Источники могут быть на английском, японском (5ch) или корейском (Naver) —
обобщай их на русском, учитывая нюансы разных фандомов:

{context}

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

Не добавляй раздел «Источники» — я добавлю его сам из найденных ссылок.
Пиши живо, образно, но нейтрально в оценках, без спойлеров-в-заголовках. Если по
какому-то разделу нет информации — коротко и изящно отметь, что обсуждений по
теме пока мало, не оставляй раздел пустым.
"""


@dataclass
class TrendResult:
    text: str
    sources: list[dict]  # [{"uri":..., "title":...}]


def _episode_matches(text: str, episode: int) -> bool:
    """Проверяет, упоминает ли URL/заголовок конкретно этот номер серии.
    Мультиязычно: EN (episode 12), JP (第12話), KR (12화). Не ловит 'episode 2' при N=12."""
    patterns = [
        rf"episode[\s_-]{episode}\b",       # EN: episode 12 / episode_12 / ep-12
        rf"\bep[\s_.-]{episode}\b",
        rf"#\s*{episode}\b",
        rf"第{episode}話",                   # JP: 第12話
        rf"{episode}話",
        rf"제\s*{episode}\s*화",             # KR: 제12화
        rf"\b{episode}화\b",
    ]
    return any(re.search(p, text, re.IGNORECASE) for p in patterns)


# Домены с реальными обсуждениями, сгруппированные по языку/региону.
_EN_FORUM_DOMAINS = ["reddit.com", "myanimelist.net", "anilist.co", "youtube.com"]
_FOREIGN_DOMAINS = ["5ch.net", "cafe.naver.com", "blog.naver.com"]


def _is_foreign(url: str) -> bool:
    return any(d in url for d in _FOREIGN_DOMAINS)


def _result_mentions_title(text: str, anime: dict) -> bool:
    """Проверяет, что результат действительно про это аниме (не созвучное название).
    Решает баг «Kill la Kill» вместо «Kill Ao». Учитывает англ./яп./рус. названия —
    критично для JP (5ch) результатов, где текст на кандзи/катакане."""
    candidates = [anime["title"]] + anime.get("aliases", []) + [
        anime.get("title_jp", ""), anime.get("title_ru", ""),
    ]
    text_lower = text.lower()
    # Нормализованная версия для сравнения (без интерпункта, пробелов, _ и -)
    text_norm = re.sub(r"[・\s_-]", "", text_lower)
    for c in candidates:
        if not c:
            continue
        c_clean = c.strip()
        has_cjk = bool(re.search(r"[^\x00-\x7F]", c_clean))  # non-ASCII → JP/KR
        if has_cjk:
            # CJK: нормализуем (убираем ・ и пробелы) — «キル・アオ» матчит «キルアオ»
            c_norm = re.sub(r"[・\s]", "", c_clean.lower())
            if len(c_norm) >= 2 and c_norm in text_norm:
                return True
        else:
            # Латиница: нормализуем candidate (без пробелов) и ищем в нормализованном text
            # «Kill Ao» → «killao» матчит «kill ao»/«killao»/«Kill_Ao»
            c_lat = re.sub(r"[\s_-]", "", c_clean.lower())
            if len(c_lat) >= 4 and c_lat in text_norm:
                return True
    return False


def _do_one_search(client, query: str, episode: int, air_date: dt.date, domains: list[str], anime: dict) -> list[dict]:
    """Один запрос к Tavily с постфильтром: эпизод-специфичность + название аниме."""
    try:
        response = client.search(
            query=query,
            max_results=6,
            search_depth="advanced",
            start_date=air_date.isoformat(),
            include_domains=domains,
        )
    except Exception:  # noqa: BLE001
        return []
    out = []
    for r in response.get("results", []):
        haystack = (r.get("url", "") + " " + r.get("title", ""))
        if not _result_mentions_title(haystack, anime):
            continue  # Kill la Kill вместо Kill Ao и пр. — отбрасываем
        if _episode_matches(haystack, episode):
            r["_spec"] = True
        out.append(r)
    return out


def _tavily_search(anime: dict, episode: int, air_date: dt.date) -> tuple[str, list[dict]]:
    """Мультпоиск обсуждений КОНКРЕТНОЙ серии: англоязычные форумы + японский 5ch + корейский Naver.

    Каждый регион ищется отдельным запросом на релевантном языке (это критично —
    английский запрос не находит JP/KR-треды). Результаты постфильтруются по номеру
    серии и названию аниме, затем диверсифицируются по регионам для «золотых» JP/KR
    источников.
    """
    try:
        from tavily import TavilyClient
    except ImportError:
        sys.exit("Не установлен tavily-python: pip install -r scripts/requirements.txt")

    api_key = os.environ.get("TAVILY_API_KEY")
    if not api_key:
        sys.exit("Нет TAVILY_API_KEY в окружении (получить: https://app.tavily.com).")

    client = TavilyClient(api_key=api_key)

    title_jp = anime.get("title_jp", "")
    aliases = anime.get("aliases", [])

    # 1) Англоязычные форумы (Reddit/MAL/Anilist/YouTube)
    en_query = f"{' '.join([anime['title']] + aliases[:2])} episode {episode} discussion"
    en_results = _do_one_search(client, en_query, episode, air_date, _EN_FORUM_DOMAINS, anime)

    # 2) Японский 5ch — запрос на японском (ромадзи/кандзи плохо ищутся на англ)
    jp_results = []
    if title_jp:
        jp_query = f"{title_jp} {episode}話"  # «第12話»-стиль
        jp_results = _do_one_search(client, jp_query, episode, air_date, ["5ch.net"], anime)

    # 3) Корейский Naver — корейские фаны знают англ./яп. названия
    kr_query_parts = [anime["title"]] + ([title_jp] if title_jp else [])
    kr_query = f"{' '.join(kr_query_parts)} {episode}화 리뷰"
    kr_results = _do_one_search(client, kr_query, episode, air_date, ["cafe.naver.com", "blog.naver.com"], anime)

    # Диверсификация: до 3 EN (приоритет эпизод-специфичным) + до 2 JP/KR («золото»)
    en_spec = [r for r in en_results if r.get("_spec")]
    en_gen = [r for r in en_results if not r.get("_spec")]
    picked = (en_spec + en_gen)[:3]
    foreign = jp_results + kr_results
    picked += foreign[:2]
    # добиваем до 6
    seen = {r.get("url") for r in picked}
    for r in en_gen + en_spec:
        if len(picked) >= 6:
            break
        if r.get("url") not in seen:
            picked.append(r)
            seen.add(r.get("url"))
    picked = picked[:6]

    if not picked:
        return "", []

    context_parts = []
    sources = []
    for i, r in enumerate(picked):
        title = r.get("title", "")
        url = r.get("url", "")
        content = r.get("content", "")[:800]
        region = "🇯🇵" if "5ch.net" in url else ("🇰🇷" if "naver.com" in url else "")
        prefix = f"{region} " if region else ""
        context_parts.append(f"{prefix}[{i+1}] {title}\n{url}\n{content}")
        if url:
            sources.append({"uri": url, "title": title or url})

    return "\n\n".join(context_parts), sources


def _glm_generate(prompt: str, *, model: str, max_tokens: int) -> str:
    """Генерирует текст через GLM (Z.AI, OpenAI-совместимый API)."""
    try:
        from openai import OpenAI
    except ImportError:
        sys.exit("Не установлен openai: pip install -r scripts/requirements.txt")

    api_key = os.environ.get("GLM_API_KEY")
    if not api_key:
        sys.exit("Нет GLM_API_KEY в окружении (получить: https://open.bigmodel.cn).")

    client = OpenAI(api_key=api_key, base_url="https://api.z.ai/api/paas/v4/")

    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=max_tokens,
        temperature=0.7,
    )
    return (response.choices[0].message.content or "").strip()


def fetch_trends(anime: dict, episode: int, air_date: dt.date, *, model: str, max_tokens: int) -> TrendResult:
    """Полный цикл: Tavily поиск → GLM генерация. Требует GLM_API_KEY и TAVILY_API_KEY."""
    # 1. Поиск свежих обсуждений через Tavily
    context, sources = _tavily_search(anime, episode, air_date)

    if not context.strip():
        print(f"  ⚠️  Tavily не нашёл результатов для {anime['slug']} ep{episode} — пропускаю.")
        return TrendResult(text="", sources=[])

    # 2. Генерация страницы через GLM
    prompt = PROMPT_TEMPLATE.format(
        title=anime["title"],
        title_jp=anime.get("title_jp", "—"),
        aliases=", ".join(anime.get("aliases", [])) or "—",
        season=anime.get("season", 1),
        episode=episode,
        air_date=air_date.isoformat(),
        context=context,
    )

    text = _glm_generate(prompt, model=model, max_tokens=max_tokens)
    return TrendResult(text=text, sources=sources)


# --------------------------------------------------------------------------- #
#  Рендер страниц
#  textwrap.dedent применяется к ШАБЛОНУ до .format(), иначе вставляемый
#  многострочный {body} ломает левый отступ.
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
        front_matter=_front_matter(anime, hide_nav=True),
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
            "- _Это шаблонная страница (dry-run без API-ключей)._ "
            "После настройки `GLM_API_KEY` и `TAVILY_API_KEY` здесь появятся реальные темы обсуждения.\n\n"
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
    """(Пере)создаёт страницу тайтла из расписания — держит её синхронной с schedule.yaml."""
    path = anime_page_path(anime["slug"])
    wd = str(anime.get("weekday", "")).lower()
    wd_idx = WEEKDAYS.get(wd)
    wd_ru = f"по {RU_WEEKDAYS_PREP[wd_idx]}" if wd_idx is not None else str(anime.get("weekday", "—"))
    content = _ANIME_PAGE_TMPL.format(
        front_matter=_front_matter(anime, nav_title=anime.get("title_ru") or anime["title"]),
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

    # «Свежие обновления» считаем с диска — блок всегда отражает реальное состояние.
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
        recent = "_Пока нет обновлений. Они появятся после первого запуска генератора._"
    text = replace_block(text, "AUTO-RECENT", recent)
    index.write_text(text, encoding="utf-8")


def update_anime_index(sched: dict) -> None:
    """Перегенерирует docs/anime/index.md из расписания."""
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
        "Полное расписание — на странице [Календарь](../calendar.md).\n\n"
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
def process_episode(anime: dict, episode: int, *, model: str, dry_run: bool, max_tokens: int) -> bool:
    air_date = air_date_for_episode(anime, episode)
    if dry_run:
        md = render_dryrun_page(anime, episode, air_date)
    else:
        try:
            result = fetch_trends(anime, episode, air_date, model=model, max_tokens=max_tokens)
        except Exception as e:  # noqa: BLE001 — ошибка API: пропускаем, но не роняем весь прогон
            print(f"  ⚠️  Ошибка API для {anime['slug']} ep{episode} ({type(e).__name__}: {str(e)[:120]}) — пропускаю.")
            return False
        if not result.text.strip():
            print(f"  ⚠️  Пустой ответ GLM для {anime['slug']} ep{episode} — пропускаю.")
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
    ap = argparse.ArgumentParser(description="Сбор трендов через Tavily + GLM.")
    ap.add_argument("--anime", help="slug конкретного тайтла")
    ap.add_argument("--episode", type=int, help="номер серии (с --anime)")
    ap.add_argument("--all-due", action="store_true", help="все свежие (для cron)")
    ap.add_argument("--since-days", type=int, default=3, help="окно «свежести» в днях (по умолч. 3)")
    ap.add_argument("--dry-run", action="store_true", help="без API (шаблонные страницы)")
    ap.add_argument("--commit", action="store_true", help="закоммитить и запушить (для CI)")
    ap.add_argument("--force", action="store_true", help="перегенерировать даже существующие страницы")
    ap.add_argument("--update-only", action="store_true", help="только агрегирующие страницы")
    args = ap.parse_args()

    sched = load_schedule()
    model = sched.get("llm_model", "glm-4.6")
    max_tokens = sched.get("llm_max_tokens", 2000)
    inter_call_delay = sched.get("llm_inter_call_delay", 5)
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
        if ep and process_episode(a, ep, model=model, dry_run=args.dry_run, max_tokens=max_tokens):
            generated.append((a, ep))
    elif args.all_due:
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
                if process_episode(a, ep, model=model, dry_run=args.dry_run, max_tokens=max_tokens):
                    generated.append((a, ep))
                    # ИНКРЕМЕНТАЛЬНЫЙ КОММИТ: сохраняем прогресс сразу после каждой серии.
                    if args.commit:
                        update_anime_episodes_block(a)
                        update_index(sched, generated)
                        update_calendar(sched)
                        update_anime_index(sched)
                        git_commit_push(f"chore(wiki): +{a['slug']} ep{ep}")
                # пауза между реальными запросами к API
                if not args.dry_run and inter_call_delay > 0:
                    time.sleep(inter_call_delay)
    elif args.update_only:
        pass
    else:
        ap.error("Укажите --all-due, --anime [--episode] или --update-only.")

    for a in sched["anime"]:
        update_anime_episodes_block(a)
    update_index(sched, generated)
    update_calendar(sched)
    update_anime_index(sched)

    if not generated:
        print("ℹ️  Нет новых выпусков для обработки.")
        return 0

    print(f"\nГотово. Обновлено страниц: {len(generated)}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
