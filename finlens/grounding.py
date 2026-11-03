"""Narrow, deterministic checks for lost classification qualifications.

This detects explicit predicate broadening, not general semantic entailment.
It never changes source text or silently repairs a generated financial claim.
"""
import re

from finlens.text import normalize,tokens


def compact(text):
    return re.sub(r'[^a-z0-9]','',normalize(text))


def classification_rules(text):
    # OCR may omit Vietnamese syllable spaces. Inspect whole source sentences,
    # retaining their subject separately from category and subsequent rule.
    for sentence in re.split(r'(?<=[.!?;])\s+',text):
        joined=compact(sentence)
        marker='duocphanloaila'
        start=joined.find(marker)
        if start>=0:
            tail=joined[start+len(marker):]
            ends=[tail.find(p) for p in ('duocdanhgialai','duocghinhan','duoctrinhbay','duocdoluong') if p in tail]
            if ends:
                category=tail[:min(ends)]
                if 8<=len(category)<=140:
                    prefix=sentence[:next((m.start() for m in re.finditer(r'được\s*phân\s*loại\s*là',sentence,re.I)),len(sentence))]
                    yield prefix,category,'vi'
        # Relative/conditional classification in English. An unconditional
        # definition "X is classified as Y" alone is intentionally not matched.
        pattern=r'(?P<subject>[^.!?]{5,250}?)\b(?:that are|when|if)\s+classified as\s+(?P<category>[^.!?]{4,140}?)\s+(?:are|shall be|will be|must be)\b'
        for match in re.finditer(pattern,' '.join(normalize(sentence).split())):
            yield match['subject'],compact(match['category']),'en'


def lost_classification_scope(claim,source_texts):
    """Reject X is Y when its cited rule only covers X classified as Y.

    Correctly qualified noun phrases, explicit conditions and negation are
    allowed. Subject overlap prevents unrelated conditions on a long cited
    page from vetoing an otherwise valid statement.
    """
    joined=compact(claim);words=set(tokens(claim))
    stop={'cac','khoan','duoc','nhung','the','and','are','that','thuoc','cua','muc','nay','nhu','mot','la','as'}
    for source in source_texts:
        for subject,category,language in classification_rules(source):
            if language=='vi':
                if 'trongtruonghop' in joined or re.search(r'\b(?:neu|khi)\b',normalize(claim)):continue
                categories=[category]
                if category.startswith('cac') and not category.startswith('cach'):categories.append(category[3:])
                coordinated=next((joined.find('duocphanloaila'+c+'va') for c in categories if 'duocphanloaila'+c+'va' in joined),-1)
                if 'duocphanloai' in joined and coordinated<0:continue
                predicate=coordinated if coordinated>=0 else next((joined.find('la'+c) for c in categories if 'la'+c in joined),-1)
                if predicate<0 or joined[max(0,predicate-12):predicate].endswith(('khongphai','chuaphai')):continue
            else:
                if re.search(r'\b(?:if|when|classified|that are)\b',normalize(claim)):continue
                match=re.search(r'\b(?:are|is)\s+(?:all\s+)?',normalize(claim))
                if not match or not compact(claim[match.end():]).startswith(category):continue
                if re.search(r'\bnot\b',normalize(claim)):continue
            overlap=(set(tokens(subject))-stop)&(words-stop)
            if len(overlap)>=2 or any(len(w)>=8 for w in overlap):return True
    return False


def unsupported_allocation_basis(claim,source_texts):
    """A normal-capacity allocation basis is a policy detail, not a default.

    Check only that the named basis occurs in at least one cited excerpt, in
    either language. This is not verification of every accounting principle.
    """
    phrases=('hoat dong binh thuong','cong suat binh thuong','normal operations','normal operating activity','normal capacity','normal level of activity')
    present=lambda text:any(compact(p) in compact(text) for p in phrases)
    return present(claim) and not any(present(s) for s in source_texts)
