"""
GCE past-paper catalogue.

Past papers live in reg_documents alongside ordinary notes, and almost all of
them carry doc_type='notes' — the only reliable signal is the filename
("GCE_Physics_Paper1_June2020.pdf", "AL_2025_Biology_1_Text_Based.pdf", …).
This module turns a reg_documents row into a paper record (or None for notes)
and filters paper records by a free-text search.

Pure functions only — no database or network access — so it is easy to test.
"""
import re
from typing import Optional

PAPER_DOC_TYPES = {"past_paper", "marking_scheme"}

_CAMEL = re.compile(r"(?<=[a-z])(?=[A-Z])")
_ALPHA_NUM = re.compile(r"(?<=[A-Za-z])(?=\d)|(?<=\d)(?=[A-Za-z])")
_NON_WORD = re.compile(r"[^a-z0-9]+")
_YEAR = re.compile(r"^(?:19[89]\d|20[0-3]\d)$")

# Any one of these words marks a file as an exam paper.
_PAPER_WORDS = {"paper", "papers", "mcq", "gce", "mock", "exam"}
# These only count when the filename also has a year ("AL_2025_Biology_1…").
_LEVEL_WORDS = {"al", "alevel", "june"}
# Words that carry no meaning in a search or a title.
_NOISE = {"text", "based", "clean", "final", "fixed", "tables", "recreated",
          "reformatted", "pdf", "meetlearn", "cameroon"}
# What students type -> what filenames say.
_ALIASES = {"p1": ["paper", "1"], "p2": ["paper", "2"], "p3": ["paper", "3"],
            "maths": ["math"], "mathematics": ["math"], "further": ["further"]}


def _tokens(text: str) -> list:
    """'GCE_Physics_Paper1_June2020.pdf' -> ['gce','physics','paper','1','june','2020']"""
    stem = re.sub(r"\.pdf$", "", text or "", flags=re.IGNORECASE)
    stem = _ALPHA_NUM.sub(" ", _CAMEL.sub(" ", stem))
    return [t for t in _NON_WORD.split(stem.lower()) if t]


_YEAR_RANGE = re.compile(r"(?<!\d)((?:19|20)\d\d)\s*-\s*((?:19|20)\d\d)(?!\d)")


def _years(toks: list, filename: str = "") -> list:
    """Every year named; a written range ('2018-2023') covers the years between."""
    years = {int(t) for t in toks if _YEAR.match(t)}
    for a, b in _YEAR_RANGE.findall(filename):
        if 0 < int(b) - int(a) <= 30:
            years.update(range(int(a), int(b) + 1))
    return sorted(years)


def _paper_numbers(toks: list) -> list:
    nums = set()
    for i, t in enumerate(toks):
        nxt = toks[i + 1] if i + 1 < len(toks) else ""
        prev = toks[i - 1] if i else ""
        if t in ("paper", "papers") and nxt in ("1", "2", "3"):
            nums.add(int(nxt))
            # "Paper1_and_2"
            if toks[i + 2:i + 4] and toks[i + 2] == "and" and toks[i + 3] in ("1", "2", "3"):
                nums.add(int(toks[i + 3]))
        # "Chemistry_1_June_2023", "Biology_1_Text", "Stats_3_June_2025"
        elif (t in ("1", "2", "3") and prev not in ("paper", "papers", "and", "pages", "to")
              and (nxt in ("june", "text", "mcq") or _YEAR.match(nxt))):
            nums.add(int(t))
        elif t == "mcq":
            nums.add(1)
    return sorted(nums)


def classify(row: dict, subject_name: Optional[str] = None) -> Optional[dict]:
    """Return a paper record for a reg_documents row, or None if it is notes."""
    filename = row.get("filename") or ""
    toks = _tokens(filename)
    words = set(toks)
    years = _years(toks, filename)

    is_paper = (
        (row.get("doc_type") in PAPER_DOC_TYPES)
        or bool(words & _PAPER_WORDS)
        or (bool(years) and bool(words & _LEVEL_WORDS))
        or ("all" in words and "years" in words)
    )
    if not is_paper:
        return None

    title = " ".join(t for t in toks if t not in _NOISE)
    title = re.sub(r"\b(gce|mcq|al)\b", lambda m: m.group(1).upper(), title.title(), flags=re.IGNORECASE)
    return {
        "id": row.get("id"),
        "title": title or filename,
        "filename": filename,
        "subject": subject_name,
        "years": years,
        "papers": _paper_numbers(toks),
        "kind": ("marking_scheme" if row.get("doc_type") == "marking_scheme"
                 else "mock" if "mock" in words else "past_paper"),
        "_tokens": toks + _tokens(subject_name or ""),
    }


def _matches(paper: dict, query_tokens: list) -> bool:
    """Every query word must match. Numbers match exactly (a year, or a paper
    number); words match by prefix either way, so 'bio' finds Biology and
    'mathematics' finds 'Maths'."""
    hay = paper["_tokens"]
    for q in query_tokens:
        if _YEAR.match(q):
            if int(q) not in paper["years"]:
                return False
        elif q.isdigit():
            if int(q) not in paper["papers"] and q not in hay:
                return False
        elif not any(h.startswith(q) or (len(h) >= 3 and q.startswith(h)) for h in hay if not h.isdigit()):
            return False
    return True


def search(papers: list, q: str = "", subject: str = "", year: Optional[int] = None,
           paper: Optional[int] = None) -> list:
    """Filter paper records. Newest year first, then by title."""
    query_tokens = []
    for t in _tokens(q):
        query_tokens.extend(_ALIASES.get(t, [t]))
    query_tokens = [t for t in query_tokens if t not in ("paper", "papers", "past", "the", "for", "of", "and")]
    subject_tokens = [t for s in _tokens(subject) for t in _ALIASES.get(s, [s])]

    out = []
    for p in papers:
        if year is not None and year not in p["years"]:
            continue
        if paper is not None and paper not in p["papers"]:
            continue
        if subject_tokens and not _matches({**p, "_tokens": _tokens(p["subject"] or "") + p["_tokens"]}, subject_tokens):
            continue
        if not _matches(p, query_tokens):
            continue
        out.append(p)
    out.sort(key=lambda p: (-(max(p["years"]) if p["years"] else 0), p["title"]))
    return out


def public(paper: dict) -> dict:
    """Strip internal fields before returning a record to the client."""
    return {k: v for k, v in paper.items() if not k.startswith("_")}
