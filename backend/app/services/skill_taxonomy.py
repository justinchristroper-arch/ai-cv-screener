"""The controlled vocabulary the screener can actually evaluate.

This file is the product's boundary, not an implementation detail. A skill that
is not here cannot be screened for, and the interface says so rather than
accepting a requirement it can never answer (ADR-0012). Making that boundary
visible is the point: the prototype this replaced returned "no evidence" for an
unknown skill, which is indistinguishable from the candidate not having it.

## How to add a skill

1. Add the canonical name as the key, with its family and its **surface forms**
   -- the spellings a CV or an advert really prints. Aliases are spellings of
   the same thing, never related technologies: conflating those is how a matcher
   starts crediting Docker for Kubernetes.
2. If the name is also an ordinary English or Indonesian word (`go`, `rust`,
   `excel`, `spark`), add it to `COMMON_WORD_TERMS`. If it is one or two
   characters, add it to `SHORT_AMBIGUOUS_TERMS`. Both lists make the matcher
   demand supporting context before believing the mention.
3. Add a test that the new skill matches a realistic line and does **not** match
   a plausible near miss.

The list is deliberately small. A large vocabulary looks more capable and is
harder to keep honest; every entry here is a term that appears on ordinary
technology job adverts.
"""

from __future__ import annotations

#: canonical name -> (family, surface forms)
#:
#: `family` groups related technologies for display and for skill-group presets.
#: It is deliberately NOT used to credit one skill for another.
SKILLS: dict[str, tuple[str, tuple[str, ...]]] = {
    # languages
    "Python": ("language", ("python", "python3")),
    "Java": ("language", ("java",)),
    "JavaScript": ("language", ("javascript", "ecmascript")),
    "TypeScript": ("language", ("typescript",)),
    "Go": ("language", ("golang", "go")),
    "C": ("language", ("c",)),
    "C++": ("language", ("c++", "cpp")),
    "C#": ("language", ("c#", "csharp")),
    "Ruby": ("language", ("ruby",)),
    "PHP": ("language", ("php",)),
    "Rust": ("language", ("rust",)),
    "Scala": ("language", ("scala",)),
    "R": ("language", ("r",)),
    "SQL": ("language", ("sql",)),
    "Bash": ("language", ("bash", "shell scripting")),
    # web frameworks
    "FastAPI": ("web_framework", ("fastapi", "fast api")),
    "Django": ("web_framework", ("django",)),
    "Flask": ("web_framework", ("flask",)),
    "Spring": ("web_framework", ("spring boot", "springboot", "spring")),
    "Express": ("web_framework", ("express.js", "expressjs", "express")),
    "React": ("web_framework", ("react.js", "reactjs", "react")),
    "Node.js": ("web_framework", ("node.js", "nodejs", "node js")),
    # datastores
    "PostgreSQL": ("datastore", ("postgresql", "postgres", "psql")),
    "MySQL": ("datastore", ("mysql", "mariadb")),
    "MongoDB": ("datastore", ("mongodb", "mongo")),
    "Redis": ("datastore", ("redis",)),
    "Elasticsearch": ("datastore", ("elasticsearch", "elastic search")),
    "SQLite": ("datastore", ("sqlite",)),
    # containers and infrastructure
    "Docker": ("container", ("docker",)),
    "Kubernetes": ("container", ("kubernetes", "k8s")),
    "Terraform": ("container", ("terraform",)),
    # cloud
    "AWS": ("cloud", ("aws", "amazon web services")),
    "GCP": ("cloud", ("gcp", "google cloud platform", "google cloud")),
    "Azure": ("cloud", ("microsoft azure", "azure")),
    # data and machine learning
    "Machine Learning": ("ml", ("machine learning", "pembelajaran mesin")),
    "Deep Learning": ("ml", ("deep learning",)),
    "TensorFlow": ("ml", ("tensorflow",)),
    "PyTorch": ("ml", ("pytorch",)),
    "scikit-learn": ("ml", ("scikit-learn", "scikit learn", "sklearn")),
    "Pandas": ("ml", ("pandas",)),
    "Spark": ("ml", ("apache spark", "pyspark", "spark")),
    "Airflow": ("ml", ("apache airflow", "airflow")),
    "Kafka": ("ml", ("apache kafka", "kafka")),
    "NLP": ("ml", ("natural language processing", "nlp")),
    # testing
    "pytest": ("testing", ("pytest",)),
    "JUnit": ("testing", ("junit",)),
    "Selenium": ("testing", ("selenium",)),
    # analytics
    "Excel": ("analytics", ("microsoft excel", "ms excel", "excel")),
    "Tableau": ("analytics", ("tableau",)),
    "Power BI": ("analytics", ("power bi", "powerbi")),
    # ways of working
    "Git": ("tooling", ("github", "gitlab", "git")),
    "CI/CD": ("tooling", ("ci/cd", "cicd", "continuous integration")),
    "REST": ("concept", ("rest apis", "rest api", "restful", "rest")),
    "GraphQL": ("concept", ("graphql",)),
    "Microservices": ("concept", ("microservices", "microservice")),
    "OCR": ("concept", ("optical character recognition", "ocr")),
    # accounting and finance
    #
    # Added because everything above is software engineering, which made the
    # screener useless for the roles it was actually being pointed at: an
    # accounting CV came back with no supported skills at all, so there was
    # nothing to screen on and every skill criterion a recruiter might want was
    # refused. Every term below appears on ordinary Indonesian accounting and
    # finance adverts, in the spelling those adverts and CVs use.
    "Accounting": ("accounting", ("accounting", "akuntansi", "akunting")),
    "Bookkeeping": ("accounting", ("bookkeeping", "book keeping", "pembukuan")),
    "Financial Reporting": (
        "accounting",
        ("financial reporting", "financial statements", "laporan keuangan", "pelaporan keuangan"),
    ),
    "General Ledger": ("accounting", ("general ledger", "buku besar")),
    "Journal Entry": ("accounting", ("journal entry", "journal entries", "jurnal umum")),
    "Reconciliation": (
        "accounting",
        ("bank reconciliation", "rekonsiliasi bank", "reconciliation", "rekonsiliasi"),
    ),
    "Accounts Payable": (
        "accounting",
        ("accounts payable", "account payable", "hutang dagang", "utang dagang"),
    ),
    "Accounts Receivable": (
        "accounting",
        ("accounts receivable", "account receivable", "piutang"),
    ),
    "Taxation": ("tax", ("taxation", "perpajakan", "pajak", "tax")),
    "PPh": ("tax", ("pph 21", "pph21", "pph")),
    "PPN": ("tax", ("pajak pertambahan nilai", "ppn")),
    "Tax Invoice": ("tax", ("faktur pajak", "e-faktur", "efaktur")),
    "Auditing": ("audit", ("internal audit", "external audit", "auditing", "audit")),
    "Budgeting": ("finance", ("budgeting", "penganggaran", "anggaran")),
    "Cost Accounting": ("accounting", ("cost accounting", "akuntansi biaya")),
    "Financial Analysis": (
        "finance",
        ("financial analysis", "analisa keuangan", "analisis keuangan"),
    ),
    "Payroll": ("accounting", ("payroll", "penggajian")),
    "Cash Flow": ("finance", ("cash flow", "arus kas")),
    "Petty Cash": ("accounting", ("petty cash", "kas kecil")),
    "PSAK": ("accounting", ("psak",)),
    "IFRS": ("accounting", ("ifrs",)),
    "Invoicing": ("accounting", ("invoicing", "invoice", "penagihan")),
    "Stock Opname": ("accounting", ("stock opname", "stok opname")),
    # Accounting software. "Accurate" and "Zahir" are the two packages
    # Indonesian adverts name most often.
    "Accurate": (
        "accounting_software",
        ("accurate accounting", "accurate online", "accurate"),
    ),
    "Zahir": ("accounting_software", ("zahir accounting", "zahir")),
    "MYOB": ("accounting_software", ("myob",)),
    "SAP": ("accounting_software", ("sap",)),
    "QuickBooks": ("accounting_software", ("quickbooks", "quick books")),
    "Xero": ("accounting_software", ("xero",)),
}

#: One or two characters. Their presence inside prose proves nothing, so the
#: extractor only accepts them from a list-like section.
SHORT_AMBIGUOUS_TERMS = {"go", "r", "c", "ai", "it", "ml", "ts", "js", "py"}

#: Names that are also ordinary words. "Ability to excel under pressure" must
#: not become a spreadsheet requirement. Used when reading a *requirement*;
#: the CV side is covered by section and context rules.
COMMON_WORD_TERMS = {
    "excel",
    "go",
    "r",
    "c",
    "rust",
    "spark",
    "flask",
    "django",
    "express",
    "react",
    "spring",
    "scala",
    "ai",
    "it",
    # "Accurate" is an accounting package and an everyday adjective, and the
    # adjective is far commoner: "accurate financial reporting" must not credit
    # the software. "audit" and "sap" are milder cases of the same thing
    # ("audit trail", and "sap" as an ordinary noun), gated for the same reason.
    "accurate",
    "audit",
    "sap",
}

#: Presets the interface can offer as one click. They resolve to the same
#: `SKILL` rows a recruiter would add by hand -- a grouping, never a new
#: criterion type, and never a way to screen for something unsupported.
SKILL_GROUPS: dict[str, tuple[str, ...]] = {
    "AI / ML": (
        "Machine Learning",
        "Deep Learning",
        "TensorFlow",
        "PyTorch",
        "scikit-learn",
        "NLP",
    ),
    "Data engineering": ("SQL", "Spark", "Airflow", "Kafka", "Pandas"),
    "Backend web": ("Python", "FastAPI", "Django", "Flask", "PostgreSQL", "REST"),
    "Cloud & infrastructure": ("AWS", "GCP", "Azure", "Docker", "Kubernetes", "Terraform"),
    # Not "Accounting": a preset must not share a name with a skill, or the
    # interface offers "Accounting" twice meaning two different things and the
    # API refuses one of them. A test asserts no group name is also a skill.
    "Accounting core": (
        "Accounting",
        "Bookkeeping",
        "Financial Reporting",
        "General Ledger",
        "Reconciliation",
        "Excel",
    ),
    "Tax (Indonesia)": ("Taxation", "PPh", "PPN", "Tax Invoice"),
    "Accounting software": ("Accurate", "Zahir", "MYOB", "SAP", "QuickBooks", "Xero"),
}

#: Languages the screener can detect. Presence only -- never a level.
#: Add a language by adding its English name plus the forms a CV prints,
#: including the endonym, and a test for a plausible false friend.
LANGUAGES: dict[str, tuple[str, ...]] = {
    "English": ("english", "inggris", "bahasa inggris"),
    "Indonesian": ("indonesian", "bahasa indonesia", "indonesia"),
    "Mandarin": ("mandarin", "chinese", "putonghua"),
    "Japanese": ("japanese", "nihongo", "jepang"),
    "Korean": ("korean", "korea"),
    "Arabic": ("arabic", "arab"),
    "German": ("german", "deutsch", "jerman"),
    "French": ("french", "francais", "perancis"),
    "Spanish": ("spanish", "espanol", "spanyol"),
    "Dutch": ("dutch", "nederlands", "belanda"),
}

#: Credentials the screener can detect. Presence only -- never a grade, never a
#: date comparison, and never an inference that one certificate implies another.
#:
#: A credential is not a skill, and keeping the two lists apart matters. "CPA"
#: in a sentence about what a team needs is not a credential the candidate
#: holds, and a certification criterion answered from loose prose would be the
#: keyword matching this engine exists to avoid. `cv_facts.extract_certifications`
#: therefore requires the line to look like a credential claim, not merely to
#: contain the name.
#:
#: Weighted towards Indonesia because that is who uses this. Brevet A/B is the
#: tax credential named on most Indonesian accounting adverts, and USKP is its
#: consultant exam; the accountancy bodies are IAI (CA) and IAPI (CPA).
CERTIFICATIONS: dict[str, tuple[str, ...]] = {
    # tax and accounting, Indonesia
    # Indonesian CVs almost never write these separately: "Brevet A & B" is the
    # ordinary spelling, and one certificate covers both levels. Each combined
    # form therefore appears under both names, so a CV claiming "Brevet A & B"
    # answers a criterion for either.
    "Brevet A": ("brevet a & b", "brevet a dan b", "brevet a/b", "brevet ab", "brevet a"),
    "Brevet B": ("brevet a & b", "brevet a dan b", "brevet a/b", "brevet ab", "brevet b"),
    "Brevet C": ("brevet c",),
    "USKP": ("uskp", "ujian sertifikasi konsultan pajak"),
    "CA": ("chartered accountant", "ca (iai)"),
    "CPA": ("certified public accountant", "cpa"),
    "CPSAK": ("cpsak",),
    "CPMA": ("cpma",),
    # accountancy and finance, international
    "ACCA": ("acca",),
    "CMA": ("certified management accountant", "cma"),
    "CIA": ("certified internal auditor",),
    "CISA": ("cisa",),
    "CFA": ("cfa",),
    "CFP": ("certified financial planner", "cfp"),
    # general professional
    "BNSP": ("bnsp",),
    "PMP": ("project management professional", "pmp"),
    "Scrum Master": ("certified scrummaster", "professional scrum master", "scrum master"),
    # technology
    "AWS Certified": ("aws certified",),
    "Azure Certified": ("azure certified", "microsoft certified: azure"),
    "GCP Certified": ("google cloud certified", "gcp certified"),
    "CCNA": ("ccna",),
    "CKA": ("certified kubernetes administrator", "cka"),
    "TOEFL": ("toefl",),
    "IELTS": ("ielts",),
}


def alias_index() -> list[tuple[str, str]]:
    """Every surface form, longest first, paired with its canonical name.

    Longest-first matters: "node.js" must be tried before "node", or the
    shorter form claims the line and the specific one never fires.
    """
    pairs = [
        (alias, canonical) for canonical, (_family, aliases) in SKILLS.items() for alias in aliases
    ]
    pairs.sort(key=lambda pair: -len(pair[0]))
    return pairs


def family_of(canonical: str) -> str:
    entry = SKILLS.get(canonical)
    return entry[0] if entry else ""


def is_supported(name: str) -> bool:
    return name in SKILLS


def supported_skills() -> list[str]:
    return sorted(SKILLS)


def supported_languages() -> list[str]:
    return sorted(LANGUAGES)


def supported_certifications() -> list[str]:
    return sorted(CERTIFICATIONS)


def certification_aliases() -> list[tuple[str, str]]:
    """Every credential surface form, longest first, with its canonical name."""
    pairs = [
        (alias, canonical) for canonical, aliases in CERTIFICATIONS.items() for alias in aliases
    ]
    pairs.sort(key=lambda pair: -len(pair[0]))
    return pairs


__all__ = [
    "CERTIFICATIONS",
    "COMMON_WORD_TERMS",
    "LANGUAGES",
    "SHORT_AMBIGUOUS_TERMS",
    "SKILLS",
    "SKILL_GROUPS",
    "alias_index",
    "certification_aliases",
    "family_of",
    "is_supported",
    "supported_certifications",
    "supported_languages",
    "supported_skills",
]
