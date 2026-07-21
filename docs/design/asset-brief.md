# 道具素材征集单 · 实照级质感怎么来

> 结论先行：你截的三个 App（电击枪模拟器 9+ / 真实枪械模拟器 13+ / 空气枪弹 13+）的「真实感」
> **全部是实物照片抠图，不是 3D**。纯色大底 + 全屏道具照片 + 实录音效震动 = 效果的全部。
> 我们照抄这条路，一部手机 + 几十块道具就够。

---

## 你帮我的方式：三选一给素材（可混用）

| 路线 | 成本 | 质感上限 | 推荐度 |
|---|---|---|---|
| **A · 实物拍摄** | 淘宝几十块 + 手机半小时 | ★★★★★（就是那三个 App 的水平） | **首选** |
| B · AI 生图 | 零成本，用下方 prompt | ★★★★（角度/状态难精确控制） | 补位、快速验证 |
| C · 3D 模型 | 买模型 $10–50 或手机扫描 | ★★★★★ 且可任意角度/动画 | 二期再上 |

---

## 路线 A · 实物拍摄（首选）

### 采购清单（淘宝合计约 ¥50–80）

- [ ] KTV 骰盅一只（哑光黑、带盖那种）+ 配套 14–16mm 骰子 5 颗（**要幺四红点的**）
- [ ] 玩具左轮一把：优先找「旗枪 / BANG flag gun 整蛊道具」；买不到就普通仿真左轮玩具（金属感强的），旗子我后期合成
- [ ] 深绿或墨绿绒布一块（当桌布，顺便拍一张空桌底图）

### 拍摄规格（手机就行）

- **光**：白天靠窗的侧光，或一盏台灯 45° 打光 + 一张白纸对面补光。不要开闪光灯。
- **底**：每件道具拍两套——①纯白/纯灰底（方便抠图）②绒布底（留真实投影，最好用）。
- **机位**：主角度 45° 俯拍 + 正侧面 + 正顶视，三个角度。镜头拉远一点再放大（2x/3x），避免广角畸变。
- **格式**：能开 RAW 开 RAW，不能就最高画质 JPG；单边 ≥3000px；道具先擦掉指纹。

### 状态清单（关键！每个交互状态都要一张）

**骰子**
- [ ] 六个面各一张顶视（幺、二、三、四、五、六）
- [ ] 三颗骰子随机散落一张（45°）

**骰盅**
- [ ] 倒扣在绒布上（45° 主图）
- [ ] 掀开一条缝、能看见骰子边缘（「偷看」状态）
- [ ] 盅身拿开、三颗骰子露出（「开盅」状态）

**左轮**
- [ ] 正侧面全枪（主图）
- [ ] 弹巢甩出状态
- [ ] 顶视平放在绒布上
- [ ] 旗枪的话：旗子弹出那一下

**环境**
- [ ] 空绒布桌面一张（全屏底图用）

### 录音顺手也拍了（手机贴近实录，各 3–5 秒）

- [ ] 骰盅摇（轻/重两种手劲）· 盅底磕桌「咚」
- [ ] 骰子撒在木桌上
- [ ] 左轮弹巢拨转的棘轮声 · 空扣扳机「咔」

---

## 路线 B · AI 生图 prompt 包（复制即用）

**即梦 / 可灵（中文）：**
> 商品摄影，KTV黑色骰盅倒扣在墨绿色绒布桌面上，哑光质感，45度俯拍，柔和侧光，真实投影，超写实，4K，无文字

> 商品摄影，五颗白色骰子散落在墨绿色绒布上，幺点和四点为红色，凹陷点数，微距，柔光，超写实，4K

> 商品摄影，玩具左轮手枪平放在墨绿色绒布上，钢灰色枪身胡桃木握把，正侧面，柔和侧光，超写实，4K，无文字

**Midjourney / DALL·E（英文）：**
> product photography, black matte dice cup upside down on dark green felt, soft side lighting, realistic shadow, 45 degree angle, photorealistic, 4k --no text

> product photography, toy revolver with steel frame and walnut grip lying flat on dark green felt table, side profile, soft studio light, photorealistic, 4k --no text

要求：同一道具的不同状态尽量同一次会话里改图（保持一致性）；出图后原图发我，不用抠。

---

## 交付方式

把文件丢进仓库 `inputs/props-photos/`（或直接发我），命名照这个来：

```
dice_face1.jpg … dice_face6.jpg   dice_scatter.jpg
cup_closed.jpg  cup_peek.jpg  cup_open.jpg
revolver_side.jpg  revolver_cyl_open.jpg  revolver_top.jpg  revolver_flag.jpg
table_felt.jpg
sfx_cup_shake_soft.m4a  sfx_cup_slam.m4a  sfx_dice_roll.m4a  sfx_cyl_spin.m4a  sfx_hammer_click.m4a
```

## 素材到手后我这边的产出

1. 抠图 + 统一调色（绒布底色、投影方向对齐）
2. 道具规格页 v3「实照版」——CSS 手绘全部换真照片
3. 可交互网页 demo：摇一摇出骰、按住偷看、拨转弹巢（带你已批的那套震动/音效规格）
4. 沉淀成 App 用的素材包（切图 + 命名 + 状态机对照表）

---

## 路线 B 补充 · gpt-image-2 直调（OpenAI 兼容接口）

不需要 Codex CLI，一条 curl 即可（KEY 不要提交进仓库）：

```bash
curl $BASE_URL/v1/images/generations \
  -H "Authorization: Bearer $API_KEY" -H "Content-Type: application/json" \
  -d '{"model":"gpt-image-2","prompt":"<下方任选>","size":"1536x1024","quality":"high"}'
```

**左轮 prompt 组（同一会话连续出，保持同一把枪）：**
1. 主图：`Product photography, top-down view: a realistic snub-nose revolver, dark steel frame, walnut wood grip, lying flat on dark green felt, soft studio side lighting, realistic soft shadow, photorealistic, 4k, no text, no hands`
2. 弹巢甩出：`same revolver, cylinder swung open showing six empty chambers, same angle, same lighting`
3. 旗枪彩蛋：`same revolver, a small red flag with a Chinese character popping out of the muzzle, comedy prop style, same scene`

**骰盅补角度：**`Product photography, 45-degree view: a matte black KTV dice cup upside down on dark green felt, dark red felt rim touching the table, soft side lighting, photorealistic, no text`

产出命名与交付同上方「交付方式」一节；AI 图与 Cycles 渲染图可混用，绒布底色统一往 #14472F 调。
