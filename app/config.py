"""全局配置。所有可调项都支持环境变量覆盖，方便直接部署。"""
from __future__ import annotations

import os
import sys
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
FROZEN = bool(getattr(sys, 'frozen', False))
# PyInstaller 解包目录；源码运行时就是项目根目录
RESOURCE_ROOT = Path(getattr(sys, '_MEIPASS', APP_DIR.parent))
PROJECT_DIR = APP_DIR.parent
STATIC_DIR = RESOURCE_ROOT / 'app' / 'static'
DEMO_PAGE = RESOURCE_ROOT / 'demo' / 'demo_page.html'


def _data_root() -> Path:
    """可写数据目录：桌面版放到用户目录，避免 Program Files 只读。"""
    env = os.environ.get('DPC_DATA_DIR')
    if env:
        return Path(env)
    if FROZEN:
        base = os.environ.get('LOCALAPPDATA') or str(Path.home() / 'AppData' / 'Local')
        return Path(base) / 'DrissionPageCatcher'
    return APP_DIR.parent


DATA_DIR = _data_root()
SNAPSHOT_DIR = DATA_DIR / 'snapshots'


def _env_str(name: str, default):
    value = os.environ.get(name)
    return value if value not in (None, '') else default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in ('1', 'true', 'yes', 'on')


# 画质 / 帧率预设，影响镜像画面清晰度与 CPU 占用
STREAM_PRESETS = {
    'smooth': {'label': '流畅', 'scale': 0.70, 'quality': 45, 'max_fps': 10, 'force_ms': 1800},
    'standard': {'label': '标准', 'scale': 0.85, 'quality': 62, 'max_fps': 8, 'force_ms': 1300},
    'hd': {'label': '高清', 'scale': 1.0, 'quality': 80, 'max_fps': 5, 'force_ms': 1000},
}


class Settings:
    """运行期可变配置。"""

    def __init__(self) -> None:
        # Web 服务
        self.host: str = _env_str('DPC_HOST', '127.0.0.1')
        self.port: int = _env_int('DPC_PORT', 8777)

        # 浏览器
        self.debug_port: int = _env_int('DPC_DEBUG_PORT', 9333)
        self.browser_path: str | None = _env_str('DPC_BROWSER_PATH', None)
        self.headless: bool = _env_bool('DPC_HEADLESS', False)
        self.window_width: int = _env_int('DPC_WIN_W', 1600)
        self.window_height: int = _env_int('DPC_WIN_H', 1000)
        self.start_url: str = _env_str('DPC_START_URL', str(DEMO_PAGE))
        self.user_data_dir: str | None = _env_str('DPC_USER_DIR', None)
        self.auto_start: bool = _env_bool('DPC_AUTO_START', True)
        # eager = DOMContentLoaded 即返回，捕获场景更跟手
        self.load_mode: str = _env_str('DPC_LOAD_MODE', 'eager')
        self.retry_times: int = _env_int('DPC_RETRY_TIMES', 3)
        self.retry_interval: float = float(_env_str('DPC_RETRY_INTERVAL', '0.15'))
        self.timeout: float = float(_env_str('DPC_TIMEOUT', '8'))

        # 镜像流
        self.stream_enabled: bool = _env_bool('DPC_STREAM', True)
        self.stream_preset: str = _env_str('DPC_STREAM_PRESET', 'standard')
        self.poll_ms: int = _env_int('DPC_POLL_MS', 70)

        # 行为限制
        self.max_highlight: int = _env_int('DPC_MAX_HIGHLIGHT', 200)
        self.max_candidates: int = _env_int('DPC_MAX_CANDIDATES', 12)
        self.burst_frames: int = _env_int('DPC_BURST_FRAMES', 6)

    # ---- 流参数 ----
    @property
    def preset(self) -> dict:
        return STREAM_PRESETS.get(self.stream_preset, STREAM_PRESETS['standard'])

    @property
    def demo_page_path(self) -> Path:
        return DEMO_PAGE

    def set_preset(self, name: str) -> dict:
        if name in STREAM_PRESETS:
            self.stream_preset = name
        return self.preset

    def as_dict(self) -> dict:
        return {
            'host': self.host,
            'port': self.port,
            'debug_port': self.debug_port,
            'browser_path': self.browser_path,
            'headless': self.headless,
            'window': [self.window_width, self.window_height],
            'start_url': self.start_url,
            'load_mode': self.load_mode,
            'timeout': self.timeout,
            'stream_enabled': self.stream_enabled,
            'stream_preset': self.stream_preset,
            'presets': {k: v['label'] for k, v in STREAM_PRESETS.items()},
            'poll_ms': self.poll_ms,
            'max_highlight': self.max_highlight,
            'snapshot_dir': str(SNAPSHOT_DIR),
            'demo_page': str(DEMO_PAGE),
            'data_dir': str(DATA_DIR),
            'desktop': bool(os.environ.get('DPC_DESKTOP')),
            'frozen': FROZEN,
        }


settings = Settings()
SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
