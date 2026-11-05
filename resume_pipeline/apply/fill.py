"""Execute a fill plan against scanned fields. Never clicks submit.

Text controls are filled in one batched DOM pass (native value setter + input /
change events, which React and Vue forms accept), then verified. Anything the
batch did not take is retried with a real Playwright action. That keeps a
20-field form around 30-80 ms instead of ~1 s of per-field round trips.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field as dc_field
from pathlib import Path
from typing import Any

from .answers import AnswerBook
from .fields import Field, SUBMIT_WORDS, classify


@dataclass
class FillResult:
    field: Field
    key: str
    status: str  # filled | selected | checked | uploaded | skipped | needs_human | error
    value: str = ""
    detail: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            **self.field.describe(),
            "key": self.key,
            "status": self.status,
            "value": self.value,
            "detail": self.detail,
        }


BULK_FILL_JS = r"""
(entries) => {
  const out = [];
  for (const e of entries) {
    const el = document.querySelector(e.sel);
    if (!el) { out.push({ sel: e.sel, ok: false, value: "", reason: "missing" }); continue; }
    const proto = el.tagName === "TEXTAREA" ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    const desc = Object.getOwnPropertyDescriptor(proto, "value");
    try {
      el.focus();
      if (desc && desc.set) desc.set.call(el, e.value); else el.value = e.value;
      el.dispatchEvent(new Event("input", { bubbles: true }));
      el.dispatchEvent(new Event("change", { bubbles: true }));
      el.dispatchEvent(new Event("blur", { bubbles: true }));
      out.push({ sel: e.sel, ok: el.value === e.value, value: el.value, reason: "" });
    } catch (err) {
      out.push({ sel: e.sel, ok: false, value: "", reason: String(err) });
    }
  }
  return out;
}
"""

VERIFY_JS = r"""
(sels) => sels.map((sel) => {
  const el = document.querySelector(sel);
  return el ? el.value : null;
})
"""


def _norm(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip().lower()


def _match_option(options: list[dict[str, Any]], answer: str) -> dict[str, Any] | None:
    wanted = _norm(answer)
    if not wanted:
        return None
    usable = [o for o in options if not o.get("disabled") and _norm(o.get("value")) not in {"", "select", "choose"}]
    if not usable:
        usable = [o for o in options if not o.get("disabled")]
    for opt in usable:
        if _norm(opt.get("text")) == wanted or _norm(opt.get("value")) == wanted:
            return opt
    for opt in usable:
        text = _norm(opt.get("text"))
        if text and (text.startswith(wanted) or wanted.startswith(text)):
            return opt
    for opt in usable:
        if wanted in _norm(opt.get("text")) or _norm(opt.get("text")) in wanted:
            return opt
    return None


def _locator(page, field: Field):
    return page.frames[field.frame].locator(field.selector)


def _fill_text(loc, value: str, number_only: bool = False) -> None:
    if number_only:
        value = re.sub(r"[^0-9.]", "", value) or value
    loc.fill(value, timeout=3000)


def _answer_text(book: AnswerBook, key: str, field: Field) -> str | None:
    if key == "custom":
        return book.custom_for(field.label or field.aria or field.placeholder)
    if not book.has(key):
        for alias in ALIASES.get(key, ()):
            if book.has(alias):
                return book.as_text(alias)
        return None
    return book.as_text(key)


ALIASES: dict[str, tuple[str, ...]] = {
    "preferred_name": ("first_name",),
}


def _plan(fields: list[Field], book: AnswerBook, resume_path: Path | None, cover_path: Path | None):
    """Return (fillable entries, immediate results for skipped fields)."""
    entries: list[tuple[Field, str, str]] = []
    results: list[FillResult] = []

    for field in fields:
        key, confidence, reason = classify(field, book)
        if key is None:
            if field.required:
                results.append(FillResult(field, "", "needs_human", detail="required, no rule matched"))
            continue

        if field.readonly:
            results.append(FillResult(field, key, "skipped", detail="readonly"))
            continue

        # Long paragraph questions are job-specific. Guessing is worse than asking.
        if key != "custom" and field.kind in {"text", "textarea"}:
            prompt = (field.label or field.aria or "").strip()
            if len(prompt) > 90:
                results.append(
                    FillResult(
                        field,
                        key,
                        "needs_human",
                        detail="long custom question; add it under [questions] with the exact wording",
                    )
                )
                continue

        if field.kind == "file":
            target = cover_path if key == "cover_letter" else resume_path
            if target is None or not Path(target).exists():
                results.append(FillResult(field, key, "skipped", detail="no file configured"))
                continue
            entries.append((field, key, str(target)))
            continue

        value = _answer_text(book, key, field)
        if value is None:
            if field.required:
                results.append(FillResult(field, key, "needs_human", detail="no answer configured"))
            continue
        entries.append((field, key, value))

    return entries, results


def fill_form(
    page,
    fields: list[Field],
    book: AnswerBook,
    resume_path: Path | None = None,
    cover_letter_path: Path | None = None,
    *,
    allow_widgets: bool = True,
) -> list[FillResult]:
    entries, results = _plan(fields, book, resume_path, cover_letter_path)

    # --- batched text pass
    text_entries = [
        (f, k, v)
        for f, k, v in entries
        if f.kind in {"text", "textarea"} and f.type not in {"file", "radio", "checkbox"}
    ]
    interactive = [(f, k, v) for f, k, v in entries if (f, k, v) not in text_entries]

    pending: list[tuple[Field, str, str]] = []
    for frame_index in sorted({f.frame for f, _, _ in text_entries}):
        frame = page.frames[frame_index]
        batch = [(f, k, v) for f, k, v in text_entries if f.frame == frame_index]
        payload = [
            {"sel": f.selector, "value": _clean(v, f)}
            for f, _, v in batch
        ]
        try:
            outcome = frame.evaluate(BULK_FILL_JS, payload)
        except Exception:
            pending.extend(batch)
            continue
        by_sel = {item.get("sel"): item for item in outcome or []}
        for field, key, value in batch:
            item = by_sel.get(field.selector)
            if item and item.get("ok"):
                results.append(FillResult(field, key, "filled", value=_clean(value, field), detail="text input"))
            else:
                pending.append((field, key, value))

    # Verify once more: some frameworks revert a value on the next tick.
    if pending:
        by_frame: dict[int, list[tuple[Field, str, str]]] = {}
        for item in pending:
            by_frame.setdefault(item[0].frame, []).append(item)
        retry: list[tuple[Field, str, str]] = []
        for frame_index, batch in by_frame.items():
            frame = page.frames[frame_index]
            try:
                values = frame.evaluate(VERIFY_JS, [f.selector for f, _, _ in batch])
            except Exception:
                values = [None] * len(batch)
            for (field, key, value), current in zip(batch, values):
                if current == _clean(value, field):
                    results.append(FillResult(field, key, "filled", value=_clean(value, field), detail="text input"))
                else:
                    retry.append((field, key, value))
        pending = retry

    # --- Playwright fallback for text that did not stick
    for field, key, value in pending:
        try:
            _fill_text(_locator(page, field), _clean(value, field), number_only=field.type == "number")
            results.append(FillResult(field, key, "filled", value=_clean(value, field), detail="typed"))
        except Exception as exc:  # noqa: BLE001
            results.append(FillResult(field, key, "error", value=value, detail=str(exc).splitlines()[0][:160]))

    # --- selects, radios, checkboxes, files, custom widgets
    for field, key, value in interactive:
        try:
            status, stored, detail = _apply(
                page, field, key, value, resume_path, cover_letter_path, allow_widgets
            )
        except Exception as exc:  # noqa: BLE001
            status, stored, detail = "error", value, str(exc).splitlines()[0][:160]
        results.append(FillResult(field, key, status, value=stored, detail=detail))

    return results


def _clean(value: str, field: Field) -> str:
    if field.type == "number":
        return re.sub(r"[^0-9.]", "", value) or value
    return value


def _pick_option(texts: list[str], wanted: str) -> int | None:
    """Choose the option that best matches without letting 'male' pick 'female'."""
    if not texts or not wanted:
        return None
    normed = [_norm(t) for t in texts]
    for i, text in enumerate(normed):
        if text == wanted:
            return i
    for i, text in enumerate(normed):
        if text.startswith(wanted):
            return i
    boundary = re.compile(rf"(?<![a-z0-9]){re.escape(wanted)}(?![a-z0-9])")
    hits = [i for i, text in enumerate(normed) if boundary.search(text)]
    if hits:
        return min(hits, key=lambda i: len(normed[i]))
    for i, text in enumerate(normed):
        if wanted in text:
            return i
    for i, text in enumerate(normed):
        if text and text in wanted:
            return i
    return None


def _apply(
    page,
    field: Field,
    key: str,
    value: str,
    resume_path: Path | None,
    cover_letter_path: Path | None,
    allow_widgets: bool,
) -> tuple[str, str, str]:
    loc = _locator(page, field)
    kind = field.kind

    if kind == "file":
        target = Path(value)
        loc.set_input_files(str(target), timeout=6000)
        return "uploaded", target.name, "file attached"

    if kind == "select":
        opt = _match_option(field.options, value)
        if opt is None:
            return "needs_human", value, "no matching option"
        loc.select_option(value=str(opt.get("value")), timeout=4000)
        return "selected", str(opt.get("text") or opt.get("value")), "option matched"

    if kind == "radio":
        label = _norm(field.label or field.aria or field.ancestor)
        wanted = _norm(value)
        if not (label == wanted or re.search(rf"\b{re.escape(wanted)}\b", label)):
            return "skipped", value, "radio not the matching option"
        loc.check(timeout=3000)
        return "checked", value, "radio selected"

    if kind == "checkbox":
        flag = _norm(value) in {"yes", "true", "1", "y"}
        if flag and not field.checked:
            loc.check(timeout=3000)
            return "checked", "yes", "checked"
        if not flag and field.checked:
            loc.uncheck(timeout=3000)
            return "checked", "no", "unchecked"
        return "skipped", value, "already in desired state"

    if kind == "widget":
        if not allow_widgets:
            return "needs_human", value, "custom widget; set manually"
        frame = page.frames[field.frame]
        wanted = _norm(value)
        try:
            loc.click(timeout=2500)
        except Exception:
            return "needs_human", value, "could not open dropdown"
        try:
            loc.fill(value, timeout=1500)
        except Exception:
            try:
                page.keyboard.type(value, delay=5)
            except Exception:
                pass

        options = frame.locator('[role="option"]:visible, [role="listbox"] li:visible')
        try:
            options.first.wait_for(state="visible", timeout=1200)
        except Exception:
            pass
        try:
            texts = [t.strip() for t in options.all_inner_texts()]
        except Exception:
            texts = []

        index = _pick_option(texts, wanted)
        if index is None:
            page.keyboard.press("Escape")
            return "needs_human", value, "no dropdown option matched"

        try:
            options.nth(index).click(timeout=3000)
            return "selected", texts[index][:80], "dropdown option"
        except Exception:
            page.keyboard.press("Escape")
            return "error", value, "option click failed"

    _fill_text(loc, value, number_only=field.type == "number")
    return "filled", value, "text input"


def find_unfilled_required(fields: list[Field], results: list[FillResult]) -> list[Field]:
    touched = {(r.field.frame, r.field.jaf) for r in results if r.status in {"filled", "selected", "checked", "uploaded"}}
    return [f for f in fields if f.required and (f.frame, f.jaf) not in touched]


def find_submit_buttons(page) -> list[str]:
    """Locate submit-like controls without clicking them."""
    found: list[str] = []
    for frame in page.frames:
        try:
            labels = frame.evaluate(
                """() => Array.from(document.querySelectorAll('button, input[type=submit], [role=button]'))
                    .map(el => (el.innerText || el.value || el.getAttribute('aria-label') || '').trim())
                    .filter(Boolean)"""
            )
        except Exception:
            continue
        for label in labels:
            if SUBMIT_WORDS.search(label):
                found.append(label)
    return sorted(set(found))
