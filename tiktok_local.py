"""Visible TikTok Studio browser workflow; no private APIs or cookie extraction."""
import hashlib
import json
from pathlib import Path
import re
import subprocess
import uuid

from city_pipeline import ROOT
from reddit_local import Publisher, DISCLOSURE
from locations import city_from_folder, location


def make_video(folder):
    from PIL import Image, ImageFilter
    import imageio_ffmpeg
    folder = Path(folder).resolve()
    if not folder.is_relative_to((ROOT / 'city_posts').resolve()):
        raise ValueError('Invalid city folder.')
    city = city_from_folder(folder)
    if not city:
        raise ValueError('City location is missing.')
    images = sorted(folder.glob('*.png'))
    if not images:
        raise ValueError('No illustrations found.')
    signature = hashlib.sha256(b''.join(p.read_bytes() for p in images)).hexdigest()
    work = ROOT / 'work' / 'tiktok_exports' / signature
    work.mkdir(parents=True, exist_ok=True)
    video = work / 'travel.mp4'
    if not video.exists():
        for i, path in enumerate(images):
            with Image.open(path) as source:
                source = source.convert('RGB')
                bg = source.copy()
                factor = max(1080 / bg.width, 1920 / bg.height)
                bg = bg.resize((round(bg.width*factor), round(bg.height*factor)))
                x, y = (bg.width-1080)//2, (bg.height-1920)//2
                bg = bg.crop((x, y, x+1080, y+1920)).filter(ImageFilter.GaussianBlur(35))
                source.thumbnail((1000, 1600))
                bg.paste(source, ((1080-source.width)//2, (1920-source.height)//2))
                bg.save(work / f'frame-{i:02}.png')
        command = [imageio_ffmpeg.get_ffmpeg_exe(), '-y', '-framerate', '1/4', '-i', str(work / 'frame-%02d.png'),
                   '-vf', 'fps=30', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-movflags', '+faststart', str(video)]
        subprocess.run(command, check=True, capture_output=True)
    en, _ = location(city)
    foods = folder / '美食推荐.txt'
    food_text = foods.read_text(encoding='utf-8') if foods.exists() else ''
    if food_text.startswith('本次来源'):
        food_text = ''
    credits = folder / 'image_credits.txt'
    caption = '\n\n'.join(x for x in [DISCLOSURE, en, (folder/'title.txt').read_text(encoding='utf-8').strip(), food_text,
                                       credits.read_text(encoding='utf-8') if credits.exists() else '', '#ChinaTravel #AIArt'] if x)
    if len(caption) > 3800:
        raise ValueError('TikTok caption is too long. Shorten the draft while preserving attribution.')
    (work / 'caption.txt').write_text(caption, encoding='utf-8')
    return {'folder': str(folder), 'video': video, 'body': caption, 'fingerprint': hashlib.sha256((signature+caption).encode()).hexdigest(), 'subreddit': 'tiktok'}


class TikTokPublisher(Publisher):
    def db(self):
        # The shared ledger stores platform-qualified hashes, so Reddit and TikTok do not collide.
        return super().db()

    def browser(self):
        if self.context:
            try:
                self.context.pages
                return
            except Exception:
                self.context = None
        from playwright.sync_api import sync_playwright
        self.playwright = sync_playwright().start()
        self.context = self.playwright.chromium.launch_persistent_context(
            str(ROOT / 'work' / 'tiktok-chrome-profile'), channel='chrome', headless=False,
            viewport={'width':1280, 'height':900})
        self.page = self.context.pages[0] if self.context.pages else self.context.new_page()

    def worker(self):
        while True:
            action, data = self.commands.get()
            try:
                self.browser()
                if action == 'login':
                    self.prepared = None
                    self.page.goto('https://www.tiktok.com/login', wait_until='domcontentloaded')
                    self.update(status='login', prepared_id='', message='请在专用 Chrome 登录 TikTok，完成后回主页准备视频。')
                elif action == 'prepare':
                    self.prepare(data)
                elif action == 'publish':
                    self.publish(data)
            except Exception as error:
                self.update(status='error', prepared_id='', message=str(error))
            finally:
                self.commands.task_done()

    def prepare(self, data):
        item = make_video(data['folder'])
        self.duplicate(item)
        self.page.goto('https://www.tiktok.com/tiktokstudio/upload', wait_until='domcontentloaded')
        upload = self.page.locator('input[type="file"]')
        upload.first.wait_for(state='attached', timeout=30000)
        if upload.count() != 1:
            raise ValueError('无法识别 TikTok 上传入口，请确认专用窗口登录及账号可上传。')
        upload.set_input_files(str(item['video']))
        editor = self.page.locator('[contenteditable="true"], textarea').filter(visible=True)
        editor.first.wait_for(state='visible', timeout=60000)
        if editor.count() != 1:
            raise ValueError('无法识别唯一的 TikTok 说明编辑框；请在 Chrome 手动完成，不会自动提交。')
        editor.fill(item['body'])
        ai_label = self.page.get_by_role('switch', name=re.compile('AI.generated', re.I))
        if ai_label.count() != 1:
            ai_label = self.page.get_by_role('checkbox', name=re.compile('AI.generated', re.I))
        if ai_label.count() != 1:
            raise ValueError('未识别 TikTok 的 AI 内容标记。请在 Chrome 手动开启 AI 标签，并手动发布；不会自动提交。')
        ai_label.check()
        self.prepared = {'id':uuid.uuid4().hex, 'item':item, 'editor':editor, 'ai_label':ai_label}
        self.record(item, 'prepared')
        self.update(status='prepared', prepared_id=self.prepared['id'], folder=str(Path(item['folder']).relative_to(ROOT/'city_posts')).replace('\\','/'),
                    message='视频已上传，说明已填写，AI 标签已开启。请核对账号、预览及可见范围；点击主页发布按钮才提交。')

    def publish(self, data):
        prepared = self.prepared
        if not prepared or data['prepared_id'] != prepared['id']:
            raise ValueError('请先准备当前视频。')
        item = prepared['item']
        self.duplicate(item)
        from urllib.parse import urlparse
        parsed = urlparse(self.page.url)
        if parsed.hostname != 'www.tiktok.com' or not parsed.path.startswith('/tiktokstudio/upload'):
            raise ValueError('当前 Chrome 页面不是已准备的 TikTok 上传页。')
        actual = prepared['editor'].inner_text()
        if re.sub(r'\s+',' ',actual).strip() != re.sub(r'\s+',' ',item['body']).strip() or not prepared['ai_label'].is_checked():
            raise ValueError('说明或 AI 标签发生变化，请重新准备。')
        button = self.page.get_by_role('button', name=re.compile(r'^(Post|发布)$'))
        if button.count() != 1 or not button.is_enabled():
            raise ValueError('TikTok 发布按钮不可用，请检查上传状态和账号提示。')
        self.record(item, 'sending')
        try:
            button.click()
            self.page.wait_for_url(re.compile(r'https://www\.tiktok\.com/@[^/]+/video/\d+'), timeout=30000)
            self.record(item, 'published', self.page.url)
            self.update(status='published', prepared_id='', url=self.page.url, message='TikTok 已发布，链接已记录。')
        except Exception:
            self.record(item, 'unknown')
            raise ValueError('已点击发布，但没有确认视频链接。请在 TikTok 检查结果，不会自动重试。') from None
        finally:
            self.prepared = None


publisher = TikTokPublisher()
