# Аватарки команды

Портреты для интерфейсов — квадратные **PNG 256×256**. Первые три получены из
выбранных владельцем профилей Linear и перекодированы из JPEG. Их промпты неизвестны.
Портрет Падме создан по этим референсам и уменьшен из оригинала 1254×1254.

| Файл | Агент | Референс |
| --- | --- | --- |
| `qui-gon.png` | Квай-Гон Джинн | Зелёный портрет координатора |
| `dexter.png` | Декстер Джеттстер | Синий портрет исследователя |
| `lando.png` | Лэндо Калриссиан | Тёплый портрет специалиста по продвижению |
| `padme.png` | Падме Амидала | Фиолетовый портрет редактора; промпт в `padme.md` |

Это образцы общей рисованной стилистики для следующих агентов. Правила генерации,
кропов и сохранения промптов описаны в [agent-profiles.md](../../docs/agent-profiles.md).
Полноразмерные оригиналы генераций хранить в `source/`, промпт и референсы —
в `<id>.md`. Оригиналы не входят в Docker-образ; интерфейсы используют только
нормализованные портреты в этой директории.

У всех четырёх портретов прозрачный внешний фон (alpha); цветной фон внутри
круга сохранён. Редактирование встроенным imagegen 1 ноября 2026 года,
`transparent_background=true`, по соответствующему PNG 256×256. Полные результаты
сохранены как `source/<id>-transparent.png`, опубликованные уменьшены до 256×256.

Промпт (для каждого `<id>`):

> Use case: background-extraction. Edit target: the attached existing <id> circular illustrated avatar. Remove ONLY the white outside background at the four corners outside the circular portrait silhouette, making it truly transparent with alpha. Preserve the entire colored circular city background, every detail of the character, face, hair, clothing, lightsaber, pose, colors and composition exactly. Keep any character silhouette extending slightly beyond the circular backdrop. Do not redraw, reinterpret, add objects or crop the subject. Square PNG, clean anti-aliased alpha edges with no white halo, no border or shadow. This is a precise transparency edit of an existing asset, not a new illustration.
