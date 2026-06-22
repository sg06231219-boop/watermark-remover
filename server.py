#!/usr/bin/env python3
"""
视频去水印服务 - 专业版
支持链接下载 + 本地上传 · 5 种算法 · 3 档画质
"""

import http.server, socketserver, json, os, sys, threading, tempfile, time, shutil, cgi, re, urllib.parse
from pathlib import Path
from io import BytesIO

from watermark_engine import WatermarkEngine
from ai_assistant import ZhipuAIAssistant

# ─── 配置 ──────────────────────────────────────────────────
PORT = 8081
BASE_DIR = Path(__file__).parent
OUT_DIR = BASE_DIR / 'output'
OUT_DIR.mkdir(exist_ok=True)
UPLOAD_DIR = BASE_DIR / 'uploads'
UPLOAD_DIR.mkdir(exist_ok=True)

if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

# ─── 全局状态 ──────────────────────────────────────────────
_lock = threading.Lock()
state = {'step': 'ready', 'msg': '就绪', 'pct': 0, 'result': None, 'err': None}
videoInfo_for_ai = {}  # 供 AI 端点使用的视频元信息

engine = WatermarkEngine()
ai = ZhipuAIAssistant()

PREVIEW_DIR = BASE_DIR / 'previews'
PREVIEW_DIR.mkdir(exist_ok=True)


# ═══════════════════════════════════════════════════════════════
# 工具函数
# ═══════════════════════════════════════════════════════════════

def get_ytdlp():
    """查找 yt-dlp"""
    for p in [
        Path.home() / 'AppData/Local/Python/pythoncore-3.14-64/Scripts/yt-dlp.exe',
        Path.home() / 'AppData/Local/Programs/Python/Python314/Scripts/yt-dlp.exe',
    ]:
        if p.exists():
            return str(p)
    return shutil.which('yt-dlp') or 'yt-dlp'


def download_video(url, out_path):
    """yt-dlp 下载视频"""
    global state
    with _lock:
        state.update(step='downloading', msg='解析链接...', pct=3)

    ytdlp = get_ytdlp()
    cmd = [ytdlp, '-f', 'best[ext=mp4]/best', '--no-playlist',
           '-o', out_path, '--no-progress', url]

    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if r.returncode == 0 and os.path.exists(out_path) and os.path.getsize(out_path) > 1024:
            return True
        with _lock:
            state['err'] = f'下载失败: {r.stderr[-150:] if r.stderr else "未知错误"}'
    except subprocess.TimeoutExpired:
        with _lock:
            state['err'] = '下载超时（超过 5 分钟）'
    except FileNotFoundError:
        with _lock:
            state['err'] = '未找到 yt-dlp，请执行: pip install yt-dlp'
    except Exception as e:
        with _lock:
            state['err'] = f'下载异常: {str(e)}'
    return False


# ═══════════════════════════════════════════════════════════════
# 处理入口
# ═══════════════════════════════════════════════════════════════

def process_url_task(url, region, algorithm, quality, out_path):
    """URL 模式：下载 → 去水印"""
    global state
    tmp = tempfile.mktemp(suffix='.mp4')

    if not download_video(url, tmp):
        return

    if state.get('err'):
        return

    def on_progress(pct, msg):
        with _lock:
            state.update(pct=pct, msg=msg)

    try:
        global videoInfo_for_ai
        info = engine._get_video_info(tmp)
        with _lock:
            videoInfo_for_ai = info
            state.update(step='processing', msg='去水印中...', pct=10)

        engine.process(tmp, out_path, region, algorithm, quality, on_progress)

        with _lock:
            state.update(step='done', msg='完成', pct=100, result=out_path)

    except Exception as e:
        with _lock:
            state.update(step='done', msg='完成', pct=100, err=str(e))
    finally:
        try:
            os.unlink(tmp)
        except Exception:
            pass


def process_file_task(file_path, region, algorithm, quality, out_path):
    """文件模式：直接去水印"""
    global state

    def on_progress(pct, msg):
        with _lock:
            state.update(pct=pct, msg=msg)

    try:
        global videoInfo_for_ai
        info = engine._get_video_info(file_path)
        with _lock:
            videoInfo_for_ai = info
            state.update(step='processing', msg='分析视频...', pct=5)

        engine.process(file_path, out_path, region, algorithm, quality, on_progress)

        with _lock:
            state.update(step='done', msg='完成', pct=100, result=out_path)

    except Exception as e:
        with _lock:
            state.update(step='done', msg='完成', pct=100, err=str(e))


# ═══════════════════════════════════════════════════════════════
# 解析 multipart/form-data（手动，避免内存问题）
# ═══════════════════════════════════════════════════════════════

def parse_multipart(rfile, content_type, max_size=2 * 1024 ** 3):
    """解析 multipart/form-data，返回 {name: bytes|dict}"""
    # 提取 boundary
    boundary = None
    for part in content_type.split(';'):
        part = part.strip()
        if part.lower().startswith('boundary='):
            boundary = part.split('=', 1)[1].strip('" \'')
            break
    if not boundary:
        return {}

    boundary = boundary.encode()
    data = rfile.read()
    result = {}

    # 按 boundary 分割
    parts = data.split(b'--' + boundary)
    for part in parts:
        if part in (b'', b'--', b'--\r\n'):
            continue

        # 剥离末尾换行
        part = part.lstrip(b'\r\n')

        # 分离头部和内容
        idx = part.find(b'\r\n\r\n')
        if idx < 0:
            continue
        header_bytes = part[:idx]
        body = part[idx + 4:]

        # 去除尾部 boundary 标记
        if body.endswith(b'\r\n'):
            body = body[:-2]

        # 解析 header
        headers = header_bytes.decode('utf-8', errors='replace')
        name = None
        filename = None
        for line in headers.split('\r\n'):
            if line.lower().startswith('content-disposition'):
                # 提取 name
                nm = re.search(r'name="([^"]*)"', line)
                if nm:
                    name = nm.group(1)
                # 提取 filename
                fn = re.search(r'filename="([^"]*)"', line)
                if fn:
                    filename = fn.group(1)

        if name:
            if filename:
                result[name] = {
                    'filename': filename,
                    'data': body,
                }
            else:
                result[name] = body

    return result


# ═══════════════════════════════════════════════════════════════
# HTTP Handler
# ═══════════════════════════════════════════════════════════════

class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(BASE_DIR), **kwargs)

    def _cors(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET,POST,OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')

    def _json(self, obj, code=200):
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self._cors()
        self.end_headers()
        self.wfile.write(json.dumps(obj, ensure_ascii=False).encode('utf-8'))

    def do_OPTIONS(self):
        self.send_response(200)
        self._cors()
        self.end_headers()

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        # ── 状态轮询 ──
        if path == '/api/status':
            self._json(dict(state))

        # ── 下载结果 ──
        elif path == '/api/download':
            result = state.get('result')
            if result and os.path.exists(result):
                size = os.path.getsize(result)
                self.send_response(200)
                self.send_header('Content-Type', 'video/mp4')
                self.send_header('Content-Disposition',
                                 f'attachment; filename*=UTF-8\'\'%E5%8E%BB%E6%B0%B4%E5%8D%B0%E8%A7%86%E9%A2%91.mp4')
                self.send_header('Content-Length', str(size))
                self._cors()
                self.end_headers()
                with open(result, 'rb') as f:
                    shutil.copyfileobj(f, self.wfile)
            else:
                self._json({'error': '没有可下载的文件'}, 404)

        # ── 算法列表 ──
        elif path == '/api/algorithms':
            self._json(engine.ALGORITHMS)

        # ── 下载预览帧 ──
        elif path == '/api/preview':
            qs = urllib.parse.parse_qs(parsed.query)
            fname = qs.get('file', [''])[0]
            fpath = PREVIEW_DIR / fname
            if fname and fpath.exists():
                self.send_response(200)
                self.send_header('Content-Type', 'image/jpeg')
                self._cors()
                self.end_headers()
                with open(fpath, 'rb') as ff:
                    shutil.copyfileobj(ff, self.wfile)
            else:
                self._json({'error': '预览不存在'}, 404)

        # ── 静态文件 ──
        else:
            if path in ('/', '/index.html'):
                self.path = '/index.html'
            super().do_GET()

    def do_POST(self):
        global state
        path = urllib.parse.urlparse(self.path).path
        clen = int(self.headers.get('Content-Length', 0))

        # ── 处理 URL 链接 ──
        if path == '/api/process':
            if clen == 0:
                return self._json({'error': '请求体为空'}, 400)

            body = self.rfile.read(clen)
            data = json.loads(body.decode('utf-8'))
            url = data.get('url', '').strip()
            region = data.get('region', {})
            algorithm = data.get('algorithm', 'delogo')
            quality = data.get('quality', 'balanced')

            if not url:
                return self._json({'error': '请输入视频链接'}, 400)

            out_path = str(OUT_DIR / f'clean_{int(time.time())}.mp4')

            with _lock:
                state.update(step='submitted', msg='提交成功', pct=2,
                            result=None, err=None)

            threading.Thread(
                target=process_url_task,
                args=(url, region, algorithm, quality, out_path),
                daemon=True
            ).start()

            self._json({'ok': True, 'msg': '任务已提交'})

        # ── AI 智能检测水印 ──
        elif path == '/api/ai/detect':
            # 接收一帧图片 + 可选视频元信息，用 GLM-4V 检测水印位置
            ctype = self.headers.get('Content-Type', '')
            vinfo = dict(videoInfo_for_ai)  # 复制全局状态
            tmp_img = None

            if 'multipart/form-data' in ctype:
                fields = parse_multipart(self.rfile, ctype)
                files = {k: v for k, v in fields.items() if isinstance(v, dict)}
                if not files:
                    return self._json({'error': '请上传帧图片'}, 400)
                file_info = list(files.values())[0]
                tmp_img = str(PREVIEW_DIR / f'ai_detect_{int(time.time())}.jpg')
                with open(tmp_img, 'wb') as f:
                    f.write(file_info['data'])
                # 从表单字段读取视频信息
                for k, v in fields.items():
                    if k.startswith('video_') and isinstance(v, bytes):
                        try:
                            key = k.replace('video_', '')
                            vinfo[key] = json.loads(v.decode('utf-8'))
                        except:
                            pass
            elif clen > 0:
                body = self.rfile.read(clen)
                tmp_img = str(PREVIEW_DIR / f'ai_detect_{int(time.time())}.jpg')
                with open(tmp_img, 'wb') as f:
                    f.write(body)
            else:
                return self._json({'error': '请上传帧图片'}, 400)

            try:
                result = ai.detect_watermark(tmp_img)
                # 填默认视频信息
                vi = {
                    'width': vinfo.get('width') or 1920,
                    'height': vinfo.get('height') or 1080,
                    'duration': vinfo.get('duration') or 0,
                    'fps': vinfo.get('fps') or 30,
                }
                if result.get('has_watermark'):
                    rec = ai.recommend_algorithm(vi, result)
                    result['recommendation'] = rec
                self._json(result)
            except Exception as e:
                self._json({'error': f'AI检测失败: {str(e)}'}, 500)
            finally:
                if tmp_img:
                    try: os.unlink(tmp_img)
                    except: pass

        # ── AI 算法推荐 ──
        elif path == '/api/ai/recommend':
            if clen == 0:
                return self._json({'error': '请求体为空'}, 400)
            body = self.rfile.read(clen)
            data = json.loads(body.decode('utf-8'))
            video_info = data.get('video_info', {})
            watermark_info = data.get('watermark_info', {})
            try:
                result = ai.recommend_algorithm(video_info, watermark_info)
                self._json(result)
            except Exception as e:
                self._json({'error': f'AI推荐失败: {str(e)}'}, 500)

        # ── AI 生成报告 ──
        elif path == '/api/ai/report':
            if clen == 0:
                return self._json({'error': '请求体为空'}, 400)
            body = self.rfile.read(clen)
            data = json.loads(body.decode('utf-8'))
            try:
                report = ai.generate_report(
                    data.get('video_info', {}),
                    data.get('watermark_info', {}),
                    data.get('params', {}))
                self._json({'report': report})
            except Exception as e:
                self._json({'error': f'报告生成失败: {str(e)}'}, 500)

        # ── 上传本地文件 ──
        elif path == '/api/upload':
            ctype = self.headers.get('Content-Type', '')

            if 'multipart/form-data' not in ctype:
                return self._json({'error': '需要 multipart/form-data'}, 400)

            fields = parse_multipart(self.rfile, ctype)
            files = {k: v for k, v in fields.items() if isinstance(v, dict)}

            if not files:
                return self._json({'error': '未接收到文件'}, 400)

            # 取第一个文件
            file_info = list(files.values())[0]
            filename = file_info.get('filename', 'upload.mp4')
            # 安全检查文件名
            safe_name = re.sub(r'[^\w.\-一-鿿]', '_', filename)
            saved = str(UPLOAD_DIR / f'{int(time.time())}_{safe_name}')
            with open(saved, 'wb') as f:
                f.write(file_info['data'])

            if os.path.getsize(saved) < 512:
                os.unlink(saved)
                return self._json({'error': '文件太小，可能不是有效视频'}, 400)

            # 读取其他表单字段
            region_raw = None
            algorithm = 'delogo'
            quality = 'balanced'

            for k, v in fields.items():
                if isinstance(v, bytes):
                    try:
                        if k == 'region':
                            region_raw = json.loads(v.decode('utf-8'))
                        elif k == 'algorithm':
                            algorithm = v.decode('utf-8')
                        elif k == 'quality':
                            quality = v.decode('utf-8')
                    except Exception:
                        pass

            region = region_raw or {'x_frac': 0.72, 'y_frac': 0.89,
                                    'w_frac': 0.28, 'h_frac': 0.11}

            out_path = str(OUT_DIR / f'clean_{int(time.time())}.mp4')

            with _lock:
                state.update(step='uploaded', msg='上传完成，开始处理',
                            pct=5, result=None, err=None)

            threading.Thread(
                target=process_file_task,
                args=(saved, region, algorithm, quality, out_path),
                daemon=True
            ).start()

            self._json({'ok': True, 'msg': '上传成功，开始处理'})

        # ── 未知路由 ──
        else:
            self._json({'error': f'未知路由: {path}'}, 404)

    def log_message(self, *args):
        pass  # 静默日志


# ═══════════════════════════════════════════════════════════════
# 启动
# ═══════════════════════════════════════════════════════════════

if __name__ == '__main__':
    print()
    print('╔══════════════════════════════════════════════╗')
    print('║       🧹 视频去水印工具 · 专业版             ║')
    print('╠══════════════════════════════════════════════╣')
    print(f'║  算法: 5 种 (融合/模糊/马赛克/AI修复)       ║')
    print(f'║  画质: 3 档 (快速/标准/高质量)              ║')
    print(f'║  AI:   智谱 GLM-4V 智能检测 + 推荐         ║')
    print(f'║  上传: 链接解析 + 本地上传                  ║')
    print(f'║  地址: http://localhost:{PORT}               ║')
    print('╚══════════════════════════════════════════════╝')
    print()

    try:
        import webbrowser
        webbrowser.open(f'http://localhost:{PORT}', new=2)
    except Exception:
        pass

    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(('0.0.0.0', PORT), Handler) as httpd:
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print('\n👋 停止服务')
