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
TAG = "v1.4.0"
NAME = "DesktopFX v1.4.0"
ASSET = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "dist", "DesktopFX.exe")

BODY = """\
## DesktopFX v1.4.0

新增**鼠标光环**：跟随光标的一圈光环，随**系统正在播放的音乐**律动 —— 鼓点一响就跳一下。

### 鼠标光环
- **九种样式**（全部代码绘制，不引入图片资源）：呼吸圆环 / 双环脉冲 / 彩虹光环 /
  辐条光环 / 星芒光环 / 粒子光环 / 三条线 / 折线圆环 / 赛博风
- **波动幅度**可调：上限是原来的 3 倍（0~1.65）
- 辐条 / 星芒改为**空心描边**，中心让开鼠标指针
  辐条光环 / 星芒光环 / 粒子光环
- 电平取自 Windows 的 **`IAudioMeterInformation`**（系统默认渲染设备的实时峰值，
  纯 ctypes 调 COM，零新依赖）—— **读的是系统正在播放的声音**，不是程序自己
  播放器的声音，所以放什么歌它就跟什么
- 可调：**样式 / 大小 / 灵敏度 / 颜色**；拿不到音频设备时不报错也不崩，
  光环退化成"一圈很淡的环"
- 三件事让它"看起来像律动"：① **自适应归一化**（不同音量下峰值差一个数量级，
  实测 0.01~0.10，固定阈值会让轻音乐不动、重音乐爆表）② **双时间常数包络**
  （上升快、下降慢，光环不会疯狂闪烁）③ **采集在后台线程**（设备被拔也能自恢复）
- *取舍*：峰值电平是"整体强弱"，拿不到频谱（分不出低频鼓点与高频镲片）——
  分频段需要自己抓 PCM 做 FFT，代价大得多

### 顺带修掉的两处
- **控制面板的悬浮提示不再飘离面板**：提示浮层原本自己设了 `-topmost`，而面板在
  v1.3.0 改成普通窗口后，两者分属不同 z 序组，"从属窗口永远在拥有者之上"的
  规则失效 —— 提示会飘到离面板很远的地方。现在去掉它自己的 `-topmost`
- **窗口高度会同时考虑侧栏**：新增特效后侧栏比最高的页面还高，之前按内容算
  窗口高会把侧栏多出来的那截切掉，最后一项特效露不出来

### 性能
鼠标层每帧预算很紧，初版最慢 18.8ms/帧（彩虹样式），已优化到最慢 8.2ms，
其余 0.5~3.8ms：按样式收缩包围盒、角度懒计算、彩虹改 256 级色环查表、
辐条抖动系数查表、粒子只在小方框内算。

### 验证
- `tests/_verify_halo.py`（新）10 项：电平算法 4 项 + 样式渲染 3 项 +
  跨文件一致性 2 项 + 注册与 Scene 接线 1 项
- 全量 `_run_all_verify.py` 41/41 通过
- 真机：7 个内置音效全部起播；电平随音乐起伏（实测 0.000 → 0.818）

### 资源
- `DesktopFX.exe`：单文件打包（PyInstaller，含 `assets` 与内置音效）
- 源码已同步至 `main`，标签 `v1.4.0`
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
