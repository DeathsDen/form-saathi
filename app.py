"""
Form Saathi - voice form-filling assistant in Indian languages.
Sarvam models used:
  1. Mayura / Sarvam Translate -> questions EN -> user's language, answers -> EN,
                                  answers EN -> the form's language
  2. Bulbul (TTS)              -> reads questions and answers aloud
  3. Saaras (STT)              -> transcribes speech and detects the language
  4. Sarvam-105B (LLM)         -> cleans + validates answers, reads whole-form
                                  answers, turns scanned forms into questions
  5. Transliterate             -> writes names in the form's script
  6. Document Intelligence     -> reads a photo / PDF of a paper form
Cross-language: the person can speak one language (e.g. Hindi) while the form
is in another (e.g. Telugu). Answers are kept in English internally and written
into the form's language at the end.
If Sarvam is unreachable (no credits, bad key, no internet) the app keeps
working: text stays in English, answers can be typed, and built-in checks
replace the LLM. Demo mode runs entirely on built-in sample data.
Run:  pip install -r requirements.txt
      export SARVAM_API_KEY=...   (Windows: $env:SARVAM_API_KEY="...")
      streamlit run app.py
"""
import base64, datetime, hashlib, html, io, json, os, re, tempfile, zipfile
from urllib.parse import urlencode, urlparse
import httpx, qrcode
import streamlit as st
from sarvamai import SarvamAI

def _secret_key():
    try:
        return st.secrets.get("SARVAM_API_KEY", "")
    except Exception:
        return ""

API_KEY = os.getenv("SARVAM_API_KEY") or _secret_key()
client = SarvamAI(api_subscription_key=API_KEY)

LLM_MODEL = "sarvam-105b"         # sarvam-30b was retired by Sarvam
STT_MODEL = "saaras:v4"
TTS_MODEL = "bulbul:v4-flash"
TTS_SPEAKER = "simran_en_customer"  # change if a language rejects this speaker

# Every language here works with all of Sarvam's speech, voice and translation models.
LANGS = {"Telugu": ("te-IN", "తెలుగు"), "Hindi": ("hi-IN", "हिन्दी"),
         "Tamil": ("ta-IN", "தமிழ்"), "Kannada": ("kn-IN", "ಕನ್ನಡ"),
         "Malayalam": ("ml-IN", "മലയാളം"), "Marathi": ("mr-IN", "मराठी"),
         "Bengali": ("bn-IN", "বাংলা"), "Gujarati": ("gu-IN", "ગુજરાતી"),
         "Punjabi": ("pa-IN", "ਪੰਜਾਬੀ"), "Odia": ("od-IN", "ଓଡ଼ିଆ"),
         "English": ("en-IN", "English")}
LANG_NAMES = list(LANGS)
CODE_TO_NAME = {code: name for name, (code, _) in LANGS.items()}
AUTO = "Auto-detect"

def lang_label(name):
    if name == AUTO:
        return "✨ Auto-detect"
    native = LANGS[name][1]
    return name if native == name else f"{name} · {native}"

FORMS = {
    "Scholarship application": [
        ("full_name", "What is your full name?", "a person's name"),
        ("age", "How old are you?", "a number of years"),
        ("phone", "What is your phone number?", "a 10 digit Indian mobile number"),
        ("village_town", "Which village or town do you live in?", "a place name"),
        ("course", "Which course or class are you studying?", "a course or class"),
        ("family_income", "What is your family's yearly income in rupees?", "an amount in rupees"),
    ],
    "Ration card update": [
        ("full_name", "What is the name of the head of the family?", "a person's name"),
        ("members", "How many members are in your family?", "a number"),
        ("phone", "What is your phone number?", "a 10 digit Indian mobile number"),
        ("address", "What is your full address?", "an address"),
        ("change", "What change do you want to make to your ration card?", "a short request"),
    ],
}

def kind_of(fid, question):
    """What sort of answer a field takes, guessed from its id and question."""
    t = f"{fid} {question}".lower()
    if "email" in t or "e-mail" in t:
        return "email"
    if "phone" in t or "mobile" in t:
        return "phone"
    if re.search(r"\bage\b|how old", t):
        return "age"
    if re.search(r"income|rupee|salary|amount|₹", t):
        return "money"
    if re.search(r"how many|number of|members", t):
        return "count"
    # "name of your street / school / bank" is not a person's name.
    if "name" in t and not re.search(
            r"street|colony|road|lane|village|town|city|district|mandal|taluk|tehsil|block|state|"
            r"country|school|college|universit|institut|company|employer|organi[sz]ation|office|"
            r"bank|branch|hospital|place|area|locality|nagar|ward|post|building|house|flat|"
            r"apartment|course|scheme|caste|religion|occupation|relation|project|business|shop|"
            r"product|account holder", t):
        return "name"
    return "text"

EXPECTED = {"email": "an email address",
            "phone": "a 10 digit Indian mobile number", "age": "a number of years",
            "money": "an amount in rupees", "count": "a number",
            "name": "a person's name", "text": "a short answer"}

# ---------- Demo mode data (clearly labelled in the app; no live AI) ----------
_DEMO = {
    "What is your full name?": ("आपका पूरा नाम क्या है?", "మీ పూర్తి పేరు ఏమిటి?"),
    "How old are you?": ("आपकी उम्र कितनी है?", "మీ వయస్సు ఎంత?"),
    "What is your phone number?": ("आपका फ़ोन नंबर क्या है?", "మీ ఫోన్ నంబర్ ఏమిటి?"),
    "Which village or town do you live in?": ("आप किस गाँव या शहर में रहते हैं?",
                                              "మీరు ఏ గ్రామం లేదా పట్టణంలో నివసిస్తున్నారు?"),
    "Which course or class are you studying?": ("आप कौन सा कोर्स या कक्षा पढ़ रहे हैं?",
                                                "మీరు ఏ కోర్సు లేదా తరగతి చదువుతున్నారు?"),
    "What is your family's yearly income in rupees?": ("आपके परिवार की सालाना आय कितने रुपये है?",
                                                       "మీ కుటుంబ వార్షిక ఆదాయం ఎన్ని రూపాయలు?"),
    "What is the name of the head of the family?": ("परिवार के मुखिया का नाम क्या है?",
                                                    "కుటుంబ పెద్ద పేరు ఏమిటి?"),
    "How many members are in your family?": ("आपके परिवार में कितने सदस्य हैं?",
                                             "మీ కుటుంబంలో ఎంత మంది సభ్యులు ఉన్నారు?"),
    "What is your full address?": ("आपका पूरा पता क्या है?", "మీ పూర్తి చిరునామా ఏమిటి?"),
    "What change do you want to make to your ration card?": (
        "आप अपने राशन कार्ड में क्या बदलाव करना चाहते हैं?", "మీ రేషన్ కార్డులో ఏ మార్పు చేయాలనుకుంటున్నారు?"),
    "Scholarship application": ("छात्रवृत्ति आवेदन", "స్కాలర్‌షిప్ దరఖాస్తు"),
    "Ration card update": ("राशन कार्ड अपडेट", "రేషన్ కార్డు నవీకరణ"),
    "A mobile number must have 10 digits and start with 6, 7, 8 or 9.": (
        "मोबाइल नंबर में 10 अंक होने चाहिए और यह 6, 7, 8 या 9 से शुरू होना चाहिए।",
        "మొబైల్ నంబర్‌లో 10 అంకెలు ఉండాలి, అది 6, 7, 8 లేదా 9తో మొదలవ్వాలి."),
    "The answer was empty.": ("जवाब खाली था।", "సమాధానం ఖాళీగా ఉంది."),
    "Please say a number.": ("कृपया एक संख्या बताइए।", "దయచేసి ఒక సంఖ్య చెప్పండి."),
    "That age does not look right.": ("यह उम्र सही नहीं लगती।", "ఈ వయస్సు సరిగ్గా అనిపించడం లేదు."),
    "That number does not look right.": ("यह संख्या सही नहीं लगती।", "ఈ సంఖ్య సరిగ్గా అనిపించడం లేదు."),
    "Please say the amount in rupees.": ("कृपया रकम रुपयों में बताइए।", "దయచేసి మొత్తాన్ని రూపాయలలో చెప్పండి."),
    "Ravi Kumar": ("रवि कुमार", "రవి కుమార్"),
    "Lakshmi Devi": ("लक्ष्मी देवी", "లక్ష్మి దేవి"),
    "Guntur": ("गुंटूर", "గుంటూరు"),
    "B.Sc Computer Science": ("बी.एससी कंप्यूटर साइंस", "బి.ఎస్సీ కంప్యూటర్ సైన్స్"),
    "12 Gandhi Road, Guntur": ("12 गांधी रोड, गुंटूर", "12 గాంధీ రోడ్, గుంటూరు"),
    "Add my daughter's name": ("मेरी बेटी का नाम जोड़ें", "నా కూతురి పేరు చేర్చండి"),
}
DEMO_TEXT = {}
for en, (hi, te) in _DEMO.items():
    DEMO_TEXT[(en, "hi-IN")] = hi
    DEMO_TEXT[(en, "te-IN")] = te
DEMO_ANSWERS = {
    "Scholarship application": {"full_name": "Ravi Kumar", "age": "19", "phone": "9876543210",
                                "village_town": "Guntur", "course": "B.Sc Computer Science",
                                "family_income": "₹2,50,000"},
    "Ration card update": {"full_name": "Lakshmi Devi", "members": "4", "phone": "9123456780",
                           "address": "12 Gandhi Road, Guntur", "change": "Add my daughter's name"},
}

# ---------- Sarvam helpers ----------
@st.cache_data(show_spinner=False)
def translate(text, target, source="auto"):
    if target == "en-IN" and source == "en-IN":
        return text
    r = client.text.translate(input=text, source_language_code=source,
                              target_language_code=target)
    return r.translated_text

@st.cache_data(show_spinner=False)
def transliterate(text, target):
    r = client.text.transliterate(input=text, source_language_code="en-IN",
                                  target_language_code=target)
    return r.transliterated_text

@st.cache_data(show_spinner=False)
def speak(text, lang):
    r = client.text_to_speech.convert(text=text, language_code=lang,
                                      model=TTS_MODEL, speaker=TTS_SPEAKER)
    return base64.b64decode("".join(r.audios))

def transcribe(audio_bytes, lang):
    """Returns (transcript, language code). lang=None lets Saaras detect the language."""
    r = client.speech_to_text.transcribe(
        file=("answer.wav", audio_bytes, "audio/wav"),
        model=STT_MODEL, mode="transcribe", language_code=lang or "unknown")
    return r.transcript, (r.language_code or lang)

def identify(text):
    return client.text.identify_language(input=text[:1000]).language_code

def llm(prompt, json_mode=False):
    # Low reasoning effort: with the default, sarvam-105b can spend its whole token
    # budget thinking on long inputs (like a scanned form) and return no answer at all.
    kw = dict(reasoning_effort="low", max_tokens=6000, temperature=0.2)
    if json_mode:
        kw["response_format"] = {"type": "json_object"}
    for _ in range(2):   # one retry: an empty reply is occasional, not permanent
        r = client.chat.completions(model=LLM_MODEL,
                                    messages=[{"role": "user", "content": prompt}], **kw)
        text = re.sub(r"<think>.*?</think>", "", r.choices[0].message.content or "", flags=re.S).strip()
        if text:
            return text
    raise ValueError("the AI gave an empty answer")

def llm_json(prompt):
    return llm(prompt, json_mode=True)

def read_document(data, filename, lang):
    """Run Sarvam Document Intelligence on a photo/PDF and return its text."""
    doc_lang = {"od-IN": "or-IN"}.get(lang, lang)
    with tempfile.TemporaryDirectory() as d:
        src = os.path.join(d, filename)
        with open(src, "wb") as f:
            f.write(data)
        job = client.document_intelligence.create_job(language=doc_lang, output_format="md")
        job.upload_file(src)
        job.start()
        status = job.wait_until_complete(timeout=180)
        if str(getattr(status, "job_state", "")) == "Failed":
            raise ValueError("Sarvam could not read this document")
        out = job.download_output(os.path.join(d, "out"))
        if zipfile.is_zipfile(out):
            with zipfile.ZipFile(out) as z:
                # Prefer the readable text; the .json files are layout metadata.
                names = [n for n in z.namelist() if n.endswith((".md", ".txt", ".html"))] or z.namelist()
                return "\n".join(z.read(n).decode("utf-8", "ignore") for n in names)
        with open(out, encoding="utf-8", errors="ignore") as f:
            return f.read()

def _why(e):
    code = getattr(e, "status_code", None)
    if code == 402:
        return "Your Sarvam account has no credits left. Add credits or use another key."
    if code in (401, 403):
        return "Sarvam rejected the API key. Check SARVAM_API_KEY."
    if code == 429:
        return "Too many requests to Sarvam. Wait a few seconds and try again."
    if code:
        msg = (getattr(e, "body", None) or {}).get("error", {}).get("message", "")
        return f"Sarvam error {code}: {msg or type(e).__name__}"
    if isinstance(e, ValueError):
        return f"Sarvam: {e}"
    return f"Couldn't reach Sarvam ({type(e).__name__}). Check your internet."

def _demo(fn, args, fallback):
    if fn in (translate, transliterate):
        return DEMO_TEXT.get((args[0], args[1]), fallback)
    return fallback

def safe(fn, *args, fallback=None):
    """Call a Sarvam helper; on failure remember why and return the fallback."""
    if st.session_state.get("demo"):
        return _demo(fn, args, fallback)
    if st.session_state.get("api_blocked"):   # no credits / bad key: skip the remaining calls this run
        return fallback
    try:
        return fn(*args)
    except Exception as e:
        why = _why(e)
        if why not in st.session_state.api_issues:
            st.session_state.api_issues.append(why)
        if getattr(e, "status_code", None) in (401, 402, 403):
            st.session_state.api_blocked = True
        return fallback

def to_local(text, lang):
    return text if lang == "en-IN" else safe(translate, text, lang, "en-IN", fallback=text)

def to_english(text, lang):
    return text if lang == "en-IN" else safe(translate, text, "en-IN", fallback=text)

def to_form(kind, value, form_lang):
    """Write an English answer the way it should appear on a form in form_lang:
    names are transliterated, free text is translated, numbers stay as they are."""
    if not value or form_lang == "en-IN" or kind == "choice":   # choices must match the form exactly
        return value
    if kind == "name":
        return safe(transliterate, value, form_lang, fallback=value)
    if kind == "text":
        return safe(translate, value, form_lang, "en-IN", fallback=value)
    return value

def parse_json(text, default):
    m = re.search(r"\{.*\}|\[.*\]", text or "", re.S)
    try:
        return json.loads(m.group(0)) if m else default
    except Exception:
        return default

# ---------- Answer checks ----------
def inr(n):
    s = str(n)
    if len(s) <= 3:
        return s
    return re.sub(r"(\d)(?=(\d\d)+$)", r"\1,", s[:-3]) + "," + s[-3:]

def local_check(kind, value):
    """Instant rule-based checks. Returns (clean value, issue or '')."""
    v = (value or "").strip().strip(".")
    if not v:
        return v, "The answer was empty."
    digits = re.sub(r"\D", "", v)
    if kind == "email":
        e = re.sub(r"\s+", "", re.sub(r"\s+at\s+", "@", re.sub(r"\s+dot\s+", ".", v.lower())))
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[a-z]{2,}", e):
            return v, "That does not look like an email address."
        return e, ""
    if kind == "phone":
        if len(digits) == 12 and digits.startswith("91"):
            digits = digits[2:]
        elif len(digits) == 11 and digits.startswith("0"):
            digits = digits[1:]
        if len(digits) != 10 or digits[0] not in "6789":
            return v, "A mobile number must have 10 digits and start with 6, 7, 8 or 9."
        return digits, ""
    if kind in ("age", "count"):
        if not digits:
            return v, "Please say a number."
        n = int(digits)
        if kind == "age" and not 3 <= n <= 100:
            return v, "That age does not look right."
        if kind == "count" and not 1 <= n <= 1000:
            return v, "That number does not look right."
        return str(n), ""
    if kind == "money":
        if not digits:
            return v, "Please say the amount in rupees."
        return f"₹{inr(int(digits))}", ""
    return v, ""

def field_options(fid):
    """Choices for a multiple-choice question imported from a Google Form, else []."""
    return st.session_state.get("form_meta", {}).get(form_name, {}).get("options", {}).get(fid, [])

def field_kind(fid, question):
    return "choice" if field_options(fid) else kind_of(fid, question)

def validate(fid, question, value):
    """local_check, plus matching multiple-choice answers to the exact option text."""
    opts = field_options(fid)
    if not opts:
        return local_check(kind_of(fid, question), value)
    v = (value or "").strip().strip(".").casefold()
    for o in opts:
        if v == o.casefold():
            return o, ""
    hits = [o for o in opts if v and (v in o.casefold() or o.casefold() in v)]
    if len(hits) == 1:
        return hits[0], ""
    return value, "Please choose one of: " + ", ".join(opts)

def check_answer(fid, question, expected, english_answer):
    out = safe(llm_json,
        f"A user filling a form was asked: '{question}' (hint: usually {expected}).\n"
        f"Their spoken answer (translated to English): '{english_answer}'.\n"
        "The question itself decides what fits; the hint is only a guide. Pull out just the "
        "part that answers the question and ignore other chatter. Write numbers as plain "
        "digits (e.g. 250000, not 2.5 lakh). Be lenient: mark it invalid only if the answer "
        "clearly does not answer the question.\n"
        'Reply ONLY with JSON: {"value": "<clean value for the form>", '
        '"valid": true/false, "issue": "<short reason if invalid, else empty>"}')
    res = {"value": english_answer, "valid": True, "issue": ""}
    got = parse_json(out, {})
    if isinstance(got, dict):
        res.update(got)
    if res.get("valid", True):
        value, issue = validate(fid, question, str(res.get("value", "")))
        res.update(value=value, valid=not issue, issue=issue)
    return res

def rough_extract(text, fields):
    """Rule-based fallback for 'say everything at once' when the LLM is unavailable."""
    pats = {
        "phone": r"(?:\+?91[\s-]?)?[6-9](?:[\s-]?\d){9}",
        "age": r"(\d{1,3})\s*(?:years?|yrs?|saal|sal)\b|(?i:\bage\s*(?:is)?\s*)(\d{1,3})",
        "money": r"(?:₹|rs\.?|inr)\s*([\d,]+)|([\d,]+)\s*(?:rupees|rs\b)",
        "name": r"(?i:my name is|name is)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)",
    }
    out = {}
    for fid, q, _ in fields:
        pat = pats.get(kind_of(fid, q))
        m = pat and re.search(pat, text, 0 if kind_of(fid, q) == "name" else re.I)
        if m:
            out[fid] = next((g for g in m.groups() if g), m.group(0))
    return out

def extract_all(text_en, fields):
    out = safe(llm_json,
        "Fields to fill:\n" + "\n".join(f"- {fid}: {q}" for fid, q, _ in fields) +
        f"\n\nThe applicant said (translated to English): '{text_en}'\n"
        "Extract each field's value. Write numbers as plain digits. "
        'Reply ONLY with a JSON object mapping field id to value; use "" if not mentioned.')
    got = parse_json(out, None)
    return got if isinstance(got, dict) else rough_extract(text_en, fields)

# ---------- Importing forms ----------
_JUNK = re.compile(
    r"^(your answer|submit|clear form|required|\*\s*indicates required|indicates required|next|back|"
    r"page \d+ of \d+|sign in|switch account|never submit passwords|this (form|content)|google forms|"
    r"report abuse|terms of service|privacy policy|not shared|other:?|choose|select|option \d+|"
    r"the name, email|record .* email|untitled)", re.I)
_LABEL_HINT = re.compile(
    r"\b(name|phone|mobile|e-?mail|address|age|date of birth|dob|gender|city|village|town|district|"
    r"state|income|occupation|aadhaar|pin ?code|course|class|school|college|father|mother|guardian)\b", re.I)

def _is_foreign(text):
    return bool(re.search(r"[^\x00-\x7F₹’‘“”–—]", text or ""))

def questions_from_text(text):
    """Rule-based fallback: pick question-like lines out of form text."""
    out = []
    for line in (text or "").splitlines():
        raw = line.strip()
        # Headings and whole-line bold text are titles, not questions.
        if not raw or raw.startswith("#") or re.fullmatch(r"\*\*[^*]+\*\*:?", raw):
            continue
        l = re.sub(r"^[>\-•\s]+", "", raw)
        l = re.sub(r"^\(?\d{1,2}[.)]\s*", "", l)            # "1." / "(2)" numbering
        has_blank = bool(re.search(r"_{3,}|\.{4,}", l))
        l = re.sub(r"_{3,}|\.{4,}", " ", l).strip()
        required = l.endswith("*") and not l.endswith("**")
        q = l.replace("**", "").strip("*").strip()
        ends_colon = q.endswith(":")
        q = q.split(":")[0].strip() if ":" in q else q
        if " / " in q:   # bilingual label like "పేరు / Name": keep the English part
            latin = [p.strip() for p in q.split(" / ") if re.search(r"[A-Za-z]", p) and not _is_foreign(p)]
            if latin:
                q = " / ".join(latin)
        if not 3 <= len(q) <= 150 or _JUNK.search(q) or re.search(
                r"office use|signature|seal|verified by|^note\b|instruction", q, re.I):
            continue
        if has_blank or required or ends_colon or raw.endswith("?") or _LABEL_HINT.search(q):
            if q.casefold() not in (o.casefold() for o in out):
                out.append(q)
    return out[:20]

def fields_from_text(text):
    """Turn the text of any form into (title, English questions, used_ai)."""
    got = parse_json(safe(llm_json,
        "Below is the text of a form (copied from a website or read from a photo; it may be in an "
        "Indian language). List every blank the applicant has to fill in. Skip office-use-only parts, "
        'signatures and website menus. Reply ONLY with JSON: {"title": "<form title in English>", '
        '"fields": ["<a short, polite English question asking for that field>", ...]} '
        "with at most 20 fields.\n\n" + (text or "")[:6000]), {})
    if isinstance(got, dict) and got.get("fields"):
        title, qs, used_ai = str(got.get("title") or "Imported form"), [str(q) for q in got["fields"][:20]], True
    else:
        first = next((l.strip().strip("#* ") for l in (text or "").splitlines() if l.strip()), "")
        title = first[:60] if 3 <= len(first) <= 80 else "Imported form"
        qs, used_ai = [q for q in questions_from_text(text) if q != first], False
    # Everything inside the app works in English; translate anything that isn't.
    qs = [safe(translate, q, "en-IN", fallback=q) if _is_foreign(q) else q for q in qs]
    if _is_foreign(title):
        title = safe(translate, title, "en-IN", fallback=title)
    return title, qs, used_ai

GF_TYPES = {0: "short", 1: "paragraph", 2: "choice", 3: "dropdown", 4: "checkboxes",
            5: "scale", 9: "date", 10: "time"}

def google_form(url):
    """Read a public Google Form. Returns (title, fields, meta) without needing Sarvam."""
    url = url.strip()
    if not url.startswith("http"):
        url = "https://" + url
    if urlparse(url).netloc.lower() not in ("docs.google.com", "forms.gle"):
        raise ValueError("That isn't a Google Form link (it should start with docs.google.com/forms or forms.gle).")
    r = httpx.get(url, follow_redirects=True, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
    final = urlparse(str(r.url))
    m = re.search(r"FB_PUBLIC_LOAD_DATA_\s*=\s*(.*?);\s*</script>", r.text, re.S)
    if final.netloc != "docs.google.com" or "/forms/" not in final.path or not m:
        raise ValueError("Couldn't open that form. It may need sign-in, be closed, or not be public.")
    data = json.loads(m.group(1))
    title = (data[1][8] if len(data[1]) > 8 else None) or data[3] or "Google Form"
    fields, entries, options = [], {}, {}
    for item in data[1][1] or []:
        if len(item) < 5 or not item[4] or item[3] not in GF_TYPES:
            continue
        q, entry = str(item[1] or "").strip(), item[4][0][0]
        if not q:
            continue
        fid = f"g{entry}"
        opts = [str(o[0]) for o in (item[4][0][1] or []) if o and o[0]]
        if GF_TYPES[item[3]] in ("choice", "dropdown", "checkboxes", "scale") and opts:
            options[fid] = opts
            expected = "one of: " + ", ".join(opts)
        elif GF_TYPES[item[3]] == "date":
            expected = "a date (write it as YYYY-MM-DD)"
        else:
            expected = EXPECTED[kind_of("", q)]
        fields.append((fid, q, expected))
        entries[fid] = entry
    if not fields:
        raise ValueError("That form has no questions Form Saathi can fill.")
    base = f"https://docs.google.com{final.path.rsplit('/', 1)[0]}/viewform"
    return str(title)[:80], fields, {"gform": base, "entries": entries, "options": options}

def prefill_link(meta, values):
    params = [("usp", "pp_url")] + [(f"entry.{meta['entries'][fid]}", v)
                                    for fid, v in values.items() if v and fid in meta["entries"]]
    return meta["gform"] + "?" + urlencode(params)

YES = {"yes", "yeah", "yep", "correct", "right", "ok", "okay", "haan", "han", "ha", "avunu",
       "aamaam", "houdu", "ho", "hoy", "sari", "हाँ", "हां", "అవును", "ஆம்", "ಹೌದು", "അതെ",
       "होय", "হ্যাঁ", "હા", "ਹਾਂ", "ହଁ"}
NO = {"no", "nope", "wrong", "nahi", "nahin", "kadu", "illa", "illai", "naahi", "na",
      "नहीं", "కాదు", "இல்லை", "ಇಲ್ಲ", "ഇല്ല", "नाही", "না", "ના", "ਨਹੀਂ", "ନା"}

def yes_or_no(*texts):
    # Split on spaces/punctuation only: \w would break Indic words at their vowel signs.
    words = set(re.split(r"[\s,.!?।'\"]+", " ".join(t or "" for t in texts).lower()))
    if words & NO:
        return "no"
    if words & YES:
        return "yes"
    return None

# ---------- Output ----------
def form_html(title, title_en, rows, note):
    """rows: (label in form language, English label, value in form language)."""
    def label(q, q_en):
        sub = f"<br><small>{html.escape(q_en)}</small>" if q != q_en else ""
        return html.escape(q) + sub
    body = "".join(
        f"<tr><th>{label(q, q_en)}</th><td>{html.escape(v) or '<i>—</i>'}</td></tr>"
        for q, q_en, v in rows)
    today = datetime.date.today().strftime("%d %B %Y")
    return f"""<!doctype html><html><head><meta charset="utf-8">
<title>{html.escape(title)}</title><style>
body{{font-family:system-ui,sans-serif;max-width:720px;margin:40px auto;padding:0 16px;color:#111}}
table{{border-collapse:collapse;width:100%}} th,td{{border:1px solid #999;padding:10px;text-align:left}}
th{{width:45%;background:#f2f2f2}} small{{color:#555;font-weight:normal}} .sig{{margin-top:70px}}
</style></head><body><h1>{label(title, title_en)}</h1>
<p>Filled with Form Saathi on {today}. {html.escape(note)}</p><table>{body}</table>
<p class="sig">Applicant signature: ______________________</p></body></html>"""

def qr_png(text):
    data = text.encode("utf-8")[:1800].decode("utf-8", "ignore")
    qr = qrcode.QRCode(box_size=6, border=2)
    qr.add_data(data)
    qr.make(fit=True)
    buf = io.BytesIO()
    qr.make_image().save(buf)
    return buf.getvalue()

# ---------- UI ----------
st.set_page_config(page_title="Form Saathi", page_icon="📝", initial_sidebar_state="collapsed")
ss = st.session_state
ss.api_issues = []
ss.api_blocked = False
ss.setdefault("custom_forms", {})
ss.setdefault("form_meta", {})
if "next_form" in ss:                     # a form was just created; select it
    ss.form_pick = ss.pop("next_form")

# ----- Light / dark mode -----
# Streamlit can't switch its built-in theme without a page reload (which would lose
# the answers), so dark mode is a set of CSS overrides on top of the light theme.
hero_box = st.container(key="herobox")       # header card; holds the Dark switch and the title
dark = hero_box.toggle(":material/dark_mode: Dark", key="dark")
st.markdown("""<style>
/* Sits in the top-right corner of the orange header card, clear of Streamlit's own toolbar
   (which gains Share / star / edit / GitHub buttons when deployed). */
.st-key-herobox {position:relative; gap:0}
.st-key-dark {position:absolute !important; top:1rem; right:1.3rem;
  z-index:5; width:auto !important; background:transparent !important}
.st-key-dark label, .st-key-dark p, .st-key-dark span {color:#fff !important}
.element-container:has(.st-key-dark), [data-testid="stElementContainer"]:has(.st-key-dark) {height:0; margin:0}
</style>""", unsafe_allow_html=True)
LIGHT_CSS = """
.st-key-setup, .st-key-qcard {background:#FFFFFF; box-shadow:0 6px 22px rgba(30,27,24,.07)}
.pill {background:#FFE3D3; color:#9C2C77}
"""
BG, SURF, SURF2, LINE, TEXT, MUTED, ACCENT = ("#15110E", "#201A16", "#2C241F", "#3D332C",
                                              "#F4ECE4", "#B9ACA0", "#FF8A5B")
DARK_CSS = f"""
.stApp, [data-testid="stAppViewContainer"], [data-testid="stMain"], [data-testid="stHeader"] {{background:{BG}}}
[data-testid="stSidebar"], [data-testid="stSidebarContent"] {{background:{SURF}}}
.stApp, .stApp p, .stApp li, .stApp label, .stApp h1, .stApp h2, .stApp h3, .stApp h4,
.stApp [data-testid="stWidgetLabel"] *, .stApp [data-testid="stMarkdownContainer"] *,
[data-testid="stHeader"] *, [data-testid="stToolbar"] * {{color:{TEXT}}}
.stApp [data-testid="stCaptionContainer"], .stApp [data-testid="stCaptionContainer"] * {{color:{MUTED}}}
.st-key-setup, .st-key-qcard {{background:{SURF}; border-color:{LINE} !important; box-shadow:none}}
[data-baseweb="input"], [data-baseweb="base-input"], [data-baseweb="textarea"],
[data-baseweb="select"] > div {{background:{SURF2} !important; border-color:{LINE} !important}}
.stApp input, .stApp textarea, [data-baseweb="select"] div {{color:{TEXT} !important;
  -webkit-text-fill-color:{TEXT}; caret-color:{ACCENT}}}
.stApp input::placeholder, .stApp textarea::placeholder {{color:{MUTED} !important; -webkit-text-fill-color:{MUTED}}}
[data-baseweb="select"] svg, [data-testid="stExpander"] summary svg {{color:{MUTED}}}
[data-baseweb="popover"] ul, [data-baseweb="popover"] li, [data-baseweb="menu"] {{background:{SURF} !important; color:{TEXT} !important}}
[data-baseweb="popover"] li:hover, [data-baseweb="popover"] li[aria-selected="true"] {{background:{SURF2} !important}}
[data-testid="stExpander"] details {{background:{SURF}; border-color:{LINE}}}
[data-testid="stExpander"] summary:hover * {{color:{ACCENT}}}
[data-testid="stBaseButton-secondary"], [data-testid="stBaseButton-pills"] {{background:{SURF2}; border-color:{LINE}}}
[data-testid="stBaseButton-secondary"]:hover, [data-testid="stBaseButton-pills"]:hover {{border-color:{ACCENT}}}
[data-baseweb="tab"] p {{color:{MUTED}}} [data-baseweb="tab"][aria-selected="true"] p {{color:{ACCENT}}}
[data-baseweb="tab-border"] {{background:{LINE}}}
[data-testid="stProgress"] [data-baseweb="progress-bar"] > div > div {{background:{SURF2}}}
[data-testid="stAudioInput"] > div, [data-testid="stFileUploaderDropzone"] {{background:{SURF2} !important; border-color:{LINE}}}
[data-testid="stAlertContainer"] {{background:rgba(255,255,255,.05) !important; border:1px solid {LINE}}}
[data-testid="stAlertContainer"]:has([data-testid="stAlertContentWarning"]) {{border-left:4px solid #F2B84B}}
[data-testid="stAlertContainer"]:has([data-testid="stAlertContentInfo"]) {{border-left:4px solid #5BA4F2}}
[data-testid="stAlertContainer"]:has([data-testid="stAlertContentSuccess"]) {{border-left:4px solid #4CC38A}}
[data-testid="stAlertContainer"]:has([data-testid="stAlertContentError"]) {{border-left:4px solid #F2665B}}
.stApp code {{background:{SURF2}; color:#FFB38A}}
[data-testid="stAudioInputWaveformTimeCode"] {{background:transparent !important; color:{MUTED} !important}}
[data-testid="stAudioInputActionButton"], [data-testid="stAudioInputActionButton"] span {{color:{TEXT} !important}}
.stApp .pill {{background:rgba(255,138,91,.16); color:#FFB38A}}
.hero {{box-shadow:none !important}}
"""
st.markdown(f"<style>{DARK_CSS if dark else LIGHT_CSS}</style>", unsafe_allow_html=True)

# The app's name cycles through every supported language ("form friend").
APP_NAMES = ["Form Saathi", "फ़ॉर्म साथी", "ఫారం నేస్తం", "படிவ நண்பன்", "ಫಾರ್ಮ್ ಗೆಳೆಯ",
             "ഫോം കൂട്ടുകാരൻ", "फॉर्म मित्र", "ফর্ম বন্ধু", "ફોર્મ મિત્ર", "ਫ਼ਾਰਮ ਸਾਥੀ", "ଫର୍ମ ସାଥୀ"]
SLOT = 3  # seconds each name stays on screen
_names = "".join(f'<span style="animation-delay:{n * SLOT}s">{html.escape(t)}</span>'
                 for n, t in enumerate(APP_NAMES))
_chips = "".join(f"<span>{html.escape(native)}</span>" for _, native in LANGS.values())
hero_box.markdown(f"""<style>
.block-container {{padding-top:4.6rem; max-width:780px}}
.hero {{padding:1.3rem 1.5rem 1.1rem; border-radius:22px; color:#fff; margin-bottom:.6rem;
  background:linear-gradient(135deg,#FF8A3D 0%,#E4572E 45%,#9C2C77 100%);
  box-shadow:0 10px 30px rgba(228,87,46,.25)}}
.hero .rot {{position:relative; height:3.3rem; overflow:hidden}}
.hero .rot span {{position:absolute; left:0; top:0; font-size:2.5rem; font-weight:800;
  line-height:3.3rem; white-space:nowrap; opacity:0;
  animation:rot {len(APP_NAMES) * SLOT}s infinite}}
@keyframes rot {{
  0% {{opacity:0; transform:translateY(70%)}}
  {100 / len(APP_NAMES) * 0.15:.2f}% {{opacity:1; transform:none}}
  {100 / len(APP_NAMES) * 0.85:.2f}% {{opacity:1; transform:none}}
  {100 / len(APP_NAMES):.2f}% {{opacity:0; transform:translateY(-70%)}}
  100% {{opacity:0}} }}
@media (max-width: 640px) {{
  .hero {{padding-top:2.8rem}} .hero .rot {{height:2.6rem}}
  .hero .rot span {{font-size:1.9rem; line-height:2.6rem}} }}
@media (prefers-reduced-motion: reduce) {{
  .hero .rot span {{animation:none}} .hero .rot span:first-child {{opacity:1}} }}
.hero .tag {{margin:.25rem 0 .7rem; font-size:1.05rem; opacity:.95}}
.hero .chips span {{display:inline-block; background:rgba(255,255,255,.2); border-radius:999px;
  padding:.12rem .62rem; margin:.14rem .1rem; font-size:.82rem}}
.pill {{display:inline-block; border-radius:999px;
  padding:.1rem .7rem; font-size:.8rem; font-weight:600; margin-bottom:.2rem}}
</style>
<div class="hero"><div class="rot">{_names}</div>
<div class="tag">Fill any form just by talking, in your own language.</div>
<div class="chips">{_chips}</div></div>""", unsafe_allow_html=True)

with st.sidebar:
    st.header("⚙️ Settings")
    simple = st.toggle("👵 Simple mode", help="Big buttons and voice only, for people who can't read.")
    readback = st.toggle("🔁 Read answers back", value=True,
                         help="After each answer the app reads it back and you say yes or no.")
    ss.demo = st.toggle("🎬 Demo mode", help="Runs on built-in sample data with no Sarvam calls. "
                        "Sample translations exist for Hindi and Telugu only.")
    st.caption("Powered by Sarvam AI: Saaras, Bulbul, Mayura, Sarvam-105B and Document Intelligence.")

banner = st.empty()

if simple:
    st.markdown("""<style>
.stButton, .stDownloadButton, [data-testid="stButton"], [data-testid="stDownloadButton"] {width:100% !important}
.stButton button, .stDownloadButton button {min-height:4.5rem; width:100% !important}
.stButton button p, .stDownloadButton button p {font-size:1.6rem !important}
h3 {font-size:2.4rem !important}
[data-testid="stCaptionContainer"], .pill {display:none}
</style>""", unsafe_allow_html=True)

if not API_KEY and not ss.demo:
    st.error("Set SARVAM_API_KEY first, or turn on Demo mode in the sidebar."); st.stop()

RULES_NOTE = ("The AI couldn't read this form, so the questions were picked with simple rules. "
              "Check them, and use Skip for anything that isn't a real question.")

def add_form(title, flds, meta=None):
    """Store an imported/created form and switch to it."""
    title = title.strip() or "My form"
    ss.custom_forms[title] = flds
    if meta:
        ss.form_meta[title] = meta
    ss.next_form = title
    st.rerun()

all_forms = {**FORMS, **ss.custom_forms}
with st.container(border=True, key="setup"):
    c1, c2, c3 = st.columns(3)
    speak_choice = c1.selectbox(":material/record_voice_over: You speak", [AUTO] + LANG_NAMES, index=1, format_func=lang_label)
    form_lang_name = c2.selectbox(":material/translate: Form is in", LANG_NAMES,
                                  index=LANG_NAMES.index("English"), format_func=lang_label)
    form_name = c3.selectbox(":material/description: Form", list(all_forms), key="form_pick")
    form_lang = LANGS[form_lang_name][0]
    fields = all_forms[form_name]

    key = f"{form_name}|{speak_choice}"
    if ss.get("key") != key:
        ss.update(key=key, i=0, answers={}, attempt=0, heard={}, summary={}, detected=None)
    auto = speak_choice == AUTO
    lang = (ss.detected or "en-IN") if auto else LANGS[speak_choice][0]
    lang_name = CODE_TO_NAME.get(lang, "English")
    i = ss.i

    if auto:
        st.caption(f"✨ Detected language: {lang_name}" if ss.detected
                   else "✨ Speak in any language. Form Saathi will switch to it.")
    if form_lang != lang:
        st.caption(f"You answer in {lang_name}; Form Saathi writes the form in {form_lang_name}.")

    if not simple:
        with st.expander("Use another form", icon=":material/add_circle:"):
            st.caption("Google Form link, text copied from any form website, a photo, or your own questions.")
            t_link, t_paste, t_photo, t_write = st.tabs(
                ["🔗 Google Form", "📋 Paste text", "📷 Photo / PDF", "✍️ Write questions"])
            with t_link:
                st.caption("Paste the link of a public Google Form. At the end you get the same "
                           "Google Form pre-filled, ready to check and submit.")
                link = st.text_input("Google Form link", placeholder="https://forms.gle/…")
                if link.strip() and st.button("Load this form", type="primary"):
                    with st.spinner("Opening the form..."):
                        try:
                            title, flds, meta = google_form(link)
                        except Exception as e:
                            st.error(str(e) if isinstance(e, ValueError)
                                     else f"Couldn't open that link ({type(e).__name__}). Check the internet.")
                        else:
                            add_form("🔗 " + title, flds, meta)
            with t_paste:
                st.caption("On any form website press Ctrl+A (Cmd+A on Mac), then Ctrl+C, "
                           "and paste everything here.")
                pasted = st.text_area("Copied form text", height=150)
                if pasted.strip() and st.button("Make questions from this text", type="primary"):
                    with st.spinner("Finding the questions..."):
                        title, qs, used_ai = fields_from_text(pasted)
                    if qs:
                        if not used_ai:
                            ss.flash = RULES_NOTE
                        add_form("📋 " + title[:60], [(f"p{n}", q, EXPECTED[kind_of('', q)])
                                                      for n, q in enumerate(qs, 1)])
                    st.warning("Couldn't find any questions in that text.")
            with t_photo:
                st.caption("Upload a photo or PDF of a paper form. Sarvam reads it and turns "
                           "each blank into a spoken question.")
                up = st.file_uploader("Photo or PDF", type=["png", "jpg", "jpeg", "pdf"])
                shot = st.camera_input("…or take a photo") if st.toggle("Use camera") else None
                pic = up or shot
                if pic and st.button("Read this form", type="primary"):
                    with st.spinner("Reading the form (this can take a minute)..."):
                        name = pic.name if up else "camera.jpg"
                        if name.lower().endswith(".jpeg"):
                            name = name[:-5] + ".jpg"
                        text = safe(read_document, pic.getvalue(), name, form_lang)
                        title, qs, used_ai = fields_from_text(text) if text else ("", [], False)
                    if qs:
                        if not used_ai:
                            ss.flash = RULES_NOTE
                        add_form("📷 " + title[:60], [(f"s{n}", q, EXPECTED[kind_of('', q)])
                                                      for n, q in enumerate(qs, 1)])
                    st.error("Couldn't read questions from that form. "
                             + (ss.api_issues[-1] if ss.api_issues else "Try a clearer photo."))
            with t_write:
                new_name = st.text_input("Form name", placeholder="e.g. Hostel admission")
                new_qs = st.text_area("Questions, one per line",
                                      placeholder="What is your full name?\nWhat is your phone number?")
                if st.button("Create form"):
                    qs = [q.strip() for q in new_qs.splitlines() if q.strip()]
                    if new_name.strip() and qs:
                        add_form(new_name, [(f"q{n}", q, EXPECTED[kind_of('', q)])
                                            for n, q in enumerate(qs, 1)])
                    st.warning("Give the form a name and at least one question.")

if "flash" in ss:
    _msg = ss.pop("flash")
    (st.warning if _msg == RULES_NOTE else st.success)(_msg)

def heard_from(rec, typed):
    """Transcribe a recording or take typed text. Returns (text, language code)."""
    if rec:
        out = safe(transcribe, rec.getvalue(), None if auto else lang)
        return out if out else (None, lang)
    code = lang
    if auto:
        code = safe(identify, typed, fallback=None) or lang
    return typed, code

def note_language(code):
    if auto and code in CODE_TO_NAME and code != ss.detected:
        ss.detected = code

def go_next():
    ss.attempt = 0
    ss.i = next((n for n, (fid, _, _) in enumerate(fields) if fid not in ss.answers), len(fields))

if ss.demo and form_name in DEMO_ANSWERS:
    with st.sidebar:
        if st.button("🎲 Fill sample answers"):
            ss.answers = dict(DEMO_ANSWERS[form_name]); ss.i = len(fields); st.rerun()

done = sum(1 for fid, _, _ in fields if ss.answers.get(fid))
st.progress(done / len(fields), text=f"{done}/{len(fields)} answered")

if i < len(fields):
    fid, q_en, expected = fields[i]
    kind = field_kind(fid, q_en)
    if expected in EXPECTED.values():   # recompute generic hints, so older imported forms pick up fixes
        expected = EXPECTED[kind]

    if not simple:
        with st.expander("Say everything at once", icon=":material/bolt:"):
            st.caption("Say all your details in one go, e.g. “My name is Ravi Kumar, I am 19, "
                       "my number is 98765 43210…” and Form Saathi fills what it can.")
            all_rec = st.audio_input(":material/mic: Say everything", key=f"all_rec_{key}")
            all_txt = st.text_area("…or type everything", key=f"all_txt_{key}")
            if (all_rec or all_txt.strip()) and st.button("Fill the form", type="primary"):
                with st.spinner("Filling..."):
                    said, code = heard_from(all_rec, all_txt.strip())
                    if said:
                        note_language(code)
                        got = extract_all(to_english(said, code or lang), fields)
                        filled = 0
                        for f_id, f_q, _ in fields:
                            value, issue = validate(f_id, f_q, str(got.get(f_id, "")))
                            if not issue:
                                ss.answers[f_id] = value; filled += 1
                        ss.flash = (f"Filled {filled} of {len(fields)} answers from what you said. "
                                    "Answer the rest below.")
                        go_next(); st.rerun()
                    st.warning("Couldn't understand that recording. Try typing instead.")

    with st.container(border=True, key="qcard"):
        with st.spinner("Preparing question..."):
            q_local = to_local(q_en, lang)
            audio = safe(speak, q_local, lang)
        st.markdown(f'<span class="pill">Question {i + 1} of {len(fields)}</span>', unsafe_allow_html=True)
        st.subheader(q_local)
        if q_local != q_en:
            st.caption(q_en)
        if form_lang not in (lang, "en-IN"):
            st.caption(f"On the {form_lang_name} form: {to_local(q_en, form_lang)}")
        if audio:
            st.audio(audio, format="audio/wav", autoplay=True)

        wkey = f"{key}_{i}_{ss.attempt}"
        rec = st.audio_input(":material/mic: Tap and answer", key=f"rec_{wkey}")
        typed = "" if simple else st.text_input(":material/keyboard: Or type your answer", key=f"txt_{wkey}")
        opts = field_options(fid)
        if opts:   # multiple-choice question from a Google Form: tap instead of speaking
            pick = st.pills(":material/touch_app: Or tap your choice", opts, key=f"pick_{wkey}")
            if pick:
                typed = pick

        # Each recording/typed answer is processed once, so button clicks don't re-bill Sarvam.
        src = rec.getvalue() if rec else typed.strip().encode()
        if src:
            h = hashlib.md5(src).hexdigest()
            if h not in ss.heard:
                with st.spinner("Listening..."):
                    heard, code = heard_from(rec, typed.strip())
                    if heard:
                        note_language(code)
                        heard_en = to_english(heard, code or lang)
                        ss.heard[h] = (heard, check_answer(fid, q_en, expected, heard_en))
                    else:
                        ss.heard[h] = (None, None)
            heard, res = ss.heard[h]
            if heard is None:
                st.warning("Couldn't understand the recording. Please try again"
                           + ("." if simple else " or type your answer."))
            else:
                st.write(f"**You said:** {heard}")
                st.write(f"**Form value:** `{res['value']}`")
                if not res.get("valid", True):
                    warn = to_local(res["issue"], lang)
                    note = f"  \n({res['issue']})" if warn != res["issue"] else ""
                    st.warning(f"{warn}{note}  \nPlease answer again.")
                    warn_audio = safe(speak, warn, lang)
                    if warn_audio:
                        st.audio(warn_audio, format="audio/wav", autoplay=True)
                    if st.button("🔁 Answer again", type="primary"):
                        ss.attempt += 1; st.rerun()
                else:
                    value = res["value"]
                    if form_lang != "en-IN":
                        st.write(f"**On the {form_lang_name} form:** `{to_form(kind, value, form_lang)}`")
                    if readback:
                        spoken = " ".join(value) if kind == "phone" else value  # read numbers digit by digit
                        rb = to_local(f"I heard: {spoken}. Is that right? Say yes or no.", lang)
                        st.info(f"🔊 {rb}")
                        rb_audio = safe(speak, rb, lang)
                        if rb_audio:
                            st.audio(rb_audio, format="audio/wav", autoplay=True)
                        yn = st.audio_input(":material/mic: Say yes or no", key=f"yn_{wkey}")
                        if yn:
                            h2 = hashlib.md5(yn.getvalue()).hexdigest()
                            if h2 not in ss.heard:
                                said, code = heard_from(yn, "")
                                ss.heard[h2] = yes_or_no(said, to_english(said, code or lang) if said else "")
                            verdict = ss.heard[h2]
                            if verdict == "yes":
                                ss.answers[fid] = value; go_next(); st.rerun()
                            elif verdict == "no":
                                ss.attempt += 1; st.rerun()
                            else:
                                st.warning("Didn't catch a yes or no. Tap a button below.")
                    b1, b2 = st.columns(2)
                    if b1.button("✅ Correct, next", type="primary"):
                        ss.answers[fid] = value; go_next(); st.rerun()
                    if b2.button("🔁 Answer again"):
                        ss.attempt += 1; st.rerun()

    n1, n2, _ = st.columns([1, 1, 3])
    if i > 0 and n1.button("Back", icon=":material/arrow_back:", use_container_width=True):
        ss.i -= 1; ss.attempt = 0; st.rerun()
    if (n2 if i > 0 else n1).button("Skip", icon=":material/skip_next:", use_container_width=True):
        ss.answers[fid] = ""
        go_next(); st.rerun()
else:
    st.success("Form complete! Check your answers and fix anything below.")
    answers = ss.answers
    for fid, q_en, _ in fields:
        kind = field_kind(fid, q_en)
        new = st.text_input(q_en, value=answers.get(fid, ""), key=f"edit_{key}_{fid}")
        if new != answers.get(fid, ""):
            value, issue = validate(fid, q_en, new)
            if issue:
                st.warning(issue)
            answers[fid] = value if not issue else new
        if form_lang != "en-IN" and answers.get(fid):
            st.caption(f"On the {form_lang_name} form: {to_form(kind, answers[fid], form_lang)}")

    # Summarise once per set of answers, so edits don't re-bill Sarvam.
    skey = json.dumps([answers, lang], sort_keys=True)
    if skey not in ss.summary:
        with st.spinner("Summarising..."):
            missing = [q for fid, q, _ in fields if not answers.get(fid)]
            summary_en = safe(llm,
                f"Summarise this {form_name} in 2 simple sentences for the applicant, "
                f"and mention any missing field: {json.dumps(answers)}") or (
                f"Your {form_name.lower()} has {len(fields) - len(missing)} of {len(fields)} answers filled."
                + (f" Still missing: {', '.join(missing)}" if missing else " Nothing is missing."))
            summary = to_local(summary_en, lang)
            ss.summary[skey] = (summary, safe(speak, summary[:450], lang))
    summary, summary_audio = ss.summary[skey]
    st.info(summary)
    if summary_audio:
        st.audio(summary_audio, format="audio/wav")

    # The finished form is written in the form's language, with English labels underneath.
    form_values = {fid: to_form(field_kind(fid, q_en), answers.get(fid, ""), form_lang)
                   for fid, q_en, _ in fields}
    rows = [(to_local(q_en, form_lang), q_en, form_values[fid]) for fid, q_en, _ in fields]
    title = to_local(form_name, form_lang)
    note = (f"Answered in {lang_name}, written in {form_lang_name}."
            if form_lang != lang else f"Answered in {lang_name}.")
    slug = re.sub(r"\W+", "_", form_name.lower()).strip("_") or "form"
    d1, d2 = st.columns(2)
    d1.download_button(f"Printable form ({form_lang_name})",
                       form_html(title, form_name, rows, note),
                       file_name=f"{slug}.html", mime="text/html", type="primary", icon=":material/print:")
    d2.download_button("Data (JSON)",
                       json.dumps({"form": form_name, "form_language": form_lang,
                                   "answered_in": lang, "answers": form_values,
                                   "answers_english": answers},
                                  ensure_ascii=False, indent=2),
                       file_name=f"{slug}.json", mime="application/json", icon=":material/download:")
    st.caption("Open the printable form in your browser and press Cmd+P (Ctrl+P on Windows) to print or save as PDF.")
    meta = ss.form_meta.get(form_name, {})
    if meta.get("gform"):
        st.link_button("Open your pre-filled Google Form", prefill_link(meta, form_values), icon=":material/send:",
                       type="primary", use_container_width=True)
        st.caption("Your answers are already filled in there. Check them and press Submit yourself.")

    q1, q2 = st.columns([1, 2])
    q1.image(qr_png(title + "\n" + "\n".join(f"{q}: {v}" for q, _, v in rows)), width=200)
    q2.markdown("**:material/smartphone: Scan to keep a copy**  \nPoint your phone camera at this code "
                "to get the filled answers on your phone. No internet needed.")

    b1, b2, _ = st.columns([1.3, 1, 2])
    if b1.button("Back to questions", icon=":material/arrow_back:", use_container_width=True):
        ss.i = len(fields) - 1; st.rerun()
    if b2.button("Start over", icon=":material/restart_alt:", use_container_width=True):
        ss.update(i=0, answers={}, attempt=0, heard={}, summary={}, detected=None); st.rerun()

if ss.demo:
    banner.info("**Demo mode**: built-in sample translations (Hindi and Telugu), no live AI "
                "and no voice. Turn it off in the sidebar for the real thing.", icon=":material/movie:")
elif ss.api_issues:
    banner.warning("  \n".join(ss.api_issues) + "  \nOffline mode: text stays in English and you can "
                   "type answers.", icon=":material/cloud_off:")
