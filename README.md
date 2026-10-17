# Form Saathi · फ़ॉर्म साथी · ఫారం నేస్తం · படிவ நண்பன்

**Fill any form just by talking, in your own language.**

Millions of people in India struggle with forms written in English or in a language they don't read. Form Saathi asks each question out loud in the person's language, listens to their spoken answer, checks it, and writes the finished form in whatever language the form needs.

Built on [Sarvam AI](https://www.sarvam.ai/) models.

## Features

**Languages**
- 11 languages: Telugu, Hindi, Tamil, Kannada, Malayalam, Marathi, Bengali, Gujarati, Punjabi, Odia and English
- **Cross-language forms:** speak Hindi, get the form filled in Telugu (or any other pair)
- Auto-detects the language you speak
- Names are transliterated into the form's script, free text is translated, numbers stay as numbers

**Filling the form**
- Questions are read aloud; answer by voice or by typing
- Say everything at once ("My name is Ravi, I'm 19, my number is…") and it fills what it can
- Reads each answer back and you confirm with a spoken yes or no
- Simple mode: big buttons, voice only, for people who can't read

**Smart checks**
- AI cleanup and validation of every answer (Sarvam-105B)
- Built-in checks that work offline: 10-digit Indian mobile numbers, ages, amounts in ₹ (Indian format), emails
- Wrong answers are flagged and read out in your language

**Bring any form**
- 🔗 Paste a **public Google Form link**: questions are read straight from the form, and at the end you get the **same Google Form pre-filled**, ready to check and submit
- 📋 Copy-paste the text of any form website (Ctrl+A, Ctrl+C)
- 📷 Upload a photo or PDF of a paper form (Sarvam Document Intelligence)
- ✍️ Write your own questions

**At the end**
- Editable review page and a spoken summary
- Printable form in the form's language (save as PDF) and a JSON download
- QR code to carry the answers on your phone, no internet needed

**Also**
- Light and dark mode
- Keeps working when Sarvam is unreachable (no credits, no internet): text stays in English and answers can be typed
- Demo mode with built-in sample data (Hindi and Telugu) as a stage backup. It is clearly labelled in the app.

## Sarvam models used

| Model | Used for |
|---|---|
| Saaras v4 (speech-to-text) | Transcribing answers and detecting the spoken language |
| Bulbul v4 (text-to-speech) | Reading questions, warnings and summaries aloud |
| Mayura / Sarvam Translate | Translating questions and answers |
| Transliterate | Writing names in the form's script |
| Sarvam-105B (LLM) | Cleaning and checking answers, "say everything at once", turning form text into questions |
| Document Intelligence | Reading photos and PDFs of paper forms |

## Run it locally

You need Python 3.9+ and a Sarvam API key from [dashboard.sarvam.ai](https://dashboard.sarvam.ai/).

```bash
git clone <your-repo-url>
cd form-saathi
./run.sh
```

`run.sh` creates a virtual environment, installs the requirements, asks for your key the first time, and starts the app at http://localhost:8501.

Or do it by hand:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .streamlit/secrets.toml.example .streamlit/secrets.toml   # then put your key in it
streamlit run app.py
```

You can also set the key as an environment variable instead: `export SARVAM_API_KEY="..."`.

No key? Turn on **Demo mode** in the sidebar to try the app with sample data.

## Project layout

```
form-saathi/
├── app.py                          # the whole app (Streamlit)
├── requirements.txt
├── run.sh                          # one-command setup and launch
└── .streamlit/
    ├── config.toml                 # colour theme
    └── secrets.toml.example        # copy to secrets.toml and add your key
```

## Known limits

- Google Forms must be public (no sign-in). Grid questions are skipped, and checkbox questions take one answer.
- Demo mode only has sample translations for Hindi and Telugu.
- The multilingual app names in the header should be checked by native speakers.

## Never commit your key

`.streamlit/secrets.toml` is in `.gitignore`. Keep it that way.
