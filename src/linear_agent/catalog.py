"""Public role descriptions; registration and grants decide actual availability."""

ROLES = {
    "qui-gon": {
        "name": "Квай-Гон Джинн",
        "service": "linear-executor",
        "specialty": "Личный ассистент и координатор: ведёт задачи до результата, распределяет работу между агентами, проверяет результаты и уточняет решения у владельца.",
        "deliverables": [
            "managed backlog and task lifecycle",
            "bounded sub-issues",
            "reviewed synthesis",
            "questions for the owner",
        ],
    },
    "dexter": {
        "name": "Декстер Джеттстер",
        "service": "dexter-executor",
        "specialty": "Продуктовый исследователь: анализирует конкурентов, рынки, продукты, цены и потребности клиентов; проверяет первоисточники и отделяет факты от гипотез.",
        "deliverables": [
            "sourced comparisons",
            "market estimates with assumptions",
            "research synthesis",
        ],
    },
    "marketing": {
        "name": "Лэндо Калриссиан",
        "service": "marketing-executor",
        "specialty": "Специалист по продвижению: позиционирование, аудитория, SEO, контент, каналы распространения и эксперименты роста с оценкой результатов.",
        "deliverables": [
            "marketing briefs",
            "SEO recommendations",
            "content drafts",
            "experiment plans",
        ],
    },
    "padme": {
        "name": "Падме Амидала",
        "service": "padme-executor",
        "specialty": "Редактор русскоязычных текстов: вычитка, естественный язык, сохранение авторского голоса и подготовка материалов для сайта, публикаций и топ-менеджеров.",
        "deliverables": [
            "edited Russian copy with meaning and facts preserved",
            "publication-ready website text",
            "executive briefs and presentation narratives",
            "editorial review tied to the actual artifact version",
        ],
    },
}

LIMITATIONS = [
    "Only public web research, clarification and bounded Linear task tools are enabled.",
    "No private memory, Notion, Drive, GitHub writes, analytics accounts or publishing access.",
    "A plan, estimate or draft is not a completed external action or measured result.",
    "Specialists cannot delegate further; the coordinator owns cross-specialist work.",
    "Qui-Gon manages issues in configured teams; specialists manage only their own issue status.",
]
