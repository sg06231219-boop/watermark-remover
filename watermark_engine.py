#!/usr/bin/env python3
"""
视频去水印引擎 - 高质量专业版
支持 5 种算法 · 3 档画质 · 批量帧处理
"""

import os, sys, subprocess, tempfile, shutil, re, time, json
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading

# Windows 终端 UTF-8
if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

# ─── 可选依赖 ───────────────────────────────────────────────
try:
    import cv2
    import numpy as np
    HAS_OPENCV = True
except ImportError:
    HAS_OPENCV = False

try:
    from PIL import Image
    HAS_PIL = True
except ImportError:
    HAS_PIL = False


# ═══════════════════════════════════════════════════════════════
# 引擎核心
# ═══════════════════════════════════════════════════════════════

class WatermarkEngine:
    """视频去水印处理引擎"""

    # 画质预设 → FFmpeg 参数
    QUALITY = {
        'fast':     {'crf': 23, 'preset': 'veryfast', 'bitrate': '128k'},
        'balanced': {'crf': 20, 'preset': 'medium',   'bitrate': '192k'},
        'high':     {'crf': 17, 'preset': 'slow',     'bitrate': '256k'},
    }

    # 算法元信息
    ALGORITHMS = {
        'delogo':       {'name': '智能融合', 'icon': '🔧', 'desc': '边缘色彩融合，适合矩形纯色水印',      'engine': 'ffmpeg',  'speed': '⚡极速'},
        'blur':         {'name': '高斯模糊', 'icon': '🌀', 'desc': '自然模糊水印区域，过渡柔和',            'engine': 'ffmpeg',  'speed': '⚡极速'},
        'mosaic':       {'name': '马赛克',   'icon': '🔲', 'desc': '像素化处理，简单粗暴不留痕',            'engine': 'ffmpeg',  'speed': '⚡极速'},
        'inpaint_telea':{'name': '快速修复', 'icon': '🖌️', 'desc': 'Telea 算法智能填充，效果自然',        'engine': 'opencv',  'speed': '🐢较慢'},
        'inpaint_ns':   {'name': '深度修复', 'icon': '✨', 'desc': 'Navier-Stokes 算法，最佳画质',        'engine': 'opencv',  'speed': '🐢慢'},
    }

    def __init__(self):
        self.ffmpeg = self._find_ffmpeg()
        self._lock = threading.Lock()

    # ─── 工具方法 ─────────────────────────────────────────

    def _find_ffmpeg(self):
        """查找 FFmpeg"""
        try:
            import imageio_ffmpeg
            return imageio_ffmpeg.get_ffmpeg_exe()
        except Exception:
            pass
        return shutil.which('ffmpeg') or 'ffmpeg'

    def _get_video_info(self, path):
        """获取视频元信息"""
        info = {'width': 1920, 'height': 1080, 'duration': 0, 'fps': 30, 'has_audio': False}
        try:
            r = subprocess.run(
                [self.ffmpeg, '-i', path, '-hide_banner'],
                capture_output=True, text=True, timeout=20
            )
            out = r.stderr + r.stdout

            m = re.search(r'(\d{2,5})x(\d{2,5})', out)
            if m:
                info['width'] = int(m.group(1))
                info['height'] = int(m.group(2))

            dm = re.search(r'Duration:\s*(\d+):(\d+):(\d+\.?\d*)', out)
            if dm:
                info['duration'] = int(dm.group(1)) * 3600 + int(dm.group(2)) * 60 + float(dm.group(3))

            fm = re.search(r'(\d+(?:\.\d+)?)\s*fps', out)
            if fm:
                info['fps'] = float(fm.group(1))

            info['has_audio'] = 'Audio:' in out

        except Exception:
            pass
        return info

    def _region_to_pixels(self, region, info):
        """将比例坐标转为像素坐标"""
        W, H = info['width'], info['height']
        x = max(0, min(int(region.get('x_frac', 0.72) * W), W - 1))
        y = max(0, min(int(region.get('y_frac', 0.89) * H), H - 1))
        w = max(1, min(int(region.get('w_frac', 0.28) * W), W - x))
        h = max(1, min(int(region.get('h_frac', 0.11) * H), H - y))
        return {'x': x, 'y': y, 'w': w, 'h': h}

    # ─── 生成预览帧 ─────────────────────────────────────

    def generate_preview(self, video_path, seek_pct=0.1):
        """从视频提取一帧用于预览"""
        info = self._get_video_info(video_path)
        seek_sec = min(info['duration'] * seek_pct, 5) if info['duration'] > 0 else 0

        tmp = tempfile.mktemp(suffix='.jpg')
        cmd = [
            self.ffmpeg, '-y', '-ss', str(seek_sec),
            '-i', video_path, '-vframes', '1', '-q:v', '3', tmp
        ]
        subprocess.run(cmd, capture_output=True, timeout=30)

        if os.path.exists(tmp) and os.path.getsize(tmp) > 500:
            return tmp, info
        return None, info

    # ═══════════════════════════════════════════════════════════
    # FFmpeg 算法（快速，直接操作流）
    # ═══════════════════════════════════════════════════════════

    def _ffmpeg_filter(self, algorithm, region, quality):
        """生成 FFmpeg 滤镜字符串"""
        q = self.QUALITY[quality]
        x, y, w, h = region['x'], region['y'], region['w'], region['h']

        if algorithm == 'delogo':
            band = 4 if quality == 'high' else 2
            return f'delogo=x={x}:y={y}:w={w}:h={h}:show=0:band={band}'

        elif algorithm == 'blur':
            # 自适应模糊核大小
            ks = max(w, h) // 2
            ks = max(2, ks // 2 * 2 + 1)  # 保证奇数
            return (
                f'split[a][b];'
                f'[a]crop={w}:{h}:{x}:{y},'
                f'gblur=sigma={ks//10+1}:steps=3[blurred];'
                f'[b][blurred]overlay={x}:{y}'
            )

        elif algorithm == 'mosaic':
            bs = max(8, min(32, max(w, h) // 8))
            return (
                f'split[a][b];'
                f'[a]crop={w}:{h}:{x}:{y},'
                f'scale={max(2,w//bs)}:{max(2,h//bs)}:flags=neighbor,'
                f'scale={w}:{h}:flags=neighbor[mosaic];'
                f'[b][mosaic]overlay={x}:{y}'
            )

        return 'null'

    def _process_ffmpeg(self, input_path, output_path, region, algorithm, quality, progress_cb):
        """FFmpeg 流式处理（delogo/blur/mosaic）"""
        info = self._get_video_info(input_path)
        q = self.QUALITY[quality]
        filt = self._ffmpeg_filter(algorithm, region, quality)

        tmp = output_path + '.tmp.mp4'
        cmd = [
            self.ffmpeg, '-y', '-i', input_path,
            '-vf', filt,
            '-c:v', 'libx264', '-preset', q['preset'], '-crf', str(q['crf']),
            '-c:a', 'aac', '-b:a', q['bitrate'],
            '-movflags', '+faststart',
            '-progress', 'pipe:1', '-nostats',
            tmp
        ]

        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            universal_newlines=True
        )

        duration = info.get('duration', 0)
        last_pct = 0
        for line in proc.stdout:
            m = re.search(r'out_time_us=(\d+)', line)
            if m and duration > 0:
                t_us = int(m.group(1))
                pct = min(95, 10 + int(85 * (t_us / 1_000_000) / duration))
                if pct > last_pct + 1:
                    last_pct = pct
                    if progress_cb:
                        progress_cb(pct, '编码中...')

        proc.wait()
        if proc.returncode != 0:
            raise RuntimeError(f'{algorithm} 处理失败 (exit code {proc.returncode})')

        # 保留原音频
        self._copy_audio(tmp, input_path, output_path)
        self._clean(tmp)
        if progress_cb:
            progress_cb(100, '完成')

    # ═══════════════════════════════════════════════════════════
    # OpenCV 算法（逐帧修复，高质量）
    # ═══════════════════════════════════════════════════════════

    def _process_opencv(self, input_path, output_path, region, algorithm, quality, progress_cb):
        """OpenCV 逐帧 inpaint"""
        if not HAS_OPENCV:
            raise RuntimeError('需要安装 OpenCV: pip install opencv-python')

        info = self._get_video_info(input_path)
        q = self.QUALITY[quality]
        w, h, fps, duration = info['width'], info['height'], info['fps'], info['duration']

        # 生成 mask
        mask = np.zeros((h, w), dtype=np.uint8)
        rx, ry, rw, rh = region['x'], region['y'], region['w'], region['h']
        # 略微扩展 mask 以获得更好的边缘修复
        pad = 6 if quality == 'high' else 3
        my1 = max(0, ry - pad)
        my2 = min(h, ry + rh + pad)
        mx1 = max(0, rx - pad)
        mx2 = min(w, rx + rw + pad)
        mask[my1:my2, mx1:mx2] = 255

        # 内核模糊 mask（柔和边缘）
        ks = max(3, pad * 2 + 1)
        mask = cv2.GaussianBlur(mask, (ks, ks), 0)

        inpaint_flag = cv2.INPAINT_NS if algorithm == 'inpaint_ns' else cv2.INPAINT_TELEA
        radius = 10 if quality == 'high' else 5

        # 帧临时目录
        tdir = tempfile.mkdtemp(prefix='wm_frames_')
        frame_pattern = os.path.join(tdir, 'frame_%08d.png')

        if progress_cb:
            progress_cb(5, '提取视频帧...')

        # 用 FFmpeg 提取所有帧为 PNG（无损）
        cmd_extract = [
            self.ffmpeg, '-y', '-i', input_path,
            '-vsync', '0', frame_pattern
        ]
        subprocess.run(cmd_extract, capture_output=True, timeout=600)

        frames = sorted(Path(tdir).glob('frame_*.png'))
        total = len(frames)
        if total == 0:
            raise RuntimeError('无法提取视频帧')

        if progress_cb:
            progress_cb(10, f'修复 {total} 帧...')

        # 并行修复帧
        workers = min(8, os.cpu_count() or 4)
        repaired = 0

        def repair_one(frame_path):
            img = cv2.imread(str(frame_path))
            if img is None:
                return frame_path
            if img.shape[:2] != (h, w):
                img = cv2.resize(img, (w, h))
            result = cv2.inpaint(img, mask, radius, inpaint_flag)
            cv2.imwrite(str(frame_path), result, [cv2.IMWRITE_PNG_COMPRESSION, 1])
            return frame_path

        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(repair_one, f): f for f in frames}
            for i, fut in enumerate(as_completed(futures)):
                repaired += 1
                if i % max(1, total // 40) == 0:
                    pct = 10 + int(78 * repaired / total)
                    if progress_cb:
                        progress_cb(pct, f'修复 {repaired}/{total} 帧')

        if progress_cb:
            progress_cb(88, '合成视频...')

        # FFmpeg 合成 + 原音频
        tmp = output_path + '.tmp.mp4'
        cmd_merge = [
            self.ffmpeg, '-y',
            '-framerate', str(fps), '-i', frame_pattern,
            '-i', input_path,
            '-map', '0:v', '-map', '1:a?',
            '-c:v', 'libx264', '-preset', q['preset'],
            '-crf', str(q['crf']),
            '-c:a', 'copy',
            '-shortest',
            '-movflags', '+faststart',
            tmp
        ]
        subprocess.run(cmd_merge, capture_output=True, timeout=600)

        self._clean(tdir)
        if os.path.exists(tmp) and os.path.getsize(tmp) > 1024:
            shutil.move(tmp, output_path)
        else:
            raise RuntimeError('视频合成失败')

        if progress_cb:
            progress_cb(100, '完成')

    # ─── 音频处理 ────────────────────────────────────────

    def _copy_audio(self, processed_video, original, output):
        """合并原音频到处理后视频"""
        cmd = [
            self.ffmpeg, '-y',
            '-i', processed_video, '-i', original,
            '-c:v', 'copy', '-c:a', 'aac', '-b:a', '192k',
            '-map', '0:v:0', '-map', '1:a?',
            '-movflags', '+faststart',
            '-shortest', output
        ]
        r = subprocess.run(cmd, capture_output=True, timeout=120)
        if r.returncode != 0 or not os.path.exists(output) or os.path.getsize(output) < 1024:
            shutil.copy(processed_video, output)

    def _clean(self, path):
        """安全清理"""
        try:
            if os.path.isdir(path):
                shutil.rmtree(path)
            elif os.path.isfile(path):
                os.unlink(path)
        except Exception:
            pass

    # ═══════════════════════════════════════════════════════════
    # 主入口
    # ═══════════════════════════════════════════════════════════

    def process(self, input_path, output_path, region, algorithm='delogo',
                quality='balanced', progress_callback=None):
        """
        处理视频去水印。

        Args:
            region: {'x_frac': float, 'y_frac': float, 'w_frac': float, 'h_frac': float}
            algorithm: 'delogo' | 'blur' | 'mosaic' | 'inpaint_telea' | 'inpaint_ns'
            quality: 'fast' | 'balanced' | 'high'
        """
        os.makedirs(os.path.dirname(os.path.abspath(output_path)) or '.', exist_ok=True)

        info = self._get_video_info(input_path)
        px_region = self._region_to_pixels(region, info)

        algo_info = self.ALGORITHMS.get(algorithm, self.ALGORITHMS['delogo'])

        if algo_info['engine'] == 'ffmpeg':
            self._process_ffmpeg(input_path, output_path, px_region, algorithm, quality, progress_callback)
        else:
            self._process_opencv(input_path, output_path, px_region, algorithm, quality, progress_callback)

        return output_path


# ═══════════════════════════════════════════════════════════════
# 命令行模式
# ═══════════════════════════════════════════════════════════════

if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description='🎬 视频去水印工具 - 专业版')
    parser.add_argument('input', help='输入视频路径')
    parser.add_argument('output', help='输出视频路径')
    parser.add_argument('--algo', default='delogo',
                        choices=['delogo', 'blur', 'mosaic', 'inpaint_telea', 'inpaint_ns'])
    parser.add_argument('--quality', default='balanced',
                        choices=['fast', 'balanced', 'high'])
    parser.add_argument('--x', type=float, default=0.72, help='水印 X 起点比例 (0~1)')
    parser.add_argument('--y', type=float, default=0.89, help='水印 Y 起点比例 (0~1)')
    parser.add_argument('--w', type=float, default=0.28, help='水印宽度比例 (0~1)')
    parser.add_argument('--h', type=float, default=0.11, help='水印高度比例 (0~1)')
    args = parser.parse_args()

    print(f'\n🧹 视频去水印工具 - 专业版')
    print(f'{"─"*40}')
    print(f'  输入: {args.input}')
    print(f'  输出: {args.output}')
    print(f'  算法: {args.algo}')
    print(f'  画质: {args.quality}')
    print(f'  区域: x={args.x:.0%} y={args.y:.0%} w={args.w:.0%} h={args.h:.0%}')
    print(f'{"─"*40}\n')

    engine = WatermarkEngine()

    def on_progress(pct, msg):
        bar = '█' * (pct // 5) + '░' * (20 - pct // 5)
        print(f'\r  [{bar}] {pct:3d}%  {msg}', end='', flush=True)

    try:
        engine.process(
            args.input, args.output,
            region={'x_frac': args.x, 'y_frac': args.y,
                    'w_frac': args.w, 'h_frac': args.h},
            algorithm=args.algo,
            quality=args.quality,
            progress_callback=on_progress,
        )
        print(f'\n✅ 完成! → {args.output}')
    except Exception as e:
        print(f'\n❌ 失败: {e}')
