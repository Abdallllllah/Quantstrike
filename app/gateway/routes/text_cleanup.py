"""
Text post-processing shared by the chat answer path and the practice quiz
generator. Turns LLM-produced LaTeX-ish math into readable plain text the
WhatsApp-style chat UI renders cleanly:

  $P(x) = x^3 - 7x + 6$   →   P(x) = x³ - 7x + 6
  H_2O                     →   H₂O
  x^{n+1}                  →   xⁿ⁺¹
  \\pi r^2                  →   πr²
  3 \\times 10^{-3}         →   3 × 10⁻³
"""
import re

# --- Greek letters + math symbols ---
# Long backslashed LaTeX commands first so a longer prefix is matched before
# its shorter sibling (e.g. \alpha before \al).
_LATEX_COMMAND_REPLACEMENTS = [
    # Lowercase Greek
    (r"\\alpha\b", "α"), (r"\\beta\b", "β"), (r"\\gamma\b", "γ"),
    (r"\\delta\b", "δ"), (r"\\epsilon\b", "ε"), (r"\\varepsilon\b", "ε"),
    (r"\\zeta\b", "ζ"), (r"\\eta\b", "η"), (r"\\theta\b", "θ"),
    (r"\\vartheta\b", "ϑ"), (r"\\iota\b", "ι"), (r"\\kappa\b", "κ"),
    (r"\\lambda\b", "λ"), (r"\\mu\b", "μ"), (r"\\nu\b", "ν"),
    (r"\\xi\b", "ξ"), (r"\\pi\b", "π"), (r"\\rho\b", "ρ"),
    (r"\\sigma\b", "σ"), (r"\\tau\b", "τ"), (r"\\upsilon\b", "υ"),
    (r"\\phi\b", "φ"), (r"\\varphi\b", "φ"), (r"\\chi\b", "χ"),
    (r"\\psi\b", "ψ"), (r"\\omega\b", "ω"),
    # Uppercase Greek
    (r"\\Alpha\b", "Α"), (r"\\Beta\b", "Β"), (r"\\Gamma\b", "Γ"),
    (r"\\Delta\b", "Δ"), (r"\\Theta\b", "Θ"), (r"\\Lambda\b", "Λ"),
    (r"\\Xi\b", "Ξ"), (r"\\Pi\b", "Π"), (r"\\Sigma\b", "Σ"),
    (r"\\Phi\b", "Φ"), (r"\\Psi\b", "Ψ"), (r"\\Omega\b", "Ω"),
    # Operators
    (r"\\times\b", "×"), (r"\\div\b", "÷"), (r"\\pm\b", "±"),
    (r"\\mp\b", "∓"), (r"\\cdot\b", "·"), (r"\\ast\b", "∗"),
    (r"\\leq\b", "≤"), (r"\\geq\b", "≥"), (r"\\neq\b", "≠"),
    (r"\\approx\b", "≈"), (r"\\equiv\b", "≡"),
    (r"\\to\b", "→"), (r"\\rightarrow\b", "→"), (r"\\leftarrow\b", "←"),
    (r"\\Rightarrow\b", "⇒"), (r"\\Leftarrow\b", "⇐"),
    (r"\\infty\b", "∞"), (r"\\sum\b", "Σ"), (r"\\int\b", "∫"),
    (r"\\partial\b", "∂"), (r"\\nabla\b", "∇"), (r"\\degree\b", "°"),
    (r"\\circ\b", "°"), (r"\\prime\b", "′"),
    # Common spacing / formatting commands that should just be removed
    (r"\\,", " "), (r"\\;", " "), (r"\\!", ""), (r"\\:", " "),
    (r"\\\\", "\n"),  # LaTeX newline
    (r"\\text\{([^{}]*)\}", r"\1"),
    (r"\\mathbf\{([^{}]*)\}", r"\1"),
    (r"\\mathrm\{([^{}]*)\}", r"\1"),
    (r"\\boxed\{([^{}]*)\}", r"\1"),
    # \frac{a}{b} → (a)/(b) — best effort, only one level deep
    (r"\\frac\{([^{}]+)\}\{([^{}]+)\}", r"(\1)/(\2)"),
    # \sqrt{x} → √(x)
    (r"\\sqrt\{([^{}]+)\}", r"√(\1)"),
]


# --- Super/subscript Unicode maps ---
# Every char we know how to lift / drop. Anything outside the map causes the
# replacement to skip (leaving the original notation alone) so we never ship
# half-converted gibberish like "x^a²".
_SUPER_MAP = {
    '0': '⁰', '1': '¹', '2': '²', '3': '³', '4': '⁴',
    '5': '⁵', '6': '⁶', '7': '⁷', '8': '⁸', '9': '⁹',
    '+': '⁺', '-': '⁻', '=': '⁼', '(': '⁽', ')': '⁾',
    'a': 'ᵃ', 'b': 'ᵇ', 'c': 'ᶜ', 'd': 'ᵈ', 'e': 'ᵉ',
    'f': 'ᶠ', 'g': 'ᵍ', 'h': 'ʰ', 'i': 'ⁱ', 'j': 'ʲ',
    'k': 'ᵏ', 'l': 'ˡ', 'm': 'ᵐ', 'n': 'ⁿ', 'o': 'ᵒ',
    'p': 'ᵖ', 'r': 'ʳ', 's': 'ˢ', 't': 'ᵗ', 'u': 'ᵘ',
    'v': 'ᵛ', 'w': 'ʷ', 'x': 'ˣ', 'y': 'ʸ', 'z': 'ᶻ',
}
_SUB_MAP = {
    '0': '₀', '1': '₁', '2': '₂', '3': '₃', '4': '₄',
    '5': '₅', '6': '₆', '7': '₇', '8': '₈', '9': '₉',
    '+': '₊', '-': '₋', '=': '₌', '(': '₍', ')': '₎',
    'a': 'ₐ', 'e': 'ₑ', 'h': 'ₕ', 'i': 'ᵢ', 'j': 'ⱼ',
    'k': 'ₖ', 'l': 'ₗ', 'm': 'ₘ', 'n': 'ₙ', 'o': 'ₒ',
    'p': 'ₚ', 'r': 'ᵣ', 's': 'ₛ', 't': 'ₜ', 'u': 'ᵤ',
    'v': 'ᵥ', 'x': 'ₓ',
}


def _to_super(match: re.Match) -> str:
    inner = match.group(1)
    if all((c in _SUPER_MAP) or c.isspace() for c in inner):
        return ''.join(_SUPER_MAP.get(c, c) for c in inner)
    # Fall back to original notation so we don't ship partial conversions.
    return match.group(0)


def _to_sub(match: re.Match) -> str:
    inner = match.group(1)
    if all((c in _SUB_MAP) or c.isspace() for c in inner):
        return ''.join(_SUB_MAP.get(c, c) for c in inner)
    return match.group(0)


# Pre-compile regexes that run on every cleanup call.
_INLINE_MATH_DOUBLE_DOLLAR = re.compile(r"\$\$([^$\n]+?)\$\$")
_INLINE_MATH_SINGLE_DOLLAR = re.compile(r"\$([^$\n]+?)\$")
_DISPLAY_MATH_BRACKETS = re.compile(r"\\\[([^\]]*?)\\\]")
_INLINE_MATH_PARENS = re.compile(r"\\\(([^)]*?)\\\)")
_BRACED_SUPER = re.compile(r"\^\{([^{}]+?)\}")
_BRACED_SUB = re.compile(r"_\{([^{}]+?)\}")
_BARE_SUPER_DIGITS = re.compile(r"\^([0-9]+)")
_BARE_SUPER_LETTER = re.compile(r"\^([a-zA-Z])")
_BARE_SUB_DIGITS = re.compile(r"_([0-9]+)")
_BARE_SUB_LETTER = re.compile(r"_([a-zA-Z])")


def clean_math_notation(text: str) -> str:
    """Turn LaTeX-flavoured math text into readable plain Unicode."""
    if not text:
        return text

    # 1. Drop math delimiters but keep their contents.
    text = _INLINE_MATH_DOUBLE_DOLLAR.sub(r"\1", text)
    text = _INLINE_MATH_SINGLE_DOLLAR.sub(r"\1", text)
    text = _DISPLAY_MATH_BRACKETS.sub(r"\1", text)
    text = _INLINE_MATH_PARENS.sub(r"\1", text)

    # 2. Replace LaTeX commands (Greek letters, operators, \frac, \sqrt, …).
    for pattern, replacement in _LATEX_COMMAND_REPLACEMENTS:
        text = re.sub(pattern, replacement, text)

    # 3. Super/subscripts: braced first (greedier), then single-token.
    text = _BRACED_SUPER.sub(_to_super, text)
    text = _BRACED_SUB.sub(_to_sub, text)
    text = _BARE_SUPER_DIGITS.sub(_to_super, text)
    text = _BARE_SUPER_LETTER.sub(_to_super, text)
    text = _BARE_SUB_DIGITS.sub(_to_sub, text)
    text = _BARE_SUB_LETTER.sub(_to_sub, text)

    return text


def clean_question_strings(question: dict) -> dict:
    """Apply `clean_math_notation` to every text field in a quiz question
    dict. Mutates and returns the same dict for caller convenience."""
    for key in ("prompt", "correct_answer", "model_answer", "explanation"):
        v = question.get(key)
        if isinstance(v, str):
            question[key] = clean_math_notation(v)
    opts = question.get("options")
    if isinstance(opts, list):
        question["options"] = [clean_math_notation(o) if isinstance(o, str) else o for o in opts]
    variations = question.get("acceptable_variations")
    if isinstance(variations, list):
        question["acceptable_variations"] = [
            clean_math_notation(v) if isinstance(v, str) else v for v in variations
        ]
    points = question.get("marking_points")
    if isinstance(points, list):
        question["marking_points"] = [
            clean_math_notation(p) if isinstance(p, str) else p for p in points
        ]
    return question
