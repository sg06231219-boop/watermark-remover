#!/usr/bin/env python3
"""
智谱 AI 水印分析助手
GLM-4V 视觉识别 + GLM-4 文本推理 → 自动定位 · 算法推荐 · 效果评估
"""

import base64, json, os, re, time
import requests

ZHIPU_API_KEY = "680b341095d04c74bf5ed2320fb34036.4GGHZhqogLzapG9w"
API_BASE = "https://open.bigmodel.cn/api/paas/v4"


class ZhipuAIAssistant:
    """智谱 AI 水印分析引擎"""

    def __init__(self, api_key=None):
        self.api_key = api_key or ZHIPU_API_KEY
        self._cache = {}

    # ═══════════════════════════════════════════════════════════
    # 底层通信
    # ═══════════════════════════════════════════════════════════

    def _headers(self):
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def _call_text(self, prompt, model="glm-4-flash", temperature=0.3, max_tokens=1024):
        """纯文本推理"""
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": "你是专业的视频处理和水印分析专家。请始终以中文回复，并用结构化 JSON 返回分析结果。"},
                {"role": "user", "content": prompt},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        r = requests.post(f"{API_BASE}/chat/completions", headers=self._headers(),
                          json=payload, timeout=60)
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]

    def _call_vision(self, prompt, image_path, model="glm-4v-flash", temperature=0.2, max_tokens=1024):
        """视觉多模态推理 — 发送图片"""
        # 读取并编码图片
        with open(image_path, "rb") as f:
            img_data = base64.b64encode(f.read()).decode("utf-8")

        # 推断 mime 类型
        ext = os.path.splitext(image_path)[1].lower()
        mime_map = {".jpg": "jpeg", ".jpeg": "jpeg", ".png": "png", ".webp": "webp", ".bmp": "bmp"}
        mime = mime_map.get(ext, "jpeg")

        payload = {
            "model": model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": f"data:image/{mime};base64,{img_data}"}},
                    ],
                }
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        r = requests.post(f"{API_BASE}/chat/completions", headers=self._headers(),
                          json=payload, timeout=90)
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]

    # ═══════════════════════════════════════════════════════════
    # 智能功能
    # ═══════════════════════════════════════════════════════════

    def detect_watermark(self, image_path):
        """
        🔍 AI 自动检测水印位置 & 类型
        发送视频帧给 GLM-4V，返回结构化检测结果。

        Returns: {
            "has_watermark": bool,
            "x_frac": float, "y_frac": float,
            "w_frac": float, "h_frac": float,
            "type": "text" | "logo" | "mixed" | "transparent" | "none",
            "confidence": float,
            "description": str
        }
        """
        cache_key = f"detect_{os.path.getmtime(image_path)}_{os.path.getsize(image_path)}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        prompt = """请仔细分析这张视频帧截图，找出其中水印的位置和类型。

水印通常出现在视频角落（右下/左下/右上/左上），可能是：
- 文字水印（如平台名称、用户ID）
- 图标/Logo水印（如抖音、快手、B站图标）
- 混合水印（文字+图标组合）
- 半透明水印

请严格按照以下 JSON 格式返回，不要包含任何其他文字：
{
  "has_watermark": true,
  "x_frac": 0.72,
  "y_frac": 0.88,
  "w_frac": 0.26,
  "h_frac": 0.10,
  "type": "logo",
  "confidence": 0.92,
  "description": "右下角抖音圆形logo，半透明，直径约占画面1/10"
}

注意：x_frac/y_frac 是水印区域左上角的比例坐标 (0~1)，w_frac/h_frac 是水印宽度/高度的比例 (0~1)。"""

        try:
            start = time.time()
            raw = self._call_vision(prompt, image_path)
            elapsed = time.time() - start

            # 提取 JSON（模型可能包裹在 markdown 代码块中）
            json_str = self._extract_json(raw)
            result = json.loads(json_str)

            # 规范化数值
            for k in ("x_frac", "y_frac", "w_frac", "h_frac"):
                result[k] = max(0.0, min(1.0, float(result.get(k, 0))))
            result["confidence"] = max(0.0, min(1.0, float(result.get("confidence", 0.5))))
            result["_elapsed"] = round(elapsed, 1)
            result["_model"] = "glm-4v-flash"

            self._cache[cache_key] = result
            return result

        except json.JSONDecodeError as e:
            return {
                "has_watermark": False,
                "x_frac": 0, "y_frac": 0, "w_frac": 0, "h_frac": 0,
                "type": "none", "confidence": 0,
                "description": f"AI 解析异常: {e}",
                "_raw": raw[:200] if 'raw' in dir() else "",
            }
        except Exception as e:
            return {
                "has_watermark": False,
                "x_frac": 0, "y_frac": 0, "w_frac": 0, "h_frac": 0,
                "type": "none", "confidence": 0,
                "description": f"AI 检测失败: {str(e)}",
            }

    def recommend_algorithm(self, video_info, watermark_info):
        """
        🧠 AI 算法推荐
        结合视频参数和水印特征，推荐最优处理方案。

        video_info:  {"width": int, "height": int, "duration": float, "fps": float}
        watermark_info: {"type": str, "has_watermark": bool, ...}

        Returns: {
            "algorithm": str,
            "quality": str,
            "reasoning": str,
            "tips": [str, ...]
        }
        """
        prompt = f"""你是一个视频处理专家。请根据以下信息，推荐最佳去水印方案：

【视频信息】
- 分辨率: {video_info.get('width', '?')}×{video_info.get('height', '?')}
- 时长: {video_info.get('duration', '?')} 秒
- 帧率: {video_info.get('fps', '?')} fps

【水印特征】
- 类型: {watermark_info.get('type', '?')}
- 置信度: {watermark_info.get('confidence', '?')}
- 描述: {watermark_info.get('description', '?')}

【可选算法】
1. delogo（智能融合）- 边缘色彩融合，适合矩形纯色水印，速度极快
2. blur（高斯模糊）- 自然模糊处理，适合大面积水印
3. mosaic（马赛克）- 像素化处理，适合复杂水印
4. inpaint_telea（快速修复）- AI 逐帧修复，效果好，速度较慢
5. inpaint_ns（深度修复）- Navier-Stokes AI修复，效果最佳，速度最慢

【画质选项】
- fast（快速）- CRF 23, 适合预览
- balanced（标准）- CRF 20, 推荐日常使用
- high（高质量）- CRF 17, 适合最终成品

请严格按照以下 JSON 格式返回，不要包含其他文字：
{
  "algorithm": "inpaint_ns",
  "quality": "high",
  "reasoning": "因为水印为半透明logo且位置固定，建议使用深度修复算法保留背景细节，高质量编码保真",
  "tips": ["处理前建议备份原文件", "对于半透明水印，可适当扩展处理区域", "处理后建议检查边缘过渡效果"]
}"""

        try:
            raw = self._call_text(prompt)
            json_str = self._extract_json(raw)
            return json.loads(json_str)
        except Exception as e:
            return {
                "algorithm": "delogo",
                "quality": "balanced",
                "reasoning": f"推荐失败({e})，使用默认方案",
                "tips": ["如效果不满意，可尝试 inpaint_ns 深度修复"],
            }

    def evaluate_quality(self, before_frame_path, after_frame_path):
        """
        📊 AI 效果评估
        对比处理前后的视频帧，评价去水印质量。

        Returns: {
            "score": float (0-10),
            "visible_artifacts": bool,
            "edge_quality": str,
            "comments": str
        }
        """
        # 为了支持两张图对比，用纯文本模式，分别描述两个文件路径让用户手动对比
        # 这里有局限性，所以改为：先拼接 prompt，让用户上传两张图分别检测
        # 实际实现：取处理后帧再做一次检测，看还有没有水印

        prompt = """你正在评估去水印效果。请对比两张视频帧：
第一张是去水印前的原始帧，第二张是去水印后的帧。
请评估：
1. 水印是否完全去除
2. 处理区域是否有可见痕迹（模糊、色差、马赛克）
3. 边缘过渡是否自然
4. 整体画质是否受损

请严格按照 JSON 格式返回：
{
  "score": 8.5,
  "watermark_removed": true,
  "visible_artifacts": false,
  "edge_quality": "smooth/natural/slight_halo/obvious",
  "quality_loss": "none/minimal/moderate/severe",
  "comments": "水印已完全去除，处理区域与周围融合自然，无明显痕迹"
}"""

        # 构造多图消息
        try:
            images = []
            for p in (before_frame_path, after_frame_path):
                with open(p, "rb") as f:
                    b = base64.b64encode(f.read()).decode("utf-8")
                    ext = os.path.splitext(p)[1].lower()
                    m = {"jpg": "jpeg", "jpeg": "jpeg", "png": "png"}.get(ext, "jpeg")
                    images.append({"type": "image_url",
                                   "image_url": {"url": f"data:image/{m};base64,{b}"}})

            payload = {
                "model": "glm-4v-flash",
                "messages": [{"role": "user", "content": [
                    {"type": "text", "text": prompt},
                    images[0],
                    {"type": "text", "text": "以上是去水印前的原始帧，下面是去水印后："},
                    images[1],
                ]}],
                "temperature": 0.2,
                "max_tokens": 600,
            }
            r = requests.post(f"{API_BASE}/chat/completions",
                              headers=self._headers(), json=payload, timeout=120)
            r.raise_for_status()
            raw = r.json()["choices"][0]["message"]["content"]
            json_str = self._extract_json(raw)
            return json.loads(json_str)
        except Exception as e:
            return {"score": 0, "watermark_removed": False,
                    "comments": f"评估失败: {e}"}

    def generate_report(self, video_info, watermark_info, processing_params, after_frame_path=None):
        """
        📝 生成完整处理报告
        综合所有信息，输出一份排版美观的处理报告。
        """
        params_str = json.dumps(processing_params, ensure_ascii=False, indent=2)
        prompt = f"""请为一次视频去水印处理生成一份专业的处理报告摘要：

【视频信息】
- {video_info.get('width','?')}×{video_info.get('height','?')}
- 时长 {video_info.get('duration','?')}s @ {video_info.get('fps','?')}fps

【检测到的水印】
{json.dumps(watermark_info, ensure_ascii=False, indent=2)}

【处理参数】
{params_str}

请用中文输出一份简洁的报告（纯文本，200字以内），包含：
1. 视频概况
2. 水印分析
3. 处理方案
4. 效果展望"""

        try:
            return self._call_text(prompt)
        except Exception as e:
            return f"报告生成失败: {e}"

    # ═══════════════════════════════════════════════════════════
    # 工具方法
    # ═══════════════════════════════════════════════════════════

    def _extract_json(self, text):
        """从 AI 回复中提取 JSON（处理 markdown 代码块包装）"""
        # 尝试匹配 ```json ... ``` 
        m = re.search(r'```(?:json)?\s*\n?([\s\S]*?)\n?```', text)
        if m:
            return m.group(1).strip()
        # 尝试匹配 { ... }
        m = re.search(r'\{[\s\S]*\}', text)
        if m:
            return m.group(0)
        return text.strip()


# ═══════════════════════════════════════════════════════════════
# 模块测试
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import sys
    ai = ZhipuAIAssistant()

    if len(sys.argv) > 1 and sys.argv[1] == "test":
        # 纯文本测试
        r = ai._call_text("回复'OK'，不要其他内容", max_tokens=10)
        print("Text API:", r)

    elif len(sys.argv) > 1:
        # 视觉检测测试
        img_path = sys.argv[1]
        print(f"\n🔍 检测: {img_path}")
        result = ai.detect_watermark(img_path)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print("用法: python ai_assistant.py <图片路径>")
        print("      python ai_assistant.py test")
