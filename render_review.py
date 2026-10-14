"""Render illustrated silent MP4s and an offline review page. No publishing."""
import argparse
import html
import json
from pathlib import Path
import re
import subprocess
import sys

from PIL import ImageFont

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'work' / 'runtime'))
import imageio_ffmpeg


def wrapped(text, font, width):
    lines, current = [], ''
    for word in text.split():
        candidate = (current + ' ' + word).strip()
        if font.getlength(candidate) > width:
            if not current:
                raise ValueError('Caption contains an oversized word.')
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines


def ass_text(text):
    return text.replace('\\', '').replace('{', '').replace('}', '').replace('\n', r'\N')


def stamp(seconds, ass=False):
    if ass:
        return f'0:00:{seconds:05.2f}'
    return f'00:00:{seconds:02d},000'


def subtitles(episode, folder):
    font = ImageFont.truetype('C:/Windows/Fonts/seguisb.ttf', 64)
    lines = [
        '[Script Info]', 'ScriptType: v4.00+', 'PlayResX: 1080', 'PlayResY: 1920',
        'WrapStyle: 2', 'ScaledBorderAndShadow: yes',
        '[V4+ Styles]',
        'Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding',
        'Style: Main,Segoe UI Semibold,64,&H00F4F6F8,&H00F4F6F8,&H00101920,&H80000000,0,0,0,0,100,100,0,0,1,1.2,0,7,78,78,0,1',
        '[Events]', 'Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text',
    ]
    lines.append('Dialogue: 0,0:00:00.00,0:00:30.00,Main,,0,0,0,,{\\pos(78,100)\\fs29\\1c&H8BD8E9&}' + ass_text(episode['label']))
    lines.append('Dialogue: 0,0:00:00.00,0:00:30.00,Main,,0,0,0,,{\\pos(78,1825)\\fs24\\1c&HCCD0D4&}AI-generated illustration / not real footage')
    srt = []
    for index, caption in enumerate(episode['post']['captions']):
        formatted = wrapped(caption, font, 900)
        if len(formatted) > 4:
            raise ValueError(f"Too many caption lines: {episode['slug']} / {index + 1}")
        start, end = index * 5, (index + 1) * 5
        tag = f'{{\\pos(78,1288)\\fs26\\1c&H8BD8E9&}}{index + 1:02d} / 06'
        lines.append(f'Dialogue: 0,{stamp(start, True)},{stamp(end, True)},Main,,0,0,0,,{tag}')
        content = ass_text('\n'.join(formatted))
        lines.append(f'Dialogue: 0,{stamp(start, True)},{stamp(end, True)},Main,,0,0,0,,{{\\pos(78,1360)\\fad(180,180)}}{content}')
        srt.append(f'{index + 1}\n{stamp(start)} --> {stamp(end)}\n{caption}\n')
    (folder / 'subtitles.ass').write_text('\n'.join(lines), encoding='utf-8-sig')
    (folder / 'captions.srt').write_text('\n'.join(srt), encoding='utf-8')


def render(episode, out, ffmpeg):
    folder = out / episode['slug']
    subtitles(episode, folder)
    image = out / 'assets' / (episode['slug'] + '.png')
    vf = (
        "scale=2160:3840,zoompan=z='1+0.00009*on':x='iw/2-iw/zoom/2':"
        "y='ih/2-ih/zoom/2':d=720:s=1080x1920:fps=24,"
        "drawbox=x=0:y=1220:w=iw:h=700:color=0x07121d@0.72:t=fill,"
        "drawbox=x=0:y=0:w=iw:h=180:color=0x07121d@0.50:t=fill,"
        "ass=subtitles.ass"
    )
    command = [ffmpeg, '-hide_banner', '-loglevel', 'error', '-y', '-i', str(image.resolve()),
               '-vf', vf, '-frames:v', '720', '-an', '-c:v', 'libx264', '-preset', 'veryfast',
               '-crf', '21', '-pix_fmt', 'yuv420p', '-threads', '4', '-movflags', '+faststart', 'video.mp4']
    with (folder / 'render.log').open('w', encoding='utf-8') as log:
        subprocess.run(command, cwd=folder, stdout=log, stderr=log, check=True)
    subprocess.run([ffmpeg, '-hide_banner', '-loglevel', 'error', '-y', '-ss', '1',
                    '-i', 'video.mp4', '-frames:v', '1', 'poster.jpg'], cwd=folder, check=True)
    # Decode the whole output; a file existing is not enough verification.
    subprocess.run([ffmpeg, '-hide_banner', '-loglevel', 'error', '-i', 'video.mp4',
                    '-f', 'null', '-'], cwd=folder, check=True, stdout=subprocess.DEVNULL)
    probe = subprocess.run([ffmpeg, '-hide_banner', '-i', 'video.mp4'], cwd=folder,
                           capture_output=True, text=True).stderr
    if '1080x1920' not in probe or not re.search(r'Duration: 00:00:30\.00', probe):
        raise ValueError(f'Unexpected output dimensions or duration: {folder}')
    (folder / 'verification.json').write_text(json.dumps({
        'decoded_all_frames': True, 'width': 1080, 'height': 1920,
        'duration_seconds': 30, 'fps': 24, 'codec': 'H.264', 'audio': False,
    }, indent=2), encoding='utf-8')


def exports(episode, out):
    folder = out / episode['slug']
    post = episode['post']
    body = post['body'] + '\n\nVisuals: AI-generated illustration; not real footage.\n\nSource context: ' + episode['source']['source_url']
    for filename, value in [('title.txt', post['title']), ('body.txt', body),
                            ('video_script.txt', post['video_script'])]:
        (folder / filename).write_text(value + '\n', encoding='utf-8')
    # Keep the initial exported draft consistent with the latest revision.
    draft = Path(episode['draft_folder'])
    if draft.exists():
        record = json.loads((draft / 'post.json').read_text(encoding='utf-8'))
        record.update(post=post, review_status='needs_review', generation=episode['generation'])
        (draft / 'post.json').write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
        for filename, value in [('title.txt', post['title']), ('body.txt', body),
                                ('short_post.txt', post['short_post']), ('video_script.txt', post['video_script'])]:
            (draft / filename).write_text(value + '\n', encoding='utf-8')
        (draft / 'preview.md').write_text('# ' + post['title'] + '\n\n' + body +
                                        '\n\n## Video script\n\n' + post['video_script'] +
                                        '\n\nStatus: needs human review; not published.\n', encoding='utf-8')
    review_notes = '\n'.join('- ' + claim for claim in post['claims_to_verify']) or 'No unresolved factual assertion flagged; human review still required.'
    (folder / 'review.md').write_text(f"# {post['title']}\n\n{body}\n\n## Topic evidence\n\n{episode['source']['signal']}\n\nSource date: {episode['source']['source_date']}\n\n## Caption script\n\n" +
                                      '\n'.join(f'{i + 1}. {caption}' for i, caption in enumerate(post['captions'])) +
                                      '\n\n## Review notes\n\n' + review_notes + '\n', encoding='utf-8')


def review_page(episodes, out):
    cards = []
    for i, episode in enumerate(episodes, 1):
        slug, post, source = episode['slug'], episode['post'], episode['source']
        paragraphs = ''.join('<p>' + html.escape(part) + '</p>' for part in post['body'].split('\n\n'))
        checks = ''.join('<li>' + html.escape(item) + '</li>' for item in post['claims_to_verify'])
        cards.append(f'''<article id="{slug}"><div class="video"><video controls preload="metadata" poster="{slug}/poster.jpg" src="{slug}/video.mp4" playsinline></video></div>
<div class="copy"><span class="eyebrow">DRAFT {i:02d} / {html.escape(episode['label'])}</span><h2>{html.escape(post['title'])}</h2>{paragraphs}
<div class="signal">{html.escape(source['signal'])}<br>来源日期：{html.escape(source['source_date'])}</div>
<div class="actions"><a href="{slug}/video.mp4" download>下载 MP4</a><a href="{slug}/title.txt">标题</a><a href="{slug}/body.txt">正文</a><a href="{slug}/captions.srt">字幕</a></div>
<details><summary>来源与待核实项</summary><p><a href="{html.escape(source['source_url'])}" target="_blank" rel="noopener">查看原始来源</a></p><ul>{checks or '<li>模型未标记额外事实；仍需人工审阅。</li>'}</ul><p>插画为艺术化合成，不代表真实地点的精确布局。</p></details></div></article>''')
    page = '''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>China Travel / 本地审阅</title><style>
*{box-sizing:border-box}body{margin:0;background:#0b1721;color:#edf0ed;font:16px/1.7 "Segoe UI",sans-serif}main{max-width:1240px;margin:auto;padding:64px 36px}.eyebrow{font-size:12px;letter-spacing:2px;color:#a9d9cf}h1{font:normal clamp(36px,5vw,64px)/1.12 Georgia,serif;max-width:800px;margin:20px 0}header p{max-width:690px;color:#b5c2ca}.badge{display:inline-block;border:1px solid #5e786e;color:#d8ebc0;padding:5px 13px;border-radius:30px;font-size:13px}.nav{display:flex;gap:24px;flex-wrap:wrap;padding:22px 0 38px}.nav a{color:#d8ebc0;text-decoration:none;border-bottom:1px solid #577365}article{display:grid;grid-template-columns:340px 1fr;gap:52px;padding:44px 0;border-top:1px solid #29404c}.video video{display:block;width:100%;aspect-ratio:9/16;border-radius:16px;background:#101e29}.copy{padding:10px 0}h2{font:normal 32px/1.25 Georgia,serif;margin:16px 0 24px}.copy p{color:#ccd5d9}.signal{border-left:2px solid #b9d6a7;padding-left:16px;font-size:13px;color:#a7b8c1;margin-top:26px}.actions{display:flex;gap:12px;flex-wrap:wrap;margin:24px 0}.actions a{background:#d5e7be;color:#112332;padding:8px 15px;text-decoration:none;border-radius:5px;font-size:13px}details{font-size:14px;color:#b5c2ca}details a{color:#9ed4d8}footer{border-top:1px solid #29404c;margin-top:40px;padding-top:24px;color:#8aa0ab;font-size:13px}@media(max-width:750px){main{padding:32px 20px}article{grid-template-columns:1fr;gap:24px}.video{max-width:340px;margin:auto}h2{font-size:28px}}
</style><main><header><span class="eyebrow">CHINA TRAVEL / REVIEW ROOM</span><h1>Three ways to see<br>a different China.</h1><span class="badge">本地审阅 · 尚未发布</span><p>三条英文讨论短视频，每条 30 秒。原创 AI 插画、缓慢镜头运动、英文字幕；无配音和音乐。标题与正文可单独查看。</p></header><nav class="nav"><a href="#shanghai">01 上海夜游</a><a href="#chongqing">02 重庆城市景观</a><a href="#citywalk">03 慢节奏 CityWalk</a></nav>'''
    page += '\n'.join(cards) + '<footer>文字：本机 Ollama / Qwen 2.5 3B，附人工编辑。画面：内置 ImageGen 原创插画。研究日期：2026-10-12。新闻报道及持续性趋势不等于实时热搜；视频不是实拍。发布前请审阅内容及目标社区规则。</footer></main></html>'
    (out / 'review.html').write_text(page, encoding='utf-8')


def run(args):
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    episodes = [json.loads(path.read_text(encoding='utf-8')) for path in
                [args.out / slug / 'episode.json' for slug in ('shanghai', 'chongqing', 'citywalk')]]
    for episode in episodes:
        print('Rendering: ' + episode['slug'], flush=True)
        exports(episode, args.out)
        if not args.skip_video:
            render(episode, args.out, ffmpeg)
    review_page(episodes, args.out)
    print('Review page: ' + str(args.out / 'review.html'), flush=True)


if __name__ == '__main__':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, default=ROOT / 'work' / 'review-20261012')
    parser.add_argument('--skip-video', action='store_true', help='Refresh copy and HTML without encoding again')
    run(parser.parse_args())
