"""
resume_tailer.py — job-tailored resume PDF generation.

Design:
- A LaTeX resume template lives at `config.RESUME_TEX_PATH`.
- We replace two replaceable sections (Technical Skills + Projects) between
  markers, then compile to a job-specific PDF.
- The resulting PDF is cached in SQLite as `jobs.resume_pdf_path`.

Compilation requires a working LaTeX toolchain (typically `pdflatex`).
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional, Tuple

import anthropic

from config import config
from parser import Job

log = logging.getLogger(__name__)


_TECH_MARKER_START = "%% BEGIN_TECHNICAL_SKILLS"
_TECH_MARKER_END = "%% END_TECHNICAL_SKILLS"
_PROJ_MARKER_START = "%% BEGIN_PROJECTS"
_PROJ_MARKER_END = "%% END_PROJECTS"


@dataclass(frozen=True)
class TailoredBlocks:
    technical_skills_block: str
    projects_block: str


def _read_file(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _extract_block(tex: str, start_marker: str, end_marker: str) -> str:
    # Use non-greedy capture across newlines.
    pat = re.compile(
        re.escape(start_marker) + r"\s*([\s\S]*?)\s*" + re.escape(end_marker),
        flags=re.MULTILINE,
    )
    m = pat.search(tex)
    if not m:
        raise ValueError(f"Template markers not found: {start_marker} .. {end_marker}")
    return m.group(1).strip("\n")


def _replace_block(tex: str, start_marker: str, end_marker: str, new_block: str) -> str:
    pat = re.compile(
        re.escape(start_marker) + r"\s*([\s\S]*?)\s*" + re.escape(end_marker),
        flags=re.MULTILINE,
    )
    if not pat.search(tex):
        raise ValueError(f"Template markers not found for replacement: {start_marker} .. {end_marker}")
    replacement = f"{start_marker}\n{new_block.strip()}\n{end_marker}"
    # Use callable replacement so backslashes in LaTeX are treated literally.
    replaced = pat.sub(lambda _m: replacement, tex, count=1)
    return replaced


def _pdflatex_available() -> bool:
    return bool(shutil.which(config.RESUME_TEX_COMPILER))


def _compile_tex_to_pdf(tex_text: str, out_dir: str) -> str:
    os.makedirs(out_dir, exist_ok=True)
    tex_path = os.path.join(out_dir, "resume.tex")
    pdf_path = os.path.join(out_dir, "resume.pdf")

    with open(tex_path, "w", encoding="utf-8") as f:
        f.write(tex_text)

    if not _pdflatex_available():
        raise RuntimeError("pdflatex not available (set up TeX or install a TeX distribution).")

    cmd = config.RESUME_TEX_COMPILER
    passes = max(int(config.RESUME_TEX_PASSES), 1)

    # Compile in the output directory so any aux files stay contained.
    for i in range(passes):
        log.info(f"Compiling tailored resume PDF pass {i + 1}/{passes} for {out_dir}")
        subprocess.run(
            [
                cmd,
                "-interaction=nonstopmode",
                "-halt-on-error",
                "-output-directory",
                out_dir,
                "resume.tex",
            ],
            cwd=out_dir,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=True,
            text=True,
        )

    if not os.path.exists(pdf_path):
        # pdflatex normally produces resume.pdf, but fail gracefully.
        raise FileNotFoundError("Expected compiled PDF not found: resume.pdf")

    return pdf_path


def _fallback_blocks(job: Job, tech_block: str, proj_block: str) -> TailoredBlocks:
    # If Claude fails or API key is missing, we keep the template blocks as-is.
    return TailoredBlocks(
        technical_skills_block=tech_block,
        projects_block=proj_block,
    )


def _claude_tailor_blocks(job: Job, base_tech: str, base_projects: str) -> TailoredBlocks:
    if not config.ANTHROPIC_API_KEY:
        return _fallback_blocks(job, base_tech, base_projects)

    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)

    prompt = f"""You are tailoring a software engineer resume for a specific job.
Return ONLY valid JSON (no markdown) with these keys:
- "technical_skills_block": a LaTeX snippet that will replace the Technical Skills section contents
- "projects_block": a LaTeX snippet that will replace the Projects section contents

Rules:
- The returned snippets must be valid LaTeX.
- Do NOT include the marker lines (BEGIN/END markers).
- Keep the same overall structure: a Technical Skills itemize block and the full Projects section body
  with the same number of project headings (3 projects) and 3 bullet items per project (as in the template).
- Emphasize skills and keywords that match the job.
- Use concrete numbers/impact where reasonable, but do not invent wildly.

CANDIDATE PROFILE
Name: {config.YOUR_NAME}
Bio: {config.BIO}
Core Skills: {', '.join(config.SKILLS)}
Target Roles: {', '.join(config.TARGET_ROLES)}

JOB
Company: {job.company}
Title: {job.title}
Location: {job.location}
Remote: {"yes" if job.is_remote else "no"}
Source: {job.source}
Job Context (may be partial): {job.body[:2500] if job.body else ""}

TEMPLATE TECH SKILLS BLOCK:
{base_tech}

TEMPLATE PROJECTS BLOCK:
{base_projects}

JSON OUTPUT FORMAT EXAMPLE:
{{
  "technical_skills_block": "<latex>",
  "projects_block": "<latex>"
}}"""

    message = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=1400,
        messages=[{"role": "user", "content": prompt}],
    )

    raw = message.content[0].text.strip()
    raw = re.sub(r"^```[a-z]*\\n?|```$", "", raw, flags=re.MULTILINE).strip()
    data = json.loads(raw)

    return TailoredBlocks(
        technical_skills_block=str(data["technical_skills_block"]),
        projects_block=str(data["projects_block"]),
    )


def generate_tailored_resume_pdf(job: Job) -> str:
    """
    Returns a path to a tailored resume PDF.
    If tailoring/compilation fails, returns `config.RESUME_PATH` when possible.
    """
    if not config.TAILORED_RESUME_ENABLED:
        return config.RESUME_PATH

    template_path = config.RESUME_TEX_PATH
    if not os.path.exists(template_path):
        raise FileNotFoundError(f"Resume template not found at {template_path!r}")

    out_dir = os.path.join(config.TAILORED_RESUME_DIR, job.id)
    pdf_path = os.path.join(out_dir, "resume.pdf")
    if os.path.exists(pdf_path):
        return pdf_path

    base_tex = _read_file(template_path)
    base_tech = _extract_block(base_tex, _TECH_MARKER_START, _TECH_MARKER_END)
    base_projects = _extract_block(base_tex, _PROJ_MARKER_START, _PROJ_MARKER_END)

    try:
        blocks = _claude_tailor_blocks(job, base_tech=base_tech, base_projects=base_projects)
    except Exception as e:
        log.warning(f"Claude tailoring failed; using template blocks. Error: {e}")
        blocks = _fallback_blocks(job, tech_block=base_tech, proj_block=base_projects)

    # Merge blocks back into template.
    tailored_tex = base_tex
    tailored_tex = _replace_block(
        tailored_tex,
        start_marker=_TECH_MARKER_START,
        end_marker=_TECH_MARKER_END,
        new_block=blocks.technical_skills_block,
    )
    tailored_tex = _replace_block(
        tailored_tex,
        start_marker=_PROJ_MARKER_START,
        end_marker=_PROJ_MARKER_END,
        new_block=blocks.projects_block,
    )

    try:
        return _compile_tex_to_pdf(tailored_tex, out_dir=out_dir)
    except Exception as e:
        log.error(f"Resume compilation failed; falling back to base PDF. Error: {e}")
        return config.RESUME_PATH

