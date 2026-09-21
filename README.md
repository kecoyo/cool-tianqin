# 天勤量化

## 生成 venv

```bash
python -m venv venv
```

## 生成 requirements.txt

```bash
pip freeze > requirements.txt
```

## 安装 requirements.txt

```bash
pip install -r requirements.txt
```

## 导出镜像
```
docker save -o cool-tianqin.tar cool-tianqin:latest
```