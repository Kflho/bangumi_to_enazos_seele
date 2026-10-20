# bangumi_to_enazos_seele

把 Bangumi 的标记列表转成 enazo（绘谜）词库。读取收藏导出表，逐条问 Bangumi 接口要别名，
输出 enazo 批量录入用的文本，一行一条：

```text
词条名,别名一,别名二$小狐狸提示
```

## 快速开始

```bash
python bangumi_to_enazo.py --input in/collection.xlsx
```

先小样本核对格式：

```bash
python bangumi_to_enazo.py --input in/collection.xlsx --limit 20
```

结果写在 `out/bank.txt`，同时生成 `out/report.md` 记录失败条目、没补到别名的行、
被清洗的词条名。

## 输入

表格来自 Bangumi 收藏导出脚本（[gm_scripts](https://github.com/zhifengle/gm_scripts) 的
「导出收藏」），xlsx 和 csv 都行。至少要这几列：

| 列 | 用途 |
|---|---|
| `名称` | 中文名，作为词条名 |
| `别名` | 导出表里的日文原名，作为别名 |
| `地址` | 形如 `/subject/551559`，subject id 从这里取 |
| `吐槽` | 可选，作为 `$` 后面的小狐狸提示 |
| `观看状态` | 可选，配合 `--status` 筛条目 |

表头写成 `name` / `greyName` / `url` / `comment` 也认。

## 输出

`姓名,别名…$提示`，别名顺序是：表格的中文名 → 接口的中文名 → 日文原名 → 接口原名 →
条目信息里的「中文名 / 别名 / 英文名 / 罗马字」。

enazo 把 `,` `，` `、` `;` `；` `/` 和空白当分隔符，名字里带这些字符会被切成两段，
所以默认换成 `-`（`--dash all`）。只想换分隔符、保留空格用 `--dash seps`，
全都不动用 `--dash none`。

## 常用参数

| 参数 | 说明 |
|---|---|
| `--input` / `--output` | 输入表格、输出词库，默认 `in/collection.xlsx`、`out/bank.txt` |
| `--cache` | 条目缓存目录，默认 `cache/subjects` |
| `--offline` | 只用缓存，不发请求 |
| `--status 看过,在看` | 只导出这几类标记 |
| `--no-hint` | 不输出 `$` 后面的提示 |
| `--dash all\|seps\|none` | 分隔符与空白的处理档位 |
| `--delay 0.5` | 请求间隔，被限流时脚本会自己放慢 |
| `--limit 20` | 只跑前 20 行 |
| `--progress-every 10` | 每 N 行打一条进度，0 表示不打 |

## 网络

`api.bgm.tv` 只读公开数据，不需要登录，但要求请求带 User-Agent，且直连常常超时，
需要走代理。用代理跑一层即可：

```bash
python bangumi_to_enazo.py --input in/collection.xlsx   # 已设好 HTTP(S)_PROXY 的环境
```

首次全量约九百次请求，跑完缓存在 `cache/subjects/`，之后重跑命中缓存不再发请求。
被限流时脚本读 `Retry-After` 并把间隔翻倍（上限 5 秒），不用手动干预；
`Ctrl+C` 会先写出已完成的部分，原样重跑从缓存接着补。

## 验证

跑完看两个地方：

1. `out/report.md` 的「接口失败」和「没补到额外别名的词条」；
2. `out/bank.txt` 前几行，确认词条名没被切成半截、`$` 后面提示完整。

## 已知限制

- 依赖 Python 3.9+，只用标准库，不装第三方包；
- 表格列名按 Bangumi 收藏导出认，换导出源要改 `bangumi/sheet.py` 里的列名表；
- 已删除的条目（接口 404）补不到别名，只能用表格里的名字；
- 词条名里的分隔符会被换成 `-`，这是 enazo 的切分规则决定的，不是 bug。

## 相关

- Bangumi API：<https://github.com/bangumi/api>
- 收藏导出脚本：[gm_scripts](https://github.com/zhifengle/gm_scripts)
- enazo 词库格式说明：见 `docs/enazo-词库格式.md`
