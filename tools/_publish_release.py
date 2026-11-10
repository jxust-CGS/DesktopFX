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
TAG = "v1.5.0"
NAME = "DesktopFX v1.5.0"
ASSET = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "dist", "DesktopFX.exe")

BODY = """\
## DesktopFX v1.5.0

两批改动：**鼠标光环大改**（波动幅度放大 3 倍、三种新样式、全部改成稀疏描边）
+ **暂停快捷键与恢复后层级修复**。

### 鼠标光环 v2

#### 波动幅度：从写死变成可调，上限是原来的 3 倍

原先半径的涨落系数 `0.55` 是**写在代码里**的，用户根本调不了。现在做成
滑块「**波动幅度**」（0~1.65，默认 0.55）—— 上限正好是原值的 3 倍，调到头
就是"音乐最响时光环比安静时涨 165%"。

半径上限 320 像素：极端组合（大小 160 + 幅度拉满）理论半径 424，会被削掉
一小段 —— 有意的，再大的光环既难看又抢戏。

#### 辐条 / 星芒改空心（描边 + 中心让开鼠标指针）

实心辐条是一根根从圆心射出的楔形，十几根在指针周围堆成一坨，**尖端正好压在
光标上** —— 想点准一个小按钮得先猜光标在哪。

- 辐条 = "口"字框（两条径向边 + 一道外弧）
- 星芒 = 描边三角（两条斜边 + 外弧），内端离圆心留出 0.34 倍半径

判据：光标附近 12 像素内**一个像素都不能有**。

#### 新增三种样式

- **三条线**：三条同心细弧随音乐散开，像均衡器的三根柱
- **折线圆环**：不规则多边形边框（而不是圆），随音乐缩放并缓慢自转
- **赛博风**：断续霓虹段 + 旋转扫描弧 + 沿圈分布的数据块

九种样式全部由代码绘制，不引入图片资源。

#### 全部样式改稀疏描边（这是幅度能放大的前提）

包围盒面积 ∝ 半径²。幅度放大 3 倍 ⇒ 面积涨 9 倍 —— 逐像素画法直接爆：
实测「大小 160 + 幅度 1.65 + 音乐正响」时**星芒要 90ms/帧**（60fps 预算只有
16.7ms）。

改成稀疏描边：用极坐标把每条笔画参数化出来，只写真正有墨的像素。代价从
O(包围盒面积) 降到 O(笔画总长)，只随半径**线性**增长。

同一极端组合优化后：**最慢 10.5ms**（彩虹），星芒 9.3ms。

#### 顺带修掉一个真 bug：「粒子光环」一直是空白

粒子位置是**屏幕绝对坐标**，而包围盒坐标是相对框内的，两者没换算，窗口全被
裁到框外 —— 一个粒子都画不出来。

### 暂停快捷键失效 + 恢复后层级不对

#### 缺陷一：pause 事件只有控制面板消费

`("pause", None)` 事件原先**只有控制面板**在处理。而控制面板是可选的 ——
它没起来、或还没跑到它的 `_poll` 时，事件就堆在队列里没人管，用户按
Ctrl+Alt+R 毫无反应。

而且状态被**两处各切一次**（App 与面板），谁后处理谁说了算 —— 表现就是
"按一下没反应、按两下才暂停"，界面上的"[已暂停]"标记还常常是错的。

改法：**状态归 App 管，界面只负责显示**。
- App 新增统一入口 `on_pause_event`，事件泵里就地处理
- 转发给面板时换事件名 `pause-state`，面板**只刷界面、不再切状态**
- 面板上点状态区也走同一个入口 —— 三条路（面板点击 / 托盘菜单 / 快捷键）
  汇到同一个入口，状态只有一处说了算

#### 缺陷二：恢复时"骑在面板之上"的标志可能残留

`_ride_above_panel` 残留 True 时，`keep_on_top` 开头就 `return False` ——
置顶位自愈**永远不执行**，特效层再也抢不回顶。

为什么残留：暂停期间渲染循环整个被 `continue` 跳过，标志的配对清理没机会跑。

改法：恢复路径里**强制复位该标志**，并立刻补做被跳过的维护（置顶位自愈 +
两层先后纠正）。恢复后的第一帧用户就在看，等下一帧慢慢自愈会看到
"闪一下才浮上来"。

#### 体验：热键被占用时给出提示

系统级热键全局唯一。Ctrl+Alt+R 被别的程序抢占时，原先只在 error.log 里留痕，
用户按了没反应却不知道为什么。现在启动时弹一次提示（只弹一次），并说明
仍可用托盘菜单 / 面板状态区。

### 验证

- `tests/_verify_halo.py` 13 项：幅度 3 倍（半径**涨落量**实测 2.98 倍）、
  空心中心零像素、性能预算、跨文件一致性
- `tests/_verify_pause.py`（新）9 项：热键投递、App 侧独立处理、面板不重复
  切状态、暂停往返、置顶自愈、骑乘标志复位、`leave_panel_ride` 不丢置顶位、
  两层先后、不误伤同层像素
- 全量 `_run_all_verify.py` **42/42** 通过
- 真机：赛博风与空心星芒都在真实屏幕上确认过

### 资源

- `DesktopFX.exe`：单文件打包（PyInstaller，含 `assets` 与内置音效）
- 源码已同步至 `main`，标签 `v1.5.0`
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
