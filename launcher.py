"""PyInstaller 单文件入口：双击运行，起服务并自动打开浏览器。

与 Docker/nginx 形态共用数据目录约定：data/、output/、config/ 都落在 exe 旁，
拷走整个文件夹即完成迁移。宿主 8000 与 Docker 的 80 不冲突，可并存但共享
同一份 bidagent.db（若都在同一目录）——两套调度器别同时跑定时任务。
"""

from __future__ import annotations

import threading
import webbrowser

import uvicorn

from app.config import ensure_user_config, load_config


def main() -> None:
    print("=" * 46)
    print("  AI-Bid-Agent  招投标信息智能聚合")
    print("  数据目录: 本文件旁边的 data/ output/ config/")
    print("  停止服务: 关闭本窗口或按 Ctrl+C")
    print("=" * 46)

    ensure_user_config()
    config = load_config()
    port = config.app.port
    url = f"http://127.0.0.1:{port}/"

    from app.main import app

    def _open_browser() -> None:
        try:
            webbrowser.open(url)
        except Exception:
            pass  # 打不开浏览器不影响服务

    threading.Timer(2.5, _open_browser).start()
    print(f"启动中: {url}")
    try:
        uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")
    except OSError as exc:
        # 端口被占（多半是双开了第二个实例）——别甩 traceback 吓人
        print(f"\n端口 {port} 被占用，无法启动（{exc}）。")
        print("可能服务已在运行——直接用浏览器打开 " + url)
        input("按回车键退出...")


if __name__ == "__main__":
    main()
