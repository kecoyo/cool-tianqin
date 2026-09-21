from flask import Flask, request, jsonify
from apscheduler.schedulers.background import BackgroundScheduler
import time
import macd_kdj
import trend_analysis

app = Flask(__name__)
scheduler = BackgroundScheduler()

# 定时任务
def my_job():
    print(f"APScheduler任务：{time.strftime('%Y-%m-%d %H:%M:%S')}")
    macd_kdj.main()
    trend_analysis.main()

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
@app.route("/api/macd_kdj", methods=["GET"])
def api_macd_kdj():
    macd_kdj.main()
    return jsonify({"msg": "ok", "code": 200})

# 接口
@app.route("/api/trend_analysis", methods=["GET"])
def api_trend_analysis():
    trend_analysis.main()
    return jsonify({"msg": "ok", "code": 200})

if __name__ == '__main__':
    scheduler.start()
    app.run(host="0.0.0.0", port=8002, debug=False)