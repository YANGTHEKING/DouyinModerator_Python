# Douyin Virtual Streamer Mod Manager Prototype

本项目是一个本地 PySide6 原型，用于验证“虚拟主播直播间房管辅助工具”的核心工作流。

当前版本走模拟数据源，不接入抖音私有协议，不自动登录，不绕过平台限制。真实页面接入会在后续通过 Qt WebEngine 承载你手动登录后的页面，并只观察页面上已经展示的内容。

## 当前已实现

- 单账号、单直播间、单场次的本地控制台原型
- 手动直播模式：歌回、杂谈、PK
- 模拟事件源：弹幕、进场、礼物、关注、系统提示
- 统一 `LiveEvent` 事件模型
- 可扩展规则引擎：规则内 AND，多规则 OR
- 默认半自动回复，低风险规则可自动发送
- 全局自动发送暂停
- 每分钟 3 条的 Mock 全局限频
- SQLite 事件日志和动作日志
- 点歌识别：`点歌 歌名`
- 歌单队列表
- 本地风险词提示
- 礼物感谢策略
- Mock 发送器
- WebEngine 页面区
- 配置化只读 DOM 观察器
- WebEngine 输入框/按钮发送器
- 本地 demo 直播间页面

## 运行

```bash
/Users/yangchangrui/.pyenv/shims/python3.13 -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/python -m douyin_mod_manager
```

或：

```bash
douyin-mod-manager
```

SQLite 数据库默认写入：

```text
data/app.db
```

## 第一阶段边界

- 不保存账号密码
- 不读取或逆向私有接口
- 不解密 WebSocket
- 不自动处罚用户
- 不自动禁言、拉黑、举报或踢人
- 自动发送必须经过暂停、限频、规则冷却和日志

## 后续阶段

1. 根据真实抖音页面调整 `config/dom_selectors.json`。
2. 增加选择器调试面板，显示每个选择器命中数量。
3. 完善发送成功/失败的异步日志确认。
4. 增加规则、模板、词库的导入导出。
5. 做场次日志 CSV/JSON 导出。

## WebEngine Demo

启动应用后点击左侧页面区的 `Demo`，应用会加载：

```text
tools/demo_live_room.html
```

然后切换到 `WebEngine` 事件源并启动 DOM 观察器。Demo 页面会自动生成弹幕、礼物、关注和进场事件；右侧快捷回复会通过页面里的模拟输入框和发送按钮发送。

真实页面接入时，先手动登录页面，再根据页面 DOM 调整：

```text
config/dom_selectors.json
```
