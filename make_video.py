#!/usr/bin/env python3
"""Free AI video maker: topic -> script (Gemini) -> images (Pollinations)
-> Kazakh voice (Edge TTS) -> subtitles -> MP4 (ffmpeg).

Usage:
  python make_video.py --topic "Ақылды құлып деген не?" --scenes 6
  python make_video.py --mock   # offline test, no internet needed
"""
import argparse, asyncio, json, os, random, re, shutil, subprocess, sys, time
import urllib.parse, urllib.request

OUT = "out"
VOICES = {"aigul": "kk-KZ-AigulNeural", "daulet": "kk-KZ-DauletNeural"}
SIZES = {"vertical": (1080, 1920), "horizontal": (1920, 1080)}
FPS = 30


def log(*a):
    print(*a, flush=True)


def run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        log(r.stderr[-3000:])
        raise SystemExit(f"ffmpeg failed: {' '.join(cmd[:6])}...")
    return r.stdout


def duration(path):
    return float(run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                      "-of", "csv=p=0", path]).strip())


# ---------- 1. Script ----------
def http_json(url, body, headers):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers)
    with urllib.request.urlopen(req, timeout=180) as r:
        return json.loads(r.read())


def make_script(topic, n, style, lang):
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        raise SystemExit("GEMINI_API_KEY жоқ. GitHub → Settings → Secrets ішіне қосыңыз.")
    prompt = f"""You write scripts for short narrated videos.
Topic: {topic}
Narration language: {lang}. Write natural, simple, engaging {lang} (Cyrillic for Kazakh).
Make exactly {n} scenes. Scene 1 must be a strong hook. Last scene: short conclusion / call to action.
Each narration: 1-3 sentences (about 15-35 words).
Also invent ONE short English description of the recurring main character/setting so images stay consistent.
Each image_prompt: English, concrete visual description of that scene, no text in image.
Return ONLY JSON:
{{"title": "...", "character": "...", "scenes": [{{"narration": "...", "image_prompt": "..."}}]}}"""
    body = {"contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"responseMimeType": "application/json", "temperature": 0.9}}
    last = None
    for m in gemini_models(key):
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{m}:generateContent"
        for attempt in range(3):
            try:
                data = http_json(url, body, {"Content-Type": "application/json",
                                             "x-goog-api-key": key})
                script = parse_json(data["candidates"][0]["content"]["parts"][0]["text"])
                if script.get("scenes"):
                    log(f"Script ready with model {m}")
                    return script
            except Exception as e:  # try again / next model
                last = e
                log(f"Gemini {m} attempt {attempt+1} failed: {e}")
                if "404" in str(e) or "400" in str(e):
                    break
                time.sleep(10 * (attempt + 1))
    log(f"Gemini failed ({last}); trying free Pollinations text")
    for attempt in range(3):
        try:
            req = urllib.request.Request(
                "https://text.pollinations.ai/openai",
                data=json.dumps({"model": "openai", "messages": [{"role": "user", "content": prompt}],
                                 "response_format": {"type": "json_object"}}).encode(),
                headers={"Content-Type": "application/json", "User-Agent": "video-maker/1.0"})
            with urllib.request.urlopen(req, timeout=180) as r:
                data = json.loads(r.read())
            script = parse_json(data["choices"][0]["message"]["content"])
            if script.get("scenes"):
                log("Script ready with Pollinations")
                return script
        except Exception as e:
            last = e
            log(f"Pollinations text attempt {attempt+1} failed: {e}")
            time.sleep(15)
    raise SystemExit(f"Сценарий жазылмады: {last}")


def parse_json(text):
    text = re.sub(r"^```(json)?|```$", "", text.strip()).strip()
    return json.loads(text[text.find("{"):text.rfind("}") + 1])


def gemini_models(key):
    """Ask Google which flash text models this key can use right now."""
    names = []
    try:
        req = urllib.request.Request(
            "https://generativelanguage.googleapis.com/v1beta/models?pageSize=200",
            headers={"x-goog-api-key": key})
        with urllib.request.urlopen(req, timeout=60) as r:
            for md in json.loads(r.read()).get("models", []):
                n = md["name"].split("/", 1)[1]
                if ("generateContent" in md.get("supportedGenerationMethods", [])
                        and "flash" in n and not re.search(
                            r"image|tts|audio|live|embed|vision|thinking|exp|preview", n)):
                    names.append(n)
    except Exception as e:
        log(f"Model list failed: {e}")
    log(f"Available flash models: {names}")
    names.sort(key=lambda n: ("latest" not in n, "lite" in n, n), reverse=False)
    pref = [os.environ.get("GEMINI_MODEL"), "gemini-flash-latest", "gemini-flash-lite-latest"]
    out = []
    for n in [p for p in pref if p] + names:
        if n not in out:
            out.append(n)
    return out[:6]


# ---------- 2. Images ----------
def fallback_image(path, w, h, text, idx):
    from PIL import Image, ImageDraw
    random.seed(idx)
    c1 = tuple(random.randint(10, 60) for _ in range(3))
    c2 = (20, 90, 200)
    img = Image.new("RGB", (w, h))
    d = ImageDraw.Draw(img)
    for y in range(h):
        t = y / h
        d.line([(0, y), (w, y)], fill=tuple(int(c1[i] * (1 - t) + c2[i] * t) for i in range(3)))
    img.save(path, quality=92)


def get_image(prompt, path, w, h, seed, idx):
    q = urllib.parse.quote(prompt[:900])
    url = (f"https://image.pollinations.ai/prompt/{q}?width={w}&height={h}"
           f"&seed={seed}&nologo=true&model=flux")
    for attempt in range(4):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "video-maker/1.0"})
            with urllib.request.urlopen(req, timeout=180) as r:
                data = r.read()
            if len(data) > 10000:
                open(path, "wb").write(data)
                return True
        except Exception as e:
            log(f"Image {idx} attempt {attempt+1} failed: {e}")
        time.sleep(15 * (attempt + 1))
    log(f"Image {idx}: using fallback background")
    fallback_image(path, w, h, prompt, idx)
    return False


# ---------- 3. Voice ----------
async def tts(text, voice, path, rate):
    import edge_tts
    await edge_tts.Communicate(text, voice, rate=rate).save(path)


def make_voice(text, voice, path, rate):
    for attempt in range(4):
        try:
            asyncio.run(tts(text, voice, path, rate))
            if os.path.getsize(path) > 1000:
                return
        except Exception as e:
            log(f"TTS attempt {attempt+1} failed: {e}")
        time.sleep(5 * (attempt + 1))
    raise SystemExit("Озвучка жасалмады")


# ---------- 4. Subtitles ----------
def ass_time(t):
    h, rem = divmod(t, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h)}:{int(m):02d}:{s:05.2f}"


def chunk_words(text, max_words=5):
    words = text.split()
    return [" ".join(words[i:i + max_words]) for i in range(0, len(words), max_words)]


def write_ass(scenes, path, w, h):
    fs = int(h * 0.035) if h > w else int(h * 0.05)
    margin_v = int(h * 0.18) if h > w else int(h * 0.08)
    lines = [
        "[Script Info]", "ScriptType: v4.00+", f"PlayResX: {w}", f"PlayResY: {h}", "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
        "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
        "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: Default,DejaVu Sans,{fs},&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,"
        f"-1,0,0,0,100,100,0,0,1,{max(3, fs // 12)},2,2,60,60,{margin_v},1", "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    t0 = 0.0
    for s in scenes:
        parts = chunk_words(s["narration"])
        total = sum(len(p) for p in parts) or 1
        t = t0 + 0.15
        speak = s["dur"] - 0.5
        for p in parts:
            d = speak * len(p) / total
            txt = p.replace("{", "(").replace("}", ")")
            lines.append(f"Dialogue: 0,{ass_time(t)},{ass_time(t + d)},Default,,0,0,0,,{txt}")
            t += d
        t0 += s["dur"]
    open(path, "w", encoding="utf-8").write("\n".join(lines) + "\n")


# ---------- 5. Video ----------
def scene_clip(img, audio, dur, out, w, h, idx):
    frames = int(dur * FPS) + 1
    zoom_in = idx % 2 == 0
    z = "min(1+0.0009*on,1.15)" if zoom_in else "max(1.15-0.0009*on,1)"
    vf = (f"scale={w*2}:{h*2}:force_original_aspect_ratio=increase,crop={w*2}:{h*2},"
          f"zoompan=z='{z}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={frames}:s={w}x{h}:fps={FPS},"
          f"format=yuv420p")
    run(["ffmpeg", "-y", "-loop", "1", "-i", img, "-i", audio, "-vf", vf, "-af", "apad",
         "-t", f"{dur:.3f}", "-c:v", "libx264", "-preset", "veryfast", "-crf", "21",
         "-c:a", "aac", "-b:a", "160k", "-ar", "44100", "-ac", "2", out])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--topic", default="")
    ap.add_argument("--scenes", type=int, default=6)
    ap.add_argument("--voice", default="aigul")
    ap.add_argument("--format", default="vertical", choices=list(SIZES))
    ap.add_argument("--style", default="bright cartoon illustration, cinematic lighting, detailed")
    ap.add_argument("--lang", default="Kazakh")
    ap.add_argument("--rate", default="+0%")
    ap.add_argument("--mock", action="store_true", help="offline test")
    a = ap.parse_args()

    w, h = SIZES[a.format]
    shutil.rmtree(OUT, ignore_errors=True)
    os.makedirs(f"{OUT}/parts", exist_ok=True)
    voice = VOICES.get(a.voice.lower(), a.voice)

    if a.mock:
        script = {"title": "Тест видео", "character": "", "scenes": [
            {"narration": "Бұл тест сахнасы нөмір бір. Субтитр мен зум тексеріледі.", "image_prompt": "x"},
            {"narration": "Екінші сахна. Қазақша әріптер: ә, ғ, қ, ң, ө, ұ, ү, һ, і.", "image_prompt": "y"}]}
    else:
        if not a.topic.strip():
            raise SystemExit("Тақырып (topic) жазылмаған")
        script = make_script(a.topic, a.scenes, a.style, a.lang)
    json.dump(script, open(f"{OUT}/script.json", "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    log(f"Title: {script.get('title')}  scenes: {len(script['scenes'])}")

    seed = random.randint(1, 10**6)
    char = script.get("character", "")
    clips = []
    for i, s in enumerate(script["scenes"]):
        img, mp3, clip = (f"{OUT}/parts/{i:03d}.jpg", f"{OUT}/parts/{i:03d}.mp3",
                          f"{OUT}/parts/{i:03d}.mp4")
        if a.mock:
            fallback_image(img, w, h, "", i)
            run(["ffmpeg", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=3", mp3])
        else:
            full = f"{s['image_prompt']}. {char}. Style: {a.style}. No text, no watermark."
            get_image(full, img, w, h, seed + i, i)
            make_voice(s["narration"], voice, mp3, a.rate)
        s["dur"] = duration(mp3) + 0.4
        scene_clip(img, mp3, s["dur"], clip, w, h, i)
        clips.append(clip)
        log(f"Scene {i+1}/{len(script['scenes'])} done ({s['dur']:.1f}s)")

    with open(f"{OUT}/parts/list.txt", "w") as f:
        for c in clips:
            f.write(f"file '{os.path.abspath(c)}'\n")
    run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", f"{OUT}/parts/list.txt",
         "-c", "copy", f"{OUT}/joined.mp4"])

    write_ass(script["scenes"], f"{OUT}/subs.ass", w, h)
    run(["ffmpeg", "-y", "-i", f"{OUT}/joined.mp4", "-vf", f"ass={OUT}/subs.ass",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-c:a", "copy",
         "-movflags", "+faststart", f"{OUT}/video.mp4"])
    log(f"DONE: {OUT}/video.mp4  ({duration(f'{OUT}/video.mp4'):.1f}s)")


if __name__ == "__main__":
    main()
