from apscheduler.schedulers.blocking import BlockingScheduler
from datetime import datetime
import macd_kdj

def job():
    print(f"[{datetime.now()}] 执行任务中...")
    macd_kdj.main()

scheduler = BlockingScheduler()

# scheduler.add_job(job, 'cron', second=0)                  # 每分钟执行一次
# scheduler.add_job(job, 'cron', minute=0)                  # 每小时执行一次

scheduler.add_job(job, 'cron', hour=9, minute=0)
scheduler.add_job(job, 'cron', hour=10, minute=0)
scheduler.add_job(job, 'cron', hour=11, minute=15)
scheduler.add_job(job, 'cron', hour=12, minute=0)
scheduler.add_job(job, 'cron', hour=13, minute=30)
scheduler.add_job(job, 'cron', hour=14, minute=15)
scheduler.add_job(job, 'cron', hour=15, minute=0)
scheduler.add_job(job, 'cron', hour=21, minute=0)
scheduler.add_job(job, 'cron', hour=22, minute=0)
scheduler.add_job(job, 'cron', hour=23, minute=0)

scheduler.start()
