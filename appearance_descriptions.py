"""Observable appearance attributes and deterministic Polish descriptions."""
import re

VERSION = 2
QUESTIONS = {
    'clothing': 'What clothing is the person wearing? Describe the colors and garment types.',
    'hair': 'What is the color, length and style of the visible hair?',
    'headwear': "What headwear is visible on the person's head?",
    'glasses': "Are eyeglasses visible on the person's face?",
    'facial_hair': 'What facial hair is visible?',
    'accessories': 'What visible accessories is the person wearing?'}
UNKNOWN = {'state': 'unknown'}


def neutralize(text):
    # Discard unsolicited sex/gender labels; the descriptor has no such field.
    return re.sub(r'\b(man|woman|men|women|male|female|boy|girl|gentleman|lady|he|she|his|her|him)\b', 'person', text, flags=re.I)


def unclear(text):
    return not text or bool(re.search(r"unclear|not clear|cannot|can't|unable|hard to|not enough|unknown|can't see",text,re.I))


def absent(text):
    return bool(re.search(r'\b(no|not|without|none)\b|clean.shaven',text,re.I))


def color(text, gender='plural', hair=False):
    from clothing_descriptions import _COLORS
    plurals = {'white':'białe','black':'czarne','grey':'siwe' if hair else 'szare',
               'gray':'siwe' if hair else 'szare','red':'rude' if hair else 'czerwone',
               'brown':'brązowe','blond':'blond','blonde':'blond','ginger':'rude','auburn':'kasztanowe',
               'blue':'niebieskie','green':'zielone','yellow':'żółte','orange':'pomarańczowe',
               'purple':'fioletowe','pink':'różowe','beige':'beżowe','navy':'granatowe',
               'olive':'oliwkowe','cream':'kremowe','burgundy':'bordowe','maroon':'kasztanowe',
               'teal':'turkusowe','khaki':'khaki'}
    match = re.search(r'\b(?:(light|dark)[ -]+)?('+ '|'.join(plurals)+r')\b',text,re.I)
    if not match:
        return None
    shade, name = match.groups();name=name.lower()
    if gender == 'plural': value=plurals[name]
    elif name in _COLORS: value=_COLORS[name][1 if gender=='f' else 0]
    else: value={'blond':'blond','blonde':'blond','ginger':'ruda' if gender=='f' else 'rudy',
                 'auburn':'kasztanowa' if gender=='f' else 'kasztanowy'}[name]
    return (('jasno' if shade.lower()=='light' else 'ciemno') if shade else '')+value


def hair(text):
    if unclear(text): return dict(UNKNOWN)
    lower=text.lower()
    if re.search(r'\bbald\b|shaved head|shaved hair',lower):
        return {'state':'visible','style':'ogolona głowa / brak widocznych włosów'}
    if absent(text): return {'state':'not_visible'}
    # Do not pick colors from clothes mentioned after the hair answer.
    focus=lower.split('hair')[0][-70:] if 'hair' in lower else lower
    result={'state':'visible'}
    tone=color(focus,hair=True)
    if tone: result['color']=tone
    for term,value in [('shoulder-length','do ramion'),('medium','średniej długości'),
                       ('short','krótkie'),('long','długie')]:
        if term in lower: result['length']=value;break
    styles=[value for term,value in [('curly','kręcone'),('wavy','falowane'),('straight','proste'),
             ('ponytail','związane w kucyk'),('bun','upięte w kok'),('braid','zaplecione'),
             ('slicked back','zaczesane do tyłu'),('combed back','zaczesane do tyłu'),
             ('side part','przedziałek z boku')] if term in lower]
    if styles: result['style']=', '.join(dict.fromkeys(styles))
    return result if len(result)>1 else dict(UNKNOWN)


def headwear(text):
    if unclear(text): return dict(UNKNOWN)
    if absent(text) or re.search(r'hood.{0,8}down',text,re.I): return {'state':'not_visible'}
    for term,name,gender in [('baseball cap','czapka z daszkiem','f'),('beanie','czapka','f'),
                            ('cap','czapka','f'),('hat','kapelusz','m'),('hood','kaptur','m'),
                            ('headscarf','chusta na głowie','f'),('bandana','bandana','f')]:
        match=re.search(r'\b'+term+r'\b',text,re.I)
        if match:
            tone=color(text[max(0,match.start()-40):match.start()],gender)
            return {'state':'visible','type':name,**({'color':tone} if tone else {})}
    return dict(UNKNOWN)


def glasses(text):
    if unclear(text): return dict(UNKNOWN)
    if absent(text): return {'state':'not_visible'}
    if re.search(r'\byes\b|glasses|spectacles',text,re.I):
        result={'state':'visible','type':'okulary przeciwsłoneczne' if 'sunglasses' in text.lower() else 'okulary'}
        tone=color(text)
        if tone: result['color']=tone
        return result
    return dict(UNKNOWN)


def facial_hair(text):
    if unclear(text): return dict(UNKNOWN)
    result=[]
    for term,name,gender in [(r'beard','broda','f'),(r'mou?stache','wąsy','plural'),
                             (r'stubble','krótki zarost','m')]:
        match=re.search(r'\b'+term+r'\b',text,re.I)
        if not match: continue
        prefix=re.split(r'\b(?:but|and)\b',text[:match.start()],flags=re.I)[-1][-50:]
        if absent(prefix): continue
        words=[]
        tone=color(prefix,gender)
        if tone: words.append(tone)
        if 'long' in prefix.lower(): words.append('długa' if gender=='f' else 'długie' if gender=='plural' else 'długi')
        if 'short' in prefix.lower() and term!='stubble': words.append('krótka' if gender=='f' else 'krótkie' if gender=='plural' else 'krótki')
        result.append(' '.join(words+[name]))
    if result: return {'state':'visible','items':result}
    return {'state':'not_visible'} if absent(text) else dict(UNKNOWN)


def accessories(text):
    if unclear(text): return dict(UNKNOWN)
    result=[]
    for term,name,gender in [(r'(?:wrist)?watch','zegarek','m'),(r'ring','pierścionek','m'),
                            (r'earrings?','kolczyki','plural'),(r'necklace','naszyjnik','m'),
                            (r'scarf','szalik','m'),(r'tie','krawat','m')]:
        match=re.search(r'\b'+term+r'\b',text,re.I)
        if match:
            prefix=re.split(r'\b(?:but|and)\b|,',text[:match.start()],flags=re.I)[-1][-40:]
            if absent(prefix): continue
            tone=color(prefix,gender)
            result.append(' '.join(([tone] if tone else [])+[name]))
    if result: return {'state':'visible','items':result}
    return {'state':'not_visible'} if absent(text) else dict(UNKNOWN)


def summarize(answers):
    from clothing_descriptions import clothing_only
    features={'hair':hair(answers.get('hair','')), 'headwear':headwear(answers.get('headwear','')),
              'glasses':glasses(answers.get('glasses','')), 'facial_hair':facial_hair(answers.get('facial_hair','')),
              'accessories':accessories(answers.get('accessories',''))}
    clothes=clothing_only(answers.get('clothing',''))
    features['clothing']={'description':clothes}
    text=['Ubiór: '+clothes[0].lower()+clothes[1:]]
    if re.search(r'\b(logo|graphic|print)\b',answers.get('clothing',''),re.I):
        text.append('Na ubraniu widoczny nadruk lub logo.')
    for key,label in [('hair','Włosy'),('headwear','Nakrycie głowy'),('glasses','Okulary'),
                      ('facial_hair','Zarost'),('accessories','Dodatki')]:
        value=features[key]
        if value['state']=='unknown': detail='nie można ocenić'
        elif value['state']=='not_visible': detail='niewidoczne' if key in {'hair','glasses','accessories'} else 'niewidoczny w kadrze' if key=='facial_hair' else 'niewidoczne w kadrze'
        elif 'items' in value: detail=', '.join(value['items'])
        else: detail=', '.join(value[k] for k in ('color','length','style','type') if value.get(k))
        text.append(label+': '+detail+'.')
    return ' '.join(text),features
