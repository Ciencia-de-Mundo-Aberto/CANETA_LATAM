"""Authors and affiliations: collected from the original document or ORCID, filled into every export.

Each author is {name, affiliation, email, orcid, corresponding}; several affiliations go in one field
separated by ";". Double-blind journals: with project.data["blind"] the manuscript leaves the authors
out (Word, PDF, LaTeX and the file properties) and they go to a separate title page instead.

Where they go:
  - Word: "Ana Silva¹,*" under the title, numbered affiliations and the corresponding e-mail below;
  - PDF / LaTeX: \\author{Ana Silva\\thanks{affiliation, e-mail, ORCID} \\and ...}."""

from __future__ import annotations

import re

from .project import Project

FIELDS = ("name", "affiliation", "email", "orcid", "corresponding")
BLANK = {"name": "", "affiliation": "", "email": "", "orcid": "", "corresponding": False}
ORCID = re.compile(r"\b(\d{4}-\d{4}-\d{4}-\d{3}[\dX])\b")
EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
CORRESPONDING = {"pt_BR": "Autor(a) correspondente", "es": "Autor(a) de correspondencia",
                 "en": "Corresponding author"}


# ------------------------------------------------------------------ the project's list
def normalize(raw) -> list[dict]:
    """Old projects keep plain names; every entry becomes a dict with all fields."""
    out = []
    for a in raw or []:
        a = {"name": a} if isinstance(a, str) else dict(a)
        entry = {k: (bool(a.get(k)) if k == "corresponding" else str(a.get(k) or "").strip()) for k in FIELDS}
        if entry["name"]:
            out.append(entry)
    return out


def get(project: Project) -> list[dict]:
    return normalize(project.data.get("authors"))


def blind(project: Project) -> bool:
    return bool(project.data.get("blind"))


def affiliations(authors: list[dict]) -> tuple[list[str], list[list[int]]]:
    """(unique affiliations in order of appearance, the 1-based numbers of each author's)."""
    unique: list[str] = []
    numbers = []
    for a in authors:
        mine = []
        for aff in (x.strip() for x in re.split(r"[;\n]", a["affiliation"])):
            if aff:
                if aff not in unique:
                    unique.append(aff)
                mine.append(unique.index(aff) + 1)
        numbers.append(mine)
    return unique, numbers


def merge(current: list[dict], found: list[dict]) -> list[dict]:
    """Fill what is missing without overwriting what the author typed (matched by name)."""
    out = [dict(a) for a in current]
    for f in found:
        same = next((a for a in out if _key(a["name"]) == _key(f["name"])), None)
        if same is None:
            out.append(f)
            continue
        for k in FIELDS:
            if not same.get(k) and f.get(k):
                same[k] = f[k]
    return out


def _key(name: str) -> str:
    return re.sub(r"\W+", " ", name).strip().lower()


# ------------------------------------------------------------------ export metadata
def _md_escape(text: str) -> str:
    return re.sub(r"([\\*_^~\[\]`<>#@])", r"\\\1", text)


def _tex_escape(text: str) -> str:
    text = text.replace("\\", r"\textbackslash{}")
    return re.sub(r"([&%$#_{}])", r"\\\1", text).replace("~", r"\textasciitilde{}").replace("^", r"\^{}")


def word_meta(project: Project) -> dict:
    """pandoc metadata for Word: names with superscript numbers; affiliations and the corresponding
    e-mail under them (the "date" line of pandoc's title block, one line each)."""
    authors = get(project)
    if blind(project) or not authors:
        return {}
    unique, numbers = affiliations(authors)
    names = []
    for a, nums in zip(authors, numbers, strict=True):
        marks = [str(n) for n in nums] if len(unique) > 1 or not unique else []
        if a["corresponding"]:
            marks.append("\\*")
        names.append(_md_escape(a["name"]) + (f"^{','.join(marks)}^" if marks else ""))
    lines = [(f"^{i}^ " if len(unique) > 1 else "") + _md_escape(aff) for i, aff in enumerate(unique, 1)]
    corr = [a for a in authors if a["corresponding"] and a["email"]]
    if corr:
        label = CORRESPONDING.get(project.lang, CORRESPONDING["pt_BR"])
        lines.append(f"\\* {label}: " + ", ".join(_md_escape(a["email"]) for a in corr))
    orcids = [f"{_md_escape(a['name'])}: https://orcid.org/{a['orcid']}" for a in authors if a["orcid"]]
    if orcids:
        lines.append("ORCID — " + "; ".join(orcids))
    meta = {"author": names}
    if lines:
        meta["date"] = "\\\n".join(lines)
    return meta


def latex_author(a: dict, lang: str = "pt_BR") -> str:
    """One author in LaTeX: name\\thanks{affiliation. e-mail. ORCID} (standard in every class)."""
    notes = [_tex_escape(x.strip()) for x in re.split(r"[;\n]", a["affiliation"]) if x.strip()]
    if a["email"]:
        label = CORRESPONDING.get(lang, CORRESPONDING["pt_BR"]) + ": " if a["corresponding"] else ""
        notes.append(label + f"\\texttt{{{_tex_escape(a['email'])}}}")
    if a["orcid"]:
        notes.append(f"ORCID: \\url{{https://orcid.org/{a['orcid']}}}")
    name = _tex_escape(a["name"])
    return name + ("\\thanks{" + ". ".join(notes) + "}" if notes else "")


def latex_meta(project: Project) -> dict:
    authors = get(project)
    if blind(project) or not authors:
        return {}
    return {"author": [f"`{latex_author(a, project.lang)}`{{=latex}}" for a in authors]}


def plain_meta(project: Project) -> dict:
    authors = get(project)
    return {} if blind(project) or not authors else {"author": [a["name"] for a in authors]}


def title_page_markdown(project: Project) -> str:
    """The separate identification page double-blind journals ask for."""
    authors = get(project)
    unique, numbers = affiliations(authors)
    lines = [f"# {project.title} {{.unnumbered}}", ""]
    for a, nums in zip(authors, numbers, strict=True):
        marks = [str(n) for n in nums] + (["\\*"] if a["corresponding"] else [])
        sup = f"^{','.join(marks)}^" if marks else ""
        extra = [f"<{a['email']}>" if a["email"] else "", f"ORCID <https://orcid.org/{a['orcid']}>" if a["orcid"] else ""]
        lines.append(f"**{_md_escape(a['name'])}**{sup}" + "".join(f" — {e}" for e in extra if e) + "\\")
    lines.append("")
    for i, aff in enumerate(unique, 1):
        lines.append(f"^{i}^ {_md_escape(aff)}\\")
    corr = [a for a in authors if a["corresponding"]]
    if corr:
        label = CORRESPONDING.get(project.lang, CORRESPONDING["pt_BR"])
        lines.append(f"\\* {label}: " + ", ".join(f"{a['name']} <{a['email']}>" if a["email"] else a["name"]
                                                 for a in corr))
    return "\n".join(lines).rstrip("\\") + "\n"


def leaks(project: Project) -> list[tuple[str, str]]:
    """Where the manuscript still identifies an author (double-blind): [(section title, what)]."""
    found = []
    needles = []
    for a in get(project):
        needles += [a["name"]] if len(a["name"].split()) > 1 else []
        needles += [x for x in (a["email"], a["orcid"]) if x]
    for s in project.sections:
        text = project.read_section(s.id).lower()
        for n in needles:
            if n.lower() in text:
                found.append((s.title, n))
    return found


# ------------------------------------------------------------------ collecting
NOTES = "textsuperscript|thanks|footnote|corref|fnref|orcid|orcidlink|email|ead"


def _drop(text: str, names: str) -> str:
    """text without its \\name[opt]{...} commands (balanced braces)."""
    for _, _, _, start, end in reversed(list(_commands(text, names, ends=True))):
        text = text[:start] + text[end:]
    return text


def _clean_tex(text: str) -> str:
    text = _drop(re.sub(r"%[^\n]*", "", text), NOTES)
    text = re.sub(r"\\(?:\\|newline|and|AND)\b|\\\\", " ", text)
    text = re.sub(r"\\[a-zA-Z]+\*?\s*(\[[^\]]*\])?", "", text)
    text = re.sub(r"\b(organization|addressline|city|postcode|state|country)\s*=\s*", "", text)
    text = text.replace("{", "").replace("}", "").replace("~", " ")
    return re.sub(r"\s+", " ", re.sub(r"\s*,\s*(,\s*)+", ", ", text)).strip(" ,;.")


def _commands(text: str, names: str, ends: bool = False):
    """Every \\name[opt]{arg} (balanced braces) in order: (name, opt, arg, start[, end])."""
    for m in re.finditer(rf"\\({names})(?![a-zA-Z])\*?\s*(?:\[([^\]]*)\])?\s*\{{", text):
        depth, i = 1, m.end()
        while i < len(text) and depth:
            depth += {"{": 1, "}": -1}.get(text[i], 0)
            i += 1
        found = (m.group(1), m.group(2) or "", text[m.end(): i - 1], m.start())
        yield found + (i,) if ends else found


def from_latex(tex: str) -> list[dict]:
    """Authors from a LaTeX front matter: plain \\author{A\\thanks{..} \\and B \\\\ Affiliation},
    authblk (\\author[1]{..} + \\affil[1]{..}), elsarticle (\\affiliation / \\address, \\ead, \\corref)
    and \\orcid / \\orcidlink / \\email."""
    head = re.split(r"\\section\*?\s*[\[{]|\\begin\{abstract\}", tex, maxsplit=1)[0]
    head = re.sub(r"(?m)^\s*%.*$", "", head)
    labelled = {}
    for _, opt, arg, _pos in _commands(head, "affil|affiliation|address|institute"):
        for lab in (opt or "").split(","):
            labelled.setdefault(lab.strip(), _clean_tex(arg))
    authors: list[dict] = []
    for cmd, opt, arg, _pos in _commands(head, "author|ead|orcid"):
        if cmd == "ead" and authors:
            m = EMAIL.search(arg)
            if m and not authors[-1]["email"]:
                authors[-1]["email"] = m.group(0)
            continue
        if cmd == "orcid" and authors:
            m = ORCID.search(arg)
            authors[-1]["orcid"] = authors[-1]["orcid"] or (m.group(1) if m else "")
            continue
        if cmd != "author":
            continue
        for piece in re.split(r"\\and\b|\\AND\b", arg):
            lines = [x for x in re.split(r"\\\\(?:\[[^\]]*\])?", piece) if x.strip()]
            if not lines:
                continue
            name = _clean_tex(lines[0])
            if not name:
                continue
            notes = [a for _, _, a, _ in _commands(piece, "thanks|footnote")]
            rest = lines[1:] + notes
            email = EMAIL.search(piece)
            orcid = ORCID.search(piece)
            affs = []
            for r in rest:
                c = _clean_tex(EMAIL.sub("", ORCID.sub("", r)))
                c = re.sub(r"(?i)\b(e-?mail|orcid|corresponding author|autor\(?a?\)? correspondente)\s*:?", "", c).strip(" ,;.:")
                if c and not re.fullmatch(r"(?i)https?\S*|orcid\.org\S*", c):
                    affs.append(c)
            for lab in (x.strip() for x in opt.split(",")):
                if lab in labelled:
                    affs.append(labelled[lab])
            if not affs and "" in labelled and len(labelled) == 1:
                affs.append(labelled[""])
            authors.append({"name": name, "affiliation": "; ".join(dict.fromkeys(affs)),
                            "email": email.group(0) if email else "", "orcid": orcid.group(1) if orcid else "",
                            "corresponding": bool(re.search(r"\\corref|correspond", piece + opt, re.I))})
    if authors and not any(a["affiliation"] for a in authors) and len(labelled) == 1:
        for a in authors:
            a["affiliation"] = next(iter(labelled.values()))
    return normalize(authors)


def from_project_source(project: Project) -> list[dict]:
    """Authors in the original LaTeX kept by the import (material/latex-source), or []."""
    from .export import _latex_source

    source = _latex_source(project)
    return from_latex(source) if source else []


# ------------------------------------------------------------------ ORCID
def valid_orcid(value: str) -> str | None:
    """The bare iD (checksum verified) from '0000-0002-1825-0097' or an orcid.org URL, else None."""
    m = ORCID.search(value or "")
    if not m:
        return None
    digits = m.group(1).replace("-", "")
    total = 0
    for d in digits[:-1]:
        total = (total + int(d)) * 2
    check = (12 - total % 11) % 11
    return m.group(1) if digits[-1] == ("X" if check == 10 else str(check)) else None


def from_orcid(orcid: str, client=None) -> dict:
    """Name, current affiliation and public e-mail from the ORCID public API (only the iD is sent)."""
    import httpx

    oid = valid_orcid(orcid)
    if not oid:
        raise ValueError("ORCID inválido")
    own = client is None
    client = client or httpx.Client(timeout=20, headers={"Accept": "application/json"})
    try:
        r = client.get(f"https://pub.orcid.org/v3.0/{oid}/record")
        r.raise_for_status()
        rec = r.json()
    finally:
        if own:
            client.close()
    person = rec.get("person") or {}
    nm = person.get("name") or {}
    credit = (nm.get("credit-name") or {}).get("value")
    name = credit or " ".join(x for x in ((nm.get("given-names") or {}).get("value"),
                                         (nm.get("family-name") or {}).get("value")) if x)
    emails = [e.get("email") for e in ((person.get("emails") or {}).get("email") or []) if e.get("email")]
    groups = (((rec.get("activities-summary") or {}).get("employments") or {}).get("affiliation-group") or [])
    affiliation = ""
    for g in groups:  # ORCID lists the most recent first; prefer one without an end date
        for s in g.get("summaries") or []:
            emp = s.get("employment-summary") or {}
            org = emp.get("organization") or {}
            parts = [emp.get("department-name"), org.get("name"),
                     (org.get("address") or {}).get("city"), (org.get("address") or {}).get("country")]
            text = ", ".join(p for p in parts if p)
            if text and (not affiliation or not emp.get("end-date")):
                affiliation = text
                if not emp.get("end-date"):
                    break
        if affiliation:
            break
    return {"name": name, "affiliation": affiliation, "email": emails[0] if emails else "", "orcid": oid,
            "corresponding": False}
