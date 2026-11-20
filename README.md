# Video Maker (тегін)

Тақырып жазасыз → AI сценарий жазады (Gemini) → суреттер (Pollinations) → қазақша озвучка (Edge TTS) → субтитрлі MP4.

## Қалай қолдану
1. **Actions** → **Видео жасау** → **Run workflow**
2. Тақырыпты жазып, **Run** басыңыз
3. 5–15 минуттан кейін видео **Releases** бөлімінде шығады → MP4 файлын басып жүктеңіз

## Бір рет баптау
Settings → Secrets and variables → Actions → New repository secret
- Name: `GEMINI_API_KEY`
- Secret: aistudio.google.com/apikey сайтынан алынған кілт
