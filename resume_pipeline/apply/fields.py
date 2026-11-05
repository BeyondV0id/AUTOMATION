"""Read a page's form controls in one DOM pass and classify them.

Every control is tagged with ``data-jaf-id`` so later Playwright calls address
it directly instead of re-resolving labels.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field as dc_field
from typing import Any

# Elements that must never be touched by an autofiller.
BLOCKED_TYPES = {"password", "hidden", "submit", "reset", "button", "image", "range", "color"}
SENSITIVE = re.compile(
    r"\b(ssn|social security|credit card|card number|cvv|passport number|bank account|routing number)\b",
    re.I,
)
SUBMIT_WORDS = re.compile(r"\b(submit|send application|apply now|finish application|complete application)\b", re.I)

EXTRACT_JS = r"""
() => {
  const out = [];
  const norm = (s) => (s || "").replace(/\s+/g, " ").trim();
  const visible = (el) => {
    const style = getComputedStyle(el);
    if (style.display === "none" || style.visibility === "hidden" || style.opacity === "0") return false;
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  };
  const labelFor = (el) => {
    if (el.id) {
      const l = document.querySelector('label[for="' + CSS.escape(el.id) + '"]');
      if (l && norm(l.innerText)) return norm(l.innerText);
    }
    const wrap = el.closest("label");
    if (wrap && norm(wrap.innerText)) return norm(wrap.innerText);
    const lb = el.getAttribute("aria-labelledby");
    if (lb) {
      const parts = lb.split(/\s+/).map((id) => document.getElementById(id)).filter(Boolean);
      if (parts.length) return norm(parts.map((n) => n.innerText).join(" "));
    }
    const fs = el.closest("fieldset");
    if (fs) {
      const lg = fs.querySelector("legend");
      if (lg && norm(lg.innerText)) return norm(lg.innerText);
    }
    return "";
  };
  const ownText = (el) => {
    // Nearest ancestor block with short text, used for div-based custom fields.
    let p = el.parentElement, hops = 0;
    while (p && hops < 4) {
      const clone = p.cloneNode(true);
      clone.querySelectorAll("input,select,textarea,button,option,script,style").forEach((n) => n.remove());
      const t = norm(clone.innerText);
      if (t && t.length <= 120) return t;
      p = p.parentElement; hops++;
    }
    return "";
  };
  const nodes = document.querySelectorAll(
    'input, select, textarea, [contenteditable="true"], [role="combobox"]:not(input):not(textarea)'
  );
  let id = 0;
  for (const el of nodes) {
    const tag = el.tagName.toLowerCase();
    const type = (el.getAttribute("type") || "").toLowerCase();
    if (tag === "input" && type === "hidden") continue;
    if (el.disabled) continue;
    if (el.hasAttribute("data-jaf-id")) continue;
    const isFile = tag === "input" && type === "file";
    if (!isFile && !visible(el)) continue;
    if (tag === "input" && type === "password") continue;
    const label = labelFor(el);
    const aria = norm(el.getAttribute("aria-label"));
    const placeholder = norm(el.getAttribute("placeholder"));
    const groupFor = (el) => {
      const fs = el.closest("fieldset");
      if (fs) {
        const lg = fs.querySelector("legend");
        if (lg && norm(lg.innerText)) return norm(lg.innerText);
      }
      const rg = el.closest('[role="radiogroup"],[role="group"]');
      if (rg) {
        const aria = norm(rg.getAttribute("aria-label"));
        if (aria) return aria;
        const lb = rg.getAttribute("aria-labelledby");
        if (lb) {
          const el2 = document.getElementById(lb.split(/\s+/)[0]);
          if (el2) return norm(el2.innerText);
        }
      }
      return "";
    };
    let ancestor = "";
    if (tag !== "input" && tag !== "select" && tag !== "textarea") ancestor = ownText(el);
    else if (!label && type === "checkbox") ancestor = ownText(el);
    const group = groupFor(el);
    el.setAttribute("data-jaf-id", String(id));
    const options = [];
    if (tag === "select") {
      for (const o of el.options) options.push({ value: o.value, text: norm(o.textContent), disabled: o.disabled });
    }
    out.push({
      jaf: id,
      tag,
      type,
      name: norm(el.getAttribute("name")),
      el_id: norm(el.getAttribute("id")),
      label,
      aria,
      placeholder,
      ancestor,
      group,
      autocomplete: (el.getAttribute("autocomplete") || "").toLowerCase(),
      required: el.required || el.getAttribute("aria-required") === "true",
      readonly: !!el.readOnly,
      value: tag === "select" ? (el.value || "") : (el.value || ""),
      checked: !!el.checked,
      options,
      is_file: isFile,
      inputmode: (el.getAttribute("inputmode") || "").toLowerCase(),
      role: (el.getAttribute("role") || "").toLowerCase(),
    });
    id++;
  }
  return out;
}
"""


@dataclass
class Field:
    jaf: int
    frame: int
    tag: str
    type: str
    name: str = ""
    el_id: str = ""
    label: str = ""
    aria: str = ""
    placeholder: str = ""
    ancestor: str = ""
    group: str = ""
    autocomplete: str = ""
    required: bool = False
    readonly: bool = False
    value: str = ""
    checked: bool = False
    options: list[dict[str, Any]] = dc_field(default_factory=list)
    is_file: bool = False
    inputmode: str = ""
    role: str = ""

    @property
    def selector(self) -> str:
        return f'[data-jaf-id="{self.jaf}"]'

    @property
    def text(self) -> str:
        return " ".join(
            part
            for part in (
                self.group,
                self.label,
                self.aria,
                self.placeholder,
                self.ancestor,
                self.name,
                self.el_id,
            )
            if part
        )

    @property
    def text_norm(self) -> str:
        raw = self.text.lower()
        raw = re.sub(r"[_\-\[\]\.]+", " ", raw)
        return re.sub(r"\s+", " ", raw).strip()

    @property
    def kind(self) -> str:
        if self.is_file:
            return "file"
        if self.tag == "select":
            return "select"
        if self.tag == "textarea":
            return "textarea"
        if self.tag == "input" and self.type in {"radio", "checkbox"}:
            return self.type
        if self.role == "combobox" or self.tag not in {"input"}:
            return "widget"
        return "text"

    def describe(self) -> dict[str, Any]:
        return {
            "jaf": self.jaf,
            "frame": self.frame,
            "kind": self.kind,
            "type": self.type,
            "label": self.label or self.aria or self.placeholder or self.name or self.el_id,
            "name": self.name,
            "required": self.required,
        }


def scan(page) -> list[Field]:
    """One DOM pass per frame; returns flat, ordered field list."""
    fields: list[Field] = []
    for frame_index, frame in enumerate(page.frames):
        try:
            raw = frame.evaluate(EXTRACT_JS)
        except Exception:
            continue
        for item in raw or []:
            fields.append(
                Field(
                    jaf=int(item.get("jaf", 0)),
                    frame=frame_index,
                    tag=item.get("tag", ""),
                    type=item.get("type", ""),
                    name=item.get("name", ""),
                    el_id=item.get("el_id", ""),
                    label=item.get("label", ""),
                    aria=item.get("aria", ""),
                    placeholder=item.get("placeholder", ""),
                    ancestor=item.get("ancestor", ""),
                    group=item.get("group", ""),
                    autocomplete=item.get("autocomplete", ""),
                    required=bool(item.get("required")),
                    readonly=bool(item.get("readonly")),
                    value=item.get("value", "") or "",
                    checked=bool(item.get("checked")),
                    options=item.get("options") or [],
                    is_file=bool(item.get("is_file")),
                    inputmode=item.get("inputmode", ""),
                    role=item.get("role", ""),
                )
            )
    return fields


# ---------------------------------------------------------------- classification

AUTOCOMPLETE_MAP = {
    "given-name": "first_name",
    "family-name": "last_name",
    "name": "full_name",
    "email": "email",
    "tel": "phone",
    "tel-national": "phone",
    "url": "portfolio",
    "address-level2": "city",
    "address-level1": "region",
    "postal-code": "postal_code",
    "country": "country",
    "country-name": "country",
    "street-address": "address",
    "organization": "current_company",
    "organization-title": "current_title",
}

# (answer key, positive pattern, negative pattern)
RULES: list[tuple[str, str, str]] = [
    ("phone_country_code", r"\b(phone\s*country|country\s*code|dial(l)?ing\s*code)\b", ""),
    ("preferred_name", r"\b(preferred|nick)\s*(first\s*)?name\b", ""),
    ("first_name", r"\b(first|given|forename)\s*name\b|\bfname\b", r"last|family|surname|middle"),
    ("middle_name", r"\bmiddle\s*name\b|\bmname\b", ""),
    ("last_name", r"\b(last|family|sur)\s*name\b|\blname\b", r"first|given|middle"),
    ("email", r"\b(e-?mail)(\s*address)?\b", ""),
    ("phone", r"\b(phone|mobile|telephone|contact\s*number|cell)\b", r"country\s*code"),
    ("linkedin", r"\blinked\s*in\b", ""),
    ("github", r"\bgit\s*hub\b", ""),
    ("portfolio", r"\b(portfolio|personal\s*(web)?site|website|blog|homepage)\b", r"company|employer"),
    ("resume", r"\b(resume|cv|curriculum\s*vitae)\b", r"cover"),
    ("cover_letter", r"\bcover\s*letter\b", ""),
    ("country", r"\bcountry\b", r"code"),
    ("postal_code", r"\b(zip|postal)\s*(code)?\b", ""),
    ("city", r"\b(city|town)\b", r"state"),
    ("region", r"\b(state|province|region)\b", ""),
    ("address", r"\b(street|address\s*line|full\s*address|mailing\s*address)\b", r"e-?mail"),
    ("location", r"\b(location|where\s+are\s+you\s+(based|located)|currently\s+based|based\s+in)\b", r"company"),
    ("salary_expectation", r"\b(salary|compensation|expected\s*(pay|ctc)|ctc|pay\s*expectation|desired\s*pay)\b", r"currency"),
    ("notice_period", r"\b(notice\s*period|availability|available\s*to\s*start|when\s+can\s+you\s+start|start\s*date)\b", ""),
    ("years_experience", r"\b(years?|yrs)\s*(of\s*)?(relevant\s*)?experience\b|\bexperience\s*(in\s*)?years\b", ""),
    ("work_authorization", r"\b(work\s*(authori[sz]ation|permit)|right\s*to\s*work|legally\s*(authori[sz]ed|eligible|permitted)|eligible\s*to\s*work|authori[sz]ed\s*to\s*work)\b", r"sponsor|visa"),
    ("requires_sponsorship", r"\b(sponsor(ship)?|visa)\b", r"status|type|number|expiry"),
    ("current_company", r"\b(current|present|latest)\s*(company|employer|organization)\b", ""),
    ("current_title", r"\b(current|present|latest)\s*(job\s*)?title\b|\bcurrent\s*role\b", ""),
    ("education_level", r"\b(highest\s*(level\s*of\s*)?education|education\s*level|degree\s*level|qualification)\b", ""),
    ("university", r"\b(university|college|school|institution)\b", r"company"),
    ("degree", r"\b(degree|major|field\s*of\s*study|discipline)\b", ""),
    ("pronouns", r"\bpronouns?\b", ""),
    ("gender", r"\bgender\b", ""),
    ("transgender", r"\btransgender\b", ""),
    ("sexual_orientation", r"\bsexual\s*orientation\b", ""),
    ("ethnicity", r"\b(ethnicity|race)\b", ""),
    ("veteran_status", r"\bveteran\b", ""),
    ("disability_status", r"\bdisabilit(y|ies)\b", ""),
    ("referral_source", r"\b(how\s+did\s+you\s+(initially\s+|first\s+)?(hear|find)|referral|referred\s*by|source)\b", ""),
    ("consent", r"\b(i\s*agree|consent|privacy\s*(policy|notice)|terms\s*(and|&)\s*conditions)\b", r"newsletter|marketing|sms|text\s*message"),
    ("newsletter", r"\b(newsletter|marketing|job\s*alerts|keep\s*me\s*(posted|informed)|sms|text\s*message)\b", ""),
    ("full_name", r"\b(full|legal|candidate|your)\s*name\b", r"company|employer|school|university|reference|emergency|user"),
]

_COMPILED = [(key, re.compile(pos, re.I), re.compile(neg, re.I) if neg else None) for key, pos, neg in RULES]

GENERIC_NAME = re.compile(r"^(full\s*)?name$", re.I)


def classify(field: Field, answers=None) -> tuple[str | None, float, str]:
    """Return (answer_key, confidence, reason). Unknown fields return None."""
    t = field.text_norm
    if not t:
        return None, 0.0, "no label"

    if SENSITIVE.search(t):
        return None, 0.0, "sensitive field"
    if field.type in {"password"}:
        return None, 0.0, "password field"

    ac = (field.autocomplete or "").strip()
    if ac in AUTOCOMPLETE_MAP:
        return AUTOCOMPLETE_MAP[ac], 0.95, "autocomplete"

    # Custom question overrides are checked before fuzzy rules.
    if answers is not None:
        custom = answers.custom_for(field.label or field.aria or field.placeholder)
        if custom is not None:
            return "custom", 0.8, "config question"

    for key, pos, neg in _COMPILED:
        if neg and neg.search(t):
            continue
        if pos.search(t):
            return key, 0.85, "label match"

    if GENERIC_NAME.match(t.replace(" ", "")) or t in {"name", "your name", "full name"}:
        return "full_name", 0.6, "generic name"

    if answers is not None:
        custom = answers.custom_for(field.label or field.aria or field.placeholder)
        if custom is not None:
            return "custom", 0.8, "config question"

    return None, 0.0, "no rule"
