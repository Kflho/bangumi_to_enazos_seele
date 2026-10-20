# bangumi_to_enazos_seele

把 Bangumi 标记列表导成 enazo（绘谜）词库。默认只导「看过」，按用户和日期分开存档。

```text
词条名,别名一,别名二$小狐狸提示
```

## 快速开始

在仓库根目录放一个 `local.json`（不入库）：

```json
{
  "users": ["723737", "kflhotat"],
  "subject_type": 2,
  "dash": "all",
  "delay": 0.25
}
```

然后：

```bash
python bangumi_to_enazo.py
```

产出（文件名用昵称，不用数字 id）：

```text
bin/exports/Rokka-2026-10-20.txt
bin/exports/Kflho-2026-10-20.txt
bin/exports/report-2026-10-20.md
```

昵称会缓存在 `bin/data/profiles.json`，离线跑也能用昵称命名。

临时指定用户也行：`python bangumi_to_enazo.py --user sai`。

## 标记与状态

Bangumi 的标记分五种状态，接口里是 `type` 1–5：

| type | 状态 |
|---|---|
| 1 | 想看 |
| 2 | **看过（默认只导这个）** |
| 3 | 在看 |
| 4 | 搁置 |
| 5 | 抛弃 |

要别的状态就显式写，逗号分隔，`all` 表示五种都要：

```bash
python bangumi_to_enazo.py --status 看过,在看
python bangumi_to_enazo.py --status all
```

## 数据来源

**用户列表（推荐）**：走 `/v0/users/{username}/collections`，每次导出都是拿「当下」的标记，
不需要先导出 Excel。

**导出表**：也能吃 [gm_scripts](https://github.com/zhifengle/gm_scripts) 导出的 xlsx / csv：

```bash
python bangumi_to_enazo.py --input in/collection.xlsx --status all
```

表里要有 `名称` / `别名` / `地址` / `吐槽` / `观看状态` 这几列，表头写成
`name` / `greyName` / `url` / `comment` 也认。

## 本地数据与缓存

| 路径 | 内容 | 何时复用 |
|---|---|---|
| `bin/data/collections/<user>-t<type>.json` | 收藏快照，带每条的 `updated_at` | 增量同步靠它判断从哪页停 |
| `bin/data/subjects/<id>.json` | 条目详情，别名从这里取 | 抓到一次就不再请求 |
| `bin/exports/<昵称>-<日期>.txt` | 词库 | 同一天重跑会覆盖 |
| `bin/exports/report-<日期>.md` | 当天报告 | 每次重写 |

`bin/data/`（缓存与快照）和 `local.json` 不入库；`bin/exports/` 的产出会入库，
方便换机器时直接取题库，`.old/` 里的历史数据除外。

## 增量是怎么做的

收藏接口按 `updated_at` **倒序**返回，每页最多 100 条，所以：

- 上次同步记下最新的 `updated_at`，这次从第一页开始翻；
- 翻到比它更旧的记录就停，没改动的话只花 **1 个请求**；
- 新增和变动按 `updated_at` 比对，报告里分开计数；
- 想强制全量重拉用 `--full`。

条目别名变更频率低，`bin/data/subjects` 里的缓存一直复用；想强制刷新就删对应文件。

## 输出规则

词条名取表格或接口的中文名，别名依次是：中文名 → 接口中文名 → 日文原名 → 接口原名 →
条目信息里的「中文名 / 别名 / 英文名 / 罗马字」，去重后拼接。

enazo 把 `,` `，` `、` `;` `；` `/` 和空白当分隔符，名字里带这些会被切成两段，
所以默认换成 `-`（`--dash all`）。只想换分隔符、保留空格用 `--dash seps`，
全都不动用 `--dash none`。

`吐槽` 列放在 `$` 后面当小狐狸提示。单元格里有多条评论时按行拆开、用逗号并列
（官方示例就是 `$小狐狸文案，小狐狸2，小狐狸提示`），不会粘成一句；
`--hint-sep` 可改分隔符，传空串表示直接相接。`--no-hint` 整个关掉提示。

## 合并多个用户的词库

`merge_bank.py` 按 subject id 把几个人的词库并成一个：别名取并集，提示是各人评论
并列（逗号分隔）。只读本地快照和缓存，不联网。

要合并谁、题库叫什么，都写在 `local.json` 里：

```json
{
  "merge_users": ["469325", "869334", "chiotto", "723737"],
  "merge_title": "动画截图花菜q袜吃婆罗门题库"
}
```

然后直接跑：

```bash
python merge_bank.py
```

产出（题库名后面跟日期）：

```text
bin/exports/动画截图花菜q袜吃婆罗门题库-2026-10-20/动画截图花菜q袜吃婆罗门题库-2026-10-20.txt
bin/exports/动画截图花菜q袜吃婆罗门题库-2026-10-20/report.md
```

临时换人可以 `--users a b`，临时换名可以 `--title 名字`。

跑完会自检并写进报告：

- 每个用户的条目是否都进了合并结果（漏一条就报错）；
- 同名词条（多半是不同季/不同版本，只提醒不算错）；
- 格式自检：空行、多个 `$`、残留分隔符。

## 直接管理 enazo 词库

`enazo_lib.py` 直接调词库接口，不用打开页面点按钮。

词库的「唯一标识码」（`https://enazo.cn/seele?name=xxx` 里的 `xxx`）就是钥匙：
接口不校验登录，谁拿到码谁就能改库，所以别把码贴到公开地方。词库名写在
`local.json` 的 `enazo_lib` 里。

```bash
python enazo_lib.py stat                     # 看词库现状
python enazo_lib.py pull                     # 拉一份 json 备份到 bin/data/enazo
python enazo_lib.py import 题库.txt          # 用题库替换全部词条
python enazo_lib.py import 题库.txt --append # 追加；同名合并别名和提示
python enazo_lib.py import 题库.txt --limit 5 # 只导前 5 条，试水
python enazo_lib.py clear                    # 清空（打删除标记）
python enazo_lib.py copy --source 另一个库    # 整份复制另一个词库的条目
python enazo_lib.py copy --source 另一个库 --meta # 连简介、群号、专区、作者一起搬
python enazo_lib.py meta --title 名称 --summary 简介 --resume 介绍 --group 群号 --zone 日本动画
python enazo_lib.py meta --zone none          # 清空专区；其余字段不传就不改
python enazo_lib.py push 备份.json            # 把本地 json 推回去
```

改别的库用 `--name 词库码`；默认读 `local.json` 的 `enazo_lib`。

写之前会自动把当前词库备份到 `bin/data/enazo/<词库名>-<时间>.json`。

导入时按站点自己的规则解析每一行：`#` 后面是备注、`$` 后面是小狐狸提示、
`^` 后面是角色，别名和提示都按 `, ， ; ； 、 /` 切分并去重。

## 常用参数

| 参数 | 说明 |
|---|---|
| `--user` | 指定用户，可重复；不给就用 `local.json` 的 `users` |
| `--input` | 改用导出表（xlsx / csv） |
| `--status` | 导哪些标记状态，默认 `看过` |
| `--date` | 产出日期，默认今天 |
| `--full` | 全量重拉收藏列表 |
| `--offline` | 只用本地快照和缓存，不发请求 |
| `--dash all\|seps\|none` | 分隔符与空白的处理档位 |
| `--no-hint` | 不输出提示 |
| `--hint-sep` | 多条评论之间的分隔符，默认半角逗号 |
| `--check` | 写完后体检输出格式 |
| `--limit N` | 每个用户只处理前 N 条 |
| `--workers N` | 并发抓条目详情的线程数，默认 6 |
| `--rate R` | 全局每秒请求上限，默认 6；0 表示按 `--delay` 串行 |
| `--progress-every N` | 每 N 条打一条进度，0 表示不打 |

## 网络

`api.bgm.tv` 只读公开数据，不需要登录，但要求请求带 User-Agent，且直连常常超时，
需要走代理。首次全量会按条目数发请求（四人合计约五千条），之后靠缓存和增量。

抓条目详情默认 6 线程并发，但请求是**全局限速**的（默认 6 次/秒）：并发只用来吃掉
网络往返时间，不会把接口打爆。实测 8/16 线程都没触发 429，偶发超时来自代理链路，
所以超时设 15 秒、失败重试 3 次。

被限流时脚本读 `Retry-After` 并把请求间隔翻倍（上限 5 秒）；`Ctrl+C` 会写完当前用户
已完成的部分，原样重跑接着来。

## 验证

1. 跑完看 `bin/exports/report-<日期>.md`：失败条目、没补到别名的词条、格式自检结果；
2. 抽查 `bin/exports/<昵称>-<日期>.txt` 前几行，确认词条名没被截断、`$` 后提示完整；
3. `--check` 会检查空行、多个 `$`、残留分隔符和空词条名。

## 已知限制

- 依赖 Python 3.9+，只用标准库，不装第三方包；
- 私有收藏需要 access token，本工具只导公开收藏；
- 已删除的条目（接口 404）补不到别名；
- 增量发现不了「取消收藏」：条目被移出收藏不会从快照里删，需要 `--full` 重建；
- 词条名里的分隔符会被换成 `-`，这是 enazo 的切分规则决定的。

## 相关

- Bangumi API：<https://github.com/bangumi/api>
- 收藏导出脚本：[gm_scripts](https://github.com/zhifengle/gm_scripts)
- [enazo 词库格式](bin/requirements/enazo-词库格式.md)
- [词库合并方案](bin/requirements/词库合并方案.md)：设计取舍和边界情况
