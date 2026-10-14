"""PURE prospective simulator terminal-format candidate; unused by paid runners.

This is a declared protocol adaptation, not original tau-bench behavior. It never
calls an environment, grades an episode or authorizes a mutation. A future caller
must retain raw_reply and branch on disposition before considering consent.
"""
from dataclasses import dataclass
import re

MARKER='###STOP###'
VERSION='final-native-user-marker-v1-candidate'
_FAREWELL=re.compile(
    r'(?:farewell|goodbye|bye|thanks)[.!]?|'
    r'thank you(?: for (?:handling that|your help|helping me|your assistance))?[.!]?'
    r'(?: (?:that\'s all(?: for now)?[.!]?|'
    r'i\'ll keep an eye out for the email with the return instructions[.!]?))?'
)
_ASSENT=re.compile(r'yes[.!]?|yes, i approve (?:this|the) (?:entire |whole )?operation[.!]?')


@dataclass(frozen=True)
class TerminalView:
    raw_reply: str
    normalized_reply: str
    disposition: str
    may_classify_for_consent: bool
    version: str = VERSION


def normalize_native_user_reply(text: str, *, native_user_protocol: bool=False) -> TerminalView:
    """Only one unquoted, final simulator marker can become exact native STOP.

    Any marker-bearing response is ineligible for consent, including affirmative
    text followed by a marker. Invalid/unauthorized markers must be rejected, not
    passed to a consent classifier. Ordinary marker-free text is unchanged.
    """
    if type(text) is not str or type(native_user_protocol) is not bool:
        raise TypeError('Explicit text and native simulator protocol boolean required')
    if MARKER not in text:
        return TerminalView(text,text,'PASSTHROUGH',True)
    stripped=text.strip()
    if not native_user_protocol or stripped.count(MARKER)!=1 or not stripped.endswith(MARKER):
        return TerminalView(text,text,'REJECT_MARKER',False)
    prefix=stripped[:-len(MARKER)]
    # Unclosed quote/code context can turn a literal into a false terminal. Ignore
    # apostrophes inside words (e.g. "That's"), preserving actual farewell text.
    quotes=re.findall(r"(?<!\w)'|'(?!\w)",prefix)
    if (prefix.count('"')%2 or len(quotes)%2 or '`' in prefix or
            any(char in prefix for char in '{}[]“”‘’«»') or
            re.search(r'\b(example|literal|quoted|code|marker|sentinel|print|output|say)\b',prefix,re.I) or
            (prefix and not prefix[-1].isspace() and prefix[-1] not in '.!?')):
        return TerminalView(text,text,'REJECT_MARKER',False)
    # Finite whole-prefix grammar avoids treating arbitrary data/explanations as
    # terminal control. Unsupported final-prefix text remains conservatively out.
    normalized_prefix=' '.join(prefix.casefold().split())
    if normalized_prefix and not (_FAREWELL.fullmatch(normalized_prefix) or _ASSENT.fullmatch(normalized_prefix)):
        return TerminalView(text,text,'REJECT_MARKER',False)
    return TerminalView(text,MARKER,'TERMINATE',False)
