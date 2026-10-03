"""Typeset a persisted Markdown investigation report with XeLaTeX."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from datetime import date
from pathlib import Path
from urllib.parse import urlparse


class LatexUnavailable(RuntimeError):
    pass


class LatexRenderError(RuntimeError):
    pass


_SPECIAL = {"\\": r"\textbackslash{}", "&": r"\&", "%": r"\%", "$": r"\$",
            "#": r"\#", "_": r"\_", "{": r"\{", "}": r"\}",
            "~": r"\textasciitilde{}", "^": r"\textasciicircum{}"}
_MATH_COMMANDS = frozenset("""frac dfrac tfrac sqrt sum prod int iint iiint lim sup inf max min
    log ln exp sin cos tan cot sec csc sinh cosh tanh arcsin arccos arctan
    alpha beta gamma delta epsilon varepsilon zeta eta theta vartheta iota kappa
    lambda mu nu xi pi varpi rho varrho sigma varsigma tau upsilon phi varphi
    chi psi omega Gamma Delta Theta Lambda Xi Pi Sigma Upsilon Phi Psi Omega
    mathbb mathcal mathfrak mathrm mathbf mathit text operatorname left right
    big Big bigg Bigg middle cdot times pm mp div le leq ge geq neq approx
    sim simeq equiv propto in notin subset subseteq supset supseteq cup cap
    forall exists infty partial nabla to rightarrow leftarrow mapsto iff implies
    ldots cdots vdots ddots dots ell neg wedge vee land lor emptyset varnothing
    overline underline hat widehat bar vec dot ddot tilde widetilde binom
    substack overset underset underbrace overbrace perp parallel mid angle circ
    lceil rceil lfloor rfloor lbrace rbrace lvert rvert lVert rVert
    begin end""".split())
_MATH_ENVIRONMENTS = frozenset({"aligned", "align", "align*", "gathered", "matrix",
                                "pmatrix", "bmatrix", "cases", "array", "split"})


def _escape(value: str) -> str:
    return "".join(_SPECIAL.get(char, char) for char in value)


def _code_text(value: str, *, preserve_spaces: bool = False) -> str:
    # Escaped text stays inert; break opportunities keep long identifiers and JSON
    # within the page margins without dropping characters from recorded code.
    output = []
    run = 0
    for char in value:
        output.append(r"\ " if char == " " and preserve_spaces else _escape(char))
        run += 1
        if char in ' ,;:/_-=)}]' or run >= 12:
            output.append("\\allowbreak{}%\n")
            run = 0
    return "".join(output)


def _math(value: str, display: bool = False) -> str:
    """Allow common math syntax while keeping arbitrary TeX commands out of the compiler."""
    commands = re.findall(r"\\([A-Za-z]+)", value)
    environments = re.findall(r"\\(?:begin|end)\s*\{([^{}]+)\}", value)
    if (any(command not in _MATH_COMMANDS for command in commands)
            or any(env not in _MATH_ENVIRONMENTS for env in environments)
            or re.search(r"\\[^A-Za-z{}|,;! :.\\]", value)
            or value.count("{") != value.count("}")):
        return r"\texttt{" + _code_text(value) + "}"
    return (r"\[" + value + r"\]" if display else "$" + value + "$")


def _math_span(value: str, start: int):
    """Recognize paired math delimiters, including numeric math but excluding currency."""
    for opening, closing, display in (("$$", "$$", True), (r"\[", r"\]", True),
                                      (r"\(", r"\)", False), ("$", "$", False)):
        if not value.startswith(opening, start):
            continue
        end = start + len(opening)
        while True:
            end = value.find(closing, end)
            if end < 0:
                return None
            backslashes = len(value[:end]) - len(value[:end].rstrip("\\"))
            if backslashes % 2:
                end += len(closing)
                continue
            content = value[start + len(opening):end]
            finish = end + len(closing)
            if (not content.strip() or (not display and "\n" in content)
                    or (opening == "$" and ("$" in content or "`" in content or
                        content[0].isspace() or content[-1].isspace() or
                        (finish < len(value) and value[finish].isdigit())))):
                return None
            return content.strip(), display, finish
    return None


def _text_with_math(value: str) -> str:
    rendered = []
    start = 0
    while start < len(value):
        span = _math_span(value, start)
        if span:
            content, display, start = span
            rendered.append(_math(content, display))
        elif value.startswith(r"\$", start):
            rendered.append(r"\$")
            start += 2
        else:
            rendered.append(_escape(value[start]))
            start += 1
    return "".join(rendered)


def _inline_math_rule(state, silent: bool) -> bool:
    # Parse math before Markdown handles backslashes, emphasis, or underscores.
    span = _math_span(state.src, state.pos)
    if span is None:
        return False
    content, display, finish = span
    if not silent:
        token = state.push("math_display" if display else "math_inline", "", 0)
        token.content = content
    state.pos = finish
    return True


def _inline(children) -> str:
    output = []
    links = []
    for token in children or []:
        kind = token.type
        if kind == "text": output.append(_escape(token.content))
        elif kind in ("math_inline", "math_display"):
            output.append(_math(token.content, display=kind == "math_display"))
        elif kind == "strong_open": output.append(r"\textbf{")
        elif kind == "strong_close": output.append("}")
        elif kind == "em_open": output.append(r"\emph{")
        elif kind == "em_close": output.append("}")
        elif kind == "code_inline": output.append(r"\texttt{" + _code_text(token.content) + "}")
        elif kind == "softbreak": output.append("\n")
        elif kind == "hardbreak": output.append(r"\\" + "\n")
        elif kind == "link_open":
            url = token.attrGet("href") or ""
            parsed = urlparse(url)
            approved = parsed.scheme in ("http", "https")
            links.append(approved)
            if approved: output.append(r"\href{" + _escape(url) + "}{")
        elif kind == "link_close":
            if links.pop(): output.append("}")
        elif kind == "image": output.append(_escape(token.content))
    return "".join(output)


def markdown_to_latex(markdown: str) -> str:
    try:
        from markdown_it import MarkdownIt
    except ImportError as exc:
        raise LatexUnavailable("Install ResearchPilot with the 'api' extra to export LaTeX PDFs") from exc

    markdown = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", markdown)
    parser = MarkdownIt("commonmark").enable("table")
    parser.inline.ruler.before("escape", "report_math", _inline_math_rule)
    tokens = parser.parse(markdown)
    output = []
    lists = []
    for token in tokens:
        kind = token.type
        if kind == "heading_open":
            level = int(token.tag[1:])
            output.append({1: r"\section{", 2: r"\subsection{", 3: r"\subsubsection{"}.get(level, r"\paragraph{"))
        elif kind == "heading_close": output.append("}\n")
        elif kind == "inline": output.append(_inline(token.children))
        elif kind == "paragraph_close": output.append("\n\n")
        elif kind == "bullet_list_open": lists.append("itemize"); output.append("\n\\begin{itemize}\n")
        elif kind == "ordered_list_open": lists.append("enumerate"); output.append("\n\\begin{enumerate}\n")
        elif kind in ("bullet_list_close", "ordered_list_close"):
            output.append("\\end{" + lists.pop() + "}\n")
        elif kind == "list_item_open": output.append(r"\item ")
        elif kind in ("fence", "code_block"):
            output.append("\n{\\small\\ttfamily\\raggedright\n")
            for line in token.content.rstrip("\n").split("\n"):
                output.append(r"\noindent " + _code_text(line, preserve_spaces=True) + r"\par" + "\n")
            output.append("}\n")
        elif kind == "blockquote_open": output.append("\n\\begin{quote}\n")
        elif kind == "blockquote_close": output.append("\\end{quote}\n")
        elif kind == "hr": output.append("\n\\hrulefill\n")
        elif kind == "table_open": output.append("\n{\\small\n")
        elif kind == "table_close": output.append("}\n")
        elif kind == "tr_open": output.append("\n\\noindent ")
        elif kind in ("th_close", "td_close"): output.append(" \\quad ")
        elif kind == "tr_close": output.append("\\par\n")
    return "".join(output)


def latex_source(markdown: str, question: str) -> str:
    body = markdown_to_latex(markdown)
    title = _text_with_math(question[:300])
    return r"""\documentclass[11pt,a4paper]{article}
\usepackage[margin=27mm,headheight=15pt]{geometry}
\usepackage{fontspec,amsmath,amssymb,hyperref,xcolor,fancyhdr,enumitem}
\setmainfont{texgyrepagella-regular.otf}[BoldFont=texgyrepagella-bold.otf,ItalicFont=texgyrepagella-italic.otf,BoldItalicFont=texgyrepagella-bolditalic.otf]
\setsansfont{texgyreheros-regular.otf}[BoldFont=texgyreheros-bold.otf,ItalicFont=texgyreheros-italic.otf,BoldItalicFont=texgyreheros-bolditalic.otf]
\setmonofont{texgyrecursor-regular.otf}
\definecolor{reportblue}{HTML}{234C66}
\hypersetup{colorlinks=true,linkcolor=reportblue,urlcolor=reportblue,pdftitle={ResearchPilot Investigation Report}}
\pagestyle{fancy}\fancyhf{}
\fancyhead[L]{\small\sffamily ResearchPilot}
\fancyhead[R]{\small\sffamily Investigation report}
\fancyfoot[C]{\small\thepage}
\renewcommand{\headrulewidth}{0.3pt}
\setlength{\parskip}{0.55em}\setlength{\parindent}{0pt}
\setlist{itemsep=0.15em,parsep=0pt,topsep=0.35em,partopsep=0pt}
\emergencystretch=2em\sloppy
\setcounter{secnumdepth}{2}
\begin{document}
\begin{center}
{\sffamily\small\color{reportblue}\MakeUppercase{ResearchPilot}}\par
\vspace{0.6em}
{\LARGE\bfseries Investigation Report}\par
\vspace{0.5em}
{\large """ + title + r"""}\par
\vspace{0.6em}
{\small """ + _escape(date.today().strftime("%B %d, %Y")) + r"""}\par
\end{center}
\vspace{1em}\hrule\vspace{1.2em}
""" + body + "\n\\end{document}\n"


def render_latex_pdf(markdown: str, question: str) -> bytes:
    if not markdown.strip():
        raise ValueError("report is empty")
    if len(markdown) > 500_000:
        raise LatexRenderError("report is too large for PDF export")
    texlive = Path("C:/texlive/2024/bin/windows/xelatex.exe")
    engine = os.environ.get("RESEARCHPILOT_XELATEX") or (str(texlive) if texlive.is_file() else shutil.which("xelatex"))
    if engine is None:
        raise LatexUnavailable("XeLaTeX is required to download a typeset PDF report")
    source = latex_source(markdown, question)
    with tempfile.TemporaryDirectory(prefix="researchpilot-latex-") as temp:
        directory = Path(temp)
        (directory / "report.tex").write_text(source, encoding="utf-8")
        environment = {**os.environ, "openin_any": "p", "openout_any": "p"}
        try:
            result = subprocess.run(
                [engine, "-no-shell-escape", "-halt-on-error", "-interaction=nonstopmode",
                 "-output-directory", str(directory), "report.tex"],
                cwd=directory, env=environment, capture_output=True, text=True,
                encoding="utf-8", errors="replace",
                timeout=30, check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise LatexRenderError("PDF typesetting timed out") from exc
        pdf = directory / "report.pdf"
        if result.returncode or not pdf.is_file():
            raise LatexRenderError("Could not typeset the report; check its mathematical notation")
        return pdf.read_bytes()
