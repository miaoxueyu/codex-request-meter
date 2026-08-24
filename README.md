# Codex Request Meter

> Based on [codex-request-meter](https://github.com/BobDLA/codex-request-meter) by BobDLA (MIT License). See [NOTICE](NOTICE) for attribution.

This repository contains a Codex hook that reports usage for one user prompt,
including thread-spawned subagents. The completed result is rendered inside the
Codex TUI as a hook log, for example:

```text
Prompt 12,345 tok / USD 0.0123  [in 10,000 | cache 8,000 | out 2,345 | agents 2]  Session 98,765 tok / USD 0.1042
```

The session's built-in `used-tokens` status-line item remains available. The
meter's session figure additionally includes subagents. The compact summary
uses Codex's normal transcript styling; hook `systemMessage` does not expose a
stable color field, so the hook deliberately avoids raw ANSI escape sequences.

## Install

Recommended installation from this repository or a GitHub clone:

```bash
uv tool install .
codex-request-meter-install
```

For this private GitHub repository:

```bash
uv tool install git+https://github.com/BobDLA/codex-request-meter
codex-request-meter-install
```

`pipx install` can be used instead of `uv tool install`. The installer merges
hooks into `$CODEX_HOME/hooks.json` (or `~/.codex/hooks.json`), preserves
existing hooks, replaces this project's old absolute-path hook, and creates a
`.bak` copy when that file already exists. Start a new Codex session and
approve hook trust if Codex asks.

The default user pricing file is
`$XDG_CONFIG_HOME/codex-request-meter/pricing.json`, or
`~/.config/codex-request-meter/pricing.json` when `XDG_CONFIG_HOME` is unset.
The first install copies the bundled table there; later installs preserve local
edits. Override it explicitly with `codex-request-meter-install --pricing
/path/to/pricing.json`.

For local development, the legacy command remains available:

```bash
python3 scripts/install_codex_meter.py
```

## Configure prices

Prices are USD per one million tokens. The bundled table matches the rates
supplied for the GPT-5.6 family; edit the user pricing file above when the
provider changes. `meter/pricing.json` and
`src/codex_request_meter/data/pricing.json` are the repository copies of the
same default table.

| Model | Input | Cached input | Output |
| --- | ---: | ---: | ---: |
| `gpt-5.6` / `gpt-5.6-sol` | $5.00 | $0.50 | $30.00 |
| `gpt-5.6-terra` | $2.50 | $0.25 | $15.00 |
| `gpt-5.6-luna` | $1.00 | $0.10 | $6.00 |

The meter also accepts a cache-write rate because Codex transcripts can expose
that bucket. Since the supplied price list has no separate cache-write price,
cache writes use the corresponding ordinary input rate. If a provider has a
different cache-write price, replace that field for the affected model.

The meter distinguishes ordinary input, cached input, cache-write input, and
output tokens. All four rates must be configured for a cost estimate. Reasoning
output is included in the provider's output count and is not charged twice. If
the model is not in the table, the TUI still reports exact tokens and displays
`USD n/a` instead of claiming a false cost.

## Verify

```bash
python3 -m unittest discover -s tests -v
python3 -m pip install -e .
codex-request-meter-install --dry-run
```

Usage events are stored in `~/.codex/request-meter/events.jsonl`.

The meter includes Codex `thread_spawn` subagents, including nested agents.
Internal system work such as guardian agents does not expose the lifecycle
hooks needed to attribute its tokens to a user prompt, so those costs remain
outside the prompt total.

## Acknowledgments

- [Linux DO](https://linux.do/) — Learn AI, go LinuxDO!

## License

MIT


---

## 本 Fork 的定制（DeepSeek + 缓存命中率）

在 upstream 基础上做了两处改动：

- **显示层**：`meter.py` 的 `_summary_message` 把原本的 `cache <token数>` 改成 `cache <命中率>%`（单轮），并在 `Session ...` 后面追加 `[cache <会话命中率>%]`，让你同时看到单轮与整段会话的缓存命中率。
- **价格/部署**：内置 DeepSeek 价格（元/百万 token），`config/run-meter.sh` 按北京时间自动在**高峰/空闲**两档间切换（高峰 = 周一至周五 9:00-12:00、14:00-18:00，空闲价为高峰一半）。

### 新机器一键安装

```bash
git clone git@github.com:<你的账号>/codex-request-meter.git
cd codex-request-meter
./setup.sh        # 建 venv + 可编辑安装 + 拷贝配置 + 挂 hook
```

然后 **新开一个 Codex 会话**，若弹出信任 hook 提示选允许。每轮结束后会显示：

> 注意：桌面版会把 hook 消息渲染成一段流式文本（换行/多空格被合并、且不渲染 Markdown），
> 因此这里用紧凑单行让它保持在一行内。`P`=当前任务、`S`=当前会话、
> `k`/`M`=千/百万、`¥`=元、`c`=缓存命中率。若在终端 TUI（等宽、保留换行）里用，两行效果更好。

```
P 12.3k tok ¥0.02 c96.4%  S 98.8k ¥0.10 c90.5%
```

### 配置位置

- 价格：`~/.config/codex-request-meter/pricing.peak.json`、`pricing.offpeak.json`（单位：元/百万 token；`cache_write_input_per_million` 沿用未命中价，因为 DeepSeek 无单独缓存写入价）。
- 模型 key：`deepseek-v4-flash-vision-exp`、`deepseek-v4-pro`（另含通配 `deepseek-v4-pro*`，兼容带版本后缀的字符串）。
- Hook：`~/.codex/hooks.json`；明细：`~/.codex/request-meter/events.jsonl`。
- 若改了 `~/config` 里的价格，**无需重装或重启**，下一轮即读取新值。

```
