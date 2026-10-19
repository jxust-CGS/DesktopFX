# -*- coding: utf-8 -*-
"""通过 GitHub REST API 创建 Release 并上传 dist/DesktopFX.exe。

凭证：环境变量 GITHUB_TOKEN（本机 GCM 缓存的 token，运行时注入，不落盘）。
用法：
    GITHUB_TOKEN=xxx python tools/_publish_release.py
"""
import json
import os
import sys
import urllib.request

REPO = "jxust-CGS/DesktopFX"
TAG = "v1.3.0"
NAME = "DesktopFX v1.3.0"
ASSET = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "dist", "DesktopFX.exe")

BODY = """\
## DesktopFX v1.3.0

修掉一批交互与音量的顽疾，并把控制面板改回普通窗口的行为。

### 点击音效：音量真正对所有音效生效
MCI 的 **waveaudio 设备根本不支持 `setaudio volume`**（实测发任何取值都返回
err 261），此前"只有气泡声和旅店铃声有音量"的真凶就在这里 —— 那两个是
mp3，走的是 mpegvideo 设备，0~1000 区间合法；而全部 wav 走 waveaudio，
音量命令**直接被拒**，滑块动了等于没动。

改用 **waveOut 软件混音**（ctypes 直接调 `waveOut*`，零新依赖）：
- `wave` 读 16-bit PCM → numpy 乘音量系数 → 统一 44.1k 立体声后起播
- 每条流一条句柄，多击仍可叠播；守护线程轮询 `WHDR_DONE` 回收
- 解码失败 / 无音频设备 / 池满自动退回 MCI 兜底
- mp3/wma 维持原路径（区间实测合法）

同时新增**音量滑块**，默认 100% 即等于当前系统音量；「语音与文字统一」
挑中的那条语音也纳入同一支滑块。

### 点击文字 / 表情：控制面板上也能看到了
此前"命中配置面板就整块屏蔽点击"，面板空白处也一并没了。改为**三态判定**：
- 桌面与其它程序 → 照常触发
- 面板的**交互控件**（按钮 / 滑块 / 侧栏导航）→ 抑制，
  彻底避免"按下触发的旧音效"与"选择后的试听"叠成两声
- 面板其它区域 → 放行

### 控制面板：改成普通窗口
- 不再 `-topmost`、不再每秒抢顶层，**点它正常获得焦点、正常被别的窗口盖住**
- 双击托盘唤出时仍会**临时置顶一次再立刻摘掉置顶位**，保证出现在最前面
- 移除播放点击特效期间的面板观感切换 —— 那会让面板"点一下颜色变深、
  播完又变回来"
- 特效层是 topmost，天然显示在普通窗口之上，不再需要任何临时提层

### 已知遗留
配置面板**标题栏**（用于拖动窗口的那一条）仍会被判定为交互控件，
点它第一次有特效、第二次没有。三个修法已评估，待定。

### 验证
- `_run_all_verify --fast` 15/15 通过
- `_verify_zorder` 新增 4 条不变量（提层让位 / 恢复 / 标志置位 / 自愈），25→29
- `_verify_click` 79 项（含音量解码与语义、面板三态判定、试听接线）
- 真机：7 个内置音效全部起播且句柄回收；面板点击特效 0.2~2.0s 稳定可见；
  6 秒内抢顶归零 0 次；点击时面板颜色变化 0

### 资源
- `DesktopFX.exe`：单文件打包（PyInstaller，含 `assets` 与内置音效）
- 源码已同步至 `main`，标签 `v1.3.0`
"""


def api(method, url, data=None, is_upload=False):
    token = os.environ["GITHUB_TOKEN"]
    headers = {
        "Authorization": "Bearer " + token,
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if is_upload:
        headers["Content-Type"] = "application/octet-stream"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=300) as r:
        return r.getcode(), r.read().decode("utf-8", "replace")


def main():
    if not os.path.exists(ASSET):
        raise SystemExit("找不到产物：%s" % os.path.abspath(ASSET))
    size = os.path.getsize(ASSET)
    print("产物 %s  %.1f MB" % (os.path.abspath(ASSET), size / 1e6))

    # 1) 创建 release
    url = "https://api.github.com/repos/%s/releases" % REPO
    payload = json.dumps({
        "tag_name": TAG, "name": NAME, "body": BODY,
        "draft": False, "prerelease": False,
    }).encode("utf-8")
    code, resp = api("POST", url, payload)
    if code not in (200, 201):
        raise SystemExit("创建 release 失败：HTTP %d\n%s" % (code, resp[:500]))
    rel = json.loads(resp)
    rid = rel["id"]
    print("release 已创建 id=%s  url=%s" % (rid, rel.get("html_url")))

    # 2) 上传资产
    up_url = ("https://uploads.github.com/repos/%s/releases/%s/assets?name=%s"
              % (REPO, rid, "DesktopFX.exe"))
    with open(ASSET, "rb") as fh:
        blob = fh.read()
    code, resp = api("POST", up_url, blob, is_upload=True)
    if code not in (200, 201):
        raise SystemExit("上传资产失败：HTTP %d\n%s" % (code, resp[:500]))
    print("资产已上传：HTTP %d  %s" % (code, json.loads(resp).get("browser_download_url")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
