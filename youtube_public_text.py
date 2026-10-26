"""Public chat accepts numeric/statistical replies, never internal appearance fields."""
import re
import unicodedata

APPEARANCE_FIELDS = re.compile(
    r'\b(?:ubiór|ubior|ubranie|włosy|wlosy|zarost|okulary|nakrycie\s+głowy|'
    r'dodatki|opis\s+osoby|opis\s+wyglądu|płeć|clothing|hair|beard|glasses|appearance)\s*:',
    re.IGNORECASE)


def validate_public_message(text):
    if not isinstance(text,str) or not text.strip() or len(text)>200:
        raise ValueError('Message must contain 1–200 characters')
    visible=''.join(char for char in unicodedata.normalize('NFKC',text)
                    if unicodedata.category(char)!='Cf')
    if APPEARANCE_FIELDS.search(visible):
        raise ValueError('Appearance descriptions are restricted to internal use, database and Sheets')
    return text
