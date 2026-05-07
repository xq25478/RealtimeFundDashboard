"""配置加载"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

import yaml


@dataclass
class Fund:
    """基金条目（只关心代码、名称、题材，不涉及持仓细节）"""
    code: str
    name: str = ""
    theme: str = ""


@dataclass
class Watchlist:
    """关注列表"""
    indices: List[str] = field(default_factory=list)
    sectors: List[str] = field(default_factory=list)


@dataclass
class Settings:
    """设置"""
    risk_level: str = "medium"
    notification: bool = True
    report_format: List[str] = field(default_factory=lambda: ["console"])


# 默认主题 → 板块匹配关键词（兜底）。yaml 中可覆盖。
DEFAULT_THEME_KEYWORDS: Dict[str, List[str]] = {
    "人工智能":     ["人工智能", "AI", "算力", "软件"],
    "AI应用":      ["人工智能", "AI", "软件", "应用"],
    "半导体":       ["半导体", "芯片", "集成电路"],
    "存储芯片":     ["半导体", "芯片", "存储", "集成电路"],
    "中证芯片":     ["半导体", "芯片", "集成电路"],
    "CPO":         ["通信", "光通信", "光模块", "光器件"],
    "5G通信":       ["通信", "5G"],
    "科技成长":     ["科技", "电子", "计算机"],
    "上证科创50":   ["科技", "半导体", "电子", "计算机"],
    "电网设备":     ["电力", "电网", "电气"],
    "新能源":       ["新能源", "锂电", "电池", "新能源汽车"],
    "中证电池":     ["电池", "锂电", "电池化学"],
    "锂矿":         ["有色金属", "锂矿", "小金属", "稀有金属"],
    "稀土产业":     ["稀土", "有色金属", "小金属"],
    "工业有色金属": ["有色金属", "工业金属", "金属"],
    "中证油气":     ["油气", "石油", "石化", "能源"],
    "商业航天":     ["航天", "航空", "国防", "军工"],
    "国证机器人":   ["机器人", "自动化", "工业自动化"],
    "改革红利":     ["券商", "金融", "国企"],
}


@dataclass
class Config:
    """整体配置"""
    funds: List[Fund]
    watchlist: Watchlist
    settings: Settings
    themes: Dict[str, List[str]] = field(default_factory=dict)

    def keywords_for_theme(self, theme: str) -> List[str]:
        """主题 → 板块匹配关键词。优先 yaml,其次默认表,再次主题名本身。"""
        if not theme:
            return []
        if theme in self.themes:
            return [str(k) for k in self.themes[theme] if k]
        if theme in DEFAULT_THEME_KEYWORDS:
            return list(DEFAULT_THEME_KEYWORDS[theme])
        return [theme]


# 兼容旧命名
Holding = Fund


def load_config(path: str | Path) -> Config:
    """加载 YAML 配置"""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"配置文件不存在: {path}")

    with path.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    raw_funds = raw.get("funds") or raw.get("holdings") or []
    funds: List[Fund] = []
    for item in raw_funds:
        if not item:
            continue
        funds.append(Fund(
            code=str(item.get("code", "")).zfill(6),
            name=str(item.get("name", "")),
            theme=str(item.get("theme", "")),
        ))

    watchlist = Watchlist(**(raw.get("watchlist") or {}))
    settings = Settings(**(raw.get("settings") or {}))

    raw_themes = raw.get("themes") or {}
    themes: Dict[str, List[str]] = {}
    if isinstance(raw_themes, dict):
        for k, v in raw_themes.items():
            if isinstance(v, list):
                themes[str(k)] = [str(x) for x in v if x]
            elif isinstance(v, str):
                themes[str(k)] = [v]

    return Config(funds=funds, watchlist=watchlist, settings=settings, themes=themes)
