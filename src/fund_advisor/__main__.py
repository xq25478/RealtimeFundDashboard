"""dashboard 启动入口"""

import os
import sys

os.environ.setdefault("TQDM_DISABLE", "1")

try:
    from tqdm import tqdm as _tqdm
    from functools import partialmethod
    _tqdm.__init__ = partialmethod(_tqdm.__init__, disable=True)
except ImportError:
    pass

from fund_advisor.server.app import create_app
from fund_advisor.advisor.llm_client import get_server_config
from fund_advisor.utils.logger import get_logger


log = get_logger("fund_advisor")


def main():
    cfg = get_server_config()
    host = cfg["host"]
    port = cfg["port"]
    config_path = os.environ.get("FUND_CONFIG", "config/holdings.yaml")

    app = create_app(config_path)
    log.info(f"dashboard 运行于 http://{host}:{port}")
    log.info(f"配置: {config_path}")
    # threaded=True 让 SSE 长连接不阻塞其他请求
    app.run(host=host, port=port, debug=False, threaded=True, use_reloader=False)


if __name__ == "__main__":
    sys.exit(main() or 0)
