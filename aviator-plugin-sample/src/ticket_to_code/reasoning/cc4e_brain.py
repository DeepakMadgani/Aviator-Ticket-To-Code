"""
CC4E Brain — persistent, project-specific engineering memory.

This is NOT RAG. It is a durable knowledge store scoped to a single product
(CC4E) that COMPOUNDS knowledge across every ticket the reasoning engine solves.

What it remembers:
  - Feature/owner map        (which files own which behavior)
  - Ticket history           (type, files touched, outcome, confidence, lessons)
  - Per-file success counts  (which files are reliably the right answer)
  - Ticket-type patterns     (typical files / validation for each ticket type)

Every solved ticket updates the Brain, so the reasoner starts each new ticket
already knowing what worked before instead of rediscovering CC4E every time.

Storage: a single JSON document. Default location prefers the CC4E brain dir,
falling back to <workspace>/brain, then <workspace>.

Author: Deepak Madgani
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_STOPWORDS = {
    "the", "and", "for", "with", "that", "this", "from", "into", "when", "where",
    "should", "must", "need", "needs", "please", "update", "change", "fix", "add",
    "remove", "make", "ticket", "issue", "bug", "feature", "cc4e", "application",
    "value", "have", "will", "your", "user", "users", "page", "system",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _keywords(text: str, limit: int = 12) -> List[str]:
    """Extract meaningful lowercase keywords from ticket text."""
    tokens = re.findall(r"[A-Za-z0-9_]{3,}", text or "")
    seen: set[str] = set()
    out: List[str] = []
    for t in tokens:
        tl = t.lower()
        if tl in _STOPWORDS or tl in seen:
            continue
        seen.add(tl)
        out.append(tl)
        if len(out) >= limit:
            break
    return out


class CC4EBrain:
    """Durable, compounding project memory for CC4E."""

    def __init__(self, workspace_path: str):
        self.workspace_path = workspace_path
        self.path = self._resolve_store_path(workspace_path)
        self.data: Dict[str, Any] = {
            "project": "CC4E",
            "owners": {},          # feature_keyword -> {file: weight}
            "tickets": [],         # list of solved-ticket records
            "file_success": {},    # normalized_path -> count
            "patterns": {},        # ticket_type -> {typical_files, validation, count}
            "updated_at": None,
        }
        self._load()

    # ── storage ────────────────────────────────────────────────────────────

    @staticmethod
    def _resolve_store_path(workspace_path: str) -> Path:
        candidates = [
            Path(r"C:\CC4E\brain\cc4e_agent_memory.json"),
            Path(workspace_path) / "brain" / "cc4e_agent_memory.json",
            Path(workspace_path) / "cc4e_agent_memory.json",
        ]
        for c in candidates:
            if c.parent.exists():
                return c
        return candidates[-1]

    def _load(self) -> None:
        try:
            if self.path.exists():
                with open(self.path, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                if isinstance(loaded, dict):
                    self.data.update(loaded)
                    logger.info(
                        "CC4E Brain loaded: %d tickets, %d owner keys",
                        len(self.data.get("tickets", [])),
                        len(self.data.get("owners", {})),
                    )
        except Exception as exc:
            logger.warning("CC4E Brain load failed (%s) — starting fresh", exc)

    def _save(self) -> None:
        self.data["updated_at"] = _now()
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(self.data, f, indent=2, default=str)
        except Exception as exc:
            logger.warning("CC4E Brain save failed: %s", exc)

    # ── recall ─────────────────────────────────────────────────────────────

    def recall_similar_tickets(self, ticket_text: str, ticket_type: str = "", limit: int = 5) -> List[dict]:
        """Return past solved tickets most similar to this one (keyword + type overlap)."""
        kws = set(_keywords(ticket_text))
        scored: List[tuple[float, dict]] = []
        for rec in self.data.get("tickets", []):
            rec_kws = set(rec.get("keywords", []))
            overlap = len(kws & rec_kws)
            type_bonus = 1.5 if ticket_type and rec.get("type") == ticket_type else 0.0
            outcome_bonus = 0.5 if rec.get("outcome") == "verified" else 0.0
            score = overlap + type_bonus + outcome_bonus
            if score > 0:
                scored.append((score, rec))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [rec for _, rec in scored[:limit]]

    def suggest_owner_files(self, ticket_text: str, ticket_type: str = "", limit: int = 8) -> List[dict]:
        """Suggest likely owner files based on learned feature→file associations."""
        kws = _keywords(ticket_text)
        scores: Dict[str, float] = {}
        owners = self.data.get("owners", {})
        for kw in kws:
            for file_path, weight in (owners.get(kw, {}) or {}).items():
                scores[file_path] = scores.get(file_path, 0.0) + float(weight)

        # Blend in ticket-type typical files.
        pattern = self.data.get("patterns", {}).get(ticket_type, {})
        for file_path in pattern.get("typical_files", []):
            scores[file_path] = scores.get(file_path, 0.0) + 0.75

        # Blend in global file success.
        for file_path, count in self.data.get("file_success", {}).items():
            if file_path in scores:
                scores[file_path] += min(1.0, 0.1 * int(count))

        ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:limit]
        return [{"file": fp, "confidence": round(min(1.0, s / 5.0), 3)} for fp, s in ranked]

    def type_profile(self, ticket_type: str) -> dict:
        """Return the learned profile for a ticket type (typical files/validation)."""
        return self.data.get("patterns", {}).get(ticket_type, {})

    def summary(self) -> dict:
        return {
            "tickets_solved": len(self.data.get("tickets", [])),
            "owner_keys": len(self.data.get("owners", {})),
            "known_ticket_types": list(self.data.get("patterns", {}).keys()),
            "store_path": str(self.path),
        }

    # ── learning ───────────────────────────────────────────────────────────

    def record_ticket(
        self,
        *,
        ticket_id: str,
        title: str,
        description: str,
        ticket_type: str,
        files_modified: List[str],
        outcome: str,
        confidence: float,
        lessons: Optional[List[str]] = None,
        validation: str = "",
    ) -> None:
        """Persist a solved ticket and update owner/pattern/success maps."""
        text = f"{title} {description}"
        kws = _keywords(text)
        norm_files = [f.replace("\\", "/").strip() for f in files_modified if f]

        record = {
            "ticket_id": ticket_id,
            "title": title,
            "type": ticket_type,
            "keywords": kws,
            "files_modified": norm_files,
            "outcome": outcome,
            "confidence": round(float(confidence), 3),
            "lessons": lessons or [],
            "validation": validation,
            "timestamp": _now(),
        }
        self.data.setdefault("tickets", []).append(record)
        if len(self.data["tickets"]) > 5000:
            self.data["tickets"] = self.data["tickets"][-5000:]

        # Only reinforce owner/pattern maps for verified successes.
        if outcome == "verified" and norm_files:
            owners = self.data.setdefault("owners", {})
            for kw in kws:
                bucket = owners.setdefault(kw, {})
                for fp in norm_files:
                    bucket[fp] = round(float(bucket.get(fp, 0.0)) + 1.0, 3)

            success = self.data.setdefault("file_success", {})
            for fp in norm_files:
                success[fp] = int(success.get(fp, 0)) + 1

            patterns = self.data.setdefault("patterns", {})
            prof = patterns.setdefault(ticket_type, {"typical_files": [], "validation": "", "count": 0})
            prof["count"] = int(prof.get("count", 0)) + 1
            # Keep a bounded, frequency-ordered typical-file list.
            tf = {f: 0 for f in prof.get("typical_files", [])}
            for fp in norm_files:
                tf[fp] = tf.get(fp, 0) + 1
            prof["typical_files"] = sorted(tf, key=lambda k: tf[k], reverse=True)[:12]
            if validation:
                prof["validation"] = validation

        self._save()
