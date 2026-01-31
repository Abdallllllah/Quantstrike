"""
Response formatter for WhatsApp messages.

Handles:
- Character limits (WhatsApp max: 4096 chars)
- Markdown conversion (WhatsApp supports limited markdown)
- Message splitting for long responses
"""
import re


# WhatsApp message limits
MAX_MESSAGE_LENGTH = 4096
SAFE_MESSAGE_LENGTH = 4000  # Leave buffer for formatting


def format_for_whatsapp(text: str) -> str:
    """
    Format text for WhatsApp display.
    
    Converts standard markdown to WhatsApp-compatible formatting.
    
    WhatsApp supports:
    - *bold*
    - _italic_
    - ~strikethrough~
    - ```code```
    - > quote (on each line)
    """
    if not text:
        return ""
    
    # Convert headers to bold
    text = re.sub(r'^#{1,6}\s+(.+)$', r'*\1*', text, flags=re.MULTILINE)
    
    # Convert **bold** to *bold*
    text = re.sub(r'\*\*(.+?)\*\*', r'*\1*', text)
    
    # Convert numbered lists (keep as-is, WhatsApp handles them)
    # Already in correct format: 1. Item
    
    # Convert bullet points
    text = re.sub(r'^[-*]\s+', '• ', text, flags=re.MULTILINE)
    
    # Ensure code blocks use triple backticks
    text = re.sub(r'`{1,2}([^`]+)`{1,2}', r'```\1```', text)
    
    # Truncate if too long
    if len(text) > SAFE_MESSAGE_LENGTH:
        text = truncate_intelligently(text, SAFE_MESSAGE_LENGTH)
    
    return text.strip()


def truncate_intelligently(text: str, max_length: int) -> str:
    """
    Truncate text at a sensible point.
    
    Tries to cut at paragraph or sentence boundaries.
    """
    if len(text) <= max_length:
        return text
    
    # Find last paragraph break before limit
    truncated = text[:max_length]
    
    # Try to find a good break point
    break_points = [
        truncated.rfind('\n\n'),  # Paragraph
        truncated.rfind('\n'),    # Line
        truncated.rfind('. '),    # Sentence
        truncated.rfind('! '),    # Exclamation
        truncated.rfind('? '),    # Question
        truncated.rfind(' '),     # Word
    ]
    
    for bp in break_points:
        if bp > max_length * 0.7:  # Don't cut too much
            return text[:bp] + "\n\n_(Message truncated)_"
    
    return text[:max_length - 20] + "\n\n_(Message truncated)_"


def split_long_message(text: str, max_length: int = SAFE_MESSAGE_LENGTH) -> list[str]:
    """
    Split a long message into multiple parts.
    
    Useful for sending very long responses as multiple messages.
    Each part will be properly formatted.
    """
    if len(text) <= max_length:
        return [text]
    
    parts = []
    remaining = text
    part_num = 1
    
    while remaining:
        if len(remaining) <= max_length:
            parts.append(remaining)
            break
        
        # Find break point
        chunk = remaining[:max_length]
        
        break_points = [
            chunk.rfind('\n\n'),
            chunk.rfind('\n'),
            chunk.rfind('. '),
        ]
        
        bp = -1
        for candidate in break_points:
            if candidate > max_length * 0.5:
                bp = candidate
                break
        
        if bp == -1:
            bp = max_length - 50
        
        # Add part marker
        part = remaining[:bp].strip()
        if len(parts) > 0 or len(remaining) > max_length:
            part = f"_Part {part_num}_\n\n{part}"
        
        parts.append(part)
        remaining = remaining[bp:].strip()
        part_num += 1
    
    return parts


def escape_whatsapp_special(text: str) -> str:
    """
    Escape special characters that might interfere with WhatsApp formatting.
    """
    # Escape asterisks that aren't meant for formatting
    # Be careful not to escape intentional formatting
    
    # Escape underscores in the middle of words
    text = re.sub(r'(\w)_(\w)', r'\1\_\2', text)
    
    return text


def format_math_for_whatsapp(text: str) -> str:
    """
    Format mathematical expressions for WhatsApp.
    
    Since WhatsApp doesn't support LaTeX, we convert to plain text.
    """
    # Convert common LaTeX to plain text
    conversions = [
        (r'\frac\{([^}]+)\}\{([^}]+)\}', r'(\1)/(\2)'),
        (r'\sqrt\{([^}]+)\}', r'√(\1)'),
        (r'\^(\d+)', r'^(\1)'),
        (r'\\times', '×'),
        (r'\\div', '÷'),
        (r'\\pm', '±'),
        (r'\\leq', '≤'),
        (r'\\geq', '≥'),
        (r'\\neq', '≠'),
        (r'\\pi', 'π'),
        (r'\\theta', 'θ'),
        (r'\\alpha', 'α'),
        (r'\\beta', 'β'),
        (r'\\sum', 'Σ'),
    ]
    
    for pattern, replacement in conversions:
        text = re.sub(pattern, replacement, text)
    
    return text
