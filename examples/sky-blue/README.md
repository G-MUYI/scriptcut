# 示例：天空为什么是蓝色的（42 秒竖屏科普）

零 API Key 跑通完整流程：edge-tts 免费配音、本地画的简笔素材（代替 AI 出图）、程序化音效。

```bash
python examples/sky-blue/tools/make_assets.py   # 生成 assets/*.png（正式项目用 scriptcut gen 或人工出图）
scriptcut run examples/sky-blue                  # → out/sky-blue.mp4 与 out/sky-blue/ 交付包
scriptcut jianying examples/sky-blue             # 可选：剪映草稿
```

看点：
- `storyboard.yaml`：13 个镜头（含片头章节卡 S00）、人物 / 道具 / 文字三类元素、`over` 叠加红叉、`grow` 生长、`shake` 震屏、手动标题断句 `cap`。
- `scenes/scatter.js`：光点穿过大气、撞上分子后蓝光四散——所有动作挂在「光线」「撞上」「空气分子」这几个字上。
- `scenes/sunset.js`：自己画整屏背景（`hide: ['background']`），天色随「傍晚」渐变。
- 换成自己的录音：把 `voice.mp3` 放进 `input/`，重跑 `scriptcut voice` 起的后续步骤，画面自动对齐新配音。
