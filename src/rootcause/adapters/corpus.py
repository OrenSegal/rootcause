"""Postmortems on disk: one Markdown file each with YAML front matter, plus
`qrels.yaml`, which maps a scenario id to the postmortems relevant to it."""

from pathlib import Path

import yaml

from rootcause.domain.postmortems import Postmortem

POSTMORTEMS, QRELS = "postmortems", "qrels.yaml"


def load_corpus(root: Path) -> list[Postmortem]:
    docs = []
    for path in sorted((root / POSTMORTEMS).glob("*.md")):
        _, front, body = path.read_text().split("---\n", 2)
        docs.append(Postmortem(**yaml.safe_load(front), body=body.strip()))
    return docs


def load_qrels(root: Path) -> dict[str, list[str]]:
    return yaml.safe_load((root / QRELS).read_text())
