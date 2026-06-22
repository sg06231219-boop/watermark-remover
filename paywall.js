/**
 * 通用付费墙组件 v1.0
 * 用法：在HTML中 <script src="paywall.js"></script>
 * 配置：window.PAYWALL_CONFIG = { feature: 'ai-report', freeLimit: 3, price: '9.9' }
 * 触发：Paywall.track('ai-report')  // 记录使用次数
 * 锁定：Paywall.check('ai-report')   // 返回 true=允许使用, false=已锁定
 */

(function () {
  'use strict';

  const STORAGE_KEY = 'paywall_usage';
  const DEFAULT_CONFIG = { freeLimit: 3, price: '9.9', contactWx: 'a5050e' };

  function getUsage() {
    try {
      return JSON.parse(localStorage.getItem(STORAGE_KEY)) || {};
    } catch (e) {
      return {};
    }
  }

  function saveUsage(data) {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(data));
  }

  function getConfig(feature) {
    const global = window.PAYWALL_CONFIG || {};
    return { ...DEFAULT_CONFIG, ...global, ...((global.features || {})[feature] || {}) };
  }

  // 创建付费弹窗
  function createModal() {
    if (document.getElementById('paywall-modal')) return;
    const html = `
    <div id="paywall-modal" class="paywall-overlay" style="display:none">
      <div class="paywall-card">
        <button class="paywall-close" onclick="document.getElementById('paywall-modal').style.display='none'">✕</button>
        <div class="paywall-icon">🔒</div>
        <h2 class="paywall-title">免费次数已用完</h2>
        <p class="paywall-desc" id="paywall-desc"></p>
        <div class="paywall-price">
          <span class="paywall-amount" id="paywall-amount">¥9.9</span>
          <span class="paywall-unit">/ 永久解锁</span>
        </div>
        <div class="paywall-qr">
          <div class="paywall-qr-box" id="paywall-qr-box">
            <!-- 微信收款码占位 -->
            <div style="font-size:60px">💚</div>
            <div style="font-size:13px;color:#999;margin-top:6px">微信扫码支付</div>
            <div style="font-size:11px;color:#666;margin-top:4px" id="paywall-wxid">a5050e</div>
          </div>
        </div>
        <p class="paywall-tip">💡 支付后截图发送微信，即刻解锁全部功能</p>
        <p class="paywall-tip">解锁后所有功能无限使用，含：AI选校报告、录取概率、冲稳保推荐等</p>
        <button class="paywall-unlock-btn" onclick="Paywall.unlock()">我已支付，立即解锁</button>
      </div>
    </div>
    <style>
    .paywall-overlay {
      position: fixed; inset: 0; background: rgba(0,0,0,0.75);
      z-index: 99999; display: flex; align-items: center; justify-content: center;
      backdrop-filter: blur(4px); animation: pwFadeIn 0.3s;
    }
    @keyframes pwFadeIn { from{opacity:0} to{opacity:1} }
    .paywall-card {
      background: linear-gradient(145deg, #1a1f35, #11162a);
      border: 1px solid rgba(245,158,11,0.3);
      border-radius: 20px; padding: 30px 24px; max-width: 380px; width: 90%;
      text-align: center; position: relative;
      box-shadow: 0 20px 60px rgba(0,0,0,0.5), 0 0 80px rgba(245,158,11,0.08);
      color: #e2e8f0;
    }
    .paywall-close {
      position: absolute; top: 12px; right: 16px;
      background: none; border: none; color: #64748b; font-size: 20px; cursor: pointer;
    }
    .paywall-icon { font-size: 48px; margin-bottom: 12px; }
    .paywall-title { font-size: 1.3em; font-weight: 700; margin: 0 0 8px; color: #f1f5f9; }
    .paywall-desc { font-size: 0.9em; color: #94a3b8; margin-bottom: 16px; line-height: 1.6; }
    .paywall-price { margin-bottom: 16px; }
    .paywall-amount { font-size: 2em; font-weight: 800; color: #f59e0b; }
    .paywall-unit { font-size: 0.8em; color: #94a3b8; }
    .paywall-qr-box {
      display: inline-block; padding: 16px 24px;
      background: rgba(255,255,255,0.04); border: 2px dashed rgba(255,255,255,0.1);
      border-radius: 12px; margin-bottom: 12px;
    }
    .paywall-tip {
      font-size: 0.78em; color: #64748b; margin: 4px 0; line-height: 1.5;
    }
    .paywall-unlock-btn {
      margin-top: 14px; padding: 10px 32px;
      background: linear-gradient(135deg, #f59e0b, #d97706);
      border: none; border-radius: 25px; color: #fff; font-weight: 700;
      font-size: 0.95em; cursor: pointer;
      transition: all 0.2s;
    }
    .paywall-unlock-btn:hover { transform: scale(1.03); box-shadow: 0 4px 20px rgba(245,158,11,0.3); }
    </style>`;
    document.body.insertAdjacentHTML('beforeend', html);
  }

  window.Paywall = {
    /**
     * 记录一次使用
     * @param {string} feature - 功能名称
     * @param {object} config - 可选配置 { freeLimit, price, contactWx, desc }
     */
    track: function (feature, config) {
      const usage = getUsage();
      if (!usage[feature]) usage[feature] = 0;
      usage[feature]++;
      saveUsage(usage);
      return usage[feature];
    },

    /**
     * 检查是否可以使用（不增加计数）
     * @returns {boolean} true=允许, false=已锁定
     */
    check: function (feature, config) {
      const cfg = getConfig(feature);
      const usage = getUsage();
      const count = usage[feature] || 0;
      // 如果已购买，永久解锁
      if (usage[feature + '_unlocked']) return true;
      if (count < cfg.freeLimit) return true;
      return false;
    },

    /**
     * 尝试使用功能。如果可以→返回true。如果已锁→显示付费弹窗→返回false
     */
    tryUse: function (feature, config) {
      const cfg = { ...getConfig(feature), ...(config || {}) };
      const usage = getUsage();
      if (usage[feature + '_unlocked']) return true; // 已购买

      const count = (usage[feature] || 0) + 1; // 本次使用后的计数
      usage[feature] = count;
      saveUsage(usage);

      if (count <= cfg.freeLimit) return true;

      // 已超限，显示付费弹窗
      createModal();
      const modal = document.getElementById('paywall-modal');
      document.getElementById('paywall-desc').textContent =
        cfg.desc || `您已使用 ${cfg.freeLimit} 次免费${feature === 'ai-report' ? 'AI选校报告' : feature}，继续使用需付费解锁`;
      document.getElementById('paywall-amount').textContent = '¥' + (cfg.price || '9.9');
      document.getElementById('paywall-wxid').textContent = cfg.contactWx || 'a5050e';
      modal.style.display = 'flex';
      return false;
    },

    /**
     * 解锁功能（用户声称已支付后调用）
     */
    unlock: function (feature) {
      const usage = getUsage();
      usage[feature + '_unlocked'] = true;
      saveUsage(usage);
      document.getElementById('paywall-modal').style.display = 'none';
      // 触发自定义事件，让前端刷新
      window.dispatchEvent(new CustomEvent('paywall-unlocked', { detail: { feature } }));
    },

    /**
     * 获取剩余免费次数
     */
    remaining: function (feature) {
      const cfg = getConfig(feature);
      const usage = getUsage();
      if (usage[feature + '_unlocked']) return Infinity;
      return Math.max(0, cfg.freeLimit - (usage[feature] || 0));
    },

    /**
     * 重置（调试用）
     */
    reset: function (feature) {
      const usage = getUsage();
      delete usage[feature];
      delete usage[feature + '_unlocked'];
      saveUsage(usage);
    }
  };

  // 页面加载时初始化弹窗 DOM
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', createModal);
  } else {
    createModal();
  }
})();
