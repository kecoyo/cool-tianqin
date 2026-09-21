from flask import Flask, request, jsonify
from apscheduler.schedulers.background import BackgroundScheduler
import threading
import time
import macd_kdj
import trend_analysis

app = Flask(__name__)
scheduler = BackgroundScheduler()

# ── 任务执行锁 ─────────────────────────────────────────────
# 每个任务一把锁，保证同一任务不会并发重复执行，不同任务之间可并行
_task_locks = {
    "macd_kdj": threading.Lock(),
    "trend_analysis": threading.Lock(),
}

# 记录各任务当前是否正在运行（供接口查询状态）
_task_running = {
    "macd_kdj": False,
    "trend_analysis": False,
}
_task_lock = threading.Lock()


def _run_in_background(task_name, task_func):
    """后台执行任务：加锁防重复，立即返回不阻塞接口"""
    with _task_lock:
        if _task_running[task_name]:
            return False  # 已有任务在执行中，拒绝重复
        _task_running[task_name] = True

    def _wrapper():
        try:
            task_func()
        except Exception as e:
            print(f"[{task_name}] 后台执行异常: {e}")
        finally:
            with _task_lock:
                _task_running[task_name] = False

    threading.Thread(target=_wrapper, daemon=True).start()
    return True


# 定时任务
def my_job():
    print(f"APScheduler任务：{time.strftime('%Y-%m-%d %H:%M:%S')}")
    _run_in_background("macd_kdj", macd_kdj.main)
    _run_in_background("trend_analysis", trend_analysis.main)

# scheduler.add_job(job, 'cron', second=0)                      # 每分钟执行一次
scheduler.add_job(my_job, 'cron', hour=9, minute=0)
scheduler.add_job(my_job, 'cron', hour=10, minute=0)
scheduler.add_job(my_job, 'cron', hour=11, minute=15)
scheduler.add_job(my_job, 'cron', hour=12, minute=0)
scheduler.add_job(my_job, 'cron', hour=13, minute=30)
scheduler.add_job(my_job, 'cron', hour=14, minute=15)
scheduler.add_job(my_job, 'cron', hour=15, minute=0)
scheduler.add_job(my_job, 'cron', hour=15, minute=30)
scheduler.add_job(my_job, 'cron', hour=21, minute=0)
scheduler.add_job(my_job, 'cron', hour=22, minute=0)
scheduler.add_job(my_job, 'cron', hour=23, minute=0)

# 接口
@app.route("/tianqinapi/macd_kdj", methods=["GET"])
def api_macd_kdj():
    started = _run_in_background("macd_kdj", macd_kdj.main)
    if started:
        return jsonify({"msg": "任务已在后台启动", "code": 200})
    return jsonify({"msg": "任务正在执行中，请勿重复请求", "code": 409})

# 接口
@app.route("/tianqinapi/trend_analysis", methods=["GET"])
def api_trend_analysis():
    started = _run_in_background("trend_analysis", trend_analysis.main)
    if started:
        return jsonify({"msg": "任务已在后台启动", "code": 200})
    return jsonify({"msg": "任务正在执行中，请勿重复请求", "code": 409})

if __name__ == '__main__':
    scheduler.start()
    app.run(host="0.0.0.0", port=8002, debug=False)