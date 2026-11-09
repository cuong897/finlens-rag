"""Keep wrapped source paragraphs intact for the no-model evidence view."""
import re

from finlens.text import tokens


def source_passages(text):
    """Only collapse whitespace within a paragraph; never supply new content.

    PDF line endings are usually layout, not sentence endings. Keep conditions
    and exceptions in the same passage. Short headings alone are not evidence.
    Separate paragraphs (including separate policies) remain separate quotes.
    """
    passages=[]
    for block in re.split(r'\n[ \t]*\n',text):
        passage=' '.join(block.split())
        if len(passage)>=40 and len(tokens(passage))>=6 and passage not in passages:
            passages.append(passage)
    return passages
