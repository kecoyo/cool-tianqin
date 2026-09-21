FROM python:3.14.7

WORKDIR /app

# 设置时区为中国东八区，这里的配置可以被docker-compose.yml或docker run时指定的时区覆盖
ENV TZ="Asia/Shanghai"

# 拷贝项目依赖包配置文件
COPY requirements.txt .

# 安装项目的依赖包
RUN pip install --no-cache-dir -r requirements.txt

# 挂载项目目录
VOLUME [ "/app" ]

CMD ["python", "src/run.py"]