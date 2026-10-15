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
TAG = "v1.2.0"
NAME = "DesktopFX v1.2.0"
ASSET = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "dist", "DesktopFX.exe")

BODY = """\
## DesktopFX v1.2.0

桌面特效能力大幅扩展、工程结构重构、并更换了软件图标。

### 新增 / 增强特效
- **宠物跟随二期**：帧动画 + 静止渐隐 + 点击表情，同屏上限提到 10
- **点击表情**：支持 GIF 动图、自选图片、单次取图模式
- **点击文字**：支持语音包，文字与音频可统一播放
- **点击特效**：修掉文字横向被切、惯用色与"彩色"、彩带曲线、字体/预设/倾斜
- **流星雨**：燃尽距离改由屏幕几何决定，覆盖整屏（解决高屏只有上半屏有流星）
- **独立「点击音效」**：不画像素，点一下响一声；多点击允许重叠播放

### 稳定性与交互
- 渲染线程加异常保护、三处 JSON 改原子写、配置值钳制、挂载失败回滚
- 声音统一到「点击音效」；预设不再冻结素材来源
- 界面：标题栏增加窗控钮、移除悬浮操作条

### 工程
- `fx_effects.py`（5633 行）拆分为 `fx_image` + `fx_click` + `fx_effects` 三层
- 38 个 `_verify_*.py` 收进 `tests/`、25 个开发脚本收进 `tools/`
- 托盘图标换成参考图风格的**霓虹光效鼠标箭头**（`assets/tray_icon.*`）

### 资源
- `DesktopFX.exe`：单文件打包（PyInstaller，含 `assets` 与内置音效）
- 源码已同步至 `main`，标签 `v1.2.0`
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
