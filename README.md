# 🌸 Аниме-вики с авто-трендами

Вики на статическом сайте ([MkDocs Material](https://squidfunk.github.io/mkdocs-material/)),
которая **сама собирает актуальные темы обсуждения** после выхода новых серий аниме.
В день премьеры GitHub Actions запускает скрипт, тот спрашивает
[Google Gemini](https://ai.google.dev/) (с веб-поиском), что сейчас активно обсуждают
по свежей серии — реакции, теории, мемы, споры, разборы анимации — структурирует ответ
в Markdown и публикует на сайт.

```
schedule.yaml ──┐
                ▼
        fetch_trends.py ──Gemini + Google Search──▶ docs/anime/<slug>/ep-NN.md
                │                                              │
                └──▶ Главная / Календарь / «Все тайтлы»        ▼
                                                    GitHub Pages (auto-deploy)
```

## Структура проекта

```
anime-wiki/
├── schedule.yaml              # список аниме + расписание + настройки
├── mkdocs.yml                 # конфиг сайта (тема, плагины)
├── docs/                      # контент вики
│   ├── index.md               # главная (генерируется автоматически)
│   ├── calendar.md            # календарь выхода (генерируется)
│   ├── tags.md
│   ├── about.md
│   └── anime/
│       ├── index.md           # «Все тайтлы» (генерируется)
│       └── <slug>/
│           ├── index.md       # страница тайтла (из schedule.yaml)
│           └── ep-01.md …     # обсуждения серий (из Gemini)
├── scripts/
│   ├── fetch_trends.py        # генератор трендов
│   └── requirements.txt
└── .github/workflows/
    └── update-and-deploy.yml  # cron + сборка + деплой
```

## 🚀 Быстрый старт (локально)

```bash
cd anime-wiki

# виртуальное окружение и зависимости
python -m venv .venv
# Windows:  .venv\Scripts\activate
# Linux/Mac: source .venv/bin/activate
pip install -r scripts/requirements.txt

# тест генератора БЕЗ ключа Gemini (шаблонные страницы):
python scripts/fetch_trends.py --anime frieren --episode 1 --dry-run

# посмотреть сайт локально:
mkdocs serve     # открой http://127.0.0.1:8000
```

## ➕ Добавить новое аниме

Открой `schedule.yaml` и добавь запись по шаблону:

```yaml
  - slug: "dandadan"            # латиница, без пробелов — попадает в URL
    title: "Dandadan"
    title_ru: "Дандадан"
    title_jp: "ダンダダン"
    aliases: ["Dandadan"]
    season: 1
    weekday: "Thursday"         # день выхода (Monday..Sunday)
    time: "16:00"
    start_date: "2026-04-03"    # дата ПЕРВОЙ серии сезона (ГГГГ-ММ-ДД)
    episodes: 12                # ожидаемое число серий
    tags: ["экшен", "сверхъестественное", "комедия"]
    synopsis: >
      Короткое описание сюжета в двух-трёх предложениях.
```

При следующем запуске вики сама создаст страницу тайтла. Серии будут появляться
по мере выхода (скрипт считает номер серии от `start_date` и еженедельного каденса).

## 🔑 Получить ключ Gemini API

1. Зайди на <https://aistudio.google.com/apikey>.
2. Нажми **Create API key** (бесплатно, щедрый бесплатный лимит).
3. Ключ понадобится на следующем шаге.

## 🌐 Публикация на GitHub Pages

1. **Создай репозиторий** (например, `anime-wiki`) и запушь проект:
   ```bash
   git init && git add -A && git commit -m "init"
   git branch -M main
   git remote add origin https://github.com/USERNAME/anime-wiki.git
   git push -u origin main
   ```
2. В `mkdocs.yml` замени **`USERNAME`** (4 места: `site_url`, `repo_url`, `repo_name`, ссылка в `extra.social`) на свой аккаунт.
3. **Добавь секрет:** Settings → Secrets and variables → Actions → *New repository secret*:
   - Name: `GEMINI_API_KEY`
   - Value: твой ключ из AI Studio
4. **Включи Pages:** Settings → Pages → **Source: GitHub Actions**.
5. Готово. Workflow запустится при пуше — открой вкладку **Actions**, дождись зелёного
   кружка, и сайт будет жить по адресу `https://USERNAME.github.io/anime-wiki/`.

## ⚙️ Как работает автоматизация

Workflow `.github/workflows/update-and-deploy.yml`:
- **по расписанию** (ежедневно 06:00 UTC) и **по ручному запуску** — собирает тренды
  для всех «должных» серий (вышедших за последние `--since-days` дней, по умолчанию 3),
  коммитит новые страницы и деплоит;
- **по пушу в main** — просто пересобирает и деплоит то, что уже в репозитории.

Принудительно обновить конкретную серию — со вкладки Actions → *Run workflow*, либо
локально:
```bash
GEMINI_API_KEY=твой_ключ python scripts/fetch_trends.py --anime frieren --episode 14 --commit
```

## 🛠️ Команды генератора

```bash
python scripts/fetch_trends.py --all-due                 # всё свежее (для cron)
python scripts/fetch_trends.py --anime <slug>             # последняя вышедшая серия
python scripts/fetch_trends.py --anime <slug> --episode N # конкретная серия
python scripts/fetch_trends.py --all-due --since-days 5   # шире окно «свежести»
python scripts/fetch_trends.py ... --dry-run              # без Gemini (шаблоны)
python scripts/fetch_trends.py ... --commit               # закоммитить и запушить
```

## 📝 Заметки

- Язык страниц обсуждения задаётся в `schedule.yaml` (`language: "ru"`), модель — там же
  (`gemini_model`, по умолчанию `gemini-2.5-flash`).
- Gemini с Google Search имеет лимиты на бесплатном тарифе — для нескольких тайтлов в неделю
  этого с запасом хватает.
- Часовой пояс эфира — `timezone` в `schedule.yaml` (по умолчанию `Asia/Tokyo`). Скрипт
  считает даты выхода серий по еженедельному каденсу от `start_date`.
