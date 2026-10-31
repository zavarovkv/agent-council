"""Public role descriptions; registration and grants decide actual availability."""

ROLES = {
    "qui-gon": {
        "name": "Квай-Гон Джинн",
        "service": "linear-executor",
        "specialty": "Coordinate tasks, choose specialists, review evidence and synthesize decisions.",
        "deliverables": [
            "managed backlog and task lifecycle",
            "bounded sub-issues",
            "reviewed synthesis",
            "questions for the owner",
        ],
    },
    "dexter": {
        "name": "Dexter Jettster",
        "service": "dexter-executor",
        "specialty": "Product research: competitors, markets, pricing, customer evidence and discovery.",
        "deliverables": [
            "sourced comparisons",
            "market estimates with assumptions",
            "research synthesis",
        ],
    },
    "marketing": {
        "name": "Лэндо Калриссиан",
        "service": "marketing-executor",
        "specialty": "Project promotion: positioning, SEO, content, distribution and growth experiments.",
        "deliverables": [
            "marketing briefs",
            "SEO recommendations",
            "content drafts",
            "experiment plans",
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
