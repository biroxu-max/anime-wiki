# 🌸 Аниме-вики

Вики с актуальными темами обсуждения после выхода новых серий.
Страницы обновляются автоматически: в день премьеры серии скрипт
собирает тренды обсуждений (реакции, теории, мемы, разборы) через
Gemini с веб-поиском и публикует их сюда.

---

## 📺 Текущие тайтлы

<!-- AUTO-ANIME-LIST -->
- **[Himekishi wa Barbaroi no Yome](anime/himekishi/index.md)** — Принцесса-рыцарь — невеста варвара · 1 сезон · по четвергам
- **[Kujima Utaeba Ie Hororo](anime/kujima-utaeba/index.md)** — Дом, в котором щебечет Кудзима · 1 сезон · по четвергам
- **[Koori no Jouheki](anime/koori-no-jouheki/index.md)** — Ледяная стена · 1 сезон · по четвергам
- **[Snowball Earth](anime/snowball-earth/index.md)** — Земля-снежок · 1 сезон · по пятницам
- **[Yomi no Tsugai](anime/yomi-no-tsugai/index.md)** — Цугаи загробного мира · 1 сезон · по субботам
- **[Akane-banashi](anime/akane-banashi/index.md)** — Сказание об Аканэ · 1 сезон · по субботам
- **[Kill Ao](anime/kill-ao/index.md)** — Убивая юность · 1 сезон · по субботам
- **[Mao](anime/mao/index.md)** — Мао · 1 сезон · по субботам
- **[Shunkashuutou Daikousha: Haru no Mai](anime/shunkashuutou-haru/index.md)** — Агенты четырёх сезонов: Весенний танец · 1 сезон · по субботам
- **[Kami no Niwatsuki Kusunoki-tei](anime/kami-no-niwatsuki/index.md)** — Божественный сад у поместья Кусуноки · 1 сезон · по субботам
- **[Tsue to Tsurugi no Wistoria](anime/wistoria-s2/index.md)** — Меч и жезл Вистории (S2) · 2 сезон · по воскресеньям
- **[Kuroneko to Majo no Kyoushitsu](anime/kuroneko-majo/index.md)** — Чёрная кошка и класс ведьм · 1 сезон · по воскресеньям
- **[Ponkotsu Fuuki Iin to Skirt-take ga Futekisetsu na JK no Hanashi](anime/ponkotsu-fuuki/index.md)** — Бесполезный дежурный и школьница со слишком короткой юбкой · 1 сезон · по понедельникам
- **[Marriagetoxin](anime/marriagetoxin/index.md)** — Брачный токсин · 1 сезон · по вторникам
- **[Hidarikiki no Eren](anime/hidarikiki-no-eren/index.md)** — Левша Эрен · 1 сезон · по вторникам
- **[Nigashita Sakana wa Ookikatta ga, Tsuriageta Sakana ga Ookisugita Ken](anime/nigashita-sakana/index.md)** — Рыба, которую я упустила, большая, но я поймала другую, которая ещё больше · 1 сезон · по средам
- **[Otaku ni Yasashii Gal wa Inai!?](anime/otaku-yasashii-gal/index.md)** — Где те девушки, что были бы добры к отаку? · 1 сезон · по средам
<!-- /AUTO-ANIME-LIST -->

## 🆕 Последние обновления

<!-- AUTO-RECENT -->
- 🆕 **Yomi no Tsugai** — [Серия 12](anime/yomi-no-tsugai/ep-12.md)
- 🆕 **Akane-banashi** — [Серия 12](anime/akane-banashi/ep-12.md)
- 🆕 **Kill Ao** — [Серия 8](anime/kill-ao/ep-08.md)
- 🆕 **Mao** — [Серия 12](anime/mao/ep-12.md)
- 🆕 **Snowball Earth** — [Серия 12](anime/snowball-earth/ep-12.md)
<!-- /AUTO-RECENT -->

---

## 🔍 Как это работает

1. В день выхода серии **GitHub Actions** запускает генератор по расписанию.
2. Скрипт спрашивает **Gemini** (с веб-поиском): что сейчас активно обсуждают
   по вышедшей серии — реакции, теории, повороты сюжета, мемы, споры.
3. Ответ структурируется в страницу и коммитится в репозиторий.
4. Сайт пересобирается и публикуется на GitHub Pages — ты видишь свежие темы.

Подробнее — на странице [О проекте](about.md).
